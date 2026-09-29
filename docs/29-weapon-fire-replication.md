# Server→client weapon fire: `HybridProjectileActivations` decoded

Continues the open trace left in `21-hunter-ai.md` ("Server-to-client weapon
fire: open trace, not implemented"). That section named
`HybridProjectileActivations` (`0x670db30`) as the best remaining candidate and
said its record index, encoding and semantics were unknown.

The field encodings and record topology are now recovered from the retail
executable and the existing `live-combat-layout.json` capture. The 2026-09-22
audit corrected the earlier guessed time width, separate bool bytes, missing
static baked-name prefixes, and Marine primary index. None of this document's
static or saved-capture evidence establishes a visible live shot.

Evidence convention: bare hex numbers are retail RVAs in
`~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe`
(sha/build as used by every other doc here). Disassembly via `tools/xref.py`.

---

## 1. PROVEN: `HybridProjectileActivations` is the *only* thing
## `ProjectileRequestComponent` broadcasts to all clients

`docs/21` was right to suspect the four RPC structs are shooter feedback. The
static evidence now settles it by exhaustion rather than by inference.

`ProjectileRequestComponent` is native component type **115**
(`snapshots/gamemode-20260919/component-replication-schema.json`, captured live
by `tools/inspect_replication_schema.py`), with expanded record bits
`[42, 0, 17, 8]` = 42 server→clients, 0 server→servers, 17 server→autonomous,
8 client→servers.

Disassembling the component's generated serialisers
(`tools/xref.py --range 0x9dd000:0x9de500`; the file-path strings name
`BinTemp/Gen/NovaGameClient/Generated/...`):

| function | RVA | properties it visits |
|---|---|---|
| `ClientBase::SerializeAuthorityToClientProperties` | assert at `0x9dda54`, body `0x9dda00`–`0x9ddc34` | **`HybridProjectileActivations` and nothing else** |
| `ClientBase::SerializeAuthorityToAutonomousProperties` | assert at `0x9ddf7b` | `SwayRatio`, `NextProjectileId`, `WedgeStep`, `LastMovementTimePoint`, `KickRatio`, `CurrentWedgeNum`, `BloomReticleRatio`, `IsDead`, `HybridProjectileHit`, `SwayMotion`, `ShotAccuracyInfo`, `MoveBloomReticleRatio`, `PreviousAccuracyMode` |

So the whole "reticle bloom / sway / kick / hit confirm / next projectile id"
family is **autonomous-only** — it reaches the owning player and nobody else.
That is the mechanical confirmation of `docs/21`'s framing: those are shooter
feedback. The single property every other client receives is
`HybridProjectileActivations`.

**This is the NPC-fire broadcast channel.** If a server-owned entity is to be
seen shooting, this is the property that has to change.

## 2. PROVEN: it is a replicated *array* property, not an event

At `0x9ddb74` the serialiser builds a descriptor labelled
`"SerializableFixedSizeArray"` with capacity dword `0x28` at `[rsp+0x60]`, then
calls the array wrapper `0x9e02d0`, which does
`ISerializer::BeginObject("HybridProjectileActivations")` (`vtbl+0x90`),
`0x9e1cb0`, `EndObject` (`vtbl+0xd0`).

`0x9e1cb0` is the array body and it is an unrolled-guard loop:

- `cmp edi, 0xa` / `jb 0x9e1d20` at `0x9e1e30` — **exactly 10 elements**.
- `add rbp, 0xa0` at `0x9e1e1f` — element stride **0xa0 = 160 bytes**.
- element label is generated as `"Elmt"` + two uppercase hex digits
  (`strncpy_s` at `0x9e1d70`, nibble-to-ASCII at `0x9e1db3`), i.e. `Elmt00` …
  `Elmt09`. Same convention as the `AssetHashMap` XML in `docs/13`.
- the element type name registered for the profiler marker at `0x9e1e79` is
  **`ProjectileNetPayloadClass`** — the symbol `docs/21` spotted at `0x670d618`
  without being able to place it.
- each element is serialised by `0x9e2b80`.

So `HybridProjectileActivations` is a **fixed 10-slot ring of
`ProjectileNetPayloadClass` records replicated from the server to every
client**. It is a *property*, not an RPC: it rides the normal `EntityUpdates`
replication record that `server/shard_stub.py` already builds.

That shape also explains the name. It is not "a hybrid projectile activated";
it is the array of currently-live projectile activations owned by this
entity, with a slot index that wraps — which is why the autonomous side carries
`NextProjectileId` and the client→server `ClientProjectileResult` carries a
`u8 projectileId` (`docs/17`). The `projectileId` the client reports back is an
index into this same activation space.

### Record topology, recovered from the existing capture

`snapshots/gamemode-20260919/live-combat-layout.json` already contains the
component-115 graph on the player and both hunters. It has 43 rows including
the sentinel: component root, array root, and ten element roots each with
three leaf children. **42 = 1 component + 1 array + 10 × 4; there is no Size
node.** The generated element serializer names the three children, in order,
`m_Common`, `m_Deflect`, `m_Owner`. See §6 for the exact indices.

## 3. PROVEN: the element layout — `ProjectileNetPayloadClass`

Element serialiser `0x9e2b80`. After
`BeginObject(label)` (`vtbl+0x90`) it calls `vtbl+8` (the node decision) and
then, in wire order:

| # | member | struct offset (element-relative) | serialiser |
|---|---|---|---|
| 1 | `m_Common` | `+0x10` | `0x357cae0` |
| 2 | `m_Deflect` | `+0x70` | `0x357cbf0` |
| 3 | `m_Owner` — `uint32_t` | `+0x80` | `vtbl+0x38` |

`m_Owner` is emitted with type name `"uint32_t"` and flag dword `0x10001`
(`0x9e2ca2`). It is the **network entity id of the shooter**.

`vtbl+0xd0` (`EndObject`) then `vtbl+0xd8` closes the element and installs a
handler pointer at element `+0x88`/`+0x98` — a client-side callback slot, not
wire data.

### 3a. `m_Common` — `0x357cae0` → body `0x553d450`

`0x357cae0` is a thin `BeginObject("m_Common")` / `0x553d450` / `EndObject`
wrapper. Note it does **not** call `vtbl+8`, i.e. `m_Common` is transparent to
the node decision; `m_Deflect` does call it.

`0x553d450` branches on `vtbl+0x18` (`0x553d4bf`):

- **`al != 0`** → the *unpacked* path: `FireOrigin` (`+0x20`), `IdealOrigin`
  (`+0x30`), `IdealTargetPosition` (`+0x40`) each written as a full Vec3 via
  `0x7f4c80`, with the type name literal `"UNKNOWN"`. This is the
  debug/ObjectStream path.
- **`al == 0`** → the *network* path at `0x553d58a`: each Vec3 is compressed to
  a `uint64_t` by `0x4f47e10` and written as `PackedFireOrigin`,
  `PackedIdealOrigin`, `PackedIdealTargetPosition` through `vtbl+0x30` (the
  uint64 slot, type name `"uint64_t"`, flags `0x10001`). On the read side
  (`vtbl+0x10` returns 1, `0x553d6bd`) each is expanded back with `0x4f47d10`.

Wire order on the network path:

| # | field | type | offset | serialiser |
|---|---|---|---|---|
| 1 | `AllowedProjectileIndex` | `uint8_t` | `m_Common+0x10` | `vtbl+0x48` |
| 2 | `PackedFireOrigin` | `uint64_t` | packed from `+0x20` | `vtbl+0x30` |
| 3 | `PackedIdealOrigin` | `uint64_t` | packed from `+0x30` | `vtbl+0x30` |
| 4 | `PackedIdealTargetPosition` | `uint64_t` | packed from `+0x40` | `vtbl+0x30` |
| 5 | `CreationTime` | (see §4) | `m_Common+0x50` | `0x9ff670` |
| 6 | `HasFirstUpdateHit` | `bool` | `m_Common+0x54` | `vtbl+0x78` |

This is a complete, self-contained "a shot happened" record: **who fired**
(`m_Owner`), **which projectile of the shooter's loadout**
(`AllowedProjectileIndex`), **where the muzzle was** (`FireOrigin`), **where the
shot was aimed from and at** (`IdealOrigin`, `IdealTargetPosition`) and **when**
(`CreationTime`).

`IdealOrigin` vs `FireOrigin` is the classic third-person split: the muzzle the
remote client should draw the tracer from, versus the camera/eye ray the shot
was actually computed along. `docs/22`'s spitter timeline — muzzle offset +Z
2.75, then *sample target*, then `CreateProjectile` — maps onto
`FireOrigin` / `IdealTargetPosition` directly.

### 3b. `m_Deflect` — `0x357cbf0`

`BeginObject("m_Deflect")`, `vtbl+8` (node decision), then:

| # | field | type | offset (m_Deflect-relative) | serialiser |
|---|---|---|---|---|
| 1 | `m_Deflected` | `bool` | `+0x08` | `vtbl+0x78` |
| 2 | `m_Attachment` | (see §4) | `+0x0a` | `0x343e4f0` |

Deflection (Bugg's shield, etc.) rides in the same record rather than a second
message. For an NPC shot both are the default.

---

## 4. PROVEN: the three leaf encodings

### 4a. The packed Vec3 is the packer the shard already has

`0x4f47e10` is byte-for-byte the function `shard_stub.pack_practice_spawn_transform`
already implements and whose constants `docs/16`'s note records as *verified in
the running client*. The disassembly confirms every step:

- component X: `(x + 4500) * (2097151.5 / 4500)`, `round`, `shl rbx, 0x2a`
  (`0x4f47ea1`) — 22 bits at bit 42.
- component Y: same scale, `and eax, 0x3fffff` then `shl rax, 0x14`
  (`0x4f47ef2`) — 22 bits at bit 20.
- component Z: `(z + 1300) * (524287.5 / 1300)` — 20 bits at bit 0.

`0x4f47d10` is the inverse, i.e. `hunter_patrol.unpack_position`.

**So `PackedFireOrigin`, `PackedIdealOrigin` and `PackedIdealTargetPosition`
are each exactly the first eight bytes of `pack_practice_spawn_transform`,
big endian.** The server already knows how to write all three. Nothing new is
needed, and nothing has to be guessed.

### 4b. `CreationTime` is a `RelativeGameTimePoint`, not an absolute one

`0x9ff670` wraps `0x4a13760`, which branches on `vtbl+0x18` exactly as
`m_Common` does:

- plain/debug serialiser → write the absolute `GameTimePoint` through
  `0x77d1d0` — the same `u32 BE ms` serialiser `docs/13` identified for
  `ServerGameTimePoint`.
- network serialiser → `NovaNet::RelativeGameTimePoint::Serialize` at
  `0x4a137d9`. It reads the scope's start time (`0x4b64100`, field `+8`) into
  `esi` and the stored absolute time into `r15d`, and writes
  **`delta = scopeStart - creationTime`** (`0x4a1385d`) as a field named
  `"RelativeGameTime"` through `0x4a13ac0`. The read side inverts it:
  `creationTime = scopeStart - delta` (`0x4a13955`).
- `0x7fffffff` is the **invalid/sentinel** value on the wire; the in-memory
  sentinel is the dword at `0x7408364`-ish (`rip+0x29f4b10` from `0x4a1384e`),
  and the two are mapped onto each other in both directions (`cmovne` at
  `0x4a13902`, the `je` at `0x4a13969`).
- there is a range assert: *"NV_NETWORKASSERT: Time range out of bounds with
  relative time %u and game time %u with diff value %lld"*, `GameTimePoint.cpp`
  line `0xde`, tripping when `|delta| >= 0x7fffffff`.
- writing one outside a `ScopedRelativeStartTime` asserts *"Invalid
  RelativeTimePoint. Use ScopedRelativeStartTime to serialize
  RelativeGameTimePoint"* (`0x4a1381f`).

**Practical consequence for the shard:** `CreationTime` is a delta in
milliseconds measured backwards from the packet's own game-time scope, which
for an `EntityUpdates` packet is the `ServerGameTimePoint` in its header
(`docs/13`). A projectile created in the same tick the packet is built is
therefore **`0`**. That is the value an NPC shot wants, and it needs no clock
that the shard does not already maintain.

**Wire width resolved:** `0x4a13b33`–`0x4a13b41` applies signed zigzag,
`(delta << 1) ^ (delta >> 31)`, then calls `0x4fafb80`. That helper emits
unsigned LEB128: low seven bits with continuation bit 7, then shifts right
seven until the final byte. The inverse is `0x4fafcd0` followed by zigzag
decode at `0x4a13b1d`–`0x4a13b2c`. Examples: `0 → 00`, `1 → 02`,
`-1 → 01`, `64 → 80 01`. A same-tick activation therefore uses **one byte**
for CreationTime, not four.

### 4c. `m_Attachment` is a `uint16_t`

`0x343e4f0` wraps `0x42e8850`, which is
`Crucible::AttachmentPointName::Serialize` (the function-name string is at
`0x42e897c`). It serialises a single `uint16_t` through `vtbl+0x40` with flags
`0x10001`, then validates it against a name table — *"Unrecognized
AttachmentPoint id %d"*. ID **0 is the executable's static `None` entry**,
and is also the payload constructor's default (`0x357cf20`). See §5c.

### 4d. Bools share a byte across fields and objects

The network bool reader `0x4f565d0` allocates a byte only when its bit cursor
at `this+0x28` is zero, then reads the next low-to-high bit from the remembered
byte offset `this+0x24`. Other scalar reads do not reset this cursor.
Consequently a standalone activation uses **one shared byte**: bit 0 is
`HasFirstUpdateHit`, bit 1 is `m_Deflected`. Emitting one byte for each bool
would shift Attachment and Owner. Combining activations or other bool-bearing
properties requires a shared cursor across the complete value serializer.

---

## 5. PROVEN: the shooter owns the array — no projectile entity is spawned

This is the question `docs/21` could not answer, and the answer is already
sitting in this repository.

`hero_loadout.SHARED_TYPES` (`server/hero_loadout.py:84`, derived in `docs/24`
from the installed character slices, and identical across all ten shipped
hunters) is:

```
0, 10, 13, 18, 21, 24, 27, 44, 46, 48, 69, 79, 80, 98, 99, 101, 102, 107, 110,
115, 120, 125, 128, 132, 133, 143, 145, 146, 156, 158, 163, 165, 166
```

**`115` is in that list.** `ProjectileRequestComponent` is a component of the
*character* entity, and `shard_stub.build_practice_character_probe` already
emits a record bit for it — currently always `False` — on every character
update it sends. Its position in the shared traversal is therefore already
fixed and already correct: it is the 20th root decision (index 19, zero-based),
between `110` and `120`.

The two other projectile components are **not** on the character:

| component | type | shared bits | autonomous bits | on character slice? |
|---|---|---|---:|---|
| `GameplayProjectileComponent` | 59 | 3 | 0 | no |
| `ProjectileComponent` | 176 | 0 | 4 | no |
| `ProjectileRequestComponent` | 115 | 42 | 17 | **yes** |

So the picture is:

- The **hybrid** projectile path (Marine primary is `CreateProjectileType =
  Hybrid`, `docs/20`) replicates *no entity at all*. The server appends an
  activation record to the shooter's `HybridProjectileActivations` array; every
  remote client reads it off the shooter's own entity update and constructs the
  projectile visual locally from `FireOrigin` / `IdealOrigin` /
  `IdealTargetPosition` / `CreationTime` plus the `.projectile` asset it
  already has on disk (speed 625 etc., `docs/20`). That is precisely why
  `docs/21` found no `SpawnProjectile`, `ServerProjectile` or `WeaponFired`
  symbol anywhere in the client: there is no spawn message, because there is no
  spawned entity.
- `GameplayProjectileComponent` / `ProjectileComponent` belong to a different,
  genuinely entity-backed projectile kind — presumably the non-`Hybrid`
  `CreateProjectileType` values. Those are a separate trace and are **not**
  needed for a Marine or a spitter.

`AllowedProjectileIndex` (`uint8_t`) selects which of the shooter's authored
projectiles this activation is, which is why a projectile *asset* never appears
on the wire: the client looks it up from the shooter's own loadout.

And `m_Owner` being a `uint32_t` network id, on an array that already lives on
the shooter, is the redundancy you would expect for a record whose slot is
reused: the client can tell a stale slot from a live one.

---

## 5a. PROVEN: the client system that turns a record into a projectile

The mechanism is named, in full, in the binary:

```
void __cdecl `anonymous-namespace'::ProcessProjectileActivationQueue(
    class Crucible::HybridProjectileSystemClient::SysContext &,
    class ProjectileRequestComponent::Client &,
    const class LocationComponent::Client &)
```

(symbol string at `0x6794ff0`; the function is around `0xdd6900`–`0xdd7a00`,
found by scanning `.text` for rip-relative references to it.) Source file
`.../GameDll/Component/Crucible/Systems/Projectile/HybridProjectileSystemClient.cpp`.

Its diagnostics settle the semantics:

| string | RVA | what it proves |
|---|---|---|
| `...ProcessProjectileActivationQueue: Entity (%s) is autonomous, and should not create remote projectile for itself if they are not deflected` | `0x6795c20` | the queue is processed for **every** entity; the *autonomous* (locally-owned) one skips its own records because it already predicted them. Every **remote** entity — every NPC, every other player — has its projectile created from this queue. This is the NPC-fire path, stated by the client itself. |
| `...: Entity (%s) missing projectile request asset (%s)` | `0x6795cd0` | the projectile asset is resolved from the entity's own `ProjectileRequestClass`, indexed by `AllowedProjectileIndex`. The asset never goes on the wire. |
| `...: Could not retrieve bone mapping asset (%s)` | `0x6795bb0` | `m_Attachment` is resolved to a bone (`GameplayAppComponent::BoneIdFromAttachmentPointName`, `0x69d9770`). |
| `...: Entity (%s) could not load data asset (%s)` | `0x6795b40` | — |

`HybridProjectileSystemClient::UpdateEntity(float, SysContext&,
ProjectileRequestComponent::Client&, EventComponent::Client&,
ParticlesRequestComponent::Client&, const LocationComponent::Client&)`
(`0x6795a10`, note the trailing `Rtt/2` in the symbol blob) then advances the
live `HybridProjectileClass` instances client-side. So the *flight* is
simulated locally too — the server sends one activation, not a position stream.

## 5b. PROVEN: `ValidateNetData` — exactly what the client will reject

`` `anonymous-namespace'::ValidateNetData(const AZ::EntityId, const unsigned int,
const Crucible::Pr...) `` is at **`0xbc3680`**; its failure log is
`0x674cde0`:

```
HybridProjectileSystemClient::ValidateNetData: Entity id (%I64u), name (%s) ->
Network data validation failure. AllowedProjectileIndex (%i), Attachment (%i),
FireOrigin (%s), IdealOrigin (%s), IdealTargetPosition (%s),
CreationTime (%I64u), Deflected (%i)
```

The format string is an **independent confirmation of §3/§4**: it names exactly
the seven fields decoded there, in the same order, and the argument setup at
`0xbc3915`–`0xbc39cf` reads them from exactly the offsets §3 derived. The
element's in-memory layout is therefore doubly attested:

| offset (element-relative) | field |
|---|---|
| `+0x20` | `AllowedProjectileIndex` (`u8`) |
| `+0x30/+0x34/+0x38` | `FireOrigin` x/y/z (`f32`) |
| `+0x40/+0x44/+0x48` | `IdealOrigin` x/y/z |
| `+0x50/+0x54/+0x58` | `IdealTargetPosition` x/y/z |
| `+0x60` | `CreationTime` (`u32`) |
| `+0x64` | `HasFirstUpdateHit` (`bool`) |
| `+0x78` | `m_Deflected` (`bool`) |
| `+0x7a` | `m_Attachment` (`u16`) |
| `+0x80` | `m_Owner` (`u32`) |
| | stride `+0xa0` |

