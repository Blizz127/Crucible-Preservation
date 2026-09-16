# Match server: TLS pinning, and first contact (2026-09-16)

Evidence: `snapshots/match-server-first-contact-20260916/`.

## The blocker was certificate pinning, not CA trust

`matchserver.connect` reached the LAN stub and opened TLS, then died. The
client log names the exact check:

```
ValidateCertificateCallback(): OpenSSL preverification failed with (18: self signed certificate)
ValidateCertificateCallback(): net_SslAllowSelfSigned is *enabled*, clearing X509 certificate validation failure
ValidatePinnedCertificate(): Validation failed, certs are different sizes; local 550 bytes, remote 294 bytes
ValidateCertificateCallback(): Certificate validation failed
```

Read in order this is decisive:

1. The engine's TLS is OpenSSL with a `ValidateCertificateCallback`.
2. The self-signed failure was **already cleared** — `net_SslAllowSelfSigned`
   is on by default. So trusting our CA was never the problem.
3. The handshake died at `ValidatePinnedCertificate()`, which compares the
   server cert against a **pinned** one and gives up because the sizes differ.

The pin is Amazon's dead GameLift/NovaNet certificate. No self-hosted server
can match it: the pin is a specific cert whose private key does not exist
anywhere in the client.

**Correction:** `tools/patch_cacert.py` and the `certs/cacert.pem` pak change
were built on the theory that the engine reads its roots from that bundle
instead of the Wine store. That theory was wrong — the bundle was not what
rejected us. The pak change is still installed and its necessity is
**unverified**; see Open items.

## The fix

`net_SslEnablePinning` is a real engine cvar with its own failure strings
(`Failed to retrieve the remote certificate, pinned certificate validation
failed`). Setting it to 0 removes the check:

```
-- game-root user.cfg
net_SslEnablePinning 0
```

After this the client logs `Certificate validation passed` and the
`ValidatePinnedCertificate` line disappears entirely.

Gotchas:

- `user.cfg` uses **`--` for comments**, not `//`. The engine's own
  `system_windows_pc.cfg` (in `config.pak`) confirms this. A `//` comment can
  abort config parsing and silently drop the cvar that follows it.
- `user.cfg` is read at startup, so the game must be restarted after editing.
  It lives at the game root, not in the prefix.
- If `user.cfg` ever proves to be applied too late (the timing trap `docs/06`
  hit with `PlayerAuthorizerEndpoint`), the fallback is the launch option
  `+net_SslEnablePinning 0`, which applies earliest.

## First contact: the client's opening frame

TLS 1.3, no SNI (as `docs/08` observed). 17 application bytes, then silence —
the client waits 30s for a reply and disconnects:

```
[15:26:39.774] TLS handshake OK: TLSv1.3 TLS_AES_256_GCM_SHA384
[15:26:39.775] app <- 17 bytes
               00 05 00 0d 01 00 05 00 00 00 01 01 29 cb d1 7d 00
[15:27:09.804] app read error after 17 bytes: TimeoutError
```

Layout, with one field confirmed against the client's own log:

| Offset | Bytes | Reading |
|---|---|---|
| `[0:2]` | `00 05` | header — constant `5`; likely a message/packet id |
| `[2:4]` | `00 0d` | length `13` — exactly `len(frame) - 4`, so the payload fits |
| `[4:17]` | `01 00 05 00 00 00 01 01 29 cb d1 7d 00` | 13-byte payload |
| `[12:16]` | `29 cb d1 7d` | **u32 BE = 701223293 = `0x29cbd17d`** |
| `[16]` | `00` | trailing zero |

The payload's last five bytes are the **NovaNetUserId**, confirmed against the
same run's own log line:

```
Attempting to connect to 127.0.0.1:18877 with player info:
  [GameSparksId = 172966378, NovaNetUserId = (701223293 : 0x29cbd17d), PlayerSessionId = ]
```

So the opening frame is a 4-byte header plus a payload whose tail is the
client's user id. The rest of the payload is not yet interpreted.

The client also logged, on the same run:

```
NovaEACGameClient::LoadEAC(): EAC Client failed to start
PacketHandlerMetrics::SetPacketToStringFunction(): Register PacketGroup Id:0, total number of Packet Groups : 1
TcpClientManager::DispatchPendingCallbacks(): Timing out requestid 1     <- 30s after sending
```

`requestid 1` is the client's own timeout for the reply it never got. The
disconnect reason the client reports is `RemoteHostClosedConnection`, which is
our stub closing after its own 30s read timeout — i.e. caused by us, not by the
client giving up independently.

