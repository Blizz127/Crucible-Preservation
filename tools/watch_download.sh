#!/usr/bin/env bash
# Poll Steam Crucible download until promotion to common/Crucible.
# Logs progress lines to stdout; exits 0 on promotion, 1 on timeout.
set -u
STAGE="$HOME/.local/share/Steam/steamapps/downloading/1057240"
DEST="$HOME/.local/share/Steam/steamapps/common/Crucible"
MANIFEST="$HOME/.local/share/Steam/steamapps/appmanifest_1057240.acf"
DEADLINE_S="${1:-2400}"
INTERVAL_S=20
elapsed=0
while (( elapsed < DEADLINE_S )); do
  staged="gone"
  [[ -d "$STAGE" ]] && staged="$(du -sh "$STAGE" 2>/dev/null | cut -f1)"
  dest_count="$(ls -A "$DEST" 2>/dev/null | wc -l)"
  flags="$(grep -oP '"StateFlags"\s+"\K[0-9]+' "$MANIFEST" 2>/dev/null || echo ?)"
  build="$(grep -oP '"buildid"\s+"\K[0-9]+' "$MANIFEST" 2>/dev/null || echo ?)"
  rate="$(tail -n 50 "$HOME/.local/share/Steam/logs/content_log.txt" 2>/dev/null | grep -oP 'Current download rate: \K[0-9.]+ Mbps' | tail -n 1)"
  echo "[$(date +%H:%M:%S)] staged=$staged dest_files=$dest_count StateFlags=$flags buildid=$build rate=${rate:-n/a}"
  if [[ ! -d "$STAGE" && "$dest_count" -gt 0 ]]; then
    echo "PROMOTED dest_bytes=$(du -sh "$DEST" 2>/dev/null | cut -f1)"
    exit 0
  fi
  sleep "$INTERVAL_S"
  elapsed=$(( elapsed + INTERVAL_S ))
done
echo "TIMEOUT after ${DEADLINE_S}s"
exit 1
