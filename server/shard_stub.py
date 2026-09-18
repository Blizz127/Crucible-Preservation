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
# Order the spawn probe walks: the two ValidateUpdate calls out first, then
# the rest of the plausible low nibble, so an early trial is likely to be the
# right one but nothing is excluded.
ENTITY_UPDATE_TYPE_SWEEP = (3, 4, 0, 1, 2, 5, 6, 7)
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


# ---------------------------------------------------------------------------
# The 51-channel checksum, solved 2026-09-17
# ---------------------------------------------------------------------------
# The trailer on that channel is a standard zlib CRC-32, but over MORE than the
# datagram contains:
#
#     crc32( datagram[1:-4] + HIDDEN + b"\x00\x00\x00\x00" ) == trailer
#
# i.e. the region after the 1-byte prefix, then four bytes that are never
# transmitted, then the zeroed Crc32 slot. Eight earlier theories missed it
# because every one of them assumed the input was a contiguous slice OF the
# datagram.
#
# How it was found: consecutive Connect datagrams differ in exactly one byte
# (the LocalSequence low byte) plus the trailer, so the trailer's XOR delta
# pins the distance from that byte to the END of the checksummed input. That
# distance is 72 for every pair, which puts the end at datagram offset 77 -
# four bytes past a 73-byte datagram. Solving for an 8-byte suffix then gives
# a value that is constant across every packet and across two sessions, and
# verifies by direct computation.
#
# The hidden four bytes depend on the input LENGTH (a 16-byte type-1 datagram
# needs 5223f476 instead), which is the signature of a hidden value at the
# START rather than the end - most likely a header the transport prepends
# in its own buffer. Replies therefore keep the length of the datagram they
# answer, so the known value applies.
# Better formulation, and the one to use: the same rule expressed as a
# constant SEED rather than a length-dependent suffix.
#
#     crc32( region + 8 zero bytes , init = SHARD_CRC_SEED ) == trailer
#
# A seed is what a hidden PREFIX looks like algebraically, and unlike the
# suffix form it does not depend on the packet length - so it lets us build
# a correctly sized Accept instead of padding to 73 bytes and risking a
# "data not fully consumed" rejection. Verified on 87/87 of the 73-byte
# datagrams across two sessions.
#
# It does NOT hold for the 16-byte type-1 datagrams (4 samples, 4 different
# seeds), so those have some other structure; noted, not needed yet.
SHARD_CRC_SEED = 0x8A31DC2A
SHARD_CRC_TAIL_ZEROS = 8

SHARD_HIDDEN = {
    73: bytes.fromhex("792cce7f"),   # suffix form, 73-byte datagrams
    16: bytes.fromhex("5223f476"),   # 16-byte, does not generalise
}


def shard_trailer(datagram_len, region):
    """The trailer for a 51-channel datagram, via the constant-seed rule.

    `datagram_len` is accepted for symmetry with the older suffix form but is
    no longer used: the seed formulation is length-independent.
    """
    crc = zlib.crc32(region + b"\x00" * SHARD_CRC_TAIL_ZEROS,
                     SHARD_CRC_SEED) & 0xFFFFFFFF
    return crc.to_bytes(4, "big")


def build_shard_51(template, region):
    """A 51-channel datagram with a correct trailer.

    `region` must be the same length as the one in `template`, because the
    hidden bytes are length-dependent and only two lengths are known.
    """
    prefix = template[:1]
    tail = shard_trailer(len(template), region)
    if tail is None:
        raise ValueError("no hidden value known for length %d" % len(template))
    return prefix + region + tail


def _raw_crc0(data):
    """CRC-32 register with init 0 and no final xor."""
    return zlib.crc32(data, 0xFFFFFFFF) ^ 0xFFFFFFFF


def forge_from(inbound, edits):
    """Mutate the client's own datagram and repair the trailer by linearity.

    This sidesteps the unsolved `51` channel checksum entirely. CRC-32 is
    linear, so for two equal-length buffers

        crc(M1) ^ crc(M2) = raw_crc0(M1 ^ M2)

    and with init 0 a run of leading zero bytes leaves the register at zero.
    So the new trailer can be computed from the old one, the bytes changed,
    and their distance from the END of the checksummed region - **without
    knowing where that region starts**, which is the one thing we could not
    work out.

    The trailer is the last four bytes, so the region ends at len-4. `edits`
    is {datagram_offset: new_byte}; the length must not change, because the
    identity above only holds for equal lengths.

    Validated against the f0 01 channel, where the covered region IS known:
    predicting a mutated packet's trailer this way reproduces the directly
    computed CRC exactly.
    """
    p = bytearray(inbound)
    end = len(p) - 4
    old_tail = int.from_bytes(p[end:], "big")
    for off, val in edits.items():
        if off >= end:
            raise ValueError("edit at %d is inside the trailer" % off)
        p[off] = val
    delta = bytes(a ^ b for a, b in zip(inbound[:end], bytes(p[:end])))
    first = next((i for i, b in enumerate(delta) if b), None)
    if first is None:
        return bytes(inbound)
    new_tail = old_tail ^ _raw_crc0(delta[first:])
    p[end:] = new_tail.to_bytes(4, "big")
    return bytes(p)


