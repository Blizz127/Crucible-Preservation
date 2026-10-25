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

Wire format (verified 2026-09-19)
---------------------------------
The retail client sends raw LZ4 blocks containing header, payload and a
big-endian CRC32. Replies prepend BasePacket::Unencrypted (00) to that same
compressed block. See docs/15-shard-lz4.md for live Connected evidence.

Packet ids 5..12 below are SERVER-to-client ids; client EntityRpcs is also
11 (retail getter 1a97350); other client game-packet ids remain unestablished. A transport connection alone does not implement game mode,
player replication, possession or simulation.

Usage:
  python3 server/shard_stub.py                 # capture only
  CRUCIBLE_SHARD_REPLY=accept python3 server/shard_stub.py

Historical probe helpers below are retained for reproducing old experiments;
their checksum/framing theories are superseded. Leave CRUCIBLE_SHARD_PROBE
unset for the verified LZ4 path. Entity probes are opt-in and unverified.
"""
import collections
from dataclasses import dataclass, replace
import datetime
import os
import socket
import struct
import threading
import time
import zlib

import lz4.block
import client_input
import hero_loadout
import local_sessions
import solo_modes
import math

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
    """Describe a validated, decompressed client packet."""
    parsed = parse_shard(data)
    if parsed is None:
        return "invalid LZ4 packet or CRC"
    _, body, _ = parsed
    parts = ["LZ4 (%d wire bytes)" % len(data)]
    ptype = int.from_bytes(body[0:2], "big")
    parts.append("PacketType=%d (%s)" % (ptype, PACKET_NAMES.get(ptype, "?")))
    parts.append("LocalSeq=%d" % int.from_bytes(body[2:4], "big"))
    parts.append("RemoteSeq=%d" % int.from_bytes(body[4:6], "big"))
    parts.append("SeqWindow=%#x" % int.from_bytes(body[6:10], "big"))
    reliable = body[10] & 1
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
# Entity counter deltas first pack a category bit (0x4b9daa0), then use
# signed zigzag and LEB128 (0x4a13ac0 / 0x4fafcd0). Counter zero and 0xffffff
# are reserved. See docs/16 for the live assertion that exposed the missing
# outer transform; zigzag alone maps the first counter 1 back to zero.
ENTITY_ID_MAX = 0xFFFFFE  # zero and 0xffffff are reserved (0x4b9db53).

# Solo-session entity ids, as spawned by the bootstrap (docs/16, docs/18).
PLAYER_CONNECTION_ID = 0x01000001   # carries the client's input RPC
PLAYER_CHARACTER_ID = 0x01000002    # the controlled hero
HUNTER_BOT_IDS = (0x01000003, 0x01000004, 0x01000005)
# Ascending network component types shared by the installed marine_character
# slice (docs/16); Health is 79, Location is 145.
MARINE_SHARED_TYPES = (0, 10, 13, 18, 21, 24, 27, 44, 46, 48, 69, 79, 80,
                       98, 99, 101, 102, 107, 110, 115, 120, 125, 128, 132,
                       133, 143, 145, 146, 156, 158, 163, 165, 166)
MARINE_HEALTH_MAX = 800.0   # observed live, live-alpha-entry.json (docs/18)


@dataclass(frozen=True)
class BotSlot:
    """One solo bot. The roster, the spawns, the arena and the squad all read
    the same slots, so the entities they describe always agree (docs/37)."""
    entity: int
    hero: str = 'marine'               # hero_loadout key: slice, class, primary
    position: tuple = None
    team: int = 2
    health_max: float = MARINE_HEALTH_MAX


# The Saboteur's health, from the player-damage track's table (docs/37).
SABOTEUR_HEALTH_MAX = 800.0


def guide_saboteur_enabled(profile=None):
    """CRUCIBLE_SOLO_GUIDE_SABOTEUR=1 in a HOTH_GUIDE profile (docs/37)."""
    profile = solo_modes.selected() if profile is None else profile
    return (os.environ.get('CRUCIBLE_SOLO_GUIDE_SABOTEUR') == '1'
            and profile.mode_profile is solo_modes.MODES['hoth-guide']
            and bool(profile.bot_positions))


def solo_bot_slots(profile=None):
    """The selected profile's bots: one Marine per authored bot position, the
    screenshot-accepted Alpha Hunters baseline (docs/18).

    With CRUCIBLE_SOLO_GUIDE_SABOTEUR=1 a HOTH_GUIDE profile has instead its
    one retail hostile, a Saboteur guide bot, at the first bot position.
    Retail spawns it at the first hive's heart when the hive dies; with no
    hives modelled, the placeholder position stands in (docs/37).
    """
    profile = solo_modes.selected() if profile is None else profile
    if guide_saboteur_enabled(profile):
        return (BotSlot(HUNTER_BOT_IDS[0], 'saboteur', profile.bot_positions[0],
                        team=profile.bot_team, health_max=SABOTEUR_HEALTH_MAX),)
    return tuple(BotSlot(entity, 'marine', position)
                 for entity, position in zip(HUNTER_BOT_IDS, profile.bot_positions))


def bot_hero(entity):
    """The hero key of the solo bot with this network id, else None."""
    return next((slot.hero for slot in solo_bot_slots() if slot.entity == entity), None)


# docs/37: HOTH_GUIDE's hive, behind CRUCIBLE_SOLO_GUIDE_HIVE=1. Its id stays
# clear of HUNTER_BOT_IDS, which other paths treat as Marine-typed bots.
HIVE_ENTITY_ID = 0x01000006
HIVE_SLICE_HASH = 3885309295    # stringhashlibrary: slices/creatures/hive/hive
# Its shared component types, derived from the exe and validated against live
# captures (snapshots/guide-objectives-20261025/objective-shared-sets.json).
HIVE_SHARED_TYPES = (5, 8, 13, 18, 24, 27, 44, 46, 79, 80, 98, 101, 107, 115,
                     132, 145, 146, 156, 158, 163, 165, 166)
HIVE_HEALTH_MAX = 5000.0        # hive.dynamicslice
HIVE_TEAM = 3                   # it has none: everyone's enemy
# hive_outofcombatregen.cdl: HiveOutOfCombat counts 1/s from the last hit and
# at 19.5 runs DoHealing 100 every 1.0 s until full; a hit stops it.
HIVE_REGEN_AFTER = 19.5
HIVE_REGEN_STEP = 100.0
HIVE_REGEN_INTERVAL = 1.0
HIVE_DROP_DISTANCE = 30.0       # the placeholder spot's distance from the drop


# docs/37: the hive's heart, behind CRUCIBLE_SOLO_GUIDE_HEART=1 with the hive.
HEART_ENTITY_ID = 0x01000007
HEART_SLICE_HASH = 3871577199   # stringhashlibrary: slices/interactive/cruciheart
HEART_SHARED_TYPES = (18, 44, 46, 69, 83, 98, 101, 145, 165)
# cruciheart's interaction: 10.0 s within 2.9 m, one interactor at a time.
HEART_CAPTURE_RADIUS = 2.9
HEART_CAPTURE_SECONDS = 10.0
# An Interact press counts for a capture start this long after its input.
HEART_INTERACT_WINDOW = 0.5
# Mode 1 only: in range this long with no Interact seen, the capture starts
# anyway, logged as a fallback, so a misread key still reaches the ending.
HEART_FALLBACK_SECONDS = 15.0
# hoth_guide_win_condition.cdl: a team score of 1 raises MatchOverEvent 12.0 s
# later; matchover_set_teamranks_and_endmatch_flag ranks the winners first.
HEART_MATCH_OVER_DELAY = 12.0


def guide_hive_enabled(profile=None):
    """CRUCIBLE_SOLO_GUIDE_HIVE=1 in a HOTH_GUIDE profile (docs/37)."""
    profile = solo_modes.selected() if profile is None else profile
    return (os.environ.get('CRUCIBLE_SOLO_GUIDE_HIVE') == '1'
            and profile.mode_profile is solo_modes.MODES['hoth-guide'])


def guide_hive_position(profile):
    """Where the hive stands, on the level's navigation mesh, or None.

    A placeholder either way: which hive node retail's overlord picks is not
    decoded. HotH_02 uses its authored hive node nearest the drop. A map with
    none (april2018) gets the point HIVE_DROP_DISTANCE from the drop, directly
    away from the first bot point, off the hunters' circuits, turning in
    45-degree steps until a point is on the mesh.
    """
    import hunter_patrol
    import navigation
    drop = profile.player_position
    nodes = profile.map_profile.hive_spawns if profile.map_profile else ()
    mesh = navigation.installed_mesh(hunter_patrol.level_key(profile.level))
    if nodes:
        node = min((spot for _, _, spot in nodes), key=lambda spot: math.dist(spot, drop))
        located = mesh.locate(node, 20.0)
        return None if located is None else located[1]
    bot = profile.bot_positions[0] if profile.bot_positions else (drop[0] - 1., drop[1], drop[2])
    away = math.atan2(drop[1] - bot[1], drop[0] - bot[0])
    for step in (0, 1, -1, 2, -2, 3, -3, 4):
        angle = away + step * math.pi / 4
        located = mesh.locate((drop[0] + HIVE_DROP_DISTANCE * math.cos(angle),
                               drop[1] + HIVE_DROP_DISTANCE * math.sin(angle), drop[2]), 20.0)
        if located is not None:
            return located[1]
    return None


def guide_heart_enabled(profile=None):
    """CRUCIBLE_SOLO_GUIDE_HEART=1 (or =proximity) where the hive can spawn
    (docs/37)."""
    return (os.environ.get('CRUCIBLE_SOLO_GUIDE_HEART') in ('1', 'proximity')
            and guide_hive_enabled(profile))


def build_guide_heart_probe(position, *, previous_id=0):
    """Spawn the heart: every shared root false but Location, as the hive."""
    properties = (build_replication_record(location_bits(HEART_SHARED_TYPES))
                  + pack_practice_spawn_transform(position))
    return build_entity_message(
        HEART_ENTITY_ID & 0xffffff, pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION, bit4=True),
        slice_hash=HEART_SLICE_HASH, slice_entry_index=0, payload=properties,
        previous_id=previous_id)


def guide_heart_messages(state, arena, now, previous_id):
    """The heart's spawn, its capture, and the retail match end (docs/37).

    The spawn goes out on the first tick after the hive dies. A capture
    starts when the living player, within HEART_CAPTURE_RADIUS, presses
    Interact (its momentaryBits bit, docs/25), or just stands there with
    CRUCIBLE_SOLO_GUIDE_HEART=proximity. In mode 1, HEART_FALLBACK_SECONDS
    in range without a press also starts it, logged as a fallback. It
    completes after HEART_CAPTURE_SECONDS in range; leaving or dying resets
    it. Whether
    retail needs the key held throughout is not traced, and there is no
    capture bar. The capture scores 1 for the player's team, and the match
    ends 12.0 s later with that team the winner. Returns (messages,
    previous_id).
    """
    heart = state['guide_heart']
    if not heart['sent']:
        heart['sent'] = True
        log('      GUIDE HEART spawned %08x at (%.1f, %.1f, %.1f)'
            % ((HEART_ENTITY_ID,) + tuple(heart['position'])))
        return ([build_guide_heart_probe(heart['position'], previous_id=previous_id)],
                HEART_ENTITY_ID & 0xffffff)
    if heart['captured_at'] is not None:
        if (now >= heart['captured_at'] + HEART_MATCH_OVER_DELAY
                and 'match_result' not in state):
            team = solo_modes.selected().local_team
            state['match_result'] = {'winner_team': team, 'at_seconds': now,
                                     'reason': 'heart'}
            log('      GUIDE HEART match over: winner team %d' % team)
        return [], previous_id
    player = state.get('player_position')
    near = (player is not None
            and (arena is None or PLAYER_CHARACTER_ID not in arena.entities
                 or arena.alive(PLAYER_CHARACTER_ID))
            and math.dist(player, heart['position']) <= HEART_CAPTURE_RADIUS)
    pressed = (state.get('player_interact_at') is not None
               and now - state['player_interact_at'] <= HEART_INTERACT_WINDOW)
    proximity = os.environ.get('CRUCIBLE_SOLO_GUIDE_HEART') == 'proximity'
    if not near:
        heart['near_since'] = None
    elif heart.get('near_since') is None:
        heart['near_since'] = now
    fallback = (near and not proximity
                and now - heart['near_since'] >= HEART_FALLBACK_SECONDS - 1e-9)
    if near and heart['capture_started'] is None and (pressed or proximity or fallback):
        heart['capture_started'] = now
        log('      GUIDE HEART capture started at %.1f s (%s)'
            % (now, 'interact' if pressed else 'proximity' if proximity
               else 'proximity fallback'))
    elif (near and heart['capture_started'] is not None
          and now - heart['capture_started'] >= HEART_CAPTURE_SECONDS - 1e-9):
        team = solo_modes.selected().local_team
        scores = state.setdefault('team_scores', {})
        scores[team] = scores.get(team, 0) + 1
        heart['captured_at'] = now
        log('      GUIDE HEART captured at %.1f s; team %d score %d'
            % (now, team, scores[team]))
    elif not near and heart['capture_started'] is not None:
        heart['capture_started'] = None
        heart['interrupts'] += 1
        if heart['interrupts'] & (heart['interrupts'] - 1) == 0:
            log('      GUIDE HEART capture interrupted (count %d)' % heart['interrupts'])
    return [], previous_id


def build_guide_hive_probe(position, *, previous_id=0):
    """Spawn the hive: every shared root false but Location, as the spitters."""
    properties = (build_replication_record(location_bits(HIVE_SHARED_TYPES))
                  + pack_practice_spawn_transform(position))
    return build_entity_message(
        HIVE_ENTITY_ID & 0xffffff, pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION, bit4=True),
        slice_hash=HIVE_SLICE_HASH, slice_entry_index=0, payload=properties,
        previous_id=previous_id)


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


def encode_entity_id(value, previous_id=0, local=False):
    """Encode ((counter delta << 1) | kind), THEN zigzag and LEB128.

    The outer transform is at 0x4b9daa0; 0x4a13ac0 only encodes its signed
    intermediate. Kind 0 is category 0x01; kind 1 is category 0xfe.
    The previous counter starts at zero for each EntityMessages vector.
    """
    if not 1 <= value <= ENTITY_ID_MAX or not 0 <= previous_id <= ENTITY_ID_MAX:
        raise ValueError("entity counter must be 1..0xfffffe; previous may be zero")
    relative = ((value - previous_id) << 1) | int(bool(local))
    return encode_varint((relative << 1) ^ (relative >> 31))


def decode_entity_id(data, offset=0, previous_id=0):
    raw, offset = decode_varint(data, offset)
    relative = (raw >> 1) ^ -(raw & 1)
    counter = (relative >> 1) + previous_id
    if not 1 <= counter <= ENTITY_ID_MAX:
        raise ValueError("decoded entity counter is reserved or out of range")
    return counter, offset


# TypeAndFlags is one byte, unpacked by the client at rva 0x4b9dc20:
#     movzx ecx,[msg+0x40]
#     shr al,6  ; and al,1 -> +0x0d
#     shr dl,7           -> +0x0c
#     shr al,5  ; and al,1 -> +0x0e
#     shr al,4  ; and al,1 -> +0x0f
#     and eax,0x0f         -> +0x00 (destination network role, a dword)
# so:
ENTITY_TYPE_MASK = 0x0F     # low nibble: destination network role
# The nibble is the destination network role, not an arbitrary update type.
# Retail role-name dispatcher 0x4f4b450 and consumer 0x4bb484f confirm these.
ENTITY_ROLE_INVALID = 0
ENTITY_ROLE_CLIENT_SIMULATION = 1
ENTITY_ROLE_CLIENT_AUTONOMOUS = 2
ENTITY_ROLE_SERVER_PROXY = 3
ENTITY_ROLE_SERVER_AUTHORITY = 4
ENTITY_FLAG_BIT4 = 0x10     # -> +0x0f; gates the field at +0x10 (the slice ref)
ENTITY_FLAG_BIT5 = 0x20     # -> +0x0e
ENTITY_FLAG_BIT6 = 0x40     # -> +0x0d
ENTITY_FLAG_BIT7 = 0x80     # -> +0x0c; checked FIRST and routed to 0x4bb41f0
                            #    (the delete path) before the update path runs


def pack_type_and_flags(update_type, bit4=False, bit5=False, bit6=False,
                        bit7=False):
    """Build TypeAndFlags; the historical update_type argument is a role.

    1=ClientSimulation, 2=ClientAutonomous, 3=ServerProxy, 4=ServerAuthority.
    The consumer changes the entity's local network role to this value.
    Do not sweep values as spawn candidates (0x4bb484f..0x4bb4921).
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


