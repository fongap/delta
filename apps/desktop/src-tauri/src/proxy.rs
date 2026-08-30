//! Localhost reverse proxy in front of the Python sidecar (P0-A2).
//!
//! The sidecar's root API token must never exist in renderer JavaScript: any XSS or
//! compromised dependency could exfiltrate it and then drive the sidecar from anywhere.
//! Instead the Rust shell runs this proxy on `127.0.0.1:<random port>` and injects only
//! the PROXY endpoints into the WebView. The proxy is the only component that holds the
//! token; it adds `X-Delta-Token` to every forwarded REST request and rewrites the
//! WebSocket subprotocol (`["delta", <token>]`) for every upgrade, so the sidecar's
//! own auth (services/server/app.py `require_sidecar_token` / `_websocket_authenticated`)
//! keeps working unchanged.
//!
//! Security gate — Origin allowlist. The proxy binds to loopback, but any web page the
//! user visits can reach loopback from their browser. Browsers attach an unforgeable
//! `Origin` header, so requiring one of the app/webview origins here blocks cross-site
//! request forgery and DNS-rebinding attacks against the proxy (the same threat model as
//! the sidecar's `_ALLOWED_ORIGIN_RE`, mirrored below). Missing or disallowed Origin gets
//! a 403 before anything is forwarded.
//!
//! Implementation note: HTTP/1.1 heads are parsed/rewritten by hand over raw tokio TCP
//! streams and everything after the head is piped verbatim. That keeps the dependency set
//! to tokio alone (no HTTP framework) and means no crypto lives here either — the sidecar
//! computes `Sec-WebSocket-Accept`; we only replace the requested subprotocol list.

use std::io;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, ReadBuf};
use tokio::net::{TcpListener, TcpStream};

/// Largest request/response head we are willing to buffer before giving up. Real heads
/// are a few hundred bytes; this bounds memory per connection.
const MAX_HEAD_BYTES: usize = 64 * 1024;

/// Hosts allowed in browser `Origin` values — the Tauri webview's own origin(s) plus
/// localhost dev/browser builds. Mirrors `_ALLOWED_ORIGIN_RE` in services/server/app.py.
const ALLOWED_ORIGIN_HOSTS: [&str; 3] = ["localhost", "127.0.0.1", "tauri.localhost"];

/// Hop-by-hop headers that must not be forwarded across a proxy hop (RFC 9110 §7.6.1).
const HOP_BY_HOP: [&str; 9] = [
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
];

/// True if a browser `Origin` may use the proxy. Unlike the sidecar (which lets missing
/// Origin through for curl/tests), the proxy REJECTS a missing Origin: its only legitimate
/// clients are browsers, which always send it — so absence itself is suspicious.
fn origin_allowed(origin: Option<&str>) -> bool {
    let Some(origin) = origin else {
        return false;
    };
    // macOS/Linux webviews load the SPA from tauri://localhost.
    if origin == "tauri://localhost" {
        return true;
    }
    let Some(rest) = origin
        .strip_prefix("http://")
        .or_else(|| origin.strip_prefix("https://"))
    else {
        return false;
    };
    match rest.split_once(':') {
        // Port must be present-if-colon and purely numeric ("localhost:" → deny).
        Some((host, port)) => {
            !port.is_empty()
                && port.bytes().all(|b| b.is_ascii_digit())
                && ALLOWED_ORIGIN_HOSTS.contains(&host)
        }
        None => ALLOWED_ORIGIN_HOSTS.contains(&rest),
    }
}

struct ProxyConfig {
    target_port: u16,
    token: String,
}

/// Dedicated background runtime for the proxy. A separate runtime (instead of spawning
/// onto the ambient one) keeps socket registration and task execution in the same place,
/// so `start_proxy` behaves identically whether called from the Tauri setup hook or a
/// test harness.
static PROXY_RUNTIME: std::sync::OnceLock<tokio::runtime::Runtime> = std::sync::OnceLock::new();

fn proxy_runtime() -> &'static tokio::runtime::Runtime {
    PROXY_RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .enable_all()
            .build()
            .expect("failed to build the sidecar-proxy async runtime")
    })
}

