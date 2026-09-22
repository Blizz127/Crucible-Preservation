# Crucible Revival

Fan preservation project for Amazon's **Crucible** (Steam AppID `1057240`,
last build `5685572`). Goal: get the client booting and, eventually,
playable again via a community backend — same playbook as other dead-game
revivals (local server emulator + traffic redirect via hosts file / firewall
rules, client patched only where unavoidable).

> Status: **Solo entry verified on two profiles. AI and combat are written
> but not yet seen running.**
> Training Arena enters with one controllable hero and sustained movement;
> Alpha Hunters enters on April2018 with three visible Marine hunters
> (screenshot-confirmed). Both are *stationary* populations. The installed
> local services use `CRUCIBLE_SHARD_BOOTSTRAP=practice-character`. See the
> [startup and runtime evidence](docs/16-entity-bootstrap.md) and
> [Alpha Hunters acceptance](docs/18-heart-of-the-hives-solo.md).
>
> Since then, and **not yet confirmed in a running client**: authoritative
> damage/death/respawn from the retail weapon assets (`docs/20`), hunter-bot
> pursuit over the shipped navmesh (`docs/21`), creature aggro and attack
> timing (`docs/22`), and the client-input decoder that tells the server
> where the player is (`docs/25`). All of it is opt-in and off by default —
> `CRUCIBLE_SOLO_COMBAT=1` and `CRUCIBLE_SOLO_AI=1` — so the two accepted
> profiles are byte-identical unless you ask for the new behaviour.
> Multiplayer, persistence, objectives and full server simulation remain
> unimplemented.

## Scope and legal

This repository contains **no game assets** — no binaries, pak contents,
textures, audio, locale bundles or extracted game code. It ships tooling,
findings and captured logs. The `snapshots/` manifests record the file names,
sizes and hashes of a retail install; they do not contain the files themselves.

- You must **own Crucible on Steam** (AppID `1057240`). Nothing here helps you
  obtain it, and no copy-protection is bypassed to run it.
- Patching is applied to **your own local copy**, for interoperability with a
  local backend standing in for services Amazon shut down in 2020 (GameSparks
  was sunset in 2022). The goal is a clean-room reimplementation — our own
  server speaking the client's protocol.
- Everything is **local only**: the revived endpoints are `127.0.0.1`. Nothing
  here targets live infrastructure, and the original endpoints are dead
  (`NXDOMAIN`).
- **Do not redistribute patched paks or any extracted game content.**
- **No anti-cheat component has been modified or removed.** `docs/02` records
  the EAC question as an open design decision for a future LAN mode; no such
  change exists in this repository.

Not affiliated with or endorsed by Amazon Game Studios or Amazon.com, Inc.
"Crucible" and related marks belong to their respective owners. Code and
documentation here are MIT licensed (see `LICENSE`) — that covers this
repository's original work, not any third-party game content.

## Layout

- `docs/01-recon.md` — what the client actually is (engine, gems, backend
  stack), with evidence.
- `docs/02-roadmap.md` — phased revival plan.
- `docs/03`–`06` — boot results, static RE, network surface, redirect plan.
- `docs/07-local-backend.md` — the local backend and its pak patches (M1–M4).
- `docs/08-matchmaking-practice.md` — READY flow, TLS match server, practice.
- `docs/09-practice-arena.md` — the mode-select gate and entitlement unlocks.
- `docs/10-match-server-tls.md` — cert pinning (the real blocker), the first
  captured frame, and the shard's packet table. Its wire-format section is
  superseded by `docs/11`.
- `docs/11-novagamehub-protocol.md` — the NovaGameHub RPC on `:18877`: message
  ids, envelope, field encodings, and what the stub now answers.
- `docs/12-server-shard-udp.md` — the server shard on `:18878`: UDP/DTLS, the
  packet framing, and the one checksum that is still unexplained.
- `docs/13-entity-replication.md` — the EntityUpdates/EntityRpcs envelope and
  an honest ordering of the work between here and a playable match.
- `docs/14-state-of-play.md` — **start here**: what works, how to start it,
  the one open problem, and the experiment already armed to answer it.
- `docs/15`–`16` — the shard's LZ4 framing and the entity bootstrap that
  gets a controllable hero into the Training Arena.
- `docs/17-solo-ai.md` — the Training Arena population and the first
  nonlethal damage probe.
- `docs/18-heart-of-the-hives-solo.md` — per-mode selection; Alpha Hunters
  solo entry with three visible hunters, accepted.
- `docs/19-hunter-patrol.md` — the shipped MNM navmesh and the first patrol.
- `docs/20-combat-core.md` — server-authoritative damage, death and respawn,
  with the weapon falloff read out of the retail projectile asset.
- `docs/21-hunter-ai.md` — hunter-bot navigation and behaviour.
- `docs/22-creature-ai.md` — the creature `.ai`/`.cdl` traces and state machine.
- `docs/23-maps-and-modes.md` — the level-name hash function and per-map
  readiness for every shipped level.
- `docs/24-hero-and-abilities.md` — hero selection and the HUD ability slots.
- `docs/25-client-input.md` — decoding the client's input RPC, i.e. how the
  server learns where the player is. Everything that reacts depends on it.
- `server/crucible_backend.py` — local live-services backend (`:18876`).
- `server/tls_stub.py` — PlayerAuthorizer TLS logging stub (`:443`).
- `server/match_stub.py` — NovaGameHub TLS listener (`:18877`); speaks the hub
  RPC well enough that the client loads a level.
- `server/shard_stub.py` — server-shard UDP listener (`:18878`).
- `server/client_input.py` — decoder for the client's input RPC; supplies the
  player position every AI behaviour needs (`docs/25`).
- `server/combat.py` — damage, health, death and respawn (`docs/20`).
- `server/hunter_ai.py`, `server/navigation.py`, `server/hunter_patrol.py` —
  hunter-bot behaviour over the shipped navmesh (`docs/21`).
- `server/creature_ai.py`, `server/practice_population.py` — creature
  behaviour and per-map population (`docs/22`).
- `server/solo_modes.py` — solo map/mode profiles (`docs/23`).
- `tools/install_test_ca.py` — trust the stubs' test certs in the Proton prefix.
- `tools/scan_endpoints.sh` — re-runnable endpoint/credential scan.
- `tools/pak.py` — list / extract entries from a `.pak` (read-only).
- `tools/rebuild_pak.py` — replace pak entries without disturbing the rest.
- `tools/patch_menu.py`, `patch_services.py`, `patch_locale.py` — UI patches.
- `tools/test_patch_menu.py`, `test_mode_gate.py` — committed checks.
- `server/test_hubrpc.py` — committed checks for the NovaGameHub envelope.

## Quickstart

Run `python3 tools/install_local_services.py` to start all three local services and
enable them at login. This does not launch the game. See [the current instructions](docs/15-shard-lz4.md).
The historical steps below cover the live-services/menu setup only.

1. Start the backend (it must be up before/during the game):
   `python3 server/crucible_backend.py --port 18876`
2. **Set `net_SslEnablePinning 0` in the game-root `user.cfg`.** The match
   server's certificate is pinned to Amazon's dead GameLift cert, so without
   this the client aborts the handshake no matter what cert you serve. Use
   `--` for comments in that file, not `//`. See `docs/10-match-server-tls.md`.
3. Launch Crucible in Steam (Proton Experimental) and check the menu.
4. After editing the backend, **restart it** — a warm process keeps serving the
   old response shapes and makes correct code look broken.
5. Re-verify a pak patch with `python3 tools/test_patch_menu.py`.

