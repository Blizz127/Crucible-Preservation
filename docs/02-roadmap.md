# Roadmap

## Phase 0 — Finish the client (you are here)

- Let Steam complete the ~20 GB download (BuildID `5685572`). The install
  dir is currently empty; the files sit under
  `steamapps/downloading/1057240/`. Do not copy/repack from the staging dir
  mid-download — let Steam promote it.
- Verify integrity, then take a **read-only snapshot**: full file list with
  sizes + hashes (for provenance and to detect Steam silently updating).
- Re-run `./tools/scan_endpoints.sh` against the complete tree.

## Phase 1 — Offline boot test

- Boot `Crucible.exe` with no network (Steam in offline mode ok, EAC may
  need attention) and record exactly where it blocks: main menu?
  `WAITING_FOR_GAMESPARKS_PARAMS`? Expected: it reaches the menu shell and
  fails at login/auth.
- Capture: client log (`%USERPROFILE%/Saved Games` or game dir logs),
  screenshots, and a DNS/connection log (see Phase 2). This defines the
  minimum emulation surface.

## Phase 2 — Endpoint discovery (the "firewall project" pattern)

- Run the client in a VM/lab with DNS logging + a MITM TLS proxy with a
  custom CA (standard private-server recon). Since the real backends are
  dead, every hostname it tries to resolve is a hostname your local stack
  must eventually answer — redirect via hosts file / firewall rules, exactly
  like other revival projects do.
- Targets to recover: GameSparks API host + key/secret/region
  (`crucible_UsePreviewGameSparks=1` may select a second credential set —
  dump both), CloudGem API Gateway base URLs (one per live service),
  Cognito pool IDs, GameLift endpoints, Vivox voice URLs.

## Phase 3 — Auth bypass vs. emulation (first real fork)

- **Cheap path**: patch or stub the auth chain so the client proceeds
  without GameSparks/Cognito (cvar hooks, `ReinitGameSparks` short-circuit,
  or a minimal local GameSparks-protocol stub that returns canned tokens).
- **Faithful path**: reimplement the GameSparks websocket protocol + the
  CloudGem REST surface the client actually calls (discover via MITM, not
  by guessing — the client only needs a subset: identity, authorizer, game
  config, entitlements at minimum).
- Decide based on Phase 1/2 evidence. Expect the cheap path first, faithful
  later.

## Phase 4 — Gameplay server

- No server binary ships with the client, so options in order of cost:
  1. **Listen-server/practice mode**: if the client can host
     (`practicearena`, tutorials) without GameLift, enable it — first
     playable milestone.
  2. **NovaNet server reimplementation**: the client speaks NovaNet to
     `ip:port` with GameSparks/PlayerSession identity; a community server
     must implement session handshake + replication for at least one map.
  3. **Local GameLift stub**: only if the client insists on FleetIQ-style
     session placement before connecting.
- EAC and Vivox both need decisions (disable/strip for LAN play vs. stub);
  note EAC removal changes the binary, so keep pristine + patched builds
  separated and documented.

## Phase 5 — Packaging

- One-command local stack (compose file or single binary), seed data,
  version pinning to BuildID `5685572`, and a test matrix (menu → login →
  practice → multiplayer).

## Standing risks

- GameSparks is un-revivable as a service (shut down 2022) — only protocol
  emulation or client bypass can work.
- CloudGem/GameLift endpoints are per-deployment AWS resources; Amazon
  deleted them with the game. No archived copy can restore them.
- Legal: keep this to clean-room interop (your own backend speaking the
  client's protocol), distribute no game assets, require users to own the
  Steam copy.
