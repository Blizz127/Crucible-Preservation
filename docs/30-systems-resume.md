# Systems continuation, 22 September 2026

## Alienware state check and staging, 5 October

The project was resumed for the owner (request crucible-resume-20261004). A
read-only check of the Alienware found the retail client ready:
- build 5685572 is fully installed;
- the patched gamedata.pak and the loose user.cfg/autoexec.cfg are in place;
- the NET_* log-channel patch on Crucible.exe is still applied (its retail
  backups are kept);
- /etc/hosts sends the dead live-services host to loopback.

The backend, hub and shard had been active since the 3 October boot, with
zero restarts. Their tracked server/tools files matched committed eb4f9b9
exactly. The shard journal showed no client traffic, so the current source
had still never been played. The last client session (29 September, old
checkout) lasted 17 minutes and ended when the owner left via MatchExit.

The shard's systemd drop-ins set `CRUCIBLE_SOLO_HERO=saboteur` (Ajonah). The
main unit file does not show them. **Pick Ajonah in the menu.** A different
hunter leaves the client waiting on the load screen.

Before staging, a shard refusal of a projectile report left no log line. If
the first current-source run had produced no hunter damage, nothing would
have said why. Commit b2538b1 logs each refusal (docs/33).
- `tools/preflight_local_services.sh` checks the services read-only and
  reports the effective hunter.
- `tools/match_acceptance.py` scores a live match against criteria A1–A7.
  On the 29 September logs it reports: entry, three hunters through
  patrol/pursue/attack, 693 fire decisions, no accepted hunter hit, no player
  kill, no GameOver (the player left via MatchExit), and 1023 s connected.

Commit 8cd9bfa was staged into a new directory, `~/Projects/Crucible Revival
8cd9bfa`, and all 345 files were sha256-verified. The full server suite
passed on the Alienware: **351 tests**, including the three installed-asset
tests the devbox cannot run. The tools suite (13 tests) also passed. With no
client running, the installer was run from that tree and the three units
were restarted. Afterwards:
- all three services were active and loopback-only;
- the party route returned 200 and hub TLS 1.3 completed;
- the only change in the effective shard environment was the DIT root path.

The Codex tree is untouched, as a rollback. Evidence is in
[`alienware-state-check.txt`](../snapshots/runtime-20261005/alienware-state-check.txt).
be4ad3e was later staged the same way into its own directory (355 tests
passed on the Alienware) to carry the GameOver fix and the rank probe below.
The 8cd9bfa tree was kept as well.

On the devbox, the units had been regenerated on 29 September with a
`python3` that cannot import lz4. The shard then restarted about 51k times.
They now run from the repository `.venv`, and the installer refuses an
interpreter without lz4 (8cd9bfa).

While the match-end tests for that milestone were being written, a bug
turned up: Alpha Hunters had never ended. Commit 4d4d9af compared the mode
profile's name with the alias `alpha-hunters`, but the profile is named
`DOUBLES_CASUAL`. As a result:
- eliminating the last team never set a result or sent GameOver;
- the post-elimination freeze never engaged.

bea8ada compares the profile itself and adds the first tests for this path.
The later opt-in rank update (above) follows the 24 September match-flow
findings: GameOver (phase 9) puts the client in GameOver unconditionally and
clears its rejoin data, and the HUD shows victory only when it receives
rank 1.

### Next milestone: one complete Alpha Hunters match

These are the acceptance criteria for the current source on April2018 with
the three local Marine bots. Scored with `tools/match_acceptance.py`; "eyes"
means only the player can confirm the item.

| # | Criterion |
|---|---|
| A1 | Home → Alpha Hunters → READY reaches GamePlay; population spawned and input decoded |
| A2 | Marines are visible and moving with animation, without sliding, sinking into terrain or overlapping (eyes) |
| A3 | A Marine visibly fires: shot animation and projectile (eyes) |
| A4 | A hunter hit on the player is accepted and the HUD drops below full (HUD: eyes) |
| A5 | A player hit kills a Marine; it plays a visible death and stops moving and firing (death: eyes) |
| A6 | Team elimination reaches GameOver; the end screen returns home; the next READY works without restarting a service |
| A7 | 10 minutes or more connected, no crash, no shard restart during the match |

After that come the postgame results flow, backend persistence (identities
are still stubs and state is volatile), and the retail-fidelity target. That
target is HOTH_GUIDE on HotH_02, the only shipped bot mode
(`snapshots/retail-20260923/PLAN.md`).

## Alienware live run, 29 September

