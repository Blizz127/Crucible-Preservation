# Hit feedback, authored death tags, and the Marine magazine

Status: **implemented and tested offline, all opt-in, not yet played.** Each
item has its own switch, so a live run can turn them on one at a time.
Retail build 5685572; the executable and pak copies used here match the
Alienware install by SHA-256.

| Switch | Effect |
|---|---|
| `CRUCIBLE_SOLO_HIT_CONFIRM=1` | Hit marker on the player's reticle for every accepted hit |
| `CRUCIBLE_SOLO_KILL_CONFIRM=1` | Kill marker when the player's hit kills |
| `CRUCIBLE_SOLO_DEATH_EVENTS=authored` | Each kill carries its killer's own on-hit tag |
| `CRUCIBLE_SOLO_HIT_RANGE=1` | The player's hits take the authored falloff from the real shooter-to-target range |
| `CRUCIBLE_SOLO_BOT_RELOAD=1` | Marines fire from a 24-round magazine and reload |
| `CRUCIBLE_SHARD_SPLIT_UPDATES=1` | **One message per entity per packet**, so the client applies them all (see below) |
| `CRUCIBLE_SOLO_FORCED_AGGRO=1` | A Marine the player shoots targets the player for 6 s |
| `CRUCIBLE_SOLO_BOT_STRAFE=1` | Marines strafe while attacking instead of standing still |
| `CRUCIBLE_SOLO_BOT_TURN=1` | Moving Marines turn at the movement turn speed, 1440°/s |
| `CRUCIBLE_SOLO_LOS=terrain` | Marines need sight over the terrain to acquire and attack; terrain stops their rounds |
| `CRUCIBLE_SOLO_LOS=static` | As `terrain`, and walls, rocks and buildings also block (needs the offline cache) |
| `CRUCIBLE_SOLO_BOT_IDLE=1` | Patrolling Marines rest one authored idle animation, plus 1 s, between hops |
| `CRUCIBLE_SOLO_BOT_ACCEL=ai` or `=config` | Marines ramp their speed: the AI's 10 m/s² (`ai`, also `1`), or the movement config's 0.15 s with a stop at once (`config`) |
| `CRUCIBLE_SOLO_BOT_CHASE_CAP=1` | A chasing Marine holds the Default config's 5.5 m/s, not the AI's unrealisable 8.0 |
| `CRUCIBLE_SOLO_BOT_ANCHORED=1` | Patrolling Marines pick destinations 5–15 m from their anchor, as the editor tooltips say |
| `CRUCIBLE_SOLO_HIT_REACTION=1` | A Marine the player hits flinches away from the shot (needs the split); `=all` also flinches the player when a Marine's round lands |
| `CRUCIBLE_SOLO_DAMAGE_NUMBERS=1` | Damage numbers over Marines the player hits |
| `CRUCIBLE_SOLO_HIT_DIRECTION=1` | Incoming-hit arcs pointing at the Marine that hit the player |
| `CRUCIBLE_SHARD_TICK_MS=32` | Clock and simulation at the client's default 32 ms server tick, not 50 ms |

## The client applied only one message per entity per packet

This is the most consequential finding of the pass. The client logs
"Received old PropertyChangeMessage message for entity id %u, sequence %d
latest sequence %d". `EntityReplicationManager::ValidateUpdate`
(`0x4bb4f80`) drops a property message unless its sequence is greater than
the last one applied to that entity:
- the latest per entity is at subscriber `+0x10`, and -1 means none yet;
- the unsigned compare is at `0x4bb5144`.

The sequence comes from the received request
(`ClientNetworkAgent::HandleRequest`, `0xe24300` → `0xe251d2`). It is the
same for every message of the packet, and it is the shard's wire
LocalSequence. At 18:22:01.047 GMT on 5 October the client logged
"Receiving local sequence id 2369", then refused "sequence 2369". The
shard's own log decodes to the matching sends. The values rise about 80
a second: 20 clock packets plus about 60 input ACKs.

So in each tick only the **first** message for an entity applied. The shard
sent each Marine's Location first, then its animation, fire activation and
health. The client refused the rest **25,682 times** in that one
five-minute run, about 8,500 per Marine. This is why a Marine's fire,
death animation, idle/move flag and health were never seen live: they were
never applied.

With `CRUCIBLE_SHARD_SPLIT_UPDATES=1`, a tick with several messages for one
entity becomes several EntityUpdates packets:
- the first message for each entity goes in the first packet, the second in
  the second packet, and so on, so each entity's messages keep their order;
- each packet takes the next LocalSequence;
- RelativeIds restart at zero per packet, as the reader requires
  (`0x4b9dfd0`).

The rings below also need this: their message and the player's own health
are both for the player.

The switch is opt-in because it changes what the client applies. Bot
animation, fire and health reach it for the first time.

One risk is open: whether the client unwraps that u16 before comparing.
The compare itself is a plain unsigned `ja`. The shard's LocalSequence
wraps after 65,536 packets:
- about 13.6 minutes at 80 a second;
- about 8 minutes with the split, at about 140 a second.

