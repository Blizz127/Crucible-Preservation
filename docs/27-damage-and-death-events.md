# Damage and death: what actually makes them visible

Workstream started 2026-09-22, after `docs/20`'s first live run proved that
replicating `Health` alone produces **no** visible damage and **no** visible
death. This document traces the client-side consumers.

Status legend used throughout: **PROVEN** (decoded from the image or a live
capture), **INFERRED** (a reading that follows from proven facts but whose
instructions were not read), **SPECULATIVE** (a plausible story, flagged as
such).

---

## 1. The component census, resolved by name — PROVEN

The 180-entry component-type table at RVA `0x4ba22cc` (dispatcher `0x4ba1d00`,
the one `tools/inspect_replication_schema.py` already validates) names every
network component. Resolving the live layout
`snapshots/gamemode-20260919/live-combat-layout.json` against it — each
component's `getter_bytes` is `b0 <type> c3`, i.e. `mov al,<type>; ret` —
names every component on every live entity.

The four feedback/health components are the subject of this document, and **all four were already on
the wire-visible entities in the 2026-09-19 live capture**:

| type | component | on target dummies (`0xfe0000xx`) | on the player (`0x1000002`) |
|---|---|---|---|
| 36 | `DamageFloaterComponent` | no | **yes** |
| 79 | `CrucibleHealthComponent` | yes | yes |
| 80 | `HitReactionComponent` | **yes** | **yes** |
| 81 | `IncomingHitIndicatorComponent` | no | **yes** |
| 120 | `RespawnComponent` | no | **yes** |
| 134 | `TargetDummyComponent` | not present on the live dummies | no |

This immediately answers part of question 3 in the brief:

- In that Practice dummy census, `DamageFloaterComponent` (damage numbers)
  and `IncomingHitIndicatorComponent` (the victim-side hit-direction arc)
  appear on the player and not the target dummies. This does not establish
  that they are absent from other hunter characters: the 22 September Alpha
  Hunters capture includes both components on Ajonah and all three Marine
  bots. Their autonomous replication direction, rather than component
  presence, determines which viewer receives feedback.
- `HitReactionComponent` is on **every combat entity including the target
  dummies**. That is the victim-side flinch/hit-reaction, and it is the
  component `docs/21` was looking for and did not name.

Note also that `HealthComponent` is type **161**, a different component.
The thing `docs/13`/`docs/20` call "network type 79" is
`CrucibleHealthComponent` — the Crucible-specific gameplay health, not the
engine's generic resource health.

## 2. None of these four is an RPC — PROVEN

Every RPC struct in the image is named by a `…RpcStruct::Serialize` or
`NV_NETWORKASSERT: Failed to serialize …RpcStruct` string. The full set was
enumerated (166 strings). **There is no RPC on `CrucibleHealthComponent`,
`HitReactionComponent`, `IncomingHitIndicatorComponent` or
`DamageFloaterComponent`** except `DamageFloaterComponent::EnableDamageFloaters`
(a settings toggle, not a damage event).

What all four *do* have is:

```
<Component>::Client::SerializeStateDeltaMessage
NV_NETWORKASSERT: Unable to attach <Component>::ClientRecord, out no remaining
                  available bits in record
```

so **the delivery mode is the replicated property-record stream — the same
`EntityUpdates` (packet id 10) path `shard_stub.build_entity_updates` already
drives — not `EntityRpcs` (id 11).** This is good news for the lead: no new
transport is needed, only the right records.

## 3. Reflected death state and replicated death metadata

The image carries this exact set of dotted property names, which is the
component's reflected/blackboard field list:

```
CrucibleHealthComponent.Health
CrucibleHealthComponent.OriginalMax
CrucibleHealthComponent.IsDead
CrucibleHealthComponent.KillerId
CrucibleHealthComponent.KilledByDamageSource
CrucibleHealthComponent.Requests
CrucibleHealthComponent.DoTDamageThisFrame
CrucibleHealthComponent.ReceivedDOTApplied
CrucibleHealthComponent.OutsideCurrentDot
CrucibleHealthComponent.OutsideCurrentHot
CrucibleHealthComponent.ClearDotRequests
CrucibleHealthComponent.ConsumeDotRequests
CrucibleHealthComponent.InterruptMedkits
```

