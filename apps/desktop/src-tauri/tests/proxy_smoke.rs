//! End-to-end smoke tests for the localhost auth-injecting proxy (`src/proxy.rs`).
//!
//! A minimal fake sidecar plays the Python server: it asserts that the proxy injected
//! authentication (header / subprotocol) before answering. The client side plays the
//! WebView renderer, which never holds a token.

use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

const TOKEN: &str = "smoke-test-token";

async fn read_http_head(stream: &mut TcpStream) -> String {
    let mut data = Vec::new();
    let mut chunk = [0u8; 1024];
    loop {
        let n = stream.read(&mut chunk).await.expect("read head");
        assert!(n > 0, "peer closed before head completed");
        data.extend_from_slice(&chunk[..n]);
        if data.windows(4).any(|w| w == b"\r\n\r\n") {
            return String::from_utf8_lossy(&data).into_owned();
        }
    }
}

/// Fake sidecar for plain requests: requires the auth header, answers 200.
async fn serve_rest_once(listener: TcpListener) {
    let (mut sock, _) = listener.accept().await.expect("rest accept");
    let head = read_http_head(&mut sock).await;
    assert!(
        head.to_ascii_lowercase()
            .contains("x-delta-token: smoke-test-token\r\n"),
        "proxy did not inject the auth header:\n{head}"
    );
    sock.write_all(
        b"HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\ncontent-length: 2\r\nconnection: close\r\n\r\nok",
    )
    .await
    .unwrap();
    sock.flush().await.unwrap();
    // connection: close was requested by the proxy — honour it so the relay terminates.
}

/// Fake sidecar for WebSocket upgrades: requires the tokened subprotocol, accepts,
/// echoes one frame.
async fn serve_ws_once(listener: TcpListener) {
    let (mut sock, _) = listener.accept().await.expect("ws accept");
    let head = read_http_head(&mut sock).await;
    assert!(
        head.to_ascii_lowercase()
            .contains("sec-websocket-protocol: delta, smoke-test-token\r\n"),
        "proxy did not rewrite the subprotocol list:\n{head}"
    );
    sock.write_all(
        b"HTTP/1.1 101 Switching Protocols\r\nconnection: Upgrade\r\nupgrade: websocket\r\nsec-websocket-accept: dGhlIHNhbXBsZSBub25jZQ==\r\nsec-websocket-protocol: delta\r\n\r\n",
    )
    .await
    .unwrap();
    let mut buf = [0u8; 4];
    sock.read_exact(&mut buf).await.unwrap(); // "ping"
    sock.write_all(b"pong").await.unwrap();
    sock.flush().await.unwrap();
}

async fn exchange(target_port: u16, request: &str) -> Vec<u8> {
    let proxy_port = crate_under_test(target_port);
    let mut client = TcpStream::connect(("127.0.0.1", proxy_port))
        .await
        .expect("connect to proxy");
    client.write_all(request.as_bytes()).await.unwrap();
    let mut response = Vec::new();
    tokio::time::timeout(Duration::from_secs(5), async {
        let _ = client.read_to_end(&mut response).await;
    })
    .await
    .expect("proxy round-trip timed out");
    response
}

// The proxy lives in the lib crate; import its public entry point.
use delta_desktop_lib::proxy_start_for_tests;

fn crate_under_test(target_port: u16) -> u16 {
    proxy_start_for_tests(target_port, TOKEN.to_string()).expect("start proxy")
}

#[tokio::test]
async fn rest_relay_injects_token_and_passes_body_through() {
    let upstream = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let target = upstream.local_addr().unwrap().port();
    tokio::spawn(serve_rest_once(upstream));

    let response = exchange(
        target,
        "GET /v1/health HTTP/1.1\r\nhost: 127.0.0.1\r\norigin: tauri://localhost\r\nconnection: close\r\n\r\n",
    )
    .await;

    let text = String::from_utf8_lossy(&response).into_owned();
    assert!(text.starts_with("HTTP/1.1 200"), "{text}");
    assert!(text.ends_with("ok"), "{text}");
}

#[tokio::test]
async fn rest_with_disallowed_origin_is_rejected_before_forwarding() {
    let upstream = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let target = upstream.local_addr().unwrap().port();

    let response = exchange(
        target,
        "GET /v1/sessions HTTP/1.1\r\nhost: 127.0.0.1\r\norigin: https://evil.example\r\nconnection: close\r\n\r\n",
    )
    .await;
    let text = String::from_utf8_lossy(&response).into_owned();
    assert!(text.starts_with("HTTP/1.1 403"), "{text}");
}

#[tokio::test]
async fn rest_with_missing_origin_is_rejected() {
    let upstream = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let target = upstream.local_addr().unwrap().port();

    let response = exchange(
        target,
        "GET /v1/sessions HTTP/1.1\r\nhost: 127.0.0.1\r\nconnection: close\r\n\r\n",
    )
    .await;
    let text = String::from_utf8_lossy(&response).into_owned();
    assert!(text.starts_with("HTTP/1.1 403"), "{text}");
}

#[tokio::test]
async fn websocket_relay_rewrites_subprotocol_and_strips_it_from_response() {
    let upstream = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let target = upstream.local_addr().unwrap().port();
    tokio::spawn(serve_ws_once(upstream));

    let proxy_port = crate_under_test(target);
    let mut client = TcpStream::connect(("127.0.0.1", proxy_port)).await.unwrap();
    // NOTE: the renderer sends NO token subprotocol — the proxy inserts it.
    client
        .write_all(
            b"GET /ws/session/x HTTP/1.1\r\nhost: 127.0.0.1\r\norigin: http://tauri.localhost\r\nconnection: Upgrade\r\nupgrade: websocket\r\nsec-websocket-key: dGhlIHNhbXBsZSBub25jZQ==\r\nsec-websocket-version: 13\r\n\r\n",
        )
        .await
        .unwrap();

    let mut head = Vec::new();
    tokio::time::timeout(Duration::from_secs(5), async {
        let mut chunk = [0u8; 256];
        loop {
            let n = client.read(&mut chunk).await.unwrap();
            assert!(n > 0, "closed during handshake");
            head.extend_from_slice(&chunk[..n]);
            if head.windows(4).any(|w| w == b"\r\n\r\n") {
                break;
            }
        }
    })
    .await
    .expect("handshake timed out");

    let head_text = String::from_utf8_lossy(&head).into_owned();
    assert!(head_text.starts_with("HTTP/1.1 101"), "{head_text}");
    assert!(
        !head_text
            .to_ascii_lowercase()
            .contains("sec-websocket-protocol"),
        "selected subprotocol leaked downstream:\n{head_text}"
    );

    client.write_all(b"ping").await.unwrap();
    let mut echoed = [0u8; 4];
    tokio::time::timeout(Duration::from_secs(5), client.read_exact(&mut echoed))
        .await
        .expect("frame relay timed out")
        .unwrap();
    assert_eq!(&echoed, b"pong");
}