## Scale of what remains

The server side of this protocol is large: the executable carries hundreds of
`SerializeAuthorityToClientProperties` / `SerializeAuthorityToAutonomousProperties`
implementations across gameplay components, over `NovaNet::ISerializer` and
`ReplicationBitsetTraverser`. Replying to the handshake is tractable; a
playable server is a substantial project (roadmap Phase 4.2).

## Next step

Reply to the opening frame. Options in order of cost:

1. Find whether the client can be made to log packet contents
   (`NovaNet::PacketHandlerMetrics::DumpPacketMetrics`,
   `SetPacketToStringFunction` are both present). A named packet dump would
   turn this from guesswork into a spec.
2. Static-RE the packet group registered as `PacketGroup Id:0` to recover the
   id-to-struct mapping for message id `5`.
3. Blind-reply experiments against the stub, watching how the client's state
   machine reacts. Cheapest to try, weakest evidence.

## The packet layer: a dump exists, and so does the dispatch table

Chasing option 1 above turned up more than a dump.

**A conditional packet log.** `Debug_DispatchPackets` gates a logger whose
format string names the packet:

```
[Debug_DispatchPackets] %s: Received packet %s
```

It is group-based, not a boolean — the executable carries five messages of the
form *"Can only log <X> if a valid group is set"*, one per channel
(`packets`, `rpcs`, `network properties`, `slice instances`, `slice
properties`). `NovaNet_Packets` appears as a string and is a strong candidate
for the group name to pass. Untested.

There is also an assert that names unknown types, which is useful as a probe:

```
NV_NETWORKASSERT: ClientNetworkAgent DispatchPacket *Unknown* packetType:%u, id:%u ConnectionId:%u
```

**The dispatch table.** `NovaGameClientPackets::DispatchPacket` (rva
`0xe24300`) reads the packet type from the header and switches on it:

```
movzx eax, ax
add   eax, -5          ; ids below 5 are not dispatched here
cmp   eax, 7
ja    <unknown>
jmp   qword ptr [table]  ; jump table at rva 0xe25a04, 8 entries
```

so **packet ids 5..12** are handled. Each case loads **its own name string** for
that log line, so the id-to-name mapping can be read straight out of the case
bodies — nothing is inferred:

| id | name | handler rva |
|---|---|---|
| 5 | **Connect** | `0xe24374` |
| 6 | **Accept** | `0xe2455a` |
| 7 | SyncConsole | `0xe24824` |
| 8 | SyncConnectionCvars | `0xe24a5f` |
| 9 | ServerConsoleCommand | `0xe24c8d` |
| 10 | EntityUpdates | `0xe24e99` |
| 11 | EntityRpcs | `0xe25291` |
| 12 | ClientMigration | `0xe255f7` |

Each case does, e.g. for id 6:

```
0xe245b4 lea rax, [rip + 0x597d035]  ; "Accept"
0xe245cc lea r8,  [rip + 0x597cfbd]  ; "[Debug_DispatchPackets] %s: Received packet %s"
```

**This mapping is now confirmed, and it corrected an earlier guess.** The
enum names were originally recovered with `strings | sort`, which alphabetises
and destroys declaration order; guessing 5..12 in that order got five of the
eight wrong (`SyncConsole` is 7, not 9, and `ClientMigration` is 12, not 7).
The dispatcher's own strings are authoritative; the enum strings are not.

Consequences worth noting:

- The client's opening frame is id 5 = **Connect**, so the server's reply is
  **Accept = id 6**. That is what `server/match_stub.py`'s `accept` reply mode
  sends.
- The client also sends **id 8 = SyncConnectionCvars** during a session, which
  fits: connection cvar sync happens after connect. The server presumably has
  to answer that too.
- `EntityUpdates` (10) and `EntityRpcs` (11) are the gameplay replication
  channels; `ClientMigration` (12) is last, which is why the range ends there.

What is still **not** known is each packet's payload structure. The id alone
identifies the message; the fields inside it do not follow from this table.

`EPackets_START` and `EPackets_MAX` are sentinels, not types.

## What the wire actually is (2026-09-16, evening session)

Client-log line that reframes everything:

```
NovaNet::NovaGameServiceClient::ClientConnect(): User 267778957(3330213815)
  is connecting to the NovaGameHub at: 127.0.0.1:18877
NovaGameHubClient::ClientManager::Connect(): ... Sending connect request ...
```

