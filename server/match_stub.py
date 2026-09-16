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

Cert trust is only half the story - the engine ALSO pins the match server's
cert, so net_SslEnablePinning must be 0 in user.cfg or the handshake aborts
regardless of what cert is served. See docs/10-match-server-tls.md.

Usage (on the host, game not running):
  1. python3 server/match_stub.py          # generates the cert on first run
  2. python3 tools/install_test_ca.py      # installs it into the prefix root store
  3. launch, PLAY -> pick a mode -> READY
  4. read server/match_stub.log

Packet-name probing
-------------------
The client logs every packet it RECEIVES as
  [Debug_DispatchPackets] %s: Received packet %s
once the ReportTag is enabled in user.cfg:
  Crucible.ReportTagRequiredFilters +Debug_DispatchPackets

Because this stub otherwise sends nothing, that log has nothing to report. Set
  CRUCIBLE_PROBE_IDS=5,6,7,8,9,10,11,12
to have the stub reply to the client's opening frame with one frame per id.
The client then names each type in its own log, which is the only way to
resolve the id -> name mapping.

  CRUCIBLE_PROBE_IDS=5 python3 server/match_stub.py     # single id
"""
import datetime
import os
import socket
import ssl
import struct
import subprocess
import threading
import time

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


FLAG_OFFSET = 11  # payload byte 7; see the layout note below


def make_reply(data, mode):
    """Build a reply for one received frame, or None to stay silent.

    Observed frames (header is 4 bytes: u16 id, u16 payload length):

      id 14, 12B:  01000500 0000 <ctr> 01
      id  8, 16B:  01000500 0000 <ctr> 00 <uid4>
      id  5, 17B:  01000500 0000 01    01 <uid4> 00

    i.e. a constant 6-byte prefix, a 1-byte counter, then a 1-byte marker at
    payload offset 7 (frame offset 11), then optional data. The marker is 0x01
    on every frame that the client subsequently reports as a pending request
    ('Timing out requestid N') and 0x00 on the id-8 frame. So 0x01 reads as
    "expects an answer".

    Two hypotheses, selectable with CRUCIBLE_REPLY:
      echo - send the frame back unchanged
      ack  - same frame with that marker cleared to 0x00

    NOTE the marker is NOT the last byte. It is at frame offset 11; on the id-8
    frame the last four bytes are the uid, so clearing the trailing byte would
    corrupt it. The first version of this got that wrong.
    """
    if mode == "echo":
        return data
    if mode == "ack":
        if len(data) <= FLAG_OFFSET:
            return None
        return data[:FLAG_OFFSET] + b"\x00" + data[FLAG_OFFSET + 1:]
    return None


def drain(tls, addr, label, reply=None):
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
        if reply:
            out = make_reply(data, reply)
            if out is not None:
                try:
                    tls.sendall(out)
                    log("      reply(%s) -> %s" % (reply, out.hex()))
                except (ssl.SSLError, OSError) as e:
                    log("      reply(%s) failed: %r" % (reply, e))
                    return total


def build_probe(pid, payload=b""):
    """One NovaNet frame: u16 BE packet id, u16 BE payload length, payload.

    Framing inferred from the client's own opening frame
    (docs/10-match-server-tls.md): a 4-byte header whose length field is
    exactly len(frame) - 4.
    """
    return struct.pack(">HH", pid, len(payload)) + payload


def probe(tls, first_frame):
    """Send one frame per id in CRUCIBLE_PROBE_IDS, spaced out.

    The client's dispatcher logs `[Debug_DispatchPackets] %s: Received packet
    %s` for anything it receives, so a probe makes it name the packet type -
    which is the only way to learn the id -> name mapping. Unknown ids hit
    its own assert instead, so either outcome is informative.
    """
    spec = os.environ.get("CRUCIBLE_PROBE_IDS", "").strip()
    if not spec:
        return
    ids = [int(x) for x in spec.replace(" ", "").split(",") if x]

    # Echo the payload the client itself sent, so the shape stays plausible
    # for whichever id we pretend to be a reply to.
    payload = first_frame[4:] if len(first_frame) > 4 else b""
    for pid in ids:
        frame = build_probe(pid, payload)
        try:
            tls.sendall(frame)
        except (ssl.SSLError, OSError) as e:
            log("  probe id %d send failed: %r" % (pid, e))
            return
        log("  probe -> id %-2d len %-3d bytes: %s" % (pid, len(payload), frame.hex()))
        time.sleep(0.5)


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
            # Take the client's opening frame first, then optionally probe.
            try:
                first = tls.recv(8192)
            except (ssl.SSLError, OSError) as e:
                log("  no opening frame: %r" % (e,))
                return
            if not first:
                log("  peer closed before sending anything")
                return
            log("  app <- %d bytes" % len(first))
            log("      hex: %s" % first[:1024].hex())
            log("      asc: %r" % (first[:512],))
            # Resolve reply mode BEFORE anything else: the opening frame needs
            # an answer too. It is sometimes the only frame the client sends
            # (observed 2026-09-16 15:48) - without this the stub sat silent,
            # hit its own 30s read timeout, closed, and the client logged
            # "disconnected from the hub due to RemoteHostClosedConnection".
            reply = os.environ.get("CRUCIBLE_REPLY", "").strip() or None
            if reply:
                log("  reply mode: %s" % reply)
                out = make_reply(first, reply)
                if out is not None:
                    try:
                        tls.sendall(out)
                        log("      reply(%s) to opening frame -> %s"
                            % (reply, out.hex()))
                    except (ssl.SSLError, OSError) as e:
                        log("      reply(%s) to opening frame failed: %r"
                            % (reply, e))
                        return
            probe(tls, first)
            drain(tls, addr, "after-probe", reply=reply)
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
