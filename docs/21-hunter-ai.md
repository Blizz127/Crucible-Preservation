# Hunter bots: general navigation, authored speeds, and a state machine

Continues `19-hunter-patrol.md`. The three Marine hunters on April2018 were
visually accepted as stationary props and then moved along three hardcoded
routes at a flat 1.5 m/s test speed, which nobody has ever seen move. This
increment generalises the navigation, replaces the invented speed with the
authored one, and adds an explicit per-bot state machine. None of it has been
observed in a running game.

## Navigation: the tile-border defect, measured

`19` recorded that the route helper "still needs further handling of partially
overlapping tile-border edges". That defect is now quantified against the
installed April2018 mesh. Of 99,256 non-off-mesh link words, 73,424 are
internal (side 15) and 25,832 cross a tile border. The old code used the
midpoint of the *source* edge as the portal for every one of them. That is
only correct when both triangles share the whole edge:

- 1,141 border links join edges that only partially overlap, so the source
  midpoint can fall outside the destination triangle entirely.
- 430 links (all on sides 6 and 13, the vertical tile neighbours) have no
  collinear destination edge at all within 0.02 m in plan.

`navigation.shared_edge` now clips each link to the overlap of the source edge
with whichever destination edge is collinear, and stores that segment as the
portal. Collinearity is judged in plan view, because adjacent MNM triangles
share a plan edge while disagreeing slightly in height along it; a separate
0.5 m height gate keeps stacked floors apart, which matters precisely on the
vertical tile neighbours. Links with no collinear partner are dropped rather
than forced: dropping a link makes routing conservative, while inventing a
portal would put a bot off the mesh. `mesh.counts` records the census above so
it is checked by a test rather than asserted in prose.

Paths are then string-pulled. `Mesh.corridor` runs A* over triangles,
`Mesh.gates` orients each clipped portal into a (left, right) pair using the
direction between the two triangle centroids, and `navigation.funnel` runs the
simple funnel algorithm over those gates. `Mesh.path` resamples the result at
one metre and re-projects each sample onto the corridor surface.

Measured on the installed mesh: 43,130 triangles, 98,826 usable links, 99
connected components with the largest holding 41,479 triangles (96.2%).
Thirty seeded random point pairs inside that component all route; every one of
the resampled positions lies on the installed surface within 0.05 m vertically;
the funnelled length is never longer than the old centroid-to-centroid route
and is typically 10–30% shorter. A separate 40-pair sweep put the worst
vertical error at 0.015 m and the slowest single path at 60 ms.

The three authored drop-point routes are unchanged in corridor terms — 8.90,
19.05 and 10.91 m, exactly the figures in `19` — which is the regression check
that the clipping did not move the verified bounded scope. Funnelled they are
7.93, 17.49 and 9.61 m, because all three are in fact straight lines.

`navigation.installed_mesh()` is the cached read-only loader; nothing under the
Steam install is written and the game is never launched.

## Authored values, and how they were read

Binary ObjectStream assets store a CRC32 of the reflected field's *display*
name, not the name, so an asset alone is a tree of anonymous numbers. The
client registers those display names as literals, so `tools/inspect_hunter_assets.py`
harvests every printable run out of `bin/Crucible.exe`, hashes each with
`zlib.crc32(name.lower())`, and labels the asset tree with the result.

That method was validated against work already in the repository before it was
trusted: re-reading `ai/spitter.ai` reproduces every field, offset and value in
`snapshots/gamemode-20260919/combat-asset-fields.json` exactly — SensingRadius
20.0 at 0xc6, CanPatrol false at 0x205, MovementEnabled false at 0x702,
AggroRadiusNear 5.0 at 0x1003, AggroRadiusFar 20.0 at 0x101d, DeaggroRadius
25.0 at 0x1051, AttackDelayForNewTargets 1.0 at 0x123e, RangeMin 0.1 at 0x12d1,
RangeMax 40.0 at 0x12eb. It also names fields that run never labelled.

The full dump is `snapshots/hunter-ai-20260921/hunter-asset-fields.json`.

### Marine movement speed

