#!/usr/bin/env bash
# Walk the Accept probe variants, one per game launch.
#
# Why a launch per variant
# ------------------------
# After the per-connection fix (docs/14) a probe variant is pinned for the
# life of a shard connection, which is the only way to test one honestly. But
# the client only opens ~1-3 shard connections per launch before
# OnConnectTimeout gives up for good, so covering the 12-entry variant list
# takes repeated launches. This drives them.
#
# Each cycle: rotate the shard log, launch, wait out boot + the ~30 s shard
# phase, kill, summarise. The probe counter in server/.shard-probe-counter
# advances on its own, so cycle N tests variant N.
#
# Usage (HOST terminal, not sandboxed - Wine cannot run sandboxed):
#   ./tools/walk_probe_variants.sh [cycles, default 12] [start-index, default current]
#
# Success signal: a connection whose Connect count is far below ~39, or any
# packet type other than 51/f0. Both mean the client stopped retrying.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CYCLES="${1:-12}"
GAME="$HOME/.local/share/Steam/steamapps/common/Crucible"
PROTON="$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton11-6-x86_64/proton"
PREFIX="$HOME/.local/share/Steam/steamapps/compatdata/1057240"
OUT="$ROOT/snapshots/probe-walk-$(date +%Y%m%d-%H%M%S)"
BOOT_WAIT="${BOOT_WAIT:-115}"   # boot to first shard datagram (~28 s) + shard phase (~30 s)
mkdir -p "$OUT"

if [ $# -ge 2 ]; then echo "$2" > "$ROOT/server/.shard-probe-counter"; fi

kill_game() {
  python3 - <<'PY' 2>/dev/null
import os, signal, subprocess
out = subprocess.run(['pgrep','-af','Crucible.exe'], capture_output=True, text=True).stdout
for line in out.splitlines():
    pid, _, cmd = line.partition(' ')
    if 'bin/Crucible.exe' in cmd and 'pgrep' not in cmd:
        try: os.kill(int(pid), signal.SIGKILL)
        except Exception: pass
PY
  sleep 3
}

echo "walking $CYCLES variants; results -> $OUT"
printf "%-6s %-4s %-46s %-8s %-9s %s\n" cycle idx variant dgrams connect73 types | tee "$OUT/summary.txt"

for c in $(seq 1 "$CYCLES"); do
  idx=$(cat "$ROOT/server/.shard-probe-counter" 2>/dev/null || echo 0)
  kill_game
  mv "$ROOT/server/shard_udp.log" "$ROOT/server/shard_udp.log.walkprev" 2>/dev/null

  rm -f "$PREFIX/pfx.lock"
  ( cd "$GAME" && \
    STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam" \
    STEAM_COMPAT_DATA_PATH="$PREFIX" SteamAppId=1057240 SteamGameId=1057240 \
    setsid nohup timeout "$((BOOT_WAIT + 20))" "$PROTON" run ./bin/Crucible.exe \
      > "$OUT/proton-$c.log" 2>&1 < /dev/null & )

  sleep "$BOOT_WAIT"
  kill_game
  cp "$ROOT/server/shard_udp.log" "$OUT/shard-cycle$c-idx$idx.log" 2>/dev/null

  python3 - "$ROOT/server/shard_udp.log" "$c" "$idx" "$OUT/summary.txt" <<'PY'
import re, sys, collections
path, cyc, idx, summ = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try: lines = open(path, errors='replace').read().splitlines()
except FileNotFoundError: lines = []
name = next((re.search(r'pinned for this connection: index \d+ of \d+, (.*)$', l).group(1)
             for l in lines if 'pinned for this connection' in l), '(no connection)')
per = collections.Counter(); types = collections.Counter(); port = None
for l in lines:
    m = re.search(r"<- (\d+) bytes from \('127\.0\.0\.1', (\d+)\)", l)
    if m: port = m.group(2); per['n'] += 1
    m = re.search(r"hex: ([0-9a-f]+)", l)
    if m and port:
        h = m.group(1); types[h[:2]] += 1
        if len(h)//2 == 73: per['c73'] += 1
row = "%-6s %-4s %-46s %-8d %-9d %s" % (cyc, idx, name[:46], per['n'], per['c73'], dict(sorted(types.items())))
print(row)
open(summ, 'a').write(row + "\n")
if per['c73'] and per['c73'] < 20 and per['n'] > 5:
    hit = "  *** connect retries well below ~39 - CANDIDATE, inspect this cycle ***"
    print(hit); open(summ, 'a').write(hit + "\n")
if set(types) - {'51', 'f0'}:
    hit = "  *** unexpected packet types %s - CANDIDATE ***" % (set(types) - {'51','f0'})
    print(hit); open(summ, 'a').write(hit + "\n")
PY
done

kill_game
echo "--- done. summary: $OUT/summary.txt ---"
cat "$OUT/summary.txt"
