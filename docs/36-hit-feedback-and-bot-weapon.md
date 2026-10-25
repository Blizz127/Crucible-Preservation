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
| `CRUCIBLE_SOLO_BOT_RELOAD=1` | Marines fire from a 24-round magazine and reload |
| `CRUCIBLE_SOLO_FORCED_AGGRO=1` | A Marine the player shoots targets the player for 6 s |

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

What the shard sends for each accepted player hit:
- **scale:** the hit's damage over the weapon's close-range maximum. This
  is DERIVED; the retail server's formula is not traced. Reported hits carry
  no range, so it is 1.0 today.
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
hunter fire drops by nearly half. The seeded lethality table in docs/35
assumed continuous fire.

## Forced aggro on damage

`ForcedAggroDurationOnDamage` is 6.0 s (`marine_guide_dummy.ai` 0x1114):
"consider it as a possible target ... even if it is outside our aggro
range". With `CRUCIBLE_SOLO_FORCED_AGGRO=1`, a Marine hit by the player:
- takes the player as its target, even from behind or beyond 40 m;
- restarts its 0.25 s new-target delay;
- ignores the deaggro radius until the window ends.

Line of sight is still not modelled.

## Not verified

None of these has run in the retail client yet. The live checks are:
1. A hit marker appears on each hit, with Ajonah's hit sound.
2. The kill marker appears on the killing shot.
3. A Marine visibly reloads and stops firing for about 2 s every 24 rounds.
4. A Marine shot from behind turns and engages.
5. Death tags (28, 51) and (28, 86) do not break death presentation.
