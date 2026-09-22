# Server-authoritative combat core — damage, health, death, respawn

Scope: replace the crippled `CRUCIBLE_PRACTICE_DAMAGE` probe (flat 20 HP,
clamped to 1, no death, no respawn) with a reusable module that creatures,
hunter bots and the player can all share. Everything here is **implemented
and unit-tested only**. Nothing in this document has been verified in a live
run; that needs the user to launch the game.

New files: `server/combat.py`, `server/test_combat.py`,
`tools/inspect_combat_projectile.py`. `server/shard_stub.py` is untouched —
see *Integration notes* at the end.

## The damage numbers, traced

`docs/17` recorded Marine primary as "HealthChangeImmediateOnHit float pair
(20,10), CriticalMultiplier 2" and flagged that "damage interpolation and
critical classification still need tracing". They are traced below.

### The asset

`assets/player/marine/marine_projectile_primary.projectile` in the installed
`misc.pak` (sha256 `56722f39699a7fc50494ac0ab0801a003afcaf2277f011512b1b7ca0e46adb91`,
3440 bytes) is a binary ObjectStream v3. Its element names are `crc32` of the
**lowercased** field name; resolving them against the executable's own
reflection strings names every field. `tools/inspect_combat_projectile.py`
does that resolution and prints the tree. The combat-relevant values:

| field | file offset | value |
|---|---|---|
| `SpeedUnitsPerSecond` | 0x054 | 625.0 |
| `LifetimeSeconds` (Vec3) | 0x0bc | (0.4, 0.064, 0.096) |
| `TargetFilter` | 0x323 | `Enemies` |
| `HealthChangeImmediateOnHit` (Vec2) | 0x392 | (20.0, 10.0) |
| `HealthChangeOverTimeOnHit` (Vec3) | 0x3b1 | (0.0, 0.0, 0.0) |
| `CriticalMultiplier` | 0x3d4 | 2.0 |
| `preciseCriticalHits` | 0x3ee | 1 |
| `CreateProjectileType` | 0xaf8 | `Hybrid` |
| `NetworkCloseTargetHitInfo` | 0xd40 | 0 |
| `NetworkAllHits` | 0xd57 | 0 |

`Acceleration`, `Gravity`, `Drag`, `SpeedMin` and `SpeedMax` are all zero and
`CreateProjectileBehavior` is `Bullet`, so flight distance is simply
`speed * time`.

### `LifetimeSeconds` is not one number

The edit-context descriptions for that Vec3 sit consecutively in the
projectile reflection block at file offsets 0x6dee2b0 *"Lifetime in seconds"*,
0x6dee2a0 *"falloff begin"*, 0x6dee290 *"falloff end"*. So the authored value
is `(lifetime 0.4, falloffBegin 0.064, falloffEnd 0.096)` — at 625 units/s,
**40 units and 60 units**.

### `Crucible::ProjectileUtils::CalculateFalloff`, RVA 0x3220510

Read in full. It reads exactly those slots off the projectile data asset and
returns a ratio:

```
0x3220525  movss  xmm7,[0x6e00f40]      ; 1.0
0x3220532  mov    rax,[r9+0x150]        ; projectile data asset
0x3220539  movups xmm4,[rax+0x60]       ; the LifetimeSeconds Vec3
0x322053d  movss  xmm5,[rax+0x64]       ; y = falloff begin
0x3220542  shufps xmm4,xmm4,0xaa        ; z = falloff end
0x3220546  ucomiss xmm4,0 / jne
0x322054b  ucomiss xmm5,0 / je 0x3220736 ; both zero -> return 1.0
...        xmm0 = [r9+0x3c] (flight time) minus a sub-tick correction
           derived from |hit-origin| / |end-origin| and the tick fraction
0x32205c6  subss  xmm4,xmm5             ; span = end - begin
0x32205ce  comiss xmm4,0 / ja 0x3220722
0x3220722  subss  xmm0,xmm5             ; t - begin
0x3220726  maxss  xmm0,0
0x322072a  minss  xmm0,xmm4
0x322072e  divss  xmm0,xmm4
0x3220732  subss  xmm7,xmm0             ; 1.0 - clamp01((t-begin)/span)
0x3220753  ret
```