If the client does not unwrap it, every update after the wrap counts as
old, MatchPhase GameOver included, and the match could then end only by
leaving it. So each connection ends its match with a no-winner GameOver
once its LocalSequence reaches 64,000 (`CRUCIBLE_SHARD_SEQUENCE_LIMIT`;
0 disables it). That leaves 10–20 s of repeated GameOver before any wrap.
Remove the valve once a long run shows the client unwrapping.
`match_acceptance.py` counts the client's refusals (row S1) and fails on
any that come after a wrap.

## Hit and kill markers

The shard has never sent anything that sets the hit marker. Whether the
client shows one on its own is not confirmed live. The HUD's reticle
(`ui/dist/reticle-container.js`) subscribes to the native endpoint
`combat.outgoing.updates`, which reports `{scale, isCrit, killCounter,
directDamage, indirectDamage}`. It draws the hit marker at
`max(scale, 0.2) * 35%`, or 45% for a crit. It shows the kill marker for one
second whenever `killCounter` rises.

Those fields are ProjectileRequestComponent (115) state, and two
server-to-client RPCs set them. Dispatch `0x9dd650` checks for delivery 2
(Authority → Autonomous), then calls the interface at `[component+0x1298]`
(vtable `0x674c5c8`):

| RPC | slot | handler | effect |
|---|---|---|---|
| 0 ConfirmProjectileHitEntity | +0x208 | `0xbc4e60` | hit marker (damage type 1), then impact effects |
| 2 ConfirmHitIndicator | +0x218 | `0xbc53d0` | forwards its five fields to `0xbc4390` |
| 3 ConfirmKillIndicator | +0x220 | `0xbc5400` | LastKilledPlayer (+0x19c0) = id; PlayerKillCounter (+0x19b4) += 1 |

`0xbc4390` sets HitConfirmScaleValueToShow (+0x19a8), IsCrit (+0x19ac) and
DirectDamage (+0x19ad), and increments HitIndicatorCounter (+0x19b0). For
damage type 1 it also loads the hit-confirm sounds of
`AllowedAssets[projectileTypeIndex]` from the shooter's projectile request.
An index outside 0..15 skips only the sound. The damage types are from
`NovaNet::EHitConfirmationDamageTypes` (registration `0x4ae3891`):
0 None, 1 ProjectileTypeIndexed, 2 Explosion, 3 QuickMelee.

Payloads follow the shooter-feedback track's serializer reads. The RPC
reader starts with an empty bool cursor and shares one byte, low bit first.

```
ConfirmHitIndicator   f32 BE scale | u8 bools (bit0 isCrit, bit1 directDamage)
                      | u8 damageType | u32 BE projectileTypeIndex - INT32_MIN
ConfirmKillIndicator  i8 killedEntity.m_Id
```

Server packet 11 (EntityRpcs) carries them. It uses the same vector body the
client sends (`0xe26490` → `0x4ba0d10` → `0x4ba16f0`), so
`shard_stub.build_entity_rpcs` is the inverse of `parse_entity_rpcs`. Queued
RPCs go out in one packet after each clock tick.

**These RPCs cannot drop the connection.** `NovaNet::EntityReplicator::ValidateRpcMessage`
(`0x4bfcee0`) returns 0 (accept) for delivery 2 only when the entity's local
role ([+0xf8]) is 2 and its remote role ([+0xfc]) is 4. Any other pairing
with a client-side local role falls through to "Dropping RPC and Connection"
(`0x4bfd0c0`, result 2). The client's
`EntityReplicationManager::HandlePropertyChangeMessage` creates every entity
the server spawns with remote role 4: it passes `r8d = 4` to `0x4bb3c30` at
`0x4bb496f`, and `0x4bb3d7d` stores that as [+0xfc]. The player's
character is spawned with local role 2, and since 3a09282 it is kept there.
So the pair is (2, 4) and the RPC is accepted. If a future change demoted
the player's role, these RPCs would drop the connection. Check the client
log for `Dropping RPC` in the first live run.

What the shard sends for each accepted player hit:
- **scale:** the hit's damage over the weapon's close-range maximum. This
  is DERIVED; the retail server's formula is not traced. Reported hits
  carry no range. With `CRUCIBLE_SOLO_HIT_RANGE=1`, the range is the
  distance from the player's decoded input position to the target's server
  position, and the falloff applies. Ajonah's damage falls from 40 at 60 m
  to 20 at 119 m (26.5 at 100 m), so the marker shrinks to match.
  Without the switch every hit is full damage and scale 1.0.
- **damageType:** 1.
- **directDamage:** true.
- **projectileTypeIndex:** the hero's primary slot. Ajonah's
  `saboteur_primary` is 6 (7 is the aimed shot); the Marine's is 4.
- **ConfirmKillIndicator:** sends `m_Id` −1, the handler's own default. What
  the id indexes is not traced.

## Authored death tags

The previous death probe tagged every kill "Unknown / marine_primary". The
on-hit scripts tag their own damage:

- `marine_primary_onhit.cdl`: SourceName "Ability", AbilityName
  "marine_primary".
