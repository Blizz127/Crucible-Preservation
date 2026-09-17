# The NovaGameHub protocol (and why the Accept work was aimed at the wrong one)

Static RE session, 2026-09-17, against `Crucible.exe` build `5685572`. No game
run was needed: everything below is read out of the executable with
`tools/xref.py`, and checked against the one Connect frame ever captured
(`snapshots/match-server-first-contact-20260916/match_stub.log`).

## The correction

`docs/10` recovered `NovaGameClientPackets::DispatchPacket` (rva `0xe24300`),
its jump table, and the id-to-name map 5..12 (Connect, Accept, SyncConsole,
SyncConnectionCvars, ServerConsoleCommand, EntityUpdates, EntityRpcs,
ClientMigration). That table is correct — but it belongs to the **game shard**,
not to port 18877.

Port 18877 is the **NovaGameHub**, as the client's own log says:

```
NovaNet::NovaGameServiceClient::ClientConnect(): User 172966378(701223293)
  is connecting to the NovaGameHub at: 127.0.0.1:18877
```

`docs/10` noted that line but kept using the shard's packet table anyway. So
every Accept variant tried — `0100`, the verbatim Connect echo, `00`, `0000`,
`auto`, `auto2` — was a shard-protocol message sent to a hub, and the id-6
"Accept" it was imitating is `NovaGameClientPackets::Accept`, a different
message on a different connection. They could not have worked, and the
one-shot-per-restart fuzzing they cost was spent on the wrong protocol.

The hub is a generated RPC interface
(`dev/BinTemp/Gen/NovaGameHub/Generated/NovaGameHubClient_*.cpp`) with five
calls, each a (Request, Success, Error) triple.

## Message ids

`NovaGameHubClient::CallbackHandler::OnCallback` (rva `0x3187f60`) switches on
the incoming message id:

```
0x3187fa0 movzx eax, r8w
0x3187fa7 add   eax, -6
0x3187faa cmp   eax, 0xd
0x3187fad ja    <default>
0x3187fbc mov   ecx, [rdx + rax*4 + 0x3188c18]   ; 14-entry jump table
```

so the client handles ids **6..19**, and ids **8, 11, 14, 17** fall through to
the default case — those are the ones the *client sends*, which fixes the
triples. The names come from the class name strings at `0x699d5b0`ff and the
deserialiser each case calls:

| id | message | payload |
|---|---|---|
| 5 | `Connect::Request` | u32 `UserId`, str `GameLiftSessionId` |
| 6 | `Connect::Success` | u32 `Result`, str `LevelName`, str `GameRules`, `Session`, bool `ShouldLoadDebugComponents` |
| 7 | `Connect::Error` | u32 `Result` |
| 8 | `Heartbeat::Request` | u32 `UserId` |
| 9 | `Heartbeat::Success` | u32 `Result` |
| 10 | `Heartbeat::Error` | u32 `Result` |
| 11 | `GetServerShardAddress::Request` | — |
| 12 | `GetServerShardAddress::Success` | u32 `Result`, `ServerShardAddr`, `GameInstanceId` |
| 13 | `GetServerShardAddress::Error` | u32 `Result` |
| 14 | `GetUserStatus::Request` | *(empty)* |
| 15 | `GetUserStatus::Success` | u32 `Result`, u32 `UserCount`, u32 `GameState` |
| 16 | `GetUserStatus::Error` | u32 `Result` |
| 17 | `GetServerMetrics::Request` | — |
| 18 | `GetServerMetrics::Success` | u32 `Result`, `ProcessStatsList` |
| 19 | `GetServerMetrics::Error` | u32 `Result` |

Serialiser rvas: `Connect::Request` `0x3185910`, `Connect::Success`
`0x3185be0`, `Heartbeat::Request` `0x3185f20`,
`GetServerShardAddress::Success` `0x3186460`, `GetUserStatus::Success`
`0x3186890`, `GetServerMetrics::Success` `0x3186cc0`, `Session` `0x3186f80`.

The five groups are in address order (Connect, Heartbeat,
GetServerShardAddress, GetUserStatus, GetServerMetrics), which is what pins
each triple to its base id. Two independent checks agree with that assignment:

- `Heartbeat::Request` carries a `UserId` (5-byte payload) and the client's
  id-8 frame is 16 bytes — 11 envelope + 5. Its id-14 frame is 12 bytes, i.e.
  an empty payload, which is what `GetUserStatus::Request` has.
- The client sends id 14 once a second and id 8 every ~15 s. A 1 Hz
  `GetUserStatus` poll is exactly how a lobby waits for a match:
  `GetUserStatus::Success` returns `GameState`, whose enum (rva `0x3185590`)
  is `Connecting, Idle, Playing, Ended`.

## Wire format

```
frame:  u16 BE msg id | u16 BE body length | body
body:   u8 version (=1) | u16 BE msg id | u32 BE request id
        | u8 wants-response | payload
```

The msg id appears twice — once in the transport header, once in the body.

Checked against the captured Connect frame, `0005000d010005000000010129cbd17d00`:

| bytes | field | value |
|---|---|---|
| `00 05` | header msg id | 5 = `Connect::Request` |
| `00 0d` | body length | 13, the exact remainder |
| `01` | version | 1 |
| `00 05` | body msg id | 5 |
| `00 00 00 01` | request id | 1 |
| `01` | wants-response | yes |
| `29 cb d1 7d` | `UserId` | 701223293 |
| `00` | `GameLiftSessionId` | empty |

