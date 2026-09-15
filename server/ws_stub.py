#!/usr/bin/env python3
"""Generic TLS WebSocket logging stub (revival tool, stdlib + openssl CLI).

Serves the Persona endpoint from our authorizer response
(`wss://127.0.0.1:8765`): completes the WS handshake, logs every text frame,
replies with a placeholder. Also prototypes the future GameSparks endpoint
(same framing will serve `/ws/game/...` on the 443 TLS stub later).

Unprivileged port -> the agent can run this itself; no sudo needed.

Usage:
  python3 server/ws_stub.py [port]        # default 8765, log ws_frames.log
"""
import base64
import datetime
import hashlib
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
CADIR = os.path.join(HERE, ".crucible-test-ca")
CRT = os.path.join(CADIR, "ws.crt")
KEY = os.path.join(CADIR, "ws.key")
LOG = os.path.join(HERE, "ws_frames.log")
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_lock = threading.Lock()


def log(msg):
    line = f"[{datetime.datetime.now().isoformat(timespec='milliseconds')}] {msg}"
    print(line, flush=True)
    with _lock:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def ensure_cert():
    if os.path.exists(CRT) and os.path.exists(KEY):
        return
    os.makedirs(CADIR, exist_ok=True)
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", KEY, "-out", CRT, "-days", "3650",
         "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        check=True, capture_output=True)
    log("generated throwaway ws cert")


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("eof")
        buf += chunk
    return buf


def read_http_request(conn):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError("eof during handshake")
        buf += chunk
        if len(buf) > 65536:
            raise ValueError("handshake too big")
    head, _rest = buf.split(b"\r\n\r\n", 1)
    lines = head.decode("latin1").split("\r\n")
    headers = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return lines[0], headers


def send_text(conn, text):
    data = text.encode()
    conn.sendall(b"\x81" + bytes([len(data) if len(data) < 126 else 126])
                 + (struct.pack(">H", len(data)) if len(data) >= 126 else b"")
                 + data)


def read_frame(conn):
    hdr = recv_exact(conn, 2)
    fin, opcode, masked, ln = hdr[0] >> 7, hdr[0] & 15, hdr[1] >> 7, hdr[1] & 127
    if ln == 126:
        ln = struct.unpack(">H", recv_exact(conn, 2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", recv_exact(conn, 8))[0]
    mask = recv_exact(conn, 4) if masked else None
    payload = recv_exact(conn, ln) if ln else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


def handle(conn, addr):
    try:
        req, headers = read_http_request(conn)
        log(f"=== WS handshake {addr}: {req} ===")
        for k, v in headers.items():
            if k in ("host", "upgrade", "connection", "sec-websocket-key",
                      "sec-websocket-protocol", "sec-websocket-version", "origin"):
                log(f"  H {k}: {v}")
        key = headers.get("sec-websocket-key", "")
        if "websocket" not in headers.get("upgrade", "").lower():
            log("  not a websocket upgrade; closing")
            conn.close()
            return
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                     f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        log("  -> 101 established")
        while True:
            fin, opcode, payload = read_frame(conn)
            if opcode == 8:
                log("  <- close frame; closing")
                return
            if opcode == 9:
                conn.sendall(b"\x8a\x00")
                continue
            try:
                log("  <- text: " + json.dumps(json.loads(payload), indent=2)[:2000])
            except Exception:
                log(f"  <- opcode={opcode} len={len(payload)} raw={payload[:300]!r}")
    except Exception as e:  # noqa: BLE001
        log(f"  session {addr} ended: {e!r}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ensure_cert()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n--- ws stub start {datetime.datetime.now().isoformat()} port={port} ---\n")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(16)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CRT, KEY)
    log(f"listening on wss://127.0.0.1:{port} (log: {LOG})")
    try:
        while True:
            raw, addr = srv.accept()
            try:
                conn = ctx.wrap_socket(raw, server_side=True)
            except Exception as e:
                log(f"TLS handshake fail from {addr}: {e!r}")
                raw.close()
                continue
            threading.Thread(target=handle, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
