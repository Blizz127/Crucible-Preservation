# State of play — 2026-09-17

Where this stands after the session that got the client loading a level from
our own backend, and what to do next. Read this first; `docs/11`–`13` are the
detail.

## What works

| stage | state |
|---|---|
| Menu / live services (`:18876`) | working |
| NovaGameHub handshake (`:18877`) | **working** — `docs/11` |
| Level load | **working** — client loads `practicearena` from our `Connect::Success` |
| Shard address handoff | **working** — client accepts it and tries to connect |
| Shard handshake (`:18878`, UDP) | **not working** — the wall |
| Entity spawn | built, never exercised |

The client walks `Disconnected → ConnectingToGameRegistry → ConnectingToHub →
LoadingLevel → LevelLoaded → ConnectingToServerShard` and then gives up after
~4 s, re-sending `Connect` about 40 times.

## Start everything

```
python3 server/crucible_backend.py --port 18876
CRUCIBLE_REPLY=hubrpc CRUCIBLE_HUB_SHARDADDR=reply \
  CRUCIBLE_HUB_SHARD_HOST=127.0.0.1 CRUCIBLE_HUB_SHARD_PORT=18878 \
  python3 server/match_stub.py
CRUCIBLE_SHARD_REPLY=accept CRUCIBLE_SHARD_PROBE=1 python3 server/shard_stub.py
```

Then just **launch Crucible** — the UI click path is no longer needed. The
game root now carries an `autoexec.cfg` (copy in
`snapshots/autoexec-autoconnect-20260917.cfg`):

```
cl_serveraddr 127.0.0.1
cl_serverport 18877
connect
```

`cl_serveraddr` is described in the binary as *"The NovaGameHub hostname to
connect to"* (default `localhost`), `cl_serverport` pairs with it, and
`connect` is *"Connects to the game service and game server"*.

**This works.** A launch with no UI interaction produced:

```
ClientConnect(): User 3405967138(...) is connecting to the NovaGameHub at: localhost:8192
NovaGameHubClient::ClientManager::Connect: Sending connect request to 127.0.0.1:8192
```

followed by a full hub handshake and 322 shard datagrams. Two notes:

- **The hub port is 8192, not 18877.** `cl_serverport 18877` did not take —
  the port comes from elsewhere (its own help text says the default "should
  match the cvar `sv_ClientGameServicePort`"). Run a second `match_stub` with
  `CRUCIBLE_MATCH_PORT=8192` and the same `hubrpc` env; that is what the
  client actually dials.
- Relaunching repeatedly via `steam -applaunch` gets unreliable after a few
  cycles — the process starts but never reaches the network stage. Give Steam
  time between attempts, or launch from the UI. CryEngine runs
`autoexec.cfg` after system init — later than `user.cfg`, and much later than
the `+map` launch argument that crashed this build early (`docs/08`) — so it
is the right place for a command that needs the game up. `connect` is kept
last so a rejection cannot abort parsing of the cvars above.

If that works it replaces PLAY → mode → READY entirely, which matters because
each test previously needed a human at the keyboard.

`user.cfg` already carries `net_SslEnablePinning 0`, `net_UdpUseEncryption 0`
and `net_EnableDtlsPreSharedKeys 0`; all three are required.

Run `python3 server/test_hubrpc.py` after touching the hub code.

## The one open problem (updated 2026-09-17 21:2x)

The client discards everything we send to the shard. The transport is
understood (`docs/12`) except for one thing:

**The `51` channel trailer is now SOLVED but the Accept is still rejected.**

    crc32( region + 8 zero bytes , init = 0x8a31dc2a ) == trailer

reproduces 87/87 captured 73-byte datagrams across two sessions. `docs/12`
has the derivation and the eight theories it displaced.

It did not unlock anything. Accepts sent with a correct trailer — at the
padded 73-byte length and at the natural 17-byte length, under both the seed
and suffix parameterisations, with and without `IsReliable` — are all
rejected the same way: 40 `Connect` retries, then disconnect.

The honest reading was that the sender-side derivation is **under-determined**:
it was fitted on one region length, so several formulas agree there and
diverge elsewhere.

> **Superseded 2026-09-18.** Do not go read `ProcessReceived` to pin the
> checksum input. The checksum was removed from the equation entirely by
> patching its branch out, and nothing changed — see *The checksum is a red
> herring* below. The derivation being under-determined is probably still
> true; it just is not what is blocking the handshake.

## The checksum is a red herring — settled 2026-09-18

`tools/patch_crc_gate.py` was applied, the client relaunched, and the patch
**reverted**. The binary is back to original bytes; run `--check` to confirm.
Evidence in `snapshots/host-boot-crcgate-20260918-184419/`.

The one-byte patch made `74 4c` → `eb 4c` at file offset `0x4f1ff61`, so the
`Crc32 mismatch, discarding packet` path in `UdpBaseConnection::ProcessReceived`
was unreachable. **Behaviour did not change at all.** Same
`ConnectTimeout`, same `Exceeded connection retry attempts`, same teardown.

Two independent confirmations that the CRC never gated anything:

- `grep -c Crc32` over **every** `NovaNetLogs/*.log` ever captured returns 0,
  including the runs from before the patch. The discard branch was never taken.
- The blocked-attempt ratio is identical either side of the patch: 85/90
  pre-patch, 28/30 post-patch.

So take the second branch of the old prediction: **the problem is above the
transport.** Do not spend more time on the `51` trailer, and do not re-derive
the checksum. The tool stays in the tree, reverted, so this does not get
re-litigated.

## What the run actually found, and it changes the method

The client log states the real reason the retries go nowhere:

```
ClientNetworkAgent::ConnectToServerShard(): Attempted to connect to a server
shard while the UDP client connection was still open, failing the connect attempt
```

**28 of 30** `ConnectToServerShard` calls in the post-patch run were
self-blocked this way (85 of 90 pre-patch). The client is not making 40
connection attempts. It makes **about three**, each dying on `ConnectTimeout`;
everything in between is refused by the client before a packet leaves.

That means the 73-byte `Connect` datagrams arriving at the stub are the
*reliable resend of one connection*, not fresh attempts — and it invalidates
how `CRUCIBLE_SHARD_PROBE` tests:

```
connection 43392: variants used = [4,5,6,7,8,0,1,2,3, 3,3,3, 4,5,6,7,8,...]
connection 55562: variants used = [7]
```

The probe cycles a **different Accept shape per datagram**, so no variant is
ever held constant for the life of a connection, and across the whole session
only variants 4 and 7 ever occupied the slot that matters — the reply to a
connection's *first* `Connect`. Variants 0,1,2,3,5,6,8 have never actually
been tested as a first reply. The earlier reading that "variant 3 was tried
and did nothing" is not supported.

**Fixed 2026-09-18.** `server/shard_stub.py` now keys state on the peer
address instead of sharing one dict process-wide, and the variant is chosen
once per connection and held. `CRUCIBLE_SHARD_VARIANT=N` pins a specific one;
`server/.shard-probe-counter` advances one step per *connection* and persists,
so repeated launches walk the list.

### The bug that fix exposed: our LocalSequence was ~600 too high

The same global dict held `seq`, our LocalSequence, and it never reset. On a
brand-new connection:

| | LocalSequence on first packet |
|---|---|
| client's `Connect` | 1 |
| our `Accept` (before fix) | **625**, then 788 on the next connection |
| our `Accept` (after fix) | 1 |

The base header carries an explicit `SequenceWindow` field, so a reply ~600
ahead of the window is a very plausible silent drop, and it would have
invalidated *every* Accept ever sent from this stub. This is a better
candidate for the original blocker than anything in the checksum.

It is **not** sufficient on its own: variant 0 (the SOLVED-trailer Accept)
re-tested with correct sequencing still draws 39 `Connect` retries. Our first
Accept now reads `51 0006 0001 0001 00000000 00 00 <crc>`, mirroring the
client's own header exactly, and is still refused.

### Walking the rest

`tools/walk_probe_variants.sh` drives one launch per variant, because the
client only opens ~1-3 shard connections per launch before giving up for
good. Each cycle rotates the log, launches, waits out boot plus the ~30 s
shard phase, kills, and summarises. Results land in
`snapshots/probe-walk-<ts>/summary.txt`.

### Result: all twelve fail. The transport theories are exhausted.

Ran 2026-09-18 — 11 launches, **25 connections, all 12 variants covered**,
plus two extra runs with `CRUCIBLE_SHARD_VARIANT=5` and `=7` to give those
two a full-length connection rather than an end-of-budget one.

Every variant draws **38-40 `Connect` retries and then teardown**, with
correct per-connection sequencing and a checksum that satisfies the client's
own validator. Not one behaved differently from the others.

Reading the logs, do not mistake a short connection for progress. Each launch
produces one full-length connection (~40 retries) followed by one or two
6-datagram connections that carry a single `Connect` and end in the `0x53`
teardown. Those are the client burning the last of its retry budget, not an
Accept landing. The walker's "unexpected packet type `53`" flag is a **false
positive** for the same reason — `0x53` appears on every clean give-up.

The only packet types ever observed from the client, across every session,
are `51`, `f0` and `53`. It has never once sent anything that would indicate
an Accept was taken.

## Where that leaves it

Three transport-level explanations have now been tested to destruction and
all are closed:

| theory | how it was closed |
|---|---|
| the `51` trailer checksum is wrong | patched the CRC branch out of the binary; no change (above) |
| our sequence numbers are wrong | were wrong, ~600 too high, fixed; still refused |
| the Accept framing is wrong | all 12 shapes pinned per connection; all refused |

So the Accept is not being rejected on framing, checksum, or sequencing. The
remaining lead is the one the header comment in `shard_stub.py` already
flags and nothing has yet acted on: **the range-compressed integer
encoding.** The base `Packet` header (rva `0x4f212f0`) is documented as using
the same range coding as the hub, which makes every field width *nominal*.
This stub writes fixed-width big-endian fields throughout. If the client
decodes the header with a range coder driven by per-field descriptors, our
bytes do not mean what we intend regardless of how the datagram is framed —
which is exactly the signature we are seeing.

That lead turned out to be already handled — `docs/12` records the declared
widths for the base header (`u16/u16/u16/u32/bool`) and the stub already
writes exactly those. The header is not the gap. What chasing it did turn up
is below, and it is better.

## The checksum is dead for a third time, by a method that cannot be wrong

`accept_variants` built two shapes, `forged_full` and `forged_full_reliable`,
and **never returned them**. Dead variables. They had never been sent, on any
run, ever — while its own docstring called them "the only ones whose trailer
is guaranteed valid on the `51` channel".

That mattered, because every one of the twelve shapes that *was* being sent
carries a trailer we derived, and the file admits the `51`-channel derivation
is under-determined. The forged pair sidesteps derivation entirely: take the
client's own Connect, rewrite the header into an Accept, and repair the CRC
by linearity, which needs no knowledge of where the checksummed region
starts. Re-verified here against the `f0 01` channel where the region *is*
known: **300/300** mutations predict the directly computed CRC exactly.

Both were wired in and tested with a full-length connection each. **Both
refused**, 40 and 39 retries. So an Accept whose trailer is correct *by
construction* is rejected exactly like one whose trailer was guessed. The
checksum is not the blocker and cannot be made into one.

## The layer we had missed: core packets

`UdpBaseConnection::HandleCorePacket` dispatches an enum that sits **under**
the `NovaGameClientPackets` ids (5..12):

```
ECorePackets_InitiateConnection   ECorePackets_TerminateConnection
ECorePackets_Fragment             ECorePackets_Heartbeat
```

Two are pinned by observation, not by guessing the enum order:

| type | evidence | meaning |
|---|---|---|
| 1 | sent exactly once per connection, immediately before teardown | `TerminateConnection` |
| 2 | sent ~twice a second, 18 bytes, one payload byte | `Heartbeat` |

The client has **never** sent type 0 or type 3. Every datagram it has ever
sent, across every session, is one of exactly four shapes:

```
f001 0002  18 B   1734x     51 0005  73 B   450x
51   0001  16 B     12x     53 0001  14 B    11x
```

This matters because the client fails with `ConnectTimeout`, which is a
*transport*-level failure — the UDP connection never came up. A game-level
Accept may simply be the wrong layer to answer on. So three forged core
packets were tested (types 0, 3 and 2 on the `51` channel), each with a
trailer valid by construction and a full-length connection.

**All three refused.** Seventeen variants have now been tested this way and
not one has changed the client's behaviour.

### A second misfire this exposed

The spawn trigger was `ptype not in (2, 5)`, which also caught **type 1**.
Type 1 is the client hanging up. So every `SPAWN probe` line in every log to
date fired at a client that was already disconnecting, and the spawn path
from `docs/13` has still never been exercised against a live connection.
Now gated on `6 <= ptype <= 12`.

### The transport will not talk to us

There are 27 `NET_*` log channels in the binary, including
`[NET_CorePackets] %s: Received core packet %s`, which would have answered
all of this directly. Tried `net_debugVerboseLevel`, `net_assertlogging`,
`net_LogConnectionStats`, `log_Verbosity`, `log_WriteToFileVerbosity`. **Not
one `[NET_*]` line appears at any setting and none of the cvars is
acknowledged** — the channels are compiled out of this release build. Do not
try a third time; see the note now in `autoexec.cfg`.

## Where to go next

Everything reachable by varying *what we send* has now been tried: checksum
(three ways), sequencing, twelve framings, and the core-packet layer. The
client never logs a reason and never sends anything but its four shapes.

The remaining unknown is the one `docs/12` flagged and never closed: **the
`51`-channel checksum covers something that is not on the wire.** The
derivation needed 8 hidden bytes and a non-standard init (`0x8a31dc2a`) to
reproduce, which is the signature of a checksum computed over a larger
buffer — a field stripped before sending, or a connection/endpoint value the
receiver mixes in. If the receiver mixes in something we cannot see, then our
forged trailer is valid only for a packet travelling *client to server*, and
no reply we construct can ever validate. That would explain all seventeen
results at once, and it is testable: find what `SendPacket` (rva `0x4f22d30`)
feeds the CRC at `0x4f23038` that is not in the datagram it emits.

Do that before sending an eighteenth variant.

## Found it: the drop is silent, and it is the sequence window

`ProcessReceived` was read properly. The receive-side checksum rule is
simple and was never exotic:

```
0x4f209fe  call [r9+0xa8]        ; size = GetSize()
0x4f20a08  cmp eax, 4 / jae      ; else "Packet is smaller than its checksum!"
0x4f20a72  lea eax, [r10-4]      ; CRC length = size - 4
0x4f20aa0  <table loop, 0x6616060, init 0xFFFFFFFF, final NOT>
0x4f20b0a  call 0x4f57140        ; read the stored Crc32 (ranged u32)
0x4f20b5a  cmp ebx, [rsp+0xb8]   ; computed vs stored
```

so it is a plain `crc32(buffer[0 : size-4])`. Then, **immediately after the
checksum passes**:

```
0x4f20bbc  call 0x4f21570        ; -> UdpPacketIdWindow::UpdateForReceivedPacket
0x4f20bc1  test al, al
0x4f20bc3  je   0x4f20a51        ; return false -- SILENTLY, no log line
```

That silent `je` is where our packets die. It explains the whole shape of
this investigation: the CRC-gate patch changed nothing because the CRC was
never the gate, and no log line ever appeared because **this path does not
log**. The two rejection reasons inside the window check,

```
"[NET_Debug] Discarding old packet, sequence is too old to process"
"[NET_Debug] Discarding packet due to duplicated sequence id"
```

are both gated on the `NET_Debug` channel, which is off.

### The hidden 8 bytes, confirmed independently

Two Connect retries differ in exactly one body byte (the LocalSequence low
byte at index 4) plus the trailer. CRC linearity turns that into a direct
measurement of where the checksummed region *ends*, with no fitting:

| quantity | value |
|---|---|
| delta byte | `0x02` at index 4 |
| trailer XOR | `0x34e9be5d` |
| solved zero-run after the delta byte | 72 |
| implied region end | offset 77 |
| datagram length | 73 |

So the checksummed buffer carries **8 bytes after the sequence field that
never reach the wire**, confirming the earlier "8 hidden bytes" fit by an
independent route. An exhaustive contiguous-range search reproduces the
`f0 01` channel exactly (`crc32(d[2:len-4])`, the only hit) and reproduces
nothing on the `51`/`53` channels, consistent with that.

### What is blocked

`tools/patch_log_channels.py` is written and **not applied**. It is three
bytes at `0x906300`, the channel-enabled predicate, turning it into
`mov al,1 ; ret` so every `NET_*` channel reports enabled. Emission happens
at the call sites, not inside that predicate, so nothing else changes. With
it on, the client states which check rejected each datagram instead of
dropping it silently, which is the one thing that turns this from guessing
into reading.

It needs a human to approve patching the executable. Until then the
sequence-window hypothesis cannot be confirmed or killed, because the only
evidence for it is a log line that does not currently print.

## APPLIED — and the client finally told us. It was the first byte.

`tools/patch_log_channels.py` was approved and applied. The binary is
**still patched**; `--revert` undoes it. Channels immediately started
firing, and the answer was one line:

```
UdpSocketReader::ReadDataFromSocket(): Received BasePacketType of invalid header 81    (0x51)
UdpSocketReader::ReadDataFromSocket(): Received BasePacketType of invalid header 240   (0xf0)
```

**Every reply this project has ever sent was thrown away on its first byte**,
before `ProcessReceived`, before the checksum, before the sequence window.
That is why seventeen Accept shapes, three checksum theories and the
CRC-gate patch all produced identical behaviour. None of them ever got that
far. The `51`/`f0 01` leading bytes are not channel ids; we copied them off
the client's own datagrams and they are not valid inbound.

The reader accepts exactly three values (rva `0x4f2478f`), then does
`inc rsi` and records `size-1`, so the byte is consumed:

```
test r9b, r9b / je   -> 0x00
sub  edx, 0xcc / je  -> 0xCC
cmp  edx, 0x33 / je  -> 0xFF
```

giving the true framing, which is what `build_shard` already produced —
it had only ever been handed the wrong first byte:

```
[1 byte BasePacketType][base header + payload][4 byte Crc32]
```

Note the asymmetry: the client **sends** `0x51`/`0xf0` but will not
**accept** them. Its outbound framing is not its inbound expectation. That
also explains why the `51`-channel checksum never reproduced as a contiguous
range while the `f0 01` one did.

### Fixed, and verified

`shard_bpt()` now puts a valid BasePacketType on every reply, heartbeats
included (`CRUCIBLE_SHARD_BPT`, default `0x00`).

| | invalid-header rejections |
|---|---|
| before | 222 |
| after | **0** |

### Still not connected, one layer deeper

All three values were tested. All pass the reader and then die **silently**
before anything downstream logs. Two controls pin this down:

- A deliberately corrupted Crc32 (variant 17, a control, not a candidate)
  produces **no** `Crc32 mismatch, discarding packet`. So we are not
  reaching `ProcessReceived`.
- `[NET_Acks] Connection timeout exceeded ... last %d ms` is the time since
  the client last received anything. It climbs monotonically — 14 s, 44 s,
  86 s — and **never resets**, across all three BasePacketType values. The
  client has still never registered a single received packet.

So the drop is now between `ReadDataFromSocket` storing the datagram and
`ProcessReceived` consuming it. The reader writes a 0x40-byte ring entry at
`r12+0x4000000`: vtable at `+0x00`, what looks like the sender address at
`+0x0e`/`+0x1e`, data pointer `+0x30`, size `+0x38`, BasePacketType `+0x3c`.

Next: find the consumer of that ring and what it does with `+0x3c` and the
recorded address. The likely candidates are an endpoint/connection lookup
that fails to match our source, or a DTLS route selected by BasePacketType
where `0x00` is not the plain path. Identify the class from the vtable at
`0x67a2890` and work forward from there.

Do not read `RemoteSequence` as an acknowledgement. It is 1 on the first
`51`-channel packet of a fresh connection, before anything can have been
received.

`0x53` is new and appears exactly once, immediately before teardown
(`530001000600010050089714f298`), followed 50 ms later by a clean FIN on the
hub socket. Read it as the client's disconnect notification, not progress.

## After the handshake

The spawn message is built and unit-tested (`docs/13`): `EntityUpdates`
envelope, `RelativeId` (zigzag varint), `TypeAndFlags` (bit layout read from
the client's own unpack), `SliceEntryId` (u32 asset hash). Spawning
`slices/player/hero/hero_character` is eight bytes. The stub fires it
automatically on the first packet that is neither a control packet nor a
`Connect` retry, sweeping all eight low-nibble values.

Entities are built from slices the client already has on disk, so this does
**not** require reimplementing the component serialisers — that was the
single most important structural finding of the session.

## Two routes that are closed

Both were checked properly, so they should not be revisited:

- **Listen server** — the binary has no `NovaGameServerPackets` dispatcher and
  the depot ships no server executable (`docs/12`).
- **Starting a level locally** — already achieved via the hub, and it does not
  spawn anything. The client is a pure client.

## Method note

Five claims in this session were confidently wrong and caught later: the
Accept payload theory, the `SliceEntryId` varint, the "type is 3 or 4"
narrowing, "nothing was ever accepted", and "the receive path does not
validate the CRC". Each was corrected in place. The pattern is consistent —
disassembly answers *what a field is* reliably, and *what the peer will
accept* unreliably. Prefer a probe over another hour of reading whenever the
client can answer in one run.