def build_slice_entry_id(asset_hash, entry_index):
    """Asset hash followed by the slice's compact entry index.

    Retail RVA 0x4bfb1d0 continues across multiple .pdata records. Its tail
    (0x4bfb24f..0x4bfb61f) serializes the entry at object offset +0x90;
    stopping at the first unwind fragment incorrectly omitted this field.
    The index uses leading length bits, not LEB128. -1 is the F0 sentinel.
    """
    if not 0 <= asset_hash <= 0xFFFFFFFF:
        raise ValueError("asset hash must fit a uint32")
    if entry_index == -1:
        encoded = b"\xf0"
    elif 0 <= entry_index <= 0x7F:
        encoded = bytes([entry_index])
    elif entry_index <= 0x3FFF and entry_index >= 0:
        encoded = (0x8000 | entry_index).to_bytes(2, "big")
    elif entry_index <= 0x1FFFFF and entry_index >= 0:
        encoded = (0xC00000 | entry_index).to_bytes(3, "big")
    elif entry_index <= 0x0FFFFFFF and entry_index >= 0:
        encoded = (0xE0000000 | entry_index).to_bytes(4, "big")
    else:
        raise ValueError("slice entry index must be -1 or a 28-bit unsigned integer")
    return asset_hash.to_bytes(4, "big") + encoded


@dataclass(frozen=True)
class EntityMessage:
    """Header and data occupy separate sections in EntitiesUpdatedMessage."""
    header: bytes
    payload: bytes


def build_entity_message(entity_id, type_and_flags, slice_hash=None,
                         payload=b"", *, slice_entry_index=None, previous_id=0, local=False):
    """One element of the EntityMessages vector.

        RelativeId (zigzag varint)
        u8 TypeAndFlags
        SliceEntryId              -- ONLY when bit7 is clear and bit4 is set
        u16 PayloadSize

    Returns separate header and payload for the packet-level PackingBuffer.

    The conditional is the client's own, at rva 0x4b9dc5b: it tests the
    delete flag (bit 7) first and skips everything, then tests bit 4 and only
    then calls the SliceEntryId serialiser (0x4b9e620) into +0x10.
    """
    flags = type_and_flags[0]
    out = encode_entity_id(entity_id, previous_id, local=local) + type_and_flags
    wants_slice = (not flags & ENTITY_FLAG_BIT7) and (flags & ENTITY_FLAG_BIT4)
    if wants_slice:
        if slice_hash is None:
            raise ValueError("bit 4 is set, so a slice hash is required")
        if slice_entry_index is None:
            raise ValueError("a slice reference requires an explicit entry index")
        out += build_slice_entry_id(slice_hash, slice_entry_index)
    elif slice_hash is not None or slice_entry_index is not None:
        raise ValueError("a slice hash needs bit 4 set, or the client will "
                         "not read it")
    if len(payload) > 0xFFFF:
        raise ValueError("payload exceeds the u16 PayloadSize field")
    return EntityMessage(out + len(payload).to_bytes(2, "big"), bytes(payload))


# EntityMessages.Size is declared 0..2048 (descriptor at rva 0x7481d60), so it
# is two bytes big endian and a packet carries at most 2048 messages.
ENTITY_MESSAGES_MAX = 2048


def build_entity_updates(prefix, local_seq, remote_seq, messages,
                         game_time_ms=0, seq_window=0):
    """A complete EntityUpdates packet (id 10, serialiser rva 0xe262e0).

        base Packet header
        ServerGameTimePoint { u32 BE GameTimeMs }   (rva 0x77d1d0)
        EntityMessages: u16 BE Size | header[Size]
        PackingBuffer: u16 BE total payload size | concatenated payloads
    """
    if len(messages) > ENTITY_MESSAGES_MAX:
        raise ValueError("at most %d entity messages per packet"
                         % ENTITY_MESSAGES_MAX)
    # Retail 4b9df30 reads ALL headers, then 4aa8380 reads a bounded
    # PackingBuffer through NetworkInputSerializer::Serialize (4f56910).
    # Its range 0..0x1800 gives a two-byte BE length (4f57140).
    packed = b"".join(message.payload for message in messages)
    if len(packed) > 0x1800:
        raise ValueError("entity payload buffer exceeds retail capacity 6144")
    payload = (ranged_uint32(game_time_ms)
               + len(messages).to_bytes(2, "big")
               + b"".join(message.header for message in messages)
               + len(packed).to_bytes(2, "big") + packed)
    return build_shard(prefix, 10, local_seq, remote_seq, payload,
                       seq_window=seq_window)


def split_updates_enabled():
    """docs/36: one message per entity per packet (CRUCIBLE_SHARD_SPLIT_UPDATES=1)."""
    return os.environ.get('CRUCIBLE_SHARD_SPLIT_UPDATES') == '1'


def split_entity_messages(messages):
    """Lists of EntityMessages with each entity at most once per list.

    EntityReplicationManager::ValidateUpdate (0x4bb5144) drops a message
    whose sequence is not newer than the last one applied to that entity,
    and every message in a packet carries the packet's LocalSequence. So a
    second message for one entity in one packet is refused as "old", which
    the client logged 25,682 times in the 5 October run (each Marine's
    animation, fire and health after its Location). Each list goes in its
    own packet, an entity's messages keep their order across the packets,
    and RelativeIds restart at zero per packet (0x4b9dfd0).
    """
    previous, decoded = 0, []
    for message in messages:
        raw, offset = decode_varint(message.header, 0)
        relative = (raw >> 1) ^ -(raw & 1)
        counter, local = (relative >> 1) + previous, bool(relative & 1)
        decoded.append((counter, local, message.header[offset:], message.payload))
        previous = counter
    groups = []
    for item in decoded:
        for group, seen in groups:
            if item[:2] not in seen:
                break
        else:
            group, seen = [], set()
            groups.append((group, seen))
        group.append(item)
        seen.add(item[:2])
    result = []
    for group, _ in groups:
        previous, rebuilt = 0, []
        for counter, local, rest, payload in group:
            rebuilt.append(EntityMessage(encode_entity_id(counter, previous, local=local)
                                         + rest, payload))
            previous = counter
        result.append(rebuilt)
    return result


def build_replication_record(bits):
    """LEB128 bit count then LSB-first bytes (retail 0x4a930f0).

    With net_CompressHeaders=1 these are traversal decisions, not the full
    component schema: children of absent components/arrays are omitted.
    """
    if len(bits) > 1024:
        raise ValueError("replication record exceeds retail bitset capacity")
    data = bytearray((len(bits) + 7) // 8)
    for i, enabled in enumerate(bits):
        if enabled:
            data[i // 8] |= 1 << (i % 8)
    return encode_varint(len(bits)) + data


def build_practice_gamemode_probe():
    """Bounded source-derived probe, NOT a complete arena bootstrap.

    Practice level's baked GameMode entity is entry 127 (1198 entries, no
    nested slice references). GameMode is the sole replicated component.
    Its compressed traversal visits the component, ConfigurationIndex,
    AlliancesEnabled, StaticAllianceMode, TeamInfo, UsesSequentialGM.
    Only ConfigurationIndex changes; retail config-list entry 6 is PRACTICE.
    Signed ranged int16 encodes value-minus-minimum, hence 0x8006.
    See docs/16-entity-bootstrap.md for source addresses and limitations.
    """
    properties = build_replication_record([True, True, False, False, False, False])
    properties += (solo_modes.selected().configuration + 32768).to_bytes(2, "big")
    return build_entity_message(
        # Live retail slice remapping produced FE000081 for this baked entity.
        # Using ordinary network counter 1 queued activation under 01000001,
        # but the replicator was keyed by FE000081 (4bb2030..4bb20ca).
        solo_modes.selected().mode_entry + solo_modes.selected().baked_counter_bias, pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION, bit4=True),
        slice_hash=solo_modes.selected().level_hash, slice_entry_index=solo_modes.selected().mode_entry, payload=properties,
        local=True)


def build_composite_client_probe(user_id, *, previous_id=0):
    """Opt-in candidate; requires a live hub identity before transmission.

    Retail slices/characters/compositecliententity entry 0 contains the actual
    connection/loadout/player components. Live descriptors captured 17:52 GMT
    establish these nonempty directions, in ascending component type order:
      shared: 44, 85, 88, 145, 157, 158, 165, 169
      autonomous: 32, 85, 88, 111, 145, 158
    Keep authored defaults except PlayerComponent.UserId. Player's shared
    traversal is root, UserId, Name (ac4920); UserId is full-range BE uint32
    (7cb2c0). Counts here are compressed traversal counts, not expanded sizes.
    This is only a local-player activation probe, not hero/gameplay bootstrap.
    """
    if not 1 <= user_id <= 0xFFFFFFFF:
        raise ValueError("probe requires the connected session's nonzero uint32 user ID")
    shared = [False] * 7 + [True, True, False]
    autonomous = [False] * 6
    properties = (build_replication_record(shared)
                  + build_replication_record(autonomous)
                  + ranged_uint32(user_id))
    return build_entity_message(
        1, pack_type_and_flags(ENTITY_ROLE_CLIENT_AUTONOMOUS, bit4=True),
        slice_hash=646954285, slice_entry_index=0, payload=properties,
        previous_id=previous_id)


def bootstrap_reply(data, state):
    """Opt-in, once per accepted peer after ten acknowledged heartbeats."""
    mode = os.environ.get("CRUCIBLE_SHARD_BOOTSTRAP")
    if mode not in ("practice-gamemode", "practice-client", "practice-solo", "practice-roster", "practice-character"):
        return None
    if "accept" not in state or state.get("bootstrap_sent"):
        return None
    parsed = parse_shard(data)
    if parsed is None:
        return None
    region = parsed[1]
    if int.from_bytes(region[:2], "big") != 2:
        return None
    accept_region = lz4.block.decompress(state["accept"][1:], uncompressed_size=4096)
    accepted_seq = int.from_bytes(accept_region[2:4], "big")
    ack = int.from_bytes(region[4:6], "big")
    delta = (ack - accepted_seq) & 0xFFFF
    if not state.get("accept_acknowledged"):
        window = int.from_bytes(region[6:10], "big")
        if delta >= 32 or not window & (1 << delta):
            return None
        state["accept_acknowledged"] = True
    client_seq = int.from_bytes(region[2:4], "big")
    previous = state.get("bootstrap_last_heartbeat")
    if previous is not None and not 0 < ((client_seq - previous) & 0xFFFF) < 0x8000:
        return None
    state["bootstrap_last_heartbeat"] = client_seq
    state["bootstrap_heartbeats"] = state.get("bootstrap_heartbeats", 0) + 1
    if state["bootstrap_heartbeats"] < 10:
        return None
    messages = [build_practice_gamemode_probe()]
    if mode in ('practice-client', 'practice-solo', 'practice-roster', 'practice-character'):
        directory = os.environ.get('CRUCIBLE_LOCAL_SESSIONS')
        user_id = local_sessions.claim(directory, state.get('peer_ip')) if directory else None
        if user_id is None:
            if not state.get('identity_wait_logged'):
                log('      BOOTSTRAP waiting for one fresh local hub identity')
                state['identity_wait_logged'] = True
            return None
        messages.append(build_composite_client_probe(user_id,
            previous_id=solo_modes.selected().mode_entry + solo_modes.selected().baked_counter_bias))
    state["seq"] = (state["seq"] + 1) & 0xFFFF
    state["bootstrap_sent"] = True
    return build_entity_updates(
        shard_bpt(), state["seq"], state["received_seq"],
        messages, game_time_ms=2000,
        seq_window=state["received_window"])


def build_practice_match_store_probe():
    """Activate the baked store with defaults to inspect its replication graph.

    Two shared roots: DataStoreMatch type 37 and EntityFlags type 44.
    Script has only autonomous data, which role 1 does not deserialize.
    This does NOT populate PlayerList or claim roster completion.
    """
    return build_entity_message(
        solo_modes.selected().store_entry + solo_modes.selected().baked_counter_bias, pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION, bit4=True),
        slice_hash=solo_modes.selected().level_hash, slice_entry_index=solo_modes.selected().store_entry,
        payload=build_replication_record([False, False]), local=True)


def build_single_player_roster_probe(player_name, *, spawned_character=False):
    """One actual connection, optionally linked to the verified native spawn.

    Live store graph captured 18:49: root 0, PlayerList 538, Size 539,
    entry 0 at 540, NumPlayersInMatch 579. Other field roots are absent.
    PlayerData 4ab8110 serializes each selected entry atomically.
    """
    if not isinstance(player_name, bytes) or len(player_name) > 127 or b'\0' in player_name:
        raise ValueError('player name must be at most 127 non-NUL bytes')
    entries = []
    names = [player_name]
    bots = solo_bot_slots() if spawned_character else ()
    names += [('Hunter %d' % (i + 1)).encode() for i in range(len(bots))]
    count = len(names)
    for slot, name in enumerate(names, 1):
        identity = b'local-hunter' if slot == 1 else ('solo-hunter-%d' % slot).encode()
        team = 1 if slot == 1 else bots[slot - 2].team
        entries.append(bytes([slot, len(identity)]) + identity
            + bytes([len(name)]) + name + b'\x00'
            # CharacterClass (.rdata name table 0x6c1de08; 8 is "Marine").
            # Only the local player follows CRUCIBLE_SOLO_HERO; slots 2..4 are
            # the bots, each its slot's hero. b'\x00\x00' is the Unknown
            # class before spawn.
            + ((hero_loadout.character_class_bytes() if slot == 1
                else hero_loadout.character_class_bytes(hero_loadout.get(bots[slot - 2].hero)))
               if spawned_character else b'\x00\x00')
            + bytes([team, team])
            # Reader 4f565d0 shares one cached byte per eight bools across
            # PlayerData entries. Only the local player is DropMaster.
            + (bytes([int(slot == 1)]) if (slot - 1) % 8 == 0 else b'')
            + b'\x7f\xff' + (b'\x03' if spawned_character else b'\x00'))
    bits = ([True] + [False] * 6 + [True, True] + [True] * count
            + [False] * 3 + [True, False, False] + [False])
    if spawned_character:
        # Fixed 16-slot CharacterIds: no wire size; one value per selected slot.
        bits = bits[:-2] + [True] + [True] * count + [False] * (16-count) + [False]
    properties = (build_replication_record(bits) + bytes([count])
                  + b''.join(entries) + ranged_uint32(count))
    if spawned_character:
        properties += b''.join(ranged_uint32(entity) for entity in
                               (PLAYER_CHARACTER_ID,) + tuple(b.entity for b in bots))
        return build_entity_message(solo_modes.selected().store_entry + solo_modes.selected().baked_counter_bias, pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION),
                                    payload=properties, local=True)
    return build_entity_message(
        solo_modes.selected().store_entry + solo_modes.selected().baked_counter_bias, pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION, bit4=True),
        slice_hash=solo_modes.selected().level_hash, slice_entry_index=solo_modes.selected().store_entry, payload=properties,
        local=True)


def build_local_match_slot_probe(*, previous_id=0):
    """Assign the one local connection slot 1, retaining other properties.

    MatchDataReceiver shared serializer 96a644 names MyId, using 89f740's
    signed int8 range (-128..127). Reader 4f56640 copies its raw byte,
    unlike the wider signed ranged readers. Expanded size 2 is root and MyId.
    UI safely returns Unknown until this slot also exists in PlayerList.
    """
    shared = [False, False, True, True, False, False, False, False, False]
    properties = (build_replication_record(shared)
                  + build_replication_record([False] * 6) + b'\x01')
    return build_entity_message(1, pack_type_and_flags(ENTITY_ROLE_CLIENT_AUTONOMOUS),
                                payload=properties, previous_id=previous_id)


