#!/usr/bin/env python3
"""Menu patches for offline revival (applied to ui/dist/menu.js bytes).

M1 - suppress the "DISCONNECTED" modal while in LAN mode.
     Rationale: the modal waits for a NATIVE reconnect (GameSparks login)
     that can never complete offline, so it would cover the menu forever.
     In LAN mode the local backend IS the connection, therefore the
     native-timeout disconnect notice is noise. Behavior is unchanged when
     not in LAN mode (online retail path untouched).
M4 - race the boot "analytics-data" invoke against a 3 s local fallback.
     Rationale: the machine only enters `services` (feature-flags fetch,
     unready put, playerinfo) after two NATIVE bridge promises
     (data.get session, buildinfo.get) resolve; offline they never settle,
     so the battlepass feature flag is never fetched and the BATTLE PASS
     tab stays disabled with no error. The fallback only supplies
     telemetry identity (unused offline); a fast native bridge still wins
     the race, so the online retail path is untouched.

Usage: python3 patch_menu.py [--in PATH] [--out /tmp/menu.patched.js]
  default --in reads ui/dist/menu.js from the live gamedata.pak.
"""
import argparse
import os
import struct
import zlib

PAK = os.environ.get(
    "CRUCIBLE_PAK",
    os.path.expanduser("~/.local/share/Steam/steamapps/common/"
                       "Crucible/gamedata.pak"))
ENTRY = "ui/dist/menu.js"


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
    assert "QUIT-MARKER" not in s, "diagnostic markers still present"

    # M1: gate the DISCONNECTED trigger on not-LAN (`by` = Fi lan store,
    # already imported in menu.js).
    old = ('{id:"disconnected",src:()=>e=>Nt.subscribe(t=>{if(t)return '
           'e("DISCONNECTED")})}')
    new = ('{id:"disconnected",src:()=>e=>{let l=!1;const a=by.subscribe(t=>'
           'l=t),n=Nt.subscribe(t=>{t&&!l&&e("DISCONNECTED")});'
           'return()=>{a(),n()}}}')
    if new in s:
        pass  # M1 already applied
    else:
        assert s.count(old) == 1, s.count(old)
        s = s.replace(old, new)

    # M2: resolve the LAN loadout-set natively-missing bridge call so READY
    # can proceed to the (also local) matchserver.connect stage instead of
    # failing immediately with FAILED_TO_SET_LOADOUT.
    old2 = ('src:({character:e,perks:t})=>s.promise('
            '"localconnection.loadout.set",'
            '{character:e,perks:t,cosmetics:[]})')
    if 'src:()=>Promise.resolve()' in s:
        pass  # M2 already applied
    else:
        assert s.count(old2) == 1, s.count(old2)
        s = s.replace(old2, 'src:()=>Promise.resolve()')

    # M3: extend the offline localconnection object with an explicit LAN
    # endpoint pointing at the revival host, so matchserver.connect has a
    # concrete target (previously ipAddress/port were undefined). A local
    # probe listener can then observe what the native client attempts.
    old3 = 'src:()=>Promise.resolve({lan:!0,host:"127.0.0.1"})'
    new3 = ('src:()=>Promise.resolve({lan:!0,host:"127.0.0.1",'
            'ipAddress:"127.0.0.1",port:18877})')
    if new3 in s:
        pass  # M3 already applied
    else:
        assert s.count(old3) == 1, s.count(old3)
        s = s.replace(old3, new3)

    # M4: the boot machine stalls in `analytics` when the native bridge
    # never resolves data.get/buildinfo.get, so `services` (feature-flags)
    # never runs. Race it against a local fallback; only telemetry
    # identity is synthesized, and only if the native side is silent.
    old4 = ('src:()=>Promise.all([s.promise("data.get",{key:"session"}),'
            's.promise("buildinfo.get")])')
    new4 = ('src:()=>Promise.race([Promise.all([s.promise("data.get",'
            '{key:"session"}),s.promise("buildinfo.get")]),new Promise('
            'e=>setTimeout(()=>e([{result:{}},{buildVersion:"local",'
            'appId:"1057240",stage:"local"}]),3000))])')
    if new4 in s:
        pass  # M4 already applied
    else:
        assert s.count(old4) == 1, s.count(old4)
        s = s.replace(old4, new4)
    return s.encode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pak", default=PAK)
    ap.add_argument("--out", default="/tmp/menu.patched.js")
    args = ap.parse_args()
    data = read_entry(args.pak, ENTRY)
    patched = patch(data)
    open(args.out, "wb").write(patched)
    print("patched %d -> %d bytes: %s" % (len(data), len(patched),
                                          args.out))


if __name__ == "__main__":
    main()