- `saboteur_primary_on_hit.cdl` (sha256 `188ae361...`): "Ability" and
  "saboteur_primary".

The wire ids come from the executable's static names followed by the pak
tables. `tools/inspect_fire_bakednames.py` used to drop names of up to seven
characters. Those use AZ ObjectStream's inline-size flag (`0x5f` for
"Ability"), and the tool split only on the one-byte-size form (`0x79`). With
the fix:

| table | name | wire id |
|---|---|---|
| HealthChangeSource | Ability | 28 (24 static + 4) |
| AbilityNames | marine_primary | 51 (unchanged) |
| AbilityNames | saboteur_primary | 86 (the old parser gave 85) |

With `CRUCIBLE_SOLO_DEATH_EVENTS=authored`, a Marine's kill is tagged
(28, 51) and Ajonah's kill (28, 86). A hero whose asset has not been read
kills without metadata. `=1` with explicit ids behaves as before.

## The Marine magazine

`marine_resources.resourcesdata` gives Ammunition 0..24. The rifle loop
spends one round 96 ms after each projectile. `marine_reload.cdl` then starts
by itself at zero and runs this timeline:
- refill at 1.216 s;
- `using_reload` ends at 1.6 s;
- `Reload_Active` clears at 1.984 s, after which the held trigger fires again.

With `CRUCIBLE_SOLO_BOT_RELOAD=1`:
- A Marine fires 24 rounds (2.3 s), then pauses about 2.1 s.
- `using_primary1` drops for the reload.
- With `CRUCIBLE_SOLO_COMBAT_ANIMATION=1`, `using_reload` (record 31) plays
  the reload.

A full cycle is 24 rounds in about 4.3 s instead of 2.3 s, so sustained
hunter fire drops by nearly half. Lethality drops much less, because each
reload pause resets bloom: the next magazine opens with the tight 1.5° cone.
The table shows mean seconds to drain 800 health over 20 seeds, with
server-resolved hits at accuracy 0.25, a stationary target, and Marines
2 m apart facing it:

| | 3 at 8 m | 3 at 15 m | 3 at 25 m | 1 at 15 m |
|---|---|---|---|---|
| continuous fire (docs/35 measured 7/25/72/80) | 8 | 26 | 72 | 79 |
| with the magazine | 12 | 27 | 78 | 84 |

So the 0.25 accuracy knob does not need re-tuning for the magazine.

## Forced aggro on damage

`ForcedAggroDurationOnDamage` is 6.0 s (`marine_guide_dummy.ai` 0x1114):
"consider it as a possible target ... even if it is outside our aggro
range". With `CRUCIBLE_SOLO_FORCED_AGGRO=1`, a Marine hit by the player:
- takes the player as its target, even from behind or beyond 40 m;
- restarts its 0.25 s new-target delay;
- ignores the deaggro radius until the window ends.

The window also holds the target unseen: with `CRUCIBLE_SOLO_LOS=terrain`
(below) the 8 s forget does not apply inside it, but an attacker the
Marine has never seen is not fired on.

## Strafing

Until now a Marine in range stopped dead and stood while firing. The retail
editor tooltips for `marine_guide_dummy.ai` StrafeParams
(`bot-authored-params.md`) describe the behaviour:
- **Influence by distance:** 0% at 1 m, 100% from 2 m to 20 m, and back to
  0% at 30 m, which is the attack RangeMax.
- **Speed:** 4.5 m/s, the movement config's lateral cap.
- **Direction:** "Strafe for at least this long before changing
  direction", for U(1, 2) s.
- **Movement during the attack:** never locked
  (`TranslationDisallowedPeriod` is 0/0).

With `CRUCIBLE_SOLO_BOT_STRAFE=1`, an attacking Marine each tick:
- steps sideways at `4.5 × influence`;
- closes in at `5.5 × (1 − influence)`, stopping at the 1.8 m approach goal
  radius (`StopDesiredDistance`);
- keeps turning to face its target.

The inward speed is the Marine's non-sprint forward cap, not the chase speed.
How retail blends the two components is not traced; a weighted sum is the
plain reading of "influence". A sidestep that would leave the navmesh
reverses direction (`StopWhenBlocked` False). A sidestep must also land on
the bot's own triangle or a linked one, within 0.5 m of its height, so it
cannot climb or drop a ledge (`STRAFE_STEP_HEIGHT`, not authored). Strafing
moves along the tangent, so a Marine drifts outward by about 0.1 m per
second of circling.

On the installed April2018 navmesh, three strafing Marines faced a
stationary player 15–20 m away, over three seeds of 30 s each:
- They held 14–20 m and moved 129–139 m each, about 4.5 m/s.
- No sample left the mesh.
- The largest height change in one tick was 0.07–0.16 m.
- They fired 485–504 rounds with the magazine on.

## Line of sight over the terrain

`marine_guide_dummy.ai` checks line of sight (`AggroCheckLineOfSight`,
0x107b). It forgets an unseen target after 8 s
(`TimeWithoutLoSToForgetCurrentTarget`, 0x12da) and abandons an attack after
3 s unseen (`TimeWithoutLoSToTargetUntilAbandoningAttack`, 0x1420). Until
now nothing could block sight, so Marines saw and shot through hills.

