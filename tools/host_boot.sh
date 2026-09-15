#!/usr/bin/env bash
# One-command Crucible boot capture. RUN ON THE HOST (a normal terminal,
# NOT inside the agent sandbox): Wine/Proton cannot run sandboxed.
#
# Usage (from repo root):
#   ./tools/host_boot.sh [seconds-to-run, default 120]
#
# Collects into snapshots/host-boot-<timestamp>/ :
#   proton-host.log   - Proton + game stdout/stderr
#   steam-1057240.log - PROTON_LOG output (if produced)
#   dns.log           - DNS queries during the run (needs sudo tcpdump; skipped otherwise)
#   prefix-logs/      - Documents/Saved Games + AppData logs from the prefix
#   manifest.txt      - build id, state flags, Proton version
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/snapshots/host-boot-$(date +%Y%m%d-%H%M%S)"
GAME="$HOME/.local/share/Steam/steamapps/common/Crucible"
PROTON="$HOME/.local/share/Steam/steamapps/common/Proton - Experimental/proton"
PREFIX="$HOME/.local/share/Steam/steamapps/compatdata/1057240"
RUN_S="${1:-120}"
mkdir -p "$OUT/prefix-logs"
{
  echo "date=$(date -Is)"
  grep -E '"(buildid|StateFlags)"' "$HOME/.local/share/Steam/steamapps/appmanifest_1057240.acf" 2>/dev/null
  echo "proton=$("$PROTON" --version 2>/dev/null | head -n 1)"
  pgrep -a -f "^.*steam$" >/dev/null 2>&1 && echo "steam_client=running" || echo "steam_client=NOT-RUNNING"
  md5sum "$GAME/bin/Crucible.exe" 2>/dev/null
} > "$OUT/manifest.txt" 2>&1
cat "$OUT/manifest.txt"
if ! pgrep -f "steam" >/dev/null 2>&1; then
  echo "WARNING: Steam client does not appear to be running; steam_api init may fail." | tee -a "$OUT/manifest.txt"
fi
# DNS capture (best effort; needs capture privileges)
if command -v tcpdump >/dev/null 2>&1 && (sudo -n true 2>/dev/null || [ "$(id -u)" = "0" ]); then
  echo "starting DNS capture for ${RUN_S}s..."
  SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo -n"
  $SUDO tcpdump -i any -n port 53 2>/dev/null > "$OUT/dns.log" &
  TCPDUMP_PID=$!
else
  echo "NOTE: no privileged tcpdump; skipping DNS capture." | tee "$OUT/dns.log"
  TCPDUMP_PID=""
fi
echo "launching Crucible for up to ${RUN_S}s (close the window anytime)..."
cd "$GAME" || exit 1
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam" \
       STEAM_COMPAT_DATA_PATH="$PREFIX" SteamAppId=1057240 PROTON_LOG=1
timeout "$RUN_S" "$PROTON" run ./bin/Crucible.exe > "$OUT/proton-host.log" 2>&1
echo "game-exit=$?" | tee -a "$OUT/manifest.txt"
[ -n "$TCPDUMP_PID" ] && kill "$TCPDUMP_PID" 2>/dev/null
[ -f "$HOME/steam-1057240.log" ] && cp "$HOME/steam-1057240.log" "$OUT/"
# Harvest prefix logs (game writes under Documents / Saved Games / AppData)
for d in "$PREFIX"/pfx/drive_c/users/*/Documents "$PREFIX"/pfx/drive_c/users/*/Saved\ Games "$PREFIX"/pfx/drive_c/users/*/AppData; do
  [ -d "$d" ] && cp -r "$d" "$OUT/prefix-logs/" 2>/dev/null
done
echo "--- collected ---"
ls -la "$OUT" | head -n 20
echo "TIP: in the in-game console try: gamesparks_creds ; PlayerAuthorizerEndpoint ; crucible_UsePersonaAuth"
