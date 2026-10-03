# Creature AI — authored behaviour traced, targeting implemented

Scope: make the Training Arena creatures behave. `17-solo-ai.md` established
that the two spitters and eight RoboDummies spawn upright and inert, and that
the spitter is authored stationary (`MovementEnabled` and `CanPatrol` both
false). Restoring the creature therefore means target acquisition, facing,
telegraph and attack cadence, not roaming. Nothing below has been observed in
a running client; it is asset-faithful, not verified.

## New read-only inspectors

`tools/inspect_creature_ai.py` parses a binary ObjectStream v3 asset and
resolves each element's CRC32 field name against the identifier strings in the
installed executable, so every constant quoted here has a field name, a byte
offset and the asset's sha256. `tools/inspect_creature_cdl.py` builds on it and
prints a `.cdl` gameplay script as a node graph: enter/exit trigger events, the
node list with hidden-literal values resolved, designer comments, and the slot
connections that give execution order. Neither tool writes or launches
anything. Their full output for this trace (decoded `.ai` asset dumps and
`.cdl` script graphs) is verbatim retail content, so it is kept out of the
repository; the findings are summarised below, and only the string census
(`level-creature-census.json`) is in `snapshots/creature-ai-20260921/`.
Re-run the two tools against your own install to regenerate the rest.

The CRC dictionary is exact, not guessed: `crc32("sensingradius")` resolves to
the single string `SensingRadius` in `Crucible.exe`, and the same lookup
reproduces the nine spitter values already recorded in
`combat-asset-fields.json` at the identical offsets.

## What `ai/spitter.ai` actually says

`ai/spitter.ai` (sha256 `e8433ab3…`, 5149 bytes) is a flat parameter block, not
a behaviour tree. The blocks are DormantStateParams, SentryTransitionParams,
SentryStateParams, WakeUpParams, IdleParams, PatrolParams, FleeParams,
DecidingToFleeParams, MovementPararams (engine's spelling), HeadLookParams,
then the aggro/alert/attack scalars. States are the engine's, driven by these
parameters; the script assets below carry the per-event logic.

Values used by the implementation, with offsets:

| offset | field | value |
| --- | --- | --- |
| 0x0f98 | StartDormant | false |
| 0x0173 | WakeUpParams.Duration | 1.0 |
| 0x00c6 | SentryTransitionParams.SensingRadius | 20.0 |
| 0x0702 | MovementPararams.MovementEnabled | false |
| 0x0205 | PatrolParams.CanPatrol | false |
| 0x0faf | AggroOriginOffsetLocal | (0,0,1) |
| 0x0fd2 | AggroTargetOffsetVertical | 1.0 |
| 0x0fec | AggroCheckLineOfSight | true |
| 0x1003 / 0x101d | AggroRadiusNear / Far | 5.0 / 20.0 |
| 0x1037 | AggroAngleFar | 360.0 |
| 0x1051 | DeaggroRadius | 25.0 |
| 0x1085 | ForcedAggroDurationOnDamage | 4.0 |
| 0x10df | TargetTypes[0] | "Player" |
| 0x111e..0x1224 | Alert radii 4/20, delay 0.25..1.0, moratorium 4.0 | |
| 0x11b2 | AlertTypes[0] | "Spitter" |
| 0x123e | AttackDelayForNewTargets | 1.0 |
| 0x1258 | MinTimeBetweenAttacks | 0.0 |
| 0x1272 | TimeWithoutLoSToForgetCurrentTarget | 8.0 |
| 0x12b7 | Attacks[0].Angle | 15.0 |
| 0x12d1 / 0x12eb | Attacks[0].RangeMin / RangeMax | 0.1 / 40.0 |
| 0x131f | Attacks[0].Duration | 3.0 |
| 0x136d / 0x1387 | RotationDisallowedPeriod Min / Max | 0.5 / 1.0 |
| 0x13a1 | AbandonInfiniteAttackIfTargetOutOfMaxRange | false |
| 0x13b8 | TimeWithoutLoSToTargetUntilAbandoningAttack | 0.0 |

Two consequences worth stating because they look like bugs and are not:
the attack range (40) is larger than the deaggro radius (25), so a spitter
keeps shooting a target it is about to forget; and the attack is never
abandoned mid-swing, because both abandon controls are false/zero.

`slices/creatures/spitter/spitter.dynamicslice` (sha256 `60efed22…`) names
`ai/spitter.ai` at 0x1dcf — Practice's creatures run the *aggressive* profile,
not the `_noaggro` variant every other shipped level references. The same slice
carries the 400/400 health resource (0x4c1c/0x4c36), EntityTypeIds
Spitter/Monster and the CombatTarget flag.

## The attack timeline is in the attack script, not the .ai

`cdl/creatures/spitter/spitter_attack.cdl` (sha256 `fd82fba9…`) is entered by
`AIAttackEvent` (fields AttackIndex, Target) and exited by
`AIAttackTerminatedEvent` (OldTargetEntityId, OldTargetOutOfRange). Following
the slot connections:

```
AIAttackEvent -> Condition -> ExternalGet CrucibleHealthComponent.IsDead on the
target -> Condition "If the target is NOT dead..." -> EndEnterTriggerChecks
-> HasBuff Cloak (-> RemoveBuff Cloak) -> Set AnimationParameterComponent
.using_primary1 = True -> Delay 1.0 -> VectorMath2 "Offset the projectile
source pos" (+Z 2.75) -> ExternalGet LocationComponent…Position of the target
-> Delay 0.1 -> Set using_primary1 = False -> CreateProjectile
(spitter_projectile.projectile, UseManualTargeting, TargetPosition =
AIComponent.AimPosition) -> Delay 1.25 -> SphereShapeEntityQuery radius 10,
Player, CheckLineOfSight -> ForEach/InsertContainerElement/GetContainerSize
-> Condition -> MakeAIAlertedEvent
```

So, measured from the attack's start: **1.0 s telegraph** (the animation
parameter goes true and the creature is visibly winding up), **+0.1 s** to the
projectile, and **+1.25 s** after that (t = 2.35) the "if a player is close to
me when I'm still active, then alert other spitters around me" sphere query
that fans `AIAlertedEvent` out at radius 10. The attack itself terminates at
the authored 3.0 s, and `MinTimeBetweenAttacks` is 0, so the next attack can
begin on the following tick. Those four numbers — 1.0, 0.1, 1.25, 10.0 — are
hidden-literal values in the graph's blackboard, not estimates.