`span <= 0` falls into the cold block at 0x32205e8, whose assert text is
*"CalculateFalloff: Entity (%s) with Projectile info asset (%s) has been
marked with a falloffstart (%f) that is greater that its falloffend (%f)"*,
and returns 1.0 unchanged. The two float constants are 1.0 (0x6e00f40) and
the 0.001 distance epsilon (0x6e0085c), both read out of the image.

So **FalloffRatio is 1 at or inside falloff begin, falls linearly to 0 at
falloff end, and stays 0 beyond it**. `falloff_ratio` in `combat.py` is a
direct port, including the two degenerate cases.

### Where the ratio goes

The caller at RVA 0x32215cb (inside `ProjectileUtils::Update`, unwind
fragment [0x3220840,0x3221d3a]) stores the returned ratio into a struct it is
building at `[rbp+0x270]`, at offset `+0x2c8`, alongside a bool at `+0x2cc`
and two entity ids at `+0x2b0`/`+0x2b8`, then passes it as `rdx` to
`0xddae10`. The struct is the reflected `DamageData` (member list at file
offset 0x6a738c3):

```
m_ExplosionCenter  m_SourceEntity  m_TargetEntity
m_HealthChangeImmediateOnHitMax   m_HealthChangeImmediateOnHitMin
m_HealthChangeOverTimeOnHitMax    m_HealthChangeOverTimeOnHitMin
m_HealthChangeOverTimeSeconds
m_CriticalHit  m_CriticalHitMultiplier  m_FalloffRatio
m_AttackOriginPosition  m_CollisionType
```

### What is implemented, and what is inferred

`combat.immediate_damage` computes

```
damage = Min + (Max - Min) * FalloffRatio        # 20 near, 10 far
damage *= CriticalMultiplier    if critical      # x2
```

The **endpoints are proven**: the authored pair is (20,10), the ratio is
provably 1 near and 0 far, and the members are literally named Max and Min.
The **linear interpolation between them is inferred**, not decoded — the
instruction sequence that consumes `DamageData` was not read to the point of
showing the `Min + (Max-Min)*ratio` arithmetic. Any other monotonic blend
would still give 20 inside 40 units and 10 beyond 60; only the shape in
between is at risk. This is marked in the code.

## Health replication

Unchanged from the verified encoding, just factored out. Health is native
network component type 79. Its live replication graph
(`snapshots/gamemode-20260919/live-combat-layout.json`) has property record
indices `[1,2,3,4,5,23]` with a nested resource subtree at 23..26; the
default-resource serializer emits five regeneration/timing floats and then
minimum, maximum and value (9355f0 -> 9359a0 -> 93d870 -> 8c3780 -> 34cbea0
-> 35dc910). With `net_CompressHeaders=1` the wire carries traversal
decisions (0x4be8240), so selecting only the value triplet is ten decisions:

```
component root, records 1..5 false, 23 Resource, 24 DefaultResource,
25 timing group false, 26 min/max/value
```

then three big-endian floats. `combat.health_traversal_bits` /
`build_health_payload` / `build_health_update` implement this for **any**
entity given its ascending network component type list, and a regression test
asserts the produced `EntityMessage` is **byte-identical** to
`shard_stub.build_practice_health_update` for every NPC the old probe
supported, at several values and RelativeId predecessors. Unlike the old
probe it accepts 0 and is not restricted to the Practice population.

Native asset health: spitters 400/400, RoboDummy targets 600/600, the Marine
character 800/800 (all observed live in `live-combat-layout.json`).

## Death

Death is **health reaching zero and nothing else**. The old probe clamped to
1 HP precisely to stay out of a death path it had not traced; that clamp is
gone, and `apply_reported_hit` now floors at 0 and reports `killed`.

