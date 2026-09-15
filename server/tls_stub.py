#!/usr/bin/env python3
"""Crucible TLS logging stub (revival tool, stdlib + openssl CLI).

Serves https://x8evjomsd1.execute-api.us-west-2.amazonaws.com:443 via a
hosts-file redirect. Generates a throwaway self-signed cert for that name on
first run (server/.crucible-test-ca/ — local dev only, never real traffic).
Logs every request (method/path/headers/body) and answers `{}`.

Why TLS: the client's PlayerAuthorizerEndpoint console override does NOT
reach the request (proven: default hostname still queried with override
set), so we redirect the DEFAULT https endpoint instead.

Usage (on the host):
  1. echo '127.0.0.1 x8evjomsd1.execute-api.us-west-2.amazonaws.com' | sudo tee -a /etc/hosts
  2. sudo python3 server/tls_stub.py     # port 443 needs root; leave running
  3. Launch the game, logo hang, quit, Ctrl-C.
  4. stub_tls_requests.log holds the request(s).

Open question answered by this run: does Wine/WinHTTP accept the
self-signed cert (known Wine validation gaps) or reject it? Accept ->
request visible, iterate on response shape. Reject -> install our CA into
the Proton prefix trust store (next step).
"""
import datetime
import http.server
import json
import os
import socket
import ssl
import subprocess
import threading

HOST = "127.0.0.1"
PORT = int(os.environ.get("CRUCIBLE_STUB_PORT", "443"))
NAME = "x8evjomsd1.execute-api.us-west-2.amazonaws.com"
HERE = os.path.dirname(os.path.abspath(__file__))
CADIR = os.path.join(HERE, ".crucible-test-ca")
CRT = os.path.join(CADIR, "stub.crt")
KEY = os.path.join(CADIR, "stub.key")
LOG = os.path.join(HERE, "stub_tls_requests.log")
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
         "-subj", f"/CN={NAME}",
         "-addext", f"subjectAltName=DNS:{NAME},DNS:*.execute-api.us-west-2.amazonaws.com"],
        check=True, capture_output=True)
    log(f"generated throwaway cert for {NAME}")


# Response v1: 4 confirmed EndpointConfig keys (from "No configs found for"
# oracle) + auth-key candidates in both casings. Extras are harmless; the
# client logs which keys it still misses, so we converge by iteration.
RESPONSE_V1 = {
    "CrucibleLiveWebSocketUrl": "wss://127.0.0.1:8765",
    "stage": "prod",
    "region": "us-west-2",
    "analyticsKinesisStreamName": "crucible-revival-stub",
    "apiKey": "stub",
    "apiAccess": "stub",
    "apiSecret": "stub",
    "authToken": "stub-token",
    "entitlementId": "stub-entitlement",
    "playerId": "stub-player",
    "gamesparksId": "stub-gs-id",
    "gamesparksApiKey": "stub",
    "gamesparksApiAccess": "stub",
    "gamesparksApiSecret": "stub",
    "gamesparksAuthToken": "stub-token",
    "sessionTicket": "stub",
    "token": "stub-token",
    "ApiKey": "stub",
    "AuthToken": "stub-token",
    "EntitlementId": "stub-entitlement",
    "PlayerId": "stub-player",
    "SessionTicket": "stub",
    "Token": "stub-token",
}
RESPONSES = {
    "/prod/authorizePlayer": RESPONSE_V1,
    "/authorizePlayer": RESPONSE_V1,
}


def load_responses():
    """Hot-reload server/responses.json per request so iterations don't need
    a stub restart (port 443 needs sudo). Falls back to built-in RESPONSES."""
    try:
        with open(os.path.join(HERE, "responses.json"), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:  # noqa: BLE001
        log(f"  responses.json unreadable ({e}); using built-in")
    return RESPONSES


class Server(http.server.ThreadingHTTPServer):
    def get_request(self):
        conn, addr = super().get_request()
        log(f"--- TLS accept from {addr} ---")
        return conn, addr


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "CrucibleTLSStub/0.1"

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
        payload = json.dumps(load_responses().get(self.path, {})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        log(f"  -> 200 application/json {payload[:600]!r}")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _handle

    def send_error(self, code, message=None, explain=None):
        raw = getattr(self, "raw_requestline", b"") or b""
        log(f"  HTTP-PARSE-FAIL code={code} rawlen={len(raw)} hex={raw[:128].hex()}")
        super().send_error(code, message, explain)

    def log_message(self, fmt, *args):
        pass


def main():
    ensure_cert()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n--- tls stub start {datetime.datetime.now().isoformat()} ---\n")
    srv = Server((HOST, PORT), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CRT, KEY)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print(f"listening on https://{NAME}:{PORT} (log: {LOG})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