`server/level_geometry.py` decodes the level's compiled terrain
(`terrain/terrain.dat`, CryEngine version 0x1d): 4,096 leaves of 32 m, each
33 × 33 samples. It matches the shipped PhysX collision heightfield
(`levels.pak` `terrain.pxheightfield`) within 0.06 m over all 4.46 million
samples. Decoding April2018 takes 0.26 s, and one 39 m sight test about
170 µs. HotH_02 and the tutorials decode the same way; Practice's grid
(unit 2) is refused, so it gets no sight test.

With `CRUCIBLE_SOLO_LOS=terrain`, each Marine gets a sight test from its eye
(1 m above its feet) to the target's aim point (1 m above theirs). The test
is re-run at most every 0.2 s, and only for a target that is held or within
acquisition range; that interval is not authored. With the test:
- an unseen target is not acquired;
- a held target is forgotten after 8 s unseen, unless forced aggro holds it;
- a held target out of sight for under 3 s still draws back-to-back
  attacks; after 3 s the attack is abandoned and none restarts.
  `TimeWithoutLoSToTargetUntilAbandoningAttack` (3 s) is longer than one
  attack (2 s), so the window must span attacks, or it could never act.
  Only a sight test opens the window. Damage forces aggro and holds the
  target, but an attacker the Marine has never seen is not fired on;
- a server-resolved round whose line is blocked does no damage.

An end point whose eye or aim point is under the heightfield skips the
terrain test (`level_geometry.sight_clear`, `UNDERGROUND = 0`). On
April2018's main walkable component, 3.7% of navmesh spots put the eye
below the terrain:
- 1.94% are more than 0.5 m below;
- 1.75% are within 0.5 m below.

These are caves, tunnels and building interiors whose surfaces are static
meshes. The literal "stays above the terrain" test would hide a player
there from every Marine and block every round, so such a spot would win
the match. With the carve-out, Marines can see through ground there
instead, which is the milder error.

Retail ray-cast against all PhysX colliders in the AwarenessLoS group
(`snapshots/accuracy-20260924/line-of-sight-geometry.md`). The terrain
mode does not model walls, rocks and buildings, so a line clear of the
terrain can still be blocked in retail. The static mode, next, adds them.
The shard decodes the terrain once at startup, alongside the navmesh.

## Line of sight through walls, rocks and buildings

`CRUCIBLE_SOLO_LOS=static` tests the terrain as above, then the level's
static PhysX colliders. The same test also decides server-resolved rounds.

**Where the geometry comes from (April2018, read 25 October).** The level
slice holds 8,529 mesh, 599 box, 65 sphere and 1 capsule collider
components. Each has a CollisionLayer, a Trigger flag, a local
Position/Rotation and its entity's world transform.
- **What blocks.** AwarenessLoS (mask `…E1`) skips layers 1–4, and triggers
  never block. That leaves 8,236 blocking colliders: 8,187 meshes, 46
  spheres, 2 boxes and 1 capsule, almost all on WorldStatic (layer 8).
- **Assets.** The meshes use 600 assets, each named by a hint path in its
  PhysicsAsset field: 450 are in `objects.pak`, 147 in `architecture.pak`,
  2 in `animations.pak` and 1, `vfx/props/general/goo.pxmesh`, in
  `vfx.pak`.
- **No collider is on layer 2.** That is the only layer where AwarenessLoS
  and the Projectiles group (`…E5`) differ, so rounds share the sight test.

**The cooked meshes** are PhysX 3.4 `NXS\x01MESH` version 15, little-endian:
- after the magic: version, midphase id, serial flags, then the vertex and
  triangle counts;
- then the float vertices;
- then the indices: bytes for flag 4, u16 for flag 8, otherwise u32.

All 599 decode with every index in range and every float finite. Each
stores its bounds as six floats later in the blob, matching its vertices
exactly.

**Placement.** The transform's three float triples are the entity's
scaled axes. That is measured: their lengths equal the shape's Scale for
every collider, 2,365 of them non-uniform. The composition order is
inferred from the PhysX gem, not traced. It scales the geometry, then
applies the collider's local pose, then the entity's pose:
world = R (Q (S A v) + S p) + T.
Only 70 colliders with an offset p, 37 with a rotation Q and 22 with an
asset scale A depend on that order. No April2018 transform is mirrored,
and none is singular. A mirrored one would still work: the query runs in
each mesh's own space, so it needs no winding flip.

**Two checks hold the decode and the placement together:**
- **Rows against columns.** For random points on navmesh triangles more
  than 1 m above the terrain, take the offset to the nearest placed
  surface. With the triples read as columns, the offsets peak in the
  0.00–0.05 m bin, and 38% fall within −0.25..+0.5 m. That matches the
  terrain's own peak at +0.05/+0.10 m, where 50% fall in the band. Read as
  rows, the peak smears to −0.15 m and 30%.