No separate replicated "dead"/"alive" property was located on the Health
component. Its property record indices are `[1,2,3,4,5,23]`
(`live-combat-layout.json`) — the nested resource subtree at 23..26 is the
min/max/value triplet and the five regeneration/timing floats, and none of
records 1..5 was observed carrying a death flag. So the server publishes
health 0 and lets the client draw its own conclusion.

Whether that is sufficient is **unverified and is the main open risk in this
document**. Retail clearly has a death *event*, because
`cdl/practicearena/practicearena_targetdummydeathwatcher.cdl` is triggered by
an `OnDeathEvent` carrying `EntityID`, `ConnectionEntityId`,
`killerEntityId`, a `DamageType` pair (`SourceName`, `AbilityName`) and
`shouldBeRevivedIfPlayer`. That is the authored *script-side* event; it is
not proof of a wire message, and no server→client death RPC has been
decoded. If health 0 alone does not produce a death on screen, that event is
the next thing to trace — not a guess to encode now.

## Respawn

Practice respawning is not authored in the respawner slices at all: both
`slices/practicearena/practicearena_spitterrespawner.dynamicslice` and
`robodummyrespawner.dynamicslice` only *reference* .cdl node graphs. Read
with `tools/inspect_creature_cdl.py`, the two graphs are:

**`cdl/practicearena/practicearena_targetdummydeathwatcher.cdl`**
(sha256 `933d81b6…`) — event driven:

```
OnDeathEvent -> Condition -> Delay 4.0
  -> SpawnSlice(slices/practicearena/robodummy.dynamicslice, TelegraphTime 2)
  -> Delay 3.0 -> SphereShapeEntityQuery(radius 5, "RoboDummy")
  -> ForEach -> Set Shared_TargetDummyEntity
```

So a target is back **6.0 s** after death: `Delay 4.0` then a spawn with
`TelegraphTime 2`. The trailing `Delay 3.0` and re-query only re-acquire the
new entity's id for the shared blackboard.

**`cdl/practicearena/practicearena_spittertracker.cdl`** (sha256
`5c502d26…`) — **absence polling, not a death event**:

```
OnSpawnEvent -> SphereShapeEntityQuery(radius 2, type "Spitter")
  -> GetContainerSize -> Condition
       absent  -> Delay 5.0 -> SpawnSlice(spitter_noaggro, TelegraphTime 0)
                            -> Delay 1.0 -> re-query
       present -> Delay 1.0 -> re-query
```

Two things fall out of this that were not previously recorded. The spitter is
respawned by polling for its absence on a 1.0 s cadence, so its respawn delay
is measured from *detection*, not from death — up to one poll later than a
death-driven timer would give. And the slice it respawns is
`slices/creatures/spitter/spitter_noaggro.dynamicslice`, the **`_noaggro`
variant**, not the plain spitter. `server/creature_ai.py` has profiles for
both; which one the arena actually holds after a respawn matters to that
workstream.

The constants are in `combat.py` as `PRACTICE_TARGET_RESPAWN_SECONDS` (4.0),
`PRACTICE_TARGET_TELEGRAPH_SECONDS` (2.0),
`PRACTICE_SPITTER_RESPAWN_SECONDS` (5.0),
`PRACTICE_SPITTER_TELEGRAPH_SECONDS` (0.0) and
`PRACTICE_SPITTER_POLL_SECONDS` (1.0). `_die` sets the deadline to
`respawn_seconds + telegraph_seconds`; `tick` restores full health when it
passes. The 1.0 s poll is recorded but **not** modelled — `Combat` uses an
exact deadline. That is a deliberate simplification, not a decoded rule.

What respawn does **not** do: it restores health, and nothing else. It does
not re-send a spawn message, play a telegraph, or move the entity back to its
authored transform. In retail the respawner destroys and re-spawns a slice;
here the entity is never despawned, so "respawn" is only the health half of
it. If a killed NPC's corpse stays on screen, this is why.

## The tick API

