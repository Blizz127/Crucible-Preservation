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

so **packet ids 5..12** are handled, which matches the eight packet type names
present in the binary:

| id | handler rva |
|---|---|
| 5 | `0xe24374` |
| 6 | `0xe2455a` |
| 7 | `0xe24824` |
| 8 | `0xe24a5f` |
| 9 | `0xe24c8d` |
| 10 | `0xe24e99` |
| 11 | `0xe25291` |
| 12 | `0xe255f7` |

and the eight types (with their enum constants):

| name | enum |
|---|---|
| Connect | `EPackets_Connect` |
| Accept | `EPackets_Accept` |
| ClientMigration | `EPackets_ClientMigration` |
| SyncConnectionCvars | `EPackets_SyncConnectionCvars` |
| SyncConsole | `EPackets_SyncConsole` |
| ServerConsoleCommand | `EPackets_ServerConsoleCommand` |
| EntityUpdates | `EPackets_EntityUpdates` |
| EntityRpcs | `EPackets_EntityRpcs` |

The client's opening frame carries id **5**. The natural reading is
`Connect` -> server replies `Accept`, but **the id-to-name order is NOT
confirmed** and should not be treated as known:

- the `EPackets_*` strings were recovered with `strings | sort`, which
  alphabetises them, destroying declaration order;
- the name strings have exactly one code reference each, in a per-type static
  initialiser — there is no array of names to index;
- the name strings visible in the blob near the dispatcher (`Connect`,
  `Accept`, `EntityUpdates`, ...) are packed by the linker, not in declaration
  order.

Confirm before building on it: set `Debug_DispatchPackets`, send the client's
frame, and read the name the logger prints.

`EPackets_START` and `EPackets_MAX` are sentinels, not types.

## Open items

- **`certs/cacert.pem` pak change is unverified.** It is not what fixed this,
  and on the evidence above it should not be needed. Test by restoring
  `gamedata.pak.pre-cacert` and reconnecting; if the handshake still succeeds,
  drop `tools/patch_cacert.py` and keep the trust store untouched.
- Which group name `Debug_DispatchPackets` expects (`NovaNet_Packets`?), and
  whether it is settable from `user.cfg` at all.
- The id-to-name mapping for ids 5..12 (see caveat above).
- `PlayerSessionId` is empty in the connect line. May matter for the handshake.
- `EAC Client failed to start` is logged every run. Not currently blocking.
