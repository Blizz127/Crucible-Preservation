# Phase 0–1 results — full client verified, boot blocked by sandbox

## 0. Download: COMPLETE

- Steam promoted `downloading/1057240` → `steamapps/common/Crucible` at
  ~21:52 CDT. Manifest now reads `StateFlags=4`, `buildid=5685572`
  (matches `TargetBuildID`), 25 top-level entries, 21 GB on disk.
- Integrity snapshot: `snapshots/20260914-build5685572/` —
  `filelist.txt` (103 files + sizes), `sha256sums.txt` (103 hashes),
  `endpoints.txt` (156-line endpoint scan, see below).
- Binary fingerprints:
  - `bin/Crucible.exe` — PE32+ x86-64 GUI, md5 `04a844e3f1fed404c6938b8ec4a2f877`
  - `Launch_Crucible.exe` (repo root, not `bin/`) — md5 `df787a159c0f7db801632e2d53b8b7e7`
- Full pak set now includes `objects.pak` (2.5 GB), `sounds.pak` (2.1 GB),
  `textures.pak` (1.5 GB), `zero1.pak` (3.0 GB), `zero2.pak` (3.3 GB),
  `vfx.pak`, `videos.pak`, `zero.dds0.pak` — none of which were present in
  the partial staging tree. Still **no server exe in `bin/`** (client-only
  depot confirmed), and a `steamapps/compatdata/1057240` prefix already
  exists from Steam's side.

## 1. Endpoint scan re-run: deltas vs partial tree

`./tools/scan_endpoints.sh` against the complete tree (saved as
`snapshots/20260914-build5685572/endpoints.txt`). New details:

- GameSparks SDK **C++ 1.1.14**; build source paths baked in:
  `d:\workspace\crucible_hotfix\...NovaGameClient\GameDll\Component\Crucible\LivePlatform\...`
  and `E:\crucible_mainline\3rdParty\AwsGameSparks\...`
- Reinit requirements string: "GameSparks API Key, Access Key, Secret Key,
  Region, Auth Token, and entitlementId is required to reinitialize
  GameSparks."
- Message model: `GameFound` (session ID, ip, port, player session ID),
  `P2P`, `PartyStatus`, `ScriptMessage` with ExtCode/ShortCode validation
  (`GameSparksSDKMessageParser`).
- Notable flag: "If true, Crucible will use the Persona based
  PlayerAuthorizer flow **instead of Gamesparks** to authenticate players"
  — a possible bypass seam worth investigating once the client runs.
- Host-construction fragment `.service.gamesparks.`; cvars `gamesparks.creds`
  (`gamesparks_creds`), `gamesparks.authtoken`, `gamesparks.userid`;
  AWS markers `cognito-identity`, `execute-api`, `s3.*.amazonaws.com`;
  regions `us-east-1/2`, `eu-west-1/2`.

## 2. Boot attempts (sandbox) — all blocked BELOW the game layer

Three attempts, each root-caused; none reached game code:

1. **Proton Experimental `run`** → `OSError: [Errno 30] Read-only file
   system: '.../Proton - Experimental/dist.lock'`. Proton unconditionally
   lock-files its own directory, which is read-only in this sandbox.
2. **Lutris Wine-GE `wine`** → core dump (exit 159), even for
   `wine --version`. The 32-bit wine loader cannot start here.
3. **`wine64` (wine-8.0 Staging, works for `--version`)** → game launch
   fails with `wineserver: socket: Operation not permitted`.
   Proven at the syscall level: `socket(AF_UNIX)` → `PermissionError
   [Errno 1]`, while `socket(AF_INET)` succeeds. Wineserver fundamentally
   requires AF_UNIX, so **no Wine/Proton execution is possible inside this
   sandbox**. This is environmental, not a game defect — no game log was
   produced and no conclusion about the client's boot behavior can be drawn
   from it.

## 3. Host-run playbook (needs a real session: your KDE desktop)

The game must be launched on the host, where Steam, Proton, and AF_UNIX all
work. Steps:

1. In Steam, set Crucible's compatibility to **Proton Experimental**
   (Windows-only title; the `Proton EasyAntiCheat Runtime` tool is already
   in your library). Keep EAC enabled on the first run to observe the
   genuine behavior.
2. Optional DNS visibility: before launching, start a capture
   (`tcpdump -i any port 53`, or temporarily point the box at a logging
   resolver). Every hostname the client looks up while its backends are
   dead is a name your future local stack must answer (roadmap Phase 2).
3. Launch, preferably with logging:
   `STEAM_COMPAT_DATA_PATH=~/.local/share/Steam/steamapps/compatdata/1057240`
   is managed by Steam; just use Play, then read
   `steamapps/compatdata/1057240/pfx/drive_c/users/steamuser/Documents/`
   (or `Saved Games/`) plus `steam-1057240.log` in your home dir for
   Proton output.
4. Record the block point: screenshot + the first fatal/error lines
   (expect GameSparks auth timeout / `WAITING_FOR_GAMESPARKS_PARAMS` per
   the strings above). Also try `crucible_UsePreviewGameSparks=1` and
   `Crucible.ReinitGameSparks` from the in-game console if one is reachable
   — they select the preview credential set and re-init auth.
5. Do NOT "fix" it by reinstalling/verify-spamming: the 21 GB tree is
   hashed in `snapshots/` — re-run `./tools/snapshot.sh` and diff if Steam
   ever touches the install.

Next engineering step after a successful host boot log: local GameSparks
websocket stub returning canned auth + `GameFound` pointing at a loopback
NovaNet listener (roadmap Phases 2–4).
