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

## Layout

- `docs/01-recon.md` — what the client actually is (engine, gems, backend
  stack), with evidence.
- `docs/02-roadmap.md` — phased revival plan.
- `docs/03`–`06` — boot results, static RE, network surface, redirect plan.
- `docs/07-local-backend.md` — the local backend and its pak patches (M1–M4).
- `docs/08-matchmaking-practice.md` — READY flow, TLS match server, practice.
- `docs/09-practice-arena.md` — the mode-select gate and entitlement unlocks.
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
2. Launch Crucible in Steam (Proton Experimental) and check the menu.
3. After editing the backend, **restart it** — a warm process keeps serving the
   old response shapes and makes correct code look broken.
4. Re-verify a pak patch with `python3 tools/test_patch_menu.py`.

