# Retail-accuracy audit of the solo simulation — 24 September 2026

Scope: `server/hunter_ai.py`, `hunter_patrol.py`, `navigation.py`, `combat.py`,
`creature_ai.py`, `practice_population.py`, `solo_modes.py`, `hero_loadout.py`,
`animation_events.py`, `death_events.py`, `projectile_events.py`,
`hit_reaction_events.py`, and the simulation/replication parts of
`shard_stub.py` (clock, cadence, replicated entity/component set, `CRUCIBLE_*`
flags). Read against docs 14, 16–30. Branch `solo-ai-and-combat` working tree
as of this date. No file under `server/` or `tools/` was modified; no game was
launched and no service was restarted.

Classification used for every item:

| class | meaning |
|---|---|
| **SOURCED** | cites a retail asset field/offset, an executable RVA, or a live capture |
| **DERIVED** | reasoned from sourced facts; soundness noted |
| **INVENTED** | placeholder or test policy with no retail source |
| **MISSING** | authored retail behaviour known to exist but not implemented |

The audit records derived findings and source hashes only. Raw game asset
contents and extracted asset dumps are kept outside this repository.
- Live input cadence from `server/shard_udp.log` (last 400k lines, 21 Sep
  22:51 run): the client sends component-172 input RPCs at **60–61 per
  second** (16 ms apart), not 20 Hz as `docs/25` assumed.
- Tick-rate cvar names in `bin/Crucible.exe`: `net_TickRate` (rva `0x6d88958`,
  category LEAs `0x4f5a2d3` "server", `0x4f9ac7a` "client"),
  `sv_MaxServerUpdateRateMs` (`0x6d880e8`, LEA `0x4f5cd0d`),
  `sv_SystemTickRateMs` (`0x6d88b78`, LEA `0x4f5af67`), `net_ProjectileTickRate`
  (`0x6d356e0`, LEA `0x4f9a7fc`, "client"). The LEAs found are a
  cvar→role category table, **not** the defaults; the defaults are still to be
  located.

---

## Prioritised top-15 work list

Ranked by what a player would see, weighted by effort. "Impact" is what a
player would notice. "Effort" covers both finding the evidence and
implementing the change.

