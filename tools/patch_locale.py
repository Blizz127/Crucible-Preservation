#!/usr/bin/env python3
"""Add missing locale strings to ui/dist/ui.loc2.js (en-US).

L1 - matchmaking.error.failed_to_set_loadout.{title,lead,body}: the client
     can emit FAILED_TO_SET_LOADOUT but the shipped locale has no strings
     for it (raw keys render instead). Text mirrors the sibling
     cannot_set_loadout entries.

Usage: python3 patch_locale.py [--pak PATH] [--out /tmp/ui.loc2.patched.js]
"""
import argparse
import os
import struct
import zlib

PAK = os.environ.get(
    "CRUCIBLE_PAK",
    os.path.expanduser("~/.local/share/Steam/steamapps/common/"
                       "Crucible/gamedata.pak"))
ENTRY = "ui/dist/ui.loc2.js"

ADDITIONS = {
    "matchmaking.error.failed_to_set_loadout.title": "FAILED TO SET LOADOUT",
    "matchmaking.error.failed_to_set_loadout.lead":
        "Setting your loadout for matchmaking failed.",
    "matchmaking.error.failed_to_set_loadout.body":
        "Here\\'s the error code:",
    "matchmaking.error.success.title": "CONNECTION FAILED",
    "matchmaking.error.success.lead":
        "The connection to the game server failed.",
    "matchmaking.error.success.body":
        "Here\\'s the error code:",
}


def read_entry(pak_path, name):
    d = open(pak_path, "rb").read()
    eocd = d.rfind(b"PK\x05\x06")
    n = struct.unpack("<H", d[eocd + 10:eocd + 12])[0]
    off = struct.unpack("<I", d[eocd + 16:eocd + 20])[0]
    o = off
    for _ in range(n):
        f = struct.unpack("<HHHHHHIIIHHHHHII", d[o + 4:o + 46])
        fnlen, xtra, cmnt = f[9], f[10], f[11]
        ename = d[o + 46:o + 46 + fnlen].decode()
        if ename == name:
            cs, us, method, lho = f[7], f[8], f[3], f[15]
            lfn, lxtra = struct.unpack("<HH", d[lho + 26:lho + 30])
            raw = d[lho + 30 + lfn + lxtra:lho + 30 + lfn + lxtra + cs]
            data = zlib.decompress(raw, -15) if method == 8 else raw
            assert len(data) == us
            return data
        o += 46 + fnlen + xtra + cmnt
    raise KeyError(name)


def patch(data):
    s = data.decode("utf-8")
    missing = {k: v for k, v in ADDITIONS.items() if k not in s}
    if not missing:
        print("locale additions already present")
        return data
    tail = "}');export default e;"
    assert s.count(tail) == 1
    blob = ",".join('"%s":"%s"' % (k, v) for k, v in missing.items())
    s = s.replace("}');export default e;", "," + blob + "}');export default e;")
    # validate: emulate JS single-quote unescaping, then parse the JSON
    import json
    import re as _re

    def _js_unescape(text):
        out = []
        i = 0
        simple = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f",
                  "v": "\v", "0": "\0", "\\": "\\", "'": "'"}
        while i < len(text):
            c = text[i]
            if c != "\\":
                out.append(c)
                i += 1
                continue
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
            elif nxt == "u":
                out.append(chr(int(text[i + 2:i + 6], 16)))
                i += 6
            elif nxt == "x":
                out.append(chr(int(text[i + 2:i + 4], 16)))
                i += 4
            else:
                out.append(nxt)
                i += 2
        return "".join(out)

    start = s.find("JSON.parse('") + len("JSON.parse('")
    end = s.find("');export default e;")
    parsed = json.loads(_js_unescape(s[start:end]))
    for key in ADDITIONS:
        assert key in parsed, key
    return s.encode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pak", default=PAK)
    ap.add_argument("--out", default="/tmp/ui.loc2.patched.js")
    args = ap.parse_args()
    data = read_entry(args.pak, ENTRY)
    patched = patch(data)
    open(args.out, "wb").write(patched)
    print("patched %d -> %d bytes: %s" % (len(data), len(patched),
                                          args.out))


if __name__ == "__main__":
    main()
