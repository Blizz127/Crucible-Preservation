#!/usr/bin/env python3
"""Nova server-shard stub: UDP listener on 127.0.0.1:18878 (revival tool).

Why UDP
-------
The hub hands the client a shard address in GetServerShardAddress::Success
(see docs/11). Once the client accepts it, it logs:

    ClientNetworkAgent::ConnectToServerShard(): Connecting to Nova Server
      Shard at: 127.0.0.1:18878
    Attempted to connect to a server shard while the UDP client connection
      was still open, failing the connect attempt

so the shard link is **UDP**, not TLS/TCP like the hub. A TCP listener on
18878 sees nothing at all, which is what happened on the first attempt.

What speaks here
----------------
This is the `NovaGameClientPackets` stream - the protocol docs/10 recovered
the dispatch table for, and which docs/11 established does NOT belong to the
hub port. `NovaGameClientPackets::DispatchPacket` (rva 0xe24300) handles
packet ids 5..12:

    5 Connect, 6 Accept, 7 SyncConsole, 8 SyncConnectionCvars,
    9 ServerConsoleCommand, 10 EntityUpdates, 11 EntityRpcs, 12 ClientMigration

and every packet carries the base `Packet` header (rva 0x4f212f0):

    u16 PacketType, u16 LocalSequence, u16 RemoteSequence,
    u32 SequenceWindow, bool IsReliable, [u16 ReliableSequence if IsReliable]

with the same range-compressed integer encoding as the hub - so those widths
are nominal until each field's descriptor is read. `Accept` (rva 0xe25eb0) is
the base header plus a single `u8 ServerShardId`.

This stub's job is narrow, like match_stub.py's was: record exactly what the
client sends so the handshake can be worked out from real bytes rather than
guesses. It replies only when asked to.

Usage:
  python3 server/shard_stub.py                 # capture only
  CRUCIBLE_SHARD_REPLY=accept python3 server/shard_stub.py
"""
import datetime
import os
import socket
import threading

HOST = "127.0.0.1"
PORT = int(os.environ.get("CRUCIBLE_SHARD_PORT", "18878"))
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.environ.get("CRUCIBLE_LOG") or os.path.join(HERE, "shard_udp.log")
_lock = threading.Lock()

# NovaGameClientPackets ids, from the dispatcher's own name strings
# (docs/10-match-server-tls.md, "The packet layer").
PACKET_NAMES = {
    5: "Connect", 6: "Accept", 7: "SyncConsole", 8: "SyncConnectionCvars",
    9: "ServerConsoleCommand", 10: "EntityUpdates", 11: "EntityRpcs",
    12: "ClientMigration",
}


def log(msg):
    line = "[%s] %s" % (
        datetime.datetime.now().isoformat(timespec="milliseconds"), msg)
    print(line, flush=True)
    with _lock:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def describe(data):
    """Best-effort read of the first bytes, without committing to a layout.

    The hub's framing was u16 id + u16 length; the shard may or may not share
    it. Both readings are printed so the log stays useful even if one is
    wrong - the point of this stub is evidence, not interpretation.
    """
    if len(data) < 2:
        return "too short to interpret"
    be16 = int.from_bytes(data[0:2], "big")
    parts = ["first u16 BE=%d (%s)" % (be16, PACKET_NAMES.get(be16, "?"))]
    if len(data) >= 4:
        parts.append("second u16 BE=%d" % int.from_bytes(data[2:4], "big"))
        parts.append("len-4=%d" % (len(data) - 4))
    return ", ".join(parts)


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    log("listening on %s:%d (UDP, log: %s)" % (HOST, PORT, LOG))
    log("expect: NovaGameClientPackets after the hub hands out this address")

    reply = os.environ.get("CRUCIBLE_SHARD_REPLY", "").strip() or None
    if reply:
        log("reply mode: %s" % reply)

    total = 0
    peers = set()
    try:
        while True:
            data, addr = sock.recvfrom(65535)
            total += len(data)
            if addr not in peers:
                peers.add(addr)
                log("--- first datagram from %s ---" % (addr,))
            log("  <- %d bytes from %s (total %d)" % (len(data), addr, total))
            log("      %s" % describe(data))
            log("      hex: %s" % data[:1024].hex())
            log("      asc: %r" % (data[:256],))
            if reply == "echo":
                sock.sendto(data, addr)
                log("      reply(echo) -> %d bytes" % len(data))
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
