#!/usr/bin/env python3
"""Crucible PlayerAuthorizer logging stub (revival tool, stdlib only).

Listens for the client's /authorizePlayer call (and anything else), logs the
full request (method/path/headers/body) so we can learn the wire format, and
replies with a placeholder body. Expect the client to REJECT the placeholder
(unknown response shape) — the request log is the deliverable of this phase.

Usage (on the host):
  1. python3 server/stub_authorizer.py            # listens on 127.0.0.1:8443
  2. Set user.cfg line 1 to:
       PlayerAuthorizerEndpoint http://127.0.0.1:8443
  3. Launch the game, wait for the logo hang, quit.
  4. Send back stub_requests.log (+ the client log tail).

If NOTHING arrives but the client still fails fast, the client likely forces
https:// — then we move to a TLS stub with a prefix-installed CA.
"""
import datetime
import http.server
import json
import os
import threading

HOST = "127.0.0.1"
PORT = 8443
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stub_requests.log")
_lock = threading.Lock()


def log(msg):
    line = f"[{datetime.datetime.now().isoformat(timespec='milliseconds')}] {msg}"
    print(line, flush=True)
    with _lock:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class Server(http.server.ThreadingHTTPServer):
    def get_request(self):
        conn, addr = super().get_request()
        log(f"--- TCP accept from {addr} ---")
        return conn, addr


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "CrucibleStub/0.1"

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except Exception as e:  # noqa: BLE001 - log raw garbage (e.g. TLS hello to plain HTTP)
            raw = getattr(self, "raw_requestline", b"")
            try:
                peek = self.request.recv(256, 0x02)  # MSG_PEEK
            except Exception:
                peek = b""
            blob = (raw or b"") + (peek or b"")
            log(f"  NON-HTTP bytes len={len(blob)} hex={blob[:96].hex()} err={e!r}")
            try:
                self.close_connection = True
            except Exception:
                pass

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        log(f"=== {self.command} {self.path} from {self.client_address} ===")
        for k, v in self.headers.items():
            log(f"  H {k}: {v}")
        if body:
            log(f"  body_len={len(body)}")
            try:
                log("  body_json=" + json.dumps(json.loads(body), indent=2))
            except Exception:
                log(f"  body_raw={body[:2000]!r}")
        else:
            log("  body=<empty>")
        payload = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        log(f"  -> 200 application/json {payload!r}")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _handle

    def send_error(self, code, message=None, explain=None):
        raw = getattr(self, "raw_requestline", b"") or b""
        log(f"  HTTP-PARSE-FAIL code={code} rawlen={len(raw)} hex={raw[:128].hex()}")
        super().send_error(code, message, explain)

    def log_message(self, fmt, *args):  # quiet default logging; we log ourselves
        pass


def main():
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n--- stub start {datetime.datetime.now().isoformat()} ---\n")
    srv = Server((HOST, PORT), Handler)
    print(f"listening on http://{HOST}:{PORT}  (log: {LOG})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