| # | Work item | Class today | Impact | Effort | Retail evidence to use |
|---|---|---|---|---|---|
| 1 | **Marine bot rate of fire.** The current rate of one `Shot` per 2 s attack is wrong. The retail rifle fires about every **96 ms** while the trigger is held for `Attacks[0].Duration` 2.0 s. `using_primary1` and `using_aim_ik` stay true for the whole burst and Ammunition drops by 1 per shot. Lift the one-activation-per-packet cap (up to 10 live slots per shooter). | INVENTED (`fire_interval=2.0`, single-update pulse, cap) | high | med | `marine_primary_onfire.cdl` (this audit): `GetTimePoint OffsetMilliseconds 0x60`, `Delay 0.064` + `Delay 0.032`, `CreateProjectile`, `AdjustResource Ammunition −1`, `Set using_primary1 True` on enter / `False` on `OnExit`, `Set using_aim_ik True`. Also `marine_primary.ability` `Cooldown 0.032` (0x14e), `Cost 1` Ammunition (0x10a/0x134). Magazine size comes from the Marine's `EntityResourceCollection` Ammunition resource in `slices/player/marine/marine_character.dynamicslice`. |
| 2 | **NPC attacks never hurt the player.** `Combat.apply_damage` exists but no caller uses it. Hunter `Shot` and `CreatureAttack` events are only logged. The player's Health (800) is never replicated. | MISSING | high | high | Hit resolution: `ProjectileUtils::Update` (unwind `0x3220840..0x3221d3a`) → `DamageData` → `0xddae10` (docs/20). Victim feedback: `IncomingHitIndicator` (81) and `DamageFloater` (36) layouts (docs/27 §9). Hit volume: `NavigationParams/MoverRadius 0.5` (0x8c7) as a capsule stand-in until character physics is decoded. |
| 3 | **Shot geometry.** `FireOrigin` and `IdealOrigin` are both the bot's **feet**, and `IdealTargetPosition` is the player's **feet**, so tracers run along the ground. | INVENTED | high | low–med | The onfire script targets `TargetPointComponent.TargetPoint` from `TargetPointComponent.IdealRayOrigin`. Aim height: `AggroTargetOffsetVertical 1.0` (0x1061), `HeadLookPositionLocal (0.5,0,1.8)` (0xfb5). Muzzle: bind pose of `Marine_c_vfxAttach1_BND` (attachment 60) in `marine_actor.actor`. Best source: a read-only live capture of the player's own `TargetPointComponent` / component-115 values relative to its Location. |
| 4 | **Attack cone, vertical range and turn speed.** Bots fire at any facing error, including sideways or backwards while turning at 90°/s. They use the AI's 90°/s turn-in-place for every turn, including while running. | MISSING / DERIVED-unsound | high | low | `Attacks[0].Angle 90` (0x131f), `RangeVerticalMax 20` (0x136d); `PassiveTurnParams` `TurnInPlaceWhenMovingAngle 45` (0xc5f), `WhenStoppedAngle 60` (0xc79), `TurnInPlaceSpeed 90` (0xc93); `marine_default.movementconfig` `Max Turn Speed 1440` (0x143), `Turn Acceleration 0.1` (0x174). |
| 5 | **Combat movement.** Bots stop dead at 30 m and stand still. Retail has strafing enabled and approach stop distances of 1.8/3.0 m, and movement is not locked during an attack. | MISSING | high | med | `StrafeParams` (0xaba..0xbcc): Enabled, near 1–2 m, far 20–30 m, speed 4.5, accel 10, duration 1–2 s, not pushable. `TargetApproachParams` Stop 1.8/3.0 (0x9f6/0xa10), `StopAtGoal False` (0xa2a), `ForcePathfindingWhenLineOfSightBlockedDuration 0.5`. `Attacks[0].TranslationDisallowedPeriod 0/0` (0x13a1/0x13bb). Semantics: exe code referencing `DistanceNearMin` / `StrafeParams` / `StopDesiredDistance`. |
| 6 | **Hunters do not react to being shot, and see through walls.** Hits on hunters never reach `hunter_ai`. There is no line-of-sight test or forget timer. | MISSING | high | med (damage) / high (LoS) | `ForcedAggroDurationOnDamage 6.0` (0x1114), `DormantStateParams.CanBeWokenUpByDamage` (0x77), `AggroCheckLineOfSight True` (0x107b), `TimeWithoutLoSToForgetCurrentTarget 8.0` (0x12da), `TimeWithoutLoSToTargetUntilAbandoningAttack 3.0` (0x1420). Occlusion options: `levels/april2018/level.pak` terrain and brush physics, `covermission0.bai` (cover surfaces, same pak), or the MNM mesh as an approximate occluder (DERIVED, partial). |
| 7 | **The player's damage always uses Marine numbers.** The user plays Saboteur (class 12, live 22 Sep capture), yet `apply_reported_hit` defaults to `MARINE_PRIMARY` (20/10). Distance is never fed, so every hit does 20. Crits are never classified. | INVENTED (hero-agnostic weapon), MISSING (falloff input, crits) | high | low–med | Per-hero `assets/player/<hero>/<hero>_projectile_request.projectilereq` → `.projectile` `HealthChangeImmediateOnHit` / `LifetimeSeconds` / `CriticalMultiplier`. Distance: shooter's decoded input position to target Location (DERIVED). Crits: `preciseCriticalHits` plus hit-bone lookup; needs a trace of the `ClientProjectileResult` consumer / `ConfirmProjectileHitEntity.isCrit` producer. |
| 8 | **Visible death and respawn.** Only hunters get `is_dead`. Death metadata is opt-in with no source-backed IDs chosen. Respawn only resets health: nothing despawns or respawns and no telegraph plays. Spitters come back death-timed and at full aggression instead of absence-polled. | DERIVED / INVENTED / MISSING | high | med | Live test of `CRUCIBLE_SOLO_DEATH_EVENTS` with HealthChangeSource / AbilityNames IDs from `tools/inspect_fire_bakednames.py` (`marine_primary` = 51, source table `0x6c1e0c0`). Consumers of the `KilledByDamageSource` callback `[this+0x20e8]` (docs/27 §5). `SpawnSlice` / `TelegraphTime` in `practicearena_targetdummydeathwatcher.cdl` and `practicearena_spittertracker.cdl`. Animgraph `Root base->death` (docs/28). |
| 9 | **Hit feedback.** The player gets no hitmarker or kill confirm, victims do not flinch, and no damage numbers appear. | MISSING (encoders exist; not wired) | high | med | `ProjectileRequestComponent` RPCs 115/2 `ConfirmHitIndicator` and 115/3 `ConfirmKillIndicator`, delivery 2 (docs/26 §3), sendable to the player today. `HitReaction` (80) index/yaw: animgraph `hit_type` 0..1 and `hit_yaw` −180..180 (group `hit_reaction`, docs/28), consumer trace from the `NetworkedAdditiveHitIndex` reader `0x94417c`. |
| 10 | **Tick and update rate.** The 20 Hz clock is an invented stall fix, and so is the 2000 ms epoch. The client runs input at about 60 Hz. | INVENTED | med | low | Defaults of `net_TickRate`, `sv_MaxServerUpdateRateMs`, `sv_SystemTickRateMs`, `net_ProjectileTickRate` (names located above). Read them with a read-only live cvar dump (`inspect_match_startup.py`-style) or trace the cvar registration struct that holds each name pointer. Live input cadence (this audit): 60–61 msgs/s. |
| 11 | **Creature presentation and alerting.** Spitters have no `using_primary1` wind-up, send no projectile activation, and snap-turn instantly. The alert fan-out is unwired, uses the wrong radius and has an aggressor-ID bug. The Invulnerable spawn buff is not modelled. | MISSING / INVENTED / latent bug | med–high | med | `spitter_attack.cdl` timeline (docs/22). Spitter request slot 0 `spitter_projectile`, attachment 104 (docs/29 §5c). `spitter.ai` `AlertRadiusNear 4` / `Far 20` (0x1135/0x114f), `AlertProbability 1/1`, `AlertDelay 0.25–1.0` (0x11f0/0x120a), `AlertRepeatMoratorium 4.0` (0x1224), `TurnInPlaceSpeed 180` (0xc04). `spitter_onraisealert.cdl` `Radius 20`, `Aggressor` field. `spitter_spawn.cdl` ApplyBuff `Invulnerable` 1e8 s; `spitter_invulnerability.cdl` (untraced). |
| 12 | **Navigation is April2018-only whatever map is selected.** `LEVEL_PAK` and `routes()` (built from `solo_modes.HIVES.bot_positions`) are hard-coded, yet HotH_02 and the tutorials have `bot_positions`. Off-mesh jump links are dropped. | INVENTED scope / integration gap | high on non-April maps | low | `NvGameSdk/levels/<level>/level.pak` `mnmnavmission0.bai` per level. `JumpTraversalSpeed 6.0` (0x878) and `JumpArcFraction` for off-mesh links. `NavigationLayerName "Snurtles"` (0x8a8) vs the only MNM agent `MediumSizedCharacters` (this audit) is unresolved. |
| 13 | **Patrol feel.** There are no idle pauses between hops. The distance rules may be inverted. Patrol and chase speeds (5.5 / 8.0) are a guess against `SpoofedPlayerInputs`, and speed changes instantly with no acceleration. | DERIVED-questionable / MISSING | med | med | `IdleParams.IdleAnimationDurations` (10.8, 5.8, 4.67, 11.73, 9.67, 2.67; 0x1c6..0x248), `ExtraIdleTimeAtEndOfAnim 1.0` (0x263). `PatrolParams` `MinDistanceFromCurrent 0` (0x2f6), `TargetPosGenerationAttemptsUntilIgnoringDistanceToCurrentRule 50` (0x32a). `StartStopAcceleration` / `CruiseAcceleration 10` (0x7f6/0x810), `MaxSpeedMultiplier 1.1` (0x82a). Semantics: exe code referencing those strings, and the `SpoofedPlayerInputs` control path. |
| 14 | **Teams and targeting.** Every non-player entity is team 2, so bots never fight each other or creatures. Roster size 4, all-Marine bots and "bots cluster at the player's drop" are invented for FFA/Doubles modes. | INVENTED | med | med | `.gamemodeconfig` alliance fields (`FreeForAll`, `StaticAllianceMode`, `CanFormDynamicAlliancesAfterPartnerDies`) and team sizes. Creature `TargetTypes "Player"` (0x116e) covers bot hunters too. Guide AIs exist for Inventor/Duelist/Saboteur/hero. HOTH_GUIDE `SpawnNode_Guide_EnemyBot` 33..44. `assets/bakednames/guidebotidentifier.names`. |
| 15 | **HUD state: ammo, ability icons, own health bar.** `EntityResourceCollection` (46), `AbilityComponent` (6) and the player's Health are never replicated, which is consistent with the empty ability icons (docs/24). | MISSING | med–high | high | Type 46 autonomous schema (92 bits) via the `inspect_match_startup.py` graph capture. `abilities.updates` endpoint `0x668cda0` and HUD console `Unknown ability icon` check (docs/24 addendum). `_Single_Endpoint` debug cvar at rva `0x679b0ff`. |

