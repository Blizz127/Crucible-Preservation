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

The honest reading is that the sender-side derivation is **under-determined**:
it was fitted on one region length, so several formulas agree there and
diverge elsewhere. Stop fitting. Read
`UdpBaseConnection::ProcessReceived` around rva `0x4f209ef` and find what
`GetData()`/`GetSize()` return for a *received* datagram; that pins the
checksum input exactly.

## Next experiment, already armed

`CRUCIBLE_SHARD_PROBE=1` cycles nine Accept shapes that hold the payload
constant and vary the framing. The decisive one is **variant 3, the Accept
sent on the `f0 01` channel**, where our checksum provably satisfies the
client's own validator. Read it as:

- **client stops re-sending `Connect`** → the `51` trailer was the whole
  problem; concentrate there.
- **no change** → the problem is above the transport and the trailer is a
  distraction.

Do not read `RemoteSequence` as an acknowledgement. It is 1 on the first
`51`-channel packet of a fresh connection, before anything can have been
received.

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