Supporting scripts, same method:

- `spitter_spawn.cdl`: on OnSpawnEvent, apply an `Invulnerable` buff with
  duration 1e8 if not already present. (`spitter_invulnerability.cdl` is the
  counterpart; not traced.)
- `spitter_onalerted.cdl`: on `AIAlertedEvent`, play `play_spitter_vocal_alert`;
  if the game mode flag `CreaturesScanWhenAwoken` is set, query Players with
  the `CombatTarget` flag inside radius 20 and apply 1 damage to each (retail's
  way of dragging a nearby player into combat); validate the aggressor, else
  query radius 25 and take the first valid entity, then raise
  `AITargetChangedEvent`.
- `spitter_onraisealert.cdl` ("I alert another entity"): same audio, same
  radius-20 CombatTarget query, same 1 damage.
- `spitter_ontargetchange.cdl`: audio only.

`spitter_primaryfire.ability` points at `cdl/creatures/spitter/on_fire.cdl`,
which is **not present in the installed misc.pak**; the ability's cooldown is
0.0. The attack script above spawns the projectile directly, so the missing
script does not block this work, but it is an unexplained gap.

`spitter_projectile.projectile` (sha256 `66a12ca5…`): Bullet behaviour, speed
20 u/s, gravity −0.5, lifetime 5 s, dynamic/static collider radius 0.5/0.1,
target filter Enemies, `HealthChangeImmediateOnHit` = (10.0, 10.0),
`HealthChangeOverTimeOnHit` = (100, 100, 25), CriticalMultiplier 1.0, deflectable.
Damage interpretation is `combat.py`'s call, not this document's.