`assets/player/marine/marine_default.movementconfig`, misc.pak,
sha256 749e737f720dfb7160df7b3dfb3896463a7d7cda727dac0a8bdc39490ff7a59a:

| field | offset | raw | value |
| --- | --- | --- | --- |
| Movement Speeds | 0x54 | `40b00000 40900000 40600000` | 5.5, 4.5, 3.5 |
| Acceleration | 0x77 | `3e19999a` x3 | 0.15 |
| Max Turn Speed/m_Value | 0x143 | `44b40000` | 1440 deg/s |
| Simplified NPC Movement | 0x3d | `00` | false |

The triple's ordering is settled by comparison, not by guessing:
`marine_sprint.movementconfig` (0x53) is 8.5, 4.5, 3.5 and
`marine_sprint_charge.movementconfig` (0x59) is 9.0, 4.5, 3.5 — only the first
component changes when sprinting, so it is forward, and the other two are
lateral and backward. Crouch, aim-down-sights and heal are all 2.5, 2.5, 2.5.
Generic `assets/character/default.movementconfig` agrees at 5.5, 4.5, 3.5.

**A Marine walks forward at 5.5 m/s and sprints at 8.5 m/s.** The 1.5 m/s in
`19` was an invented test speed and is gone.

### Marine hunter AI

`ai/guide_dummies/marine_guide_dummy.ai`, misc.pak,
sha256 e739463bad9c9fa4eae7984e3c23983b208034f7ab7e6c27b632364c8ef6fea6:

| field | offset | raw | value |
| --- | --- | --- | --- |
| ControlMode | 0x2e | (string) | SpoofedPlayerInputs |
| PatrolParams/CanPatrol | 0x294 | `01` | true |
| PatrolParams/MaxAttemptsForFindingPatrolDestination | 0x2ab | `05` | 5 |
| PatrolParams/MinDistance | 0x2c2 | `40a00000` | 5.0 |
| PatrolParams/MaxDistance | 0x2dc | `41700000` | 15.0 |
| PatrolParams/MaxDistanceFromCurrent | 0x310 | `42480000` | 50.0 |
| MovementPararams/MovementEnabled | 0x791 | `01` | true |
| MovementPararams/SpeedWander | 0x7a8 | `41000000` | 8.0 |
| MovementPararams/SpeedChase | 0x7c2 | `41000000` | 8.0 |
| PassiveTurnParams/TurnInPlaceSpeed | 0xc93 | `42b40000` | 90 deg/s |
| AggroCheckLineOfSight | 0x107b | `01` | true |
| AggroOriginOffsetLocal | 0x103e | `(0, 0, 1)` | one metre above the character root |
| AggroTargetOffsetVertical | 0x1061 | `3f800000` | `1.0` m |
| AggroRadiusNear | 0x1092 | `40a00000` | 5.0 |
| AggroRadiusFar | 0x10ac | `42200000` | 40.0 |
| AggroAngleFar | 0x10c6 | `42f00000` | 120 deg |
| DeaggroRadius | 0x10e0 | `42480000` | 50.0 |
| ForcedAggroDurationOnDamage | 0x1114 | `40c00000` | 6.0 |
| TimeWithoutLoSToForgetCurrentTarget | 0x12da | `41000000` | 8.0 |
| AttackDelayForNewTargets | 0x12a6 | `3e800000` | 0.25 |
| MinTimeBetweenAttacks | 0x12c0 | `00000000` | 0.0 |
| Attacks[0]/RangeMin | 0x1339 | `00000000` | 0.0 |
| Attacks[0]/RangeMax | 0x1353 | `41f00000` | 30.0 |
| Attacks[0]/Duration | 0x1387 | `40000000` | 2.0 |

The peers named in `18` are in the same directory with the same layout:
Duelist wanders and chases at 7.5, Saboteur and the hero dummy at 6.5, Inventor
at 5.5, and `ai/dummy_player_guide.ai` at 5.0/6.0. Duelist and Saboteur have
`CanPatrol` false; Marine, Inventor and the hero dummy have it true. Marine is
the only one of them with `AggroRadiusNear` 5.0 and `AggroAngleFar` 120 —
the others acquire at 30 m in any direction.