`docs/20` concluded "no separate replicated dead/alive property was located on
the Health component" and that "the server publishes health 0 and lets the
client draw its own conclusion". The reflected field list alone does not prove a separately replicated
`IsDead` record. There is an `IsDead`, a `KillerId` and a
`KilledByDamageSource` on this component,
and the live replication graph exposes property records `[1,2,3,4,5,23]` —
six records, of which `docs/20` only ever used 23 (the `Resource` subtree
carrying min/max/value). Records 1..5 were assumed to be
"regeneration/timing floats"; that assumption was never decoded.

Also note `Crucible::AttemptTick(float&, Crucible::HealthChangeRequest&,
GameTimePoint, CrucibleHealthComponent::Predictable&)` — health changes are
modelled as a queue of `HealthChangeRequest`s. The generated serializer
below names the replicated vector `SelfPersistentHealthChangeRequests`; its
consumer and relationship to each feedback component must be traced separately.

## 4. The six health records, named — PROVEN

`CrucibleHealthComponent::Client::SerializeAuthorityToClientProperties` is the
unwind fragment entered at **RVA `0x9359a0`** (the function-name string is
`lea`'d at `0x935a7a`; the state-delta wrapper `docs/20` already knew,
`0x9355f0`, tail-calls it at `0x935835`). It serialises **exactly six fields,
in this order**, each announced to the `ReplicationBitsetTraverser` through a
descriptor slot at `[[this+0x20]+0x58 + 8*n]`:

| slot | field | member | serialiser called | record |
|---|---|---|---|---|
| 0 | `OutsideCurrentHot` | `+0x1e30` | `0x7f32c0` (float32) | **1** |
| 1 | `KilledByDamageSource` | (via `0x936b10`) | `0x93d520` | **2** |
| 2 | `KillerId` | `+0x1e60` (via `0x7bb620`) | `0x7cb2c0` | **3** |
| 3 | `OutsideCurrentDot` | `+0x1cd0` | `0x7f32c0` (float32) | **4** |
| 4 | `SelfPersistentHealthChangeRequests` | `+0xbc0` | `0x93d6c0` (`SerializableFixedSizeVector`) | **5** |
| 5 | `Health` | `+0x1ad0` | `0x93d870` (Resource) | **23** |

The literal field-name strings are `lea`'d right at each call site
(`0x935bcd`, `0x935c54`, `0x935d5c`, `0x935db1`, `0x935e94`, `0x935ecd`), and
each is paired with the component-type byte `0x4f` = **79** pushed at
`[rsp+0x28]`, which nails the component.

### The record numbering is confirmed by the live graph

`live-combat-layout.json`'s `replication_graph` for the type-79 component on
target dummy `0xfe00004b` is 28 nodes. Its rows are
`[i-1, nextSibling, firstChild, rawFlag, 0]`, so the tree is
(`rawFlag` is not a universal leaf marker; follow `firstChild`):

```
node 1  (component root)
  node 2   leaf                      -> record 1   OutsideCurrentHot
  node 3   leaf                      -> record 2   KilledByDamageSource
  node 4   leaf                      -> record 3   KillerId
  node 5   leaf                      -> record 4   OutsideCurrentDot
  node 6   child 7, chain 7..23      -> record 5   SelfPersistentHealthChangeRequests
                                                    (Size + 16 atomic elements, nodes 7..23)
  node 24  child 25 -> 26 -> 27      -> record 23  Health (Resource)
```

`record = node - 1`, which reproduces the reported
`property_record_indices = [1,2,3,4,5,23]` exactly, and reproduces `docs/20`'s
Health subtree `23,24,25,26` exactly. **The mapping is not a guess: it is the
serialiser's own call order laid over the live graph, and the two agree on
both the count and the index arithmetic.**

### What this means for `docs/20`

`docs/20`'s health traversal is *"component root, records 1..5 false, 23
Resource, …"*. Those five `false` decisions are now named: the server has been
explicitly telling the client, on every single tick,

> no damage source changed, no killer changed, no health-change request
> arrived

while separately dropping the health value from 600 to 0. That is consistent
with the observed lack of feedback; it does not establish which omitted
record or consumer is necessary for a visible reaction.

## 5. `KilledByDamageSource` has an immediate change notification — PROVEN

The generated serialiser is bidirectional; the client takes the read path,
identified by `call [[rbp+0x10]] +0x10` returning `1`. Compare what each field
does *after* a successful read:

