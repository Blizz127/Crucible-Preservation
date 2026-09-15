#!/usr/bin/env bash
# Snapshot a Crucible client tree: sorted file list with sizes + sha256.
# Usage: ./tools/snapshot.sh "<crucible-dir>" [out-dir]
# Writes <out-dir>/filelist.txt and <out-dir>/sha256sums.txt
set -u
ROOT="${1:-}"
OUT="${2:-snapshots/$(date +%Y%m%d-%H%M%S)}"
if [[ -z "$ROOT" || ! -d "$ROOT" ]]; then
  echo "usage: $0 <crucible-dir> [out-dir]" >&2
  exit 1
fi
mkdir -p "$OUT"
LIST="$OUT/filelist.txt"
SUMS="$OUT/sha256sums.txt"
: > "$LIST"; : > "$SUMS.tmp"
# including NvGameSdk subtree if present
find "$ROOT" -type f -printf '%s %p\n' 2>/dev/null | sort -k2 > "$LIST"
wc -l "$LIST"
find "$ROOT" -type f -exec sha256sum {} + 2>/dev/null | sort -k2 > "$SUMS.tmp"
mv "$SUMS.tmp" "$SUMS"
wc -l "$SUMS"
du -sh "$ROOT"
echo "wrote $LIST $SUMS"