Two of these deserve calling out. `CanPatrol` is true with a 5–15 m destination
band and five attempts, so the authored patrol is a bounded random walk, not a
fixed path; the three hardcoded routes in `19` were an invention. And
`ControlMode` is `SpoofedPlayerInputs`, meaning the retail guide bot is driven
through the player movement pipeline rather than by direct transform writes —
which is why the movement config's caps are treated as authoritative below.

## What is implemented

`server/hunter_ai.py` is a deterministic state machine. No wall clock, no
sockets: `Bot.update(now, target, target_position)` is a pure function of the
tick sequence it is handed, and `hunter_ai.MARINE` carries the numbers above
with the offset of each one in a comment beside it.

States are `patrol`, `pursue`, `attack` and `return`.

- **Patrol.** With `wander=True` and a mesh, the bot asks
  `Mesh.wander_point` for a reachable destination 5–15 m away and within 50 m
  of its anchor, up to five attempts, and funnels a path to it; a failed
  search holds position for the tick rather than forcing a hop. Its RNG is
  injected and seeded from the entity id, so a restart replays the same
  patrol. Without a mesh it falls back to the fixed ping-pong route, which is
  what `build_hunter_patrol_updates` still uses.
- **Detect.** Inside `AggroRadiusNear` (5 m) the bot acquires in any
  direction. Out to `AggroRadiusFar` (40 m) the target must also be inside
  `AggroAngleFar` (120 deg, so +/-60 from facing). It drops the target past
  `DeaggroRadius` (50 m), giving the authored hysteresis.
- **Pursue.** Funnelled navmesh path to the target's last replicated
  position, replanned at most every 0.5 s and only when the target has moved
  more than a metre. An unreachable target stops the bot and makes it face
  the target rather than walking into geometry.
- **Face and fire.** Inside `RangeMax` (30 m) the bot stops, turns at
  `TurnInPlaceSpeed` (90 deg/s) and, after `AttackDelayForNewTargets`
  (0.25 s), emits a `Shot`.
- **Return.** On losing the target the bot paths back to its anchor and
  resumes patrolling.

Speeds: patrol 5.5 m/s and pursuit 8.0 m/s. Both are choices between two
authored numbers and are recorded as such. The guide AI's `SpeedWander` and
`SpeedChase` are both 8.0, but 8.0 exceeds the Marine movement config's
non-sprint forward cap of 5.5, and `ControlMode` says the guide bot drives the
player movement pipeline, which enforces that cap. Patrol therefore uses the
cap and the chase uses `SpeedChase`, which fits inside the 8.5 sprint cap.
If a launch shows hunters gliding rather than running, that pairing is the
first thing to change.

Firing is an event, not a packet. `hunter_ai.Shot(shooter, target, time,
origin, direction, range)` is emitted by `Bot.update` and collected by
`Squad.shots(steps)`. Nothing is put on the wire. `server/combat.py` is
another agent's file and owns validation, damage, death and respawn; this
module deliberately stops at "bot N fired at entity X at time T".

`server/hunter_patrol.py` keeps `routes()` (now funnelled and resampled), adds
`circuits()` and `squad()`, and adds `unpack_position()`, the exact inverse of
`shard_stub.pack_practice_spawn_transform`'s 8-byte position half, so an
inbound autonomous Location update can become the target position the state
machine wants.

Tests: 35 in `test_hunter_ai.py`, 13 in `test_navigation.py` (including one
sweep over the installed mesh), 10 in `test_hunter_patrol.py`. The whole server
suite is 141 tests in under four seconds. Every one of them is a unit test.
**Nothing here is verified.** Unit tests prove the code does what the numbers
say; only a launch proves the numbers are the ones the client wants.

## Walking animation: evidence, still unverified

`19` flagged animations as unverified. The evidence now points at a client-side
evaluator rather than a replicated field, though it stops short of proof.