`0x29cbd17d` is 701223293, which is the number in that run's own log line —
so `uint32` is big-endian and byte-aligned, and the envelope offsets are not a
guess.

This also re-reads the two things `docs/10` got from the same bytes:

- The "constant 5-byte magic `01 00 05 00 00`" was generalised from this one
  Connect frame. Bytes 1-2 are the message id, so on a heartbeat they are
  `00 08`, not `00 05`.
- The "u16 counter at body offset 5" is the low half of the u32 request id at
  offset 3. Both readings give the same number below 65536, which is why the
  observed 0001..0184 run fitted either. `Timing out requestid N` is this
  field, and it is per-connection, not per-sender-and-acked: there is no
  cumulative ack in this protocol. The hub v2 "server sequence + ack" model
  was solving a problem that does not exist.
- The marker byte at body offset 7 is **wants-response**: 1 on Connect and on
  `GetUserStatus` (both of which the client later reports as pending
  requests), 0 on `Heartbeat`. That matches the observation in `docs/10`
  exactly; only its meaning was unknown.

### Field encodings

Field types are read off which `NovaNet::ISerializer` vtable slot the
generated serialiser calls:

| slot | type | seen on |
|---|---|---|
| `+0x38` | `uint32_t` | `UserId`, `Result`, `SequenceWindow` |
| `+0x40` | `uint16_t` | `GamePort`, `PacketType`, `LocalSequence` |
| `+0x48` | `uint8_t` | `ShardId`, `ServerShardId` |
| `+0x78` | `bool` | `IsReliable`, `ShouldLoadDebugComponents` |

`Session` (rva `0x3186f80`) is `{ u8 ShardId, str GameHost, u16 GamePort }`.

**Strings are the one open encoding question.** The only string on the wire so
far is the empty `GameLiftSessionId`, which is a single `00` — consistent with
both a u8 length prefix and a NUL terminator, and the frame length proves it
is one byte either way. `server/match_stub.py` takes `CRUCIBLE_HUB_STR=len8`
(default) or `cstr`. A reply that sends only empty strings does not have to
choose, which is what the first `hubrpc` run should do.

## What the stub now does

`CRUCIBLE_REPLY=hubrpc` (`server/match_stub.py`):

- logs every inbound frame by name, with request id and wants-response;
- answers `Connect::Request` with `Connect::Success`, `Result = Success`,
  empty `LevelName` / `GameRules` / `GameHost`, shard id 0, port 0,
  `ShouldLoadDebugComponents = false`;
- answers `GetUserStatus::Request` with `Result = Success`, `UserCount = 1`,
  `GameState = Idle` (`CRUCIBLE_HUB_GAMESTATE` to change it);
- stays silent on `Heartbeat` (the client does not wait on it);
  `CRUCIBLE_HUB_HB=reply` turns the answer on;
- stays silent on `GetServerShardAddress`, deliberately — see below.

`python3 server/test_hubrpc.py` pins the parse of the captured frame and the
reply shapes.

Unlike Accept, these replies are **not** one-shot: `GetUserStatus` repeats once
a second, so a wrong guess there costs a second, not a game restart. Only
`Connect` is once per run.

## Next step

Run the game with `CRUCIBLE_REPLY=hubrpc` and read the client log. The three
outcomes to look for, in order of what they'd tell us:

1. The `Connect::Request` timeout (`Timing out requestid 1`) stops — the
   envelope and `Connect::Success` both parse.
2. A `[Debug_Nonce]` line or a `GetServerShardAddress::Request` (id 11)
   arrives — the client accepted the connect and moved on to asking where the
   shard is. That is the next message to implement, and the one that leads to
   `ConnectionNonce` / `PreSharedKey` (serialisers at rva `0x31873d0` and
   `0x31876f0`).
3. `Connect::Error` handling on the client side, or a disconnect with a named
   `NovaNet::EResult` — the enum (rva `0x3185320`) is `Success, Timeout,
   InternalError, ConnectionLost, UnknownFailure, VersionMismatch, ServerFull,
   InvalidUserId, InvalidUserTeam, InvalidGameLiftPlayerSessionId,
   EntityNotFound, EntityLimitReached, NotReady`, so a rejection should name
   itself.

`GetServerShardAddress::Success` is left unimplemented on purpose:
`ServerShardAddr` and `GameInstanceId` are not pinned down yet, and the client
only asks for it after Connect succeeds — so there is no point guessing it
before outcome 1 is confirmed.

## Still open

- The string encoding (`len8` vs `cstr`), decidable with the first non-empty
  string the client accepts or rejects.
- Whether nested objects (`Session`) put any framing bytes of their own on the
  wire; assumed not.
- `GetServerShardAddress::Success`: the `ServerShardAddr` layout (the
  `ServerAddrInfo` serialiser at rva `0x4ba3930` is `{ShardId, GameHost,
  GamePort}`, and `0x4ba3d80` adds `UdpShardPort`) and `GameInstanceId`.
- Everything after the hub: the shard protocol is `NovaGameClientPackets`,
  where `docs/10`'s table does apply. `Accept` is
  `{ base Packet, u8 ServerShardId }` — the `uint8_t` in `docs/10` has a name
  now (rva `0xe25eb0`), and the base `Packet` header (rva `0x4f212f0`) is
  `u16 PacketType, u16 LocalSequence, u16 RemoteSequence, u32 SequenceWindow,
  bool IsReliable, [u16 ReliableSequence if IsReliable]` — that *is* a
  sequence-and-ack transport, just on the shard connection, not this one.
