# Entity bootstrap audit — 2026-09-19

## Confirmed live state

The user's manual Ready attempt (client log `NovaGameClient-20260919T162557Z.log`)
reached Connected at 16:26:21 GMT. It continued exchanging acknowledged
heartbeats for over eleven minutes. The connection does not require another
player. The connecting UI remains because no game-mode/player bootstrap exists.

The repeated `GameModeComponent was not ready yet` warning comes from
`GameModeUtils::TryGetConfigEntry` at RVA 0x324cd20. Specifically, the component
pointer argument is null (test at 0x324cd35). This is distinct from the later
invalid mode-config asset/index checks. Do not fix it by changing a mode number
or pretending the match has begun.

## Corrected SliceEntryId encoding

The old builder emitted only the four-byte StringAsset hash. The actual
serializer begins at 0x4bfb1d0, but **does not end at the first .pdata record's
end, 0x4bfb255**. The code continues through 0x4bfb61f and serializes another
field at object offset +0x90. Its length header is an on-wire byte, followed
by zero to three index bytes:

| Entry index | Encoding after asset hash |
|---|---|
| -1 (sentinel) | f0 |
| 0..127 | value |
| 128..16383 | 8000 OR value, two bytes big-endian |
| 16384..2097151 | c00000 OR value, three bytes big-endian |
| 2097152..268435455 | e0000000 OR value, four bytes big-endian |

This is not LEB128. The previous eight-byte spawn example in docs/13 is
incomplete. Omitting the entry consumes payload-length bytes as slice metadata.
The builder now requires an explicit entry index whenever a slice is present;
it does not guess index zero or interpret the invalid sentinel as a root entity.

`tools/xref.py --range 0x4bfb1d0:0x4bfb620` can inspect the complete span.
The tool labels .pdata results as unwind fragments rather than complete
functions to avoid repeating the truncation mistake.

## Corrected TypeAndFlags interpretation

The low nibble is a destination **network role**. The retail name dispatcher
at 0x4f4b450 maps 0 InvalidRole, 1 ClientSimulation, 2 ClientAutonomous,
3 ServerProxy, 4 ServerAuthority. The update consumer at 0x4bb484f compares
this value with the entity's current role and changes its role accordingly.
At 0x4bb4861, assigning ServerAuthority outside migration raises an assertion.

The old automatic spawn sweep was removed. Its 6..12 trigger also borrowed the
server-to-client packet table for client-origin packets. It was never proven
as an acceptance detector. No speculative spawn packets were sent in this audit.

## Remaining implementation

A valid entity spawn needs the asset hash **and the corresponding entry within
that slice**, the client network role, and the initial component-property stream.
The update path calls NetSystemComponent property deserialization at 0x4a95660,
which delegates to 0x4a957c0; empty payloads are not established as valid.
The level's hash map includes `slices/crucible/services/matchphasecontroller`
(hash 949147183), but that name alone does not identify the owner of
GameModeComponent or the correct slice entry. Both remain unresolved.

The audit fixes pass four entity-encoding tests (including nine boundary
vectors), seven transport tests, and the hub tests. This is encoding and
source evidence, **not playable-spawn acceptance**. The running shard was
not restarted, preserving the user's connected session. No game launch or
UI control was performed.

## Training Arena observation and asset owner (16:51 GMT)

The user's next manual Training Arena run, log
`NovaGameClient-20260919T165021Z.log`, loaded the level at 16:51:00.867599,
received Accept at 16:51:02.945687 and confirmed fully connected at
16:51:03.731571. Acknowledged heartbeats continued; GameModeComponent remained
missing. The user was told to close after the observation and hold off on a
relaunch. No process/UI manipulation or service restart was performed.

