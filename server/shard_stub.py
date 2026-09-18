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



# ---------------------------------------------------------------------------
# Entity replication primitives (docs/13)
# ---------------------------------------------------------------------------
# Entity RelativeId is a zigzag-encoded LEB128 varint - protobuf's sint32.
# Read path at rva 0x4a13ac0:
#     mov eax,[raw]; mov ecx,eax; and eax,1; shr ecx,1; neg eax; xor ecx,eax
# i.e. value = (raw >> 1) ^ -(raw & 1). The raw value comes from the varint
# reader at rva 0x4fafcd0, which is a textbook LEB128:
#     and eax,0x7f; shl eax,cl; or [acc],eax; test dl,dl; jns done;
#     add rbx,7; cmp rbx,0x23
# so 7 bits per byte, high bit as the continuation flag, and a 35-bit ceiling
# (five bytes). The caller asserts against 0xffffff - "Requested Id out of
# range" - so ids themselves are 24-bit.
ENTITY_ID_MAX = 0xFFFFFF


def encode_varint(value):
    """LEB128, low 7 bits per byte, high bit = continuation."""
    if value < 0:
        raise ValueError("varint is unsigned; zigzag first")
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_varint(data, offset=0):
    """Returns (value, next_offset). Stops at the 35-bit ceiling like the
    engine does."""
    value = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, offset
        shift += 7
        if shift >= 0x23:
            break
    raise ValueError("varint did not terminate within the 35-bit ceiling")


def encode_entity_id(value):
    """Zigzag then varint, as RelativeId is written."""
    if abs(value) > ENTITY_ID_MAX:
        raise ValueError("entity id out of the engine's 24-bit range")
    return encode_varint((value << 1) ^ (value >> 31 if value < 0 else 0))


def decode_entity_id(data, offset=0):
    raw, offset = decode_varint(data, offset)
    return (raw >> 1) ^ -(raw & 1), offset


# TypeAndFlags is one byte, unpacked by the client at rva 0x4b9dc20:
#     movzx ecx,[msg+0x40]
#     shr al,6  ; and al,1 -> +0x0d
#     shr dl,7           -> +0x0c
#     shr al,5  ; and al,1 -> +0x0e
#     shr al,4  ; and al,1 -> +0x0f
#     and eax,0x0f         -> +0x00 (the update type, a dword)
# so:
ENTITY_TYPE_MASK = 0x0F     # low nibble: update type
# The only two values ValidateUpdate (rva 0x4bb4f80) will accept, one per
# connection role. See pack_type_and_flags.
ENTITY_UPDATE_TYPE_3 = 3
ENTITY_UPDATE_TYPE_4 = 4
ENTITY_FLAG_BIT4 = 0x10     # -> +0x0f; gates the field at +0x10 (the slice ref)
ENTITY_FLAG_BIT5 = 0x20     # -> +0x0e
ENTITY_FLAG_BIT6 = 0x40     # -> +0x0d
ENTITY_FLAG_BIT7 = 0x80     # -> +0x0c; checked FIRST and routed to 0x4bb41f0
                            #    (the delete path) before the update path runs


def pack_type_and_flags(update_type, bit4=False, bit5=False, bit6=False,
                        bit7=False):
    """Build the TypeAndFlags byte.

    `update_type` is the low nibble. EntityReplicationManager::ValidateUpdate
    (rva 0x4bb4f80) branches on a role at [this+0x48] and accepts exactly two
    values:

        role 1: type must be 3, else "Unexpected update type coming from
                peer server" (rva 0x4bb4fc8)
        role 0: type must be 4                       (rva 0x4bb50a8)

    so the candidates are ENTITY_UPDATE_TYPE_3 and _4 rather than any of the
    sixteen the nibble allows. Which of the two a server->client spawn should
    carry is the one thing left to settle, and it is a two-way choice the
    client answers in a single run.
    """
    if not 0 <= update_type <= 0x0F:
        raise ValueError("update type must fit the low nibble")
    v = update_type
    for on, bit in ((bit4, ENTITY_FLAG_BIT4), (bit5, ENTITY_FLAG_BIT5),
                    (bit6, ENTITY_FLAG_BIT6), (bit7, ENTITY_FLAG_BIT7)):
        if on:
            v |= bit
    return bytes([v])


