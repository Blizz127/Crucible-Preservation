# The server shard: UDP transport

Findings of 2026-09-17 evening, after the NovaGameHub handshake started
working (`docs/11`). Everything here is from live captures plus
`Crucible.exe` build `5685572`.

## How the client gets here

`NovaGameClient`'s own state table (rva `0xe30230`) lists nine states:

```
Invalid, Disconnecting, Disconnected, ConnectingToGameRegistry,
ConnectingToHub, LoadingLevel, LevelLoaded, ConnectingToServerShard, Connected
```

With the hub answering correctly the client walks `ConnectingToHub →
LoadingLevel → LevelLoaded → ConnectingToServerShard`, then tries to reach the
address it was handed in `GetServerShardAddress::Success`:

```
ClientNetworkAgent::ConnectToServerShard(): Connecting to Nova Server Shard
  at: 127.0.0.1:18878
```

It retries once a second for about four seconds, then disconnects.

## The link is UDP, and normally DTLS

The client's own log gives it away:

```
Attempted to connect to a server shard while the UDP client connection was
  still open, failing the connect attempt
```

A TCP listener on the shard port captures **nothing**. The transport carries
three `BasePacket` type names (switch at rva `0x4fbbb90`) —
`Unencrypted` = `0x00`, `DtlsHandshake` = `0xCC`, `Encrypted` = `0xFF` — and
the DTLS path is real: `[NET_DebugDtls]` messages, a `DtlsEndpoint` source
file, and *"Pre-shared key pointer is null during client callback"*. The
`PreSharedKey` the hub hands out feeds it, per the cvar description of
`net_EnableDtlsPreSharedKeys`: *"If true, enables the use of pre-shared keys
to kickstart DTLS connections"*.

Implementing a DTLS-PSK server is a large detour (Python has no stdlib DTLS),
and the engine exposes the switch, so `user.cfg` now carries:

```
net_UdpUseEncryption 0
net_EnableDtlsPreSharedKeys 0
```

With those set the client sends plaintext, which is what made the rest of
this section possible.

## What the client sends

Two datagram kinds, repeated while it retries
(`snapshots/shard-first-udp-20260917.log`):

```
73 B   51   | 0005 0001 0001 00f11a03 00 | <57 byte payload> | <4 bytes>
18 B   f001 | 0002 0002 0000 00000000 00 | 00                | 0d4f5728
```

Skip the leading prefix and both parse as the base `Packet` header
(rva `0x4f212f0`), whose field widths come from each field's declared range —
the same range-compressed encoding as the hub:

| field | width |
|---|---|
| `PacketType` | u16 BE |
| `LocalSequence` | u16 BE |
| `RemoteSequence` | u16 BE |
| `SequenceWindow` | u32 BE |
| `IsReliable` | 1 byte (bit-packed bool) |
| `ReliableSequence` | u16 BE, only when `IsReliable` |

Two independent things confirm the reading:

- **The two kinds share one sequence space.** Across a run the sequences
  interleave 1, 2, 3, 4, 5, 6 — the 73-byte datagrams take the odd values and
  the 18-byte ones the even. That byte is also the *only* thing that differs
  between retries of an otherwise byte-identical datagram, which is what
  identifies `LocalSequence` without having to trust the layout.
- **The type-5 payload decodes exactly as `NovaGameClientPackets::Connect`**
  (serialiser rva `0x4b9ed30`): three `u64` hashes — `PacketHash`,
  `ComponentHash`, `StructHash` — then `NetworkProtocolVersion` = **1**, then
  a bool. Encrypted bytes would not land a recognisable `0001` at exactly the
  offset the serialiser predicts.

`PacketType` 5 is `Connect`; the dispatcher's table (`docs/10`) covers 5..12.
Type **2** is below that range and is not a `NovaGameClientPackets` id at all —
it is a transport-level control packet, sent about twice a second with a
single payload byte. Its name has not been recovered.

## The checksum, and what is still unknown

