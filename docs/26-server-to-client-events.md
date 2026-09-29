# The server→client message surface

Scope: enumerate everything a shard can *say* to the client, so the combat,
AI and hero workstreams stop guessing. `docs/20` proved replicating Health 0
produces no visible damage and no death; `docs/21` proved a constant position
still plays a run cycle. Both point at a layer we are not speaking. This
document maps that layer.

Tool: `tools/inspect_events_rpcs.py` (read-only; nothing under the Steam
install is written and the game is never launched). Everything below is static
RE of `bin/Crucible.exe` unless marked otherwise. Where a claim is checked
against the live capture `server/shard_udp.log` that is said explicitly.

**Status of this document: the dispatch table is proven, the delivery enum is
proven, and one live cross-check passes. Nothing here has been put on the
wire.**

---

## 1. The wire format of an RPC, corrected

`server/shard_stub.parse_entity_rpcs` calls the first byte `delivery` with a
declared range of 0..4. The serialiser at RVA `0x4aa80c0` names all five
fields, so the layout is no longer inferred:

| offset | field name at RVA | type | note |
|---|---|---|---|
| +0 | `RpcDeliveryType` (`0x4aa80de`) | `uint8_t`, ranged | **selects the target network role** |
| +4 | `EntityId` (`0x4aa8164`) | `uint32_t` | full network id |
| +8 | `EntityComponentType` (`0x4aa81b0`) | `uint8_t` | index into the 180-entry table in §2 |
| +9 | `RpcMessageType` (`0x4aa81f1`) | `uint8_t` | the RPC id **within that component** |
| +10 | `Data` (`0x4aa828f`) | blob | `u16` BE length ≤ 6144 (`0x4aa8380`) |

The in-memory struct and the wire have the same offsets, which is what lets
the generated handlers read the RPC id as `byte [msg + 9]`.

### `RpcDeliveryType` is a direction, and it is the gate

Every generated `HandleRpcMessage` opens with `cmp byte ptr [msg], N` and, on
mismatch, logs *"Did not handle RPC message, unexpected role (did the
networkrole change?), discarding rpc type %d role %d"*. **A message with the
wrong first byte is silently discarded — no `StreamError`, no disconnect.**
That is exactly the failure mode `docs/20` observed with Health.

`TestComponent` (type 151, handler `0xa74250`) names the values, because its
two RPCs are literally called after their direction:

```
0xa7426d  cmp al, 1  ->  rpc 0  = RPCAuthorityToClientScriptPublic   (vtable 0x6723638)
0xa74323  cmp al, 2  ->  rpc 1  = RPCAuthorityToAutonomous           (vtable 0x6723280)
```

so:

| value | meaning | evidence |
|---|---|---|
| 1 | **Authority → Client** — every client observing the entity | `0xa7426d` + the struct name |
| 2 | **Authority → Autonomous** — only the client that controls the entity | `0xa74323` + the struct name |
| 3 | Autonomous → Authority (client→server) | every inbound RPC in `shard_udp.log` uses 3 (`docs/25`) |
| 4 | Server → Authority | `RPCServerToAuthority` at `0x6d2e7c8`; not client-facing |
| 0 | unused / invalid | ranged 0..4 |

**Consequence for the shard: the first byte of a server→client RPC is not a
free choice. It must be 1 or 2, and which one is fixed per component by the
client's own compare.** The table in §3 records it for every RPC.

### Live cross-check (strong)

Three independent facts agree, and none of them was used to derive the others:

- `docs/25` histogrammed the capture: the client only ever sends delivery **3**.
  Here 3 is the client→server direction. ✓
- `docs/25`/`docs/17` recorded client→server component **115 rpc 4**
  (`ClientProjectileResult`). The static decode gives
  `ProjectileRequestComponent` ids 0..3 as the four inbound `Confirm*` and
  leaves **4** — `ClientProjectileResult` — as the only id the client does
  *not* dispatch inbound. ✓