---

## 1. `server/hunter_ai.py` — Marine hunter state machine

All offsets below are in `ai/guide_dummies/marine_guide_dummy.ai` unless noted
(full dump: `marine_guide_dummy.ai.flat.txt`).

### 1.1 Constants in `MARINE`

| Constant | Value | Class | Citation / reasoning |
|---|---|---|---|
| `aggro_near` | 5.0 | SOURCED | `AggroRadiusNear` 0x1092 |
| `aggro_far` | 40.0 | SOURCED | `AggroRadiusFar` 0x10ac |
| `aggro_angle` | 120° full cone | SOURCED (value), DERIVED (use as full cone ±60°) | `AggroAngleFar` 0x10c6. "Full width" is the natural reading; creature_ai uses the same reading. Plausible, unverified. |
| `deaggro` | 50.0 | SOURCED | `DeaggroRadius` 0x10e0 |
| `attack_delay` | 0.25 | SOURCED | `AttackDelayForNewTargets` 0x12a6 |
| `range_min` / `range_max` | 0 / 30 | SOURCED | `Attacks[0].RangeMin/Max` 0x1339/0x1353 |
| `fire_interval` | 2.0 s | **INVENTED** in effect | Derived from `Attacks[0].Duration` 2.0 (0x1387) + `MinTimeBetweenAttacks` 0 (0x12c0), which is sound for *attack* cadence. The module then emits **one bullet per attack**. The per-bullet cadence is ≈96 ms (item 1). |
| `patrol_speed` | 5.5 | DERIVED, questionable | `marine_default.movementconfig` Movement Speeds[0] (0x54). The reasoning is that the `SpoofedPlayerInputs` control mode (0x2e) makes the movement config the cap. Sound as a cap, but patrol then never uses lateral/back speeds (4.5/3.5) and has no acceleration (`Acceleration 0.15` 0x77). |
| `pursue_speed` | 8.0 | DERIVED, **unsound** | `SpeedChase` 0x7c2. Under spoofed player inputs, 8.0 exceeds the non-sprint cap of 5.5, and sprint is a separate ability (`marine_sprint.ability`, Ability3) that the bot never activates. `MaxSpeedMultiplier 1.1` (0x82a) is ignored. Retail more likely chases at 5.5 (×1.1?) unless it sprints. |
| `turn_rate` | 90°/s for all turns | DERIVED, unsound | `TurnInPlaceSpeed` 0xc93 is a *turn-in-place* rate, applied here to moving turns too. Movement `Max Turn Speed` is 1440°/s (0x143). |
| `patrol_near/far` | 5 / 15 from current position | SOURCED values, DERIVED semantics | `PatrolParams.MinDistance/MaxDistance` 0x2c2/0x2dc. The asset also has `MinDistanceFromCurrent 0` (0x2f6) and `MaxDistanceFromCurrent 50` (0x310). The code reads 5–15 as distance from current and 50 as distance *from anchor*. The field names suggest the opposite pairing. Needs the exe consumer. |
| `patrol_anchor_limit` | 50 (from anchor) | DERIVED, possibly inverted | see above; `TargetPosGenerationAttemptsUntilIgnoringDistanceToCurrentRule 50` (0x32a) also unmodelled |
| `patrol_attempts` | 5 | SOURCED | `MaxAttemptsForFindingPatrolDestination` 0x2ab |
| `repath_interval` / `repath_distance` | 0.5 s / 1.0 m | INVENTED | Retail has `PathVerificationParams` (0x95f..0x993), `ForcePathfindingWhenLineOfSightBlockedDuration 0.5` (0xa58) and `TimePathWasSuboptimalForToAbandonTarget 10` (0x9c5). None is used. |
| RNG seeded by entity id | — | INVENTED (test determinism) | Retail is random. Harmless, but every restart replays the same patrol. |

### 1.2 Policies and decisions

| Decision | Class | Notes |
|---|---|---|
| States patrol/pursue/attack/return | DERIVED | The retail state set is the engine's (Dormant/Sentry/WakeUp/Idle/Patrol/Flee/Deciding-to-flee blocks exist in the asset); "return to anchor" is not authored (`StayCloseToAnchorParams.MaxDistanceFromAnchorPos 0`, 0xa89). |
| Stop at `RangeMax` and stand | **INVENTED** | Contradicts the authored strafing and approach parameters (item 5). |
| Fire regardless of facing | **MISSING** attack cone | `Attacks[0].Angle 90` (0x131f). |
| Vertical attack limit | MISSING | `RangeVerticalMax 20` (0x136d). |
| Distance measured feet-to-feet | DERIVED-unsound | `AggroOriginOffsetLocal (0,0,1)` (0x103e) and `AggroTargetOffsetVertical 1.0` (0x1061) are ignored here but honoured in creature_ai. |
| Unreachable target → stand and face | INVENTED | Retail: `DecidingToFleeParams.CanFleeWhenFailingToGetToCombatTarget True` (0x422), `MaxTimePermittedToBeStationaryWhenChasingTarget 5.0` (0x712), `StuckChasingTargetCalculationMoratorium 5.0`, flee parameters 0x35b..0x3da. |
| Single target (the player only) | INVENTED | `CurrentTargetDistanceBias 3.0` (0x10fa) implies multi-target selection. `TargetTypes "Player"` (0x116e). In FFA the other bots are Players too. |
| No damage input | MISSING | `ForcedAggroDurationOnDamage 6.0` (0x1114); `CanBeWokenUpByDamage` (0x77). |
| No LoS | MISSING | 0x107b, 0x12da, 0x1420. |
| No alerts | MISSING | `AlertRadiusNear 7` / `Far 15` (0x11c4/0x11de), probability 1.0/0.0, delay 1–2 s, moratorium 4.0. |
| No fleeing | MISSING | `CanFleeForDamagedReasons False` (0x40b); the health-vs-flee curve exists but damage-flee is off. Only the stuck-chase flee applies. |
| No idle pauses | MISSING | `IdleParams` (item 13). |
| No sentry/dormant | SOURCED as no-op | `StartDormant False` (0x1027), `CanTransitionFromIdle False` (0xbc). Correctly not modelled. |
| Yaw convention `atan2(dx, dy)` | DERIVED, internally consistent | Opposite sign to the Cry convention used in `shard_stub.pack_upright_rotation` and `creature_ai.facing_yaw`. The shard passes `step.facing` (a vector), so the wire is correct. See §13. |

