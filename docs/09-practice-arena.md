# Practice arena reachability — the mode gate (2026-09-15)

## Why the practice button never appeared

`docs/08` recorded the in-menu practice button as "the remaining path" to
gameplay. It was not clickable because **it did not exist**: the mode-select
list is not shipped in the pak and is not static. It is built at runtime by a
derived store in `ui/dist/perks.js` from two backend inputs, and both were
wrong — so the list rendered empty and no mode button (practice included) was
ever reachable.

The gate, verbatim from `ui/dist/perks.js` (build `5685572`):

```js
const G = derived([sysmsgs, gamemodeStore], ([e, a]) =>
  !e || !a.size ? [] :
  p.TUTORIAL.entitlementId.find(id => a.has(id) && a.get(id).amount)
    ? Object.entries(e.modes).reduce((out, [name, enabled]) => {
        if (!p[name]) return out;
        const { entitlementId } = p[name];
        let want = entitlementId;
        if (Array.isArray(entitlementId))
          want = entitlementId.find(id => a.has(id) && a.get(id).amount > 0);
        const owned = a.get(want) || false;
        out.push(Object.assign(Object.create(null), p[name],
                 { gametype: name, unlocked: owned.amount > 0, enabled }));
        return out;
      }, [])
    : []);
```

- `p` — the gametype catalogue compiled into `ui/dist/preloader.js` as `Hl`
  (9 entries). Not in any config file, not server-supplied.
- `a` — the entitlement store named `"Gamemode"`.
- `e` — the body of `/CrucibleLiveSystemMessages/gamemodes.json`.

Both `!e` and `!a.size` short-circuit to `[]`, and the `TUTORIAL` lookup is a
second master gate. Three separate gaps had to be closed.

## The three gaps

1. **`/gamemodes.json` returned `{"messages": []}`.** The store reads `e.modes`,
   so `Object.entries(undefined)` throws inside the derived callback and the
   list is empty. The payload must be `{"modes": {<GAMETYPE>: <bool>}}` keyed
   exactly like `Hl`.
2. **`/gameConfigData/entitlements` returned `[]`.** This is the entitlement
   *dictionary*: it is what creates the `"Gamemode"` category store (and maps
   entitlementId → category). With it empty, `gamemodeStore.size === 0`, so
   `!a.size` short-circuits regardless of what is granted.
3. **`/entitlements/list` granted no `mode_*_unlock` ids.** The client forces
   catalogue amounts to 0 and re-fills them from the quantities list, so the
   grant must appear here, not in the catalogue.

`enabled` is a real boolean (the mode card computes `disabled: !enabled`), so
the `modes` values are `true`/`false`, not objects.

## Gametype catalogue (`Hl`, build 5685572)

`Ll` resolves to the literal `"mode_standard_unlock"`.

| Gametype | queue | required entitlements | unlocked by |
|---|---|---|---|
| `TUTORIAL` | `TUTORIAL` | `mode_initial_unlock`, `mode_tutorial_unlock` | either (master gate) |
| `HOTH_GUIDE` | `CRUCIHEARTS_GUIDE` | `mode_initial_unlock` | that one |
| `PRACTICE` | `PRACTICE` | `mode_standard_unlock`, `mode_practiceArena_unlock` | either |
| `CONQUEST_CASUAL` | `CONQUEST_CASUAL` | `mode_standard_unlock`, `mode_harvesterCommand_unlock` | either |
| `CRUCIHEARTS_CASUAL` | `CRUCIHEARTS_CASUAL` | `mode_standard_unlock`, `mode_heartOfTheHives_unlock` | either |
| `DOUBLES_CASUAL` | `DOUBLES_CASUAL` | `mode_standard_unlock`, `mode_alphaHunterDoubles_unlock` | either |
| `CUSTOM_CRUCIHEARTS` | `CUSTOM_CRUCIHEARTS` | `mode_standard_unlock` | that one |
| `SOLO_BOTS` | `SOLO_BOTS` | `""` (empty string) | never — ungateable |
| `SOLO_CASUAL` | `SOLO_CASUAL` | `""` | never — ungateable |