- `OutsideCurrentHot` (`0x935c0f`), `OutsideCurrentDot` (`0x935e01`) and
  **`Health` (`0x935f2d`)** each only set two dirty bytes
  (`[this+0x1e39]/[this+0x1e50]`, `[this+0x1cd9]/[this+0x1cf0]`,
  `[this+0x1b7d]/[this+0x1b90]`). **Reading a new Health value sets a flag and
  calls nothing.**
- `KillerId` (`0x935da3`) calls `0x7799e0(this+0x1e60, mode, changed)`.
- **`KilledByDamageSource` (`0x935d05`-`0x935d49`) has an explicit callback
  dispatch.** On `mode == 1 && changed` it loads a handler at
  `[this+0x20e8]`, and if non-null copies an `AZStd::function`-style pair from
  `[this+0x2018]`/`[this+0x2020]` (bumping its refcount with
  `lock xadd [rax+8],1`), then calls `0x318ed30(this+0x20f0, …)` and sets
  `[this+0x20f8] = 1`.

This establishes an immediate change-notification path for property record 2
(`Crucible::DamageMetadata`). It does **not** prove that no later consumer
reads the Health dirty flags, or that this callback alone renders death. The
callback target and repeated-death/reset behavior remain unresolved.

Corroborating: the same `.rdata` block that holds these field names is full of
`Crucible::HistoryBuffer<Crucible::DamageMetadata>`,
`Crucible::ComponentVariable<Crucible::DamageMetadata>::QueueWrite`,
`::BroadcastRemove` and `::GetAuthoritativeValueForSerialize` (`0x66f89e0` ..
`0x66f93e0`). `DamageMetadata` is a first-class replicated, rewindable
component variable. Its name and callback make it a death-metadata candidate;
that does not make it a general replacement for the separate damage feedback
records below.

## 6. The payloads — PROVEN

### Record 2 — `KilledByDamageSource` : `Crucible::DamageMetadata`

Serialiser `0x93d520`. It opens an object named `KilledByDamageSource` and
writes **exactly two fields**, at value offsets `+8` and `+0xa`:

| offset | field | serialiser | type | range |
|---|---|---|---|---|
| +8 | `m_SourceName` | `0x3518920` → `0x4308950` | `uint16_t` enum | `[0, 0xffff]` (descriptor `0x6e02880`) |
| +0xa | `m_AbilityName` | `0x3518a30` → `0x42f0880` | `uint16_t` enum | `[0, 0xffff]` (same descriptor) |

Both go through `ISerializer` vtable slot `+0x40` (the `uint16_t` slot) with
the full 16-bit range, i.e. **two bytes each, no range compression**.

This has the same two-name shape as the `DamageType { SourceName, AbilityName }` pair that
`cdl/practicearena/practicearena_targetdummydeathwatcher.cdl` receives on its
`OnDeathEvent` (`docs/20`). The structural correspondence is established; the
complete dispatch path from this replicated variable into that script event
has not been traced.

### Record 3 — `KillerId` : `uint32_t`

Serialiser `0x7cb2c0`. One field, `ISerializer` slot `+0x38`, declared
`uint32_t` with range descriptor `0x6e02b70` = `{min 0, max 0xffffffff}` —
**four bytes, full range, no compression**. This is the `killerEntityId` of
`OnDeathEvent`.

### Record 5 — `SelfPersistentHealthChangeRequests`: atomic request vector

Serializer `0x93d6c0` calls `0x93f950`. This is a
`SerializableFixedSizeVector` with capacity **16**. Its first child is `Size`
(record **6**), followed by 16 atomic request records **7..22**. The graph
has no descendants beneath any request. **There are no 20 nested field bits**
and no Vec3 child bits inside a request.

Although `Size` uses the `uint32_t` serializer slot, the range descriptor
loaded at `0x93f9ab` is `0x6e02710 = {0,16}`. It occupies **one byte** on the
network, not four. The client validates the limit at `0x93fab0`, then the
normal read/write loop (`0x93fba3`–`0x93fbcb`) visits `Size` elements; schema
construction mode separately enumerates all 16. Each selected element calls
`0x940d40` → **`0x31bbb20`**, with in-memory stride `0xd0`.

