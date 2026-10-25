# The client's input stream: where the player actually is

Every AI workstream ran into the same wall: the server did not know where
the player was. Hunter bots cannot pursue, and creature aggro radii cannot
fire, against a position the shard never receives. `docs/17` had noted the
client is *autonomous* for its character — it never sends a Location update
— and read-only memory observation was the only way the earlier sessions
learned the player had moved (`docs/18`, `live-alpha-entry.json`).

This document records how that was resolved from the existing capture, with
no new launch.

## What the client actually sends

A histogram of every EntityRpc in the last 47,399 type-11 datagrams of
`server/shard_udp.log` (all decode; zero rejected):

| entity | component | rpc | delivery | count | payload sizes |
|---|---|---|---|---|---|
| `01000001` | 172 | 1 | 3 | 48,458 | **326 × 48,183**; 329–356 × 264; 36 × 10; 132, 229 × 1 |
| `01000002` | 115 | 4 | 3 | 40 | 6, 10 |
| `01000001` | 169 | 1 | 3 | 1 | 11 |
| `01000001` | 169 | 2 | 3 | 1 | 0 |

Component 115/RPC 4 is `ClientProjectileResult` (`docs/17`). 169/1 and 169/2
are the player-name and activation RPCs the bootstrap already consumes.
**Component 172 / RPC 1 is everything else the client ever says** — one
message per datagram, on the *connection* entity, reliable delivery. It had
never been decoded because `observe_client_rpcs` only inspected the
connection entity for the two 169 RPCs and passed the rest by.

## Finding the position without the schema

Rather than trace the serializer, the payload was searched for the one
encoding already proven on this wire: the 8-byte packed position from retail
packer `4f47e10` (X, Y in 22 bits over ±4500 m, Z in 20 bits over ±1300 m —
`hunter_patrol.unpack_position`). Sliding an 8-byte window over 400 of the
326-byte messages and keeping only decodes inside April2018's playable box
gave exactly nine offsets that decoded plausibly in **all 400** samples:

```
64, 80, 95      170, 186, 201      275, 292, 307
```

Three groups, stride 106. Three distinct values per group, constant while
the player stood still. Then, sampled every 2,000 messages across the whole
capture:

- **offset 80** walked `(1204.52, 1283.03, 26.86)` → `(1221.94, 1308.39,
  26.01)` → `(1231.54, 1318.22, 29.34)`. That last value is the player
  position `docs/18` recorded from an **independent read-only memory
  observation** during the same Alpha Hunters session: `(1231.541,
  1318.221, 29.339)`. Same session, two unrelated instruments, agreement to
  the packer's resolution.
- offset 64 tracks offset 80 with a metre-scale lateral offset and
  different Z — a camera or aim origin is the likely reading; not decoded.
- offset 95 occasionally decodes Z = 124 m — not a position; not decoded.

## The three frames

In a stretch where the player is walking, consecutive messages read:

```
message i   : frame0 (1204.571, 1283.102)  frame1 (1204.549, 1283.071)  frame2 (1204.532, 1283.048)
message i+1 : frame0 (1204.596, 1283.138)  frame1 (1204.571, 1283.102)  frame2 (1204.549, 1283.071)
message i+2 : frame0 (1204.626, 1283.181)  frame1 (1204.596, 1283.138)  frame2 (1204.571, 1283.102)
```

Frame *k* of one message is frame *k−1* of the next. So the 326-byte
message is a **sliding history of the last three input frames, newest
first**, the standard loss-tolerant input pattern: a dropped datagram's
frames still arrive in the following two. Each step is a few centimetres, one
20 Hz tick of walking, which is what rules out a misread stride. Position
sits at `80 + 106·k`, `k ∈ 0..2`. The 80 leading bytes and the rest of each
frame — presumably tick ids, buttons and axes — are **not decoded**; the
first four bytes of each frame do look like a sequence number (`001225f0 02`,
`… 01`, `… 00`) but that is an observation, not a claim.

## The facing (25 October)

Each frame's position is the front of a full 12-byte packed transform. The
4 bytes after it (`88 + 106·k`) are the character's rotation, in the same
smallest-three packing the shard sends (`pack_upright_rotation`): the
largest component's index in the top two bits, then three 10-bit components
(scale 723.37, bias 512). Three checks against the two committed captures:
- **Upright.** In every frame of both, the quaternion's x and y decode to
  exactly zero: a pure turn about Z, as a character's rotation should be.
- **Walking.** In the walking capture the facing (+Y rotated by it) is
  36.4°, 37.2° and 37.9°, newest first: a slight turn. The walking
  direction over the same three frames is 35.8°.
- **Standing.** In the still capture the facing is −171.8° in all three
  frames.

`client_input.parse_input_facings` decodes it, and the shard keeps the
newest as `state['player_facing']`. Nothing yet uses it on the wire. It is
what a flinch on the player needs: the yaw is measured in the victim's frame
(docs/36).

## The rest of the frame, and the held actions (25 October)