`SOLO_BOTS` / `SOLO_CASUAL` carry `entitlementId: ""`, so the gate yields
`unlocked: false` for them permanently. They are omitted from the unlocked
list by design, not by oversight.

## The fix

- `server/crucible_data.py` — `GAMEMODES`, `MODE_UNLOCKS`,
  `MODE_UNLOCK_CATEGORY`, plus `gamemodes_payload()` and
  `entitlement_catalog()`; `base_entitlements()` now grants every
  `mode_*_unlock` at amount 1.
- `server/crucible_backend.py` — `/gamemodes.json` returns the modes object;
  `/gameConfigData/entitlements` returns the catalogue.
- `tools/test_mode_gate.py` — replays the gate logic above against the live
  backend routes, so this contract cannot silently regress. Includes a guard
  that the old `{"messages": []}` payload still reproduces the bug.

## Gotcha: stale backend process

`snapshots/quit-after-notready-20260915.log:403` contains
`404: Not Found for .../CrucibleLiveSystemMessages/gamemodes.json`. That was
**not** a routing bug — the backend process serving that run predated the
route. Proof: its `offers/list` response still carried
`battlepass_s1_premium`, which the current code no longer emits. Restart the
backend after editing it; a warm process silently serves the old shapes and
makes correct code look broken.

## What the PRACTICE click actually does

Traced through `ui/dist/menu.js`; it does **not** load a level locally.

1. `SELECTED` → `select` state → `invoke{id:"gamerule"}` →
   `POST /CrucibleLivePartyService/gameRule/<rule>` (body `{gameRule:<rule>}`).
   Implemented in `perks.js`; our backend already echoes it.
2. The mode machine then resolves to `hidden` and the parent goes back to
   HOME/lobby. **No level load, no map change.**
3. From the lobby, READY runs the matchmaking machine, which is selected by one
   flag (`menu.js`): `serversource: always [ {cond:({offline}) => offline,
   target:"lan"}, {target:"services"} ]`.
4. `localconnection.get` — patched by M3 to return
   `{lan:true, host:"127.0.0.1", ipAddress:"127.0.0.1", port:18877}` — sets
   `offline:true`, so the **LAN** machine wins, not the GameLift one.
5. That machine's `connecting` state invokes
   `matchserver.connect {type:"lan", ipAddress:"127.0.0.1", port:18877}`.

So practice needs **no GameLift, no GameSparks** — good news — but it does need
something to answer that port, and the client is the TLS client. This is the
same wall `docs/08` hit, now located precisely: everything routes through
`127.0.0.1:18877`, and the only missing piece is a server that speaks the
client's protocol.

`server/match_stub.py` is that listening post: TLS on `127.0.0.1:18877`, cert
self-signed for `127.0.0.1` (+`localhost`), logs the handshake result and every
byte the client sends as hex+ascii. The application protocol is still unknown,
so the stub records rather than parses — that recording is the prerequisite for
any NovaNet server.

## Still open

- The NovaNet/lan app protocol above TLS. `match_stub.py` exists to capture the
  first bytes; nothing has been captured from the real client yet.
- Whether the client even trusts `match.crt` once installed — untested, since
  it needs a host run.
- Nothing here explains the `+map practicearena` early crash from `docs/08`;
  that is a separate failure and is still unexplained.
- The entitlement catalogue is non-empty for the first time in this project.
  Previously it was `[]`, so no entitlement quantity was ever merged into any
  category store. Other screens that read category stores (`Currency`,
  `AccountIcon`, `BattlePass`) should be re-checked for regressions.

## Verify (in-game)

1. `python3 server/crucible_backend.py --port 18876` — start it **fresh**.
2. Launch the game. Expect no `gamemodes.json` error in the client log, and
   `/gameConfigData/entitlements` + `/gamemodes.json` in the backend stderr.
3. PLAY → the mode-select screen should now list 7 enabled modes with
   PRACTICE/TRAINING among them (TRAINING label comes from
   `play.modeselect.practice.title`).
4. Click PRACTICE → should return to the lobby (not a load). Press READY.
5. With `server/match_stub.py` running and `match.crt` installed via
   `tools/install_test_ca.py`, `server/match_stub.log` should show either the
   handshake and the client's first bytes, or the trust rejection.