The accept test, in order (`0xbc369a`–`0xbc37fd`) — a record failing **any** of
these is logged and dropped:

1. `AllowedProjectileIndex < 16` (`cmp byte [r8+0x20], 0x10 ; jae fail`).
2. `m_Attachment` resolves in the baked attachment-point table (`0x42e8800`).
3. none of the nine position floats is NaN — nine `_fdtest` calls, each
   rejecting class `2`.
4. `CreationTime != ` the invalid sentinel dword (`0xbc37ee`).
5. `m_Deflected <= 1` (`cmp byte [r8+0x78], 1 ; ja fail`).

It does **not** check `HasFirstUpdateHit` or `m_Owner`.

Note what this buys: a malformed activation is *validated and dropped*, not a
stream desync. The record-bit layout is still the thing that must be exactly
right, but the field values themselves have a documented safe envelope.

## 5c. PROVEN: baked-name IDs include the executable's static prefix

The first inspection mistakenly treated file indices as wire IDs. The
`assets/bakednames/attachmentpoint.names` asset contains 186 **additional**
names. `BakedNames::BusHandlerBase::InitNames` (`0x33fffa4`) first calls
`InitStaticNames` (`0x33ffaf0`), then appends asset names at the current vector
size (`0x340024f`–`0x340027d`).

