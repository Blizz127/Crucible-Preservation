#!/usr/bin/env python3
"""Install the throwaway Crucible test CA(s) into the Proton prefix Root store.

Why: Wine keeps its own Root certificate store in the prefix registry
(system.reg) and ignores the host CA bundle, so the revival stubs' self-signed
certs are rejected with TLSV1_ALERT_INTERNAL_ERROR. This appends each cert as a
new entry in the exact format Wine uses (replicated from the 156 existing
entries: SHA1-keyed blob holding the DER certificate).

Every *.crt in server/.crucible-test-ca/ is installed, so all three stubs are
covered:
  * stub.crt  - the PlayerAuthorizer TLS stub  (server/tls_stub.py)
  * ws.crt    - the GameSparks websocket stub  (server/ws_stub.py)
  * match.crt - the LAN match-server stub      (server/match_stub.py)

Safety: stops this prefix's wineserver first (the registry is memory-resident
while it runs), backs up system.reg with a timestamp, then appends.
Revert: restore the newest system.reg.bak-* over system.reg (game stopped).

Usage (on the host, game quit):
  python3 tools/install_test_ca.py
"""
import glob
import hashlib
import os
import shutil
import ssl
import subprocess
import sys
import time

APPID = "1057240"


def sh(*cmd, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def build_entry(crt, now_unix):
    """Return (registry_key, entry_text) for one PEM certificate."""
    pem = open(crt, "rb").read()
    der = ssl.PEM_cert_to_DER_cert(pem.decode())
    sha1 = hashlib.sha1(der).digest()
    key = "".join(f"{b:02X}" for b in sha1)
    filetime = (now_unix + 11644473600) * 10_000_000
    blob = (b"\x03\x00\x00\x00" + b"\x01\x00\x00\x00" + b"\x14\x00\x00\x00"
            + sha1 + b"\x20\x00\x00\x00" + b"\x01\x00\x00\x00"
            + len(der).to_bytes(4, "little") + der)
    hx = ",".join(f"{b:02x}" for b in blob)
    # Wine-style wrap: ~76 cols, backslash continuations, 2-space indent.
    parts, line = [], ""
    for tok in hx.split(","):
        piece = ("" if not line else ",") + tok
        if len(line) + len(piece) > 74:
            parts.append(line + ",\\")
            line = "  " + tok
        else:
            line += piece
    parts.append(line)
    entry = (f"\n[Software\\\\Microsoft\\\\SystemCertificates\\\\Root\\\\Certificates\\\\{key}] {now_unix}\n"
             f"#time={filetime:016x}\n\"Blob\"=hex:" + "\n".join(parts) + "\n")
    return key, entry


def find_wineserver(home):
    """Locate this prefix's wineserver via Steam's recorded compat tool."""
    steam = os.path.join(home, ".local/share/Steam")
    cfginfo = os.path.join(steam, "steamapps/compatdata", APPID, "config_info")
    tool = open(cfginfo).readline().strip() if os.path.isfile(cfginfo) else ""
    tools = os.path.join(steam, "compatibilitytools.d")
    cand = sorted(glob.glob(os.path.join(tools, tool + "*")))
    if not cand:
        cand = sorted(glob.glob(os.path.join(tools, "GE-Proton*")))
    if not cand:
        sys.exit("no GE-Proton install found under compatibilitytools.d")
    ws = os.path.join(cand[-1], "files", "bin", "wineserver")
    if not os.path.isfile(ws):
        sys.exit(f"no wineserver at {ws}")
    return ws, steam


def main():
    home = os.path.expanduser("~")
    here = os.path.dirname(os.path.abspath(__file__))
    cadir = os.path.join(here, "..", "server", ".crucible-test-ca")
    certs = sorted(glob.glob(os.path.join(cadir, "*.crt")))
    if not certs:
        sys.exit(f"no test certs in {cadir} (run server/tls_stub.py or "
                 f"server/match_stub.py once to generate them)")

    ws, steam = find_wineserver(home)
    pfx = os.path.join(steam, "steamapps/compatdata", APPID, "pfx")
    sysreg = os.path.join(pfx, "system.reg")
    if not os.path.isfile(sysreg):
        sys.exit(f"no prefix registry: {sysreg}")

    # Stop the prefix server so the registry edit sticks.
    env = dict(os.environ, WINEPREFIX=pfx)
    r = sh(ws, "-k", env=env)
    print(f"wineserver -k: rc={r.returncode} {r.stderr.strip()[:200]}")
    time.sleep(2)

    reg = open(sysreg, "r", encoding="utf-8", errors="replace").read()
    now_unix = int(time.time())
    backup = sysreg + f".bak-crucible-{now_unix}"
    added, done = [], []

    for crt in certs:
        name = os.path.basename(crt)
        key, entry = build_entry(crt, now_unix)
        if key in reg:
            done.append(name)
            print(f"{name}: already trusted (sha1={key[:16]}...), skipping")
            continue
        if not added:
            shutil.copy2(sysreg, backup)
            print(f"backup: {backup}")
        with open(sysreg, "a", encoding="utf-8") as f:
            f.write(entry)
        reg += entry              # so a duplicate cert in the dir is caught too
        added.append((name, key))
        print(f"{name}: installed (sha1={key[:16]}...)")

    if not added:
        print(f"nothing to do: all {len(done)} cert(s) already in the prefix Root store.")
    else:
        print(f"installed {len(added)} cert(s). Next: launch the game.")
        print("Revert: restore %s over system.reg with the game stopped." % backup)


if __name__ == "__main__":
    main()