The client contains `BlendSpaceMoveSpeedParamEvaluator` (rva 0x6d7b440,
referenced from 0x4edcb71) and `BlendSpaceTravelDirectionParamEvaluator`
(rva 0x6d7b4e0, from 0x4edd011), plus `BlendSpaceTurnSpeedParamEvaluator`,
`BlendSpaceTravelSlopeParamEvaluator` and `BlendSpaceTravelDistanceParamEvaluator`.
"Evaluator" is the operative word: these derive the blend parameters. Against
that, there is no `ReplicatedVelocity`, no `LocomotionState` and no
`MovementState` replicated property anywhere in the binary, and the Location
component's only properties are `InternalTransform` and
`InternalTransformParentEntityId` — exactly the 12-byte packed transform the
shard already sends.

Every `Velocity` string in the client belongs to the velocity *override*
system: `VelocityOverrideRequestComponent`, `OnVelocityOverrideAppliedEvent`,
`StartVelocityOverrideWithEntityTarget`, and the `InVelocityOverride` bool at
rva 0x66bda90 (referenced from 0x785723, 0x78574a, 0x785763, 0x785d05) that
`17` recorded on AIComponent. That is a dash/knockback mechanism, not ordinary
locomotion.

**Working conclusion, not a verified fact:** locomotion animation is driven
client-side from the character's own observed motion, so sending Location
deltas at 20 Hz should be sufficient to make hunters animate, and no velocity
field needs to be found or sent. If the next launch shows hunters sliding in an
idle pose, this conclusion is wrong and the blend-space evaluators at
0x4edcb71 are where to look next.

One related asset finding: `Simplified NPC Movement` is false on every Marine
movement config and true on `megadronedefault.movementconfig`, so the Marine
character does not take the simplified NPC path.

## Server-to-client weapon fire: open trace, not implemented

This could not be proven, so nothing is sent. The leads are specific.

`ProjectileRequestComponent` has four RPC structs, and all four are feedback to
the shooter rather than a "spawn a projectile on a remote entity" message:
`ClientProjectileResultRpcStruct` (the known client-to-server direction,
component 115 / RPC 4, decoded at 0x9d9120; serializer name at rva 0x670d8a0,
fields `projectileId` at 0x670d938 and `prevHitEntities` at 0x670d948),
`ConfirmKillIndicatorRpcStruct` (0x670d960, `killedEntity` 0x670dbb8),
`ConfirmHitIndicatorRpcStruct` (0x670dbe0, `isCrit`, `hitOffset`, `damageType`,
`directDamage`, `projectileTypeIndex`) and
`ConfirmProjectileHitLocationRpcStruct` (0x670dc50).

Its replicated properties are named in the string block at 0x670d7a8–0x670db4c:
`PlayerKillCounter`, `LastKilledPlayer`, `IndirectDamage`, `HitIndicatorCounter`,
`IsCrit`, `DirectDamage`, `HitConfirmScaleValueToShow`, `DecayStartTime`,
`AccuracySwitchTimePoint`, `MoveBloomReticleRatio`, `PreviousAccuracyMode`,
`SwayMotion`, `ShotAccuracyInfo`, `IsDead`, `HybridProjectileHit`, `KickRatio`,
`CurrentWedgeNum`, `WedgeStep`, `LastMovementTimePoint`, `NextProjectileId`,
`HybridProjectileActivations`. There is also `ProjectileNetPayloadClass` at
0x670d618. Component classes are registered as
`ProjectileRequestComponent::Client` (0x66affd0), `::ClientBase` (0x670d6d8) and
`::Common` (0x670d728).

`HybridProjectileActivations` is the best candidate: the Marine primary's
`CreateProjectileType` is `Hybrid` (`17`), so a remote client plausibly spawns
the visual when that replicated value changes. That is a hypothesis. Its record
index, encoding and semantics are unknown, and no bytes have been guessed onto
the wire. There is no `ServerProjectile`, `WeaponFired` or `SpawnProjectile`
symbol in the client.

## Not implemented, and knowingly so

- `AggroCheckLineOfSight` is true and `TimeWithoutLoSToForgetCurrentTarget` is
  8.0, but no collision geometry has been decoded, so there is no occlusion
  test. Bots see through walls.
- `ForcedAggroDurationOnDamage` (6.0) needs a damage input this module does
  not receive; combat owns that.
