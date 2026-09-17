#!/usr/bin/env python3
"""Checks for the NovaGameHub RPC envelope (server/match_stub.py, hubrpc mode).

The only ground truth we have is the single Connect::Request frame captured on
2026-09-16 (snapshots/match-server-first-contact-20260916/match_stub.log). The
first test pins the parse against it byte for byte; the rest pin the shapes
that were read out of the executable, so a later edit cannot silently change
them.

Run: python3 server/test_hubrpc.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match_stub as ms  # noqa: E402

# Captured verbatim; the client log for the same run (20:26:39.694 GMT) reads
# "User 172966378(701223293) is connecting to the NovaGameHub", and
# 701223293 == 0x29cbd17d == the u32 at payload offset 0.
CONNECT = bytes.fromhex("0005000d010005000000010129cbd17d00")

failures = []


def check(name, got, want):
    if got != want:
        failures.append("%s: got %r, want %r" % (name, got, want))


def main():
    frame, rest = ms.split_frame(CONNECT)
    check("split consumes the whole frame", (frame, rest), (CONNECT, b""))

    msg_id, req_id, wants, payload = ms.parse_hub(CONNECT)
    check("msg id", msg_id, ms.HUB_CONNECT_REQ)
    check("request id", req_id, 1)
    check("wants response", wants, 1)
    check("payload", payload, bytes.fromhex("29cbd17d00"))
    check("UserId", int.from_bytes(payload[:4], "big"), 701223293)
    check("GameLiftSessionId is empty", payload[4:], b"\x00")

    # A frame we build must parse back to what we put in, and its length field
    # must cover the body exactly — the client rejects anything else.
    built = ms.build_hub(ms.HUB_CONNECT_OK, 1, b"\x00\x00\x00\x00", 0)
    check("built length field",
          int.from_bytes(built[2:4], "big"), len(built) - 4)
    check("round trip", ms.parse_hub(built),
          (ms.HUB_CONNECT_OK, 1, 0, b"\x00\x00\x00\x00"))

    # Connect::Success with the defaults: Result(1) + two empty strings(1+1)
    # + Session(u8 shard 1 + empty host 1 + u16 port 2) + bool(1) = 8.
    for key in ("CRUCIBLE_HUB_LEVEL", "CRUCIBLE_HUB_RULES",
                "CRUCIBLE_HUB_SHARD_HOST", "CRUCIBLE_HUB_SHARD_PORT",
                "CRUCIBLE_HUB_SHARD_ID", "CRUCIBLE_HUB_DEBUGCOMPS"):
        os.environ.pop(key, None)
    out = ms.hubrpc_reply(CONNECT)
    out_id, out_req, _out_wants, out_payload = ms.parse_hub(out)
    check("Connect answered with Connect::Success", out_id, ms.HUB_CONNECT_OK)
    check("response echoes the request id", out_req, 1)
    check("Connect::Success payload", out_payload,
          bytes.fromhex("00" "00" "00" "00" "00" "0000" "00"))

    # Range compression is what sizes a field, not the C++ type. EResult is a
    # 32-bit enum declared 0..13, so it is one byte; UserId is a full-range
    # uint32, so it is four, big endian.
    check("EResult is one byte", ms.ranged_uint(0, 0, 13), b"\x00")
    check("full-range u32 is four BE",
          ms.ranged_uint(701223293, 0, 0xFFFFFFFF), bytes.fromhex("29cbd17d"))
    check("u16 range is two BE", ms.ranged_uint(18877, 0, 65535),
          bytes.fromhex("49bd"))
    check("a value is stored relative to the range minimum",
          ms.ranged_uint(12, 10, 20), b"\x02")

    # Strings are u8 length then bytes, capped at the field's 127 capacity,
    # and are not NUL terminated on the wire.
    check("empty string", ms.hub_str(""), b"\x00")
    check("short string", ms.hub_str("ab"), b"\x02ab")
    try:
        ms.hub_str("x" * 128)
        failures.append("a 128-byte string should not fit a 127-byte field")
    except ValueError:
        pass

    check("Session", ms.hub_session(3, "127.0.0.1", 18878),
          b"\x03" + b"\x09127.0.0.1" + bytes.fromhex("49be"))

    # Heartbeat carries HasHandler 0 and is unanswered by default.
    hb = ms.build_hub(ms.HUB_HEARTBEAT_REQ, 7, b"\x29\xcb\xd1\x7d", 0)
    check("heartbeat silent by default", ms.hubrpc_reply(hb), None)
    os.environ["CRUCIBLE_HUB_HB"] = "reply"
    hb_id, _r, _w, hb_payload = ms.parse_hub(ms.hubrpc_reply(hb))
    check("heartbeat answered when asked", hb_id, ms.HUB_HEARTBEAT_OK)
    check("Heartbeat::Success is one Result byte", hb_payload, b"\x00")
    del os.environ["CRUCIBLE_HUB_HB"]

    # GetUserStatus::Success: Result(1) + UserStatus(u32 + 2 empty strings = 6)
    # + UserCount(4) + GameState(1) = 12.
    for key in ("CRUCIBLE_HUB_USERID", "CRUCIBLE_HUB_PLAYERNAME",
                "CRUCIBLE_HUB_USERCOUNT", "CRUCIBLE_HUB_GAMESTATE"):
        os.environ.pop(key, None)
    us = ms.build_hub(ms.HUB_USERSTATUS_REQ, 9, b"", 1)
    us_id, us_req, _w, us_payload = ms.parse_hub(ms.hubrpc_reply(us))
    check("user status answered", us_id, ms.HUB_USERSTATUS_OK)
    check("user status echoes request id", us_req, 9)
    check("GetUserStatus::Success payload", us_payload,
          bytes.fromhex("00" "00000000" "00" "00" "00000001" "01"))
    check("last byte is the GameState enum",
          us_payload[-1], ms.EGAMESTATE_IDLE)

    # GetServerShardAddress::Success: Result(1) + Session(1+1+2) + instance(1)
    # + ConnectionNonce(32) + PreSharedKey(32) = 70. Off unless asked for.
    sa = ms.build_hub(ms.HUB_SHARDADDR_REQ, 11, b"", 1)
    check("shard address silent by default", ms.hubrpc_reply(sa), None)
    os.environ["CRUCIBLE_HUB_SHARDADDR"] = "reply"
    os.environ["CRUCIBLE_HUB_SHARD_HOST"] = ""
    os.environ["CRUCIBLE_HUB_SHARD_PORT"] = "18878"
    sa_id, _r, _w, sa_payload = ms.parse_hub(ms.hubrpc_reply(sa))
    check("shard address answered when asked", sa_id, ms.HUB_SHARDADDR_OK)
    check("shard address payload length", len(sa_payload), 70)
    check("port is u16 BE at offset 3",
          sa_payload[3:5], bytes.fromhex("49be"))
    check("nonce and psk are 4 x u64 each",
          sa_payload[6:], b"\x00" * 64)
    for key in ("CRUCIBLE_HUB_SHARDADDR", "CRUCIBLE_HUB_SHARD_HOST",
                "CRUCIBLE_HUB_SHARD_PORT"):
        del os.environ[key]

    if failures:
        for f in failures:
            print("FAIL %s" % f)
        return 1
    print("ok: envelope, range compression, strings, and all four replies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
