# Systems continuation, 22 September 2026

Continues Claude's unfinished event-state investigation on
`solo-ai-and-combat`, starting at `37e9d86`. The existing dirty work was
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
shard unit enables Alpha Hunters, bot AI/combat, Marine fire, movement/combat
animation, the explicit death metadata probe, hunter hit reports, and the DIT
transport root. Exact service and socket evidence is in
`snapshots/runtime-20260929/local-services.txt`.

There is still no Crucible client process or graphical display in this
session, so visible death, respawn, fire, and animation behavior has not been
accepted. No active DIT tendency file exists at the configured transport root;
human-derived patrol cadence stays gated until DIT sync-down and approval.