The AttachmentPoint handler vtable installed at `0x432ddd3` is `0x6c1f1a0`:

- slot `+0x20` → `0x4328710` returns pointer array `0x6c1f190` containing
  **`None`, `Camera`**;
- slot `+0x28` → `0x5822e0` returns **2**;
- interface vtable `0x6c1f1d0`, slot 0 → `0x4328400` accepts an ID below the
  name-vector size. **ID 0 is valid `None`.**

The corrected `tools/inspect_fire_bakednames.py` includes this prefix:

| wire ID | name |
|---|---|
| 0 | None |
| 1 | Camera |
| 59 | Marine_c_barrel1_BND |
| 60 | Marine_c_vfxAttach1_BND |
| 103 | Spitter_Fire_Point |
| 104 | Spitter_Head_Vfx |

The same tool decodes `abilitynames` (static prefix `None`, `marine_sprint`;
handler `0x6c1edb8`, array `0x6c1ee30`) and `healthchangesource` (24 static
names; handler `0x6c1e300`, array `0x6c1e0c0`). Thus `marine_primary` is
AbilityName **51**, and HealthChangeSource **0 is Unknown**. Other tables are
explicitly reported as file indices until their static prefixes are decoded.

### Normal shots use the projectile asset's attachment

`ProcessProjectileActivationQueue` reads payload `m_Deflected` at `0xdd6f55`
and saves it at `0xdd6fa4`. The branch at `0xdd7557` selects:

- deflected: payload `m_Attachment` (`0xdd7565`, `[r13+0x7a]`);
- nondeflected: projectile asset attachment (`0xdd7571`, asset `+0x582`).

The payload constructor initializes deflected=false and attachment=None at
`0x357cf1c`/`0x357cf20`. Thus **attachment 0 is the source-backed default for
ordinary shots**, and the client obtains their visible attachment from the
projectile asset. The validation check still runs for the payload attachment.
The earlier advice to transmit a guessed muzzle name was incorrect.

### Authored allowed-projectile indices

`--request` on the inspector reads `AllowedAssets` in a `.projectilereq`,
retains null slots, and resolves each referenced `.projectile` Attachment
with hashes and offsets. It never writes or extracts into the game install.

| request | index | projectile | authored Attachment |
|---|---|---|---|
| Marine | 0 | marine_projectile_grenade | None |
| Marine | 1 | marine_projectile_grenade_flashfire | None |
| Marine | 2 | marine_projectile_grenade_mini | None |
| Marine | 3 | marine_projectile_grenade_mini_flashfire | None |
| Marine | **4** | **marine_projectile_primary** | Marine_c_vfxAttach1_BND (60) |
| Marine | 5 | marine_projectile_primary_extended | Marine_c_vfxAttach1_BND (60) |
| Marine | 6 | marine_projectile_primary_extended_hyperburst | Marine_c_vfxAttach1_BND (60) |
| Marine | 7 | marine_projectile_primary_hyperburst | Marine_c_vfxAttach1_BND (60) |
| Spitter | 0 | spitter_projectile | Spitter_Head_Vfx (104) |
| Spitter | 1 | spitter_evolved_projectile | Spitter_Head_Vfx (104) |