- `AlertRadiusNear/Far` (7/15) and the alert delay/moratorium fields exist in
  the asset and are not used.
- `StrafeParams` (enabled, 4.5 m/s, 1–2 m near and 20–30 m far bands),
  `BackUpParams`, `ActiveTurn`, and `TargetApproachParams` Stop distances of
  1.8/3.0 are all authored and all ignored: the bot stops at `RangeMax`.
- Rate of fire. `MinTimeBetweenAttacks` is 0.0 and `Attacks[0].Duration` is
  2.0, so one `Shot` is emitted every 2 s. That is one event per *attack*, not
  per bullet; the rifle's per-shot cadence is a separate trace.
- Damage, health, death, respawn, teams, objectives, abilities and loadouts.
- 430 vertical tile-border links are dropped, so 3.8% of the mesh sits outside
  the largest component and a bot cannot reach it. Routing is conservative
  there, not wrong.

## What to watch for on the next launch

Nothing here has been enabled and no service has been restarted. With the
patrol override on, watch for: hunters moving at a plausible jog rather than a
crawl; feet planted on the ground through the whole route; a walk or run
animation rather than an idle pose sliding around; the bot turning to face
travel; and the client not disconnecting. Then walk to within 40 m in front of
a hunter and check whether it turns and approaches, and to within 5 m behind
one and check whether it notices. If hunters move but do not animate, the
blend-space evaluator conclusion above is wrong. If they move too fast, swap
patrol to `SpeedWander` semantics. Disable the override and restart only after
Crucible is closed, and keep the accepted Alpha Hunters profile intact.

## Integration notes

`server/shard_stub.py` was not touched; other agents are working in it. Three
changes are needed there, in dependency order.

1. **Track the player's replicated position.** `build_hunter_patrol_updates`
   is stateless and the AI is not. `observe_client_rpcs` (line 912) already
   walks inbound messages; the shard additionally needs the client's
   autonomous Location update for 0x01000002. Decode its 8-byte packed
   position with `hunter_patrol.unpack_position(blob)` — the exact inverse of
   `pack_practice_spawn_transform`, covered by a round-trip test — and store
   it as `state['player_position']`. Until that exists, pass `None` and the
   bots simply patrol.

2. **Drive the squad from the clock instead of `sample()`.** In
   `game_clock_reply` (line 810), replace the
   `build_hunter_patrol_updates(elapsed_ms / 1000.)` call at line 833 with a
   squad tick. Hold one `hunter_patrol.squad()` per connection in `state`
   (build it lazily, it parses the mesh on first use), then:

       steps = squad.update(elapsed_ms / 1000.,
                            0x01000002 if position else None, position)

   `Squad.update` returns one `Step` per bot in entity order 0x01000003..05.
   Feed `step.position` and `step.yaw` into the existing packing in
   `build_hunter_patrol_updates` (line 684) — the property mask, the
   `pack_practice_spawn_transform` position half and the WXYZ largest-index
   rotation encoding are all unchanged and still covered by
   `test_hunter_patrol.py`. Note the sign: that function computes
   `yaw = atan2(-direction[0], direction[1])` while `hunter_ai.yaw_of` uses
   `atan2(direction[0], direction[1])`, so pass `step.facing` through the
   existing expression rather than `step.yaw` directly, or negate.

   The squad must be ticked exactly once per clock tick. Ticking it twice
   double-advances the bots; ticking a fresh squad each tick freezes them,
   because the first `update` only establishes the clock epoch.

3. **Hand the shots to combat.** After the tick, `squad.shots(steps)` returns
   `hunter_ai.Shot` records. Pass them to `server/combat.py` (another agent's
   file) as "bot N fired at entity X at time T"; `Shot` carries `shooter`,
   `target`, `time`, `origin`, `direction` and `range`. Do **not** put a fire
   message on the wire: the server-to-client projectile encoding is an open
   trace above, and guessing it risks the StreamError disconnects that
   `18` documents.

`build_solo_hunter_probes` (line 670) needs no change: it takes `route[0]` of
each route, which is still the nav-projected authored drop point. If the squad
becomes the source of initial positions, use `squad().bots[i].position`, which
is the same value.