The element serializer has **different debug and network branches**. The
network serializer's virtual `+0x18` method is `0x4fdfb0`, which returns false;
the branch at `0x31bbfdf` therefore enters `0x31bc246`. The older field list
incorrectly combined the debug bool/Vec3 branch with this network branch.
The actual network order is:

| byte offset | field | wire form | source |
|---|---|---|---|
| 0 | `m_BoneId` | biased uint32 BE: `value - INT32_MIN` | `0x31bbbb6`, descriptor `0x6e02a80` |
| 4 | `m_PreviousDotTickTimePoint` | uint32 BE milliseconds | `0x31bbc12` → `0x77d1d0` |
| 8 | `m_HealthChangeAmount` | float32 BE | `0x31bbc7b` |
| 12 | `m_IndicatorType` | uint16 BE enum | `0x31bbcc9` → `0x31bc790` → `0x42fc3e0` |
| 14 | `m_ChangeType` | uint16 BE enum | `0x31bbd20` → `0x31bc8a0` → `0x42f74e0` |
| 16 | `m_MetaData` | source uint16 BE, then ability uint16 BE | `0x31bbd77` → `0x93d520` |
| 20 | `m_OverTimeSeconds` | float32 BE | `0x31bbdd9` |
| 24 | `m_SourceEntityId` | uint32 BE | `0x31bbe40` |
| 28 | `m_TargetEntityId` | uint32 BE | `0x31bbea0` |
| 32 | `m_FallOffRatio` | float32 BE | `0x31bbf03` |
| 36 | `m_TotalDotHealthChangeAmount` | float32 BE | `0x31bbf66` |
| 40 | `m_TotalOverTimeSeconds` | float32 BE | `0x31bbfc9` |
| 44 | `healthChangeFlags` | raw uint8 | `0x31bc2e9`, slot `+0x48` |
| 45 | `attackOriginPosition` | packed position uint64 BE | `0x31bc469` → `0x4f47f80` |
| 53 | `impactPosition` | packed position uint64 BE | `0x31bc560` → `0x4f47f80` |
| 61 | `direction` | packed direction uint32 BE | `0x31bc65f` → `0x4f48600` |

A complete selected element is therefore **65 bytes**. The flag byte is an
explicit integer, independent of the serializer's shared bool cursor:

| mask | flag | in-memory offset |
|---|---|---|
| `0x01` | `m_FirstDotTick` | `+0x18` |
| `0x02` | `m_IndicatorDirectDamage` | `+0x22` |
| `0x04` | `m_IsCriticalHit` | `+0x7c` |
| `0x08` | `m_IsUnique` | `+0x7d` |

The debug branch (`0x31bbfe5`–`0x31bc241`) serializes those four members as
bools and three vectors with `0x7f4c80`; the network branch packs them instead.
The reverse flag assignments at `0x31bc71b`–`0x31bc74f` confirm the masks.

Packed positions use `0x4f47e10`/`0x4f47d10` and the existing 22/22/20-bit
position layout. `0x4f47f80` selects full-range uint64 descriptor `0x6e04f00`
and serializer slot `+0x30` at `0x4f480f6`. Packed direction uses
`0x4f483f0`/`0x4f481c0`, not the position codec; `0x4f48600` selects the full
uint32 range at `0x4f48780`. Its normalization and rounding need an independent
codec test before implementation. The width alone is not a tested encoder.

The `m_BoneId` detail matters: signed ranged input `0x4f56690` → `0x4f56b50`
reads four bytes and adds the minimum at `0x4f56c7e`. For example, `-1` is
`7f ff ff ff`, **not** `ff ff ff ff`. The latter decodes to `INT32_MAX`.

The current damage calculation supplies only part of this request. The valid
type enums, request lifetime/replay policy, exact direction codec, and client
consumption are not yet a complete gameplay contract. The independently
replicated feedback components in §9 must not be assumed to arise solely from
sending this vector.

## 7. The wire encoding rule, now explicit — PROVEN

`docs/20` guessed the traversal bit list by pattern-matching. The rule behind
it is decodable and is worth stating, because everything below depends on it.

`ReplicationBitsetTraverser`'s bit reader is **RVA `0x4be8240`**. It:

```
0x4be8260  rcx = [this+0x2b0]          ; the bit reader
0x4be8267  rsi = [this+0x2c0]          ; the running bit index (u16)
0x4be8271  ebx = [rsi]                 ; current index
0x4be8274  call [reader+0x20]          ; total bits available
0x4be8277  jae bail                    ; index past end -> stop, return false
0x4be828e  [rsi] = ebx + 1             ; ADVANCE BY EXACTLY ONE
0x4be8291  call [reader+0x10](index)   ; read that one bit
0x4be82a8  call [listener+8](recordIndex, bit)
0x4be82ac  return bit
```

so the bitset is a **flat, sequential stream: exactly one bit per schema node,
consumed in depth-first visitation order, and a zero bit skips that node's
whole subtree.**

The generated property wrappers use `ISerializer` vtable slot `+0x90`
(`BeginField`) to enter fields, but **a source-level field is not necessarily a
separate replication record**. The captured graph is the authority for record
boundaries: metadata has two values but one leaf; each request has many values
but one leaf. Do not append traversal bits for nested serializer calls inside
an atomic graph node. Resource has actual descendants and therefore does add
those child decisions.

Independent confirmation from an already-accepted message:
`shard_stub.build_composite_client_probe` sends `[F]*7 + [T,T,F]` for
`PlayerComponent{root, UserId, Name}` and then a bare 4-byte BE `UserId` —
one bit for `UserId`, whose serialiser is `0x7cb2c0`, **the same function that
serialises `KillerId`**. So `KillerId` is one bit plus four big-endian bytes,
with certainty, by direct analogy to a message the client has already
accepted.

### Value widths

Resolved from the `ISerializer` vtable slot each field calls and its range
descriptor (the `movdqa xmm0,[rip+…]` constant loaded immediately before):

| slot | type | descriptor | wire |
|---|---|---|---|
| `+0x28` | `float` | `0x6e049d0` (unranged) | 4 bytes BE |
| `+0x30` | `uint64_t` | `0x6e04f00` = `{0, UINT64_MAX}` | 8 bytes BE |
| `+0x38` | `uint32_t` | `0x6e02b70` = `{0, 0xffffffff}` | 4 bytes BE |
| `+0x38` | ranged `uint32_t` | `0x6e02710` = `{0,16}` | 1 byte |
| `+0x40` | `uint16_t` | `0x6e02880` = `{0, 0xffff}` | 2 bytes BE |
| `+0x48` | `uint8_t` | `0x6e02810` = `{0, 0xff}` | 1 byte |
| `+0x58` | `int32_t` | `0x6e02a80` = `{INT32_MIN, INT32_MAX}` | 4 bytes BE after subtracting `INT32_MIN` |
| `+0x78` | `bool` | `0x69a3cf0` = `{0, 1}` | one bit in a shared bool byte (`0x4f565d0`) |

`m_PreviousDotTickTimePoint` is serialised by **`0x77d1d0`**, which
`docs/13` already identified as `ServerGameTimePoint { u32 BE GameTimeMs }` —
the very function `shard_stub.build_entity_updates` already calls for the
packet's own timestamp.

The network bool reader (`0x4f565d0`–`0x4f5662e`) allocates a byte when its
bit cursor is zero and consumes low-to-high bits. Intervening float/integer
fields do not reset that cursor. This matters for `FloatersEnabled` and other
bool properties; it does **not** apply to the request's explicit
`healthChangeFlags` uint8. Byte width follows both the scalar slot and its
range descriptor, not the C++ type name alone.

The native UDP entity-message path constructs a derived, change-detecting
input serializer: factory `0x4f207c0`, vtable `0x675fbe0`. Its bool method
`0xc52180` uses the same cursor algorithm, float method `0xc51ec0` reads the
same BE32 bits, and signed/unsigned integer methods `0xc520e0`/`0xc51fa0`
forward to `0x4f56b50`/`0x4f57140`. The factory clears the bool cursor at
`0x4f20840` for each entity message. Cursor sharing therefore applies within
one message's value stream, not across separate entity messages in a packet.
The provider/factory dispatch chain is recorded in `docs/29`.

## 8. The implementable answer

### 8a. Death metadata — wire layout specified, visible effect unverified

The death-metadata candidate selects **records 2 and 3 of component 79**,
both atomic leaves. `DamageMetadata` contains two `uint16_t` values but its
record has **no child nodes** in the captured graph; those two values add no
traversal bits. It uses the `EntityUpdates` envelope `shard_stub` already
builds, on the victim entity. Whether this produces visible death, and how
metadata must reset for repeated deaths, still require client observation.