/// Bind the proxy on a random free loopback port and start serving in the background.
/// Returns the listening port. Never logs — `token` stays in memory only.
pub fn start_proxy(target_port: u16, token: String) -> io::Result<u16> {
    // Bind synchronously so the caller knows the port before building the window.
    let guard = proxy_runtime().enter();
    let std_listener = std::net::TcpListener::bind("127.0.0.1:0")?;
    // Required before handing a socket to tokio/mio (Windows included).
    std_listener.set_nonblocking(true)?;
    let listener = TcpListener::from_std(std_listener)?;
    drop(guard);
    let port = listener.local_addr()?.port();
    let config = Arc::new(ProxyConfig { target_port, token });
    proxy_runtime().spawn(async move {
        while let Ok((socket, _)) = listener.accept().await {
            let config = Arc::clone(&config);
            tokio::spawn(handle_connection(socket, config));
        }
    });
    Ok(port)
}

async fn handle_connection(client: TcpStream, config: Arc<ProxyConfig>) {
    if let Err(error) = serve_connection(client, &config).await {
        // Diagnostics only — never include headers/token material.
        eprintln!("[delta-proxy] connection error: {error}");
    }
}

async fn serve_connection(client: TcpStream, config: &ProxyConfig) -> io::Result<()> {
    let mut client = client;
    let (head_bytes, leftover) = read_head(&mut client).await?;
    let Some(head) = parse_request_head(&head_bytes) else {
        write_simple_response(&mut client, 400, "Bad Request").await?;
        return Ok(());
    };

    // THE security gate: reject non-browser / foreign-origin traffic before forwarding.
    let origin = header(&head.headers, "origin");
    if !origin_allowed(origin) {
        write_simple_response(&mut client, 403, "Forbidden").await?;
        return Ok(());
    }

    let is_websocket = header(&head.headers, "connection")
        .map(|v| v.to_ascii_lowercase().contains("upgrade"))
        .unwrap_or(false)
        && header(&head.headers, "upgrade")
            .map(|v| v.eq_ignore_ascii_case("websocket"))
            .unwrap_or(false);

    if is_websocket {
        relay_websocket(client, head, leftover, config).await
    } else {
        relay_http(client, head, leftover, config).await
    }
}

// -- plain HTTP ---------------------------------------------------------------------------

async fn relay_http(
    client: TcpStream,
    head: RequestHead,
    leftover: Vec<u8>,
    config: &ProxyConfig,
) -> io::Result<()> {
    let mut upstream = TcpStream::connect(("127.0.0.1", config.target_port)).await?;

    let mut forwarded = format!("{} {} HTTP/1.1\r\n", head.method, head.target);
    for (name, value) in &head.headers {
        if HOP_BY_HOP.contains(&name.as_str()) || name == "x-delta-token" {
            continue;
        }
        forwarded.push_str(&format!("{name}: {value}\r\n"));
    }
    // Force close semantics so exactly one request rides each proxied connection: after the
    // response the sidecar closes its end, which cleanly ends our byte-level relay without
    // having to parse response framing (keep-alive requests would otherwise be forwarded
    // unauthenticated).
    forwarded.push_str("connection: close\r\n");
    // Auth injection happens HERE, in Rust — the token never reaches the renderer.
    forwarded.push_str(&format!("x-delta-token: {}\r\n", config.token));
    forwarded.push_str("\r\n");

    upstream.write_all(forwarded.as_bytes()).await?;
    upstream.write_all(&leftover).await?;

    // Pipe both directions verbatim until the sidecar closes the response (connection:
    // close above) or the client disconnects.
    let mut downstream = PrefixedStream::new(client, Vec::new());
    let _ = tokio::io::copy_bidirectional(&mut downstream, &mut upstream).await;
    Ok(())
}

// -- WebSocket ----------------------------------------------------------------------------