def build_player_rank_update(rank, *, previous_id=0):
    """PlayerStats.Rank on the local connection, for the post-game banner.

    The only replicated Rank is PlayerStatsComponent (111), an autonomous,
    full-range BE u32 (9cf7b0 -> 7cb2c0). The connection's shared roots are
    44, 85, 88, 145, 157, 158, 165 and 169; its autonomous roots are 32, 85,
    88, 111, 145 and 158. After GameOver the HUD waits up to 5 s for
    rank > 0, then shows victory for rank 1 and defeat otherwise
    (snapshots/retail-20260923 match-flow 01/03). That `match.stats.updates`
    reads this property is inferred, not traced.
    """
    properties = (build_replication_record([False] * 8)
                  + build_replication_record([False, False, False, True, True, False, False])
                  + ranged_uint32(rank))
    return build_entity_message(1, pack_type_and_flags(ENTITY_ROLE_CLIENT_AUTONOMOUS),
                                payload=properties, previous_id=previous_id)


def match_store_reply(state):
    if (os.environ.get('CRUCIBLE_SHARD_BOOTSTRAP') not in ('practice-roster', 'practice-character')
            or not state.get('bootstrap_sent')
            or not state.get('local_player_activation_received')
            or state.get('match_store_sent')):
        return None
    state['seq'] = (state['seq'] + 1) & 0xffff
    state['match_store_sent'] = True
    return build_entity_updates(
        shard_bpt(), state['seq'], state['received_seq'],
        [build_single_player_roster_probe(state.get('local_player_name', b'')),
         build_local_match_slot_probe(previous_id=solo_modes.selected().store_entry + solo_modes.selected().baked_counter_bias)],
        game_time_ms=2000, seq_window=state['received_window'])


def solo_phase_reply(state):
    """Practice's authored WaitForPlayers -> GamePlay path for one local peer.

    This opt-in phase probe follows the client's own activation RPC; no other
    peer or population count is involved. It does not prove character loading,
    possession or simulation. CurrentPhase alone cannot dismiss the UI when
    DataStoreMatch/local character data are missing (retail c8d700/c8de40).
    """
    if (os.environ.get('CRUCIBLE_SHARD_BOOTSTRAP') not in ('practice-solo', 'practice-roster', 'practice-character')
            or not state.get('bootstrap_sent')
            or not state.get('local_player_activation_received')
            or state.get('solo_phase_sent')):
        return None
    # Baked Practice entry 123, local counter 125 (entry + 2). Its sole
    # replicated component is MatchPhase: root, TimeRemaining, CurrentPhase,
    # StartTimeSeconds (96dccc). Enum serializer 42c3990 is full BE u16.
    # Installed practice.gamemodeconfig has GamePlay after WaitForPlayers,
    # AutoSkipPhaseToGameplay, DoNotSpawnDropPod, and unlimited phase time.
    properties = build_replication_record([True, False, True, False]) + b'\0\x08'
    message = build_entity_message(
        solo_modes.selected().phase_entry + solo_modes.selected().baked_counter_bias, pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION, bit4=True),
        slice_hash=solo_modes.selected().level_hash, slice_entry_index=solo_modes.selected().phase_entry, payload=properties,
        local=True)
    state['seq'] = (state['seq'] + 1) & 0xffff
    state['solo_phase_sent'] = True
    return build_entity_updates(shard_bpt(), state['seq'], state['received_seq'],
                                [message], game_time_ms=2000,
                                seq_window=state['received_window'])


def build_solo_match_phase_update(profile, phase, *, previous_id=0):
    """Update the accepted map's MatchPhase component (docs/23)."""
    properties = build_replication_record([True, False, True, False]) + phase.to_bytes(2, 'big')
    return build_entity_message(
        profile.phase_entry + profile.baked_counter_bias,
        pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION, bit4=True),
        slice_hash=profile.level_hash, slice_entry_index=profile.phase_entry,
        payload=properties, previous_id=previous_id, local=True)


def pack_practice_spawn_transform(position=None):
    """Retail packed Location transform at Practice's authored PlayerStartSpawn.

    4f47e10 packs X/Y into 22 bits over +/-4500 and Z into 20 over
    +/-1300; constants were verified in the running client. 4f45700 packs
    the identity rotation using largest-index 0 (WXYZ) and three round(511.5)
    components. Network readers consume a BE u64 then a BE u32.
    """
    position = solo_modes.selected().player_position if position is None else position
    x, y = (int((v + 4500) * 2097151.5 / 4500 + 0.5) for v in position[:2])
    z = int((position[2] + 1300) * 524287.5 / 1300 + 0.5)
    packed = (x << 42) | (y << 20) | z
    rotation = (512 << 20) | (512 << 10) | 512
    return packed.to_bytes(8, 'big') + rotation.to_bytes(4, 'big')


def build_practice_character_probe(*, counter=2, position=None, autonomous=True,
                                   previous_id=0, hero=None):
    """Spawn authored Marine entry 0 as autonomous counter 2, still unverified.

    Nonempty directions come from the installed marine_character slice and
    captured descriptor table. Location.InternalTransform is atomic. The
    EntityHierarchy parent is the existing connection's full network ID.
    Hierarchy's CharacterEntity kind is authored in this slice; its normal
    autonomous OnActivate registers the local character (c3c940).
    This probe does not invent health, Alive status or simulation acceptance.
    """
    # Every one of the ten shipped hunters has this exact replication layout
    # (docs/24); only the slice hash differs. So the hero is a two-value
    # substitution, not a different packet.
    hero = hero_loadout.selected() if hero is None else hero
    shared_types = hero_loadout.SHARED_TYPES
    bits = []
    for component in shared_types:
        if component == 145:
            bits.extend((True, False, False, True, False, False))
        elif component == 158 and autonomous:
            bits.extend((True, True, False))
        else:
            bits.append(False)
    properties = (build_replication_record(bits)
                  + (build_replication_record([False] * hero_loadout.AUTONOMOUS_ROOTS)
                     if autonomous else b'')
                  + pack_practice_spawn_transform(position)
                  + (ranged_uint32(0x01000001) if autonomous else b''))
    return build_entity_message(
        counter, pack_type_and_flags(ENTITY_ROLE_CLIENT_AUTONOMOUS if autonomous else ENTITY_ROLE_CLIENT_SIMULATION, bit4=True),
        slice_hash=hero.slice_hash, slice_entry_index=hero.slice_entry,
        payload=properties, previous_id=previous_id)


def character_spawn_reply(state):
    """Bounded opt-in character probe after this one player's startup."""
    if (os.environ.get('CRUCIBLE_SHARD_BOOTSTRAP') != 'practice-character'
            or not all(state.get(flag) for flag in (
                'bootstrap_sent', 'local_player_activation_received',
                'match_store_sent', 'solo_phase_sent'))
            or state.get('character_spawn_sent')):
        return None
    state['seq'] = (state['seq'] + 1) & 0xffff
    state['character_spawn_sent'] = True
    state['character_spawn_seq'] = state['seq']
    return build_entity_updates(shard_bpt(), state['seq'], state['received_seq'],
                                [build_practice_character_probe()], game_time_ms=2000,
                                seq_window=state['received_window'])


def build_character_connection_links(*, previous_id=0):
    """Link the connection's loadout and prediction children to its Marine.

    Values serialize by component ID across directions: LoadoutSetup's
    autonomous Character (85) precedes Hierarchy's shared child list (158).
    Hierarchy's autonomous following/controlling targets both reference that
    same native character. Its captured autonomous graph is root plus these
    two atomic properties. The already verified MyId stays intact.
    """
    shared = [False] * 5 + [True, False, True, True, True] + [False] * 2
    autonomous = [False, True, True] + [False] * 10 + [True, True, True]
    properties = (build_replication_record(shared)
                  + build_replication_record(autonomous)
                  # LoadoutSetup.Character, then the ChildEntityIds size.
                  + hero_loadout.loadout_character_bytes() + b'\x01'
                  + ranged_uint32(0x01000002)
                  # Hierarchy autonomous FollowingEntityId / ControllingEntityId.
                  # Both use full BE u32 (a97090/a970ba -> 7cb2c0).
                  + ranged_uint32(0x01000002) + ranged_uint32(0x01000002))
    return build_entity_message(1, pack_type_and_flags(ENTITY_ROLE_CLIENT_AUTONOMOUS),
                                payload=properties, previous_id=previous_id)


def character_links_reply(data, state):
    """Publish spawned roster after delivery of the native character packet.

    Packet ACK is delivery evidence only, not a gameplay/activation receipt.
    Native activation, local character registration, authored spawn transform
    and 800/800 health were separately verified in the 19:18 live capture.
    """
    if (os.environ.get('CRUCIBLE_SHARD_BOOTSTRAP') != 'practice-character'
            or not state.get('character_spawn_sent')
            or 'character_spawn_seq' not in state
            or state.get('character_links_sent')):
        return None
    parsed = parse_shard(data)
    if parsed is None:
        return None
    region = parsed[1]
    ack = int.from_bytes(region[4:6], 'big')
    window = int.from_bytes(region[6:10], 'big')
    behind = (ack - state['character_spawn_seq']) & 0xffff
    if behind >= 32 or not window & (1 << behind):
        return None
    state['seq'] = (state['seq'] + 1) & 0xffff
    state['character_links_sent'] = True
    state['clock_epoch'] = time.monotonic()
    state['clock_last_sent'] = state['clock_epoch']
    return build_entity_updates(
        shard_bpt(), state['seq'], state['received_seq'],
        [build_single_player_roster_probe(state.get('local_player_name', b''),
                                         spawned_character=True),
         build_character_connection_links(previous_id=solo_modes.selected().store_entry + solo_modes.selected().baked_counter_bias)],
        game_time_ms=2000, seq_window=state['received_window'])


def build_solo_hunter_probes():
    messages = []
    previous = 0
    bots = solo_bot_slots()
    positions = tuple(slot.position for slot in bots)
    if positions and os.environ.get('CRUCIBLE_SOLO_PATROL') == '1':
        from hunter_patrol import Unroutable, routes
        try:
            positions = tuple(route[0] for route in routes(solo_modes.selected().level))
        except Unroutable:
            log('      HUNTER PATROL: no routes on %s; the hunters stand'
                % solo_modes.selected().level)
    # Each bot is its slot's hero whatever CRUCIBLE_SOLO_HERO says: the
    # default slots are the screenshot-accepted Marine baseline (docs/18).
    for slot, position in zip(bots, positions):
        counter = slot.entity & 0xffffff
        messages.append(build_practice_character_probe(counter=counter, position=position,
            autonomous=False, previous_id=previous, hero=hero_loadout.get(slot.hero)))
        previous = counter
    return messages


def location_bits(shared_types):
    """Traversal selecting only Location.InternalTransform on an entity.

    Location is network component type 145; its third property is the atomic
    packed InternalTransform (six decisions: root, two skipped, transform,
    two skipped). Every other shared component root is one False.
    """
    bits = []
    for component in shared_types:
        if component == 145:
            bits.extend((True, False, False, True, False, False))
        else:
            bits.append(False)
    return bits


def pack_upright_rotation(direction):
    """Packed WXYZ smallest-three quaternion for an upright facing.

    Cry's forward axis is +Y. The 723.37 scale and 512 bias are the retail
    packer's (docs/17, WXYZ order correction).
    """
    yaw = math.atan2(-direction[0], direction[1])
    quat = (math.cos(yaw/2), 0., 0., math.sin(yaw/2))
    largest = max(range(4), key=lambda i: abs(quat[i]))
    sign = 1 if quat[largest] >= 0 else -1
    rotation = largest << 30
    for shift, i in zip((20, 10, 0), (i for i in range(4) if i != largest)):
        rotation |= max(0, min(1023, int(quat[i]*sign*723.3699951171875+512))) << shift
    return rotation.to_bytes(4, 'big')


def build_location_update(entity_id, shared_types, transform, *, local=False,
                          previous_id=0):
    """Simulation-role Location delta carrying a 12-byte packed transform."""
    return build_entity_message(
        entity_id & 0xffffff, pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION),
        payload=build_replication_record(location_bits(shared_types)) + transform,
        previous_id=previous_id, local=local)


def build_bot_location_message(counter, position, direction, *, previous_id=0):
    """One simulation hunter's native Location delta, facing `direction`."""
    # Same 33 shared component roots as the Marine spawn; only Location is
    # selected.
    transform = pack_practice_spawn_transform(position)[:8] + pack_upright_rotation(direction)
    return build_location_update(counter, MARINE_SHARED_TYPES, transform,
                                 previous_id=previous_id)


def build_hunter_patrol_updates(seconds, *, previous_id=0):
    """Native Location deltas for the three simulation hunters only."""
    from hunter_patrol import Unroutable, routes, sample
    try:
        legs = routes(solo_modes.selected().level)
    except Unroutable:
        return []           # build_solo_hunter_probes logged it once
    messages = []
    for counter, route in enumerate(legs, 3):
        position, direction = sample(route, seconds)
        messages.append(build_bot_location_message(counter, position, direction,
                                                   previous_id=previous_id))
        previous_id = counter
    return messages


def build_hunter_ai_updates(squad, now, player_position, *, previous_id=0,
                            active_entities=None):
    """Location deltas driven by the hunter_ai state machine.

    Unlike the stateless patrol this pursues: `player_position` is the
    position decoded from the client's own input stream (client_input), so
    the bots react to where the player actually is. Bots are entities
    01000003..05, i.e. RelativeId counters 3..5, in the order squad holds.
    """
    import hunter_patrol
    from hunter_ai import MARINE
    messages = []
    kwargs = {} if active_entities is None else {'active_entities': active_entities}
    steps = squad.update(now, PLAYER_CHARACTER_ID if player_position else None,
                         player_position, **kwargs)
    if not steps:
        return messages, []

    def report_once(key, error):
        # Bound reports to one per correction stage/entity for this squad;
        # a persistent mesh failure must remain visible without tick spam.
        reported = getattr(squad, '_reported_position_errors', None)
        if reported is None:
            reported = squad._reported_position_errors = set()
        if key not in reported:
            reported.add(key)
            log('      HUNTER AI position correction %s failed (%s): %s' %
                (key, type(error).__name__, str(error)[:160]))
    # Two corrections between the state machine and the wire, both from the
    # 2026-09-21 live runs (docs/21): bots that share a target converge into
    # one body, and straight-line motion between waypoints cuts under convex
    # slopes so they sink into the terrain.
    positions = [step.position for step in steps]
    try:
        mesh = next((bot.mesh for bot in squad.bots if bot.mesh is not None), None)
        positions = hunter_patrol.separate(positions, mesh=mesh)
    except Exception as error:              # keep the other corrections usable
        report_once('separation', error)
        positions = [step.position for step in steps]
    corrected = []
    bots = {bot.entity: bot for bot in squad.bots}
    for step, position in zip(steps, positions):
        bot = bots.get(step.entity)
        mesh = getattr(bot, 'mesh', None)
        if mesh is not None:
            try:
                # A separation margin cannot override walkability. Keep the
                # original X/Y when there is no surface under the candidate,
                # and still correct the original's interpolated height.
                if mesh.locate(position, vertical_tolerance=3.0) is None:
                    position = step.position
                position = hunter_patrol.ground(position, mesh=mesh)
            except Exception as error:
                report_once('%08x grounding' % step.entity, error)
                position = step.position
        shots = []
        for shot in step.shots:
            # Keep the authored AI origin and target offsets after the server's
            # separation/grounding correction. The Marine guide AI aims from
            # one metre above its root toward one metre above the target.
            origin = tuple(position[i] + MARINE.aggro_origin_offset_local[i]
                           for i in range(3))
            target = (player_position[0], player_position[1],
                      player_position[2] + MARINE.aggro_target_offset_vertical)
            aim = tuple(target[i] - origin[i] for i in range(3))
            distance = math.dist(origin, target)
            shots.append(replace(shot, origin=origin, range=distance,
                                 direction=tuple(v / (distance or 1.) for v in aim)))
        step = replace(step, position=position, shots=tuple(shots))
        corrected.append(step)
        if bot is not None:
            # The next AI tick and any shot must start at the position that
            # was sent, rather than the uncorrected path interpolation.
            bot.position = position
        counter = step.entity & 0xffffff
        messages.append(build_bot_location_message(
            counter, position, step.facing, previous_id=previous_id))
        previous_id = counter
    return messages, corrected