- **Left unexplained.** 1,574 of the 11,468 navmesh centres more than 1 m
  above the terrain have no placed collider under them. The 342
  InvisibleGeo (layer 1) meshes, which can carry walkable surfaces, were
  never fetched for this check.
- **The greenhouse.** At guide node 44 the navmesh lies 0.34 m above
  `research_greenhouse_001`'s top face.

**Back faces do not block.** PhysX raycasts skip them unless asked
(`eMESH_BOTH_SIDES`), so a line leaving a rock is clear. All 320 closed,
consistently wound meshes have positive signed volume: their faces point
outward.

**Cost.** `tools/build_static_geometry.py` reads the slice and the meshes,
taking about 20 s for the slice parse alone. It writes a 3.0 MB cache to
`~/.cache/crucible-revival/` (mode 600). The shard only loads that cache:
0.1 s to parse and about 3 s to build one bounding-volume hierarchy per
mesh, at prewarm; a process holding the loaded world peaks at 97 MB. The
instances sit in an 8 m XY grid. The build itself peaks at 830 MB (the
slice parse), so it runs as a separate tool, never in the shard.
- **Query speed.** 1,174 dense 5–40 m segments around the Alpha Hunters
  area took 0.14 ms median and 0.58 ms p99, in pure Python.
- **Without a cache.** The static mode logs one line and falls back to
  the terrain.

**An example.** Of the drop cluster's six eye-to-eye lines (eyes 1 m up,
`AggroOriginOffsetLocal`), the two to 17b are blocked. The blocker is
`rock_coquina_ledge_01_top`, a ledge that rises to 24.0 m between them.

**Not modelled, and inferred:**
- That bots use the AwarenessLoS group is inferred from its name.
- Colliders spawned at runtime by dynamic slices are not in the level slice.
- HotH_02's own colliders are not built yet.

## Damage numbers and incoming-hit arcs

Both are autonomous ring buffers on the player's character (components 36
and 81; layouts in docs/27 §9). The client consumers are
`DamageFloaterAutonomousSystem` (`0xdab820`) and
`HitIndicatorAutonomousSystem` (`0xdade60`). Neither predicts anything
locally, so without the server nothing is drawn. Each keeps a read cursor
that starts at 0. Every tick it walks slots `cursor+1 .. LastServerIndex`,
wrapping at the ring size (32 and 16), and stops only on equality. So the
server:
- writes each new entry into slot `(LastServerIndex + 1) % size`, then
  publishes that slot as LastServerIndex; the first entry is slot 1;
- **never publishes a LastServerIndex of the ring size or more**. Cursors
  are masked with `& 0x1f` (`0xdabb56`) and `& 0xf` (`0xdae01d`), so
  equality would never come and the client would loop forever.

Damage numbers (36) work as follows:
- Entries are keyed by target, and a new entry *replaces* the shown amount.
- A target's number lives for 224 ms after its last entry
  (`common_damagefloaters.floatersettings` DirectAccumulationWindow) and
  re-shows at most every 156 ms.
- So the shard sends a running total per target that resets after 224 ms
  without a hit.
- `m_TargetEntity` is the victim's network id. The client places the number
  over that entity itself.
- `m_Flags` bit 0 is crit and bit 1 is damage over time.

Hit arcs (81) work as follows:
- `m_IndicatorPosition` and `m_ScreenEdgePosition` are world positions; the
  shard sends the shooting Marine's.
- The HUD arc's bearing is `atan2` of that point against the camera, and its
  size is `damage / (maxHealth + maxShield)`.
- HitIndicatorType 2 is Projectile and HealthChangeType 1 is Direct (enum
  tables `0x65c4b70` and `0x65c4340`). Type 8 or higher would index past the
  name table.
- The client recomputes the scale from damage, so the shard sends 1.0.

Record layout:
- The 25 autonomous roots are, in type order: 6, 10, 20, 21, 24, 25, 27, 36,
  46, 48, 50, 61, 70, 81, 82, 84, 101, 110, 115, 117, 120, 145, 156, 158,
  164. These are the character's components (type getters in
  `live-character-spawn.json`) that have server-to-autonomous bits in the
  schema.
- Component 36's subtree is root, array, 32 entries, FloatersEnabled and
  index: 36 bits. Component 81's is root, index, array and 16 entries:
  19 bits. Both match the schema's expanded counts.
- Values go in component order. 36 sends each entry (u32, f32, u8), then its
  u8 index. 81 sends its u32 index, then each 36-byte entry.
- Both rings share one player message per tick, at the head of the tick, so
  the player never gets two messages in a row.
- The encoders reproduce, byte for byte, three worked examples derived
  independently from the serializers: a first floater, a first arc, and
  both in one message.

**Lost updates.** A client that misses an update would later walk past a
slot it never received, and replay that slot's old contents. Each ring
therefore re-sends its newest entries, up to 8, for 0.5 s after they are
written (`RESEND_SECONDS`, `RESEND_ENTRIES`; not authored). Re-sending a
slot the client has already consumed is harmless, because it reads only
slots between its cursor and LastServerIndex.