## Other creature profiles

Same extraction, for comparison and for the other maps (full dumps in the
snapshot directory):

| profile | dormant | moves | aggro near/far/angle | deaggro | attack delay | attack angle/range/duration |
| --- | --- | --- | --- | --- | --- | --- |
| spitter | no | no | 5 / 20 / 360 | 25 | 1.0 | 15 / 0.1–40 / 3.0 |
| spitter_noaggro | no | no | 0 / 0 / 360 | 60 | 1.0 | 15 / 0.1–60 / 3.0 |
| megadrone | **yes** (0.7 s wake) | yes, 9 u/s chase, patrols | 7 / 25 / 115 | 10 | 0.25 | 65 / 0–15 / 2.7 |
| megadrone_beamer | yes | yes, 13 u/s | 10 / 20 / 300 | 20 | 0.25 | 65 / 0.1–20 / 4.0 |
| snurtle | no | yes, 4.64 u/s | 5 / 15 / 120 | 30 | 0.0 | 90 / 0–5.5 / 3.15 |
| sentry_laser | no | no | 0 / 15 / 360 | 15 | 1.0 | 70 / 0.1–20 / 0.0 |

`server/creature_ai.py` ships spitter, spitter_noaggro, megadrone and snurtle,
and the megadrone and snurtle attack scripts are now traced too:

- `cdl/creatures/megadrone_v2/megadrone_v2_attack0.cdl` (sha256 `7c8eec7c…`,
  bound by `slices/creatures/megadrone/megadrone_v2.dynamicslice`):
  `using_primary1 = True` → **Delay 0.65** → sample the target position →
  VectorMath2 "Offset the target by 1m on the z axis" → `CreateProjectile
  megadrone_projectile.projectile` with `UseManualTargeting` false → audio →
  **Delay 1.6** → `using_primary1 = False`. The attack raises no alert of its
  own; `megadrone_onattackalertothers.cdl` is a separate `AIRaiseAlertEvent`
  script (Delay 3.0, then a 25 m Megadrone query with line of sight off).
- `cdl/creatures/snurtle/snurtle_attack0.cdl` (sha256 `1658e63d…`): checks its
  *own* health as well as the target's, then `using_primary1 = True` →
  **Delay 0.3** → false → **Delay 0.8** → particles →
  `CircleShapeEntityQuery` radius 8, Player, **line of sight off** → per hit
  entity a camera shake and a `MeleeAttack snurtle_primary_attack.melee` →
  **Delay 1.25**. So the snurtle is a melee area attack at t+1.1, not a
  projectile.

All three creatures therefore land their hit around 0.65–1.1 s into an attack
whose authored duration is 2.7–3.15 s.

## Population beyond Practice

A string census of every installed `level.dynamicslice`
(`level-creature-census.json`; a name that occurs exactly once is the spawner
archetype in the asset table, not an instance):

- `april2018`: 89 `Spawner_Spitter`, 97 `Spawner_MegaDrone`, 62
  `Spawner_Snurtle`, 47 `Spawner_Hive`, 115 `SpawnNodeMatchStart`
- `HotH_02`: 97 `Spawner_MegaDrone`, 7 `SpitterCamp`, 7 `SpitterColony`,
  8 `SnurtleCamp`, 8 `SnurtleHerd`, 10 `MegaDroneCampAnchor`, 7 `Spawner_Hive`,
  12 `GuideEnemyBotSpawner` — group encounters, not individual spawners
- `tutorial_02`: 10 `Spawner_Spitter`, 4 `Spawner_Snurtle`
- `tutorial_03`: the only level referencing `ai/sentry_laser.ai` and
  `ai/snurtle_noaggro.ai`, plus `TutorialSpitterSpawn`
- `practicearena`: the two SpitterRespawners and eight RoboDummies already
  replicated

`server/practice_population.py` is now a per-map table (`POPULATION`,
`population(level)`, `creatures(level)`) instead of Practice-only constants;
the Practice tuples keep their previous names and values byte-for-byte so
`shard_stub.build_practice_npc_probes` is unaffected. Every non-Practice map
declares an empty population on purpose: their spawner entities have no
observed baked network IDs, and Practice's `entry+2` mapping demonstrably does
not transfer (April2018 needed +787, per `18-heart-of-the-hives-solo.md`).