def build_practice_npc_probes():
    """Authored population activation, not AI or combat simulation proof."""
    from practice_population import (SPITTER_SLICE_HASH, SPITTER_SHARED_TYPES,
                                     DUMMY_SHARED_TYPES, SPITTER_POSITIONS,
                                     DUMMY_POSITIONS)
    messages = []
    previous = 0
    for index, position in enumerate(SPITTER_POSITIONS + DUMMY_POSITIONS):
        dummy = index >= len(SPITTER_POSITIONS)
        types = DUMMY_SHARED_TYPES if dummy else SPITTER_SHARED_TYPES
        bits = []
        for component in types:
            bits.extend((True, False, False, True, False, False)
                        if component == 145 else (False,))
        x, y = (int((v + 4500) * 2097151.5 / 4500 + 0.5) for v in position[:2])
        z = int((position[2] + 1300) * 524287.5 / 1300 + 0.5)
        packed = (x << 42) | (y << 20) | z
        # CryQuat order is WXYZ: identity W=1, authored target rotation Z=1.
        rotation = ((3 if dummy else 0) << 30) | (512 << 20) | (512 << 10) | 512
        properties = (build_replication_record(bits) + packed.to_bytes(8, 'big')
                      + rotation.to_bytes(4, 'big'))
        entry = 73 + index - len(SPITTER_POSITIONS) if dummy else 0
        counter = entry + 2 if dummy else index + 3
        messages.append(build_entity_message(
            counter, pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION, bit4=True),
            slice_hash=1289380704 if dummy else SPITTER_SLICE_HASH,
            slice_entry_index=entry, payload=properties,
            previous_id=previous, local=dummy))
        previous = counter
    return messages


def practice_npcs_reply(state):
    if (os.environ.get('CRUCIBLE_PRACTICE_NPCS') != '1'
            or os.environ.get('CRUCIBLE_SHARD_BOOTSTRAP') != 'practice-character'
            or not state.get('character_links_sent') or state.get('practice_npcs_sent')):
        return None
    state['practice_npcs_sent'] = True   # once per connection, whatever we send
    profile = solo_modes.selected()
    # The population is per MAP. build_practice_npc_probes() hardcodes the
    # Training Arena's spitters, RoboDummies and practicearena's level hash,
    # so it must never run on another level - a map with no hunters that is
    # not Practice (tutorial_02/03, a HotH_02 profile without bots) would
    # otherwise have arena NPCs injected into it under the wrong slice hash.
    if profile.bot_positions:
        messages = build_solo_hunter_probes()
    elif profile.level == 'practicearena':
        messages = build_practice_npc_probes()
    else:
        messages = []
    if messages and guide_hive_enabled(profile):
        position = guarded(state, 'guide hive', guide_hive_position, profile)
        if position is None:
            log('      GUIDE HIVE: no spot on the mesh near the drop; no hive')
        else:
            state['guide_hive'] = {'position': position, 'next_heal': None, 'regens': 0}
            messages.append(build_guide_hive_probe(
                position, previous_id=solo_bot_slots(profile)[-1].entity & 0xffffff))
            log('      GUIDE HIVE spawned %08x at (%.1f, %.1f, %.1f)'
                % ((HIVE_ENTITY_ID,) + tuple(position)))
    if not messages:
        # Nothing authored for this map yet; say nothing rather than send an
        # empty update the client would have to parse.
        log('      SOLO POPULATION: none audited for %s, sending nothing'
            % profile.level)
        start_solo_simulation(state)
        return None
    state['seq'] = (state['seq'] + 1) & 0xffff
    start_solo_simulation(state)
    # Called immediately after the initial character links, before clock ticks.
    return build_entity_updates(shard_bpt(), state['seq'], state['received_seq'],
                                messages, game_time_ms=2000,
                                seq_window=state['received_window'])


# ---------------------------------------------------------------------------
# Solo session simulation: authoritative combat and AI (docs/20..22, docs/25)
# ---------------------------------------------------------------------------
# Both are opt-in and off by default so the visually accepted stationary
# baselines (docs/17, docs/18) are untouched unless asked for.
#   CRUCIBLE_SOLO_COMBAT=1  combat.Combat replaces the nonlethal probe:
#                           asset damage/falloff, death at 0, authored respawn.
#   CRUCIBLE_SOLO_AI=1      hunter_ai drives the hunters (pursuit toward the
#                           player's decoded input position) instead of the
#                           stateless patrol; creature_ai turns creatures to
#                           face the player on their authored aggro radii.
def solo_combat_enabled():
    return os.environ.get('CRUCIBLE_SOLO_COMBAT') == '1'


def server_bot_damage_enabled():
    """docs/35: hunter rounds are resolved here, never from client reports."""
    return os.environ.get('CRUCIBLE_SOLO_BOT_DAMAGE') == 'server'


def hunter_gunnery(state):
    gunnery = state.get('hunter_gunnery')
    if gunnery is None:
        from hunter_hits import ACCURACY_SCALE, GUNS, HunterGunnery
        scale = float(os.environ.get('CRUCIBLE_SOLO_BOT_ACCURACY') or ACCURACY_SCALE)
        seed = int(time.time())
        gunnery = state['hunter_gunnery'] = HunterGunnery(
            seed=seed, accuracy_scale=scale,
            guns={slot.entity: GUNS[slot.hero] for slot in solo_bot_slots()})
        log('      HUNTER HITS server-resolved, accuracy scale %.2f, seed %d'
            % (scale, seed))
    return gunnery


def land_hunter_hits(state, arena, now):
    """Apply server-resolved hunter rounds whose flight has ended."""
    landings = state.get('hunter_landings')
    if not landings or arena is None:
        return
    state['hunter_landings'] = [l for l in landings if l.time > now]
    import feedback_rings
    for landing in sorted((l for l in landings if l.time <= now), key=lambda l: l.time):
        hit = arena.apply_damage(landing.shooter, landing.target, landing.damage,
                                 now=landing.time)
        if hit is not None:
            log('      SOLO COMBAT hunter %08x hit target=%08x damage=%.0f health=%.0f%s'
                % (landing.shooter, hit.target, hit.damage, hit.health,
                   ' KILLED' if hit.killed else ''))
            squad = state.get('squad')
            shooter = next((bot for bot in getattr(squad, 'bots', ())
                            if bot.entity == landing.shooter), None)
            if (hit.target == PLAYER_CHARACTER_ID and shooter is not None
                    and feedback_rings.hit_direction_enabled()):
                state.setdefault('indicator_ring', feedback_rings.IndicatorRing()).hit(
                    hit.damage, shooter.position, landing.time)
            if (hit.target == PLAYER_CHARACTER_ID and shooter is not None
                    and player_flinch_enabled()):
                note_player_flinch(state, shooter.position)


def solo_ai_enabled():
    return os.environ.get('CRUCIBLE_SOLO_AI') == '1'


def bot_reload_enabled():
    """docs/36: hunters fire from the authored 24-round magazine and reload."""
    return os.environ.get('CRUCIBLE_SOLO_BOT_RELOAD') == '1'


def level_sight():
    """docs/36: the opt-in sight test for CRUCIBLE_SOLO_LOS, or None.

    'terrain' tests the level's compiled heightfield (level_geometry), which
    matches the shipped PhysX heightfield within 0.06 m. 'static' adds the
    level's walls, rocks and buildings from the offline cache that
    tools/build_static_geometry.py writes; without a cache it falls back to
    the terrain alone, with one log line per match. A level whose terrain
    does not decode gets no sight test.
    """
    mode = os.environ.get('CRUCIBLE_SOLO_LOS')
    if mode not in ('terrain', 'static'):
        return None
    import level_geometry
    level = solo_modes.selected().level
    try:
        terrain = level_geometry.installed_terrain(level)
    except Exception as error:      # noqa: BLE001 - sight is optional; never crash-loop
        log('      LINE OF SIGHT unavailable on %s: %r' % (level, error))
        return None
    world = None
    if mode == 'static':
        import static_geometry
        try:
            world = static_geometry.installed_world(level)
        except Exception as error:  # noqa: BLE001 - a bad cache must not end the shard
            log('      LINE OF SIGHT static geometry unreadable on %s: %r' % (level, error))
        if world is None:
            log('      LINE OF SIGHT: no static geometry cache for %s (%s); terrain only'
                % (level, static_geometry.cache_path(level)))
    if world is None:
        return lambda eye, target: level_geometry.sight_clear(terrain, eye, target)
    # On April2018 no collider is on layer 2, the one layer where the
    # AwarenessLoS and Projectiles groups differ, so rounds share this test.
    return lambda eye, target: (level_geometry.sight_clear(terrain, eye, target)
                                and not world.blocked(eye, target))


def authored_death_metadata(bots):
    """docs/36: each killer's own on-hit tag, "Ability" plus its primary."""
    import combat
    pairs = {slot.entity: (combat.ABILITY_SOURCE, combat.HERO_PRIMARY_ABILITY[slot.hero])
             for slot in bots}
    ability = combat.HERO_PRIMARY_ABILITY.get(hero_loadout.selected().name)
    if ability is not None:
        pairs[PLAYER_CHARACTER_ID] = (combat.ABILITY_SOURCE, ability)
    return pairs


def solo_death_metadata():
    """Require explicit baked ids for the unverified death-event probe.

    CRUCIBLE_SOLO_DEATH_EVENTS=authored needs none: start_solo_simulation
    then gives each killer its authored pair instead.
    """
    if os.environ.get('CRUCIBLE_SOLO_DEATH_EVENTS') != '1':
        return None
    try:
        values = tuple(int(os.environ['CRUCIBLE_SOLO_DEATH_' + name + '_ID'], 0)
                       for name in ('SOURCE', 'ABILITY'))
        if any(not 0 <= value <= 65535 for value in values):
            raise ValueError('id outside uint16')
    except (KeyError, ValueError) as error:
        raise ValueError('death events require explicit uint16 source/ability metadata') from error
    return values


def build_solo_squad(profile):
    """The hunter squad on the selected map's own mesh, or None.

    A map whose bot points do not route (the tutorials') keeps its hunters
    standing where they spawned: an expected condition, logged in one line.
    Anything else raises, for the caller's guard.
    """
    import hunter_ai
    import hunter_patrol
    slots = solo_bot_slots(profile)
    try:
        return hunter_patrol.squad(tuple(slot.entity for slot in slots),
                                   level=profile.level,
                                   tunings=tuple(hunter_ai.TUNINGS[slot.hero] for slot in slots))
    except hunter_patrol.Unroutable:
        log('      HUNTER AI: no routes on %s; the hunters stand' % profile.level)
        return None


def start_solo_simulation(state):
    """Build per-connection combat/AI state for the population just sent."""
    import combat
    import practice_population
    profile = solo_modes.selected()
    if solo_combat_enabled():
        bots = solo_bot_slots(profile)
        killers = (authored_death_metadata(bots)
                   if os.environ.get('CRUCIBLE_SOLO_DEATH_EVENTS') == 'authored' else {})
        arena = combat.Combat(death_metadata=solo_death_metadata(),
                              killer_metadata=killers)
        arena.register(PLAYER_CHARACTER_ID, MARINE_SHARED_TYPES,
                       maximum=MARINE_HEALTH_MAX, team=1, autonomous=True)
        for slot in bots:
            arena.register(slot.entity, MARINE_SHARED_TYPES,
                           maximum=slot.health_max, team=slot.team)
        if 'guide_hive' in state:
            arena.register(HIVE_ENTITY_ID, HIVE_SHARED_TYPES,
                           maximum=HIVE_HEALTH_MAX, team=HIVE_TEAM)
        for place in practice_population.population(profile.level):
            respawn = (combat.PRACTICE_SPITTER_RESPAWN_SECONDS if place.profile
                       else combat.PRACTICE_TARGET_RESPAWN_SECONDS)
            telegraph = (combat.PRACTICE_SPITTER_TELEGRAPH_SECONDS if place.profile
                         else combat.PRACTICE_TARGET_TELEGRAPH_SECONDS)
            arena.register(place.entity_id, place.shared_types,
                           maximum=place.health_max, team=2, local=place.local_id,
                           respawn_seconds=respawn, telegraph_seconds=telegraph)
        state['combat'] = arena
    if hit_reaction_enabled():
        # match_acceptance F4 fails a run with this line, accepted player
        # hits on hunters, and no flinch sent: a silent no-op otherwise.
        log('      HIT REACTION on: hunters the player hits flinch (component 80)')
    if solo_ai_enabled():
        squad = None
        if profile.bot_positions:
            squad = guarded(state, 'hunter squad', build_solo_squad, profile)
        if squad is not None:
            state['squad'] = squad
            if bot_reload_enabled():
                for bot in state['squad'].bots:
                    bot.ammo = bot.tuning.magazine or None
            if os.environ.get('CRUCIBLE_SOLO_BOT_STRAFE') == '1':
                for bot in state['squad'].bots:
                    bot.strafe = True
            if os.environ.get('CRUCIBLE_SOLO_BOT_TURN') == '1':
                for bot in state['squad'].bots:
                    bot.move_turn = True
            if os.environ.get('CRUCIBLE_SOLO_BOT_IDLE') == '1':
                for bot in state['squad'].bots:
                    bot.idle = True
            accel = os.environ.get('CRUCIBLE_SOLO_BOT_ACCEL')
            if accel in ('1', 'ai', 'config'):
                # 'ai' (or '1'): StartStopAcceleration; 'config': the movement
                # config's 0.15 s ramp and friction stop (docs/36).
                for bot in state['squad'].bots:
                    bot.accelerate = True
                    bot.accel_model = 'config' if accel == 'config' else 'ai'
            if os.environ.get('CRUCIBLE_SOLO_BOT_CHASE_CAP') == '1':
                for bot in state['squad'].bots:
                    bot.pursue_cap = bot.tuning.chase_cap or None
            if os.environ.get('CRUCIBLE_SOLO_BOT_FLEE') == '1':
                for bot in state['squad'].bots:
                    bot.flee = True
                    # The flight search's component index takes a quarter
                    # second to build: here, not on the first hit.
                    if hasattr(bot.mesh, 'component_index'):
                        bot.mesh.component_index()
            if os.environ.get('CRUCIBLE_SOLO_BOT_ANCHORED') == '1':
                for bot in state['squad'].bots:
                    bot.anchored = True
            sight = level_sight()
            if sight is not None:
                state['sight'] = sight
                for bot in state['squad'].bots:
                    bot.visible = sight
            # One line confirms which drop-ins took effect (match_acceptance B0).
            switches = [name for name, on in (
                ('guide-saboteur', guide_saboteur_enabled(profile)),
                ('guide-hive', 'guide_hive' in state),
                ('guide-heart', 'guide_hive' in state and guide_heart_enabled(profile)
                 and os.environ.get('CRUCIBLE_SOLO_GUIDE_HEART') == '1'),
                ('guide-heart-proximity', 'guide_hive' in state and guide_heart_enabled(profile)
                 and os.environ.get('CRUCIBLE_SOLO_GUIDE_HEART') == 'proximity'),
                ('reload', bot_reload_enabled()),
                ('strafe', os.environ.get('CRUCIBLE_SOLO_BOT_STRAFE') == '1'),
                ('turn', os.environ.get('CRUCIBLE_SOLO_BOT_TURN') == '1'),
                ('idle', os.environ.get('CRUCIBLE_SOLO_BOT_IDLE') == '1'),
                ('accel-ai', os.environ.get('CRUCIBLE_SOLO_BOT_ACCEL') in ('1', 'ai')),
                ('accel-config', os.environ.get('CRUCIBLE_SOLO_BOT_ACCEL') == 'config'),
                ('chase-cap', os.environ.get('CRUCIBLE_SOLO_BOT_CHASE_CAP') == '1'),
                ('flee', os.environ.get('CRUCIBLE_SOLO_BOT_FLEE') == '1'),
                ('anchored', os.environ.get('CRUCIBLE_SOLO_BOT_ANCHORED') == '1'),
                ('forced-aggro', os.environ.get('CRUCIBLE_SOLO_FORCED_AGGRO') == '1'),
                ('flinch', hit_reaction_enabled() and not player_flinch_enabled()),
                ('flinch-all', player_flinch_enabled())) if on]
            if switches or os.environ.get('CRUCIBLE_SOLO_LOS'):
                log('      HUNTER AI switches: %s; sight %s'
                    % (' '.join(switches) or 'none',
                       os.environ.get('CRUCIBLE_SOLO_LOS') or 'off'))
            import dit_motion_policy
            pause_range, move_range = dit_motion_policy.approved_patrol_motion_ranges(
                os.environ.get('CRUCIBLE_DIT_TRANSPORT_ROOT'))
            if pause_range is not None and move_range is not None:
                for bot in state['squad'].bots:
                    bot.dit_patrol_pause_range = pause_range
                    bot.dit_patrol_move_range = move_range
                log('      DIT approved human patrol pause/move IQR %.2f..%.2f / %.2f..%.2f s'
                    % (pause_range + move_range))
        placements = practice_population.creatures(profile.level)
        if placements:
            import creature_ai
            state['creatures'] = creature_ai.CreatureDirector(placements)
            state['creature_types'] = {p.entity_id: (p.shared_types, p.local_id)
                                       for p in placements}
            # practicearena_spittertracker.cdl respawns the no-aggro slice,
            # rather than restoring the initially aggressive spitter (docs/20).
            state['creature_respawn_profiles'] = {
                p.entity_id: creature_ai.SPITTER_NOAGGRO for p in placements
                if profile.level == solo_modes.PRACTICE.level and p.profile == 'spitter'}


