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
| 6 | `Connect::Success` | enum `Result`, str `LevelName`, str `GameRules`, `Session`, bool `ShouldLoadDebugComponents` |
| 7 | `Connect::Error` | enum `Result` |
| 8 | `Heartbeat::Request` | u32 `UserId` |
| 9 | `Heartbeat::Success` | enum `Result` |
| 10 | `Heartbeat::Error` | enum `Result` |
| 11 | `GetServerShardAddress::Request` | — |
| 12 | `GetServerShardAddress::Success` | enum `Result`, `Session ServerShardAddr`, str `GameInstanceId`, u64[4] `ConnectionNonce`, u64[4] `PreSharedKey` |
| 13 | `GetServerShardAddress::Error` | enum `Result` |
| 14 | `GetUserStatus::Request` | *(empty)* |
| 15 | `GetUserStatus::Success` | enum `Result`, `UserStatus`, u32 `UserCount`, enum `GameState` |
| 16 | `GetUserStatus::Error` | enum `Result` |
| 17 | `GetServerMetrics::Request` | — |
| 18 | `GetServerMetrics::Success` | enum `Result`, `ProcessStatsList` |
| 19 | `GetServerMetrics::Error` | enum `Result` |

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
frame:     u16 BE msg id | u16 BE body length | body
body:      u8 version (=1) | u16 BE msg id | Metadata | payload
Metadata:  u32 BE RequestId | u8 HasHandler
```

The msg id appears twice — once in the transport header, once in the body.
`Metadata` is its own serialised object (rva `0x4f1c6c0`), which is where the
request id and the marker byte come from and what names them.

Checked against the captured Connect frame, `0005000d010005000000010129cbd17d00`:

| bytes | field | value |
|---|---|---|
| `00 05` | header msg id | 5 = `Connect::Request` |
| `00 0d` | body length | 13, the exact remainder |
| `01` | version | 1 |
| `00 05` | body msg id | 5 |
| `00 00 00 01` | `Metadata.RequestId` | 1 |
| `01` | `Metadata.HasHandler` | true |
| `29 cb d1 7d` | `UserId` | 701223293 |
| `00` | `GameLiftSessionId` | empty (length 0) |

`0x29cbd17d` is 701223293, which is the number in that run's own log line —
so the envelope offsets are not a guess.

This also re-reads the three things `docs/10` got from the same bytes:

- The "constant 5-byte magic `01 00 05 00 00`" was generalised from this one
  Connect frame. Bytes 1-2 are the message id, so on a heartbeat they are
  `00 08`, not `00 05`.
- The "u16 counter at body offset 5" is the low half of `RequestId` at offset
  3. Both readings give the same number below 65536, which is why the observed
  0001..0184 run fitted either. `Timing out requestid N` is this field, and it
  is a request id, not a sequence number: there is no cumulative ack in this
  protocol. The hub v2 "server sequence + ack" model was solving a problem
  that does not exist.
- The marker byte at body offset 7 is `HasHandler` — the sender registered a
  callback for the response. It is 1 on Connect and on `GetUserStatus` (both
  of which the client later reports as pending requests) and 0 on `Heartbeat`.
  That matches `docs/10`'s observation exactly; only its meaning was unknown.

### Field encodings: integers are range-compressed

**This is the part that is easy to get wrong, and the first `hubrpc` draft did
get it wrong.** A field's wire width does not follow from its C++ type. Every
integer goes through one ranged reader (rva `0x4f57140`) which takes the
field's declared minimum and maximum and sizes itself from the span:

| declared span | width |
|---|---|
| `<= 0xFF` | 1 byte |
| `<= 0xFFFF` | 2 bytes big endian (it calls `ntohs`) |
| `<= 0xFFFFFFFF` | 4 bytes big endian |
| otherwise | 8 bytes big endian |

and the value written is `value - min`. The range lives in each field's
descriptor at descriptor offset `0x10` (two `int64`s), either built on the
stack by the generated serialiser or pointed at in `.rdata`, so it can be read
off directly rather than guessed.

The consequences are concrete:

- `NovaNet::EResult` is a 32-bit enum, but it is declared `0..13`, so **every
  `Result` field is one byte**.
- `NovaNet::EGameState` is declared `0..4` → one byte.
- `UserId`, `RequestId` and `UserCount` are declared over the whole `uint32`
  range → four bytes big endian, which is what the capture shows.
- `GamePort` is declared `0..65535` → two bytes big endian.
- `ShardId` is declared `0..255` → one byte.

Which vtable slot a field calls still identifies its type, and that is how the
descriptors were located:

| slot | reader | type |
|---|---|---|
| `+0x30` | `0x4f57320` | `uint64_t`, ranged |
| `+0x38` | `0x4f57140` | `uint32_t`, ranged |
| `+0x40` | `0x4f56f80` | `uint16_t`, ranged |
| `+0x48` | `0x4f56640` | `uint8_t`, one raw byte |
| `+0x78` | `0x4f565d0` | `bool`, one **bit** |
| `+0x88` | `0x4f56910` | string |

**Strings are settled** (this was open an hour ago): the reader at
`0x4f56910` asks the ranged integer reader for the length with min 0 and max
= the field capacity, and the helper at `0x8552e0` passes capacity 127
(`mov r8d, 0x7f`) for every `String` field. A span of 127 is one byte, so a
string is **u8 length then that many bytes**, capped at 127, and it is *not*
NUL terminated on the wire — the client writes its own terminator into its
buffer afterwards. The earlier `CRUCIBLE_HUB_STR=cstr` option is gone.

**Bools are bit-packed.** The reader takes a fresh byte only when its bit
cursor is at 0, records that byte's offset, and then reads bit *n* of it; the
cursor advances mod 8 and is **not** reset by the non-bool fields in between.
So eight bools scattered through a message share one byte, allocated where the
first of them appears. Every message the stub answers has at most one bool, so
a lone byte is correct here — but a second bool would go into bit 1 of that
same byte, not a new one.

### Struct payloads

```
Session          = u8 ShardId | str GameHost | u16 GamePort      (0x3186f80)
UserStatus       = u32 UserId | str PlayerName | str GameLiftSessionId  (0x31870c0)