`tools/objectstream.py` now reads binary ObjectStream v3 without modifying the
asset. Format authority is Amazon's
[ObjectStreamImpl::ReadElement](https://github.com/aws/lumberyard/blob/master/dev/Code/Framework/AzCore/AzCore/Serialization/ObjectStream.cpp).
Three synthetic tests cover nested/name/version tags, extended sizes, inline
values, siblings, every truncation of a valid nested stream, invalid flags,
unsupported versions and trailing data. Retail matchcomponents and practicearena
slice streams parsed through their final terminators.

The retail component registration at RVA 0x745a4c calls 0x724810 and assigns
`GameModeComponent::Client`. Its UUID getter copies from 0x77105e0; static
initializer 0xcf0d0 writes UUID **31b5fb02-b797-edab-f41e-e4576e15c5b9** there.
Searching decoded slice types (not names alone) locates this component in:

- `slices/crucible/services/matchcomponents.dynamicslice`: entity `GameMode`,
  asset EntityId 0x000000e30bccdd89, component offset 0x2524. Slice hash in the
  practicearena hash library is 2761235007.
- `levels/practicearena/commondata/level.dynamicslice`: entity `GameMode`,
  asset EntityId 0x000000fa0b16f7d5, entity offset 0x731c9, component offset
  0x73451. Its mode-list asset is
  `design/gamemodes/gamemodes.gamemodeconfiglist`. The entity occupies position
  127 in the serialized 1198-entity array. **This position is not yet a proven
  network slice-entry index.** Level slice hash is 1289380704.

CreateReplicatedIndexedEntity at 0x4bf5120 rejects index -1 and indices outside
its vector at +0xc8..+0xd0, then selects the EntityId at that index before
cloning/remapping. The mapping builder near 0x4bf4dd1 walks an entity vector and
records sequential indices and network-component presence. The connection
between this vector and serialized level ordering still needs tracing, as do
initial property masks and player ownership. No spawn packet has been sent.

Reproduce asset inspection after extraction with `tools/pak.py`:

```
python3 tools/objectstream.py /tmp/practice-level.dynamicslice \
  --component 31b5fb02-b797-edab-f41e-e4576e15c5b9
```

The extracted assets remain outside the repository. The asset EntityIds above
are not server-assigned network EntityIds and must not be placed directly into
spawn messages.

## Source-derived GameMode probe (pending client verification)

The index chain is now traced: CreateSliceData (0x4bea820) calls
AZ::SliceComponent::GetEntities (0x530a110) at 0x4beab9d, then passes that same
vector to the indexing builder 0x4bf4d60. GetEntities appends nested-instance
entities, then the local entities in array order. Both inspected baked slices
have an empty reference list (field CRC 0xafa37bc9), so there are no nested
entities preceding the local array. The resulting vector is stored in SliceData
+0xc8..+0xd0. Practice level index 127 therefore selects GameMode.

The other generated components on this entity are GlobalAccessComponent::Client
(UUID 6e413692-e4c1-d0cd-17a3-2624329a0854) and MatchComponentMarker::Client
(UUID caaebaf3-c5dd-dc4a-8d37-e7640ddc3a07), identified through UUID getters
0x73f460 / 0x735c50 and name registration at 0x746244 / 0x745f9c.

ReplicationBitset::Serialize (0x4a929d0) reads only ServerToClientsRecord for
ClientSimulation role 1. The record serializer (0x4a930f0..0x4a933f8) uses an
unsigned LEB128 bit count, then ceil(count/8) raw bytes. Bitset bits are LSB first.
`net_CompressHeaders` defaults to true (static initializer 0x4d5360 writes 1).
Compressed traversal 0x4be8240 consumes sequential wire bits and expands them
into the schema bitset. False component/array decisions omit their children.

GameMode descriptor setup 0x8e7bc0 declares 14 expanded ServerToClients bits;
other directions have zero. The property serialization path 0x8e8550 visits
ConfigurationIndex, AlliancesEnabled, StaticAllianceMode, TeamInfo (8 entries),
and UsesSequentialGM. A configuration-only update uses six compressed decisions
(component=true, config=true, remaining four=false), yielding `06 03`.
ConfigurationIndex uses full-range signed int16, min=-32768 max=32767
(descriptor at 0x6e02870, reader 0x4f56990). PRACTICE is entry 6 of retail
`design/gamemodes/gamemodes.gamemodeconfiglist`, so its wire value is `80 06`.
The probe's complete property payload is `06 03 80 06`.

`CRUCIBLE_SHARD_BOOTSTRAP=practice-gamemode` enables a **single** EntityUpdates
packet per accepted peer after ten distinct acknowledged heartbeats. It sends
network id 1, ClientSimulation role, practice level slice index 127, and the
configuration-only payload. It preserves the current sequence/ack window.
The default remains disabled. This probe is restricted operationally to the
user's Training Arena run; it is not a general mode selector or a player spawn.
Fifteen server unit tests pass, including opt-in isolation, Accept-ack gating,
duplicate heartbeat rejection, one-shot delivery and the framed payload.
Gameplay acceptance and even this entity's live acceptance remain pending.

### First probe result and RelativeId correction

At 17:08:17 GMT the client dispatched EntityUpdates, then asserted in
EntityCommon.h:79: `Requested Id out of range`. Log
`NovaGameClient-20260919T170757Z.log` is preserved under
`snapshots/gamemode-20260919/`. No GameMode property acceptance occurred.

Tracing the **caller** 0x4b9daa0 exposed another omitted outer transform:
`relative = ((counter - previous_counter) << 1) | kind`, then the already-known
zigzag/LEB128 serializer runs. Kind 0 maps to category 0x01; kind 1 to 0xfe.
The reader arithmetic-shifts the decoded intermediate right once more and adds
the previous counter. The first counter is relative to zero (0x4b9dfd0), and
valid counters are 1..0xfffffe, excluding both zero and 0xffffff.

The old `02` encoded intermediate 1, so the outer reader produced counter 0
and asserted. Counter 1 must be `04`. The builder now includes the outer
transform and supports an explicit previous counter for subsequent messages.
Seventeen tests pass, including source-derived positive/negative deltas,
reserved values, the observed failing 02 byte and the corrected packet.
Only this ID byte changed for the next live probe; property acceptance still
needs observation.

### Offline continuation: remaining arena entities

The baked practicearena list also identifies these service entries by their
component UUIDs and client registry names: MatchPhaseController 123,
MatchComponents 125, GameMode 127, Dialogue 128, PreloadMatchController 129,
DropPodLifetimeMatchController 130, PhysXTriggerEventController 131,
ParticlesCoordinator 132, MatchDataStore 133, AccountProgression 135.
The authored `PlayerStartSpawn` is entry 695 and contains PlayerSpawnPoint,
Location, GlobalAccess and CharacterDropPoint client components.

`slices/novanet/player` (hash 3739129850) is distinct from
`slices/player/hero/hero_character` (hash 501823405): the former includes
PlayerComponent, PlayerNetworkInput and PlayerWasdMovement, while the hero has
Input, Owner/Ownable, CameraData/CameraGameplayData, PlayerMovement and
ServerPlayerState among its gameplay components. PlayerComponent autonomous
activation at 0xc45a30 registers its entity with ClientNetworkAgent. These
findings establish separate player and character responsibilities; merely
spawning the hero is not proof of player possession or playable startup.

The has-slice flag is also traced through HandleEntityUpdateMessage
0x4bb54ba..0x4bb5569: it copies the entry and passes a true initial-update
argument to HandlePropertyChangeMessage. No additional guessed flag is needed
for that initial-update argument. The corrected-ID probe reached a second framing error, documented below.


### 2026-09-19: shared payload framing correction

User-launched runs 17:15:38 and 17:16:23 GMT passed the earlier reserved-ID
assertion and failed at FixedSizeVector.inl:240, requested 0/reserved 0.
Logs are preserved in snapshots/gamemode-20260919. Retail 0x4b9e30f is an exact
matching assertion in EntitiesUpdatedMessage::Serialize: the message has a
nonzero PayloadSize but its shared payload vector is empty.

The full serializer 0x4b9df30..0x4b9e450 reads all message headers first,
then calls 0x4aa8380 for EntityMessagePayload/PackingBuffer. This uses
serializer vtable 0x6d875b0 slot 0x88 -> 0x4f56910, which reads a ranged
unsigned length through 0x4f57140 then copies that many bytes. Capacity 0x1800
means a two-byte big-endian length. The old encoder omitted it and interleaved
headers/properties. Its first property bytes 0603 were interpreted as a buffer
length 1539, exceeding remaining input, leaving the vector empty.

EntityMessage now holds separate header/payload bytes; the packet builder
writes every header, total payload length, then all payloads. Regression tests
cover two entities, an empty packet and the capacity bound. The corrected
single-message tail is `04114cda63607f0004000406038006`. Live acceptance of
this correction and the component properties remains PENDING.


### Live framing/property result and pending activation correction

Run NovaGameClient-20260919T172229Z.log received the corrected packet at
17:22:47.465 GMT and kept exchanging heartbeats for several minutes with no
assertion. Read-only /proc memory inspection (no debugger attach, no writes)
confirmed ClientNetworkAgent's latest server time 2000, a GameModeComponent
client instance with ConfigurationIndex 6, and AZ entity state 2 (initialized).
The five component types match level entry 127. This proves property delivery,
not activation or gameplay. See live-initialized-gamemode.json for scalar
observations; addresses are specific to this process, not reusable constants.

The registry and replication tree contain FE000081 for that entity, whereas
the sent message named 01000001. The slice's existing remap table takes
precedence (4bf5c00..4bf5c78). HandlePropertyChangeMessage enqueues the incoming
ID at 4bb4d71; processing at 4bb2030..4bb20ca looks it up in the replication
tree and skips missing IDs. The queue was empty while the entity remained
initialized. This supports correcting the message ID to the client's observed
baked-level identity, local-kind counter 129 (zigzag/LEB128 86 04).

The opt-in probe now uses that ID. Fresh-launch identity stability and normal
activation remain PENDING; do not infer arbitrary baked IDs from asset IDs or
force the entity state. No live memory values were changed.


### 2026-09-19 17:33 GMT: GameMode activation observed

The fresh user-launched run NovaGameClient-20260919T173321Z.log accepted local
counter 129, activated GameModeComponent at 17:33:42.729 GMT and registered it
with the match-component registry. The prior GameMode-not-ready warning stops.
Read-only inspection confirms AZ state 4 (active), ConfigurationIndex 6,
ClientGameState 8 (Connected) and an empty activation queue. Identity FE000081
was stable across these two launches. Scalar snapshot: live-active-gamemode.json.

User still sees the connecting/loading screen. This is NOT gameplay entry.
The UI invokes matchserver.connect and awaits native completion. Its endpoint
constructor is 0x64aad0 (vtable 0x668b570), OpenConnection 0xcbb3b0,
connection-status polling 0xcbbf20. Further player/match bootstrap is required;
no local-player entity has been sent. Preserve the proven GameMode path while
tracing normal PlayerComponent autonomous activation and match readiness.
## Local-player schema investigation after the active GameMode run

The user reported the client closed after the 17:33 GMT observation. No player
spawn has been sent. GameMode activation remains the latest successful runtime
gate; the loading screen and null local-player handle remain unresolved.

`PlayerComponent::Autonomous::OnActivate` at RVA 0xc45a30 copies its entity
handle into ClientNetworkAgent+0x4112728 at 0xc45a86, then sends
`PlayerComponent::SetPlayerName` and logs `Player Entity activated`. This gives
the next observable gate; a successful transport connection alone does not
establish a local player.

The installed `slices/novanet/player` asset contains PlayerComponent UUID
009f4241-8593-8ba6-e3d7-ab2d5e14f31a. Its generated authority-to-client serializer
at 0xac4920 visits UserId and Name. The full slice also contains other replicated
components, so those two fields alone are not a valid entity payload. The role-2
record selector at 0x4a929d0 requires both ServerToClientsRecord and
ServerToClientAutonomousRecord.

Component order is established by NetSystemComponent setup at
0x4a950d0..0x4a953a6: it inserts network components into a tree keyed by the
one-byte type returned by vtable+0x70, then traverses that tree into the
component vector at +0x1528. Use ascending network type order, not serialized
asset component order. NetworkAttach at 0x4a967e0 records the per-component
direction offsets in +0x1568. Compressed traversal at 0x4be8240 consumes a bit
for each visited node and invalidates itself if it exhausts the bitset; padding
an unknown schema to an arbitrary large count is not a format derivation.

PlayerComponent's NetworkAttach at 0xac3f50 locates its descriptor at
EntityManager+0x6a0+0x2760+type*0x1d8. Descriptor+0x150 has four u16 expanded
direction counts. Player type is 169 (getter 0xac2e40); GameMode is 58. The native
type-name dispatcher at 0x4ba1d00 uses the 180-entry RVA table at 0x4ba22cc.

`tools/inspect_replication_schema.py` reads only these schema counts from a
user-launched client and emits names, IDs and counts. It uses O_RDONLY on
/proc/PID/mem, checks the executable table and EntityManager vtable, and rejects
an uninitialized schema. The static dispatcher parsed successfully and the
closed-client check rejected cleanly. **Live descriptor offsets/counts are
PENDING verification.** The user was asked to launch normally and stay at the
main menu without pressing Ready for that observation. No service restart or
new player bootstrap was performed in this investigation.

## Live schema and dedicated client entity (17:52 GMT)

The next user-launched run, NovaGameClient-20260919T175140Z.log (PID 490399),
activated GameMode at 17:51:59.295513 GMT. The read-only schema inspector passed
its runtime guards and captured all 180 component descriptors in
`snapshots/gamemode-20260919/component-replication-schema.json`. This verifies
its EntityManager/descriptor offsets; the earlier PENDING schema gate is now
satisfied. It does not prove player activation.

An exact UUID scan of installed misc.pak dynamicslices found PlayerComponent
in TWO assets: slices/novanet/player and
**slices/characters/compositecliententity**. The latter contains the dedicated
ClientConnection, LoadoutSetup, MatchDataReceiver, CruciComms and PlayerStats
components. Use this candidate for the Crucible player bootstrap rather than
assuming the generic NovaNet character is the game's connection entity. The
practice hash library identifies it as 646954285; the slice's direct Entities
array contains one entry, index 0, named CompositeClientEntity.

Nonempty replication directions, ordered by native component type ID:

| Component | Type | Shared expanded bits | Autonomous expanded bits |
|---|---:|---:|---:|
| CruciComms | 32 | 0 | 20 |
| EntityFlags | 44 | 2 | 0 |
| LoadoutSetup | 85 | 28 | 18 |
| MatchDataReceiver | 88 | 2 | 19 |
| PlayerStats | 111 | 0 | 2 |
| Location | 145 | 6 | 2 |
| Debug | 157 | 7 | 0 |
| EntityHierarchy | 158 | 20 | 3 |
| OctreeNode | 165 | 3 | 0 |
| Player | 169 | 3 | 0 |

Thus keeping all defaults except Player.UserId yields compressed shared
traversal 10 bits (seven false component roots, Player root true, UserId true,
Name false), then six false autonomous roots. Both records precede values:
`0a 80 01 06 00 | uint32 BE UserId`. The uint32 range descriptor at RVA
0x6e02b70 is [0, 4294967295], used by 0x7cb2c0.

`build_composite_client_probe(user_id, previous_id=...)` constructs that
candidate with network counter 1, autonomous role 2 and slice entry 0. It
requires an explicit nonzero session UserId; it is **not called by the active
bootstrap**. Two new tests failed before its implementation and passed after;
all 22 server unittests, the standalone hub protocol tests and 3 ObjectStream
tests pass. No player packet has been transmitted. Initialization callbacks,
source of per-connection identity, and live activation are still unresolved.

The shard Connect payload serializer 0x4b9ed30..0x4b9f094 contains protocol
hashes/version, DebugComponentsEnabled, AutonomousEntityIds, spawn-transform
fields and build IDs; it does not expose the hub's UserId directly. Do not
search arbitrary payload offsets or hard-code a previous session's UserId.
The existing hub already parses the current UserId on Connect/Heartbeat;
server-side identity handoff needs to be connected before enabling this probe.
The user was asked to close the client after the schema capture. No service
restart was performed.

## Local identity handoff prepared (18:11 GMT)

The user acknowledged closing the client; /proc maps confirmed it absent before
service changes. The hub and shard now share an opt-in local lease directory
under /run/user/UID/crucible-revival-sessions, configured through
`local-session.conf` in each user service's drop-in directory. This is a
single-loopback-client development handoff, not retail nonce authentication.

Each hub TLS connection gets its own random lease key. Connect establishes the
UserId; GetServerShardAddress makes it eligible for the shard. Subsequent traffic
refreshes the lease, mismatched heartbeat identities invalidate it, and closing
the TLS connection removes it. Atomic file replacement prevents partial reads.
The shard requires exactly one matching loopback lease no older than 60 seconds;
missing, corrupt, future-dated or ambiguous identity does not send the probe.
No previous session UserId is hard-coded.

The `practice-client` bootstrap waits for Accept acknowledgement and ten unique
heartbeats, then sends the verified GameMode message followed by the candidate
CompositeClientEntity message. Relative counter 1 after baked counter 129 uses
`ff03`, autonomous role + slice flag is `12`; both headers precede the shared
13-byte property buffer. The candidate changes only Player.UserId and retains
other component defaults. It is not a hero, match-phase or gameplay simulation.

New tests cover lease expiry/cleanup, competing and nonlocal sessions, corrupt
records, hub per-connection identity establishment, mismatched heartbeat
invalidation, and bootstrap waiting for identity before packing both entities.
All 27 server unittests and the standalone hub protocol checks pass. The hub and
shard restarted successfully with the client closed; arena-probe.conf now sets
CRUCIBLE_SHARD_BOOTSTRAP=practice-client. Runtime player activation remains
PENDING the user's next manual Ready attempt. No executable/pak/UI mutation,
process launch, commit or push was performed.

To return to the previously observed GameMode-only probe, with the client
closed set arena-probe.conf back to practice-gamemode and restart the shard.
The local-session drop-ins can remain inert for that probe or be removed.

## Hub disconnect race observed and corrected (18:19–18:22 GMT)

The user-launched run NovaGameClient-20260919T181854Z.log reached the shard at
18:19:13 GMT. The hub logged clean EOF at 18:19:12.771, immediately after its
shard-address handoff. The shard's bootstrap at 18:19:14.816 waited for identity:
the hub's unconditional disconnect cleanup had already deleted its lease.
Neither GameMode nor CompositeClientEntity was sent in this run. A successful
schema read from PID 651162 confirmed the client remained inspectable.

The handoff now retains an advertised lease after hub disconnect, without
refreshing its timestamp or extending its original 60-second expiry. Shard
bootstrap atomically claims the unique eligible lease once. A claimed-file
marker and a shared file lock stop concurrent or late hub refreshes from
republishing the same lease. Unadvertised/invalid sessions still clean up.
This supersedes the previous unconditional close-removes-lease behavior.

A regression test models Connect, GetServerShardAddress, finish_local_session,
one successful shard claim, a rejected second claim and a late publish that
cannot resurrect the claim. All 28 server unittests and standalone hub protocol
checks pass. After the user explicitly reported Closed, /proc confirmed no
client image and both services restarted successfully. Player activation is
still PENDING the next manual Training Arena Ready run.


## Local player activated; solo phase probe (18:24–18:39 GMT)

The user-launched NovaGameClient-20260919T182352Z.log successfully activated
GameMode at 18:24:12.657634 and Player at 18:24:12.657823, network ID 01000001.
The player's UserId matched the hub identity. Filtered log evidence is in
snapshots/gamemode-20260919/player-activation-1824.txt; live-active-player.json
records Connected game state 8, connection status 4, active local-player
entity state 4, and a nonnull registered player handle. No assertions occurred.
The user still reported connecting/loading, then explicitly closed the game.
This is player activation, NOT playable arena acceptance.

The user explicitly requires solo startup: no second player, lobby population,
or multiplayer countdown may gate starting this local Practice session.
The installed design/gamemodes/practice.gamemodeconfig defines WaitForPlayers,
GamePlay, GameOver, Shutdown and includes AutoSkipPhaseToGameplay and
DoNotSpawnDropPod. Its normal selection phases are absent.

Client packet 11 is NovaGameServerPackets::EntityRpcs (getter 1a97350).
Vector serializer 4ba16f0 bounds count at 2048; 4aa80c0 encodes delivery u8
(range 0..4), entity u32 BE, component u8, RPC u8, then PackingBuffer's u16 BE
length and data. In the 18:24 capture, RPCs were Player 169/1 (SetPlayerName),
169/2 (AcknowledgeControlledEntity), and PlayerNetworkInput 172/1 (36 bytes).
Both Player RPCs are emitted by OnActivate c45a30; its call ac32e0 emits 169/2
with empty data. This acknowledgement is NOT proof that a hero exists.

The shard previously replied only to Connect/heartbeat, leaving RPC traffic
unacknowledged while it replaced heartbeats. The new parser validates complete
batches and bounds, counts metadata without logging player names, and handles
reliable-sequence duplicates independently of packet-sequence retries. Valid
transport packets 11 now receive type-2 transport acknowledgements.

New opt-in practice-solo mode preserves the verified GameMode/CompositeClient
bootstrap and sends a MatchPhase update after this peer's own 169/2 activation
request. It never consults another peer. The candidate is baked Practice entry
123, local counter 125 (entry + 2), role ClientSimulation, CurrentPhase=8.
Its source-derived compressed record is 04 05 followed by BE u16 0008;
96dccc/42c3990 establish field order and enum width. Runtime identity and phase
activation still require fresh observation. TimeRemaining stays at authored
constructor default -1; no UI/memory flags are written.

Retail UI GetLocalCharacterStatus c8d700 additionally needs MatchDataReceiver and
DataStoreMatch records; GetCurrentState c8d970 branches on MatchPhase+130,
and GamePlay delegates to c8de40. Sending phase 8 cannot by itself establish
hero possession, character readiness or gameplay. Those remain UNRESOLVED.
Rollback mode is practice-client. The game remains user-launched/user-closed.


## One-player GamePlay phase verified (18:40 GMT)

User-launched NovaGameClient-20260919T184014Z.log activated GameMode and the
local player at 18:40:33.343, then MatchPhase at 18:40:33.350782 with ID
FE00007D. Source-derived entry 123/counter 125 was correct. Read-only snapshot
live-solo-phase.json confirms its AZ state 4 and CurrentPhase=8. The shard
sent this immediately after the sole player's activation RPC, with no other
participant or waiting-for-population logic. Filtered logs: solo-phase-1840.txt.

The local MatchDataReceiver component (type 88, primary vtable 66ff060) still
has MyId=-1 at component+f0. DataStoreMatch is absent from the replication tree.
UI c8d700 queries MatchDataReceiver, not LoadoutSetup, through UUID global
7714250 ({DF70423A-B218-9251-AFA5-3D6CAA1E0059}). Its MyId is needed to find
PlayerList[MyId-1] in DataStoreMatch; lookup 4b78940 returns invalid for absent
slots, so assigning MyId alone cannot establish character readiness.
The user reported connecting and then explicitly closed the game again.

Next bounded mode practice-roster activates baked MatchDataStore entry 133,
local counter 135, with two false shared roots (DataStoreMatch 37, EntityFlags
44). It assigns MatchDataReceiver.MyId=1 to the existing local player, using
signed int8 value-minus-minimum 81 (serializer 89f740, range at 6e027f0).
It does NOT yet populate the PlayerList or declare the character Alive.
New read-only tool inspect_match_startup.py will collect the live store graph
needed to encode PlayerList's nested traversal rather than guessing its bits.
PlayerData serializer 4ab8110 names PlayerId, GameSparksId, DisplayName,
NameService, CharacterClass, TeamId, PartyId, DropMaster, RespawnWaveIndex,
CharacterStatus. Its vector serializer 888c30 bounds the roster at 16 entries.
The local mode will populate exactly one. Roster/hero acceptance remains pending.


## Match store graph captured; int8 correction (18:49–18:55 GMT)

NovaGameClient-20260919T184904Z.log activated the baked store FE000087, slice
entry 133. Read-only live-match-store.json captures the 605-node shared graph
and all 13 property record indices: 1,35,61,62,72,73,538,556,557,569,579,580,587.
PlayerList root is bit 538, Size bit 539, entry 0 bit 540; entry nodes are
atomic (child node 0), so PlayerData's members do not consume more record bits.
With one element, later element nodes are not visited. NumPlayersInMatch is
bit 579. The source-derived compressed record selecting just that list/entry
and total count is 11 81 23 00, followed by size 01, PlayerData, BE u32 count 1.
The store and local player are active; the empty roster size was observed as 0.

The prior candidate MyId byte 81 was wrong: live MyId became -127. Native
reader vtable 6d875b0+68 resolves to 4f56640, which copies the byte directly
and ignores the signed range descriptor. Wider signed types use ranged
readers, but int8 does not. Correct slot 1 is byte 01. A regression test first
failed against 81 and now expects 01. Earlier documentation's int8 bias claim
is superseded by this live and static evidence.

The roster candidate contains one local-hunter backend identity, the name
received in the client's validated 169/1 RPC, slot/team/party 1 and the first
player as drop master. CharacterClass and CharacterStatus remain Unknown (0),
not an invented spawned/Alive character. The full PlayerData field order is
4ab8110; strings are bounded 127-byte buffers (8557b0). Descriptor table
74806c0 bounds NameService 0..7, team/party signed int8, RespawnWaveIndex signed
int16, and CharacterStatus 0..8. Respawn index -1 is 7fff via the wider ranged
reader. Thirty-eight server tests pass. Live populated-roster acceptance and
hero startup remain PENDING. No service restart while the game is open.

## One-player roster accepted; character handoff pending (18:58 GMT)

Read-only live-one-player-roster.json confirms all four bootstrap entities are
Active (4), MatchPhase is GamePlay (8), MyId is correctly 1, and PlayerList has
exactly one entry with id/team 1. CharacterClass/Status remain Unknown (0).
The user still sees connecting/loading. This verifies roster decoding, not
playable gameplay. live-control-handoff.json also captures connection
EntityHierarchy's three-node autonomous graph and empty FollowingEntityId /
ControllingEntityId fields (component +1f8/+1fc). No process writes were used.

## Bounded native character spawn candidate (19:10 GMT)

New opt-in practice-character includes the verified roster/solo phase steps,
then emits one autonomous Marine character (slice hash 9256072, entry 0,
ordinary counter 2 / full ID 01000002). The installed marine_character asset
contains 33 nonempty shared directions and 25 autonomous directions. The
candidate changes only Location.InternalTransform and EntityHierarchy's
ParentEntityId (01000001, the existing connection); other properties retain
asset defaults. It does not declare the character Alive or the game playable.

Practice's authored PlayerStartSpawn, direct baked entry 695, has position
(501.09075927734375, 564.5346069335938, 45.27315902709961) and identity rotation.
This is initial spawning at the authored start, not an acceptance teleport.
Location serializer 4f467b0 uses packed position u64 then packed quaternion
u32. Position pack/unpack 4f47e10/4f47d10 uses 22/22/20 bits and live extents
4500/4500/1300. Import 6244a90 is round; quaternion pack 4f45700 uses roundf,
largest-component index and smallest-three quantization. Transform roundtrip
is tested within 3mm, quaternion within unit-rotation quantization error.
Hierarchy parent wrapper 9007a0 -> 901bd0 serializes one full BE u32.

Hierarchy autonomous OnActivate c3c940 registers its own entity in the local
entity slot selected by the asset's hierarchy kind (+420). The Marine asset
specifies CharacterEntity. Its parent lookup also registers the character in
the connection's hierarchy. ControllingEntityId's callback c3d170 copies the
property into autonomous +10 and resets +18; it is not itself the local UI
character-slot writer. This supersedes the earlier tentative handoff theory.

Forty-one server tests pass, including spawn framing, transform roundtrip,
strict mode/startup gating and exactly-once emission. Candidate is NOT yet
runtime verified. Health, ownership, character roster linkage, input
simulation/acknowledgement and actual possession remain to be verified.

## Native Marine activation verified (19:18 GMT)

NovaGameClient-20260919T191801Z.log and live-character-spawn.json establish
Marine network ID 01000002, slice hash 9256072/entry 0, active state 4, and
local-character slot 1 registered to that entity by the native activation
callback. Location authoritative position is (501.09149,564.53540,45.27296),
within quantization tolerance of the authored start. Quaternion is near
identity (this initial interpretation was wrong; see the orientation
correction below). Health resource has minimum 0, maximum 800 and current value 800:
component +1ad0 Resource, +58 DefaultResource pointer, +c/+10/+14 min/max/value
(8c3780 -> 34cbea0 -> 35dc910). No health values were forced. The inspector
now skips vtable entries that are not executable image functions; character
slices include ordinary components without network type getters.

The user still saw connecting. CurrentPhase=8 and the character are valid,
but PlayerList CharacterStatus/Class and LoadoutSetup.Character remain 0;
connection ChildEntityIds is also empty. Native c8d700 reads roster status,
and c8de40 maps status 3 Alive to gameplay client state 10. Status enum table
4b7f114 confirms Alive=3. This is missing authoritative roster state for an
actually spawned, healthy character, not evidence of a missing second player.

Next candidate retains practice-character and, after the client acknowledges
delivery of its spawn packet, sends roster class Marine=8/status Alive=3,
CharacterIds[0]=01000002, connection LoadoutSetup.Character=8 and connection
Hierarchy.ChildEntityIds=[01000002]. Child entries feed autonomous prediction
in c3c630. CharacterIds serializer 883d00 -> 8890c0 is a fixed 16-entry array,
so it has no size payload; graph bits 587/588 select array and first element.
Cross-direction values follow component order: loadout 85 before hierarchy
158. Forty-four tests pass, including withheld/stale ACK and exactly-once
gating. Transport delivery is explicitly not activation or gameplay proof.
Input simulation, ownership behavior and visible arena control remain pending.

## Human-visible arena entry achieved (19:46 GMT)

User-launched test after the character-link change: the user reports
"its ing ame! middle of somewhere but tis in!". This is the first confirmed
visible transition out of connecting/loading into the arena with one player.
Read-only live-character-links.json independently confirms PlayerList size 1,
MyId 1, class Marine 8, status Alive 3, CharacterIds[0]=01000002, loadout 8,
connection child list [01000002], all five replicated entities Active (4),
phase GamePlay (8), and Marine health 800/800 at the authored PlayerStartSpawn.

No other player joined or was required. No client memory, executable, asset,
UI state or position was forced. All changes were local shard replication.
Human movement/camera/jump acceptance is still pending. Server simulation,
input acknowledgement and broader game mechanics are not certified by arena
entry; this remains the opt-in practice-character preservation scaffold.

## Arena visible, control absent; target assignment candidate (19:48 GMT)

The user confirmed "Arena visible, no control" / "No control" after trying
mouse, WASD and jump. The client log records Connecting -> MainMenu -> Playing
at 19:46:07.140285 GMT, so visible entry is established but playable acceptance
is explicitly NOT met. Both connection FollowingEntityId and ControllingEntityId
remain zero in live-character-links.json.

The next candidate fills these two connection autonomous Hierarchy properties
with the already spawned Marine's network ID 01000002. The captured graph has
root + two atomic fields; serializers a97090/a970ba use 7cb2c0 full BE u32.
This expands connection autonomous traversal from 14 to 16 bits (1006e0).
Hierarchy values follow its shared child list, after lower-ID LoadoutSetup.
A regression expectation failed with the omitted targets, then all 44 tests
passed with the targets present. Effect on camera/input is still unverified;
no client UI or memory changes were made. User was asked to close before restart.

## Control targets accepted; movement observed (19 September, latest launch)

Read-only live-control-targets.json, PID 1093066, confirms connection
FollowingEntityId=ControllingEntityId=01000002, with the roster, health and
local character registration retained. Character position changed naturally
from the authored start to (501.49060,576.72980,45.38206), with a changed
rotation. No follow-up position packet or process write was sent: this is
client-side movement processing after the control target assignment.
Human camera/walking/jump acceptance is pending; movement observation alone
does not certify server simulation, combat or persistence.

## Movement stall diagnosed: frozen server clock / exhausted input offset

The user could initially move/jump, then ran in place and could no longer move.
Read-only live-stalled-clock.json confirms last server game time=2000,
PlayerNetworkInput autonomous InputId server time=2000, multiplier=2,
client offset=254. The native input autonomous pointer is component +c8
(acb3f2); +d0 is NetSystem, not the autonomous object. Inspector corrected
before accepting the diagnostic values. ServerPlayerState time remains 0.

PlayerNetworkInput Update c4b0bd/c4b615 reads agent +4112610 (last received
server time). c4b671..c4b698 increments the one-byte offset but saturates at
254 and skips new input once that value is reached. The stub only sent time
2000 in startup messages, then transport heartbeats with no game-time updates.
The unchanged character position and exhausted offset support a prediction
clock stall, not a demonstrated collision failure.

Fix candidate: after character links, seed a monotonic clock at 2000 ms and
send empty EntityUpdates (zero messages, zero packed bytes) at up to 20 Hz.
The socket timeout pumps the clock independently of input, avoiding deadlock
once the client stops sending input RPCs. Clock sends stop after valid peer
traffic has been absent for the configured idle timeout. This does not write
positions, widen prediction limits or fabricate LastProcessedGameTime ACKs.
Forty-seven server tests pass, including advancement beyond the old freeze,
rate limiting, independent timer sends, idle expiry, startup/mode gates and
sequence wrap. Sustained human movement acceptance is still pending.

## Solo entry and sustained movement accepted

After the monotonic clock fix was installed and the user manually relaunched,
the requested 30-second walking/turning/jumping test received the user reply
"works". live-solo-working.json independently records server and input-ID
time both at 80655 ms, input offset 0 (no saturation), exactly one roster
entry, and a naturally moved character at (522.95685,640.73804,37.67065).

Acceptance for this task: Ready -> visible solo Training Arena -> working
camera/control and sustained movement, with no other player required. This
is achieved. Current installed mode is practice-character; backend, hub and
shard user services remain running. The user need not close the working game.
No commit or push was made. Broader combat, AI, multiplayer, persistence and
authoritative server simulation are outside this acceptance and remain
unfinished/unverified. Last server suite: 47 passing tests; diff whitespace
check passes. README and state-of-play now lead with this current result.

## Quaternion interpretation correction during NPC work

The earlier identity-rotation claim above was incorrect. CryQuat component
order is WXYZ, so largest component 0 encodes identity; component 3 encodes
180 degrees around Z. Player input subsequently replaced the bad initial
rotation, masking the mistake during movement acceptance. NPC spawning
exposed the error because its rotations stayed fixed. Initial player
rotation now uses component 0; details and pending live acceptance are in
`17-solo-ai.md`. Authored positions and the accepted movement clock remain
unchanged.
