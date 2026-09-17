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

    # Default Connect::Success: Result(4) + two empty strings + Session
    # (u8 shard, empty string host, u16 port) + bool = 4+1+1+1+1+2+1 = 11.
    for key in ("CRUCIBLE_HUB_LEVEL", "CRUCIBLE_HUB_RULES",
                "CRUCIBLE_HUB_SHARD_HOST", "CRUCIBLE_HUB_STR"):
        os.environ.pop(key, None)
    out = ms.hubrpc_reply(CONNECT)
    out_id, out_req, _out_wants, out_payload = ms.parse_hub(out)
    check("Connect answered with Connect::Success", out_id, ms.HUB_CONNECT_OK)
    check("response echoes the request id", out_req, 1)
    check("Connect::Success payload length", len(out_payload), 11)
    check("Connect::Success Result is Success",
          int.from_bytes(out_payload[:4], "big"), ms.ERESULT_SUCCESS)

    # Both string encodings agree on the empty string, which is why the first
    # run can avoid choosing between them.
    os.environ["CRUCIBLE_HUB_STR"] = "cstr"
    check("empty string is one byte either way", ms.hub_str(""), b"\x00")
    check("cstr is NUL terminated", ms.hub_str("ab"), b"ab\x00")
    os.environ["CRUCIBLE_HUB_STR"] = "len8"
    check("len8 is length prefixed", ms.hub_str("ab"), b"\x02ab")
    del os.environ["CRUCIBLE_HUB_STR"]

    # Heartbeat carries wants-response 0 and is unanswered by default.
    hb = ms.build_hub(ms.HUB_HEARTBEAT_REQ, 7, b"\x29\xcb\xd1\x7d", 0)
    check("heartbeat silent by default", ms.hubrpc_reply(hb), None)
    os.environ["CRUCIBLE_HUB_HB"] = "reply"
    check("heartbeat answered when asked",
          ms.parse_hub(ms.hubrpc_reply(hb))[0], ms.HUB_HEARTBEAT_OK)
    del os.environ["CRUCIBLE_HUB_HB"]

    us = ms.build_hub(ms.HUB_USERSTATUS_REQ, 9, b"", 1)
    us_id, us_req, _w, us_payload = ms.parse_hub(ms.hubrpc_reply(us))
    check("user status answered", us_id, ms.HUB_USERSTATUS_OK)
    check("user status echoes request id", us_req, 9)
    check("user status payload length", len(us_payload), 12)
    check("user status defaults to Idle",
          int.from_bytes(us_payload[8:12], "big"), ms.EGAMESTATE_IDLE)

    # GetServerShardAddress is deliberately unanswered: its Success payload is
    # not pinned down, and a wrong guess burns a game restart.
    sa = ms.build_hub(ms.HUB_SHARDADDR_REQ, 11, b"", 1)
    check("shard address left unanswered", ms.hubrpc_reply(sa), None)

    if failures:
        for f in failures:
            print("FAIL %s" % f)
        return 1
    print("ok: hub envelope, Connect::Success, Heartbeat, GetUserStatus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