async fn relay_websocket(
    client: TcpStream,
    head: RequestHead,
    leftover: Vec<u8>,
    config: &ProxyConfig,
) -> io::Result<()> {
    let mut upstream = TcpStream::connect(("127.0.0.1", config.target_port)).await?;

    // Rebuild the upgrade request: keep the client's key/version/extensions, but swap the
    // subprotocol list for ["delta", <token>] — what the sidecar's
    // `_websocket_authenticated` expects — WITHOUT ever exposing the token downstream.
    let mut forwarded = format!(
        "{} {} HTTP/1.1\r\nhost: 127.0.0.1:{}\r\n",
        head.method, head.target, config.target_port
    );
    for (name, value) in &head.headers {
        if HOP_BY_HOP.contains(&name.as_str()) || name == "sec-websocket-protocol" || name == "host"
        {
            continue;
        }
        forwarded.push_str(&format!("{name}: {value}\r\n"));
    }
    forwarded.push_str("connection: Upgrade\r\n");
    forwarded.push_str("upgrade: websocket\r\n");
    forwarded.push_str(&format!(
        "sec-websocket-protocol: delta, {}\r\n",
        config.token
    ));
    forwarded.push_str("\r\n");

    upstream.write_all(forwarded.as_bytes()).await?;
    upstream.write_all(&leftover).await?;

    // Relay the upstream handshake response, stripping the selected subprotocol: the real
    // client offered none, and per RFC 6455 a server-selected protocol the client never
    // offered makes browsers abort the connection.
    let (response_head, upstream_leftover) = read_head(&mut upstream).await?;
    let response_head = strip_header_line(&response_head, "sec-websocket-protocol");

    let mut downstream = PrefixedStream::new(client, Vec::new());
    let mut upstream = PrefixedStream::new(upstream, upstream_leftover);
    downstream.write_all(&response_head).await?;
    downstream.flush().await?;

    // Bidirectional frame relay until either side closes.
    let _ = tokio::io::copy_bidirectional(&mut downstream, &mut upstream).await;
    Ok(())
}

// -- minimal HTTP/1.1 head handling --------------------------------------------------------

struct RequestHead {
    method: String,
    target: String,
    /// Header names lowercased, values trimmed, order preserved.
    headers: Vec<(String, String)>,
}

fn parse_request_head(bytes: &[u8]) -> Option<RequestHead> {
    let text = std::str::from_utf8(bytes).ok()?;
    let mut lines = text.split("\r\n");
    let request_line = lines.next()?;
    let mut parts = request_line.split_whitespace();
    let method = parts.next()?.to_string();
    let target = parts.next()?.to_string();
    parts.next()?; // HTTP version — accepted as-is; framing is handled by the sidecar.
    let headers = lines
        .filter(|line| !line.is_empty())
        .filter_map(|line| line.split_once(':'))
        .map(|(name, value)| (name.trim().to_ascii_lowercase(), value.trim().to_string()))
        .collect();
    Some(RequestHead {
        method,
        target,
        headers,
    })
}

fn header<'a>(headers: &'a [(String, String)], name: &str) -> Option<&'a str> {
    headers
        .iter()
        .find(|(key, _)| key == name)
        .map(|(_, value)| value.as_str())
}

/// Read from the stream up to and including the `\r\n\r\n` terminator. Returns the head
/// bytes plus whatever body bytes were already buffered past it.
async fn read_head(stream: &mut TcpStream) -> io::Result<(Vec<u8>, Vec<u8>)> {
    let mut data: Vec<u8> = Vec::with_capacity(4 * 1024);
    let mut chunk = [0u8; 4096];
    loop {
        if let Some(pos) = data.windows(4).position(|w| w == b"\r\n\r\n") {
            let end = pos + 4;
            let rest = data.split_off(end);
            return Ok((data, rest));
        }
        if data.len() > MAX_HEAD_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "HTTP head exceeds size limit",
            ));
        }
        let n = stream.read(&mut chunk).await?;
        if n == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "peer closed before sending a complete HTTP head",
            ));
        }
        data.extend_from_slice(&chunk[..n]);
    }
}

