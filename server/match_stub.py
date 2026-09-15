#!/usr/bin/env python3
"""LAN match-server stub: TLS listener on 127.0.0.1:18877 (revival tool).

Why this exists
---------------
With the offline/LAN path active (tools/patch_menu.py M3 patches
`localconnection.get` to `{lan:true, host:"127.0.0.1", ipAddress:"127.0.0.1",
port:18877}`), the client sets `offline:true` and picks the LAN matchmaking
machine, not the GameLift one:

    serversource: always [ {cond: ({offline}) => offline, target: "lan"}, ... ]
    lan/connecting: invoke matchserver.connect
                    {type:"lan", ipAddress, port}

The client is the TLS *client* (docs/08: raw ClientHello, no SNI), so the
revival must answer that connection. The application protocol above TLS is
still unknown, so this stub's job is narrow: complete the handshake and record
exactly what the client sends. That recording is the input to any future
NovaNet server implementation.

Cert trust
----------
Wine keeps its own Root store in the prefix registry, so a self-signed cert is
rejected (docs/08: TLSV1_ALERT_INTERNAL_ERROR) until it is installed. This
generates a self-signed cert claiming 127.0.0.1; install it as a trusted root
with tools/install_test_ca.py.

Usage (on the host, game not running):
  1. python3 server/match_stub.py          # generates the cert on first run
  2. python3 tools/install_test_ca.py      # installs it into the prefix root store
  3. launch, PLAY -> pick a mode -> READY
  4. read server/match_stub.log
"""
import datetime
import os
import socket
import ssl
import subprocess
import threading

HOST = "127.0.0.1"
PORT = int(os.environ.get("CRUCIBLE_MATCH_PORT", "18877"))
HERE = os.path.dirname(os.path.abspath(__file__))
CADIR = os.path.join(HERE, ".crucible-test-ca")
CRT = os.path.join(CADIR, "match.crt")
KEY = os.path.join(CADIR, "match.key")
LOG = os.path.join(HERE, "match_stub.log")
_lock = threading.Lock()


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().isoformat(timespec="milliseconds"), msg)
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
    log("generated self-signed cert for 127.0.0.1 (%s)" % CRT)


def drain(tls, addr, label):
    """Read until the peer closes, logging every chunk. The whole point: we do
    not know the framing, so we record bytes rather than parse them."""
    total = 0
    while True:
        try:
            data = tls.recv(8192)
        except (ssl.SSLError, OSError) as e:
            log("  %s read error after %d bytes: %r" % (label, total, e))
            return total
        if not data:
            log("  %s peer closed (clean EOF) after %d bytes" % (label, total))
            return total
        total += len(data)
        log("  %s <- %d bytes (total %d)" % (label, len(data), total))
        log("      hex: %s" % data[:1024].hex())
        log("      asc: %r" % (data[:512],))


def handle(conn, addr, ctx):
    try:
        conn.settimeout(60)
        try:
            tls = ctx.wrap_socket(conn, server_side=True)
        except ssl.SSLError as e:
            log("TLS handshake FAILED from %s: %r" % (addr, e))
            log("  -> cert not trusted yet? run tools/install_test_ca.py")
            return
        except (OSError, socket.timeout) as e:
            log("TLS handshake aborted from %s: %r" % (addr, e))
            return
        try:
            log("TLS handshake OK from %s: %s %s alpn=%r sni=%r" % (
                addr, tls.version(), tls.cipher()[0],
                tls.selected_alpn_protocol(), getattr(tls, "server_hostname", None)))
            tls.settimeout(30)
            drain(tls, addr, "app")
        finally:
            try:
                tls.close()
            except OSError:
                pass
    finally:
        log("--- connection from %s closed ---" % (addr,))


def main():
    ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CRT, KEY)

    def sni_cb(sock, name, c):
        log("  SNI presented: %r" % (name,))

    ctx.sni_callback = sni_cb

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(8)
    log("listening on %s:%d (TLS, log: %s)" % (HOST, PORT, LOG))
    log("expect: matchserver.connect {type:'lan'} after PLAY -> mode -> READY")
    try:
        while True:
            try:
                conn, addr = srv.accept()
            except KeyboardInterrupt:
                break
            log("--- TCP accept from %s ---" % (addr,))
            threading.Thread(target=handle, args=(conn, addr, ctx),
                             daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


if __name__ == "__main__":
    main()