Traversal bits, for an entity whose ascending network component type list is
`shared_types` (one bit per component, `False` for every component that is not
79, as `combat.health_traversal_bits` already does):

```
component 79 present                              True
  record 1   OutsideCurrentHot                    False
  record 2   KilledByDamageSource                 True
  record 3   KillerId                             True
  record 4   OutsideCurrentDot                    False
  record 5   SelfPersistentHealthChangeRequests    False
  record 23  Health                               True
    Resource / m_defaultResource                  True
    regen/timing group                            False
    minimum/maximum/value                         True
```

payload after the bitset:

```
u16 BE  m_SourceName        (explicit, source-backed enum value)
u16 BE  m_AbilityName       (explicit, source-backed enum value)
u32 BE  KillerId            (the killer's network entity id)
f32 BE  minimum             (0.0)
f32 BE  maximum             (e.g. 600.0)
f32 BE  value               (0.0)
```

i.e. **ten** decisions:
`(True, False, True, True, False, False, True, True, False, True)`.
The payload gains `>HHI` in front of `>fff`. The separate
`server/death_events.py` encoder implements this selection; the existing
health-only encoder remains unchanged. `server/test_death_events.py` walks
every captured type-79 graph to verify the selection and checks the exact
wire bytes.

The two `uint16_t` fields accept the full integer range on the wire; this
does **not** make every integer a valid gameplay enum. The executable prepends
static names before the `.names` file entries. The HealthChangeSource handler
(`0x6c1e300`, static pointer array `0x6c1e0c0`, count 24) establishes **0 =
Unknown**. The AbilityNames handler (`0x6c1edb8`, array `0x6c1ee30`, count 2)
establishes **0 = None**, **1 = marine_sprint**; the authored `marine_primary`
entry is therefore wire ID **51**. File indices alone are not wire IDs.
`tools/inspect_fire_bakednames.py` now includes these static prefixes.

Although the all-zero pair is valid Unknown/None metadata, no evidence proves
it differs from the client's initial value or triggers a change notification.
Callers still supply explicit source-backed IDs, and visible death plus the
consumer's repeated-kill/reset behavior remain unverified. No all-zero default
probe is enabled.

### 8b. Health requests — atomic layout resolved, semantics incomplete

For one request, select record 5, its `Size` child (record 6), and the first
atomic element (record 7); leave unused request records unselected. The value
stream is one byte `01`, followed by the **complete 65-byte request** above.
Nested metadata, flags and vector members add no record decisions. Record 23
(Health) remains independently selectable.

No health-request encoder or runtime feedback probe was added in this audit.
A safe implementation still needs valid source-backed type enums and values,
request insertion/removal semantics, the direction codec, and consumer
validation. The earlier claim that `combat.py` already computes every required
value was incorrect.

## 9. The separate feedback components — graph and serializer resolved

The fresh read-only capture
`snapshots/systems-20260922/live-animation-20260922T174627Z.json` was recorded
at `2026-09-22T17:46:27.284154+00:00` against executable SHA-256
`e28643747df4a4eccf6a0f5490a3a1c4ab0a6a75b2cee62d3c62f2b191d18203`.
It contains Ajonah (`0x01000002`) and three Marine bots
(`0x01000003`–`0x01000005`). Their component 36/79/80/81 graphs are identical.
The capture includes a local-entity slot but does not record explicit network
roles; AZ entity `active_state = 4` must not be interpreted as a network role.
Direction names below come from generated serializer entry points.

### Type 80: HitReactionComponent — authority to client

Shared graph: component record **0**, then atomic records **1** and **2**.
The state serializer `0x943c00` calls `0x943fa0` (function-name reference
`0x944054`). Its complete field order is:

| record | name | wire | source |
|---|---|---|---|
| 1 | `NetworkedAdditiveHitIndex` | uint8 | name `0x94417c`, call `0x94418d` → `0x79fcd0` |
| 2 | `NetworkedAdditiveHitYaw` | float32 BE | name `0x9441a6`, call `0x9441c3` → `0x7f32c0` |