def unpack_type_and_flags(byte):
    """Inverse of pack_type_and_flags; mirrors the client's own unpack."""
    return {
        "type": byte & ENTITY_TYPE_MASK,
        "bit4": bool(byte & ENTITY_FLAG_BIT4),
        "bit5": bool(byte & ENTITY_FLAG_BIT5),
        "bit6": bool(byte & ENTITY_FLAG_BIT6),
        "bit7": bool(byte & ENTITY_FLAG_BIT7),
    }


def build_slice_entry_id(asset_hash):
    """`SliceEntryId` — the reference that makes the client build an entity
    from a slice it already has on disk.

    Resolved by following the types rather than guessing from the name:
    `SliceEntryId` (body rva 0x4bfb1d0) serialises exactly ONE field, named
    `Slice`, of type `StringAsset`. `StringAsset` (rva 0xa88e90) in turn
    serialises one field named `HashString` through the ranged-integer slot
    (+0x38), and its descriptor (rva 0x7480dd0) declares the full uint32
    range — so it is **four bytes big endian**, not a varint.

    (The 0x7f / 0x3fff / 0x1fffff / 0xfffffff comparisons further down
    0x4bfb1d0 are a separate varint length computed over a different field at
    +0x90; they are not this id. Mistaking them for it is what produced the
    first, wrong version of this function.)

    The hash is resolved against the level's own AssetHashMap
    (`levels/<level>/commondata/stringhashlibrary.xml`), e.g. 501823405 =
    slices/player/hero/hero_character in practicearena.
    """
    if not 0 <= asset_hash <= 0xFFFFFFFF:
        raise ValueError("asset hash must fit a uint32")
    return asset_hash.to_bytes(4, "big")


def build_entity_message(entity_id, type_and_flags, slice_hash=None,
                         payload=b""):
    """One element of the EntityMessages vector.

        RelativeId (zigzag varint)
        u8 TypeAndFlags
        SliceEntryId              -- ONLY when bit7 is clear and bit4 is set
        u16 PayloadSize
        <payload>

    The conditional is the client's own, at rva 0x4b9dc5b: it tests the
    delete flag (bit 7) first and skips everything, then tests bit 4 and only
    then calls the SliceEntryId serialiser (0x4b9e620) into +0x10.
    """
    flags = type_and_flags[0]
    out = encode_entity_id(entity_id) + type_and_flags
    wants_slice = (not flags & ENTITY_FLAG_BIT7) and (flags & ENTITY_FLAG_BIT4)
    if wants_slice:
        if slice_hash is None:
            raise ValueError("bit 4 is set, so a slice hash is required")
        out += build_slice_entry_id(slice_hash)
    elif slice_hash is not None:
        raise ValueError("a slice hash needs bit 4 set, or the client will "
                         "not read it")
    if len(payload) > 0xFFFF:
        raise ValueError("payload exceeds the u16 PayloadSize field")
    return out + len(payload).to_bytes(2, "big") + payload


# EntityMessages.Size is declared 0..2048 (descriptor at rva 0x7481d60), so it
# is two bytes big endian and a packet carries at most 2048 messages.
ENTITY_MESSAGES_MAX = 2048


def build_entity_updates(prefix, local_seq, remote_seq, messages,
                         game_time_ms=0):
    """A complete EntityUpdates packet (id 10, serialiser rva 0xe262e0).

        base Packet header
        ServerGameTimePoint { u32 BE GameTimeMs }   (rva 0x77d1d0)
        EntityMessages: u16 BE Size | element[Size]
    """
    if len(messages) > ENTITY_MESSAGES_MAX:
        raise ValueError("at most %d entity messages per packet"
                         % ENTITY_MESSAGES_MAX)
    payload = (ranged_uint32(game_time_ms)
               + len(messages).to_bytes(2, "big")
               + b"".join(messages))
    return build_shard(prefix, 10, local_seq, remote_seq, payload)


def ranged_uint32(value):
    """Full-range u32, four bytes big endian — the encoding docs/11 recovered
    for every integer whose declared range spans the whole type."""
    return value.to_bytes(4, "big")


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