**EnableDamageFloaters.** The client sends this every tick while the
player's floater setting differs from the replicated FloatersEnabled. The
default for both is on. The request travels on component 36 as RPC 0,
delivery 3, with one packed bool; the send site is `0xdab8a4` and the
struct `0x86d920`. With `CRUCIBLE_SOLO_DAMAGE_NUMBERS=1` (and the split,
which every ring update needs), the shard:
- records the player's choice;
- writes no damage numbers while floaters are off, because the client stops
  walking its ring then and would replay them later;
- echoes the value as FloatersEnabled (record 34) for 10 ticks, so the
  client stops asking.

Record 34 is a packed bool. It comes after any entries and before the
index, and it can be sent on its own, with the array and index records
skipped. The shard always sends it on its own: while the echo runs, new
damage numbers wait in the ring. That way the shard never relies on the
entry flags (`0x870d20`) being a whole byte rather than packed bools, an
assumption not yet checked live.

One risk is recorded but not resolved. If the client's screen-edge effects
asset failed to load, `PlayEdgeOfScreenEffect` (`0xdacd30`) logs "Edge
effects asset is null" and then dereferences it anyway. The first incoming
arc would then crash the client. Check the client log on the first run.

## The client's default server tick is 32 ms

The shard's 50 ms clock was an invented stall fix (docs/16; accuracy audit
item 10). The executable answers the question directly. The AZ_CVAR
descriptor at `0x74455c0` is `net_ServerRateMs`, "Server tick rate to use
for network relevent simulations". Its storage at `0x7bd4730` is a uint16,
and the static initializer at `0x4c9c80` sets it to `0x20`, which is 32 ms.
The client reads it in its input timing (`0xc4b25f`), the hybrid projectile
system (`0xbbc285`), `ClientNetworkAgent`'s getter (`0xe22ff0`) and NovaNet
time sync (`0x4b9d052`). The client therefore expects a server tick of
32 ms, about 31 Hz. The server binary never shipped, so a server-side
configuration may have overridden this. Retail servers probably did run at
32 ms, but that is not proven. `net_ProjectileTickRate` defaults to 0
(storage `0x7bd45b0`, cleared at `0x4c85a0`).

`CRUCIBLE_SHARD_TICK_MS=32` runs the clock, and with it bot simulation and
replication, at 32 ms. The socket wakes twice per tick. Without the switch,
both the 50 ms gate and the 50 ms wake are unchanged.

## Marines flinch when hit

Component 80, HitReactionComponent, carries `NetworkedAdditiveHitIndex`
(u8) and `NetworkedAdditiveHitYaw` (f32) to every client (docs/27). The
hit-reactions track traced the client's consumer, `HitReactionClientSystem`
(worker `0xdae770`). Its notes, now in `snapshots/hit-reactions-20260923/`,
mark these PROVEN:
- **The trigger.** The client flinches when the networked index rises
  above the last one it played (an unsigned compare), so the index is a
  hit counter. A wrap from 255 to 0 never triggers.
- **The cooldown.** During `flinchDelay` (0.2 s for the Marine,
  `marine.animconfig`) a new index advances the counter but does not
  replay.
- **The yaw.** It is the signed angle, in degrees, of the shot's travel
  direction in the victim's frame: acos(d · f) × sign(d · (f × Z)), with f
  the victim's forward. `additive_hit_react.animgraph` turns 0 into
  `hit_flinch_f`, +90 into `_r`, −90 into `_l`, and ±180 into `_b`.
- **Prediction.** The client also predicts a flinch for its own projectile
  hits.

With `CRUCIBLE_SOLO_HIT_REACTION=1`, each hit the shard accepts from the
player on a hunter adds one to that hunter's index, capped at 255 and never
reset. The yaw goes in the frame of the facing the hunter's last Location
update carried, so the flinch matches what the client shows. The update
repeats for 3 ticks; a repeat cannot replay a flinch. Counting only accepted
reports keeps the count at or below the client's own, so a predicted flinch
never plays twice. Where prediction is off, this count drives every flinch.
The update is another message for the hunter in the tick, so it needs
`CRUCIBLE_SHARD_SPLIT_UPDATES=1`. Without the split it logs one line and
sends nothing.
- **The start line.** With the switch on, each match start logs
  `HIT REACTION on`. If player hits on hunters are accepted but no flinch
  goes out, `match_acceptance` F4 fails rather than going missing.
- **A fresh count per match.** Each match runs on a fresh connection
  state: a new peer, or one idle for 10 s (`CRUCIBLE_SHARD_PEER_IDLE`).
  So the count starts at 0 for that match's newly spawned hunters, as the
  client's own component does.

**The player flinches too, with `=all`.** A hunter round that lands on the
player raises the player's own index the same way.
- **The yaw** is taken in the frame of the player's newest input rotation,
  which the client sends in every input frame (docs/25). If no facing has
  been decoded yet, the round is skipped.
- **The framing.** The player's own character is ClientAutonomous, so its
  update is framed like its Health update: role 2, then the shared record,
  then an all-false autonomous record. A role-1 update to that character
  crashed the retail client (`combat.autonomous_record`).