The owner authorized use of the Alienware retail client. Steam build 5685572
launched through the home screen into Alpha Hunters on April2018. Its loopback
backend, hub and shard were already active. At 07:21 CDT the shard logged
three Marine hunters, decoded player input, transitioned each hunter to
`attack`, and logged primary fire against player entity `01000002` at 6.2,
14.1 and 23.7 m. The client HUD showed `REMAINING 4` and 800/800 health. A
later screenshot still showed 4 remaining and 800/800, with no Marine in that
camera view. The excerpt and screenshot are
[`alienware-alpha-hunters-live.log`](../snapshots/runtime-20260929/alienware-alpha-hunters-live.log)
and [`alienware-alpha-hunters-live.png`](../snapshots/runtime-20260929/alienware-alpha-hunters-live.png).

This confirms retail-client entry, input, and server-side AI/fire decisions.
It does **not** confirm visible Marine models, replicated projectile effects,
death, respawn or win/loss presentation. The Alienware checkout was at
`d859ca3` with uncommitted edits; this checkout was at `36ac71d`. No remote
files were overwritten, and no service was restarted during the active run.
The old worktree remains preserved. A fresh current-source match and its
presentation checks remain pending.

After the match returned to the home screen, committed source `eb4f9b9` was
installed into a separate Alienware code directory, preserving its older dirty
checkout. The managed backend, hub and shard units now start from that tree;
the shard enables bot-hit reports, the explicit death metadata probe, fire,
movement/combat animation and the DIT transport, and all service sockets bind
to `127.0.0.1`. The game home screen showed Alpha Hunters `READY`
([screenshot](../snapshots/runtime-20260929/alienware-alpha-ready.png)). No
new GamePlay events followed the attempted Ready key. The Crucible window was
not foreground, so input to unrelated desktop windows was avoided. A fresh
current-source match and its presentation checks remain pending. The current
backend party route returned 200 (159 bytes), the hub completed a TLS 1.3
handshake with its IP certificate verified for `127.0.0.1`, and all three
service listeners remained loopback-only. The DIT transport directory exists
but contains no tendency file, so no DIT motion adjustments were active.
The staged tree's focused server suite also passed on Alienware: 182 tests,
including combat lifecycle, hunter AI/fire, animation/death replication and DIT
motion policy. One `ResourceWarning` came from `rebuild_pak.py` reading the
installed partial April2018 `level.pak`; no game assets were modified.

Continues Claude's unfinished event-state investigation on
`solo-ai-and-combat`, starting at `d859ca3`. The existing dirty work was
preserved. The resumed Claude processes were idle before edits began.
No game executable or pak was patched, and no commit or push was made.

## Working server paths

- Hunter separation uses the authored 2 m margin and handles coincident
  positions correctly. Separation yields to each bot's navigation surface.
  Grounded positions are used consistently by the server, returned steps,
  shot origins and outgoing Location records. This does not prove wall
  clearance or prevent every possible invalid path segment.
- Dead hunters stop moving/firing; dead players stop being AI targets.
  Suspended hunters advance their clocks so revival cannot cause movement
  to catch up over their entire dead interval. Creature AI is recreated when
  the combat simulation respawns a creature; Practice spitters use their
  authored `spitter_noaggro` respawn profile.
- Hit/aggro timestamps now use the same session-relative clock as timer
  ticks. The previous uptime/session-time mismatch could schedule a six
  second respawn hours into the future. Tests exercise the real
  reported-hit-to-game-clock path with a nonzero session epoch.
- Death metadata has a separate tested encoder and optional combat wiring.
  Health's metadata record is an atomic leaf: adding field-name bits inside
  it, as the earlier draft proposed, is incorrect. See [docs/27](27-damage-and-death-events.md).
- Marine NPC fire has a tested Hybrid activation encoder and optional shard
  wiring. Marine primary uses allowed-projectile slot **4**. The activation
  array has ten slots and no Size field; each element has three atomic
  children. Relative creation time is zigzag ULEB128, and two flags share one
  boolean byte. See [docs/29](29-weapon-fire-replication.md).
- Animation now has a tested scalar encoder and an opt-in movement-state
  policy. Native client code derives blend velocity from interpolated Location
  motion, but independently requires replicated record 16 for idle/move state.
  The policy emits 0 while moving and 1 after 100 ms stationary, using bot
  speed as an explicit proxy for native input intent. See [docs/28](28-animation-state.md).
- A standalone hit-reaction encoder implements component 80's explicit index
  and yaw. It has no firing/damage policy or live integration. Damage-floater
  and incoming-indicator formats are documented, with consumer rules still open.

## Opt-in settings