## First live run, 2026-09-21 23:0x: they follow

The user played the Alpha Hunters profile on April2018 with
`CRUCIBLE_SOLO_AI=1`. Everything below is **live observation**, not inference.

**Pursuit works.** The user's words were "they are following". That settles
several things at once that the unit tests could not: the navmesh routing
produces usable paths at runtime, `Bot.update` is being driven correctly off
the decoded player position (`docs/25`), and — importantly — **our Location
deltas do move the rendered entity**. The renderer-transform worry carried
over from `docs/18` (the `+1440` transform populated by `c406b0`) is not a
problem for simulation-role bots.

Three defects came with it.

### They ran in place, and that breaks this document's animation prediction

The section above concluded, from the absence of any replicated velocity or
locomotion property and the presence of `BlendSpaceMoveSpeedParamEvaluator`
(`0x6d7b440`), that Location deltas alone should drive the walk animation.

Observed instead: hunters standing still — the server was sending a
*constant* position, confirmed in simulation at speed 0.00 with 0.034 m of
drift over six seconds — while playing a **run cycle**. Under the prediction
above a constant position gives an idle stance.

So the prediction is **falsified as stated**. Either animation is not driven
by replicated position deltas, or there is a movement-state input that is
never replicated and whose default is "running". The second is the better bet
and is now the leading open trace, because it also explains why the bots
looked wrong while genuinely moving. Do not treat the earlier conclusion as
settled; it was reasoned from absence of evidence.

### They stood inside each other

All three path to the same target and nothing pushed them apart. Retail does
this with a constraint solver; `marine_guide_dummy.ai` carries the margins —
`NavigationParams/RepulsionMarginMin` 2.0 (0x8f2), `RepulsionMarginMax` 2.7,
and `TargetApproachParams/StopDesiredDistance` 1.8 (0x9c4),
`StopAllowedDistance` 3.0. `hunter_patrol.separate` is a bounded relaxation
over the authored 2.0 m margin in plan view. It is not the retail solver.

### They clipped into the terrain, and the first fix was wrong

The first attempt anchored each bot to its authored `DropPoint` transform and
carried `authored Z - mesh Z` as a per-bot offset. **That was a mistake.**
Drop points are pods, not standing positions: one of April2018's three sits
0.5 m *below* the walkable surface, so the "fix" buried that bot.

The player's own replicated stream settles it without guessing. Over 5,783
input samples from the 23:08 run, `player Z - mesh surface Z` has its mode at
**-0.03 m with 1,577 samples**; the +0.49/+0.92/+1.15 m clusters are jumps.
A grounded character stands *on* the mesh. `STANDING_HEIGHT_OFFSET` is
therefore 0.0 and the authored drop points are explicitly rejected as a
standing reference.

The real cause of sinking is that `advance` walks straight lines between
waypoints, so on a convex slope the segment passes under the surface.
`hunter_patrol.ground` re-projects every emitted position onto the mesh at its
own X/Y, which removes the class of defect rather than tuning a constant. It
costs 0.01 ms per call — 0.3 ms per second for three bots at 20 Hz.

Both corrections are applied in `shard_stub.build_hunter_ai_updates`, between
the state machine and the wire, so `hunter_ai` stays a pure simulation.

## Operational trap: never restart the shard during a match

This cost about an hour and is worth writing down, because nothing in the
code makes it visible.

Restarting the shard while the client is in a match makes the client write
`AppData/Local/AGS/Crucible/rejoindata` with `m_hasValidRejoinData="true"`
and the hub's address. From then on **every launch auto-rejoins that dead
match instead of showing the menu**. The symptoms are misleading: the hunter
select screen never appears, the client appears to "crash", and the client log
fills with `[Warning][GameMode] GameModeComponent was not ready yet` — which
is the same line `docs/18` produced from genuinely wrong baked entity IDs, so
it is easy to misdiagnose as a replication regression. It is not; the client
log's final line is `Crucible client application is quitting normally`.

Two further traps in the same area:

