//! Manual smoke harness: starts a fake sidecar + the real proxy, then drives REST
//! (allowed/missing/denied Origin) and a WebSocket upgrade through it.
//! Run with `cargo run --example proxy_smoke_harness` from src-tauri.

use std::io::{Read, Write};
use std::net::TcpListener;

const TOKEN: &str = "harness-token";

fn main() -> std::io::Result<()> {
    // Fake sidecar: echoes request head back so we can see injected auth.
    let sidecar = TcpListener::bind("127.0.0.1:0")?;
    let sidecar_port = sidecar.local_addr()?.port();
    std::thread::spawn(move || {
        for stream in sidecar.incoming() {
            let mut stream = match stream {
                Ok(s) => s,
                Err(_) => break,
            };
            std::thread::spawn(move || {
                let mut buf = Vec::new();
                let _ = read_head(&mut stream, &mut buf);
                let text = String::from_utf8_lossy(&buf).into_owned();
                if text.to_ascii_lowercase().contains("upgrade: websocket") {
                    let has_token = text
                        .to_ascii_lowercase()
                        .contains(&format!("sec-websocket-protocol: openworker, {TOKEN}\r\n"));
                    let response = format!(
                        "HTTP/1.1 101 Switching Protocols\r\nconnection: Upgrade\r\nupgrade: websocket\r\nsec-websocket-accept: dGhlIHNhbXBsZSBub25jZQ==\r\nsec-websocket-protocol: openworker\r\nx-fake-sidecar-saw-token: {}\r\n\r\necho!",
                        has_token
                    );
                    let _ = stream.write_all(response.as_bytes());
                    let _ = stream.write_all(b"\r\n");
                } else {
                    let body = format!(
                        "saw-token-header={}",
                        text.to_ascii_lowercase()
                            .contains("x-openworker-token: harness-token")
                    );
                    let _ = stream.write_all(
                        format!(
                            "HTTP/1.1 200 OK\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                            body.len()
                        )
                        .as_bytes(),
                    );
                }
                let _ = stream.flush();
                // hold WS connections briefly so relay bytes can flow
            });
        }
    });

    let proxy_port =
        openworker_desktop_lib::proxy_start_for_tests(sidecar_port, TOKEN.to_string())
            .expect("start proxy");
    println!("fake sidecar port: {sidecar_port}\nproxy port:         {proxy_port}");

    // 1. REST with allowed Origin.
    let out = send(format!(
        "GET /v1/health HTTP/1.1\r\nhost: x\r\norigin: tauri://localhost\r\nconnection: close\r\n\r\n"
    ), proxy_port);
    println!("[REST allowed origin]   {out}");
    // 2. REST with missing Origin.
    let out = send("GET /v1/x HTTP/1.1\r\nhost: x\r\nconnection: close\r\n\r\n".into(), proxy_port);
    println!("[REST missing origin]   {out}");
    // 3. REST with evil Origin.
    let out = send("GET /v1/x HTTP/1.1\r\nhost: x\r\norigin: https://evil.example\r\nconnection: close\r\n\r\n".into(), proxy_port);
    println!("[REST denied origin]    {out}");
    // 4. WS upgrade WITHOUT token subprotocol (renderer-style).
    let out = send(
        format!(
            "GET /ws/session/x HTTP/1.1\r\nhost: x\r\norigin: http://tauri.localhost\r\nconnection: Upgrade\r\nupgrade: websocket\r\nsec-websocket-key: dGhlIHNhbXBsZSBub25jZQ==\r\nsec-websocket-version: 13\r\n\r\n"
        ),
        proxy_port,
    );
    println!("[WS no token offered]   {out}");
    // 5. WS upgrade with wrong Origin.
    let out = send(
        "GET /ws/session/x HTTP/1.1\r\nhost: x\r\norigin: http://evil.example\r\nconnection: Upgrade\r\nupgrade: websocket\r\nsec-websocket-key: dGhlIHNhbXBsZSBub25jZQ==\r\nsec-websocket-version: 13\r\n\r\n".into(),
        proxy_port,
    );
    println!("[WS denied origin]      {out}");

    Ok(())
}

fn send(request: String, port: u16) -> String {
    use std::net::TcpStream;
    let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("connect");
    stream.write_all(request.as_bytes()).unwrap();
    stream
        .set_read_timeout(Some(std::time::Duration::from_millis(500)))
        .unwrap();
    let mut response = Vec::new();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
    while std::time::Instant::now() < deadline {
        let mut chunk = [0u8; 4096];
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => response.extend_from_slice(&chunk[..n]),
            Err(_) => break,
        }
    }
    String::from_utf8_lossy(&response)
        .replace("\r\n", " | ")
        .chars()
        .take(300)
        .collect()
}

fn read_head(stream: &mut std::net::TcpStream, buf: &mut Vec<u8>) -> std::io::Result<usize> {
    let mut chunk = [0u8; 4096];
    loop {
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            return Ok(buf.len());
        }
        buf.extend_from_slice(&chunk[..n]);
        if buf.windows(4).any(|w| w == b"\r\n\r\n") {
            return Ok(buf.len());
        }
    }
}