Connect::Success = u8 Result | str LevelName | str GameRules
                 | Session | bool ShouldLoadDebugComponents      (0x3185be0)
GetUserStatus::Success
                 = u8 Result | UserStatus | u32 UserCount
                 | u8 GameState                                  (0x3186890)
GetServerShardAddress::Success
                 = u8 Result | Session ServerShardAddr
                 | str GameInstanceId
                 | u64[4] ConnectionNonce | u64[4] PreSharedKey   (0x3186460)
Heartbeat::Success / every ::Error
                 = u8 Result
```

`ServerShardAddr` is the same `Session` serialiser as Connect's. The two
trailing vectors are `SerializableFixedSizeVector<uint64_t>` with a hard count
of 4 (`cmp edi, 4` at rva `0x3187460`) and no length prefix on the wire — 32
bytes each. **That is the nonce and pre-shared key `docs/10` was chasing**: the
hub hands them over in `GetServerShardAddress::Success`, together with the
address of the shard they authenticate against.

## What the stub now does

`CRUCIBLE_REPLY=hubrpc` (`server/match_stub.py`):

- logs every inbound frame by name, with request id and `HasHandler`;
- answers `Connect::Request` with `Connect::Success`: `Result = Success`,
  empty `LevelName` / `GameRules` / `GameHost`, shard 0, port 0,
  `ShouldLoadDebugComponents = false` — 8 payload bytes;
- answers `GetUserStatus::Request` with `Result = Success`, an empty
  `UserStatus`, `UserCount = 1`, `GameState = Idle`
  (`CRUCIBLE_HUB_GAMESTATE` to change it);
- stays silent on `Heartbeat` (the client does not wait on it);
  `CRUCIBLE_HUB_HB=reply` turns the answer on;
- stays silent on `GetServerShardAddress` unless
  `CRUCIBLE_HUB_SHARDADDR=reply`. It is implemented and tested, but pointing
  the client at a shard that does not exist yet would replace a clean
  "connected" result with a confusing shard-connect failure, so the first run
  should leave it off.

`python3 server/test_hubrpc.py` pins the parse of the captured frame, the
range-compression widths, the string encoding, and every reply payload.

Unlike Accept, these replies are **not** one-shot: `GetUserStatus` repeats once
a second, so a wrong guess there costs a second, not a game restart. Only
`Connect` is once per run.

## Next step

Run the game with `CRUCIBLE_REPLY=hubrpc` and read the client log. The three
outcomes to look for, in order of what they'd tell us:

1. The `Connect::Request` timeout (`Timing out requestid 1`) stops — the
   envelope and `Connect::Success` both parse.
2. A `GetServerShardAddress::Request` (id 11) arrives — the client accepted the
   connect and moved on to asking where the shard is. Turn
   `CRUCIBLE_HUB_SHARDADDR=reply` on for the next run and watch for
   `[Debug_Nonce]`, which is where `ConnectionNonce` and `PreSharedKey` land.
3. A disconnect naming a `NovaNet::EResult` — the enum (rva `0x3185320`) is
   `Success, Timeout, InternalError, ConnectionLost, UnknownFailure,
   VersionMismatch, ServerFull, InvalidUserId, InvalidUserTeam,
   InvalidGameLiftPlayerSessionId, EntityNotFound, EntityLimitReached,
   NotReady`, so a rejection should name itself.

If Connect is rejected rather than ignored, the likeliest culprits are the
version byte and the `HasHandler` value on a *response*, which are the two
envelope fields still taken from the one captured request rather than from
code (`CRUCIBLE_HUB_RESPFLAG` flips the latter).

## Still open

- The body's leading `01`. It is called a version byte here because it sits
  outside `Metadata` and was 1 in the capture; the code that writes it has not
  been found.
- `HasHandler` on a server-to-client frame: 0 is what the stub sends, on the
  reasoning that a response does not itself expect one.
- Whether a nested object (`Session`, `UserStatus`, `Metadata`) puts any
  framing bytes of its own on the wire. `Metadata` in the captured frame does
  not — its two fields sit at offsets 3 and 7 with nothing between them or
  around them — and the same is assumed for the rest.
- `GetServerMetrics::Success`'s `ProcessStatsList` (serialiser rva
  `0x3186cc0`), the only Success payload not worked out. Nothing needs it yet.
- Everything after the hub: the shard protocol is `NovaGameClientPackets`,
  where `docs/10`'s table does apply. `Accept` is
  `{ base Packet, u8 ServerShardId }` — the `uint8_t` in `docs/10` has a name
  now (rva `0xe25eb0`), and the base `Packet` header (rva `0x4f212f0`) is
  `u16 PacketType, u16 LocalSequence, u16 RemoteSequence, u32 SequenceWindow,
  bool IsReliable, [u16 ReliableSequence if IsReliable]` — that *is* a
  sequence-and-ack transport, just on the shard connection, not this one.
  Note that its integers are range-compressed too, so those widths are
  nominal until each field's descriptor is read.