Selecting both gives component decisions `(True, True, True)` and five value
bytes, before considering other components. Index meaning, yaw units,
change detection and repeated-hit behavior are not established by the wire
layout. A successful decode would not by itself prove a visible flinch.
The pure `server/hit_reaction_events.py` encoder now implements this explicit
two-value shape, with captured-graph and literal-byte tests. It assigns no
index policy and has no live integration.

### Type 36: DamageFloaterComponent — authority to autonomous

The shared graph is empty. The **autonomous** graph has 37 rows including its
sentinel, with component root record 0 and this topology:

| record | name / topology | wire |
|---|---|---|
| 1 | `ServerDamageFloaters`, fixed array of 32 | container; no Size value |
| 2..33 | atomic array entries 0..31 | uint32 BE target, float32 BE damage, uint8 flags |
| 34 | `FloatersEnabled` | packed serializer bool |
| 35 | `LastServerIndex` | uint8 |

Generated entry `0x86eae0` announces
`SerializeAuthorityToAutonomousProperties` at `0x86eb9c`. Array wrapper
`0x86f130` calls `0x870510`; comparisons against `0x20` at `0x87063a` and
`0x87068d` establish 32 entries. Each selected atomic entry calls `0x870d20`
and writes **nine bytes**, in this order:

- `m_TargetEntity`: name `0x870db9`, slot `+0x38` at `0x870dd5`.
- `m_Damage`: name `0x870e07`, slot `+0x28` at `0x870e2e`.
- `m_Flags`: name `0x870e5e`, slot `+0x48` at `0x870e82`.

`FloatersEnabled` calls `0x786620` at `0x86ed6d`; `LastServerIndex` calls
`0x79fcd0` at `0x86eda7`. Entry fields have no nested record decisions.
Flag meanings, ring advancement/reset rules, and delivery to the correct
viewer still need consumer tracing; component presence on a bot does not
make this autonomous stream a shared broadcast.

### Type 81: IncomingHitIndicatorComponent — authority to autonomous

The shared graph is empty. The autonomous graph has 20 rows including its
sentinel:

| record | name / topology | wire |
|---|---|---|
| 1 | `LastServerIndex` | uint32 BE |
| 2 | `ServerHitIndicators`, fixed array of 16 | container; no Size value |
| 3..18 | atomic array entries 0..15 | complete 36-byte entry below |

Generated entry `0x946e50` announces its autonomous direction at `0x946f0b`.
`LastServerIndex` calls `0x7cb2c0` at `0x947013` — unlike the floater index,
this index is **four** bytes. The array wrapper `0x947480` calls `0x9488d0`;
comparisons at `0x9489fa`/`0x948a4d` establish 16 entries. Element wrapper
`0x948dc0` calls `0x3519600` at `0x948df9`:

| byte offset | entry field | wire | call |
|---|---|---|---|
| 0 | `m_Damage` | float32 BE | `0x3519694` |
| 4 | `m_IndicatorScale` | float32 BE | `0x35196e5` |
| 8 | `m_IndicatorType` | uint16 BE enum | `0x3519725` → `0x31bc790` |
| 10 | `m_DamageType` | uint16 BE enum | `0x3519767` → `0x31bc8a0` |
| 12 | `m_IndicatorPosition` | three float32 BE values x/y/z | `0x35197a9` → `0x7f4c80` |
| 24 | `m_ScreenEdgePosition` | three float32 BE values x/y/z | `0x35197f3` → `0x7f4c80` |

These two vectors really are **12 bytes each**, unlike the health request's
packed network positions. Helper `0x7f4c80` calls scalar slot `+0x28` for x,
y and z at `0x7f4d29`, `0x7f4d72` and `0x7f4db8`. None of these internal
fields adds graph descendants beneath the atomic entry. Screen/world-space
meaning, valid enum names, index policy and presentation remain unverified.

## 10. Remaining implementation gates

The highest-impact small wire slice is type 80: two atomic shared properties,
five explicit value bytes, no autonomous envelope or ring buffer. It still
requires source-backed index/yaw semantics before live emission. Types 36 and
81 now have exact entry shapes and record topology, but require the autonomous
message direction and consumer/index rules. Health requests have an exact
65-byte network shape; their gameplay lifecycle and direction quantization
remain additional work. Death metadata retains the explicit opt-in encoder
in §8a, with visible death and reset behavior unverified.

This audit changed documentation only. No packets were sent, no live memory
or assets were written, and no running service was changed.
