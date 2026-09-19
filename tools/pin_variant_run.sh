#!/usr/bin/env bash
# One game launch with ONE Accept variant pinned for every shard connection.
#
# Use this when a specific variant needs a full-length connection rather than
# whatever slot the walker happened to give it. walk_probe_variants.sh covers
# the list; this interrogates one entry.
#
# Usage (HOST terminal, not sandboxed - Wine cannot run sandboxed):
#   ./tools/pin_variant_run.sh <variant-index> [outdir]
set -u
R="$(cd "$(dirname "$0")/.." && pwd)"
V="${1:?variant index required}"
OUT="${2:-$R/snapshots/pinned-v$V-$(date +%Y%m%d-%H%M%S)}"
GAME="$HOME/.local/share/Steam/steamapps/common/Crucible"
PROTON="$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton11-6-x86_64/proton"
PREFIX="$HOME/.local/share/Steam/steamapps/compatdata/1057240"
WAIT="${WAIT:-115}"
mkdir -p "$OUT"

killg() { python3 -c "
import os,signal,subprocess
o=subprocess.run(['pgrep','-af','Crucible.exe'],capture_output=True,text=True).stdout
for l in o.splitlines():
    p,_,c=l.partition(' ')
    if 'bin/Crucible.exe' in c and 'pgrep' not in c:
        try: os.kill(int(p),signal.SIGKILL)
        except Exception: pass
" 2>/dev/null; sleep 3; }
kills() { python3 -c "
import os,signal,subprocess
o=subprocess.run(['pgrep','-af','shard_stub.py'],capture_output=True,text=True).stdout
for l in o.splitlines():
    p,_,c=l.partition(' ')
    if 'server/shard_stub.py' in c and 'pgrep' not in c and '-af' not in c:
        try: os.kill(int(p),signal.SIGTERM)
        except Exception: pass
" 2>/dev/null; sleep 1; }

killg; kills
mv "$R/server/shard_udp.log" "$R/server/shard_udp.log.pinprev" 2>/dev/null
CRUCIBLE_SHARD_REPLY=accept CRUCIBLE_SHARD_PROBE=1 CRUCIBLE_SHARD_VARIANT="$V" \
  setsid nohup python3 "$R/server/shard_stub.py" > "$OUT/stub.out" 2>&1 < /dev/null &
sleep 2
rm -f "$PREFIX/pfx.lock"
( cd "$GAME" && STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam" \
  STEAM_COMPAT_DATA_PATH="$PREFIX" SteamAppId=1057240 SteamGameId=1057240 \
  setsid nohup timeout "$((WAIT + 20))" "$PROTON" run ./bin/Crucible.exe \
    > "$OUT/proton.log" 2>&1 < /dev/null & )
sleep "$WAIT"
killg
cp "$R/server/shard_udp.log" "$OUT/shard.log" 2>/dev/null

python3 - "$OUT/shard.log" "$V" <<'PY'
import re, sys, collections
path, v = sys.argv[1], sys.argv[2]
try: lines = open(path, errors='replace').read().splitlines()
except FileNotFoundError: sys.exit("no shard log")
pin = next((re.search(r'pinned for this connection: index (\d+) of \d+, (.*)$', l).groups()
            for l in lines if 'pinned for this connection' in l), None)
conns = collections.OrderedDict(); port = None; order = []
for l in lines:
    m = re.search(r"first datagram from \('127\.0\.0\.1', (\d+)\)", l)
    if m: port = m.group(1); order.append(port); conns.setdefault(port, collections.Counter())
    m = re.search(r"<- (\d+) bytes from \('127\.0\.0\.1', (\d+)\)", l)
    if m: port = m.group(2); conns.setdefault(port, collections.Counter()); conns[port]['n'] += 1
    m = re.search(r"hex: ([0-9a-f]+)", l)
    if m and port and port in conns:
        h = m.group(1); conns[port]['t' + h[:2]] += 1
        if len(h)//2 == 73: conns[port]['c73'] += 1
print("pinned:", pin)
worst = 0
for i, p in enumerate(order):
    c = conns[p]; t = {k[1:]: val for k, val in c.items() if k.startswith('t')}
    worst = max(worst, c['c73'])
    print("   conn%d port %s: dgrams=%4d connect73=%3d types=%s"
          % (i+1, p, c['n'], c['c73'], dict(sorted(t.items()))))
novel = {k for c in conns.values() for k in c if k.startswith('t')} - {'t51', 'tf0', 't53'}
print("VERDICT:", "REFUSED (full-length connection still retried %d times)" % worst
      if worst >= 20 else "LOOK - no full-length retry loop; inspect")
if novel: print("NOVEL PACKET TYPES:", novel)
PY
echo "artifacts in $OUT"
