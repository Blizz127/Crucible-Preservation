# A server-driven player respawn: what the client does, and what is unknown

Scoped on 25 October from the client exe, read-only. Nothing is built. The
disassembly dumps behind it are not committed, because they are the client's
code. The addresses are given so it can be re-read. PROVEN means read from the
code; INFERRED is reasoning on top of it.

## The client's side of RespawnComponent (120)

- **RPC 0, `RespawnWaveExpiredRPC`** (server to client, delivery 2).
  - PROVEN: `HandleRpcMessage` (0x9f2270) checks the role byte (2), then
    forwards to the `RespawnComponent::Autonomous` object at
    `[this+0x480]` (vtable 0x6710b48, slot +0xa8 = 0xbcd780).
  - PROVEN: that handler does three things:
    - stores the transform at `comp+0x300` (position) and `+0x310` (the
      quaternion, WXYZ);
    - calls `SetRespawnState` (0x4b649d0) with 3, InitiatingDrop;
    - clears `comp+0x320`.
  - PROVEN: no teleport, no UI change, no control change and no health
    reset.
  - The next client tick (`RespawnSystemGameplayClient::UpdateEntity`,
    0xdeede0, case 3) sets 4, Dropping.
  - PROVEN: the payload is the 12-byte packed transform that Location
    uses: Serialize 0x9f0840, then 0x892de0, then 0x4f467b0, which is
    `pack_practice_spawn_transform`'s format.
- **RPC 1, `RespawnDropPodLanded`**, no fields.
  - PROVEN: the handler (0xbcd880) sets 2, Respawning, whatever the prior
    state.
  - On the next tick (case 2, not tutorial) it copies the ack byte into the
    type-120 input record (the client's input stream) and calls
    `ProcessPlayerRespawn` (0x32e6e10). That posts OnPlayerRespawned, sets
    Alive ("Revived & teleported player") and the wave index to -1.
  - PROVEN: no client code in this flow writes Health.
- **The respawn states** (`comp+0x2f0`, u16, table 0x65bd180): 0 Alive,
  1 RespawnWaveInProgress, 2 Respawning, 3 InitiatingDrop, 4 Dropping,
  5 Dead.
- **RPC 2, `RespawnPointPickedRPC(uint32 respawnPointId)`** is client to
  server, delivery 3. It is sent only from the UI's `respawn.set` (a click
  on the respawn map), only in state 1.
- **Replicated, shared** (7 bits, root plus six, serializer 0x9f2430):
  - `NetworkedRespawnWaveIndex`: int16, value+32768;
  - `NetworkedTutorialRespawnPosition`: 12-byte transform;
  - `NetworkedIsTutorial`: bool;
  - `NetworkedRespawnState`: u16, where 6 and above assert;
  - `NetworkedAcknowledgementId`: u8; a change makes the client adopt the
    networked state;
  - `NetworkedDefaultRespawnPointNetId`: u32.
- **Replicated, autonomous** (3 bits): root, `TimeToDisableAimIK`,
  `TimeToEnableAimIK` (types not decoded).
- **RespawnMatchComponent (121)** has 18 bits: `RespawnWaves`, a fixed
  array of 16 atomic waves. Each wave holds the players respawning, a
  second vector, `m_TimeToRespawn`, `m_MaxNumberOfPlayers` and
  `m_LockTime`. The character slice does not carry it.

## The respawn screen

- PROVEN: `GetLocalCharacterStatus` (0xc8d700) reads the roster's
  CharacterStatus. The values are 0 Unknown, 1 WaitingToDrop, 2 Dropping,
  3 Alive, 4 Dead, 5 WaitingToRespawn, 6 Respawning, 7 Spectator.
- `GetGameplayState` (0xc8de40) maps 5 to Dead (the respawn screen) and 6 to
  Respawning. **4 "Dead" maps to Finished, meaning eliminated.**
- The countdown (0xc8df90) is the RespawnMatchComponent wave's
  `m_TimeToRespawn` minus game time. It reads -1 when there is no 121, or
  when the wave index is outside 0..15. The UI does not read health.

## Verdict

- **Candidate A: RPCs.** The client makes the transitions itself.
  1. Optionally, 120/0 with a 12-byte transform.
  2. 120/1, empty.
  3. Restore the player's health, as the arena's respawn path does.

  An unhandled or malformed RPC is discarded silently, so the crash surface
  is low. But RPCs are one-shot over UDP: a lost 120/1 leaves the player
  unrevived. Re-sending it is safe, apart from a repeated
  OnPlayerRespawned. The teleport is separate (`TeleportedRPC`, 86/0), and
  whether the Marine slice carries 86 is unproven.
- **Candidate B: replication.** It selects the tutorial flag and position,
  state 2 and an incremented ack id, on the player's role-2 entity. That
  puts it in the role-1/role-2 crash class. Other risks:
  - a bit-count mismatch;
  - state 6 or above (an assert);
  - an ack that is not incremented;
  - the tutorial flag firing a tutorial respawn event (0xa4af3c71) whose
    effects are unknown.

## Unknown, in order

1. Whether CharacterComponent (156) is the real alive gate: autonomous
   IsAlive, CurrentDeathTimer, CurrentSpawnTimer (serializers 0xa8c1a0
   and 0xa8c450).
2. Whether the client runs its respawn system for this character at all.
   The gate at 0xdefb20 is undecoded.
3. How to clear the death metadata the client latched (Health records 2
   and 3).
4. Retail's server-side order. HOTH_GUIDE has no RespawnInPlace flag, so
   retail presumably moves the controller to a spectator placeholder about
   2.1 s after death.
5. Which entity carries RespawnMatchComponent (121).
6. The input suppress and un-suppress record.
7. The client's transition log is gated by a global flag (0x7bd4780) with
   no setter found, so a live probe must watch behaviour.
8. Whether TeleportComponent (86) is on the Marine slice.

One retail capture of a death and respawn in HOTH_GUIDE would settle the
order, the roles and the bool layout. None exists.
