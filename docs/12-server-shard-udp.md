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


## First full session with the stub replying (2026-09-17 20:04)

166 datagrams, every one of them answered. Snapshot:
`snapshots/shard-session-20260917-2004.log`.

What the client sent, decoded:

| type | count | prefix | RemoteSequence |
|---|---|---|---|
| 5 `Connect` | 40 | `51` | always 1 |
| 2 (control) | 125 | `f0 01` | always 0 |
| 1 | 1, last | `51` | 1 |

**Packet type 1 is new** — sent once, immediately before the client gave up,
so it is almost certainly the transport-level disconnect.

### The prefix is a channel id, and the checksum rule differs per channel

The prefix is constant per channel for the whole session — `51` carries types
5 and 1, `f0 01` carries type 2 — so it is not a counter or a length. Packet
arithmetic confirms both lengths: type 1 is `1 + 11 + 4` = 16, type 2 is
`2 + 12 + 4` = 18, type 5 is `1 + 68 + 4` = 73.

And the two channels do **not** checksum the same way:

- `f0 01` channel: `crc32(region)` big endian, reproduces exactly.
- `51` channel: nothing reproduces. Checked exhaustively on both the 73-byte
  Connect and the 16-byte type-1 — every contiguous range in both byte
  orders, every fixed seed (solved as an affine system), every single-byte
  edit, every appended 4-byte value, and md5/sha1/sha256/HMAC-with-zero-key/
  adler32 over the obvious regions.

So the `51` channel authenticates its packets with something that is not a
CRC over their own bytes.

### RemoteSequence never moved — but it is not an ack

Across 166 datagrams and nine Accept shapes, `RemoteSequence` stayed at 1 on
the `51` channel and 0 on the `f0 01` channel from first packet to last.

It is tempting to read that as "the client received nothing from us", and
that reading was written here first. It is **wrong**: the *first* packet on
the `51` channel of a fresh connection already carries `RemoteSequence = 1`,
before anything could have been received. So the field is not a simple
"highest sequence seen from the peer" — it may be a stream or connection
index, or carry an initial-value convention — and its not moving proves
nothing either way.

What the session *does* show is behavioural: the client re-sent `Connect` 40
times over ~30 seconds and then emitted the type-1 packet and disconnected.
That is consistent with never receiving an Accept it accepted, but it does
not localise the failure to a layer.

So the honest position is that we do not yet know whether our datagrams are
discarded by the transport or rejected by the Accept handler. Two candidate
next probes, in order of cost:

1. **Reply on the `f0 01` channel only**, where our checksum provably matches
   the client's own rule, and see whether anything in the client's behaviour
   changes. That isolates "can it accept our datagrams at all" from "is the
   `51` trailer wrong".
2. **Crack the `51` trailer.** Solving the affine system for the required
   CRC seed per Connect packet gives a structured, non-random series — the
   XOR between consecutive packets alternates, with `0x78c05360` recurring on
   every other step. That is a real handle, and it implies the checksum
   covers something that changes once per packet and is not in the datagram.


## The `51` channel trailer: what it is not

Accumulated across two sessions and a lot of algebra. Recorded so none of it
gets retried.

The trailer is four bytes and the engine's only CRC helper is a standard zlib
CRC-32 (table dumped at rva `0x6616060`, matched against `0x77073096,
0xee0e612c, 0x990951ba`). On the `f0 01` channel `crc32(region)` big endian
reproduces exactly. On the `51` channel none of the following does:

- **Any contiguous range.** All start/end pairs, both byte orders, against
  every 4-byte window in the datagram, on a 73-byte Connect and a 16-byte
  type-1.
- **Any fixed seed.** Solving the affine system for the required initial CRC
  register gives a *different* value per packet.
- **Any single-byte difference** from a CRC'd buffer — every position, every
  value.
- **Any appended 4-byte value**, solved rather than searched; the required
  values are neither constant nor meaningful.
- **A constant XOR**: `crc32(region) XOR trailer` takes 12 distinct values
  over 12 packets.
- **A hidden prepended integer** (e.g. the `PacketId` the engine asserts on):
  70 000 candidate values in five encodings, none matches any solved seed.
- **A rolling checksum** across the connection: neither
  `init_N = trailer_{N-1}`, nor a running `crc32` over concatenated regions,
  nor one over whole datagrams.
- **Common hashes**: md5 / sha1 / sha256 first or last four bytes, HMAC with
  a zero key (we hand the client an all-zero PreSharedKey), adler32, over the
  region and over the whole datagram.

The one real handle: restricted to the 73-byte Connect packets, the solved
seeds form a *structured* series rather than a random one — the XOR between
consecutive packets alternates, with `0x78c05360` recurring on every other
step. Whatever the checksum covers changes once per packet and is not in the
datagram.

Note the ordering caveat that broke a first reading of that pattern: the `51`
channel carries the type-1 disconnect as well as Connect, so "consecutive
packets on the channel" and "consecutive Connect packets" are not the same
series.