- **The shared list.** Every hero shares the list that carries component 80.
- **What drives it.** The client reports no hunter hits, so this count
  drives the player's flinch, unless the client also predicts flinches from
  hunter projectiles. That isn't traced; if it does, its count runs ahead
  and some networked flinches are swallowed (fewer, never doubled).
- **Why a separate value.** Its framing has not been seen live, so `=1`
  keeps the Marines' flinch alone.

Not established: the retail server's increment policy. No server symbols
are in the client, so "one per accepted hit" is inferred.

## Resting between patrol hops

`marine_guide_dummy.ai` authors `IdleParams`:
`IdleAnimationDurations` 10.8, 5.8, 4.67, 11.73, 9.67 and 2.67 s
(0x1c6..0x248), and `ExtraIdleTimeAtEndOfAnim` 1.0 s (0x263). With
`CRUCIBLE_SOLO_BOT_IDLE=1`, a wandering Marine that finishes a patrol hop
stands for one of those durations, chosen uniformly, plus 1 s, and then
walks its next 5–15 m hop. A target ends the rest at once.

The reading is inferred from the names: a rest between hops, one idle
animation long, plus the extra time. The exe only shows these fields'
reflection registration (`0x5727a00..0x5727e40`); the code that uses them
is not traced. Nor is how retail picks the animation. The client chooses
which idle animation to play; the server only holds the bot still.

At the Marine's 5.5 m/s patrol speed, a 10 m hop takes about 2 s, and a
rest 3.7–12.7 s. So with the switch on, a patrolling Marine stands far more
than it walks, as the authored values imply. Without it, Marines never stop.
The DIT patrol pauses, when approved, replace this rest.

## Patrol destinations around the anchor

`PatrolParams` has two distance pairs, and its editor tooltips
(`bot-authored-params.md`) say which is which:
- `MinDistance`/`MaxDistance`, 5–15 m (0x2c2/0x2dc), are "from anchor pos";
- `MinDistanceFromCurrent`/`MaxDistanceFromCurrent`, 0–50 m (0x2f6/0x310),
  are "from current pos".

The wander so far read them the other way round: hops 5–15 m long from the
current position, anywhere within 50 m of the anchor. So retail Marines
patrol a ring 5–15 m around their anchor, while ours roam up to 50 m from
it.

With `CRUCIBLE_SOLO_BOT_ANCHORED=1`, a patrolling Marine draws each
destination from the 5–15 m ring around its anchor, within 50 m of where it
stands, in up to five attempts (`MaxAttemptsForFindingPatrolDestination`).
It walks there along the navmesh from where it is. On open ground it never
strays more than 15 m from the anchor. After a chase, a returning Marine
walks to its anchor, the ring's centre. The earlier reading stays the
default, so the deployed baseline is unchanged.

`TargetPosGenerationAttemptsUntilIgnoringDistanceToCurrentRule` is 50. A
Marine more than 65 m from its anchor finds no ring point within 50 m of
itself. After 50 failed attempts (ten searches), it drops the from-current
limit and walks back to its ring.

Each match start logs `HUNTER AI switches: …; sight …` when any bot
switch or sight mode is on. `match_acceptance` shows it as row B0, so one
row confirms which drop-ins took effect.

## Speeding up and slowing down

Two parameters could set the ramp, and which one a retail Marine obeys is
not traced:
- **The AI's.** `MovementPararams/StartStopAcceleration`, 10 m/s² (0x7f6):
  "used to accelerate up to and decelerate down from the desired speed".
- **The player movement config's.** The Marine's guide AI is a
  `SpoofedPlayerInputs` bot, driven through the player movement pipeline
  (snapshots/speed-20261025). `marine_default` gives `Acceleration` 0.15 s
  "from Min Speed to Max Speed", and `Brake Friction` 100. `0x33f2900` has
  the shape of friction damping: speed × (1 − min(a × b, c)). That its
  coefficient is Brake Friction 100, which would stop the character at once,
  is inferred.

The client reads only the direction of a spoofed move, and asserts its
magnitude is at most 1. If its speed also ignores that magnitude, the AI
cannot shape it, and the config's ramp is what shows. The multiply site is
not found.

`CRUCIBLE_SOLO_BOT_ACCEL` takes the model:
- **`ai`** (or `1`): speed moves toward the desired one by at most
  10 m/s² × the tick. The bot brakes so that it stops where its plan ends:
  each tick it takes the fastest speed that, after the tick's step, can still
  stop by then. A Marine reaches 5.5 m/s in 0.55 s, and an attacking one
  slides to a stop facing its target.
- **`config`**: 0 to the desired speed in 0.15 s, and a stop at once.

A live check with a gamepad decides it one way only. About half speed at
half tilt shows that magnitude scales speed, which favours `ai`. Full speed
fits `config`, but so does an input layer that snaps the stick to unit
length. Not modelled:
`CruiseAcceleration` (10), `MaxSpeedMultiplier` 1.1 (flocking catch-up),
`PathAcceleration` 20 (steering), and strafing's own 10 m/s².

## The chase speed