def accept_variants(prefix, region, their_seq, our_seq, shard_id, inbound):
    """Accept shapes, revised after the 2026-09-17 20:04 session.

    That session answered all 166 datagrams with nine shapes and the client
    re-sent Connect 40 times regardless, so none was accepted. It also showed
    what the earlier guessing could not:

    - the prefix is a CHANNEL id, constant per channel (`51` carries Connect
      and the type-1 disconnect, `f0 01` carries the type-2 control stream);
    - the two channels checksum differently - `f0 01` is plain
      crc32(region), the `51` channel reproduces as nothing.

    The observable that matters is whether the client STOPS re-sending
    Connect. These variants therefore attack the transport, not the payload:
    the payload is a constant one-byte ServerShardId throughout.
    """
    sid = bytes([shard_id & 0xFF])
    their_trailer = inbound[-4:]

    def raw(pre, body):
        return pre + body

    base_region = (b"\x00\x06" + our_seq.to_bytes(2, "big")
                   + their_seq.to_bytes(2, "big") + b"\x00\x00\x00\x00"
                   + b"\x00" + sid)

    # The forged shapes come FIRST: they are the only ones whose trailer is
    # guaranteed valid on the 51 channel, because they are the client's own
    # packet with a few bytes changed and the CRC repaired by linearity.
    # Everything after them is a fallback that assumes crc32(region), which
    # the client's own 51-channel packets demonstrably do not satisfy.
    # A full forge: every field an Accept needs, with the trailing Connect
    # payload zeroed. The linearity identity allows arbitrary edits as long
    # as the LENGTH is unchanged, so the datagram stays 73 bytes and carries
    # zero padding after the ServerShardId. The earlier forge kept the
    # client's own 57-byte Connect payload and its sequence numbers - a valid
    # trailer on a semantically wrong packet, which is probably why it was
    # dropped.
    plen = len(prefix)
    hdr = plen + 11                      # first payload byte
    end = len(inbound) - 4               # region end / trailer start

    def full_forge(local, remote, reliable=0, pad=0x00):
        edits = {
            plen + 0: 0x00, plen + 1: 0x06,            # PacketType = 6
            plen + 2: (local >> 8) & 0xFF, plen + 3: local & 0xFF,
            plen + 4: (remote >> 8) & 0xFF, plen + 5: remote & 0xFF,
            plen + 6: 0x00, plen + 7: 0x00,
            plen + 8: 0x00, plen + 9: 0x00,            # SequenceWindow = 0
            plen + 10: 1 if reliable else 0,
        }
        edits[hdr] = shard_id & 0xFF                   # ServerShardId
        for i in range(hdr + 1, end):                  # zero the rest
            edits[i] = pad
        return forge_from(inbound, edits)

    forged_full = full_forge(our_seq, their_seq)
    forged_full_reliable = full_forge(our_seq, their_seq, reliable=1)

    # SOLVED trailer: build the Accept region and checksum it with the rule
    # recovered from the client's own packets - crc32(region + hidden + 0000)
    # - keeping the datagram length identical so the known hidden value
    # applies. This is the first reply whose trailer is right rather than
    # hoped for.
    def accept_region(local, remote, reliable=0):
        reg = bytearray(12)
        reg[0:2] = b"\x00\x06"
        reg[2:4] = local.to_bytes(2, "big")
        reg[4:6] = remote.to_bytes(2, "big")
        reg[10] = 1 if reliable else 0
        reg[11] = shard_id & 0xFF
        return bytes(reg)

    def solved_exact_suffix(local, remote, reliable=0):
        """Same Accept, trailer under the SUFFIX formulation.

        The seed and suffix forms were both fitted on 68-byte regions, where
        they agree by construction. At any other region length they diverge,
        and nothing observed says which is the real rule - so send both and
        let the client decide. This one is
        crc32(region + 792cce7f + 00000000) with the standard seed.
        """
        reg = accept_region(local, remote, reliable)
        tail = (zlib.crc32(reg + SHARD_HIDDEN[73] + b"\x00" * 4)
                & 0xFFFFFFFF).to_bytes(4, "big")
        return prefix + reg + tail

    def solved_exact(local, remote, reliable=0):
        """A correctly SIZED Accept: 12-byte region, no padding.

        Possible now that the checksum is expressed as a constant seed rather
        than a length-dependent suffix. Padding to 73 bytes leaves 56 bytes
        the Accept deserialiser will not consume, and this engine does assert
        on unconsumed data elsewhere, so the short form is the better bet.
        """
        reg = bytearray(12)
        reg[0:2] = b"\x00\x06"
        reg[2:4] = local.to_bytes(2, "big")
        reg[4:6] = remote.to_bytes(2, "big")
        reg[10] = 1 if reliable else 0
        reg[11] = shard_id & 0xFF
        return prefix + bytes(reg) + shard_trailer(0, bytes(reg))

    def solved(local, remote, reliable=0):
        reg = bytearray(len(inbound) - len(prefix) - 4)
        reg[0:2] = b"\x00\x06"                      # PacketType = Accept
        reg[2:4] = local.to_bytes(2, "big")
        reg[4:6] = remote.to_bytes(2, "big")
        reg[10] = 1 if reliable else 0
        reg[11] = shard_id & 0xFF                    # ServerShardId
        return build_shard_51(inbound, bytes(reg))

    solved_accept = solved_exact(our_seq, their_seq)
    solved_reliable = solved_exact(our_seq, their_seq, reliable=1)
    solved_remote1 = solved_exact(our_seq, 1)
    solved_padded = solved(our_seq, their_seq)
    solved_sfx = solved_exact_suffix(our_seq, their_seq)
    solved_sfx_rel = solved_exact_suffix(our_seq, their_seq, reliable=1)

    return [
        ("0 SOLVED trailer: Accept, crc32(region+hidden+0000)",
         solved_accept),
        ("1 SOLVED trailer, IsReliable set",
         solved_reliable),
        ("2 SOLVED trailer, RemoteSequence 1",
         solved_remote1),
        ("3b SUFFIX-form trailer, exact size",
         solved_sfx),
        ("3c SUFFIX-form trailer, exact size, IsReliable",
         solved_sfx_rel),
        ("3d SEED-form trailer, padded to 73 bytes",
         solved_padded),
        ("3 trailer copied verbatim from the client's packet",
         raw(prefix, base_region + their_trailer)),
        ("4 correct crc32(region), for comparison",
         build_shard(prefix, 6, our_seq, their_seq, sid)),
        ("5 Accept on the f0 01 control channel",
         build_shard(b"\xf0\x01", 6, our_seq, their_seq, sid)),
        ("6 two-byte prefix 51 00",
         build_shard(b"\x51\x00", 6, our_seq, their_seq, sid)),
        ("7 RemoteSequence 1, as the client's own Connect carries",
         build_shard(prefix, 6, our_seq, 1, sid)),
        ("8 reply as type 2 on the control channel",
         build_shard(b"\xf0\x01", 2, our_seq, their_seq, b"\x00")),
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
        # The control channel is the only place our checksum is provably
        # right (crc32(region), matching the client's own packets), so it is
        # the only place a FIELD mistake can be isolated. The client's own
        # type-2 RemoteSequence stays 0 for an entire session; we have been
        # echoing theirs instead. Cycle the candidates and watch whether the
        # client's RemoteSequence ever becomes non-zero - that is the one
        # unambiguous signal that it accepted something from us.
        if os.environ.get("CRUCIBLE_CTRL_PROBE") == "1":
            n = state.get("ctrl", 0)
            state["ctrl"] = n + 1
            pay = region[11:12] or b"\x00"
            variants = [
                ("RemoteSeq 0, like the client sends",
                 build_shard(prefix, 2, state["seq"], 0, pay)),
                ("RemoteSeq = their LocalSeq (previous behaviour)",
                 build_shard(prefix, 2, state["seq"], their_seq, pay)),
                ("LocalSeq 0",
                 build_shard(prefix, 2, 0, 0, pay)),
                ("LocalSeq mirrors theirs, RemoteSeq 0",
                 build_shard(prefix, 2, their_seq, 0, pay)),
                ("payload byte 01",
                 build_shard(prefix, 2, state["seq"], 0, b"\x01")),
                ("IsReliable set",
                 build_shard(prefix, 2, state["seq"], 0, pay, reliable=1)),
                ("verbatim echo of their datagram",
                 data),
                ("forged: their datagram with RemoteSeq = their LocalSeq",
                 forge_from(data, {len(prefix) + 4: (their_seq >> 8) & 0xFF,
                                   len(prefix) + 5: their_seq & 0xFF})),
            ]
            name, out = variants[n % len(variants)]
            if n % len(variants) == 0 or n < len(variants):
                log("      CTRL PROBE variant %d: %s"
                    % (n % len(variants), name))
            return out
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
        # ValidateUpdate only constrains the nibble to 3 or 4 for roles 0
        # and 1, and the replicator constructor (0x4bb1500) sets the role to
        # 2, which takes neither branch - so the narrowing is not safe. The
        # nibble IS read downstream (mov edi,[msg] at 0x4bb5538), so sweep the
        # low values, leading with the two ValidateUpdate names.
        update_type = ENTITY_UPDATE_TYPE_SWEEP[n % len(ENTITY_UPDATE_TYPE_SWEEP)]
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
                                       state["seq"], shard_id, data)
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