Marine slots 8..15 and Spitter slots 2..15 are empty. Passing the native
`index < 16` check alone does not establish that a projectile asset exists.
Marine primary **is not slot 0**, which selects a grenade.

Evidence: `assets/player/marine/marine_projectile_request.projectilereq`
SHA256 `49cced4c02fd169099bc5ce01978bcd3ce82f76866cdb756edf8cb306248a0a3`;
`assets/creature/spitter/spitter_projectile_request.projectilereq`
SHA256 `6f1355af28179dec381b5e08bc3faa0b5e0ed7180b38ffb8c7da7bfc3add8fa2`.

## 6. Exact standalone activation record

Use the existing `EntityUpdates` envelope on the already-spawned shooter's
component 115, with the shared ServerToClients traversal. In the saved graph:

| node index | record index | role |
|---|---|---|
| 1 | 0 | component root |
| 2 | 1 | array root |
| `3 + 4*i` | `2 + 4*i` | element i, i=0..9 |
| `4 + 4*i` | `3 + 4*i` | atomic m_Common |
| `5 + 4*i` | `4 + 4*i` | atomic m_Deflect |
| `6 + 4*i` | `5 + 4*i` | atomic m_Owner |

Selecting one complete element consumes **15 decisions**: component + array
+ four for the selected element + one for each of nine skipped elements.
There is no Size decision or value. All other shared component roots are
false. The values are:

| field | encoding |
|---|---|
| AllowedProjectileIndex | u8; Marine primary 4, ordinary Spitter 0 |
| PackedFireOrigin | u64 BE packed position |
| PackedIdealOrigin | u64 BE packed position |
| PackedIdealTargetPosition | u64 BE packed position |
| CreationTime | signed zigzag ULEB128; same-tick 00 |
| HasFirstUpdateHit and m_Deflected | one shared byte, bits 0 and 1 |
| m_Attachment | u16 BE; ordinary-shot default 0 = None |
| m_Owner | u32 BE full shooter network ID |

The shortest value payload is **33 bytes**. `server/projectile_events.py`
encodes exactly one standalone complete slot with explicit leaf inputs and
range checks; `server/test_projectile_events.py` checks the saved topology,
literal bytes, nonzero signed times, and both bool bits. This does not prove
client acceptance, visible firing, ring retirement, or replay handling.

The shard integration is explicitly enabled with `CRUCIBLE_SOLO_FIRE=1` and
is **off by default**. It sends one standalone activation per authored Marine
AI shot using primary index 4, attachment None (0), and both flags false.
At most one activation is emitted per clock packet; simultaneous shots queue
for following ticks with their original creation time preserved in the relative
delta. Events older than the authored 0.4-second lifetime are discarded.
This presentation path does not apply damage. Exact muzzle
origin, animation synchronization, and visible runtime behavior remain
unverified.

### Serializer scope, source-proven on 22 September

The packet loop (`0xe2518d`–`0xe2522e`) creates/consumes/destroys a serializer
per entity message through `0x4bb5471` and `0x4bb57f2`. The UDP provider link
is now traced too. `ClientNetworkAgent` constructs its transport at `+0x38`
and provider at `+0xf8` (`0xe21bc0`/`0xe21bd4`). The provider's derived vtable
is **`0x6d80fd8`**, installed at `0xe21bf2`; its factory slot `+0x38` is
`0x4f207c0`. The earlier audit inspected `0x6d80fb8`, the adjacent transport
vtable, which explains its mismatching slot.

The receive path passes transport `+0xc0` as provider (`0x4f1ed39`), through
`0x4f20d90` and `0x4f249b0` to the callback at `0x4f2573a`.
`0xe233e0` forwards it into the packet handler; `0xe24339` saves it and
`0xe2515a` restores it before the per-message call to `0x4bb5370`.

Factory `0x4f207c0` allocates a fresh `0x38`-byte serializer, installs vtable
`0x675fbe0`, and zeros the byte cursor, remembered bool-byte offset, and bool
cursor at `0x4f20827`/`0x4f20839`/`0x4f20840`. Derived bool reader `0xc52180`
uses the same packed-bit rule as `0x4f565d0`, with change detection. Derived
float reader `0xc51ec0` likewise retains the BE32 encoding.

**The bool cursor resets for each EntityMessage.** Fields within one message
still share it across components and intervening scalars. This is static
control-flow evidence, not a trace of a live call. The pinned-build verifier
`tools/inspect_entity_serializer.py` reproduces the key instructions and
vtables in `snapshots/systems-20260922/entity-serializer-scope.json`.
The existing one-activation-per-packet queue remains a conservative probe
policy; it is no longer required by an unresolved serializer boundary.

## 7. Remaining acceptance work

The previously listed blockers for graph topology, relative-time width, bool
packing, attachment numbering and Marine primary index are resolved above.
The pending checks are runtime behavior: one natural AI shot, client parse
acceptance, projectile/muzzle visibility, repeating shots and ring-slot reuse,
plus the authored animation timeline described below. Constructor defaults
and field encodings do not substitute for that observation.

## 8. Dependency

`docs/22` shows the spitter sets `AnimationParameterComponent.using_primary1 =
True` before firing. `AnimationParameterComponent` is native type **13**, 33
shared expanded bits, and **13 is also in `hero_loadout.SHARED_TYPES`** — so
the muzzle/telegraph animation is a second property update on the *same*
entity in the *same* packet as the activation record. Its wire layout is the
`animation-state` workstream's; this document does not duplicate it. The two
compose: one entity update can carry both the animation parameter and the
projectile activation, which is what the authored timeline in `docs/22`
requires (telegraph, then spawn).
