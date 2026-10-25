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
| `CRUCIBLE_SOLO_FORCED_AGGRO=1` | A Marine the player shoots targets the player for 6 s |
| `CRUCIBLE_SOLO_BOT_STRAFE=1` | Marines strafe while attacking instead of standing still |
| `CRUCIBLE_SOLO_BOT_TURN=1` | Moving Marines turn at the movement turn speed, 1440°/s |
| `CRUCIBLE_SOLO_LOS=terrain` | Marines need sight over the terrain to acquire and attack; terrain stops their rounds |
| `CRUCIBLE_SOLO_DAMAGE_NUMBERS=1` | Damage numbers over Marines the player hits |
| `CRUCIBLE_SOLO_HIT_DIRECTION=1` | Incoming-hit arcs pointing at the Marine that hit the player |
| `CRUCIBLE_SHARD_TICK_MS=32` | Clock and simulation at the client's default 32 ms server tick, not 50 ms |

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

Line of sight is still not modelled.

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
is re-run at most every 0.2 s; that interval is not authored. With the test:
- an unseen target is not acquired;
- a held target is forgotten after 8 s unseen, unless forced aggro holds it;
- an attack does not start unseen and is abandoned after 3 s unseen;
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
(`snapshots/accuracy-20260924/line-of-sight-geometry.md`). Walls, rocks
and buildings are static meshes in `objects.pak` and are not modelled, so
a line clear of the terrain can still be blocked in retail. The shard
decodes the terrain once at startup, alongside the navmesh.

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

**Not handled: EnableDamageFloaters.** If the player has turned damage
floaters off in settings, the client sends `EnableDamageFloaters(false)`
(component 36, delivery 3) every tick while the replicated FloatersEnabled
stays true. It also stops walking its ring while it does so. The shard does
not answer yet, so with that setting off no numbers show and the RPC
repeats. The default setting is on.

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
