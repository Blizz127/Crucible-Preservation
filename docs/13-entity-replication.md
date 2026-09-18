# Entity replication: the shape of the work after `Connected`

Static RE of 2026-09-17, mapping what a shard has to send once the handshake
lands. Nothing here has been exercised against the client — the client has not
reached `Connected` yet. This is the map, not the territory.

## The two gameplay channels

`NovaGameClientPackets` ids 10 and 11 (`docs/10`'s dispatch table) carry
everything that makes a match happen. Both serialisers were identified by the
vector element type they call, not by guessing from the id:

| id | packet | serialiser | payload |
|---|---|---|---|
| 10 | `EntityUpdates` | `0xe262e0` | base `Packet`, `ServerGameTimePoint`, `EntityMessages[]` (via `0x4ba0c00`) |
| 11 | `EntityRpcs` | `0xe26490` | base `Packet`, `EntityRpcs[]` (via `0x4ba0d10`) |

For completeness, the rest of the server→client set, resolved the same way:

| serialiser | contents | packet |
|---|---|---|
| `0xe25eb0` | `u8 ServerShardId` | `Accept` (6) |
| `0xe26020` | `CommandSet[]` | `SyncConsole` / `SyncConnectionCvars` |
| `0xe26180` | `ServerCommand` | `ServerConsoleCommand` (9) |
| `0xe26990` | `LossPercent`, `LatencyMs`, `VarianceMs` | connection stats |
| `0xe26bb0` | `ServerShardAddr`, `MigratedClientGameTimePoint` | `ClientMigration` (12) |

## The replication envelope

Unlike the hub's fixed-size vectors, `EntityMessages` is **length-prefixed**
(body at `0x4b9df30`):

```
EntityUpdates (id 10, rva 0xe262e0)
  = base Packet header
  | ServerGameTimePoint { u32 BE GameTimeMs }        (rva 0x77d1d0)
  | EntityMessages

EntityMessages = u16 BE Size | element[Size]         (Size declared 0..2048,
                                                      descriptor 0x7481d60)
element        = RelativeId          (zigzag varint)
               | u8 TypeAndFlags
               | SliceEntryId        (ONLY when bit7 clear and bit4 set)
               | u16 BE PayloadSize
               | <PayloadSize bytes>
```

The conditional `SliceEntryId` is the client's own logic at `0x4b9dc5b`: it
tests the delete flag (bit 7) first and skips the rest, then tests bit 4 and
only then calls the `SliceEntryId` serialiser (`0x4b9e620`) into `+0x10`.
That is what makes a message a *spawn* rather than a property update.

Built end to end by `build_entity_updates` in `server/shard_stub.py`. A spawn
of `slices/player/hero/hero_character` is eight bytes of entity message:

```
02 11 1de937ad 0000
|  |  |         |
|  |  |         u16 PayloadSize = 0
|  |  SliceEntryId = u32 BE 501823405
|  TypeAndFlags = type 1, bit4 (has slice)
RelativeId zigzag varint = 1
```

- `PayloadSize` is a `uint16_t` declared `0..65535`, so two bytes big endian
  (`0x4b9e904`).
- `TypeAndFlags` is a `uint8_t` (`0x4b9dbac`) stored at `+0x40`, and the
  client unpacks it **in the clear** at `0x4b9dc20`:

  ```
  movzx ecx, byte [msg+0x40]
  shr al,6 ; and al,1   -> +0x0d
  shr dl,7              -> +0x0c
  shr al,5 ; and al,1   -> +0x0e
  shr al,4 ; and al,1   -> +0x0f
  and eax, 0x0f         -> +0x00   (the update type, as a dword)
  ```

  so the layout is:

  | bits | meaning |
  |---|---|
  | 0-3 | update type (dword at `+0x00`) |
  | 4 | flag at `+0x0f` — gates the field at `+0x10`, which is the slice reference |
  | 5 | flag at `+0x0e` |
  | 6 | flag at `+0x0d` |
  | 7 | flag at `+0x0c` — checked **first** and routed to `0x4bb41f0`, the delete path, before the update path runs |

  The dispatch at `0x4bb5424` confirms the two that matter: bit 7 short-
  circuits to delete, and bit 4 is what makes the client read the slice
  reference at `+0x10` instead of treating the message as a plain property
  update. `ValidateUpdate` (`0x4bb4f80`) compares the low nibble against `3`
  and rejects with *"Unexpected update type coming from peer server"*;
  `EntityReplicationManager` exposes `HandleEntityUpdateMessage`,
  `HandleEntityDeleteMessage` and `HandlePropertyChangeMessage`. **Which
  nibble value selects which handler is still unverified** — that is a
  four-way guess the client can settle in one run.

  `pack_type_and_flags` / `unpack_type_and_flags` / `build_entity_message` in
  `server/shard_stub.py`.
- `RelativeId` is **not** a plain integer, and is now decoded: it is a
  **zigzag-encoded LEB128 varint**, i.e. protobuf's `sint32`. Its serialiser
  (`0x4a13ac0`) does `value = (raw >> 1) ^ -(raw & 1)`:

  ```
  mov eax,[raw] ; mov ecx,eax ; and eax,1 ; shr ecx,1 ; neg eax ; xor ecx,eax
  ```

  and `raw` comes from the varint reader at `0x4fafcd0`, a textbook LEB128 —
  `and eax,0x7f ; shl eax,cl ; or [acc],eax ; test dl,dl ; jns done ;
  add rbx,7 ; cmp rbx,0x23` — so seven bits per byte, high bit as the
  continuation flag, and a five-byte (35-bit) ceiling. The caller asserts
  against `0xffffff` (*"Requested Id out of range"*), so ids themselves are
  24-bit. This is the `VariableIntegerSerializer.cpp` that appears in the
  binary's source-path strings.

  Implemented as `encode_entity_id` / `decode_entity_id` in
  `server/shard_stub.py`, round-tripped and checked against the standard
  zigzag vectors (`0→00`, `-1→01`, `1→02`, `-2→03`) and LEB128 vectors
  (`127→7f`, `128→80 01`, `300→ac 02`).

The `<PayloadSize bytes>` blob is the component data.

## Entities are spawned by ASSET REFERENCE, not by serialising components

This is the finding that changes the size of the job, and it came from
following `SliceEntryId` rather than assuming.

`SliceEntryId` (wrapper `0x4b9e620`, body `0x4bfb1d0`) serialises exactly one
field, named `Slice`, of type `StringAsset`. `StringAsset` (`0xa88e90`) in
turn serialises one field, `HashString`, through the ranged-integer slot, and
its descriptor (`0x7480dd0`) declares the full `uint32` range — so the id is
an interned **asset hash written as four bytes big endian**.

> Corrected before testing: an earlier reading called this a varint, from the
> `0x7f / 0x3fff / 0x1fffff / 0xfffffff` comparisons further down `0x4bfb1d0`.
> Those belong to a separate varint length over a different field at `+0x90`,
> not to this id. The type chain is what settles it.

The client resolves that hash locally. Every level ships its own map in
`levels.pak`, e.g.
`levels/practicearena/commondata/stringhashlibrary.xml`, an `AssetHashMap`
of 907 entries, 436 of them `slices/...` paths:

```
<AssetHashMap Size="907">
    <Elmt00000003 Key="9256072"   Value="slices/player/marine/marine_character" />
    ...              Key="501823405" Value="slices/player/hero/hero_character"
```

(Extract it yourself with
`python3 tools/pak.py cat <game>/levels.pak levels/practicearena/commondata/stringhashlibrary.xml`;
it is game content and is deliberately not committed here.)

The console command `SpawnEntityByArchetype` agrees: its usage string is
*"SpawnEntityByArchetype requires a slice name and optional count"*, and the
matching RPC struct is
`DebugComponent::SendDebugSpawnEntityFromArchetypePathRpcStruct`
= `{ archetypePath, spawnCount }`.

So **construction is by reference**: the server names a slice the client
already has on disk, and the archetype supplies every component and its
defaults. The hundreds of `SerializeAuthorityTo*Properties` implementations
are for *property deltas* on an already-constructed entity, driven by the
replication bitset — they are not required to bring an entity into existence.

That reorders the work substantially. Spawning one entity needs:
the `EntityUpdates` envelope (mapped above), `RelativeId` (done),
the `TypeAndFlags` byte (done), a `SliceEntryId` varint (format known, and the
level ships the hashes), and whatever minimal property set the client insists
on — not a reimplementation of the component serialisers.

## Why the payload is still the long pole for *gameplay*

The envelope above is small and tractable. The payload is not. The executable
carries hundreds of generated
`SerializeAuthorityToClientProperties` / `SerializeAuthorityToAutonomousProperties`
implementations, one per replicated gameplay component, driven by
`ReplicationBitsetTraverser` over `NovaNet::ISerializer`. A shard that can
spawn a single controllable player has to produce byte-correct output for
every component on that entity's archetype, in the order the traverser walks
them, with the same range-compressed encodings the rest of the protocol uses
(`docs/11`).

None of that is guesswork-friendly: unlike the hub, where a wrong field cost
one second of a `GetUserStatus` poll, a wrong component byte desynchronises
the whole entity stream and the client's only feedback is a disconnect.

## Realistic order of work

1. **Finish the handshake.** `Connect` → `Accept` on the shard, reaching
   `Connected`. `docs/12` — blocked on a live test.
2. ~~Decode `RelativeId`~~ — **done**, see above. `TypeAndFlags` bit meanings
   are still open and are the next small, self-contained task.
3. **Capture real `EntityUpdates`.** The client never sends these — they are
   server→client — so there is no traffic to learn from. The only sources are
   the generated serialisers in the binary and careful replay against the
   client.
4. **Spawn one entity by slice reference.** Pick a harmless slice from the
   level's own `AssetHashMap`, send a single `EntityUpdates`, and see whether
   the client instantiates it. This is now a small experiment rather than a
   port of the component layer.
5. **Spawn a player.** `slices/player/hero/hero_character` (hash `501823405`
   in practicearena) or `marine_character` (`9256072`), then whatever input
   and property flow the client needs to control it.

Steps 4-5 are where "playable" starts. `docs/02` files this as Phase 4.2, and
the asset-reference finding above shortens it — but does not remove it, and
neither of the two routes that would have removed it survives: a listen server
and starting a level locally are both closed (`docs/12`).