The existing AI/combat settings continue to control simulation. Source defaults
remain off; `tools/install_local_services.py` now opts the managed local shard
into the Alpha Hunters bot-play profile, including the previously opt-in fire,
animation presentation and explicit death-metadata probe. The probe uses
HealthChangeSource **0 = Unknown** and AbilityName **51 = marine_primary**,
resolved from the executable's static-name prefixes (docs/27). Whether this
metadata invokes a visible native death callback remains unverified. The
source-level defaults remain off.

| Setting | Behavior |
|---|---|
| `CRUCIBLE_SOLO_FIRE=1` | Publish Marine primary activations, at most one per clock packet, advancing each shooter's ten-slot ring. |
| `CRUCIBLE_SOLO_MOVEMENT_ANIMATION=1` | Publish the separate 0/1 idle/move state for live Marine bots; the client continues deriving blend velocity from Location. |
| `CRUCIBLE_SOLO_COMBAT_ANIMATION=1` | Publish `is_dead`, a one-update `using_primary1` pulse per simulated shot, and `is_aiming` during ATTACK for Marine bots (docs/28). |
| `CRUCIBLE_SOLO_DEATH_EVENTS=1` | Include explicit death source, ability and killer metadata with zero health. The managed Alpha Hunters service enables this probe. |
| `CRUCIBLE_SOLO_DEATH_SOURCE_ID`, `CRUCIBLE_SOLO_DEATH_ABILITY_ID` | Both required when the death probe is enabled; unsigned 16-bit wire IDs, accepted in decimal or `0x` notation. Managed profile: `0` (Unknown), `51` (`marine_primary`). |
| `CRUCIBLE_SOLO_MATCH_RANK=1` | After an Alpha Hunters result, publish the local `PlayerStats.Rank` (1 = win, 2 = loss) on alternate GameOver ticks, so the HUD can show victory instead of its 5 s defeat fallback. The managed profile enables it; that the HUD reads this property is inferred (match-flow 03). |

Death metadata is a probe configuration, not per-weapon damage attribution.
The selected IDs must be appropriate to the experiment. The encoder tests
use synthetic IDs only to test byte layout. Returning health to maximum does
not establish the client's death-animation/reset semantics; a second death
with identical metadata may not generate another change notification.

Firing uses the corrected server character origin as its fire/ideal origin.
Exact muzzle-space origins, animation coordination, collision/hit authority,
and projectile loss/retry behavior remain unresolved. NPC shot events do not
apply invented damage. Disabling either new flag restores the previous wire
path for that feature.

Simultaneous shots are queued with their original creation timestamps and
discarded after the projectile's authored lifetime. The one-activation cap
remains a conservative probe policy. The UDP provider-to-factory path is now
source-proven: each EntityMessage gets a fresh bool cursor (see `docs/29`).

Never restart the shard during a match: losing its connection state previously
caused the client's auto-rejoin flow to hang. The menu-selected hunter must
match `CRUCIBLE_SOLO_HERO` in the shard configuration.

## Inspection tools

`inspect_anim_graph.py` now reads directly from a pak using the actual pak
reader contract. Its regression test failed on the old tuple/byte-name
handling; the repaired command reads the installed Marine graph (35
parameters, SHA-256
`7a491d06665129b18d9d6b407e9382705e33394a099bc9d787d06e09dcfed083`).

`inspect_fire_bakednames.py` resolves projectile request lists and the static
prefixes preceding baked names. An asset-file index is not automatically a
wire ID. For example, the attachment provider begins with `None`, `Camera`.

`inspect_match_startup.py` additionally captures AnimationParameter (13),
DamageFloater (36), HitReaction (80), and IncomingHitIndicator (81) graphs,
including autonomous feedback graphs. Captures now identify the executable
hash, image base and UTC observation time. The tool is read-only and still
requires an initialized in-match client.

## Validation and remaining runtime gate

Baseline: 248 server tests passed before implementation. Focused regressions
cover separation, corrected bot/shot positions, dead actor filtering,
creature revival, explicit death records, per-shooter activation rings, and
source-graph traversal. Final checks passed: **297 server tests**, **9 tools
tests**, inspector syntax checks, and `git diff --check`. Existing unclosed
file `ResourceWarning`s from older asset readers remain; no test failed or
was skipped to obtain these results.

The client was launched through Steam and the Alpha Hunters menu was observed.
It was no longer running when the extended schema capture was attempted.
No new in-match animation graph or visible death/firing acceptance was obtained.
After the full suites passed and a fresh process-map check found no running
Crucible client, the local shard was restarted with its existing configuration
(Alpha Hunters, Ajonah, AI and combat enabled; new fire/death probes disabled).
Startup navigation prewarming and the loopback UDP listener were checked.
The next runtime step is a normal Alpha Hunters entry with the configured
hunter, a read-only graph capture, and separate visual tests of each probe.