def log_refused_report(state, shooter, reason, detail):
    """Log a refused projectile result at counts 1, 2, 4, 8, ... per gate.

    Accepted hits are logged one by one. Refusals used to be silent, so a
    live run could not tell a client that never reports hunter hits from a
    gate that refuses every report.
    """
    counts = state.setdefault('refused_reports', {})
    count = counts[shooter, reason] = counts.get((shooter, reason), 0) + 1
    if count & (count - 1) == 0:
        log('      SOLO COMBAT report refused shooter=%08x (%s, count %d): %s'
            % (shooter, reason, count, detail))


def observe_solo_combat(message, state):
    """Route accepted player and opt-in hunter projectile results to combat."""
    if state.get('match_result') is not None:
        return
    arena = state.get('combat')
    if arena is None or (message.delivery, message.component, message.rpc) != (3, 115, 4):
        return
    shooter = message.entity_id
    # Server-resolved hunter rounds exclude client reports: no double hits.
    bot_reports = (os.environ.get('CRUCIBLE_SOLO_BOT_HIT_REPORTS') == '1'
                   and not server_bot_damage_enabled())
    if shooter != PLAYER_CHARACTER_ID and (not bot_reports
                                           or shooter not in HUNTER_BOT_IDS):
        if bot_reports:
            # A hunter result sent under some other id would otherwise vanish.
            log_refused_report(state, shooter, 'unregistered shooter',
                               message.data.hex())
        return
    try:
        projectile, reported = parse_projectile_result(message.data)
    except ValueError:
        log_refused_report(state, shooter, 'invalid vector', message.data.hex())
        return
    # game_clock_reply drives simulation in elapsed session seconds, truncated
    # to milliseconds. Use that same domain for respawn and forced-aggro
    # deadlines. Runtime startup always establishes clock_epoch; direct helper
    # harnesses without a session clock inject their own simulation time.
    now = time.monotonic()
    if 'clock_epoch' in state:
        now = int((now - state['clock_epoch']) * 1000) / 1000.
    previous = arena.last_projectile.get(shooter)
    # The player fires the selected hero's primary; a bot its slot's hero's.
    from combat import HERO_PRIMARY, MARINE_PRIMARY
    weapon = HERO_PRIMARY.get(hero_loadout.selected().name if shooter == PLAYER_CHARACTER_ID
                              else bot_hero(shooter), MARINE_PRIMARY)
    hit = arena.apply_reported_hit(shooter, projectile, reported, weapon=weapon, now=now,
                                   distance=reported_hit_range(state, shooter))
    if hit is None:
        log_refused_report(state, shooter, arena.last_rejection,
                           'projectile %d after %s, reported %s' % (
                               projectile, previous,
                               ' '.join('%08x' % e for e in reported) or 'none'))
        return
    log('      SOLO COMBAT %s hit target=%08x damage=%.0f health=%.0f%s' %
        ('player' if shooter == PLAYER_CHARACTER_ID else 'hunter %08x' % shooter,
         hit.target, hit.damage, hit.health, ' KILLED' if hit.killed else ''))
    hive = state.get('guide_hive')
    if hive is not None and hit.target == HIVE_ENTITY_ID:
        # hive_ondamage.cdl zeroes HiveOutOfCombat: regeneration waits again.
        hive['next_heal'] = now + HIVE_REGEN_AFTER
        if hit.killed:
            log('      GUIDE HIVE destroyed at %.1f s' % now)
            if guide_heart_enabled():
                # hive_ondeathspawnessence.cdl: a heart on the hive's exact
                # position (ForceCenterSpawn), at once.
                state['guide_heart'] = {'position': hive['position'], 'sent': False,
                                        'capture_started': None, 'captured_at': None,
                                        'interrupts': 0}
    if shooter == PLAYER_CHARACTER_ID:
        confirm_player_hit(state, hit, weapon)
        import feedback_rings
        if (feedback_rings.damage_numbers_enabled()
                and state.get('floaters_enabled', True)):
            state.setdefault('floater_ring', feedback_rings.FloaterRing()).hit(
                hit.target, hit.damage, now)
        if hit_reaction_enabled():
            note_hit_reaction(state, hit.target)
        squad = state.get('squad')
        player = state.get('player_position')
        if squad is not None and not hit.killed and player is not None:
            # marine_guide_on_hit.cdl's flight (docs/36): before forced aggro,
            # which a fleeing Marine ignores.
            victim = arena.entities.get(hit.target)
            shooter_entity = arena.entities.get(PLAYER_CHARACTER_ID)
            for bot in squad.bots:
                if (bot.entity == hit.target and getattr(bot, 'flee', False)
                        and victim is not None and shooter_entity is not None):
                    bot.consider_flee(now, player, victim.health / victim.maximum,
                                      shooter_entity.health / shooter_entity.maximum)
        if (squad is not None and not hit.killed
                and os.environ.get('CRUCIBLE_SOLO_FORCED_AGGRO') == '1'):
            for bot in squad.bots:
                if bot.entity == hit.target:
                    bot.note_damage(now, PLAYER_CHARACTER_ID)
    director = state.get('creatures')
    if director is not None:
        if hit.killed:
            creature = director.creatures.get(hit.target)
            if creature is not None:
                creature.kill(now)
        else:
            director.note_damage(now, hit.target, PLAYER_CHARACTER_ID)


def reported_hit_range(state, shooter):
    """Opt-in shooter-to-target range for a reported player hit, or None.

    A ClientProjectileResult carries no range, so damage used the weapon's
    close-range maximum. With CRUCIBLE_SOLO_HIT_RANGE=1 the falloff gets the
    distance from the player's decoded input position to the target's
    server position (a hunter's, or a Practice placement's).
    """
    origin = state.get('player_position')
    if (shooter != PLAYER_CHARACTER_ID or origin is None
            or os.environ.get('CRUCIBLE_SOLO_HIT_RANGE') != '1'):
        return None
    import practice_population
    positions = {place.entity_id: place.position for place in
                 practice_population.population(solo_modes.selected().level)}
    squad = state.get('squad')
    if squad is not None:
        positions.update((bot.entity, bot.position) for bot in squad.bots)
    if 'guide_hive' in state:
        positions[HIVE_ENTITY_ID] = state['guide_hive']['position']

    def distance(target):
        position = positions.get(target)
        return None if position is None else math.dist(origin, position)
    return distance


def confirm_player_hit(state, hit, weapon):
    """Queue the opt-in hit and kill markers for one accepted player hit."""
    import shooter_feedback
    from combat import HERO_PRIMARY_INDEX
    if shooter_feedback.hit_confirm_enabled():
        # An unknown hero's index is out of range: the marker still shows,
        # only the projectile's hit-confirm sound is skipped (0xbc47d1).
        index = HERO_PRIMARY_INDEX.get(hero_loadout.selected().name, -1)
        queue_player_rpc(state, shooter_feedback.PROJECTILE_REQUEST_COMPONENT,
                         shooter_feedback.CONFIRM_HIT_INDICATOR,
                         shooter_feedback.build_confirm_hit_indicator(
                             scale=shooter_feedback.hit_scale(hit.damage, weapon),
                             projectile_type_index=index))
    if hit.killed and shooter_feedback.kill_confirm_enabled():
        queue_player_rpc(state, shooter_feedback.PROJECTILE_REQUEST_COMPONENT,
                         shooter_feedback.CONFIRM_KILL_INDICATOR,
                         shooter_feedback.build_confirm_kill_indicator())


# marine_default.movementconfig Movement Speeds[0]: the chase cap a spoofed
# Marine can hold without sprinting (snapshots/speed-20261025, docs/36).
MARINE_DEFAULT_FORWARD_SPEED = 5.5

# docs/36: component 80's networked flinch (snapshots/hit-reactions-20260923).
HIT_REACTION_RESEND_TICKS = 3


def hit_reaction_enabled():
    """'1': hunters the player hits flinch. 'all': the player flinches too."""
    return os.environ.get('CRUCIBLE_SOLO_HIT_REACTION') in ('1', 'all')


def player_flinch_enabled():
    return os.environ.get('CRUCIBLE_SOLO_HIT_REACTION') == 'all'


def note_hit_reaction(state, target):
    """Count one accepted player hit on a hunter, for its networked flinch.

    The client plays a flinch when NetworkedAdditiveHitIndex rises above the
    last one it played (worker 0xdae770), and predicts one for its own
    projectile hits. Counting only accepted reports keeps this count at or
    below the client's, so nothing plays twice. The u8 never wraps: 0 after
    255 would never trigger again, and it is never reset within a
    connection. The yaw is taken in the frame of the facing the hunter's
    last Location update carried.
    """
    import hit_reaction_events
    squad = state.get('squad')
    player = state.get('player_position')
    bot = next((b for b in getattr(squad, 'bots', ()) if b.entity == target), None)
    if target not in HUNTER_BOT_IDS or bot is None or player is None:
        return
    facing = state.get('hunter_facing', {}).get(target) or bot.facing()
    yaw = hit_reaction_events.flinch_yaw(facing, player, bot.position)
    if yaw is not None:
        count_flinch(state, target, yaw)


def note_player_flinch(state, shooter_position):
    """Count one hunter round landing on the player, for the player's flinch.

    The yaw is taken in the frame of the player's newest input rotation
    (docs/25), so the character flinches away from the Marine that hit it.
    Hunter rounds are server-resolved: the client reports none of them, so
    this count is what drives the player's flinch.
    """
    import hit_reaction_events
    facing, player = state.get('player_facing'), state.get('player_position')
    if facing is None or player is None:
        return
    yaw = hit_reaction_events.flinch_yaw(facing, shooter_position, player)
    if yaw is not None:
        count_flinch(state, PLAYER_CHARACTER_ID, yaw)


def count_flinch(state, entity, yaw):
    """One more hit on `entity`: its index rises (never past 255) and the
    update repeats for HIT_REACTION_RESEND_TICKS ticks."""
    entry = state.setdefault('hit_reactions', {}).setdefault(entity, {'index': 0})
    entry['index'] = min(255, entry['index'] + 1)
    entry['yaw'] = yaw
    entry['resend'] = HIT_REACTION_RESEND_TICKS


def hit_reaction_messages(state, previous_id):
    """This tick's component-80 updates, and the RelativeId chain's new tail.

    Each change repeats for HIT_REACTION_RESEND_TICKS ticks; a repeat cannot
    replay a flinch, as only a larger index triggers one. The client itself
    limits flinches to one per flinchDelay (0.2 s for the Marine).
    """
    reactions = state.get('hit_reactions')
    if not reactions:
        return [], previous_id
    if not split_updates_enabled():
        # With a hunter's Location in the same packet, the client would
        # refuse this second message for it as old (docs/36).
        if not state.get('hit_reaction_needs_split_logged'):
            state['hit_reaction_needs_split_logged'] = True
            log('      HIT REACTION disabled: it needs CRUCIBLE_SHARD_SPLIT_UPDATES=1')
        return [], previous_id
    import hit_reaction_events
    messages = []
    logged = state.setdefault('hit_reaction_logged', {})
    for entity in sorted(reactions):
        entry = reactions[entity]
        if not entry.get('resend'):
            continue
        entry['resend'] -= 1
        messages.append(hit_reaction_events.build_hit_reaction_update(
            entity, MARINE_SHARED_TYPES, index=entry['index'], yaw=entry['yaw'],
            previous_id=previous_id, autonomous=entity == PLAYER_CHARACTER_ID))
        previous_id = entity & 0xffffff
        # Logged at indexes 1, 2, 4, ...
        if entry['index'].bit_length() != logged.get(entity, 0):
            logged[entity] = entry['index'].bit_length()
            log('      HIT REACTION %08x index %d (yaw %.0f)'
                % (entity, entry['index'], entry['yaw']))
    return messages, previous_id


def feedback_ring_message(state, now):
    """docs/36: a builder for this tick's damage-number/hit-arc update, or None."""
    import feedback_rings
    enabled = None
    if state.get('floaters_enabled_resend'):
        state['floaters_enabled_resend'] -= 1
        enabled = state['floaters_enabled']
    taken = {}
    for key, name in (('floater_ring', 'damage numbers'), ('indicator_ring', 'hit arcs')):
        ring = state.get(key)
        if ring is None or (key == 'floater_ring' and enabled is not None):
            # While FloatersEnabled is echoed it goes alone, as the bare
            # 4-bit record; damage numbers wait in the ring (docs/36).
            continue
        taken[key] = ring.take(now)
        # Log each time the count reaches a new power of two; several hits in
        # one tick may step over the exact power.
        logged = state.setdefault('ring_logged', {})
        if ring.written.bit_length() != logged.get(key, 0):
            logged[key] = ring.written.bit_length()
            log('      SOLO FEEDBACK %s index %d (count %d)'
                % (name, ring.last_index, ring.written))
    floaters, indicators = taken.get('floater_ring'), taken.get('indicator_ring')
    if floaters is None and indicators is None and enabled is None:
        return None
    if not split_updates_enabled():
        # The ring message and the player's own health are both for the
        # player; in one packet the client would drop the second (docs/36).
        if not state.get('rings_need_split_logged'):
            state['rings_need_split_logged'] = True
            log('      SOLO FEEDBACK rings disabled: they need CRUCIBLE_SHARD_SPLIT_UPDATES=1')
        return None
    return lambda previous_id: feedback_rings.build_ring_update(
        floaters=floaters, indicators=indicators, floaters_enabled=enabled,
        previous_id=previous_id)


def solo_match_rank(state):
    """Opt-in local rank for this tick after a result, or None.

    Every tick repeats GameOver, but the rank rides only every other tick.
    If the client refuses a rank datagram, it loses one GameOver repetition,
    never the result.
    """
    if os.environ.get('CRUCIBLE_SOLO_MATCH_RANK') != '1':
        return None
    result = state['match_result']
    result['ticks'] = result.get('ticks', 0) + 1
    if result['ticks'] % 2:
        return None
    rank = 1 if result['winner_team'] == solo_modes.selected().local_team else 2
    if not result.get('rank_logged'):
        result['rank_logged'] = True
        log('      ALPHA HUNTERS rank %d published (PlayerStats.Rank)' % rank)
    return rank