The guide AI asks for 8.0 m/s when chasing (`SpeedChase`), but the player
pipeline offers no sustained 8.0. The Marine's configs hold 5.5 m/s forward
(`marine_default`) or 8.5 (`marine_sprint`). Sprint is the Ability3 ability,
held: `marine_sprint.cdl` sets `ActiveConfig = Sprint` while the button is
down and drains a Sprint resource. The input's magnitude is asserted to be
at most 1, and `Overspeed Deceleration Rate` (12 m/s per second) pulls any
excess back down. So 8.0 is out under either reading of the ramp.

With `CRUCIBLE_SOLO_BOT_CHASE_CAP=1`, a chasing Marine moves at 5.5 m/s, the
Default config's forward speed. The guide scripts press Ability3 themselves:
- `marine_guide_on_hit.cdl`: on being hit, Flee and Ability3 for 4 s;
- `marine_guide_attack_selector.cdl`: Ability3 and MoveBackward for 3 s.

Those 8.5 m/s sprint bursts are not modelled: neither the scripts' gating
nor the Sprint resource's capacity is decoded. With the cap, Ajonah's
Default config (6.5 m/s forward) outruns a Marine that isn't sprinting.

## Turning while moving

Bots used PassiveTurnParams' 90°/s turn-in-place rate for every turn
(accuracy audit item 4). A running Marine therefore kept its old facing
round corners and slid sideways. A moving bot steers through its spoofed
player inputs, so its body turns at `marine_default.movementconfig`'s Max
Turn Speed of 1440°/s (0x143). With `CRUCIBLE_SOLO_BOT_TURN=1`, the
1440°/s rate applies while a bot moves, and turn-in-place at 90°/s only
while it stands.

Facing gates the far aggro cone and the attack cone, so this switch also
changes when Marines notice the player and start firing.

## A simulation error no longer ends the shard

Before this pass, an exception in the tick or in a reported hit escaped the
shard's loop. systemd then restarted the process mid-match, which leaves
the client in the rejoin trap (docs/30). New code paths add new ways to
raise, so both paths now run under `guarded`:
- The traceback is logged at counts 1, 2, 4, and so on, so
  `match_acceptance.py` still fails A7.
- The connection survives; a failing tick sends only its clock.
- After 2 s of consecutive failing ticks the match ends with a no-winner
  result (`SIMULATION_FAILURE_GAMEOVER_SECONDS`). The next tick takes the
  GameOver branch, which runs before any other simulation code. GameOver
  clears the client's rejoin data.

A broken feature therefore ends the match cleanly instead of freezing it.

The valve counts the **share** of failing ticks, not a consecutive run:
- it ends the match once at least 20% of the ticks in the last 4 s failed,
  judged over at least 2 s;
- a bug that fails only every other tick still ends the match;
- one tick in ten does not.

**A pre-existing shard crash, found by review.** `dit_motion.py` trimmed
its bout history with `del bouts[:-MAX_BOUTS]` on a `deque`. A deque cannot
be sliced, so this raised TypeError the first time any tracked actor
switched between moving and standing for 0.5 s. The managed profile sets
`CRUCIBLE_DIT_TRANSPORT_ROOT`, so the path is live:
- In the receive path, a walking player killed the shard: a mid-match
  restart and the rejoin trap.
- In the tick path, a patrolling hunter failed 25–59% of ticks.

On 5 October it never fired: DIT recorded zero moving samples for every
actor over 4.5 minutes, so no transition happened. The deques already have
`maxlen=MAX_BOUTS`, so the line is gone. Both DIT observation calls now run
under `guarded`, because an observation feed must never cost a tick.

With the live profile, five scripted sessions are byte-identical between
ffae2f1 and this commit. With DIT on and off they are also identical:
wins, losses, deaggro, teleport and flank.

## Not verified

None of these has run in the retail client yet. The live checks are:
1. A hit marker appears on each hit, with Ajonah's hit sound.
2. The kill marker appears on the killing shot.
3. A Marine visibly reloads and stops firing for about 2 s every 24 rounds.
4. A Marine shot from behind turns and engages.
5. Death tags (28, 51) and (28, 86) do not break death presentation.
6. Strafing Marines play a sideways run, not a forward run sliding
   sideways. The client derives blend velocity from Location motion
   (docs/28).
7. Damage numbers rise over a Marine as it is hit, and the client does not
   hang.
8. An incoming-hit arc points at the Marine that hit, and the client does
   not crash on the first arc.
9. With terrain sight on, a Marine behind a hill neither acquires nor hits
   the player.
10. With static sight on, the same holds behind a rock or a building. The
    rock ledge between the April2018 drop and drop point 17b is a ready
    example.
11. Toggling Settings > Gameplay > Visuals > "Enable Damage Floaters" logs
    one "client asks for damage floaters" line per change, and nothing
    fails to deserialize.
12. With idle rests on, a patrolling Marine stands and plays an idle
    animation between hops, rather than sliding in a still pose. With the
    chase cap, a chasing Marine runs at the player's own walking speed.
13. A Marine the player shoots flinches away from the shot. First, with the
    switch off: whether it already flinches shows whether the client's own
    prediction runs.