### Later read-only capture completed

At 17:46:27 and 17:47:32 UTC the user entered Alpha Hunters with Ajonah.
Two captures from the same live client now establish component 13's full
34-node graph and 26 property indices on Ajonah and all three Marine bots.
They also capture the autonomous damage-floater/hit-indicator graphs and
confirm the previously decoded projectile graph. See `docs/28` and
`snapshots/systems-20260922/animation-capture-manifest.json`.

The schema-capture gate is complete. No runtime writes, injected packets,
service restarts, or new gameplay behavior were introduced during this
observation. Visible death/fire and animation acceptance remain pending.

### Parallel decoding and implementation after capture

Three parallel tracks resolved the animation serializer, movement producers,
and damage feedback formats. The parent separately traced and independently
reviewed the UDP serializer factory path. Reproducible evidence is saved in:

- `animation-wire-map.json`: all 26 property roots mapped to actual generated
  serializer name calls and descriptor slots; 23 scalar properties supported.
- `movement-animation-source.json`: native zero initialization, the 0.1-second
  thresholded 0/1 policy, and Location interpolation → client-local velocity.
- `entity-serializer-scope.json`: correct provider subobject/vtable, callback
  path, fresh per-message serializer construction and bool reset.

These files are under `snapshots/systems-20260922/`. The source maps supersede
the earlier claim that remote character velocity had no native producer and
the earlier unresolved UDP bool-boundary note. Health requests were also
corrected to atomic 65-byte network values, with a one-byte Size, packed
positions and an explicit flags byte; the old nested/debug-field interpretation
was incorrect.

Final offline validation: **326 server tests and 9 tools tests passed**.
Seven new shard tests cover the movement policy and combined Location →
animation → fire → health RelativeId chaining. The fire tests explicitly
isolate the movement flag, including a check with that flag inherited as `1`.
The two pinned-build static inspectors reproduce their saved source maps.

The client was no longer running after the read-only captures. The shard remains
PID `1331527`, started before this capture/decoding pass. No new presentation
flag was enabled, and no service was restarted during this pass. The movement
implementation is available on disk for a later controlled live test; its
visible effect is **NOT_OBSERVED**. Hit-reaction index/yaw semantics, feedback
ring/consumer policies, SyncInfo meanings, and visible death/reset behavior
remain open.

### Combat animation policy, evening 22 September

The earlier parallel research reports were not saved, so the wiring trace was
redone from the asset and executable: see docs/28, "Combat-state wiring".
`CRUCIBLE_SOLO_COMBAT_ANIMATION` (default off) implements the policy.
Offline: **333 server tests and 9 tools tests passed**; `git diff --check` is clean. The
running shard was not restarted, and the flag has not been enabled live.

### Local service preparation, 29 September

With no client or active match running, `tools/install_local_services.py` was
run from the repository `.venv`. Backend, hub, and shard are active; TCP
18876/18877 and UDP 18878 bind to `127.0.0.1`. The shard prewarmed the retail
navigation mesh in 4.69 seconds and is waiting for client packets. The managed
shard's active systemd environment confirms Alpha Hunters, bot AI/combat,
Marine fire, movement/combat animation, the explicit death metadata probe,
hunter hit reports, and the DIT transport root. A read-only party descriptor
returned HTTP 200 and the hub completed a local TLS 1.3 handshake. Exact
service, socket, and smoke evidence is in
`snapshots/runtime-20260929/local-services.txt`.

The installer pre-creates the DIT transport root because DIT sync-down skips
targets whose root directory does not exist. This lets DIT publish an approved
tendency before the first 60-second motion flush. The root exists now, but no
shared tendency document or DIT hub service is present on this host.

Fresh offline verification on 29 September passed **182 focused server tests**,
the separation suite independently (5 tests), and the two inspector suites
(6 tests). Full command output is in
`snapshots/runtime-20260929/offline-tests.log`; only the existing unclosed-file
`ResourceWarning` was emitted.

Full discovery also ran **348 server tests**: 345 passed and three asset
inspection tests errored because `Crucible.exe`, `misc.pak`, and `levels.pak`
are absent at the configured install path. Full tools discovery passed all 9
tests. A read-only search under `/home/blizz` found no complete retail install;
the only game file found was April2018's `level.pak`. Exact output is in
`snapshots/runtime-20260929/full-discovery.log`.

There is still no Crucible client process or graphical display in this
session, so visible death, respawn, fire, and animation behavior has not been
accepted. No active DIT tendency file exists at the configured transport root;
human-derived patrol cadence stays gated until DIT sync-down and approval.