def regenerate_guide_hive(state, arena, now):
    """hive_outofcombatregen.cdl: +100 health a second, 19.5 s after a hit."""
    hive = state['guide_hive']
    if (hive['next_heal'] is None or now < hive['next_heal']
            or HIVE_ENTITY_ID not in arena.entities or not arena.alive(HIVE_ENTITY_ID)):
        return
    if arena.heal(HIVE_ENTITY_ID, HIVE_REGEN_STEP):
        hive['regens'] += 1
        if hive['regens'] & (hive['regens'] - 1) == 0:
            log('      GUIDE HIVE regenerating (count %d, health %.0f)'
                % (hive['regens'], arena.health(HIVE_ENTITY_ID)))
    hive['next_heal'] = (now + HIVE_REGEN_INTERVAL
                         if arena.health(HIVE_ENTITY_ID) < HIVE_HEALTH_MAX else None)


def solo_simulation_messages(state, now, *, previous_id=0):
    """AI and combat entity messages for one clock tick, chained by RelativeId."""
    if state.get('match_result') is not None:
        profile = solo_modes.selected()
        messages = [build_solo_match_phase_update(
            profile, solo_modes.MATCH_PHASE['GameOver'], previous_id=previous_id)]
        rank = solo_match_rank(state)
        if rank is not None:
            messages.append(build_player_rank_update(
                rank, previous_id=profile.phase_entry + profile.baked_counter_bias))
        return messages
    messages = []
    player = state.get('player_position')
    arena = state.get('combat')
    land_hunter_hits(state, arena, now)
    # docs/36: the player's damage numbers and hit arcs lead the tick, so the
    # hunters' Location messages separate them from the player's own health
    # update at the end; one entity never gets two messages in a row.
    rings = feedback_ring_message(state, now)
    if rings is not None:
        messages.append(rings(previous_id))
        previous_id = PLAYER_CHARACTER_ID & 0xffffff
    if 'guide_hive' in state and arena is not None:
        regenerate_guide_hive(state, arena, now)
    if 'guide_heart' in state:
        heart, previous_id = guide_heart_messages(state, arena, now, previous_id)
        messages.extend(heart)
    health_entities = arena.tick(now) if arena is not None else []
    active_entities = ({entity for entity in arena.entities if arena.alive(entity)}
                       if arena is not None else None)
    if active_entities is not None and PLAYER_CHARACTER_ID not in active_entities:
        player = None
    squad = state.get('squad')
    if squad is not None:
        updates, steps = build_hunter_ai_updates(squad, now, player, previous_id=previous_id,
                                                active_entities=active_entities)
        messages.extend(updates)
        from dit_motion import transport_for_state
        transport = transport_for_state(state)
        if transport is not None:
            # DIT is an observation feed; it must never cost a tick.
            for index, step in enumerate(steps, 1):
                guarded(state, 'DIT observation', transport.observe,
                        ('bot', 'marine-hunter-%d' % index), step.position, now)
        if updates:
            previous_id = steps[-1].entity & 0xffffff
        # One animation update per bot per tick carries every enabled policy's
        # properties. Values repeat each tick so a dropped update cannot
        # permanently lose a state.
        animation = {}
        if os.environ.get('CRUCIBLE_SOLO_MOVEMENT_ANIMATION') == '1':
            # Native MovementGameplayCommon writes 0 with input and 1 after
            # 0.1 seconds without input (3292744..329277b, CVar 7bbecd0).
            # The bot simulation exposes actual pre-separation speed, not
            # native movement inputs: this opt-in policy uses that proxy.
            # Millisecond session time avoids binary-float threshold jitter.
            now_ms = round(now * 1000)
            idle_since = state.setdefault('hunter_animation_idle_since_ms', {})
            present = {step.entity for step in steps}
            for entity in idle_since.keys() - present:
                del idle_since[entity]
            for step in steps:
                if step.speed > 0 or step.entity not in idle_since:
                    idle_since[step.entity] = now_ms
                flag = float(step.speed <= 0 and now_ms - idle_since[step.entity] >= 100)
                animation.setdefault(step.entity, {})['time_since_movement_input'] = flag
        else:
            state.pop('hunter_animation_idle_since_ms', None)
        if os.environ.get('CRUCIBLE_SOLO_COMBAT_ANIMATION') == '1':
            # marine.animgraph (docs/28): Root base->death on is_dead, and
            # death->base with zero blend once it clears. primary_fire_sm0
            # enters recoil on using_primary1 and replays it every 0.1 s
            # while held. marine_primary_onfire.cdl sets using_primary1 and
            # using_aim_ik on entry and clears using_primary1 in OnExit, so
            # both are held for the whole attack. Bots never aim down sights
            # (a separate marine_aimdownsights ability), so is_aiming stays 0.
            dead = [entity for entity in HUNTER_BOT_IDS
                    if arena is not None and entity in arena.entities
                    and not arena.alive(entity)]
            for step in steps:
                if step.entity not in dead:
                    animation.setdefault(step.entity, {}).update(
                        is_dead=False, using_primary1=step.firing,
                        using_aim_ik=step.firing)
                    if bot_reload_enabled():
                        animation[step.entity]['using_reload'] = step.reloading
            for entity in dead:
                animation.setdefault(entity, {}).update(
                    is_dead=True, using_primary1=False, using_aim_ik=False)
                if bot_reload_enabled():
                    animation[entity]['using_reload'] = False
        if animation:
            from animation_events import build_animation_update
            for entity, values in animation.items():
                messages.append(build_animation_update(
                    entity, MARINE_SHARED_TYPES, values=values, previous_id=previous_id))
                previous_id = entity & 0xffffff
        for step in steps:
            for shot in step.shots:
                # docs/29: Marine primary is AllowedAssets[4]. For an
                # undeflected shot, valid attachment None (0) leaves the
                # projectile asset's own attachment in charge of rendering.
                if os.environ.get('CRUCIBLE_SOLO_FIRE') == '1':
                    state.setdefault('pending_hunter_shots', []).append(shot)
                # Without CRUCIBLE_SOLO_BOT_DAMAGE=server this is presentation
                # only; with it, docs/35 resolves the round here.
                if arena is not None and server_bot_damage_enabled():
                    landing = hunter_gunnery(state).resolve(shot)
                    sight = state.get('sight')
                    if landing is not None and sight is not None:
                        aim = tuple(shot.origin[i] + shot.direction[i] * shot.range
                                    for i in range(3))
                        if not sight(shot.origin, aim):
                            # The terrain or a static collider stops the
                            # round (docs/36). Logged at counts 1, 2, 4, ...
                            blocked = state['hunter_rounds_blocked'] = (
                                state.get('hunter_rounds_blocked', 0) + 1)
                            if blocked & (blocked - 1) == 0:
                                log('      HUNTER AI rounds blocked by line of sight (count %d)'
                                    % blocked)
                            landing = None
                    if landing is not None:
                        state.setdefault('hunter_landings', []).append(landing)
                log('      HUNTER AI %08x fired at %08x (range %.1f m)' %
                    (shot.shooter, shot.target, shot.range))
        for step in steps:
            if hit_reaction_enabled():
                # The frame the client measures a flinch's yaw in.
                state.setdefault('hunter_facing', {})[step.entity] = step.facing
            last = state.setdefault('hunter_states', {})
            if last.get(step.entity) != step.state:
                last[step.entity] = step.state
                log('      HUNTER AI %08x -> %s' % (step.entity, step.state))
            reloading = state.setdefault('hunter_reloading', {})
            now_reloading = getattr(step, 'reloading', False)
            if now_reloading and not reloading.get(step.entity):
                log('      HUNTER AI %08x reloading' % step.entity)
            reloading[step.entity] = now_reloading
        # Idle rests change no logged state (the bot stays in PATROL), so
        # count them: logged at counts 1, 2, 4, ... per bot.
        for bot in getattr(state.get('squad'), 'bots', ()):
            for attribute, key, text in (
                    ('rests', 'hunter_rests_logged', 'resting between patrol hops'),
                    ('flees', 'hunter_flees_logged', 'fleeing a losing fight')):
                count = getattr(bot, attribute, 0)
                if not count:
                    continue
                logged = state.setdefault(key, {})
                if count.bit_length() != logged.get(bot.entity, 0):
                    logged[bot.entity] = count.bit_length()
                    log('      HUNTER AI %08x %s (count %d)' % (bot.entity, text, count))
        if os.environ.get('CRUCIBLE_SOLO_FIRE') == '1':
            from combat import MARINE_PRIMARY
            from projectile_events import build_hybrid_activation_update
            # One activation per shooter per clock packet: the rifle fires
            # every 96 ms and the clock runs at up to 20 Hz, so each queue
            # drains. Per-EntityMessage bool reset is source-proven (docs/29);
            # the per-shooter cap keeps one component-115 write per entity per
            # packet. Creation times are preserved and events past their
            # authored lifetime are discarded.
            from combat import HERO_PRIMARY, HERO_PRIMARY_INDEX
            pending = [
                shot for shot in state.get('pending_hunter_shots', [])
                if 0 <= now - shot.time <= HERO_PRIMARY.get(
                    bot_hero(shot.shooter), MARINE_PRIMARY).lifetime_s
                and (active_entities is None or
                     {shot.shooter, shot.target} <= active_entities)]
            due, waiting = {}, []
            for shot in pending:
                if shot.shooter in due:
                    waiting.append(shot)
                else:
                    due[shot.shooter] = shot
            state['pending_hunter_shots'] = waiting
            for shot in sorted(due.values(), key=lambda shot: shot.shooter):
                slots = state.setdefault('hunter_projectile_slots', {})
                slot = slots.get(shot.shooter, 0)
                target = tuple(shot.origin[i] + shot.direction[i] * shot.range
                               for i in range(3))
                messages.append(build_hybrid_activation_update(
                    shot.shooter, MARINE_SHARED_TYPES, slot=slot,
                    allowed_projectile_index=HERO_PRIMARY_INDEX.get(
                        bot_hero(shot.shooter), 4), fire_origin=shot.origin,
                    ideal_origin=shot.origin, ideal_target_position=target,
                    relative_time_ms=int((now - shot.time) * 1000),
                    has_first_update_hit=False, deflected=False, attachment_id=0,
                    previous_id=previous_id))
                previous_id = shot.shooter & 0xffffff
                slots[shot.shooter] = (slot + 1) % 10
        else:
            state.pop('pending_hunter_shots', None)
    director = state.get('creatures')
    if director is not None:
        if active_entities is not None:
            from creature_ai import Creature
            for entity, creature in list(director.creatures.items()):
                if entity not in active_entities and not creature.dead:
                    creature.kill(now)
                elif entity in active_entities and creature.dead:
                    profile = state.get('creature_respawn_profiles', {}).get(
                        entity, creature.profile)
                    director.creatures[entity] = Creature(
                        entity, profile, creature.position, yaw=creature.yaw)
        targets = {PLAYER_CHARACTER_ID: player} if player else {}
        for event in director.step(now, targets):
            kind = getattr(event, 'kind', 'attack')
            log('      CREATURE AI %08x %s target=%08x' %
                (event.creature_id, kind, event.target_id))
        for entity, transform in director.rotations().items():
            types, local = state['creature_types'][entity]
            messages.append(build_location_update(entity, types, transform,
                                                  local=local, previous_id=previous_id))
            previous_id = entity & 0xffffff
    if arena is not None:
        health = arena.build_messages(health_entities, previous_id=previous_id)
        messages.extend(health)
        if health_entities:
            previous_id = health_entities[-1].entity_id & 0xffffff
        reactions, previous_id = hit_reaction_messages(state, previous_id)
        messages.extend(reactions)
        profile = solo_modes.selected()
        # The alias is 'alpha-hunters'; the profile's own name is the shipped
        # config's DOUBLES_CASUAL, so compare profiles, not that string.
        if profile.mode_profile == solo_modes.MODES['alpha-hunters']:
            winner = arena.terminal_winner()
            if winner is not None:
                if 'match_result' not in state:
                    state['match_result'] = {'winner_team': winner, 'at_seconds': now}
                    log('      ALPHA HUNTERS match over: winner team %s'
                        % (winner if winner else 'draw'))
                messages.append(build_solo_match_phase_update(
                    profile, solo_modes.MATCH_PHASE['GameOver'],
                    previous_id=previous_id))
    return messages


def build_practice_health_update(entity_id, health, *, previous_id=0):
    """Candidate nonlethal NPC delta; native graph captured in live-combat-layout.

    Health records 23/24 enter Resource/DefaultResource, 25 skips timing,
    26 selects min/max/current (35dc910). Do not replace regeneration fields.
    """
    from practice_population import SPITTER_SHARED_TYPES, DUMMY_SHARED_TYPES
    if entity_id in (0x1000003, 0x1000004):
        types, maximum, local = SPITTER_SHARED_TYPES, 400.0, False
    elif 0xfe00004b <= entity_id <= 0xfe000052:
        types, maximum, local = DUMMY_SHARED_TYPES, 600.0, True
    else:
        raise ValueError('unknown practice NPC')
    if not 1 <= health <= maximum:
        raise ValueError('nonlethal probe health out of range')
    bits = []
    for component in types:
        bits.extend((True, False, False, False, False, False, True, True, False, True)
                    if component == 79 else (False,))
    return build_entity_message(entity_id & 0xffffff, pack_type_and_flags(ENTITY_ROLE_CLIENT_SIMULATION),
        payload=build_replication_record(bits) + struct.pack('>fff', 0, maximum, health),
        previous_id=previous_id, local=local)


def observe_practice_damage(message, state):
    """Opt-in client-reported body-hit experiment, not authoritative combat.

    Fixed 20 damage is the Marine near body-hit value. No crit/falloff,
    lethal damage or death handling is claimed by this bounded probe.
    """
    if (solo_modes.selected() != solo_modes.PRACTICE
            or os.environ.get('CRUCIBLE_PRACTICE_DAMAGE') != '1'
            or not state.get('practice_npcs_sent')
            or (message.delivery, message.entity_id, message.component, message.rpc)
                != (3, 0x1000002, 115, 4)):
        return
    try:
        projectile, reported = parse_projectile_result(message.data)
    except ValueError:
        return
    previous = state.get('damage_last_projectile')
    if previous is not None and not 0 < (projectile - previous) % 256 < 128:
        return
    state['damage_last_projectile'] = projectile
    targets = set(reported) & {0x1000003, 0x1000004, *range(0xfe00004b, 0xfe000053)}
    # Marine primary despawns on collision; ambiguous multi-target reports
    # cannot establish one valid target for this experiment.
    if len(targets) != 1:
        return
    target = targets.pop()
    health = state.setdefault('npc_health', {})
    maximum = 400.0 if target in (0x1000003, 0x1000004) else 600.0
    health[target] = max(1.0, health.get(target, maximum) - 20.0)
    log('      SOLO DAMAGE PROBE target=%08x health=%.0f projectile=%d' %
        (target, health[target], projectile))


def clock_interval():
    """Seconds between clock packets.

    The default 50 ms is the accepted stall fix (docs/16). Retail's
    net_ServerRateMs ("Server tick rate to use for network relevent
    simulations", descriptor 0x74455c0) is a uint16 set to 32 at 0x4c9c80,
    and the client reads it in its input timing (0xc4b25f), projectile
    system (0xbbc285) and ClientNetworkAgent getter (0xe22ff0).
    CRUCIBLE_SHARD_TICK_MS=32 runs the retail cadence (docs/36).
    """
    try:
        milliseconds = int(os.environ.get('CRUCIBLE_SHARD_TICK_MS') or 50)
    except ValueError:
        milliseconds = 50
    return min(max(milliseconds, 16), 100) / 1000.


# Not authored: a match ends once failing ticks are at least this share of
# the last window, judged over no less than the shorter span.
SIMULATION_FAILURE_GAMEOVER_SECONDS = 2.0
SIMULATION_FAILURE_WINDOW_SECONDS = 4.0
SIMULATION_FAILURE_FRACTION = 0.2


