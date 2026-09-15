# Local backend (Phase 1 — menu live services)

Status: implemented, pak rebuilt with M1–M4 (3131 entries CRC-verified,
backup `gamedata.pak.pre-m4`), awaiting in-game verification.

## What was dead and why

With no network, every menu feature behind `window.services` (preloader.js)
hung at three gates:

1. `Ni.config` awaited `Ni.login` (resolves only on native `login.updates`
   with `isLoggedIn:true`) and then one native `serviceconfiguration.get`
   call per service key — the bridge never answers offline, so the promise
   never resolved and every store downstream (`go` season, `tl` dailies,
   `Bo` battlepass, entitlements, offers…) stayed at its empty default.
2. `Ni()` queues all HTTP requests until credentials exist (`$i`, from
   native `credentials.updates`, also never pushed offline).
3. Requests are AWS-SigV4-signed; the region/service scope is parsed from
   the URL hostname.

## The fix (two minimal pak patches, no engine changes)

`tools/patch_services.py` applies to `ui/dist/preloader.js`:

- **P1** `Si=rt(!1)` → dummy local credentials, unblocking the request
  queue. Signatures are still computed (code untouched); the local backend
  accepts any signature.
- **P2** `Ni.config=new Promise(…login…serviceconfiguration.get…)` →
  `Promise.resolve({...})` with all 20 config keys: the 12 service URLs
  point at `http://127.0.0.1:18876/<ServiceName>`, beacons `[]`, sparks
  keys `""`, `stage:"local"`, `region:"us-east-1"`.

`tools/rebuild_pak.py` installs the patched file with raw entry
preservation (local-header backslash names kept; only the patched blob and
its CRC/sizes/offsets change). Backup: `gamedata.pak.pre-services`.
Full-pak CRC verify passes before and after (3131 entries).

## Backend: `server/crucible_backend.py` + `server/crucible_data.py`

Stdlib-only HTTP on 127.0.0.1:18876. Wire rules the client demands:

- gameconfig routes return `{"data": "<json-string>"}` (client JSON-parses twice)
- service routes return plain JSON (client parses once)
- CORS `*` + OPTIONS 204 (UI fetches cross-origin with JSON content-type)

Content (retail IDs from the client's own `ui.loc2.js` locale bundle, so
names/descriptions/icons render as retail; goal counts are seeded):

- seasons: active season `crucible-season1` (2026-08-03 → 2027-02-01,
  `battlepass_s1` → real localized "Season 1" name)
- dailyChallenges: 3 retail dailies + `rerollsLeft`; useReroll swaps one
  daily from a spare pool, stateful; challenges: progression echo with
  seeded partial progress
- ui-challenge-stars / ui-daily-challenge-stars / ui-entitlement-to-achievement:
  60 weeklies (s1_w1..w20), 12 seasonals with real account-icon rewards
- ui-battlepass-tier-entitlements: 30 free + 30 paid tiers, Count = tier*10,
  rewards use pak-verified asset IDs only
- entitlements/list: seeded currencies (23 BP xp → tier 3, 3 keys,
  2500 credits) + account icons (first unlocked, rest locked)
- offers/list + buy (stateful credit grant), getuserinfo, identity
  displayname/icon, level xp curve, moderation stub

## Run

Backend must be up before/during the game (game tolerates it appearing
late — stores retry):

```
python3 server/crucible_backend.py --port 18876
```

Requests log to stderr; 404s reveal any missed route the UI calls.

## Verify (in-game)

1. HOME: battlepass widget shows Season 1, tier 3, star progress.
2. CHALLENGES tab: 3 dailies with progress bars, current-week weeklies,
   seasonals with icons; reroll button swaps a daily (3 keys).
3. BATTLE PASS tab: 30 free + 30 paid tiers, current-tier highlight,
   reward icons render.

## Known limits (honest)

- Goal counts, star seeding, tier reward mapping are approximations —
  server data never shipped in the client.
- `vendor.get` native (store purchase auth) still missing; offers/buy is
  emulated end-to-end locally instead.
- Matchmaking/party/READY: see `docs/08-matchmaking-practice.md`. READY
  now walks the full retail LAN flow to a clean Timeout modal; the match
  server speaks TLS (self-signed rejected), so real gameplay needs a
  community server beyond this scope.

## Follow-ups applied since (see tools/patch_menu.py, patch_locale.py)

- **M1**: suppress the native-timeout DISCONNECTED modal in LAN mode.
- **M2**: resolve `localconnection.loadout.set` so READY proceeds.
- **M3**: LAN endpoint 127.0.0.1:18877 for `matchserver.connect`.
- **M4**: race the boot `analytics-data` invoke against a 3 s local fallback.
  The boot machine only enters `services` (feature-flags fetch → BATTLE PASS
  tab enable) after two NATIVE bridge promises (`data.get` session,
  `buildinfo.get`) resolve; offline they never settle, so the battlepass
  feature flag was never fetched and the tab stayed disabled with no error.
  Only telemetry identity is synthesized, and only if the native bridge stays
  silent, so a working native bridge still wins the race and the online retail
  path is untouched. `Promise.race` does not cancel the loser; the orphaned
  native promise has the same rejection surface as the pre-patch code.
- **L1**: added missing `matchmaking.error.{failed_to_set_loadout,success}`
  locale strings.
- Backend also serves party (`/status`, `/describeParty`,
  `/getPartyForPlayer`, `/leaveParty`, kick/promote, gameRule,
  customgames stubs) and sysmsgs (`gamemodes.json`, `motd.json`).