The stub is the **NovaGameHub**, not the shard. Corrections to the section
above: `nonce mismatch` / `nonce not returned` are OpenSSL timestamp strings,
not the game handshake — false lead. The real nonce flow is hub-level
(`NovaGameHubClient::GetServerShardAddress` returns `ConnectionNonce` +
`PreSharedKey` + `ServerShardAddr` + `GameInstanceId`) and logs under the
`[Debug_Nonce]` tag (`Received connect nonce and pre-shared key`, ...),
now enabled in `user.cfg` via RequiredFilters.

Frame layout (verified: counter runs 00 01 .. 01 84 = 388, heartbeats 1/s,
cvars every ~15s; Connect data carries the NovaNetUserId from the log,
`c67f07b7` = 3330213815):

```
header: u16 BE id, u16 BE body length
body:   01 00 05 00 00 (magic) | u16 BE counter | data
  id  5 data: 01 <userid4> 00      (Connect, once)
  id  8 data: 00 <userid4>         (SyncConnectionCvars, ~every 15 s)
  id 14 data: 01                   (heartbeat, 1/s)
```

Transport model: `Timing out requestid N` (TcpClientManager) tracks the
counter. Timeouts ran 3..78 against counters 01..5e; req 1 never fired because
our Accept echoed counter 01 back (a response = a frame carrying the
request's counter), req 2 is the unreliable cvars frame. Corollaries:

- Probes "triggered heartbeating" because probe payloads reused the client's
  body bytes, counter 01 included — they acked req 1 by accident.
- The old `accept` mode failed on **content**, not transport: Accept
  deserialises as base Packet + one `uint8_t` (disasm of `0xe25eb0`; the byte
  lands at struct+0x608 and is stored to connection+0x408 with no comparison,
  so *any* successfully-deserialised Accept advances — the gate is purely
  deserialisation). Six Connect-payload bytes are not a valid Accept body.
- The client sends Connect exactly once per run and never resends it, so each
  Accept payload variant gets **one shot per game restart**.

`server/match_stub.py` `CRUCIBLE_REPLY=hub` implements this: Connect -> Accept
(id 6, same counter, data from `CRUCIBLE_ACCEPT_DATA`, default `0100`);
heartbeat -> echo iff `CRUCIBLE_HB=echo` (default, tests the counter theory —
timeouts should stop); cvars -> silent iff `CRUCIBLE_CVARS=silent` (default).
Accept fuzz order: `0100`, verbatim Connect data, `00`, `0000`.

## Open items

- **Payload structures, including the nonce.** This is the blocker and it is a
  real reverse-engineering task, not a one-run fix. The id-to-name map is
  settled; every field inside every message is not. Recovery paths, in order:
  the AZ reflection/serialisation data for the packet classes (the dispatcher is
  generated code — `bintemp/gen/novagamecommon/.../NovaGameClient_AutoPacketDispatcher.inl`),
  or disassembling the id-6 deserializer and reading off its field accesses.
- The heartbeat's reply packet id and payload (core packet space).
- Whether `SyncConnectionCvars` (id 8) also requires a reply; the client sends
  it repeatedly, which usually means retrying an unanswered request.
- **`certs/cacert.pem` pak change is unverified.** It is not what fixed this,
  and on the evidence above it should not be needed. Test by restoring
  `gamedata.pak.pre-cacert` and reconnecting; if the handshake still succeeds,
  drop `tools/patch_cacert.py` and keep the trust store untouched.
- Which group name `Debug_DispatchPackets` expects, and whether it is settable
  from `user.cfg` at all. Neither `Crucible.ReportTagRequiredFilters
  +Debug_DispatchPackets` nor `Debug_DispatchPackets 1` produced any packet log,
  and neither produced an error either.
- `PlayerSessionId` is empty in the connect line. May matter for the handshake
  (and for the nonce).
- `EAC Client failed to start` is logged every run. Not currently blocking.

### Revision (run of 17:41, hub v1): server sequence + cumulative ack

v1 hub echoes did **not** stop timeouts (3..129; heartbeat data stuck at `01`
even at client counter 0x91). Revised model: the counter is a **per-sender**
sequence, and server->client data[0] is a **cumulative ack** of the client
sequence. v1 echoes reused client counters (gap at server seq 02, stale ack
01) and were ignored. v2 (`c76a264`): the stub keeps its own server seq (01,
02, ...) and acks each inbound frame (data[0] = inbound counter). Heartbeat
reply is `ack` by default; `CRUCIBLE_ACCEPT_DATA=auto` echoes the Connect's
data bytes verbatim with only the id flipped (never tested — the old accept
mode always cleared data[0]). Fuzz order now: `auto`, `00`, `0000`
(`0100` failed to advance).