The trailer is a **standard zlib CRC-32**: init `0xFFFFFFFF`, final
complement, table at rva `0x6616060` (dumped and matched against the usual
`0x77073096, 0xee0e612c, 0x990951ba`). It is computed at rva `0x4f23038` over
the serialised buffer and written as the `Crc32` field at `0x4f23057`, last in
`UdpPacketHeader`, inside `NovaNet::UdpSocketWrapper::SendPacket`
(rva `0x4f22d30`).

On the **18-byte** datagrams this is confirmed end to end:
`crc32(region)` big-endian, over everything between the prefix and the
trailer.

On the **73-byte** datagrams it does not reproduce, and this is unsolved.
What has been ruled out, each exhaustively rather than by spot check:

- No CRC-32 of any contiguous range of the datagram equals any 4-byte window
  in it (all start/end pairs, both byte orders).
- No fixed seed works: solving the affine system for the initial CRC register
  gives a *different* required seed for each of the three captured Connect
  datagrams, for every candidate region start.
- The wire bytes do not differ from a CRC'd buffer by a single byte (every
  position, every value).
- No 4-byte value appended to the region produces the trailer, and the
  required values are neither constant nor meaningful.

So its checksum covers something other than its own bytes — a fragment
assembly, a field stripped before sending, or a larger buffer. `NET_Fragments`
and the `0x1800` clamp in the send path are the obvious leads.

Also unresolved: **the transport prefix is one byte on the Connect datagram
(`0x51`) and two on the control one (`0xf0 0x01`)**, and neither matches the
`BasePacket` type values above. `server/shard_stub.py` finds it by locating
the offset where the header parses sanely, which is a heuristic and is
marked as one in the code.

## What the stub does

`server/shard_stub.py` is a UDP listener on `:18878`. By default it only
records. `CRUCIBLE_SHARD_REPLY=accept` makes the first attempt at answering:

- type 2 → mirror it, with `RemoteSequence` set to the sequence being acked;
- type 5 `Connect` → `Accept` (id 6), which deserialises as the base header
  plus one `u8 ServerShardId` (rva `0xe25eb0`).

Both replies use the CRC convention verified on the 18-byte datagrams. The
`Accept` reply is a guess and is untested at the time of writing.

## Scale of what is left

Reaching `Connected` is the handshake. Reaching *gameplay* is not: the shard
has to drive entity replication, and the executable carries hundreds of
`SerializeAuthorityToClientProperties` / `SerializeAuthorityToAutonomous`
implementations across gameplay components (`docs/10`), plus `EntityUpdates`
(id 10) and `EntityRpcs` (id 11) channels. `docs/02` files a playable server
as Phase 4.2 and that remains the right estimate.

## The listen-server shortcut does not exist

Worth ruling out explicitly, because it would have replaced the whole
server-writing effort: many revivals get gameplay by making the shipped
client host its own match.

The client binary *looks* like it might. It carries hundreds of `sv_` cvars
(`sv_EnableLAN`, `sv_LanHost`, `sv_DebugDisableNonceChecks`,
`sv_ClientGameServicePort`, `sv_MaxUnackedPacketCount`, ...), a `NoCurses`
cvar of the sort a dedicated-server console would use, the strings
`DedicatedServer` and `dedicated`, console commands `connect` / `disconnect`
("Connects to the game service and game server"), and the packet type names
`NovaGameServerPackets::EntityUpdates` / `EntityRpcs` / `EntityMigration`.

It cannot. Two checks settle it:

- **No server dispatcher.** `NovaGameServerPackets::DispatchPacket`,
  `::Connect` and `::Accept` do not appear anywhere in the executable, while
  their `NovaGameClientPackets` counterparts do. The `NovaGameServerPackets::*`
  strings that *are* present are the shared type names for packets the client
  SENDS; the code that receives and dispatches them is absent.
- **No server executable.** `bin/` contains only `Crucible.exe` and
  `GameCrashUploader.exe`. Amazon shipped the dedicated server as a separate
  binary that was never in the Steam depot.

The `sv_` cvars are a shared registry declared in common code, not evidence of
a server build. `sv_EnableLAN` in particular is client-side: its only error
string is emitted from `NovaGameServiceClient::ClientConnect` and it selects
which host the CLIENT resolves for the hub (`sv_LanHost`).

So a community server has to be written from scratch, which is what
`docs/02` Phase 4.2 already assumed.