/// Remove every `name:` header line from an HTTP head (status/request line kept).
fn strip_header_line(head: &[u8], name: &str) -> Vec<u8> {
    let text = String::from_utf8_lossy(head);
    let mut out = Vec::with_capacity(head.len());
    for line in text.split("\r\n") {
        let matches = line
            .split_once(':')
            .map(|(candidate, _)| candidate.trim().eq_ignore_ascii_case(name))
            .unwrap_or(false);
        if !matches && !line.is_empty() {
            out.extend_from_slice(line.as_bytes());
            out.extend_from_slice(b"\r\n");
        }
    }
    out.extend_from_slice(b"\r\n");
    out
}

async fn write_simple_response(
    stream: &mut TcpStream,
    status: u16,
    reason: &str,
) -> io::Result<()> {
    let body = format!("{status} {reason}");
    let response = format!(
        "HTTP/1.1 {status} {reason}\r\ncontent-type: text/plain\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
        body.len()
    );
    stream.write_all(response.as_bytes()).await?;
    stream.flush().await
}

// -- stream wrapper -------------------------------------------------------------------------

/// A `TcpStream` that first yields buffered bytes (already-read body data), then the live
/// socket — so byte-level relaying never loses the tail of a parsed head.
struct PrefixedStream {
    inner: TcpStream,
    prefix: Vec<u8>,
    offset: usize,
}

impl PrefixedStream {
    fn new(inner: TcpStream, prefix: Vec<u8>) -> Self {
        Self {
            inner,
            prefix,
            offset: 0,
        }
    }
}

impl AsyncRead for PrefixedStream {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        if self.offset < self.prefix.len() {
            let n = buf.remaining().min(self.prefix.len() - self.offset);
            buf.put_slice(&self.prefix[self.offset..self.offset + n]);
            self.offset += n;
            return Poll::Ready(Ok(()));
        }
        Pin::new(&mut self.inner).poll_read(cx, buf)
    }
}

impl AsyncWrite for PrefixedStream {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.inner).poll_write(cx, buf)
    }

    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_flush(cx)
    }

    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_shutdown(cx)
    }
}

#[cfg(test)]
mod tests {
    use super::origin_allowed;

    #[test]
    fn allows_webview_and_localhost_origins() {
        assert!(origin_allowed(Some("tauri://localhost")));
        assert!(origin_allowed(Some("http://tauri.localhost")));
        assert!(origin_allowed(Some("https://tauri.localhost")));
        assert!(origin_allowed(Some("http://localhost")));
        assert!(origin_allowed(Some("https://localhost")));
        assert!(origin_allowed(Some("http://localhost:1420")));
        assert!(origin_allowed(Some("http://localhost:5173")));
        assert!(origin_allowed(Some("http://127.0.0.1")));
        assert!(origin_allowed(Some("http://127.0.0.1:8765")));
        assert!(origin_allowed(Some("https://127.0.0.1:3000")));
    }

    #[test]
    fn rejects_missing_origin() {
        assert!(!origin_allowed(None));
        assert!(!origin_allowed(Some("")));
    }

    #[test]
    fn rejects_disallowed_origins() {
        // Foreign sites / rebinding attacks.
        assert!(!origin_allowed(Some("https://evil.example")));
        assert!(!origin_allowed(Some("http://evil.example:8080")));
        // Lookalike hosts that merely CONTAIN an allowed host.
        assert!(!origin_allowed(Some("http://evil.example/localhost")));
        assert!(!origin_allowed(Some("http://localhost.evil.example")));
        assert!(!origin_allowed(Some("http://127.0.0.1.evil.example")));
        assert!(!origin_allowed(Some("http://tauri.localhost.evil.example")));
        // Wrong scheme / lookalike hosts on the tauri origin.
        assert!(!origin_allowed(Some(
            "https://tauri.localhost.evil.example"
        )));
        assert!(!origin_allowed(Some("file://localhost")));
        // Malformed ports.
        assert!(!origin_allowed(Some("http://localhost:")));
        assert!(!origin_allowed(Some("http://localhost:14x0")));
        // Browser null-origin sandbox.
        assert!(!origin_allowed(Some("null")));
    }
}