- `local_sessions.selection` only accepts a hub identity **60 seconds old or
  less**, and the hub only publishes one during matchmaking. A client that
  reconnects straight to the shard therefore hangs on Connecting forever with
  `BOOTSTRAP waiting for one fresh local hub identity`. Re-queue from the
  menu; do not reconnect.
- The client writes its own logs to
  `compatdata/1057240/pfx/.../AppData/Local/AGS/Crucible/NovaNetLogs/`. Read
  them before theorising. They answer "did it crash or did it hang" in one
  line, and none of the guessing above was necessary.

A reboot revives an old file too. `m_timePointUntilCanRejoinInMicroSecs` is
measured on the machine's uptime clock, not wall time: the client writes a
fresh file at every match start, and it normally expires within the session.
After a reboot, uptime restarts near zero, so an earlier file's deadline looks
far in the future again. On 27 September a 22 September file (deadline
165,912 s) forced the rejoin path after the 17:38 reboot. That path connects
with `NovaNetUserId 0`, and the shard waits forever for a hub identity (below).
The Banshee Crucible provider renames `rejoindata` before every launch.

Recovery: stop the game, delete `rejoindata`, clear
`$XDG_RUNTIME_DIR/crucible-revival-sessions/*`, restart the three services,
and launch fresh.

## Attack cadence from the rifle's own script, 24 September

The earlier "one `Shot` per 2 s attack" was one event per *attack*, which
`snapshots/accuracy-20260924/accuracy-audit.md` ranked as the most visible
invented policy. The per-round cadence is authored in
`cdl/player/marine/ability/marine_primary_onfire.cdl` (sha256 `10ecfb3b…`).
Raw asset contents remain outside this repository. On entry it sets `using_primary1` and
`using_aim_ik` and fires `CreateProjectile`. It then loops `Delay 0.064`,
`Delay 0.032`, `AdjustResource Ammunition −1` and
`WaitUntilTimePoint(GetTimePoint +0x60 ms)` back into the next
`CreateProjectile`, so it fires **one round every 96 ms** while the input is
held. `OnExit` clears `using_primary1`.

`hunter_ai` now models an attack as the primary held for
`Attacks[0].Duration` (2.0 s). That gives 21 rounds at their scripted times.
Attacks run back to back (`MinTimeBetweenAttacks` 0.0). The start gate is
the authored `Attacks[0]` block:
- `RangeMin`/`RangeMax` 0–30 m;
- `RangeVerticalMax` 20 m (0x136d);
- `Angle` 90° (0x131f), read as a full cone width like `AggroAngleFar`
  (that reading is DERIVED, not proven);
- `AttackDelayForNewTargets` 0.25 s.

A finite attack is not abandoned when the target leaves `RangeMax`, because
`AbandonInfiniteAttackIfTargetOutOfMaxRange` covers infinite attacks only.
Dropping the target (deaggro, death) ends it. `Step.firing` reports a held
trigger.

In the shard, `CRUCIBLE_SOLO_COMBAT_ANIMATION` holds `using_primary1` and
`using_aim_ik` for the whole attack instead of pulsing once per shot. It no
longer sets `is_aiming`: aiming down sights is the separate
`marine_aimdownsights` ability, and nothing shows bots using it.
`CRUCIBLE_SOLO_FIRE` publishes one activation per *shooter* per clock packet
(it was one per packet in total), which keeps up with 10.4 rounds/s at the
20 Hz clock. Still open: magazine size and reload (bots never run dry),
strafing and approach stop distances, and line of sight. None of this has
been observed in a running client.

### Authored aim height

The full Marine guide-AI dump adds two sourced offsets that matter to both
targeting and the fire packet: `AggroOriginOffsetLocal = (0, 0, 1)` at 0x103e
and `AggroTargetOffsetVertical = 1.0` at 0x1061. `Bot.update` now measures
aggro/attack range and angles from those points and creates its round along
that line. After grounding and separation, the shard reapplies both offsets
before encoding `FireOrigin`, `IdealOrigin` and `IdealTargetPosition`. This
removes the feet-level trace. The exact weapon muzzle attachment remains
unresolved, and this still needs visual acceptance in a running client.