def guarded(state, where, function, *args, **kwargs):
    """Run simulation code without letting an exception end the shard.

    An exception escaping the tick or the receive path stops the process,
    systemd restarts it mid-match, and the client is left in the rejoin trap
    (docs/30). Log the traceback instead (at counts 1, 2, 4, ...) and keep
    the connection: that tick sends only its clock. Returns None on error.
    """
    try:
        return function(*args, **kwargs)
    except Exception:
        import traceback
        counts = state.setdefault('simulation_errors', {})
        count = counts[where] = counts.get(where, 0) + 1
        if count & (count - 1) == 0:
            log('      SIMULATION ERROR in %s (count %d)\n%s'
                % (where, count, traceback.format_exc().rstrip()))
        return None


# ValidateUpdate (0x4bb5144) applies a property message only if its sequence is
# above the last one applied to that entity, as a plain unsigned compare. In
# the 5 October log that sequence equals our wire u16 LocalSequence (e.g.
# 2369 both received and refused). Whether the client unwraps it at 65535 is
# untested; if not, every update after the wrap is "old", GameOver included.
# So a connection ends its match before its sequence can wrap (docs/36).
SEQUENCE_LIMIT_DEFAULT = 64000


def sequence_limit():
    """CRUCIBLE_SHARD_SEQUENCE_LIMIT (0 disables), at most 65000."""
    try:
        value = int(os.environ.get('CRUCIBLE_SHARD_SEQUENCE_LIMIT') or SEQUENCE_LIMIT_DEFAULT)
    except ValueError:
        value = SEQUENCE_LIMIT_DEFAULT
    return max(0, min(value, 65000))


def match_max_seconds():
    """CRUCIBLE_SOLO_MATCH_MAX_SECONDS: an opt-in match timer, or None.

    Counted from the connection's first simulation tick, which is when
    GamePlay starts here; the match-flow track's director reads the same
    variable with the same meaning. A mode with no end condition (HOTH_GUIDE
    before objectives, docs/37) then still ends with GameOver.
    """
    try:
        value = float(os.environ.get('CRUCIBLE_SOLO_MATCH_MAX_SECONDS') or 0)
    except ValueError:
        return None
    return value if 0 < value < float('inf') else None


def game_clock_reply(state, *, now=None):
    """The tick's first packet, or None; game_clock_packets has them all."""
    packets = game_clock_packets(state, now=now)
    return packets[0] if packets else None


def game_clock_packets(state, *, now=None):
    """Advance ServerGameTimePoint at up to 20 Hz after solo startup.

    Retail input IDs have an 8-bit client offset which saturates at 254 when
    the server time stays fixed (c4b671..c4b6a8). Empty EntityUpdates carry
    time without changing positions or claiming server-processed inputs.
    """
    if (os.environ.get('CRUCIBLE_SHARD_BOOTSTRAP') != 'practice-character'
            or not state.get('character_links_sent') or 'clock_epoch' not in state):
        return []
    now = time.monotonic() if now is None else now
    if now - state['clock_last_sent'] < clock_interval():
        return []
    elapsed_ms = int((now - state['clock_epoch']) * 1000)
    game_time_ms = 2000 + elapsed_ms
    if not 2000 <= game_time_ms < 0xffffffff:
        return []
    state['seq'] = (state['seq'] + 1) & 0xffff
    state['clock_last_sent'] = now
    limit = sequence_limit()
    if (limit and state['seq'] >= limit and state.get('practice_npcs_sent')
            and 'match_result' not in state):
        state['match_result'] = {'winner_team': 0, 'at_seconds': elapsed_ms / 1000.,
                                 'reason': 'sequence limit'}
        log('      SEQUENCE LIMIT: LocalSequence %d reached %d; ending the match with '
            'GameOver before it wraps' % (state['seq'], limit))
    messages = []
    previous = 0
    if (os.environ.get('CRUCIBLE_SOLO_PATROL') == '1' and 'squad' not in state
            and solo_modes.selected().bot_positions and state.get('practice_npcs_sent')):
        patrol = guarded(state, 'hunter patrol', build_hunter_patrol_updates,
                         elapsed_ms / 1000.)
        if patrol:
            messages.extend(patrol)
            previous = 5
    if (os.environ.get('CRUCIBLE_PRACTICE_DAMAGE') == '1' and 'combat' not in state
            and state.get('practice_npcs_sent')):
        # Repeat absolute health state so a dropped UDP update cannot lose a hit.
        for entity, health in sorted(state.get('npc_health', {}).items()):
            messages.append(build_practice_health_update(entity, health, previous_id=previous))
            previous = entity & 0xffffff
    if state.get('practice_npcs_sent'):
        now_s = elapsed_ms / 1000.
        maximum = match_max_seconds()
        if maximum is not None and 'match_result' not in state:
            if 'match_started_at' not in state:
                state['match_started_at'] = now_s
                log('      MATCH TIMER: GameOver after %.1f s of play' % maximum)
            started = state['match_started_at']
            if now_s - started >= maximum:
                # A timeout is a draw: no team wins, so a published rank is 2.
                state['match_result'] = {'winner_team': 0, 'at_seconds': now_s,
                                         'reason': 'match timer'}
                log('      MATCH TIMER: %.1f s of play; ending the match with GameOver'
                    % (now_s - started))
        tick = guarded(state, 'simulation tick', solo_simulation_messages,
                       state, now_s, previous_id=previous)
        # A simulation that keeps failing, even only on some ticks, leaves a
        # broken match whose only exit is leaving it (the rejoin trap). When
        # failing ticks are a large share of the recent ones, end it: the
        # GameOver branch runs before any other simulation code, and GameOver
        # clears the client's rejoin data.
        outcomes = state.setdefault('tick_outcomes', collections.deque())
        outcomes.append((now_s, tick is None))
        while outcomes and now_s - outcomes[0][0] > SIMULATION_FAILURE_WINDOW_SECONDS:
            outcomes.popleft()
        failed = sum(1 for _, bad in outcomes if bad)
        if (tick is None and 'match_result' not in state
                and now_s - outcomes[0][0] >= SIMULATION_FAILURE_GAMEOVER_SECONDS
                and failed >= SIMULATION_FAILURE_FRACTION * len(outcomes)):
            state['match_result'] = {'winner_team': 0, 'at_seconds': now_s,
                                     'reason': 'simulation error'}
            log('      SIMULATION FAILED on %d of %d ticks: ending the match with GameOver'
                % (failed, len(outcomes)))
        if tick is not None:
            messages.extend(tick)
    groups = split_entity_messages(messages) if split_updates_enabled() else [messages]
    packets = []
    for index, group in enumerate(groups or [[]]):
        if index:
            state['seq'] = (state['seq'] + 1) & 0xffff
        packets.append(build_entity_updates(shard_bpt(), state['seq'], state['received_seq'],
                                            group, game_time_ms=game_time_ms,
                                            seq_window=state['received_window']))
    return packets


def send_game_clocks(sock, states, *, idle_reset, now=None):
    """Timer-driven so input silence cannot deadlock the prediction clock."""
    now = time.monotonic() if now is None else now
    for addr, state in states.items():
        last_valid = state.get('last_valid_monotonic')
        if last_valid is None or now - last_valid > idle_reset:
            continue
        for packet in game_clock_packets(state, now=now):
            sock.sendto(packet, addr)
        packet = rpc_reply(state)
        if packet is not None:
            sock.sendto(packet, addr)


def ranged_uint32(value):
    """Full-range u32, four bytes big endian — the encoding docs/11 recovered
    for every integer whose declared range spans the whole type."""
    return value.to_bytes(4, "big")


@dataclass(frozen=True)
class EntityRpc:
    delivery: int
    entity_id: int
    component: int
    rpc: int
    data: bytes


def parse_entity_rpcs(payload):
    """Client packet 11: bounded vector of interleaved RPC headers and data.

    Retail 4ba16f0: count 0..2048 (u16 BE). 4aa80c0: delivery 0..4,
    full u32 entity, u8 component, u8 RPC; 4aa8380: u16 size <=6144.
    Parse completely before returning, so truncated batches cannot dispatch.
    """
    if len(payload) < 2:
        raise ValueError('missing RPC count')
    count = int.from_bytes(payload[:2], 'big')
    if count > 2048:
        raise ValueError('RPC count exceeds retail bound')
    offset, messages = 2, []
    for _ in range(count):
        if len(payload) - offset < 9:
            raise ValueError('truncated RPC header')
        delivery, entity, component, rpc, size = struct.unpack_from('>BIBBH', payload, offset)
        offset += 9
        if delivery > 4 or size > 6144 or size > len(payload) - offset:
            raise ValueError('invalid RPC range or truncated data')
        messages.append(EntityRpc(delivery, entity, component, rpc, payload[offset:offset + size]))
        offset += size
    if offset != len(payload):
        raise ValueError('trailing RPC bytes')
    return messages


def build_entity_rpcs(messages):
    """Server packet 11 payload: the same vector body the client sends.

    The server-to-client EntityRpcs serializer 0xe26490 calls 0x4ba0d10,
    which reads through 0x4ba16f0 exactly as parse_entity_rpcs does.
    """
    if len(messages) > ENTITY_MESSAGES_MAX:
        raise ValueError('RPC count exceeds retail bound')
    payload = len(messages).to_bytes(2, 'big')
    for message in messages:
        if not 0 <= message.delivery <= 4 or len(message.data) > 6144:
            raise ValueError('invalid RPC delivery or size')
        payload += struct.pack('>BIBBH', message.delivery, message.entity_id,
                               message.component, message.rpc, len(message.data))
        payload += message.data
    return payload


def queue_player_rpc(state, component, rpc, data):
    """Queue one Authority -> Autonomous RPC on the player's own character."""
    state.setdefault('pending_rpcs', []).append(
        EntityRpc(2, PLAYER_CHARACTER_ID, component, rpc, data))


def rpc_reply(state):
    """One EntityRpcs packet carrying every queued RPC, or None."""
    pending = state.pop('pending_rpcs', None)
    if not pending or 'received_seq' not in state:
        return None
    counts = state.setdefault('sent_rpcs', {})
    for message in pending:
        key = (message.component, message.rpc)
        count = counts[key] = counts.get(key, 0) + 1
        if count & (count - 1) == 0:
            log('      SOLO FEEDBACK sent rpc %d/%d to %08x (count %d)'
                % (message.component, message.rpc, message.entity_id, count))
    state['seq'] = (state['seq'] + 1) & 0xffff
    return build_shard(shard_bpt(), 11, state['seq'], state['received_seq'],
                       payload=build_entity_rpcs(pending),
                       seq_window=state['received_window'])


def parse_projectile_result(data):
    """Decode ClientProjectileResult, without treating it as damage authority.

    Retail 9d9120: projectileId u8 followed by prevHitEntities vector.
    883530 -> 889840 bounds that vector to eight full network IDs.
    Captures include the shooter itself; the list is not a damage command.
    """
    if len(data) < 2 or data[1] > 8 or len(data) != 2 + 4 * data[1]:
        raise ValueError('invalid projectile result vector')
    return data[0], tuple(int.from_bytes(data[i:i + 4], 'big')
                          for i in range(2, len(data), 4))


def observe_client_actions(data, state):
    """The newest frame's held actions, while HOTH_GUIDE's hive is in play.

    A heart capture starts on Interact (docs/37). Every action bit's rise is
    logged per bit at counts 1, 2, 4, ..., and so is every payload size other
    than the decoded 326 bytes, so a live run shows which bit a key sets and
    whether pressing it changes the shape.
    """
    if len(data) != client_input.INPUT_SIZE:
        sizes = state.setdefault('input_sizes', {})
        count = sizes[len(data)] = sizes.get(len(data), 0) + 1
        if count & (count - 1) == 0:
            log('      CLIENT INPUT shape %d bytes (count %d)' % (len(data), count))
    bits = client_input.parse_newest_actions(data)
    if bits is None:
        return
    risen = bits & ~state.get('input_actions', 0)
    state['input_actions'] = bits
    rises = state.setdefault('input_action_rises', {})
    for index, name in enumerate(client_input.ACTIONS):
        if risen & (1 << index):
            count = rises[name] = rises.get(name, 0) + 1
            if count & (count - 1) == 0:
                log('      CLIENT INPUT action %s rose (count %d)' % (name, count))
    if bits & client_input.ACTION_BIT['Interact']:
        now = time.monotonic()
        if 'clock_epoch' in state:
            now = int((now - state['clock_epoch']) * 1000) / 1000.
        last = state.get('player_interact_at')
        if last is None or now - last > 1.0:
            count = state['interact_presses'] = state.get('interact_presses', 0) + 1
            if count & (count - 1) == 0:
                log('      CLIENT INPUT interact pressed (count %d)' % count)
        state['player_interact_at'] = now


def observe_client_input(message, state):
    """Track the player's live position from its own input stream (docs/25).

    Recorded unconditionally: it is a read of a message the client already
    sends, it changes nothing on the wire, and every AI behaviour needs it.
    An undecodable shape is counted and dropped, never guessed at.
    """
    if (message.delivery, message.entity_id) != (client_input.INPUT_DELIVERY,
                                                 PLAYER_CONNECTION_ID):
        return
    if not client_input.is_input(message):
        return
    if 'guide_hive' in state:
        observe_client_actions(message.data, state)
    try:
        position = client_input.latest_position(message.data)
    except ValueError:
        state['input_unknown_shape'] = state.get('input_unknown_shape', 0) + 1
        return
    state['player_position'] = position
    state['player_facing'] = client_input.parse_input_facings(message.data)[0]
    from dit_motion import transport_for_state
    transport = transport_for_state(state)
    if transport is not None:
        guarded(state, 'DIT observation', transport.observe,
                ('human', 'local-player'), position)
    if not state.get('player_position_logged'):
        state['player_position_logged'] = True
        log('      CLIENT INPUT decoded: player at (%.2f, %.2f, %.2f)' % position)


# The client's DamageFloaterComponent::EnableDamageFloaters (delivery 3,
# component 36, RPC 0, one packed bool; send site 0xdab8a4, struct 0x86d920).
# DamageFloaterAutonomousSystem sends it every tick while the player's
# floater setting differs from the replicated FloatersEnabled (docs/36).
FLOATER_SETTING_RESEND_TICKS = 10


def observe_floater_setting(message, state):
    """Record the player's damage-floater setting and schedule its echo.

    Opt-in with the damage numbers (CRUCIBLE_SOLO_DAMAGE_NUMBERS=1).
    """
    import feedback_rings
    if ((message.delivery, message.entity_id, message.component, message.rpc)
            != (3, PLAYER_CHARACTER_ID, 36, 0) or len(message.data) != 1
            or not feedback_rings.damage_numbers_enabled()):
        return
    enabled = bool(message.data[0] & 1)
    if state.get('floaters_enabled', True) != enabled or 'floaters_enabled' not in state:
        log('      SOLO FEEDBACK client asks for damage floaters %s'
            % ('on' if enabled else 'off'))
    state['floaters_enabled'] = enabled
    state['floaters_enabled_resend'] = FLOATER_SETTING_RESEND_TICKS