The 326-byte payload is `SendClientInput` (Serialize 0xaca2b0): three
106-byte `InputCommand` frames at 0, 106 and 212, then `StateHash` (u32 BE)
at +318 and `PreviousInputTime` (u32 BE) at +322. Within a frame:
- **+0** `GameTimeMs` (u32 BE), **+4** `ClientFrameOffset` (u8), **+5**
  `ServerFrameMultiplier` (u8, always 2 in the captures).
- **+6..+13** `01 9e 01 01 00 00 02 0a` in every captured frame: read as an
  EntityHierarchy wrapper (one child, entity 0x01000002, ten child inputs).
  That reading is from the data, not a decoded serializer.
- **From +14,** one block per component, sorted by type, each a type byte
  then its data. The types are:
  - 0x06 Ability
  - 0x0a Alliance
  - 0x19 Script
  - 0x3d DesignerEvent
  - 0x52 Input
  - 0x54 Interactor
  - 0x6e PlayerMovement, whose 12-byte transform is at +80
  - 0x78 Respawn
  - 0x87 TargetPoint
  - 0x9e EntityHierarchy
- **The Input block.** Its 0x52 byte is at +57 in frames 0 and 1 and at +56
  in frame 2, with the Interactor's 0x54 exactly 20 bytes after it. It
  holds:
  - `momentaryBits`, 3 bytes;
  - the move axes x, y, z, one byte each, 127 neutral (254 is full
    forward in the walking capture);
  - `cameraPosition`, 8 bytes;
  - `cameraForward`, 4 bytes;
  - `latentAction`, 1 byte.

`momentaryBits` holds the actions held in that frame, bit i in byte i / 8
from the least significant bit. The bit indices are the literals that
`ActionsLibrary`'s populate routine (0x36fe370) passes, the same for every
hero. The serializers are `InputComponent::NetworkInput::Serialize`
(0x3262580) and `momentaryBits` (0x32734e0).

| Bit | Action | Bit | Action |
|---|---|---|---|
| 0 | Jump | 9 | UseMedPack |
| 1 | Ability1 | 10 | Reload |
| 2 | Ability2 | 11 | AllianceOffer |
| 3 | Ability3 | 12 | AllianceAccept |
| 4 | Ability4 | 13 | AllianceReject |
| 5 | PrimaryFire | 14 | AllianceBreak |
| 6 | DetectMode | 15 | AccessibilityMacro |
| 7 | Interact | 16 | UiHelp |
| 8 | Melee | 17 | UIMap |

Checked against the 5 October capture (`server/shard_udp-20261005T0245Z.log.gz`,
not committed; the scripts are in `snapshots/input-actions-20261025/`): 369,439 input messages and 663 accepted hit reports (115/4).
- **Fire.** 95% of the hit reports follow a frame with PrimaryFire set
  within a second, against 3% for a random frame, and the median gap is
  0 ms.
- **Alliance keys.** The alliance bits toggle in the order of their default
  digit bindings.
- **Abilities.** Jump comes with Ability3, and Ability4 (aim) precedes
  PrimaryFire.

Interact itself is seen only once: one 20-message burst from a standing
player. So its bit rests on the static table. One live press of the
Interact key at an interactable would confirm it.

`client_input.parse_input_actions` decodes the three frames'
`momentaryBits` from the 326-byte shape. `parse_newest_actions` reads frame
0's from a payload of any length: the Input block is 19 bytes, so its 0x52
still has the Interactor's 0x54 exactly 20 bytes after it. Latent actions
(pings, emotes, voice lines) have no bit, and the other shapes (0.8% of
messages) are otherwise not decoded.

## Implementation

`server/client_input.py` decodes exactly this shape and nothing else:
`parse_input_positions` refuses any payload that is not 326 bytes, so the
264 variant-size messages and the 36/132/229-byte ones are counted
(`state['input_unknown_shape']`) and dropped, never misread as a position.
`server/test_client_input.py` carries two real payloads from the capture and
asserts the stationary one repeats a single position, the moving one orders
newest-first with centimetre steps, the decoded value matches the `docs/18`
observation, and every other size is refused.

`shard_stub.observe_client_input` records `state['player_position']` from
every input message. It is **unconditional** — no flag — because it reads a
message the client already sends and changes nothing on the wire.

## What it unlocks

- `hunter_ai.Bot.update(now, target, target_position)` now gets a real
  target: with `CRUCIBLE_SOLO_AI=1` the three Alpha Hunters bots pursue the
  player's decoded position over the navmesh instead of walking the
  stateless patrol.
- `creature_ai.Creature.step(now, targets)` gets `{01000002: position}`, so
  Practice spitters wake, aggro and turn to face the player on their
  authored radii.
- `combat.py` needs no position, but distance-based falloff
  (`immediate_damage(distance=…)`) can now be fed the real shooter–target
  distance rather than `None`. That is **not** wired yet: the reported hit
  carries no impact point, and the player position is the frame the client
  *sent*, not where the projectile was fired from. Left as an open item
  rather than approximated.

## Not verified

Nothing here has been observed in a running game since the decoder was
written. The identification rests on (a) the exact match to an independent
live observation and (b) the sliding-frame structure; both are strong, but
the first launch with `CRUCIBLE_SOLO_AI=1` is still the test. The log line
`CLIENT INPUT decoded: player at (…)` is emitted once per connection so it
can be checked against the spawn coordinates immediately.
