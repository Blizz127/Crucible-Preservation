#!/usr/bin/env python3
"""Point the shipped menu UI at the local revival backend.

Two minimal, documented patches to ui/dist/preloader.js (applied to bytes
extracted from gamedata.pak; see tools/rebuild_pak.py for installation):

P1 - dummy credentials: `Si=rt(!1)` (empty credential store) becomes a
     store holding local dummy credentials. The services layer queues all
     HTTP requests until credentials exist and signs them with AWS SigV4;
     the local backend accepts any signature, so dummy values unblock the
     queue without touching the signing code.

P2 - local service URLs: `Ni.config=...` (which awaits a real login and
     then queries the native `serviceconfiguration.get` bridge that never
     answers offline) becomes an immediately-resolved map of all 20
     config keys to the local backend / safe defaults.

Usage: python3 patch_services.py [--port 18876] [--out /tmp/preloader.patched.js]
"""
import argparse
import os
import struct
import zlib

PAK = os.environ.get(
    "CRUCIBLE_PAK",
    os.path.expanduser("~/.local/share/Steam/steamapps/common/"
                       "Crucible/gamedata.pak"))
ENTRY = "ui/dist/preloader.js"

SERVICES = [
    "CrucibleLivePlayerIdentityService",
    "CrucibleLivePlayerDataService",
    "CrucibleLivePartyService",
    "CrucibleLiveMatchmakingService",
    "CrucibleLiveModerationService",
    "CrucibleLiveEntitlementService",
    "CrucibleLiveChallengeService",
    "CrucibleLiveOfferService",
    "CrucibleLiveCharacterLevelService",
    "CrucibleLiveSystemMessages",
    "CrucibleLiveGameConfigService",
    "CrucibleLiveDefectReporter",
]


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
            assert len(data) == us, (len(data), us)
            return data
        o += 46 + fnlen + xtra + cmnt
    raise KeyError(name)


def patch(data, port):
    s = data.decode("utf-8")

    # P1: dummy credentials.
    old1 = "Si=rt(!1)"
    assert s.count(old1) == 1, s.count(old1)
    new1 = ('Si=rt({accessKeyId:"LOCALREVIVAL",secretKey:"LOCALREVIVAL",'
            'sessionToken:"LOCALREVIVAL"})')
    s = s.replace(old1, new1)

    # P2: local Ni.config. Locate by anchors (avoids transcribing 400 chars).
    start_anchor = ("Ni.config=new Promise(async e=>{try{await Ni.login}"
                    "catch(t){return}return e((await Promise.all(")
    end_anchor = ",{__proto__:null}))})"
    i = s.find(start_anchor)
    assert i >= 0, "Ni.config anchor missing"
    j = s.find(end_anchor, i)
    assert j >= 0, "Ni.config end anchor missing"
    span = s[i:j + len(end_anchor)]
    assert "serviceconfiguration.get" in span, "unexpected Ni.config body"
    assert len(span) < 700, len(span)

    base = "http://127.0.0.1:%d" % port
    parts = ['"%s":"%s/%s"' % (svc, base, svc) for svc in SERVICES]
    parts += ['"PingBeaconWebSocketUrls":[]',
              '"PingBeaconV2WebSocketUrls":[]',
              '"analyticsKinesisStreamName":"local"',
              '"gameSparksAPIKey":""',
              '"gameSparksPlayerAPISecret":""',
              '"gameSparksEnvironment":"local"',
              '"stage":"local"',
              '"region":"us-east-1"']
    new2 = "Ni.config=Promise.resolve({__proto__:null,%s})" % ",".join(parts)
    s = s[:i] + new2 + s[j + len(end_anchor):]
    return s.encode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18876)
    ap.add_argument("--out", default="/tmp/preloader.patched.js")
    args = ap.parse_args()
    data = read_entry(PAK, ENTRY)
    patched = patch(data, args.port)
    open(args.out, "wb").write(patched)
    print("patched %d -> %d bytes: %s" % (len(data), len(patched), args.out))


if __name__ == "__main__":
    main()
