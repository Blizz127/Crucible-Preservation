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
EntityMessages   = Size (ranged uint via vtable +0x38) | element[Size]
element          = EntityUpdateMessage | u16 PayloadSize | <PayloadSize bytes>
EntityUpdateMessage = RelativeId | u8 TypeAndFlags        (body at 0x4b9daa0)
```

- `PayloadSize` is a `uint16_t` declared `0..65535`, so two bytes big endian
  (`0x4b9e904`).
- `TypeAndFlags` is a `uint8_t` declared `0..255`, one byte (`0x4b9dbac`).
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

The `<PayloadSize bytes>` blob is the component data. That is where the real
work lives.

## Why this is the long pole

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
4. **One entity, one component.** Spawn something inert and see whether the
   client accepts it, before attempting a player.

Step 4 is where "playable" starts, and it is a long way past step 1. `docs/02`
files this as Phase 4.2; nothing found today shortens it. What today did
establish is that the two routes which *would* have shortened it — a listen
server, and starting a level locally — are both closed (`docs/12`).
