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

Reply modes
-----------
  CRUCIBLE_REPLY=hubrpc    speak the NovaGameHub RPC (docs/11) — the protocol
                           this port actually runs. Use this one.
  CRUCIBLE_REPLY=hub|accept|echo|ack
                           earlier attempts that treated the port as the
                           shard's NovaGameClientPackets stream. Kept only so
                           the old runs stay reproducible; all of them are
                           aimed at the wrong protocol (docs/10, "Superseded").
"""
import datetime
import os
import socket
import ssl
import struct
import subprocess
import threading
import time
import uuid

import local_sessions
import solo_modes

HOST = "127.0.0.1"
PORT = int(os.environ.get("CRUCIBLE_MATCH_PORT", "18877"))
HERE = os.path.dirname(os.path.abspath(__file__))
CADIR = os.path.join(HERE, ".crucible-test-ca")
CRT = os.path.join(CADIR, "match.crt")
KEY = os.path.join(CADIR, "match.key")
LOG = os.environ.get("CRUCIBLE_LOG") or os.path.join(HERE, "match_stub.log")
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
CONNECT_ID = 5
ACCEPT_ID = 6

# Transport layout, reverse-engineered 2026-09-16 from live runs plus the
# client log (see docs/10-match-server-tls.md "What the wire actually is"):
#   header: u16 BE packet id, u16 BE body length
#   body:   5 constant magic bytes 01 00 05 00 00, u16 BE counter, data bytes
# Revised model (run of 17:41, hub mode v1): the counter is a per-SENDER
# sequence number, and data[0] of server->client frames is a cumulative ack
# of the client sequence. Evidence: client heartbeats (id 14) always carry
# data 01 even at client counter 0x91 — exactly one server frame (our Accept,
# server seq 01) ever counted; our v1 echoes reused CLIENT counters (03, 04,
# ...) with stale ack 01, leaving a server-seq gap at 02, and every heartbeat
# timed out. So server frames need their OWN sequence (01, 02, 03, ...) and
# data[0] = the client counter being acked. A game response (Accept) is
# matched to its request the same way: ack byte == request counter.
#
# SUPERSEDED (2026-09-17, static RE): the "5 constant magic bytes" reading was
# generalised from the one Connect frame ever hex-dumped, and it is wrong. The
# stub is the NovaGameHub, whose wire is a plain request/response RPC — see the
# hubrpc section below and docs/11-novagamehub-protocol.md. MAGIC and the
# hub/echo/ack/accept modes are kept only so the old experiments stay
# reproducible; they target NovaGameClientPackets, which is the *shard*
# protocol, not this port.
MAGIC = b"\x01\x00\x05\x00\x00"

# ---------------------------------------------------------------------------
# NovaGameHub RPC (CRUCIBLE_REPLY=hubrpc)
# ---------------------------------------------------------------------------
# Recovered from Crucible.exe build 5685572, 2026-09-17 (docs/11):
#
#   frame:   u16 BE msg id | u16 BE body length | body
#   body:    u8 version(=1) | u16 BE 0x0005 (constant) | Metadata | payload
#   Metadata: u32 BE RequestId | u8 HasHandler       (rva 0x4f1c6c0)
#
# Field encodings are not "the C++ type": every integer is range-compressed by
# the reader at rva 0x4f57140 — see ranged_uint() below.
#
# Message ids come from NovaGameHubClient's response dispatcher
# (rva 0x3187fa7: `add eax,-6; cmp eax,0xd`, jump table at 0x3188c18), which
# handles 6..19 and leaves 8, 11, 14, 17 to the default case — i.e. those four
# are the ids the *client* sends, so the space is five (Request, Success,
# Error) triples starting at 5.
HUB_VERSION = 1
# Body offsets 1-2, constant 0x0005 on every frame the client sends, whatever
# the message. Purpose unknown (an interface or protocol id, most likely); the
# stub mirrors what the client sends rather than guessing something else.
HUB_BODY_TAG = b"\x00\x05"
# The caller's NovaNetUserId, learned from Connect/Heartbeat, for probes that
# want to echo it back.
HUB_LAST_UID = [0]

# Levels shipped in levels.pak: april2018, HotH_02, mainmenu, practicearena,
# tutorial_02, tutorial_03. practicearena is the smallest combat sandbox and
# the one docs/08 saw referenced as `+map practicearena`.
HUB_LEVELS = ("april2018", "HotH_02", "mainmenu", "practicearena",
              "tutorial_02", "tutorial_03")
HUB_DEFAULT_LEVEL = "practicearena"
# NovaNet::EGameRules has exactly two values, registered at rva 0x4c01ad4:
# BaseGameRules = 0, CombatGameRules = 1. The field is a string, so the client
# maps the name back to the enum.
HUB_GAMERULES = ("BaseGameRules", "CombatGameRules")
HUB_DEFAULT_RULES = "CombatGameRules"

HUB_CONNECT_REQ = 5
HUB_CONNECT_OK = 6
HUB_CONNECT_ERR = 7
HUB_HEARTBEAT_REQ = 8
HUB_HEARTBEAT_OK = 9
HUB_SHARDADDR_REQ = 11
HUB_SHARDADDR_OK = 12
HUB_SHARDADDR_ERR = 13
# Element counts of the two fixed-size vectors in
# GetServerShardAddress::Success, read off their serialiser loops.
HUB_NONCE_WORDS = 4   # rva 0x3187460: cmp edi, 4
HUB_PSK_WORDS = 8     # rva 0x3187780: cmp edi, 8
HUB_USERSTATUS_REQ = 14
HUB_USERSTATUS_OK = 15
HUB_METRICS_REQ = 17
HUB_METRICS_OK = 18

HUB_NAMES = {
    5: "Connect::Request", 6: "Connect::Success", 7: "Connect::Error",
    8: "Heartbeat::Request", 9: "Heartbeat::Success", 10: "Heartbeat::Error",
    11: "GetServerShardAddress::Request",
    12: "GetServerShardAddress::Success",
    13: "GetServerShardAddress::Error",
    14: "GetUserStatus::Request", 15: "GetUserStatus::Success",
    16: "GetUserStatus::Error",
    17: "GetServerMetrics::Request", 18: "GetServerMetrics::Success",
    19: "GetServerMetrics::Error",
}

# NovaNet::EResult, declaration order read off the enum-name table at
# rva 0x3185320. ERESULT_MAX is the declared upper bound from the field
# descriptor (0..13), which is what sizes the field on the wire.
ERESULT_SUCCESS = 0
ERESULT_MAX = 13
# NovaNet::EGameState, from rva 0x3185590: Connecting, Idle, Playing, Ended;
# declared range 0..4.
EGAMESTATE_CONNECTING = 0
EGAMESTATE_IDLE = 1
EGAMESTATE_PLAYING = 2
EGAMESTATE_ENDED = 3
EGAMESTATE_MAX = 4


def ranged_uint(value, lo, hi):
    """One integer field, encoded the way NovaNet's serialiser does it.

    Every integer goes through the ranged reader at rva 0x4f57140, which sizes
    the field from the range declared on it, not from the C++ type:

        hi - lo <= 0xFF         1 byte
        hi - lo <= 0xFFFF       2 bytes, big endian (it calls ntohs)
        hi - lo <= 0xFFFFFFFF   4 bytes, big endian
        otherwise               8 bytes, big endian

    and what goes on the wire is value - lo. This is why `Result` is one byte
    even though the field is a 32-bit enum: NovaNet::EResult is declared
    0..13. The ranges come out of each field's descriptor (the min/max pair at
    descriptor offset 0x10), so they are read, not assumed.
    """
    if not lo <= value <= hi:
        raise ValueError("%d outside the declared range %d..%d" % (value, lo, hi))
    span = hi - lo
    width = 1 if span <= 0xFF else 2 if span <= 0xFFFF else 4 if span <= 0xFFFFFFFF else 8
    return (value - lo).to_bytes(width, "big")


def hub_str(s):
    """One string field: u8 length, then the bytes. Not NUL terminated.

    The string reader (rva 0x4f56910) calls the same ranged integer reader for
    the length with lo=0 and hi=the field's capacity, and every String field
    here passes capacity 127 (`mov r8d, 0x7f` in the helper at 0x8552e0) — so
    the length is one byte and the payload caps at 127. The NUL the client
    writes afterwards goes into its own buffer, not onto the wire.
    """
    raw = s.encode("utf-8")
    if len(raw) > 127:
        raise ValueError("string longer than the 127-byte field capacity")
    return ranged_uint(len(raw), 0, 127) + raw


def hub_bool(value):
    """One bool field.

    Bools are bit-packed (rva 0x4f565d0): the first bool of a run reserves a
    byte and the next seven reuse it, and the bit cursor is NOT reset by the
    non-bool fields in between. Every message answered here has at most one
    bool, so a lone byte is correct — a second bool would have to go into bit
    1 of this same byte, not a new one.
    """
    return b"\x01" if value else b"\x00"


def hub_session(shard_id, host, port):
    """The `Session` struct (rva 0x3186f80), also used as `ServerShardAddr`.

    { u8 ShardId (0..255), str GameHost, u16 GamePort (0..65535) }
    """
    return (ranged_uint(shard_id, 0, 255)
            + hub_str(host)
            + ranged_uint(port, 0, 65535))


def parse_hub(frame):
    """Envelope fields of one hub frame, or None if it is too short.

    Returns (msg_id, request_id, has_handler, payload).

    The message id comes from the transport header ONLY. Body offsets 1-2 are
    a constant 0x0005 — proven on the wire 2026-09-17 15:08, where an id-14
    frame body reads 01 00 05 00 00 00 41 01 and an id-8 body reads
    01 00 05 00 00 00 42 00 38f1f744. Reading the id out of the body instead
    made every frame look like Connect::Request.
    """
    if len(frame) < 12:
        return None
    body = frame[4:]
    if len(body) < 8:
        return None
    return (
        int.from_bytes(frame[0:2], "big"),
        int.from_bytes(body[3:7], "big"),
        body[7],
        bytes(body[8:]),
    )


def build_hub(msg_id, request_id, payload, has_handler=1):
    body = (bytes([HUB_VERSION])
            + HUB_BODY_TAG
            + request_id.to_bytes(4, "big")
            + bytes([has_handler])
            + payload)
    return msg_id.to_bytes(2, "big") + len(body).to_bytes(2, "big") + body


def connect_success():
    """Connect::Success payload (serialiser rva 0x3185be0).

    { u8 Result, str LevelName, str GameRules,
      Session{u8 ShardId, str GameHost, u16 GamePort},
      bool ShouldLoadDebugComponents }
    """
    return (ranged_uint(ERESULT_SUCCESS, 0, ERESULT_MAX)
            + hub_str(os.environ.get("CRUCIBLE_HUB_LEVEL", solo_modes.selected().level))
            + hub_str(os.environ.get("CRUCIBLE_HUB_RULES", HUB_DEFAULT_RULES))
            + hub_session(int(os.environ.get("CRUCIBLE_HUB_SHARD_ID", "0")),
                          os.environ.get("CRUCIBLE_HUB_SHARD_HOST", ""),
                          int(os.environ.get("CRUCIBLE_HUB_SHARD_PORT", "0")))
            + hub_bool(os.environ.get("CRUCIBLE_HUB_DEBUGCOMPS") == "1"))


def user_status_success():
    """GetUserStatus::Success payload (serialiser rva 0x3186890).

    { u8 Result, UserStatus{u32 UserId, str PlayerName, str GameLiftSessionId},
      u32 UserCount, u8 GameState }
    """
    return (ranged_uint(ERESULT_SUCCESS, 0, ERESULT_MAX)
            + ranged_uint(int(os.environ.get("CRUCIBLE_HUB_USERID", "0")),
                          0, 0xFFFFFFFF)
            + hub_str(os.environ.get("CRUCIBLE_HUB_PLAYERNAME", ""))
            + hub_str("")
            + ranged_uint(int(os.environ.get("CRUCIBLE_HUB_USERCOUNT", "1")),
                          0, 0xFFFFFFFF)
            + ranged_uint(int(os.environ.get("CRUCIBLE_HUB_GAMESTATE",
                                             str(EGAMESTATE_IDLE))),
                          0, EGAMESTATE_MAX))


def shard_address_success():
    """GetServerShardAddress::Success payload (serialiser rva 0x3186460).

    { u8 Result, Session ServerShardAddr, str GameInstanceId,
      u64[4] ConnectionNonce, u64[8] PreSharedKey }

    The two vectors are NOT the same length: the element loop in the
    ConnectionNonce serialiser is `cmp edi, 4` (rva 0x3187460) and the one in
    PreSharedKey is `cmp edi, 8` (rva 0x3187780). Assuming both were 4 made
    every reply 32 bytes short, which is why a dozen otherwise-correct payload
    shapes were all dropped. Neither vector has a count prefix on the wire.

    With empty strings the payload is 1 + 4 + 1 + 32 + 64 = 102 bytes.
    """
    nonce = bytes.fromhex(os.environ.get("CRUCIBLE_HUB_NONCE",
                                         "00" * (8 * HUB_NONCE_WORDS)))
    psk = bytes.fromhex(os.environ.get("CRUCIBLE_HUB_PSK",
                                       "00" * (8 * HUB_PSK_WORDS)))
    if len(nonce) != 8 * HUB_NONCE_WORDS:
        raise ValueError("ConnectionNonce is %d x u64 = %d bytes"
                         % (HUB_NONCE_WORDS, 8 * HUB_NONCE_WORDS))
    if len(psk) != 8 * HUB_PSK_WORDS:
        raise ValueError("PreSharedKey is %d x u64 = %d bytes"
                         % (HUB_PSK_WORDS, 8 * HUB_PSK_WORDS))
    return (ranged_uint(ERESULT_SUCCESS, 0, ERESULT_MAX)
            + hub_session(int(os.environ.get("CRUCIBLE_HUB_SHARD_ID", "0")),
                          os.environ.get("CRUCIBLE_HUB_SHARD_HOST", "127.0.0.1"),
                          int(os.environ.get("CRUCIBLE_HUB_SHARD_PORT", "18878")))
            + hub_str(os.environ.get("CRUCIBLE_HUB_INSTANCE", ""))
            + nonce + psk)


# GetServerShardAddress::Success probe. Unlike GetUserStatus, the client asks
# for the shard address exactly ONCE per connection, so variants cannot be
# cycled within a run - the counter is persisted to disk and advances per
# connection, i.e. one variant per re-READY.
#
# Run 5 (2026-09-17 16:45) sent variant 0, the shape the disassembly implies,
# and the client timed out request 2 ("Failed shard request with result 1" -
# EResult 1 is Timeout, logged by the timeout path). So the frame was never
# matched: it is not a semantic rejection, the payload did not deserialise.
HUB_SHARD_PROBE_STATE = os.path.join(HERE, ".shard-probe-counter")


def shard_probe_variants(nonce, psk, shard, host, port, instance):
    """Length sweep, 2026-09-17 evening.

    Settled by experiment: a GetServerShardAddress::ERROR (id 13) carrying a
    single Result byte is accepted INSTANTLY - the client logged "Failed shard
    request with result 2" 0.3 s after we sent Result=InternalError(2), and
    that request id is absent from its timeout list. So the envelope, the
    header id, the request-id echo and the Result encoding are all correct,
    and the problem is confined to the id-12 Success payload.

    An all-zero byte string is a VALID encoding of that payload: Result=0,
    ShardId=0, GameHost len 0, GamePort 0, GameInstanceId len 0, and zeroed
    nonce/psk (both range checks in the u64 reader are unsigned, so 0 passes).
    The disassembly says that needs 70 bytes, and 70 was rejected. So this
    sweeps the LENGTH: if some longer all-zero payload is accepted, the real
    layout is bigger than the five fields imply and the accepted length says
    by how much.

    Returns (header_id, name, payload) triples.
    """
    return [
        (HUB_SHARDADDR_OK, "0 all zeros, 120 bytes", b"\x00" * 120),
        (HUB_SHARDADDR_OK, "1 all zeros, 250 bytes", b"\x00" * 250),
        (HUB_SHARDADDR_OK, "2 all zeros, 90 bytes", b"\x00" * 90),
        (HUB_SHARDADDR_OK, "3 all zeros, 71 bytes", b"\x00" * 71),
        (HUB_SHARDADDR_OK, "4 all zeros, 70 bytes (control, known to fail)",
         b"\x00" * 70),
        (HUB_SHARDADDR_OK, "5 all zeros, 500 bytes", b"\x00" * 500),
    ]


def shard_probe_index():
    """Read-and-advance the persisted per-connection variant counter."""
    try:
        with open(HUB_SHARD_PROBE_STATE) as f:
            i = int(f.read().strip() or "0")
    except (OSError, ValueError):
        i = 0
    try:
        with open(HUB_SHARD_PROBE_STATE, "w") as f:
            f.write(str(i + 1))
    except OSError:
        pass
    return i


def track_local_session(parsed, session):
    directory = os.environ.get('CRUCIBLE_LOCAL_SESSIONS')
    if not directory or session is None:
        return
    msg_id, _, _, payload = parsed
    if msg_id == HUB_CONNECT_REQ:
        local_sessions.remove(directory, session['key'])
        session['advertised'] = False
        session['user_id'] = int.from_bytes(payload[:4], 'big') if len(payload) >= 4 else 0
    elif msg_id == HUB_HEARTBEAT_REQ:
        if len(payload) != 4 or int.from_bytes(payload, 'big') != session.get('user_id'):
            local_sessions.remove(directory, session['key'])
            session['advertised'] = False
            session['user_id'] = 0
    elif (msg_id == HUB_SHARDADDR_REQ
          and os.environ.get('CRUCIBLE_HUB_SHARDADDR') == 'reply'
          and os.environ.get('CRUCIBLE_HUB_SHARD_PROBE') != '1'):
        session['advertised'] = True
    if session.get('advertised') and session.get('user_id'):
        local_sessions.publish(directory, session['key'], session['peer'], session['user_id'])


def finish_local_session(session):
    directory = os.environ.get('CRUCIBLE_LOCAL_SESSIONS')
    if not directory:
        return
    # Retail closes the hub immediately after switching to the shard. Keep
    # its advertised identity within the existing 60-second handoff window.
    # Do not refresh its timestamp on disconnect or extend expired sessions.
    if not session.get('advertised') or not session.get('user_id'):
        local_sessions.remove(directory, session['key'])


def hubrpc_reply(frame, session=None):
    """Response for one hub frame, or None to stay silent."""
    parsed = parse_hub(frame)
    if parsed is None:
        return None
    track_local_session(parsed, session)
    msg_id, req_id, _has_handler, _payload = parsed
    # HasHandler must be 1 on a RESPONSE too. Proven on the wire 2026-09-17
    # 15:40 by cycling 12 response-envelope variants across the client's 1 Hz
    # GetUserStatus poll: every request id answered with HasHandler=1 (13, 25,
    # 37, 49, 61, 73, 85) is absent from the client's "Timing out requestid N"
    # list, and every other variant timed out. Sending 0 is what made the
    # correct payloads look wrong.
    flag = int(os.environ.get("CRUCIBLE_HUB_RESPFLAG", "1"))
    if msg_id == HUB_CONNECT_REQ and len(_payload) >= 4:
        HUB_LAST_UID[0] = int.from_bytes(_payload[:4], "big")

    if msg_id == HUB_CONNECT_REQ:
        return build_hub(HUB_CONNECT_OK, req_id, connect_success(), flag)

    if msg_id == HUB_HEARTBEAT_REQ:
        if len(_payload) >= 4:
            HUB_LAST_UID[0] = int.from_bytes(_payload[:4], "big")
        # The client sets HasHandler 0 on these, so it is not waiting on a
        # response; answer only when asked to, to keep the first run's
        # variables down.
        if os.environ.get("CRUCIBLE_HUB_HB", "silent") != "reply":
            return None
        return build_hub(HUB_HEARTBEAT_OK, req_id,
                         ranged_uint(ERESULT_SUCCESS, 0, ERESULT_MAX), flag)

    if msg_id == HUB_USERSTATUS_REQ:
        if os.environ.get("CRUCIBLE_HUB_PROBE") == "1":
            return hub_probe_reply(req_id, HUB_LAST_UID[0])
        return build_hub(HUB_USERSTATUS_OK, req_id, user_status_success(), flag)

    if msg_id == HUB_SHARDADDR_REQ:
        # Off by default: it points the client at a shard that does not exist
        # yet, so on the first run it would replace a clean "connected" result
        # with a confusing shard-connect failure.
        if os.environ.get("CRUCIBLE_HUB_SHARDADDR", "silent") != "reply":
            return None
        if os.environ.get("CRUCIBLE_HUB_SHARD_PROBE") == "1":
            nonce = bytes.fromhex(os.environ.get("CRUCIBLE_HUB_NONCE", "00" * 32))
            psk = bytes.fromhex(os.environ.get("CRUCIBLE_HUB_PSK", "00" * 32))
            variants = shard_probe_variants(
                nonce, psk,
                int(os.environ.get("CRUCIBLE_HUB_SHARD_ID", "0")),
                os.environ.get("CRUCIBLE_HUB_SHARD_HOST", "127.0.0.1"),
                int(os.environ.get("CRUCIBLE_HUB_SHARD_PORT", "18878")),
                os.environ.get("CRUCIBLE_HUB_INSTANCE", ""))
            i = shard_probe_index() % len(variants)
            hdr, name, payload = variants[i]
            log("      SHARD PROBE variant %s -> id %d, payload %d bytes"
                % (name, hdr, len(payload)))
            return build_hub(hdr, req_id, payload, flag)
        return build_hub(HUB_SHARDADDR_OK, req_id, shard_address_success(), flag)

    return None


def split_frame(buf):
    """Split one frame off buf. Returns (frame, rest) or (None, buf)."""
    if len(buf) < 4:
        return None, buf
    paylen = int.from_bytes(buf[2:4], "big")
    if len(buf) < 4 + paylen:
        return None, buf
    return bytes(buf[:4 + paylen]), bytes(buf[4 + paylen:])


def frame_counter(frame):
    """The u16 BE counter at body offset 5 (frame offset 9)."""
    if len(frame) < 11:
        return None
    return int.from_bytes(frame[9:11], "big")


def build_frame(pid, counter, data):
    """One frame with explicit counter and data bytes."""
    body = MAGIC + counter.to_bytes(2, "big") + data
    return pid.to_bytes(2, "big") + len(body).to_bytes(2, "big") + body


def hub_reply(frame, srv_seq):
    """Per-frame response for CRUCIBLE_REPLY=hub.

    srv_seq is OUR next server sequence number (caller increments per send).
    Every response acks the inbound frame: data[0] = inbound counter & 0xff.

    - id 5 (Connect) -> id 6 (Accept), acking counter 01. Game payload comes
      from CRUCIBLE_ACCEPT_DATA: hex bytes, or "auto" for a verbatim echo of
      the Connect's own data (id flipped, data untouched — never tested; the
      old accept mode always cleared data[0]). Default "0100". One shot per
      run: the client sends Connect once.
    - id 14 (heartbeat) -> id 14 with 1-byte data = ack, iff
      CRUCIBLE_HB=ack (default). Tests the cumulative-ack theory: timeouts
      should stop. CRUCIBLE_HB=echo keeps the old (poisoned, gap-creating)
      verbatim echo for comparison; anything else is silent.
    - id 8 (cvars) -> silent by default. CRUCIBLE_CVARS=echo to probe it.
    """
    if len(frame) < 4:
        return None
    pid = int.from_bytes(frame[0:2], "big")
    ctr = frame_counter(frame)
    if ctr is None:
        return None
    ack = ctr & 0xFF
    if pid == CONNECT_ID:
        spec = os.environ.get("CRUCIBLE_ACCEPT_DATA", "0100")
        if spec == "auto":
            data = frame[11:]  # verbatim game bytes, ack byte included (01)
        elif spec == "auto2":
            # userid-preserving Accept: flag 00 + inbound userid + status 01.
            # Rationale (docs/10): base wire looks like 5 bytes (flag + userid;
            # client cvars send exactly that with no type fields), so the
            # Accept's uint8 must follow the userid. `00 uid 00` (run A) and
            # `01 uid 00` (run 2) both failed; value 01 is the untried half.
            data = b"\x00" + bytes(frame[12:16]) + b"\x01"
        else:
            data = bytes.fromhex(spec)
        return build_frame(ACCEPT_ID, srv_seq, data)
    if pid == 14:
        mode = os.environ.get("CRUCIBLE_HB", "ack")
        if mode == "echo":
            return frame
        if mode == "ack":
            return build_frame(14, srv_seq, bytes([ack]))
        return None
    if pid == 8:
        if os.environ.get("CRUCIBLE_CVARS", "silent") == "echo":
            return frame
        return None
    return None


def make_reply(data, mode):
    """Build a reply for one received frame, or None to stay silent.

    Observed frames (header is 4 bytes: u16 id, u16 payload length):

      id 14, 12B:  01000500 0000 <ctr> 01
      id  8, 16B:  01000500 0000 <ctr> 00 <uid4>
      id  5, 17B:  01000500 0000 01    01 <uid4> 00

    i.e. a constant 6-byte prefix, a 1-byte counter, then a 1-byte marker at
    payload offset 7 (frame offset 11), then optional data. The marker is 0x01
    on every frame the client later reports as a pending request ('Timing out
    requestid N') and 0x00 on the id-8 frame.

    Modes:
      echo   - send the frame back unchanged
      ack    - same frame with the marker cleared to 0x00
      accept - answer a Connect (id 5) with an Accept (id 6)

    Why 'accept' exists: echoing the Connect back (what 'ack' does) did NOT
    stop the timeouts - 101 of them over ~2 minutes. The client is not waiting
    for its own packet back, it is waiting for the server's reply. The id space
    says which reply: the executable carries exactly eight EPackets_ names
    (Connect, Accept, ClientMigration, SyncConnectionCvars, SyncConsole,
    ServerConsoleCommand, EntityUpdates, EntityRpcs) and the dispatcher has
    exactly eight cases, ids 5..12. The client opens with 5, so 5 is Connect
    and 6 is Accept. See docs/10-match-server-tls.md for the caveat on ordering.

    NOTE the marker is NOT the last byte. On the id-8 frame the last four bytes
    are the uid, so clearing the trailing byte would corrupt it.
    """
    if mode == "echo":
        return data
    if mode == "ack":
        if len(data) <= FLAG_OFFSET:
            return None
        return data[:FLAG_OFFSET] + b"\x00" + data[FLAG_OFFSET + 1:]
    if mode == "accept":
        if len(data) < 4 or int.from_bytes(data[0:2], "big") != CONNECT_ID:
            return None
        out = bytearray(data)
        out[0:2] = ACCEPT_ID.to_bytes(2, "big")
        if len(out) > FLAG_OFFSET:
            out[FLAG_OFFSET] = 0x00
        return bytes(out)
    return None


def drain(tls, addr, label, reply=None, srv_seq=None, session=None):
    """Read until the peer closes, logging every chunk. The whole point: we do
    not know the framing, so we record bytes rather than parse them.

    srv_seq is a one-element list holding our next server sequence number
    (hub mode); sequences are per-connection."""
    total = 0
    pending = b""
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
        if reply in ("hub", "hubrpc"):
            # Frame-aware: split by header length so coalesced recvs still
            # get one response per frame, each with its own server seq.
            pending += data
            while True:
                frame, pending = split_frame(pending)
                if frame is None:
                    break
                pid = int.from_bytes(frame[0:2], "big")
                if reply == "hubrpc":
                    log_hub_frame(frame)
                    out = hubrpc_reply(frame, session)
                    if out is not None:
                        try:
                            tls.sendall(out)
                            log("      reply(hubrpc) -> %s" % out.hex())
                        except (ssl.SSLError, OSError) as e:
                            log("      reply(hubrpc) failed: %r" % (e,))
                            return total
                    continue
                log("      frame id %-2d ctr %s datalen %d" % (
                    pid, frame_counter(frame),
                    max(0, len(frame) - 11)))
                out = hub_reply(frame, srv_seq[0])
                if out is not None:
                    srv_seq[0] += 1
                    try:
                        tls.sendall(out)
                        log("      reply(hub srv %d) -> %s"
                            % (srv_seq[0] - 1, out.hex()))
                    except (ssl.SSLError, OSError) as e:
                        log("      reply(hub) failed: %r" % (e,))
                        return total
        elif reply:
            out = make_reply(data, reply)
            if out is not None:
                try:
                    tls.sendall(out)
                    log("      reply(%s) -> %s" % (reply, out.hex()))
                except (ssl.SSLError, OSError) as e:
                    log("      reply(%s) failed: %r" % (reply, e))
                    return total


def log_hub_frame(frame):
    """One log line naming the hub message and its envelope fields."""
    parsed = parse_hub(frame)
    if parsed is None:
        log("      frame (not a hub envelope, %d bytes): %s"
            % (len(frame), frame.hex()))
        return
    msg_id, req_id, wants, payload = parsed
    log("      hub %-30s id %-2d req %-5d wants_resp %d payload %s"
        % (HUB_NAMES.get(msg_id, "UNKNOWN"), msg_id, req_id, wants,
           payload.hex() or "-"))


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
    session = {'key': uuid.uuid4().hex, 'peer': addr[0]}
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
            srv_seq = [1]  # our server sequence for this connection (hub)
            if reply:
                log("  reply mode: %s" % reply)
                if reply == "hubrpc":
                    frame, _ = split_frame(first)
                    out = None
                    if frame is not None:
                        log_hub_frame(frame)
                        out = hubrpc_reply(frame, session)
                elif reply == "hub":
                    frame, _ = split_frame(first)
                    out = hub_reply(frame, srv_seq[0]) \
                        if frame is not None else None
                    if out is not None:
                        srv_seq[0] += 1
                else:
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
            drain(tls, addr, "after-probe", reply=reply,
                  srv_seq=srv_seq if reply in ("hub", "hubrpc") else None,
                  session=session)
        finally:
            try:
                tls.close()
            except OSError:
                pass
    finally:
        finish_local_session(session)
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
