#!/usr/bin/env bash
# Read-only pre-flight for a live match against the managed local services.
# Run it on the game host, before the player presses READY:
#   bash tools/preflight_local_services.sh
#   ssh HOST 'bash -s' < tools/preflight_local_services.sh
# It changes nothing: it starts, stops or restarts no service and sends no
# input to the game. It prints OK/WARN/FAIL lines and exits 1 on any FAIL.
set -u
fail=0
ok() { echo "OK    $*"; }
warn() { echo "WARN  $*"; }
bad() { echo "FAIL  $*"; fail=1; }

for name in backend hub shard; do
  unit="crucible-revival-$name.service"
  state=$(systemctl --user show "$unit" -p ActiveState --value 2>/dev/null)
  restarts=$(systemctl --user show "$unit" -p NRestarts --value 2>/dev/null)
  since=$(systemctl --user show "$unit" -p ActiveEnterTimestamp --value 2>/dev/null)
  if [[ "$state" == active ]]; then
    ok "$unit active since $since, $restarts restarts"
  else
    bad "$unit is ${state:-missing}"
  fi
done
dir=$(systemctl --user show crucible-revival-shard.service -p WorkingDirectory --value 2>/dev/null)
echo "INFO  shard code directory: $dir"

listeners=$(ss -H -ltnup 2>/dev/null | grep -E ':(18876|18877|18878)\b')
for port in 18876/tcp 18877/tcp 18878/udp; do
  line=$(grep -E "^${port#*/} .*:${port%/*}\b" <<<"$listeners")
  if [[ -z "$line" ]]; then
    bad "nothing listening on $port"
  elif grep -q " 127\.0\.0\.1:${port%/*} " <<<"$line"; then
    ok "$port bound to 127.0.0.1 only"
  else
    bad "$port is not loopback-only: $line"
  fi
done

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
  http://127.0.0.1:18876/CrucibleLivePartyService/describeParty/local-party)
[[ "$code" == 200 ]] && ok "backend party route 200" || bad "backend party route returned $code"
tls=$(timeout 8 openssl s_client -connect 127.0.0.1:18877 -brief </dev/null 2>&1 | grep -m1 'Protocol version')
[[ -n "$tls" ]] && ok "hub TLS handshake: ${tls#*: }" || bad "hub TLS handshake failed"

env=$(systemctl --user show crucible-revival-shard.service -p Environment --value 2>/dev/null)
echo "INFO  shard settings: $(tr ' ' '\n' <<<"$env" | grep -E '^CRUCIBLE_(SOLO|SHARD)_' | paste -sd ' ' -)"
hero=$(tr ' ' '\n' <<<"$env" | sed -n 's/^CRUCIBLE_SOLO_HERO=//p')
echo "INFO  pick this hunter in the menu: ${hero:-marine}"

if pgrep -f -i 'Crucible\.exe' >/dev/null; then
  warn "Crucible client is running: do not restart services until the match is over"
else
  ok "no Crucible client running"
fi

for zone in /sys/class/thermal/thermal_zone*; do
  [[ "$(cat "$zone/type" 2>/dev/null)" == x86_pkg_temp ]] || continue
  echo "INFO  package $(( $(cat "$zone/temp") / 1000 )) C"
done
sensors 2>/dev/null | sed -n 's/^Tctl: *+\([0-9.]*\).*/INFO  Tctl \1 C/p'
echo "INFO  MemAvailable $(( $(awk '/MemAvailable/ {print $2}' /proc/meminfo) / 1048576 )) GB," \
  "home free $(df -h --output=avail ~ | tail -1 | tr -d ' ')"
exit $fail