## What is implemented

`server/creature_ai.py`: `CreatureProfile` / `AttackProfile` / `AttackScript`
hold the authored numbers with their offsets in comments; `Creature` is a
state machine over `dormant → waking → idle → aggro → attacking` driven by an
**injected** time, and `CreatureDirector` runs a whole map's placements. There
is no wall clock, no socket and no randomness in the module. The clock is
asserted monotonic.

Per tick: dormant creatures wake when a target is inside `SensingRadius` and
finish waking after `WakeUpParams.Duration`; an idle creature aggroes on the
lowest-numbered eligible target (inside `AggroRadiusNear`, or inside
`AggroRadiusFar` and within `AggroAngleFar/2` of facing, gated by
`AggroCheckLineOfSight`); an aggroed creature faces the target, deaggroes past
`DeaggroRadius` or after `TimeWithoutLoSToForgetCurrentTarget`, and starts an
attack once `AttackDelayForNewTargets` has elapsed and the target is inside the
attack cone and range band. During the attack the script timeline replays:
rotation is frozen for `RotationDisallowedPeriod`, a `CreatureAttack` event is
emitted at t+1.1, an `alert_nearby` event at t+2.35, and the attack ends at
t+3.0. `note_damage` applies `ForcedAggroDurationOnDamage`.

The combat handoff is the `CreatureAttack` record — creature N attacked entity
X at time T — carrying `kind` (`projectile` or `melee`), `attack_asset`, the
origin (for the spitter, self + Z 2.75), the target position at the moment the
script sampled it (megadrone adds the authored +Z 1.0) and, for a melee
attack, the authored area `radius` (snurtle 8.0). This module deliberately
computes no damage, health, death or respawn.

Rotation for the wire: `facing_yaw`, `pack_rotation`, `pack_position` and
`pack_transform` reproduce the verified packing — the `4f47e10` position
quantisation and the WXYZ CryQuat order corrected in `17-solo-ai.md` — and
`CreatureDirector.rotations()` returns the 12-byte atomic
`LocationComponent.InternalTransform` payload per targeting creature. A test
decodes the packed quaternion and checks the rotated forward and up axes, and
another asserts byte equality with `shard_stub.pack_practice_spawn_transform`
for the position half.

24 new tests in `server/test_creature_ai.py`; `test_practice_npcs.py` is
unchanged and still passes, and the whole `server/` suite is green.

## What could not be proved — the attack/animation replication

The client's visible reaction needs two things: rotation, which is solved
above and uses an already-accepted encoder, and the attack animation, which is
**not** solved.

The attack script drives the animation with
`AnimationParameterComponent.using_primary1`. That component is type 13 with 33
shared record bits (`component-replication-schema.json`) and it is in the
spitter's replicated component set (`SPITTER_SHARED_TYPES`). The client's
animation-parameter registration runs at `7e2bd0..7e3599`; the names are
registered in this order, each with its member offset in the component
(the `lea rcx, [rbx+off]` that precedes each name; `using_primary1` is member
`+0x1dc0` and is registered 8th, at `7e2ee4`/`7e2f0c`):

```
melee_swipe, emote, is_crouching, is_dead, using_medpack, using_quick_melee,
using_primary2, using_primary1, stance, using_ability3, using_ability2,
using_ability1, is_active, is_interacting, ability_target1,
time_since_movement_input, is_aiming, ammo_ratio, spawn_ratio, using_aim_ik,
turn_angle, resource1_ratio, is_falling, using_reload, AimDirection
```

That is registration order, **not** proven wire-record order. The compressed
traversal indices for this component have never been captured — the Health
layout in `live-combat-layout.json` came from a live capture, and the same
capture is the only way to learn this one. So no animation bytes are put on
the wire. `AIComponent` remains two shared bits (root plus
`InVelocityOverride`) and does not replicate `AimPosition`, so the client
cannot be told where a creature is aiming through that component either.

