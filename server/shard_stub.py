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
import zlib

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


# Transport-level packet type, the FIRST byte of every shard datagram
# (switch at rva 0x4fbbb90).
UNENCRYPTED = 0x00
DTLS_HANDSHAKE = 0xCC
ENCRYPTED = 0xFF
BASE_PACKET_TYPES = {
    UNENCRYPTED: "Unencrypted",
    DTLS_HANDSHAKE: "DtlsHandshake",
    ENCRYPTED: "Encrypted",
}

# Base NovaNet::Packet header, rva 0x4f212f0, widths from each field's
# declared range (the same range-compressed encoding as the hub):
#   u16 PacketType, u16 LocalSequence, u16 RemoteSequence,
#   u32 SequenceWindow, u8 IsReliable, [u16 ReliableSequence if IsReliable]
# i.e. 11 bytes, or 13 when the reliable flag is set.


def describe(data):
    """Best-effort read of the leading bytes, without over-committing.

    The first byte is the transport's BasePacket type (switch at rva
    0x4fbbb90): 0x00 Unencrypted, 0xCC DtlsHandshake, 0xFF Encrypted. Only
    Unencrypted datagrams can be read here - with net_UdpUseEncryption 0 in
    user.cfg the client should never send the other two.
    """
    if not data:
        return "empty datagram"
    kind = BASE_PACKET_TYPES.get(data[0], "unknown(%#04x)" % data[0])
    parts = ["BasePacket::%s" % kind]
    if data[0] != UNENCRYPTED:
        parts.append("encrypted or handshake - not decodable here")
        return ", ".join(parts)
    body = data[1:]
    if len(body) < 11:
        parts.append("body %d bytes, shorter than the 11-byte header" % len(body))
        return ", ".join(parts)
    ptype = int.from_bytes(body[0:2], "big")
    parts.append("PacketType=%d (%s)" % (ptype, PACKET_NAMES.get(ptype, "?")))
    parts.append("LocalSeq=%d" % int.from_bytes(body[2:4], "big"))
    parts.append("RemoteSeq=%d" % int.from_bytes(body[4:6], "big"))
    parts.append("SeqWindow=%#x" % int.from_bytes(body[6:10], "big"))
    reliable = body[10]
    parts.append("IsReliable=%d" % reliable)
    off = 11
    if reliable:
        if len(body) >= 13:
            parts.append("ReliableSeq=%d" % int.from_bytes(body[11:13], "big"))
        off = 13
    parts.append("payload=%d bytes" % max(0, len(body) - off))
    return ", ".join(parts)



def parse_shard(data):
    """Split a datagram into (prefix, region, crc) or None.

    Verified layout, from the send path (NovaNet::UdpSocketWrapper::SendPacket,
    rva 0x4f22d30) and from live captures:

        [transport prefix, 1 or 2 bytes] [serialised UdpPacketHeader] [u32 Crc32]

    The Crc32 field is the last thing serialised (rva 0x4f23057), and the value
    is a standard zlib CRC-32 - init 0xFFFFFFFF, table at rva 0x6616060 whose
    first entries are the usual 0x77073096..., final complement - computed over
    the serialised buffer (rva 0x4f23038).

    The prefix length varies: the Connect datagram used one byte (0x51) and the
    type-2 control datagram used two (0xf0 0x01). It is found by locating the
    offset at which the base Packet header parses sanely, which is how the
    two were told apart in the first place. That is a heuristic, not a
    recovered rule - the byte's meaning is still unknown.
    """
    if len(data) < 16:
        return None
    for plen in (1, 2):
        region = data[plen:-4]
        if len(region) < 11:
            continue
        ptype = int.from_bytes(region[0:2], "big")
        if ptype in PACKET_NAMES or ptype <= 4:
            return data[:plen], region, data[-4:]
    return None


def build_shard(prefix, packet_type, local_seq, remote_seq, payload=b"",
                seq_window=0, reliable=0):
    """One datagram in the layout above, with a correct CRC32.

    Base Packet header (rva 0x4f212f0), widths from each field's declared
    range: u16 PacketType, u16 LocalSequence, u16 RemoteSequence,
    u32 SequenceWindow, u8 IsReliable, [u16 ReliableSequence if IsReliable].
    """
    region = (packet_type.to_bytes(2, "big")
              + local_seq.to_bytes(2, "big")
              + remote_seq.to_bytes(2, "big")
              + seq_window.to_bytes(4, "big")
              + bytes([1 if reliable else 0]))
    if reliable:
        region += local_seq.to_bytes(2, "big")
    region += payload
    crc = zlib.crc32(region) & 0xFFFFFFFF
    return prefix + region + crc.to_bytes(4, "big")


def shard_reply(data, state):
    """Answer one datagram, or None.

    Two things the client sends while trying to reach the shard:
      - PacketType 2, one payload byte: a transport-level control packet, sent
        ~2/second. Answered by mirroring it with RemoteSequence set to the
        sequence being acknowledged.
      - PacketType 5 (Connect), carrying PacketHash/ComponentHash/StructHash,
        NetworkProtocolVersion and a bool. Answered with Accept (id 6), which
        deserialises as the base header plus one u8 ServerShardId (rva
        0xe25eb0).

    Neither reply is confirmed - this is the first attempt at speaking back.
    """
    parsed = parse_shard(data)
    if parsed is None:
        return None
    prefix, region, _crc = parsed
    ptype = int.from_bytes(region[0:2], "big")
    their_seq = int.from_bytes(region[2:4], "big")
    state["seq"] = state.get("seq", 0) + 1
    if ptype == 2:
        return build_shard(prefix, 2, state["seq"], their_seq,
                           payload=region[11:12] or b"\x00")
    if ptype == 5:
        shard_id = int(os.environ.get("CRUCIBLE_SHARD_ID", "0"))
        return build_shard(prefix, 6, state["seq"], their_seq,
                           payload=bytes([shard_id & 0xFF]))
    return None


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
    state = {}
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
            elif reply == "accept":
                out = shard_reply(data, state)
                if out is not None:
                    sock.sendto(out, addr)
                    log("      reply(accept) -> %s" % out.hex())
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
