# Crucible Revival

Fan preservation project for Amazon's **Crucible** (Steam AppID `1057240`,
last build `5685572`). Goal: get the client booting and, eventually,
playable again via a community backend — same playbook as other dead-game
revivals (local server emulator + traffic redirect via hosts file / firewall
rules, client patched only where unavoidable).

> Status: **Phase 3 — menu alive, no gameplay yet.** Client downloaded and
> hashed (21 GB, `5685572`); boot log captured; the local backend populates the
> menu (battlepass, challenges, store) and the LAN READY flow runs to a clean
> Timeout modal. Match server speaks TLS and rejects untrusted certs, so real
> gameplay still needs a community server. Practice Arena mode entry was
> unblocked last — see `docs/09-practice-arena.md`.

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
- `docs/10-match-server-tls.md` — cert pinning (the real blocker) and the first
  captured NovaNet frame.
- `server/crucible_backend.py` — local live-services backend (`:18876`).
- `server/tls_stub.py` — PlayerAuthorizer TLS logging stub (`:443`).
- `server/match_stub.py` — LAN match-server TLS listener (`:18877`); records the
  client's first bytes for the NovaNet protocol work.
- `tools/install_test_ca.py` — trust the stubs' test certs in the Proton prefix.
- `tools/scan_endpoints.sh` — re-runnable endpoint/credential scan.
- `tools/pak.py` — list / extract entries from a `.pak` (read-only).
- `tools/rebuild_pak.py` — replace pak entries without disturbing the rest.
- `tools/patch_menu.py`, `patch_services.py`, `patch_locale.py` — UI patches.
- `tools/test_patch_menu.py`, `test_mode_gate.py` — committed checks.

## Quickstart

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