def accept_variants(prefix, region, their_seq, our_seq, shard_id):
    """Candidate shapes for the Accept reply to a shard Connect.

    Unlike the hub, the client re-sends Connect about twice a second for the
    ~4 seconds it spends in ConnectingToServerShard, so several shapes can be
    tried per READY instead of one per reconnect.

    Accept (rva 0xe25eb0) deserialises as the base Packet header plus a single
    u8 ServerShardId. What is NOT known is the transport prefix rule - the
    client used one byte (0x51) on Connect and two (0xf0 0x01) on its type-2
    control packet, matching none of the BasePacket type values - so several
    variants differ only in that.

    The CRC is set correctly on all of them, but it is probably not checked:
    the CRC helper (rva 0x4f20970) has exactly one caller in the whole
    executable, and that caller is the SEND path (rva 0x4f23038). Nothing
    validates it on receive.
    """
    their_window = int.from_bytes(region[6:10], "big")
    sid = bytes([shard_id & 0xFF])
    return [
        ("0 echo prefix, seq=ours, remote=theirs, 1-byte payload",
         build_shard(prefix, 6, our_seq, their_seq, sid)),
        ("1 same but IsReliable set",
         build_shard(prefix, 6, our_seq, their_seq, sid, reliable=1)),
        ("2 mirror their SequenceWindow",
         build_shard(prefix, 6, our_seq, their_seq, sid,
                     seq_window=their_window)),
        ("3 one-byte prefix 0x51",
         build_shard(b"\x51", 6, our_seq, their_seq, sid)),
        ("4 two-byte prefix f0 01",
         build_shard(b"\xf0\x01", 6, our_seq, their_seq, sid)),
        ("5 no prefix at all",
         build_shard(b"", 6, our_seq, their_seq, sid)),
        ("6 LocalSequence mirrors theirs",
         build_shard(prefix, 6, their_seq, their_seq, sid)),
        ("7 empty payload",
         build_shard(prefix, 6, our_seq, their_seq, b"")),
        ("8 zero prefix byte (BasePacket::Unencrypted)",
         build_shard(b"\x00", 6, our_seq, their_seq, sid)),
    ]


def shard_reply(data, state):
    """Answer one datagram, or None.

    - PacketType 2: a transport-level control packet, ~2/second, one payload
      byte. Mirrored with RemoteSequence set to the sequence being acked.
    - PacketType 5 (Connect): answered with Accept (id 6). With
      CRUCIBLE_SHARD_PROBE=1 each Connect gets the next candidate shape.
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
    # Anything that is not the type-2 control packet or a Connect retry means
    # the client accepted our Accept and moved on - that is the only evidence
    # available that the handshake landed, since nothing else is logged. Use
    # it to try a spawn, alternating the two update types ValidateUpdate
    # allows, so one run answers the last open question instead of two.
    if ptype not in (2, 5) and os.environ.get("CRUCIBLE_SHARD_SPAWN", "1") == "1":
        n = state.get("spawn", 0)
        state["spawn"] = n + 1
        update_type = (ENTITY_UPDATE_TYPE_3, ENTITY_UPDATE_TYPE_4)[n % 2]
        slice_hash = int(os.environ.get("CRUCIBLE_SPAWN_SLICE", "501823405"))
        msg = build_entity_message(
            state.get("next_entity", 1),
            pack_type_and_flags(update_type, bit4=True),
            slice_hash=slice_hash)
        state["next_entity"] = state.get("next_entity", 1) + 1
        log("      SPAWN probe: update type %d, slice %d, entity %d"
            % (update_type, slice_hash, state.get("next_entity", 1) - 1))
        return build_entity_updates(prefix, state["seq"], their_seq, [msg],
                                    game_time_ms=state.get("game_time", 0))

    if ptype == 5:
        shard_id = int(os.environ.get("CRUCIBLE_SHARD_ID", "0"))
        if os.environ.get("CRUCIBLE_SHARD_PROBE") == "1":
            variants = accept_variants(prefix, region, their_seq,
                                       state["seq"], shard_id)
            i = state.get("variant", 0)
            state["variant"] = i + 1
            name, out = variants[i % len(variants)]
            log("      ACCEPT PROBE variant %s (%d bytes)" % (name, len(out)))
            return out
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
