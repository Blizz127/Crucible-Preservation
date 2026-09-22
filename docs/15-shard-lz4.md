# Shard connection repair — 2026-09-19

The client now reaches `Connected` and exchanges heartbeats with the local
shard. This does **not** yet mean a playable arena: the observed UI remains
on Connecting and logs `GameModeComponent was not ready yet`. Player spawn,
game-mode state and input ownership remain unfinished.

## Root cause and evidence

The `51`, `53`, and `f0 01` bytes in earlier captures are raw LZ4 block tokens.
They are not channel identifiers, encryption, or hidden checksum data.
Decompressing all 169 datagrams in the previous `server/shard_udp.log` produced
169 valid ordinary CRC32 checksums (41 Connect, 126 heartbeat, 2 disconnect).
The two object-framing virtual methods previously suspected of rejecting the
header simply return constant success. The overlooked receive step was the
compressor call at RVA `0x4f1eae0`, before header deserialization.

Observed client-to-server framing:

```
LZ4(header + payload + big-endian CRC32(header + payload))
```

Verified server-to-client framing:

```
00 + LZ4(header + payload + big-endian CRC32(header + payload))
```

Use raw blocks, without python-lz4's default stored-size prefix. The retail
receive decompression buffer is 4096 bytes. The extra server-side `00` is
BasePacket::Unencrypted. Do not copy client compression tokens into that field.

The first corrected run logged, at 16:02:50 GMT:

- `Received packet Accept`
- `ConnectingToServerShard to Connected`
- `Fully connected to game server shard, allowing connect retry timer to expire`

It then asserted on `Connected to Connected`: a retransmitted Connect had
received a new Accept packet id. The server now caches and retransmits the
original Accept, allowing the client's duplicate-id window to discard it.
The subsequent run maintained the same connection for over two minutes with
heartbeats, without the duplicate-Accept assertion. Acknowledgement bitmaps
were then added so reliable Connect packets can retire instead of retrying.

The old checksum/sequence/Accept-variant investigations in docs 12 and 14
are historical, superseded evidence. Do not run `CRUCIBLE_SHARD_PROBE=1`
for normal use. The old entity sweep is disabled by default and is not a
working player-spawn implementation.

## Run locally

Install Python dependencies from `server/requirements.txt`. Start:

```
python3 server/crucible_backend.py --port 18876
CRUCIBLE_REPLY=hubrpc CRUCIBLE_HUB_SHARDADDR=reply \
  CRUCIBLE_HUB_SHARD_HOST=127.0.0.1 CRUCIBLE_HUB_SHARD_PORT=18878 \
  python3 server/match_stub.py
CRUCIBLE_SHARD_REPLY=accept python3 server/shard_stub.py
```

The menu LAN Ready target is port 18877. The old autoexec `connect` path used
8192 and bypassed Ready; it is no longer the intended test path. Keep the
existing local TLS/unencrypted-UDP settings; remove the standalone `connect`
line from autoexec and restore the retail `map MainMenu` command to use the menu. No other players are required by these
local listeners.

## Checks

```
python3 server/test_shard_transport.py
python3 server/test_hubrpc.py
python3 tools/test_mode_gate.py
```

The transport tests cover a captured compressed disconnect with back-references,
a corrupted captured checksum, outbound compression, duplicate Connect, and
acknowledgement of the reliable Connect. Live-run artifacts are under
`snapshots/lz4-20260919`, `snapshots/lz4-retry-20260919`, and
`snapshots/lz4-ready-20260919` (local evidence, not retail assets).

## Manual-launch handoff

The user requested that further game launches and desktop control be left to
them after launch instability. No playable-arena or Ready-button pass is claimed.
The final transport code has seven passing regression tests; the hub, mode-gate
and installed menu-patch checks also pass.

A separate startup issue was diagnosed from the installed retail `misc.pak`:
its `autoexec.cfg` contains `map MainMenu`. The loose diagnostic autoexec had
shadowed that file with `connect`; merely removing `connect` therefore left
no startup map and a black window. The loose file now ends in the retail
`map MainMenu` command, with both earlier versions backed up under the Ready
run directory. **This restoration awaits the user's manual launch.** No new
executable or pak patch was applied in this turn.

The current blockers to an actual arena are game-mode entity state, player
spawn/ownership and simulation. No additional players are required by the
connection service. After a second restart the services were absent and the menu's entitlement
requests failed, leaving modes unavailable. `python3 tools/install_local_services.py`
now installs and enables three user systemd services: `crucible-revival-backend`,
`crucible-revival-hub`, and `crucible-revival-shard`. All three were verified active,
their ports bound, and the entitlement endpoint returned HTTP 200 with valid JSON.
They start at login and restart on failure; they never launch the game.
Use `systemctl --user restart crucible-revival-shard` after shard code changes.
Disable with `systemctl --user disable --now crucible-revival-{backend,hub,shard}`.
The user must relaunch manually to retry menu requests that failed at startup.
