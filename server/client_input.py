"""Decoder for the client's input stream: component 172, RPC 1.

This is the one message the client sends continuously and the server had
never looked at. Every other workstream needs it: hunter bots cannot pursue
a player whose position the server does not know, and creature aggro radii
are meaningless without it.

It is carried on the CONNECTION entity (01000001), not the character, which
is why `shard_stub.observe_client_rpcs` — whose only detailed handling was
for the character — never surfaced it. Delivery is 3 (reliable).

Structure, recovered from `server/shard_udp.log` (see docs/25):

    [0:80]      header and per-frame fields that are not yet identified
    80 + 106*k  packed position of input frame k, k in 0..2, NEWEST FIRST

The three frames are a sliding redundant history: frame k of one datagram is
frame k-1 of the next. That is what proves both the stride and the ordering,
and it means a dropped datagram still delivers its inputs in the next two.

The position uses the same packing as every other transform on this wire —
`hunter_patrol.unpack_position`, i.e. retail packer 4f47e10: X and Y in 22
bits over +/-4500 m, Z in 20 bits over +/-1300 m.

Each position is the front of a full 12-byte packed transform: the 4 bytes
after it are the character's rotation, in the shard's own smallest-three
packing (`shard_stub.pack_upright_rotation`). In both captured payloads the
quaternion's x and y decode to exactly zero, an upright turn about Z. In the
walking one the facing (36.4, 37.2, 37.9 degrees, newest first) follows the
walking direction (35.8). Nothing else in the frame is decoded; the rest is
presumably button/axis input and a client tick id, deliberately left alone
rather than guessed at.
"""
import math

import hunter_patrol

INPUT_COMPONENT = 172
INPUT_RPC = 1
INPUT_DELIVERY = 3

# Fixed layout of the only shape observed carrying positions. 48,183 of the
# 48,458 captured component-172 messages are exactly this size; the other
# shapes (36, 132, 329..356 bytes) do not divide into 106-byte frames and are
# refused rather than guessed at.
INPUT_SIZE = 326
FRAME_STRIDE = 106
FRAME_COUNT = 3
FIRST_POSITION = 80
ROTATION_OFFSET = 8          # the packed rotation follows each 8-byte position


# Each frame's InputComponent block (docs/25): its type byte 0x52, then the
# 3-byte momentaryBits bitset (byte 0 first, least significant bit first),
# three move axes, the camera and latentAction. The Interactor block's type
# byte 0x54 follows 20 bytes after the 0x52, which sits at +57 in frames 0
# and 1 and at +56 in frame 2.
INPUT_BLOCK, INTERACTOR_BLOCK = 0x52, 0x54
INPUT_BLOCK_OFFSETS = (57, 56)
MOMENTARY_MASK = 0x7ffff
# Bit indices as ActionsLibrary's populate routine (0x36fe370) passes them,
# literally and whatever the hero: the client's ClientInputAction ids.
ACTIONS = ('Jump', 'Ability1', 'Ability2', 'Ability3', 'Ability4', 'PrimaryFire',
           'DetectMode', 'Interact', 'Melee', 'UseMedPack', 'Reload', 'AllianceOffer',
           'AllianceAccept', 'AllianceReject', 'AllianceBreak', 'AccessibilityMacro',
           'UiHelp', 'UIMap')
ACTION_BIT = {name: 1 << index for index, name in enumerate(ACTIONS)}


def is_input(message):
    """True for a client input RPC, whatever its shape."""
    return (message.component, message.rpc) == (INPUT_COMPONENT, INPUT_RPC)


def parse_input_positions(data):
    """Positions of the three buffered input frames, newest first.

    Raises ValueError on any shape this decoder has not actually seen, so an
    unknown variant can never be silently read as a position.
    """
    if len(data) != INPUT_SIZE:
        raise ValueError('unsupported client input size %d' % len(data))
    return tuple(
        hunter_patrol.unpack_position(
            data[FIRST_POSITION + FRAME_STRIDE * k:
                 FIRST_POSITION + FRAME_STRIDE * k + 8])
        for k in range(FRAME_COUNT))


def latest_position(data):
    """The newest buffered input frame's position."""
    return parse_input_positions(data)[0]


def unpack_rotation(packed):
    """(w, x, y, z) from 4 bytes of the retail smallest-three packing: the
    largest component's index in the top two bits, the other three in 10 bits
    each (scale 723.37, bias 512), the largest restored as positive."""
    if len(packed) != 4:
        raise ValueError('expected a 4-byte packed rotation')
    value = int.from_bytes(packed, 'big')
    rest = [(((value >> shift) & 0x3ff) - 512) / 723.3699951171875 for shift in (20, 10, 0)]
    quaternion = rest[:]
    quaternion.insert(value >> 30, math.sqrt(max(0., 1. - sum(c * c for c in rest))))
    return tuple(quaternion)


def parse_input_facings(data):
    """Forward vectors (the rotation applied to +Y, Cry's forward) of the
    three buffered input frames, newest first."""
    if len(data) != INPUT_SIZE:
        raise ValueError('unsupported client input size %d' % len(data))
    facings = []
    for k in range(FRAME_COUNT):
        base = FIRST_POSITION + FRAME_STRIDE * k + ROTATION_OFFSET
        w, x, y, z = unpack_rotation(data[base:base + 4])
        facings.append((2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)))
    return tuple(facings)


def parse_input_actions(data):
    """The momentaryBits of the three buffered frames, newest first: each an
    int of ACTION_BIT masks for the actions held in that frame.

    Raises ValueError on a shape this decoder has not seen, or a frame whose
    input and interactor blocks are not where they belong.
    """
    if len(data) != INPUT_SIZE:
        raise ValueError('unsupported client input size %d' % len(data))
    actions = []
    for k in range(FRAME_COUNT):
        for offset in INPUT_BLOCK_OFFSETS:
            at = FRAME_STRIDE * k + offset
            if data[at] == INPUT_BLOCK and data[at + 20] == INTERACTOR_BLOCK:
                actions.append(int.from_bytes(data[at + 1:at + 4], 'little') & MOMENTARY_MASK)
                break
        else:
            raise ValueError('no input block in frame %d' % k)
    return tuple(actions)


def parse_newest_actions(data):
    """The newest frame's momentaryBits from a payload of any length, or None.

    Only the 326-byte shape is fully decoded, but a press may arrive in a
    longer one: the Interactor block (0x54) varies in length, and blocks
    before the Input block float by a byte or two. The Input block itself is
    19 bytes, so its 0x52 still has the 0x54 exactly 20 bytes after it. The
    decoded positions, +57 then +56, are tried first, then the rest of
    +50..+70 of frame 0.
    """
    window = [57, 56] + [at for at in range(50, 71) if at not in (56, 57)]
    for at in window:
        if (at + 20 < len(data) and data[at] == INPUT_BLOCK
                and data[at + 20] == INTERACTOR_BLOCK):
            return int.from_bytes(data[at + 1:at + 4], 'little') & MOMENTARY_MASK
    return None