- `docs/25` left component **172 rpc 1** undecoded ("everything else the client
  ever says"). The static decode shows `PlayerNetworkInputComponent` dispatches
  only rpc 0 inbound (`SendClientInputCorrection`) and declares
  `SendClientInput` and `SendMigrateClientInput` besides. So **172/1 is
  `PlayerNetworkInputComponent::SendClientInput { StateHash, PreviousInputTime,
  InputArray }`** — which is precisely the three-frame sliding input history
  `docs/25` decoded by hand. ✓

---

## 2. The component-type table is fully recovered

`componentId → class name` for all 180 ids, from the jump table at RVA
`0x4ba22cc` (the same table `tools/inspect_replication_schema.py` already
trusted; here it is read statically instead of from a live process).
`tools/inspect_events_rpcs.py --components` prints it. The ids this project
already knew all check out: 2 `AIComponent`, 6 `AbilityComponent`,
13 `AnimationParameterComponent`, 46 `EntityResourceCollectionComponent`,
58 `GameModeComponent`, 79 `CrucibleHealthComponent`,
115 `ProjectileRequestComponent`, 145 `LocationComponent`,
169 `PlayerComponent`, 172 `PlayerNetworkInputComponent`.

The ids that matter for this document and were **not** previously known:

| id | component |
|---|---|
| 36 | `DamageFloaterComponent` |
| 51 | `EventComponent` |
| 52 | `BroadcastComponent` |
| 61 | `DesignerEventComponent` |
| 80 | `HitReactionComponent` |
| 81 | `IncomingHitIndicatorComponent` |
| 99 | `OverShieldComponent` |
| 120 | `RespawnComponent` |
| 134 | `TargetDummyComponent` |
| 141 | `GameAlertComponent` |
| 157 | `DebugComponent` |
| 161 | `HealthComponent` (the generic one; Crucible uses 79) |
| 179 | `WeaponsComponent` |

---

## 3. The complete server→client RPC table

Method: the NovaNet AutoComponent generator emits exactly one
`<Component>::Client[Base]::HandleRpcMessage` per component. A component with
**no** inbound RPC compiles to a 0x58-byte stub containing nothing but the
`NV_NETWORKASSERT: Got unhandled RPC Type %d in <Component>` call, so "this
component cannot be spoken to" is *observed*, not assumed. Where RPCs exist,
the switch is decoded symbolically and each branch's payload struct is
identified by its vtable → `Serialize` → the generated
`NV_NETWORKASSERT: Failed to serialize <Name>RpcStruct` string. Field names
are the `lea` operands inside that `Serialize`, in wire order.

**166 of the 180 components accept no inbound RPC at all.** The 14 that do:

| comp | component | delivery | rpc | name | payload fields (wire order) |
|---|---|---|---|---|---|
| 10 | AllianceComponent | 2 | 0 | `DeclinedInvitation` | `declinee: uint32` |
| 10 | | 2 | 1 | `AcceptedInvitation` | `acceptee: uint32` |
| 25 | ScriptComponent | 2 | 0 | `ScriptCorrection` | `correctionTimePoint: uint32`, vector |
| 28 | ChallengesDebugMatchComponent | 1 | 0 | `ChallengeProgressReport` | `challengeId`, `progressAmount: float` |
| 32 | CruciCommsComponent | 2 | 0 | `ChannelResponse` | (opaque) |
| 32 | | 2 | 1 | `RespondToken` | (opaque) |
| 36 | **DamageFloaterComponent** | — | — | **none** | — |
| 39 | DebugVisualizationsComponent | 1 | 0..13 | `*SeriesInternal` draw calls | see tool output |
| 42 | DropPodLifetimeMatchComponent | 1 | 0 | `DestroyDropPodVisualRPC` | `mappedNetEntity: uint32` |
| 79 | **CrucibleHealthComponent** | — | — | **none** | — |
| 80 | **HitReactionComponent** | — | — | **none** | — |
| 81 | **IncomingHitIndicatorComponent** | — | — | **none** | — |
| 85 | LoadoutSetupComponent | 2 | 0 | `ConfirmDropPoint` | `id: int32`, `success: bool` |
| 85 | | 2 | 1,2 | *(unnamed, no fields)* | — |
| 85 | | 2 | 3 | `MapLocationPinged` | `location` |
| 86 | TeleportComponent | 2 | 0 | `TeleportedRPC` | `transform` |
| 115 | **ProjectileRequestComponent** | 2 | 0 | `ConfirmProjectileHitEntity` | `scale: float`, `isCrit: bool`, `hitOffset`, `hitNormal`, `hitEntity: uint32`, `projectileAssetIndex: int32` |
| 115 | | 2 | 1 | `ConfirmProjectileHitLocation` | `hitTransform`, `hitNormal`, `projectileAssetIndex: int32` |
| 115 | | 2 | 2 | **`ConfirmHitIndicator`** | `scale: float`, `isCrit: bool`, `damageType`, `directDamage`, `projectileTypeIndex: int32` |
| 115 | | 2 | 3 | **`ConfirmKillIndicator`** | `killedEntity` |
| 115 | | *(3)* | *(4)* | `ClientProjectileResult` | **client→server**, already decoded |
| 117 | QuickCommsComponent | 2 | 0 | `QuickCommPingResponse` | (opaque) |
| 117 | | 2 | 1 | `QuickCommNotifyClientSpamPreventionTriggered` | — |
| 120 | **RespawnComponent** | 2 | 0 | `RespawnWaveExpiredRPC` | `transformToRespawnAt` |
| 120 | | 2 | 1 | *(unnamed, no fields)* | — |
| 140 | TutorialObjectivesComponent | 2 | 0..6 | objective list / input suppression | see tool output |
| 144 | CrucibleVoiceScriptComponent | 2 | 0 | `PlayDialogueLine` | vector |
| 151 | TestComponent | 1 / 2 | 0 / 1 | the two direction probes | `Test: int32` |
| 155 | **AudioComponent** | 1 | 0 | `PlaySoundEffect` | `TriggerName: StringAsset` |
| 157 | DebugComponent | 1 | 0..18 | draw primitives, team message | see tool output |
| 170 | PlayerEACComponent | 2 | 0 | `SendEACPayloadToClient` | vector |
| 172 | PlayerNetworkInputComponent | 2 | 0 | `SendClientInputCorrection` | `DesyncType`, `ServerHash: uint32`, `InputArray` |
| 179 | **WeaponsComponent** | 1 | 0 | `SendConfirmHit` | `WeaponIndex: uint8` |

Run `tools/inspect_events_rpcs.py --dispatch` for the machine-readable version
including every handler RVA, vtable RVA and `Serialize` RVA, and
`--structs` for all 84 RPC payload structs (both directions).

### What that table settles

1. **There is no damage-taken RPC and no death RPC.** `CrucibleHealthComponent`
   (79), `HitReactionComponent` (80), `IncomingHitIndicatorComponent` (81) and
   `DamageFloaterComponent` (36) each compile to the 0x58-byte "no inbound RPC"
   stub — `0x935940`, `0x943f40`, `0x946df0`, `0x86ea80`. Whatever makes a
   character flinch, float a damage number, draw a hit direction or die is
   **not** an RPC. It is property replication on those components (§4).
2. **`docs/21`'s framing of the four `Confirm*` is confirmed, with the exact
   mechanism.** All four are `RpcDeliveryType` **2**, i.e. Authority →
   Autonomous: they reach only the client that *controls* the entity they are
   sent on. Sent on the shooter's own character they are shooter feedback,
   which is what `docs/21` said. They are not a way to tell a third party that
   someone took damage.
   But the inverse also follows and is new: because our player *is* the
   autonomous proxy of `0x01000002`, the shard **can** send all four to the
   player today. `ConfirmHitIndicator` is the hitmarker, `ConfirmKillIndicator`
   is the kill confirmation.
3. **`AudioComponent::PlaySoundEffect` (155/0, delivery 1)** is a general-purpose
   "play this sound on this entity for everyone" message with a single
   `StringAsset` trigger name — the same interned-hash `StringAsset` type
   `docs/13` decoded for `SliceEntryId`. It is the cheapest visible/audible
   server→client effect in the whole table.