---

## 2. `server/hunter_patrol.py`

| Item | Value | Class | Notes |
|---|---|---|---|
| `ROUTE_STEP` | 1.0 m | INVENTED (engineering) | Tested to keep samples within 0.05 m of the mesh. Low impact. |
| `routes()` / `circuits()` from `solo_modes.HIVES.bot_positions` | April2018 drop points | INVENTED scope | The drop points are SOURCED (entries 181/246/268). Circuits between them are invented (docs/21 already calls the hard-coded routes an invention). They are still used for bot start positions under `CRUCIBLE_SOLO_PATROL` and as the `Bot.route` fallback. The functions are **map-blind**, so a HotH_02 or tutorial profile would get April2018 coordinates. |
| `STANDING_HEIGHT_OFFSET` | 0.0 | SOURCED (live) | 5,783 input samples, mode −0.03 m (docs/21). |
| `SEPARATION` | 2.0 m | SOURCED value | `NavigationParams.RepulsionMarginMin` at **0x8fb** (the code comment is right; docs/21's "0x8f2" is wrong). `RepulsionMarginMax 2.7` (0x915), `MoverRadius 0.5` (0x8c7) and `Mass 1.0` (0x8e1) are unused. |
| `separate()` relaxation | 32 passes, plan view | INVENTED algorithm | Retail uses a constraint solver (the pushability flags in StrafeParams hint at it). Medium visual impact when bots bunch up. |
| `STOP_DISTANCE` | 1.8 | SOURCED value, **dead code** | `StopDesiredDistance` 0x9f6 (docs/21 says 0x9c4, which is wrong). Nothing reads the constant. |
| `ground()` tolerance | 3.0 m | INVENTED (engineering) | Fine. |
| `sample()` ping-pong | 5.5 m/s | INVENTED | Legacy patrol probe only. |
| `unpack_position` | 22/22/20-bit | SOURCED | inverse of `0x4f47e10` / `0x4f47d10` |

---

## 3. `server/navigation.py`

| Item | Class | Notes |
|---|---|---|
| MNM v7 reader, tile layout, link encoding | SOURCED | Lumberyard `NavigationSystem::ReadFromFile`, `Tile.h`, `MeshGrid.cpp`. Consumes the file exactly. |
| `NEIGHBOURS` table | SOURCED | `MeshGrid.cpp` |
| Vertex quantisation 1/2048 m | SOURCED | `Tile.h` |
| Agent `MediumSizedCharacters` | SOURCED as the only agent in `april2018/mnmnavmission0.bai` (this audit: 1 agent, 1,397,605 bytes). The Marine guide AI's `NavigationLayerName` is **"Snurtles"** (0x8a8), a different concept (probably a Crucible nav layer or area mask) that is unexplained. |
| `COLLINEAR_TOLERANCE` 0.02, `VERTICAL_TOLERANCE` 0.5, `MINIMUM_PORTAL` 1e-3, `INDEX_CELL` 8 | INVENTED (engineering) | These are reasonable. Dropping 430 vertical links leaves 3.8 % of the mesh unreachable (docs/21). |
| Off-mesh links excluded | MISSING | These are the authored jump traversals: `JumpTraversalSpeed 6.0` (0x878), `JumpArcFraction 0` (0x85e), `GravityMultiplier 1.0` (0x844). |
| A* cost centroid-to-centroid, simple funnel | DERIVED | Standard. Retail CryAISystem uses the same MNM pathfinder family. Path shape differences are low impact. |
| `wander_point` samples at origin Z with 2 m tolerance | INVENTED | Can miss multi-level destinations. Low. |
| `LEVEL_PAK` hard-coded to April2018 | INVENTED scope / integration gap | `installed_mesh(level)` accepts a path, but no caller passes one (§12). |
| `route()` (midpoint) | legacy | unused by the AI path |

---

## 4. `server/combat.py`

| Item | Value | Class | Citation / notes |
|---|---|---|---|
| `MARINE_PRIMARY` damage max/min | 20 / 10 | SOURCED | `marine_projectile_primary.projectile` `HealthChangeImmediateOnHit` 0x392 |
| falloff begin/end | 0.064 / 0.096 s | SOURCED | `LifetimeSeconds` Vec3 0x0bc; descriptions `0x6dee2a0/0x6dee290` |
| `falloff_ratio` | 1 − clamp01((t−b)/(e−b)) | SOURCED | `CalculateFalloff` `0x3220510`, both degenerate cases ported. The sub-tick flight-time correction (docs/20) is **not** ported: low impact. |
| linear Min+(Max−Min)·ratio | — | DERIVED (sound endpoints, inferred shape) | consumer `0xddae10` unread (docs/20 Q3) |
| speed 625, lifetime 0.4, crit ×2 | SOURCED | 0x054, 0x0bc, 0x3d4 |
| `distance=None` → max damage | INVENTED default | The shooter position is available (decoded input) and the target Location is known, so distance can be DERIVED. |
| critical never set | MISSING | `preciseCriticalHits 1` (0x3ee) |
| weapon defaults to Marine for every shooter | **INVENTED** | Wrong for any non-Marine hero (item 7). `apply_reported_hit` is only called for the player, whatever `CRUCIBLE_SOLO_HERO` is. |
| projectile-id gate: forward mod-256 window < 128 | INVENTED policy | A safety gate. Retail validates against the `HybridProjectileActivations` ring / `NextProjectileId` (autonomous record, docs/29 §1). This could reject legitimate out-of-order UDP results. Low. |
| exactly-one-target rule | DERIVED | from `DynamicHitSetting = Despawn` (docs/20); sound |
| `TargetFilter Enemies` via team ids | SOURCED filter, INVENTED team assignment | see §7 |
| respawn 4.0 + 2.0 (dummy), 5.0 + 0.0 (spitter) | SOURCED | the two practice `.cdl` graphs (docs/20) |
| spitter respawn measured from death | DERIVED-simplified | Retail polls for absence every 1.0 s (`PRACTICE_SPITTER_POLL_SECONDS` is defined but unused), so retail respawn is 5–6 s after death. |
| respawn restores health only | MISSING | No despawn, no `SpawnSlice`, no telegraph, no position reset (docs/20 Q2). |
| `HEALTH_VALUE_BITS` | SOURCED | live graph `live-combat-layout.json` |
| comment "records 1..5 regeneration/timing" | stale | docs/27 §4 named them OutsideCurrentHot, KilledByDamageSource, KillerId, OutsideCurrentDot, SelfPersistentHealthChangeRequests |
| repeat absolute health every tick | INVENTED (loss tolerance) | Retail replicates on change, with reliable delta retransmission unknown. Low risk. |
| `apply_damage` | implemented, **never called** | integration gap (item 2) |
| `time.monotonic()` default clock | latent clock mismatch | The shard always injects session time. The default is a different domain (§13). |
| `HealthChangeOverTimeOnHit` | ignored | Marine primary is (0,0,0), so correct for Marine. The spitter projectile is (100,100,25) (docs/22) and would be MISSING once creatures deal damage. |

---

## 5. `server/creature_ai.py`

### 5.1 Profiles

| Profile field | Class | Notes |
|---|---|---|
| spitter / spitter_noaggro / megadrone / snurtle radii, angles, deaggro, forced aggro, attack delay, min time between attacks, LoS-forget, attack angle/range/duration, rotation-disallowed min/max, alert radii/delays/moratorium, `StartDormant`, `WakeUp` | SOURCED | Verified in this audit against `snapshots/creature-ai-20260921/*.flat.txt` and a fresh `spitter.ai --flat`. |
| `health_max` megadrone/snurtle = 0.0 | INVENTED placeholder | slice health not read |
| `sensing_radius` megadrone/snurtle 20.0 | SOURCED | 0xc6 in both flats |
| `AlertProbabilityNear/Far` | MISSING | spitter 1/1, megadrone 1/1, snurtle 1/0 |
| `TranslationDisallowedPeriod` | MISSING | snurtle 0–2.5 (0x13bb/0x13d5); movement is unimplemented anyway |
| `FleeingBlockedWindows`, `TimeRememberingWantingToFlee` | MISSING | snurtle / megadrone |
| `PassiveTurnParams.TurnInPlaceSpeed` | **MISSING** | spitter 180°/s (0xc04), noaggro 180, megadrone 720, snurtle 90. `_face` snaps instantly. |
| `HeadLookParams` angles | MISSING | spitter ±30°: the head may track without a body turn. Unknown split. |
| `SentryTransitionParams` / `SentinelEnemyCheckInterval 2.0` | MISSING | `CanTransitionFromIdle False` for the spitter, so it is irrelevant there. |
| `IdleParams.IdleAnimationDurations` | MISSING | presentation only |

### 5.2 Policies

| Decision | Class | Notes |
|---|---|---|
| `lowest()` picks the min of every [min,max] | INVENTED (determinism) | Retail randomises (docs/22). Rotation lock is 0.5 s instead of 0.5–1.0 s. Low. |
| `visible` defaults to True | MISSING LoS | spitter `AggroCheckLineOfSight True` (0xfec); the 8 s LoS forget can never fire |
| rotation lock = first `RotationDisallowed` seconds of the attack | DERIVED, unverified | Interval placement in the attack is not traced. |
| target sampled at fire time t+1.1 | DERIVED-off-by-0.1 s | The script reads the target Location **before** the final `Delay 0.1`, at t+1.0 (docs/22). `CreateProjectile` then uses `AIComponent.AimPosition`, which is untraced. |
| megadrone/snurtle do not move | MISSING | `MovementEnabled True`, `CanPatrol True`, chase 9.0 / 4.64, strafing (megadrone), `ActiveTurn` (snurtle). No map currently has creature placements, so this is latent. |
| `alert_nearby` fan-out radius = `script.alert_radius` 10 | **INVENTED / misread** | The radius-10 `SphereShapeEntityQuery` in `spitter_attack.cdl` finds **Players** near the spitter. The creature-to-creature fan-out uses `AlertRadiusNear/Far` 4/20 with `AlertTypes "Spitter"`, delay 0.25–1.0 s and moratorium 4 s. `spitter_onraisealert.cdl` carries `Radius 20`. |
| `alert_near(..., aggressor_id)` | **latent bug** | The same id is used to skip the source creature *and* as the new target. docs/22's integration note passes `event.creature_id`, which would make alerted spitters target the alerting spitter. `AIRaiseAlertEvent` carries an explicit `Aggressor` (spitter_onraisealert.cdl). |
| `note_damage` aggro even when forced | DERIVED | fine |
| `Invulnerable` buff on spawn | MISSING, possibly material | `spitter_spawn.cdl` applies `Invulnerable` for 1e8 s on OnSpawnEvent. Whether `spitter_invulnerability.cdl` removes it (on wake/aggro?) is untraced, so retail spitters may be unkillable until some trigger. The Combat module lets them be killed at once. |
| `CreaturesScanWhenAwoken` 1-damage pull | MISSING | mode-flag gated (HOTH_MINIMAL only, docs/23) |
| `pack_rotation` / `pack_position` | SOURCED | `4f47e10`; WXYZ correction (docs/17) |

---

## 6. `server/practice_population.py`

| Item | Class | Notes |
|---|---|---|
| spitter slice hash 1141747700, dummy entries 73..80, positions, 180° dummy yaw | SOURCED (asset + live accepted) | docs/17 |
| `SPITTER_SHARED_TYPES`, `DUMMY_SHARED_TYPES` | SOURCED | schema snapshot / live |
| spitter network ids 0x01000003/04 | INVENTED allocation (server's choice; acceptable) | These **collide** with `HUNTER_BOT_IDS`. It is harmless only because no profile has both creatures and bots (§13). |
| health 400 / 600 | SOURCED | slice + live |
| Practice spitters run aggressive `spitter.ai` | SOURCED | slice 0x1dcf |
| all other maps empty | honest placeholder | April2018 has 89/97/62 creature spawners (docs/22), so every non-Practice map is creature-less: a large MISSING population (HotH, tutorials). |

---

## 7. `server/solo_modes.py`

| Item | Class | Notes |
|---|---|---|
| `MATCH_PHASE` enum, `SOLO_PHASE` 8 | SOURCED | `.rdata 0x65bc9c0` |
| level hash (Jenkins OAT, lowercase) | SOURCED / computed | docs/23, 942/942 fit |
| per-map baked entries | SOURCED | inspect_map_entities |
| baked bias practice 2 / april2018 787 | SOURCED (live) | |
| baked bias HotH_02 / tutorials = 2 | INVENTED placeholder | Marked 'unmeasured'; docs/23 suggests the bias may be free. |
| phase sequences and durations | SOURCED | gamemodeconfigs. Only GamePlay is ever sent (see §12.3). |
| `roster_size` 4 (FFA/Doubles/HotH), 2 (1v1) | **INVENTED** | Retail rosters are 8 or 16 players (MinimumPlayers). Three bots is a presence-test count. |
| `local_team` 1 / `bot_team` 2 | **INVENTED** | FFA modes (`FreeForAll` flag) put every hunter on its own team. DOUBLES pairs players. |
| bot spawn = drop cluster next to the player | INVENTED for match modes | The authored drop-select phase (DropPreview/DropSelect) spreads players. HOTH_GUIDE authors `SpawnNode_Guide_EnemyBot` 33..44. |
| tutorial `bot_positions` = snurtle AIOverlord nodes / dummy spawns | INVENTED stopgap | Marine hunters at creature spawn nodes. docs/23 says so. |
| `'hoth-guide'` etc. on HotH_02 | SOURCED selection, unmeasured runtime | |

---

## 8. `server/hero_loadout.py`

| Item | Class | Notes |
|---|---|---|
| CharacterClass enum values | SOURCED | `.rdata 0x6c1de08`, accessor `0x432a380`. `Spectator 22` is not separately cited. |
| slice hashes, roster order | SOURCED | stringhashlibrary, roster asset |
| `SHARED_TYPES` / 25 autonomous roots for all heroes | SOURCED (live Marine) + DERIVED (component-set equivalence) | sound |
| `ABILITY_SLOTS` | SOURCED (`abilities.js`) | It conflicts with the asset-side finding that `marine_aimdownsights.ability` has `abilitySlot None`, so Marine has no Ability4 (docs/24 addendum). The table is the JS, not the rendered HUD. |
| ability lists | SOURCED | loadouts. Marine's 14th entry (`stance_crouch`) is missing (docs/24 addendum). |
| playerdata payload skin names (`<hero>_t1_default_01`, `droppod_default_01_var01`) | INVENTED | wired in `crucible_backend.handle_playerdata`. The live 22 Sep run selected Saboteur/Ajonah, so the route is used. |
| per-hero health, weapon, movement | MISSING | The shard uses `MARINE_HEALTH_MAX` 800 and `MARINE_PRIMARY` for every player hero (item 7). |
| bots always Marine | INVENTED (accepted baseline) | Guide AIs exist for Inventor (5.5), Duelist (7.5), Saboteur (6.5), Hero (6.5). |

---

## 9. Animation (`animation_events.py` + shard policy)

| Item | Class | Notes |
|---|---|---|
| 26-root property map, float/bool widths, shared bool cursor | SOURCED | serializer `0x7e3a10`, live graphs, `animation-wire-map.json` |
| record 16 `time_since_movement_input` as a 0/1 flag with a 0.1 s threshold | SOURCED (native producer `0x3292744..0x329277b`, CVar `0x7bbecd0` = 0.1) | |
| proxy: bot `speed > 0` means "input present" | DERIVED, sound for a `SpoofedPlayerInputs` bot | The retail server derives it from the spoofed inputs. A blocked bot pushing into a wall would read "moving" in retail and "idle" here. Low. |
| `using_primary1` one-update pulse per shot | **DERIVED-unsound** | `marine_primary_onfire.cdl` holds `using_primary1 = True` from trigger-press to `OnExit` (nodes 18 → 143). The animgraph re-triggers recoil every 0.1 s while held, matching the ≈96 ms shot period. It should be held for the burst. |
| `is_aiming` during ATTACK | INVENTED | Retail Marine ADS is a separate ability (`marine_aimdownsights`, slot None). There is no evidence that bots ADS. It changes the reload/aim pose. Medium. |
| `using_aim_ik` | MISSING | The onfire script sets it True on the first shot (node 93). |
| `resource1_ratio` | MISSING | The onfire script writes 0.0 while firing and 1.0 on exit (nodes 46/149). |
| `is_dead` for hunters | DERIVED | animgraph `Root base->death` |
| creatures: no animation at all | MISSING | The spitter wind-up `using_primary1` (1.0 s) and snurtle/megadrone scripts set it server-side (docs/22). Spitter shared types include 13. |
| `AimDirection` (packed quat), `AnimationSyncInfo` | MISSING / unresolved | Aim pitch is never sent, so bots aim level. `turn_angle` is not sent (idle_turn uses `turn_speed`, which is client-local). |

## 10. Death metadata (`death_events.py`)

| Item | Class | Notes |
|---|---|---|
| traversal (T,F,T,T,F,F,T,T,F,T), `>HHI` then `>fff` | SOURCED | serializers `0x93d520`, `0x7cb2c0`, `0x35dc910`; live graph |
| source/ability IDs | INVENTED at run time (operator-supplied env) | The source-backed values are available: `marine_primary` AbilityName 51; the HealthChangeSource table is at `0x6c1e0c0` (count 24, 0 = Unknown). The correct *source* enum for a projectile kill is not chosen. |
| reset/repeat-death semantics | MISSING | docs/27 §5 |
| `previous_id` bound 0xffffff (others use 0xfffffe) | minor inconsistency | |
| `SelfPersistentHealthChangeRequests` (65-byte request) | MISSING | This is probably the path to damage floaters and flinches on the victim (docs/27 §8b). |

## 11. Projectile / fire (`projectile_events.py` + shard policy)

| Item | Class | Notes |
|---|---|---|
| 15-decision slot traversal, 33-byte value layout, zigzag-LEB128 time, shared bool byte, attachment 0 | SOURCED | docs/29 (§3–§6), `ValidateNetData 0xbc3680` |
| Marine primary `AllowedProjectileIndex` 4 | SOURCED | `marine_projectile_request.projectilereq` |
| `fire_origin = ideal_origin = feet` | **INVENTED** | item 3 |
| `ideal_target = player feet` | **INVENTED** | item 3 |
| one activation per packet | INVENTED (conservative) | docs/29 says it is not a wire requirement. At the retail cadence (item 1) it would drop most shots. |
| slot ring per shooter starting at 0 | DERIVED | Slot choice and retirement are unverified. |
| discard after 0.4 s | SOURCED lifetime, DERIVED use | |
| `HasFirstUpdateHit` False | DERIVED (constructor default) | Server-side hit on the first update is not modelled. |
| no hit authority for bot shots | MISSING | item 2 |

## 12. Hit reaction (`hit_reaction_events.py`)

| Item | Class | Notes |
|---|---|---|
| records 1/2, `>Bf` | SOURCED | `0x943fa0` → `0x79fcd0` / `0x7f32c0` |
| index semantics / yaw units | MISSING | Candidate: animgraph `hit_type` FloatSpinner 0..1 and `hit_yaw` FloatSlider −180..180 in group `hit_reaction` (docs/28 table), suggesting **degrees** relative to facing. Trace `HitReactionComponent::Client` → AnimationParameter writes. |
| integration | none | Encoder only. |

---

## 13. `server/shard_stub.py` — simulation and replication

### 13.1 Clock and cadence

| Item | Value | Class | Notes |
|---|---|---|---|
| clock epoch | `ServerGameTimePoint` starts at **2000 ms** | INVENTED | Chosen so the stalled startup value keeps advancing (docs/16). Harmless, but not retail. |
| clock/update rate | ≤20 Hz (`0.05` s gate), timer-driven at 50 ms socket timeout | **INVENTED** | docs/16 "up to 20 Hz". The client sends input at **60–61 Hz** (this audit). Retail defaults for `net_TickRate` / `sv_MaxServerUpdateRateMs` / `sv_SystemTickRateMs` are not yet read. It affects bot motion smoothness, the fire queue cap and the input-offset budget. |
| simulation time | `elapsed_ms / 1000` (ms-truncated, session-relative) | DERIVED, consistent | `observe_solo_combat` uses the same domain (docs/30 fix). |
| bot Location updates | every tick, all bots | DERIVED | Retail cadence is unknown and relevance/priority is not modelled (`sv_MinEntitiesToReplicate`, `sv_ReplicateServerProxies` exist). |
| health repeated every tick for every touched entity | INVENTED loss tolerance | |
| message order per tick | hunter Location → animation → one activation → creature rotations → health | INVENTED ordering | Each piece is a separate EntityMessage; the RelativeId chaining is SOURCED. |

### 13.2 What is replicated

| Entity | Component / record | When | Source |
|---|---|---|---|
| connection 01000001 | MatchDataReceiver `MyId`; LoadoutSetup `Character`; Hierarchy children and follow/control ids | bootstrap | SOURCED (live accepted) |
| player 01000002 | spawn: Location InternalTransform, Hierarchy parent; autonomous record all-false | once | SOURCED (live) |
| player 01000002 | **nothing after spawn**: no Health, no ammo/resources (46), no abilities (6), no Confirm* RPCs, no input correction | — | MISSING (items 2, 9, 15) |
| baked MatchPhase | `CurrentPhase = GamePlay` only; `TimeRemaining` and `StartTimeSeconds` never sent | once | SOURCED encoding; the phase *progression* (WaitForPlayers → … → GameOver) is MISSING |
| baked GameMode | configuration index | once | SOURCED |
| baked DataStoreMatch | PlayerList (1 or 4 entries), CharacterIds, NumPlayersInMatch; teams 1/2; slot 1 DropMaster | twice | SOURCED encoding, INVENTED roster/teams |
| hunters 01000003..05 | spawn (Marine slice, simulation role); Location every tick (AI or patrol); opt-in AnimationParameter records 4/8/16/17; opt-in `HybridProjectileActivations`; Health triplet once damaged; opt-in death metadata | per tick | mixed, see above |
| Practice spitters 01000003/04 | spawn; Location **rotation only while targeting**; Health once damaged | per tick | SOURCED encoding |
| RoboDummies FE00004B..52 | activation; Health once damaged | per tick | SOURCED encoding |
| never on the wire | AIComponent (2), HitReaction (80), DamageFloater (36), IncomingHitIndicator (81), Respawn (120), GameAlert (141), AudioComponent `PlaySoundEffect` (155/0), `PlayerKillCounter` / `LastKilledPlayer` (115 autonomous), EntityResourceCollection (46), AbilityComponent (6) | — | MISSING |

### 13.3 `CRUCIBLE_*` behaviour flags (all default off)

| Flag | Effect | Default-off consequence |
|---|---|---|
| `CRUCIBLE_SHARD_BOOTSTRAP=practice-character` | full solo bootstrap and clock | required for everything below |
| `CRUCIBLE_PRACTICE_NPCS=1` | sends the population (Practice NPCs or hunter shells) and calls `start_solo_simulation` | Despite its name, no bots or combat without it on **any** map. |
| `CRUCIBLE_SOLO_PATROL=1` | stateless ping-pong routes (April2018 geometry) when AI is off | invented routes |
| `CRUCIBLE_SOLO_AI=1` | hunter_ai squad + creature director | bots stationary without it |
| `CRUCIBLE_SOLO_COMBAT=1` | Combat arena (player hits only) | hits ignored |
| `CRUCIBLE_PRACTICE_DAMAGE=1` | legacy nonlethal probe (skipped if combat on) | superseded |
| `CRUCIBLE_SOLO_FIRE=1` | bot projectile activations | bots fire invisibly |
| `CRUCIBLE_SOLO_MOVEMENT_ANIMATION=1` | record 16 idle/move | bots run in place when stationary (native default 0) |
| `CRUCIBLE_SOLO_COMBAT_ANIMATION=1` | is_dead / using_primary1 / is_aiming | no death or recoil animation |
| `CRUCIBLE_SOLO_DEATH_EVENTS=1` + `_SOURCE_ID` / `_ABILITY_ID` | death metadata | health-0-only death (proven invisible, docs/20) |
| `CRUCIBLE_SOLO_MODE`, `CRUCIBLE_SOLO_HERO`, `CRUCIBLE_BAKED_BIAS`, `CRUCIBLE_LEVEL_HASH` | profile selection | Practice + Marine |

Net effect: the retail-closest configuration needs **eight** flags set
together. The default path is a static diorama. None of the presentation flags
(FIRE, MOVEMENT_ANIMATION, COMBAT_ANIMATION, DEATH_EVENTS) has been observed
live (docs/30).

### 13.4 Tested modules not wired into the live path

- `combat.Combat.apply_damage`: never called, so NPCs cannot hurt the player.
- `creature_ai.CreatureDirector.alert_near`: never called; `alert_nearby` events are only logged.
- `creature_ai.CreatureAttack`: logged only. No spitter projectile activation, no animation, no damage.
- `hit_reaction_events.build_hit_reaction_update`: no caller.
- `hunter_patrol.STOP_DISTANCE`: no reader.
- `combat.PRACTICE_SPITTER_POLL_SECONDS`: no reader.
- `navigation.installed_mesh(level)`: the level argument is never supplied.
- Health requests (65-byte), DamageFloater (36), IncomingHitIndicator (81): documented, no encoder.
- ProjectileRequest `ConfirmHitIndicator` / `ConfirmKillIndicator` RPCs: documented, no encoder.

---

## 14. Cross-module disagreements

| # | Where | Disagreement | Risk |
|---|---|---|---|
| D1 | `hunter_ai.yaw_of` vs `shard_stub.pack_upright_rotation` / `creature_ai.facing_yaw` | yaw sign: `atan2(dx,dy)` vs Cry `atan2(-dx,dy)` | Safe today because the shard passes the facing **vector**. Anyone passing `step.yaw` to a Cry-convention packer mirrors the bot (docs/21 warns about this). |
| D2 | `practice_population.SPITTER_ENTITY_IDS` vs `shard_stub.HUNTER_BOT_IDS` | both claim 0x01000003/04 | Latent collision on any map that has both creatures and bots (HotH once populated). |
| D3 | `hunter_patrol.routes()` / `navigation.LEVEL_PAK` vs `solo_modes.selected()` | April2018 geometry for every map | Wrong world on HotH_02 / tutorials. |
| D4 | `combat.Combat` default clock `time.monotonic()` vs shard session seconds | two time domains | The shard always injects `now`, so this is latent. A new caller that omits `now` would schedule respawns hours ahead (the defect docs/30 fixed). |
| D5 | `combat.apply_reported_hit(weapon=MARINE_PRIMARY)` vs `hero_loadout.selected()` | weapon hard-wired, hero selectable | wrong damage for 9 of 10 heroes |
| D6 | `MARINE_HEALTH_MAX` for the player vs selected hero | health hard-wired | wrong max HP for non-Marine heroes |
| D7 | `hero_loadout.ABILITY_SLOTS['marine']` (Ability4 `marine_ads`) vs asset `abilitySlot None` | JS table vs asset | presentation only |
| D8 | `creature_ai.alert_near` aggressor vs docs/22 integration step | caller would pass the creature id | alerted creatures target a creature |
| D9 | `creature_ai` alert radius 10 vs `spitter.ai` Alert 4/20 and `spitter_onraisealert` 20 | radius misread | too-small fan-out |
| D10 | `projectile_events` / `animation_events` previous_id bound 0xfffffe vs `death_events` 0xffffff | validation drift | cosmetic |
| D11 | Combat teams (every NPC team 2) vs creature `TargetTypes Player` and FFA flags | teams are a single invented split | bots ignore creatures and each other; creatures ignore bots (they only get the player as a target) |
| D12 | `combat.py` comment "records 1..5 regeneration/timing" vs docs/27 §4 | stale doc-in-code | misleading |
| D13 | hunter `using_primary1` pulse vs `marine_primary_onfire.cdl` hold | pulse vs held flag | wrong recoil animation |

## 15. Documentation errata found

- docs/21: `RepulsionMarginMin` is at **0x8fb**, not 0x8f2; `StopDesiredDistance` is at **0x9f6**, not 0x9c4 (full flat dump). The code comments are correct.
- docs/21 / hunter_ai: "rate of fire is a separate trace". It is now traced: `marine_primary_onfire.cdl` gives a ≈96 ms shot period and a held `using_primary1`.
- docs/25: "each step is one 20 Hz tick of walking". The client input RPC rate is ≈60 Hz (16 ms spacing in `shard_udp.log`, 21 Sep 22:51).
- docs/21 §"Not implemented" lists `StrafeParams`, "BackUpParams" and "ActiveTurn" as ignored. For the Marine guide bot, `BackUpParams.Enabled` and `ActiveTurn.Enabled` are both **False** (0xbfd, 0xcf8); only Strafe (enabled) and PassiveTurn matter.
- docs/22 describes the radius-10 query as the alert fan-out. It is a Player-proximity check; creature alert fan-out uses the AI's Alert* fields.

## 16. Suggested order of evidence gathering (cheapest first)

1. Offline, no launch. Run `tools/inspect_creature_cdl.py` on
   `spitter_invulnerability.cdl`, the other Marine `.ability` scripts
   (reload, sprint), and `marine_character.dynamicslice` (resource block:
   Ammunition max, Heat, Sprint). Run `inspect_creature_ai.py --flat` on the
   other heroes' primary `.projectile` assets. This settles items 1, 7 and 11
   (Invulnerable) and part of 15.
2. Offline RE. Find the defaults of `net_TickRate` / `sv_MaxServerUpdateRateMs` /
   `sv_SystemTickRateMs` / `net_ProjectileTickRate` (the registration structs
   that hold the name pointers). Find the consumers of `StrafeParams`,
   `PatrolParams.MaxDistanceFromCurrent` and `PassiveTurnParams` in the AI
   system (the exe carries the server AI code, since AIComponent logic is
   authority-side). This settles items 5, 10 and 13.
3. One read-only live capture while the user plays. Read the player's own
   `TargetPointComponent` (TargetPoint / IdealRayOrigin) and its component-115
   values relative to its Location, plus the client's current cvar values.
   This settles items 3 and 10.
4. Flag-by-flag visual tests: MOVEMENT_ANIMATION, COMBAT_ANIMATION, FIRE and
   DEATH_EVENTS, each alone. These gate items 1, 8 and 9 before layering the
   cadence change.