Open traces, in the order they matter:

1. Capture `AnimationParameterComponent`'s replication graph and property
   record indices on the next user-launched run, the way Health's were.
2. `cdl/creatures/spitter/on_fire.cdl` is referenced by
   `spitter_primaryfire.ability` but absent from the installed pak.
3. Line of sight: the server has no collision data, so `visible` defaults to
   true. The authored `AggroCheckLineOfSight` is therefore not honoured, and
   the 8-second LoS forget timer can never fire.
4. `pick(min, max)` returns the low bound so tests are reproducible; retail
   randomises alert delays and the rotation-lock period.
5. Whether the client will animate or accept a creature projectile at all
   without `ProjectileRequestComponent` traffic from the server is untested.

Nothing here has been seen in-game. No service was restarted, no file under
the Steam install was modified, and the game was never launched for this work.

## Integration notes

The parent agent wires this; `creature_ai.py` and `practice_population.py` do
not import `shard_stub`.

1. `shard_stub.build_practice_npc_probes` — no change required. It still
   imports the same five names from `practice_population`, which keep their
   values. Optionally rewrite it over `practice_population.population(
   solo_modes.selected().level)`, using each `Placement.entity_id`,
   `slice_hash`, `slice_entry`, `shared_types`, `local_id` and `yaw`; the
   placements already encode the accepted counters (`0x01000003/4` for the
   spitters, `0xfe00004b..52` for the dummies) and the 180-degree dummy yaw.

2. New `shard_stub.creature_director(state)` — build once, right after
   `practice_npcs_reply` sets `practice_npcs_sent`:
   `state['creatures'] = creature_ai.CreatureDirector(
   practice_population.creatures(solo_modes.selected().level))`, guarded by a
   new opt-in `CRUCIBLE_CREATURE_AI=1` so the accepted baseline is untouched
   by default. An empty population (every non-Practice map today) must simply
   produce no director and no traffic.

3. `shard_stub.game_clock_reply` — after the existing hunter-patrol and health
   blocks, and only when the director exists:
   - build `targets = {0x01000002: state['player_position']}`. That key is
     supplied by `shard_stub.observe_client_input` via the new
     `client_input.latest_position` decoder (component 172 / RPC 1 on the
     connection entity), which landed after this trace began and removes the
     blocker this section originally recorded. Until the first input arrives
     the key is absent: pass an empty `targets` mapping in that case rather
     than substituting the authored spawn position, otherwise a creature can
     aggro onto a player who is not there. The hunter squad already consumes
     the same value in `build_hunter_ai_updates`.
   - `events = state['creatures'].step(elapsed_ms / 1000.0, targets)`
   - for each `creature_ai.CreatureAttack` event, hand it to `combat.py`
     (owner's API) — do not synthesise damage here.
   - for each `alert_nearby` event call
     `state['creatures'].alert_near(now, creature.position,
     creature_ai.SPITTER.script.alert_radius, event.creature_id)`.
   - append one `build_entity_message` per entry of
     `state['creatures'].rotations()`, reusing exactly the property mask
     `build_hunter_patrol_updates` uses for Location — `[False]*n` over the
     entity's own `shared_types` with `(True, False, False, True, False,
     False)` at component 145 — with the 12-byte payload from `rotations()`.
     Chain `previous_id` the same way the existing loops do, and keep the
     packet inside the `build_entity_updates` envelope the current tests
     assert.

4. Damage feedback: when `combat.py` applies damage to a creature, call
   `state['creatures'].note_damage(now, creature_id, attacker_id)` so
   `ForcedAggroDurationOnDamage` is honoured.

5. `solo_modes.py` (owned by another agent) — two fields would let this stop
   being Practice-special: a `creature_population` key naming the entry in
   `practice_population.POPULATION` (defaulting to the `level` string, which
   already works), and the per-map baked-ID bias already present as
   `baked_counter_bias` applied to creature spawner entries as well, so a map's
   creature placements can be generated from baked entry indices rather than
   hand-listed. No change is required for Practice today.