`combat.Combat` is the whole surface:

```python
c = Combat()
c.register(entity_id, shared_types, maximum=..., team=..., local=...,
           respawn_seconds=..., telegraph_seconds=...)
hit = c.apply_reported_hit(shooter, projectile_id, reported_ids,
                           weapon=MARINE_PRIMARY, distance=None,
                           critical=False, now=...)     # -> HitResult | None
updates  = c.tick(now)                                  # -> [CombatEntity]
messages = c.build_messages(updates, previous_id=...)   # -> [EntityMessage]
```

`apply_reported_hit` keeps every gate the nonlethal probe had, now expressed
against registered entities rather than hard-coded Practice ids:

- the shooter must be a registered combatant;
- the projectile id must advance in forward modulo-256 order **per shooter**,
  which rejects replays and out-of-order results;
- exactly one registered entity on a **different team** must appear in the
  report — the shooter's own id and same-team ids are dropped, which is the
  asset's `TargetFilter = Enemies`, and an ambiguous multi-target report is
  refused because Marine primary despawns on collision
  (`DynamicHitSetting = Despawn`);
- a dead target cannot be hit again.

Strict decoding of the RPC itself stays where it already is
(`shard_stub.parse_entity_rpcs` / `parse_projectile_result`); `combat.py`
takes the already-decoded `(projectileId, reportedIds)`.

`tick(now)` advances respawn deadlines and returns every entity that has ever
changed, so each entity repeats its **absolute** health on every clock tick
and a dropped UDP datagram cannot lose a hit, a death or a respawn — the same
loss tolerance the existing probe has.

The `Combat` class has no knowledge of Practice: NPCs, hunter bots and the
player are all just registrations with a team and a component-type list.

## First live run, 2026-09-21 22:03–22:15: damage lands, death does not

The user played the Practice profile with `CRUCIBLE_SOLO_COMBAT=1` and
`CRUCIBLE_SOLO_AI=1`. This section is **live evidence**, not inference.

**Damage is correct and the transport is sound.** Two bursts against target
`fe00004b` produced 5 then 21 then 30 accepted hits, every one exactly 20
damage, health stepping 600 → 580 → … with no missed, duplicated or
out-of-order result. The gates held: no other entity ever took damage. The
client kept acknowledging throughout (`RemoteSeq` 938 → 962 across the
burst) and **never raised `StreamError` and never disconnected** — which
matters, because a malformed component payload is exactly what dropped the
connection in `docs/18`. The health update therefore *deserialises cleanly*.

**Death does not happen client-side.** The 30th hit logged

```
SOLO COMBAT hit target=fe00004b damage=20 health=0 KILLED
```

and the user reports **nothing visible happened at any point** — no damage
reaction, no health change, no death, through the whole 600 HP. The
connection survived the kill.

So the question this document flagged as "the main open risk" is now
answered, negatively and with evidence:

> **Replicating Health 0 is not sufficient to produce a visible death.**

And the weaker claim falls with it: replicated health produces no visible
*damage* feedback either, at any value. Since the packet parses and the
connection lives, the client is accepting the health value and simply not
acting on it. That points away from an encoding bug and toward a missing
*event* — the thing `OnDeathEvent` and the `ProjectileRequestComponent`
confirmation RPCs (`ConfirmHitIndicator`, `ConfirmKillIndicator`) exist to
carry. `docs/21` records that all four of those are shooter feedback, so the
damage-taken path is a separate, still-undecoded trace.

What this does **not** mean: the combat core is not wrong. Its arithmetic,
gating, replay suppression and respawn timing are all doing what the assets
say. They are simply invisible until the event layer is found.

Respawn could not be observed for the same reason — the target never
visibly died, so its return to 600 HP 6.0 s later had nothing to show. The
server-side transition is unit-tested and was confirmed by hand after the
run (0.0 at death, 600.0 at t+6.0).

