#!/usr/bin/env bash
# Re-runnable endpoint/credential scan against a Crucible client tree.
# Usage: ./tools/scan_endpoints.sh "<path-to-Crucible>"
# Works against either the Steam staging dir (.../downloading/1057240)
# or the promoted install dir (.../common/Crucible).
set -u
ROOT="${1:-}"
if [[ -z "$ROOT" || ! -d "$ROOT" ]]; then
  echo "usage: $0 <crucible-dir>" >&2
  exit 1
fi
EXE="$ROOT/bin/Crucible.exe"
if [[ ! -f "$EXE" ]]; then
  echo "no bin/Crucible.exe under $ROOT" >&2
  exit 1
fi
echo "### GameSparks / auth surface"
strings "$EXE" | grep -i -E "gamesparks|UsePreviewGameSparks|ReinitGameSparks|GS API|Auth Tokens" | sort -u
echo "### Cloud regions / AWS markers"
strings "$EXE" | grep -E "us-east-[0-9]|eu-west-[0-9]|execute-api|amazonaws\.com|cognito" | sort -u | head -n 30
echo "### Live-service API surface"
strings "$EXE" | grep -oE "CrucibleLive[A-Za-z]+Service" | sort -u
echo "### NovaNet connect strings"
strings "$EXE" | grep -E "Attempting to connect|NovaNetUserId|PlayerSessionId" | sort -u
echo "### wss/url format strings"
strings "$EXE" | grep -E "wss://|https://" | sort -u | head -n 20