def observe_client_rpcs(region, state):
    """Record valid requests from this peer's activated player once.

    This does not equate AcknowledgeControlledEntity with a spawned hero:
    OnActivate calls that RPC even before a controlled entity exists.
    """
    if not state.get('bootstrap_sent'):
        return
    reliable = bool(region[10] & 1)
    offset = 13 if reliable else 11
    try:
        messages = parse_entity_rpcs(region[offset:])
    except ValueError:
        state['invalid_rpc_packets'] = state.get('invalid_rpc_packets', 0) + 1
        return
    sequence = int.from_bytes(region[11:13] if reliable else region[2:4], 'big')
    key = 'rpc_reliable' if reliable else 'rpc_unreliable'
    previous = state.get(key)
    window = state.get(key + '_window', 0)
    if previous is None:
        state[key], state[key + '_window'] = sequence, 1
    else:
        ahead = (sequence - previous) & 0xffff
        if 0 < ahead < 0x8000:
            state[key] = sequence
            state[key + '_window'] = 1 if ahead >= 32 else ((window << ahead) | 1) & 0xffffffff
        else:
            behind = (previous - sequence) & 0xffff
            if behind >= 32 or window & (1 << behind):
                return
            state[key + '_window'] = window | (1 << behind)
    counts = state.setdefault('client_rpc_counts', {})
    for message in messages:
        if 'combat' in state:
            guarded(state, 'combat report', observe_solo_combat, message, state)
        else:
            observe_practice_damage(message, state)
        observe_client_input(message, state)
        observe_floater_setting(message, state)
        if message.entity_id != PLAYER_CONNECTION_ID:
            continue
        if (message.delivery == 3 and message.component == 169
                and message.rpc == 1 and message.data
                and message.data[0] == len(message.data) - 1
                and message.data[0] <= 127 and b'\0' not in message.data[1:]):
            state['local_player_name'] = message.data[1:]
        if (message.delivery == 3 and message.component == 169
                and message.rpc == 2 and not message.data):
            state['local_player_activation_received'] = True
        key = (message.component, message.rpc)
        counts[key] = counts.get(key, 0) + 1
        if counts[key] == 1:
            log('      CLIENT RPC component=%d rpc=%d size=%d' %
                (message.component, message.rpc, len(message.data)))


def parse_shard(data):
    """Decode the retail client's raw LZ4 block and validate its CRC.

    Captures beginning 51, 53 and f0 are LZ4 tokens, not transport ids.
    The receive path uses a 4096-byte decompression buffer (0x4f1ead7).
    Unlike server-to-client datagrams, captured client sends have no BPT.
    Return a logical unencrypted prefix for reply builders.
    """
    try:
        raw = lz4.block.decompress(data, uncompressed_size=4096)
    except lz4.block.LZ4BlockError:
        return None
    if len(raw) < 15:
        return None
    region, crc = raw[:-4], raw[-4:]
    if zlib.crc32(region) != int.from_bytes(crc, "big"):
        return None
    if region[10] & 1 and len(region) < 13:
        return None
    return bytes([UNENCRYPTED]), region, crc


# The client reader consumes a BasePacketType byte BEFORE LZ4 decoding.
# Captured client sends omit this byte. The asymmetry is observed, not a
# reason to treat their LZ4 tokens as transport identifiers.
def shard_bpt():
    """The BasePacketType byte to put in front of every reply."""
    return bytes([int(os.environ.get("CRUCIBLE_SHARD_BPT", "0"), 0) & 0xFF])


def build_shard(prefix, packet_type, local_seq, remote_seq, payload=b"",
                seq_window=0, reliable=0):
    """BasePacketType plus a raw LZ4 block containing packet and CRC32.

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
    return prefix + lz4.block.compress(
        region + crc.to_bytes(4, "big"), store_size=False)


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

    def full_forge(local, remote, reliable=0, pad=0x00, ptype=6):
        edits = {
            plen + 0: (ptype >> 8) & 0xFF, plen + 1: ptype & 0xFF,
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
        # These two were built above and then NEVER RETURNED - a dead-variable
        # bug found 2026-09-18, after all twelve shapes above had been walked
        # and refused. They matter more than any of them: every shape above
        # carries a trailer we DERIVED, and this file's own docstring admits
        # the 51-channel derivation is under-determined. These carry a trailer
        # that is correct by construction - the client's own Connect with the
        # header rewritten into an Accept and the CRC repaired by linearity,
        # which needs no knowledge of where the checksummed region starts.
        # Cost of the trick: length must not change, so the Accept is padded
        # to 73 bytes with 56 zero bytes after ServerShardId.
        ("9 FORGED from the client's own Connect, trailer valid by linearity",
         forged_full),
        ("10 FORGED, IsReliable set",
         forged_full_reliable),
        # CORE PACKETS. UdpBaseConnection::HandleCorePacket dispatches an enum
        # that sits UNDER the NovaGameClientPackets ids (5..12):
        #   ECorePackets_InitiateConnection / TerminateConnection
        #   / Heartbeat / Fragment
        # Two of the four are pinned by observation: the client sends type 2
        # about twice a second (Heartbeat) and type 1 exactly once, immediately
        # before teardown (TerminateConnection). It has NEVER sent type 0 or 3.
        #
        # The client times out with ConnectTimeout, which is a TRANSPORT-level
        # failure - the UDP connection never came up - so a game-level Accept
        # (type 6) may simply be the wrong layer to answer on. If the transport
        # is waiting for InitiateConnection, none of the shapes above can ever
        # satisfy it no matter how correct their framing.
        #
        # Forged, so the trailer is valid by construction rather than derived.
        ("11 FORGED core packet type 0 (InitiateConnection?)",
         full_forge(our_seq, their_seq, ptype=0)),
        ("12 FORGED core packet type 3 (the other unobserved id)",
         full_forge(our_seq, their_seq, ptype=3)),
        ("13 FORGED core packet type 2 (Heartbeat) on the 51 channel",
         full_forge(our_seq, their_seq, ptype=2)),
        # The three BasePacketType values ReadDataFromSocket actually accepts.
        # Everything above this line was discarded on its first byte.
        ("14 BasePacketType 0x00 + Accept",
         build_shard(b"\x00", 6, our_seq, their_seq, sid)),
        ("15 BasePacketType 0xCC + Accept",
         build_shard(b"\xcc", 6, our_seq, their_seq, sid)),
        ("16 BasePacketType 0xFF + Accept",
         build_shard(b"\xff", 6, our_seq, their_seq, sid)),
        # POSITIVE CONTROL, not a candidate. Identical to variant 14 but with
        # the Crc32 deliberately corrupted. If the client logs "Crc32
        # mismatch, discarding packet" then our datagrams are reaching
        # UdpBaseConnection::ProcessReceived and the framing is right; if it
        # logs nothing, they are still dying somewhere earlier and silently.
        ("17 CONTROL: BasePacketType 0x00, deliberately BAD Crc32",
         build_shard(b"\x00", 6, our_seq, their_seq, sid)[:-4] + b"\xde\xad\xbe\xef"),
    ]


PROBE_COUNTER = os.path.join(HERE, ".shard-probe-counter")


def _next_probe_index():
    """Index for the next CONNECTION, persisted across stub restarts.

    Persisting matters because a connection only lives ~4 s and the client
    only gets ~3 real attempts per launch (docs/14), so walking a 12-entry
    variant list takes several runs.
    """
    try:
        n = int((open(PROBE_COUNTER).read().strip() or "0"))
    except Exception:
        n = 0
    try:
        with open(PROBE_COUNTER, "w") as f:
            f.write(str(n + 1))
    except Exception:
        pass
    return n


def new_connection_state():
    """Fresh per-connection state.

    Everything here used to live in ONE dict shared by every peer, which
    broke the stub in two ways (both found 2026-09-18, see docs/14):

    - `seq` is our LocalSequence. Global, it never reset, so the first Accept
      of a brand-new connection claimed LocalSequence 625 while the client's
      own Connect carried 1. The base header has an explicit SequenceWindow
      field, so a reply ~600 ahead of the window is a plausible silent drop.
    - `variant` advanced per DATAGRAM, so a single connection was answered
      with a different Accept shape each time its Connect was resent, and no
      shape was ever tested over a whole connection lifetime.

    The variant is now chosen ONCE per connection and held. Set
    CRUCIBLE_SHARD_VARIANT=N to pin a specific one instead of walking.
    """
    st = {}
    if os.environ.get("CRUCIBLE_SHARD_PROBE") == "1":
        override = os.environ.get("CRUCIBLE_SHARD_VARIANT")
        st["variant"] = int(override) if override else _next_probe_index()
    return st


def shard_reply(data, state):
    """Answer one datagram, or None.

    - PacketType 2: a transport-level control packet, ~2/second, one payload
      byte. Mirrored with RemoteSequence set to the sequence being acked.
    - PacketType 5 (Connect): answered with Accept (id 6). With
      CRUCIBLE_SHARD_PROBE=1 the shape is fixed for the whole connection.

    `state` MUST be per-connection (see new_connection_state).
    """
    parsed = parse_shard(data)
    if parsed is None:
        return None
    prefix, region, _crc = parsed
    ptype = int.from_bytes(region[0:2], "big")
    their_seq = int.from_bytes(region[2:4], "big")
    previous = state.get("received_seq")
    if previous is None:
        state["received_seq"], state["received_window"] = their_seq, 1
    else:
        ahead = (their_seq - previous) & 0xFFFF
        if 0 < ahead < 0x8000:
            state["received_window"] = (1 if ahead >= 32 else
                ((state["received_window"] << ahead) | 1) & 0xFFFFFFFF)
            state["received_seq"] = their_seq
        else:
            behind = (previous - their_seq) & 0xFFFF
            if behind < 32:
                state["received_window"] |= 1 << behind
    state["seq"] = (state.get("seq", 0) + 1) & 0xFFFF
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
        # Reply with a VALID BasePacketType, not the client's 0xf0 echo.
        return build_shard(shard_bpt(), 2, state["seq"], state["received_seq"],
                           payload=region[11:12] or b"\x00",
                           seq_window=state["received_window"])
    if ptype == 11 and 'accept' in state:
        # Verified NovaGameServerPackets::EntityRpcs (getter 1a97350).
        # Keep ACKs flowing when RPC traffic replaces transport heartbeats.
        # The transport ACK confirms receipt, not gameplay completion.
        observe_client_rpcs(region, state)
        return build_shard(shard_bpt(), 2, state['seq'], state['received_seq'],
                           payload=b'\x00', seq_window=state['received_window'])
    # No unsolicited spawn on client packet ids: the earlier 6..12 trigger
    # incorrectly reused the SERVER-to-client dispatcher table. Spawning
    # requires a verified slice entry and component payload, not a role sweep.

    if ptype == 5:
        shard_id = int(os.environ.get("CRUCIBLE_SHARD_ID", "0"))
        if os.environ.get("CRUCIBLE_SHARD_PROBE") == "1":
            variants = accept_variants(prefix, region, their_seq,
                                       state["seq"], shard_id, data)
            # Pinned for the life of this connection - do NOT advance here.
            i = state.get("variant", 0) % len(variants)
            name, out = variants[i]
            if not state.get("variant_logged"):
                state["variant_logged"] = True
                log("      ACCEPT PROBE pinned for this connection: "
                    "index %d of %d, %s" % (i, len(variants), name))
            log("      ACCEPT PROBE variant %s (%d bytes)" % (name, len(out)))
            return out
        # Retransmit the SAME packet id: the client asserts if a second
        # Accept is dispatched after it has transitioned to Connected.
        if "accept" not in state:
            state["accept"] = build_shard(
                shard_bpt(), 6, state["seq"], their_seq,
                payload=bytes([shard_id & 0xFF]),
                seq_window=state["received_window"])
        return state["accept"]
    return None


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    log("listening on %s:%d (UDP, log: %s)" % (HOST, PORT, LOG))
    log("expect: NovaGameClientPackets after the hub hands out this address")

    # Parsing the 43,130-triangle navmesh costs ~1.2 s. Pay it before any
    # client connects: on demand it lands inside the population reply, which
    # is mid-handshake and would stall the clock ticks the client is waiting
    # on. Failure here must not take the shard down - the bots simply stay on
    # whatever path they can build.
    if solo_ai_enabled() and solo_modes.selected().bot_positions:
        try:
            start = time.monotonic()
            import hunter_patrol
            hunter_patrol.circuits(solo_modes.selected().level)
            log("prewarmed hunter navigation in %.2fs" % (time.monotonic() - start))
        except Exception as error:      # noqa: BLE001 - diagnostic only
            log("hunter navigation prewarm FAILED: %r" % (error,))
    # The same for line of sight: about 0.3 s for April2018's terrain, and
    # 3 s more for its static geometry.
    if solo_ai_enabled() and os.environ.get('CRUCIBLE_SOLO_LOS') in ('terrain', 'static'):
        start = time.monotonic()
        try:
            if level_sight() is not None:
                log("prewarmed line of sight (%s) in %.2fs"
                    % (os.environ['CRUCIBLE_SOLO_LOS'], time.monotonic() - start))
        except Exception as error:      # noqa: BLE001 - diagnostic only
            log("line-of-sight prewarm FAILED: %r" % (error,))

    reply = os.environ.get("CRUCIBLE_SHARD_REPLY", "").strip() or None
    if reply:
        log("reply mode: %s" % reply)

    if os.environ.get('CRUCIBLE_SHARD_BOOTSTRAP') == 'practice-character':
        # A custom tick also wakes twice per tick when no client packet
        # arrives; the accepted default keeps its original 50 ms wake.
        sock.settimeout(0.05 if not os.environ.get('CRUCIBLE_SHARD_TICK_MS')
                        else clock_interval() / 2)

    total = 0
    # Per-connection state, keyed on the client's source port. A shard
    # connection lives ~4 s and the client opens a fresh source port for each
    # attempt, so the peer address is the connection identity. Sharing one
    # dict across peers is what made our LocalSequence start at 625 on a
    # brand-new connection (docs/14).
    states = {}
    idle_reset = float(os.environ.get("CRUCIBLE_SHARD_PEER_IDLE", "10"))
    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                if reply == 'accept':
                    send_game_clocks(sock, states, idle_reset=idle_reset)
                continue
            total += len(data)
            now = time.time()
            st = states.get(addr)
            if st is not None and now - st.get("last", now) > idle_reset:
                log("--- %s idle > %.0fs, treating as a NEW connection ---"
                    % (addr, idle_reset))
                st = None
            if st is None:
                st = new_connection_state()
                st['peer_ip'] = addr[0]
                states[addr] = st
                log("--- first datagram from %s ---" % (addr,))
                if "variant" in st:
                    log("      (connection pinned to probe index %d)"
                        % st["variant"])
            st["last"] = now
            if parse_shard(data) is not None:
                st["last_valid_monotonic"] = time.monotonic()
            log("  <- %d bytes from %s (total %d)" % (len(data), addr, total))
            log("      %s" % describe(data))
            log("      hex: %s" % data[:1024].hex())
            log("      asc: %r" % (data[:256],))
            if reply == "echo":
                sock.sendto(data, addr)
                log("      reply(echo) -> %d bytes" % len(data))
            elif reply == "accept":
                out = shard_reply(data, st)
                if out is not None:
                    sock.sendto(out, addr)
                    log("      reply(accept) -> %s" % out.hex())
                bootstrap = bootstrap_reply(data, st)
                if bootstrap is not None:
                    sock.sendto(bootstrap, addr)
                    log("      BOOTSTRAP PROBE %s -> %s" %
                        (os.environ.get('CRUCIBLE_SHARD_BOOTSTRAP'), bootstrap.hex()))
                match_store = match_store_reply(st)
                if match_store is not None:
                    sock.sendto(match_store, addr)
                    log('      MATCH STORE PROBE -> %s' % match_store.hex())
                phase = solo_phase_reply(st)
                if phase is not None:
                    sock.sendto(phase, addr)
                    log('      SOLO PHASE PROBE GamePlay -> %s' % phase.hex())
                character = character_spawn_reply(st)
                if character is not None:
                    sock.sendto(character, addr)
                    log('      CHARACTER SPAWN PROBE %s -> %s' % (hero_loadout.selected().display, character.hex()))
                links = character_links_reply(data, st)
                if links is not None:
                    sock.sendto(links, addr)
                    log('      CHARACTER LINKS %s slot 1 -> %s' % (hero_loadout.selected().display, links.hex()))
                npcs = practice_npcs_reply(st)
                if npcs is not None:
                    sock.sendto(npcs, addr)
                    heroes = [slot.hero for slot in solo_bot_slots()]
                    log('      SOLO POPULATION PROBE: %s' %
                        ('three Marine hunters' if heroes == ['marine'] * 3
                         else 'hunters: %s' % ', '.join(heroes) if heroes
                         else 'two spitters, eight targets'))
                send_game_clocks(sock, states, idle_reset=idle_reset)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