Also confirmed live in the same run: `server/client_input.py` decoded the
player's position from the client's own input stream on the first packet —
`CLIENT INPUT decoded: player at (501.09, 564.54, 45.27)`, exactly the
authored Practice spawn. `docs/25` is verified rather than inferred.

Creature AI produced zero events, which is **correct**: the Practice spawn
is 76 m from both spitters and their authored aggro radius maxes at 20 m.
That path remains untested, not failed.

## Open questions

1. ~~**Is health 0 enough for a visible death?**~~ **Answered: no.** See the
   live run above. The next trace is the damage-taken/death *event*, starting
   from `OnDeathEvent`'s consumers — not from a guessed message, and not from
   the four `ProjectileRequestComponent` confirmations, which `docs/21`
   established are shooter feedback only.
2. **Respawn only restores health.** The entity is never despawned or
   re-spawned, so no telegraph plays and the position is not reset. Full
   fidelity needs a despawn/spawn pair, which means the death message above.
3. **Damage shape between 40 and 60 units is inferred**, not decoded. The
   `Min + (Max−Min)·ratio` blend is the natural reading of the `DamageData`
   member names and `CalculateFalloff`'s output; the consuming arithmetic
   at `0xddae10` was not read. Endpoints (20 and 10) are proven.
4. **Distance is not fed in.** `immediate_damage(distance=None)` yields the
   near value. The reported hit carries no impact point; the player position
   now decoded from the input stream (`docs/25`) is where the client *was*,
   not where the shot originated. Near damage is applied for every hit.
5. **Critical hits are never classified.** `preciseCriticalHits = 1` and
   `CriticalMultiplier = 2` are read; which hit is critical is not.
6. **Health is only ever replicated for NPCs and hunter bots.** The player
   is registered so team filtering works, but nothing damages the player:
   hunter `Shot` and `CreatureAttack` events are logged, not applied,
   because neither attack is replicated to the client yet (`docs/21`,
   `docs/22`) and invisible damage is worse than none.
7. **Spitter respawns are death-driven here, absence-polled in retail**, so
   a real spitter comes back up to `PRACTICE_SPITTER_POLL_SECONDS` later than
   `Combat` restores it.

## Integration notes

Wired by the parent session in `server/shard_stub.py`; nothing here needs
a further change to use combat. Summary of what it does:

- `CRUCIBLE_SOLO_COMBAT=1` (default off). `start_solo_simulation`, called
  from `practice_npcs_reply` the moment the population is sent, builds one
  `combat.Combat` per connection: the player (`MARINE_SHARED_TYPES`, 800,
  team 1), the hunter bots when the profile has `bot_positions` (800, team
  2), and every `practice_population.population(level)` placement with the
  authored respawn/telegraph constants above.
- `observe_solo_combat` replaces `observe_practice_damage` whenever a
  `Combat` exists: same `(3, 01000002, 115, 4)` gate, same strict
  `parse_projectile_result`, then `apply_reported_hit`. Kills call
  `creature_ai.Creature.kill`; survivable hits call
  `CreatureDirector.note_damage` so a shot spitter aggroes on the shooter.
- `solo_simulation_messages` runs from `game_clock_reply` on every 20 Hz
  tick after the population is sent: hunter Location deltas first (counters
  3..5), then creature rotations, then `Combat.tick` → `build_messages`,
  all chained by `RelativeId`. When a `Combat` exists the old
  `CRUCIBLE_PRACTICE_DAMAGE` block is skipped.
- With the flag off the byte stream is unchanged; `test_solo_simulation.py`
  asserts that `start_solo_simulation` builds nothing and that the health
  encoding is byte-identical to the accepted nonlethal probe.

To test live: enable `CRUCIBLE_SOLO_COMBAT=1` in the shard drop-in
(alongside `CRUCIBLE_PRACTICE_NPCS=1`, Practice profile), restart the shard
with Crucible closed, launch, and shoot a target thirty times. Expected:
`SOLO COMBAT hit … KILLED` in the log, health 0 on the wire, health 600
again 6.0 s later. What the client *shows* at 0 HP is the thing to report.
