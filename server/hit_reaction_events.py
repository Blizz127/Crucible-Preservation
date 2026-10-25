"""Encode explicit HitReactionComponent index and yaw on an existing entity.

Retail authority-to-client serializer 0x943fa0 writes record 1
NetworkedAdditiveHitIndex (call 0x94418d -> 0x79fcd0), then record 2
NetworkedAdditiveHitYaw (call 0x9441c3 -> 0x7f32c0). Both saved live graphs
under snapshots/systems-20260922 contain these two atomic children.

This pure encoder establishes the wire shape only. Index meaning, yaw units,
change detection, repeated-hit policy and visible flinch acceptance remain
outside this helper. It performs no integration or packet transmission.
"""
import math
import struct


HIT_REACTION_COMPONENT = 80


def flinch_yaw(facing, shooter, victim):
    """NetworkedAdditiveHitYaw for a shot from `shooter` to `victim`, or None.

    The client's worker (0xdae770, snapshots/hit-reactions-20260923) uses
    yaw = acos(d . f) * sign(d . (f x Z)) in degrees: f is the victim's
    forward (its rotation applied to +Y, i.e. the facing its Location
    update carried), d the shot's travel direction. 0 is a shot travelling
    the way the victim faces (from behind), +90 one travelling toward its
    right, +-180 one from the front. additive_hit_react.animgraph picks
    hit_flinch_f, r, l or b from that. Horizontal only: the bots stand
    upright. None when shooter and victim coincide.
    """
    dx, dy = victim[0] - shooter[0], victim[1] - shooter[1]
    length, forward = math.hypot(dx, dy), math.hypot(facing[0], facing[1])
    if length < 1e-6 or forward < 1e-6:
        return None
    d = (dx / length, dy / length)
    f = (facing[0] / forward, facing[1] / forward)
    right = (f[1], -f[0])                       # f x Z
    angle = math.degrees(math.acos(max(-1., min(1., d[0] * f[0] + d[1] * f[1]))))
    return angle if d[0] * right[0] + d[1] * right[1] >= 0 else -angle


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')


def hit_reaction_traversal_bits(shared_types):
    """Select the component root and both atomic scalar properties."""
    types = tuple(shared_types)
    if (not types or any(type(t) is not int or not 0 <= t <= 255 for t in types)
            or any(a >= b for a, b in zip(types, types[1:]))
            or HIT_REACTION_COMPONENT not in types):
        raise ValueError('shared component types must be ascending, unique, and include 80')
    return [bit for component in types
            for bit in ((True, True, True) if component == HIT_REACTION_COMPONENT
                        else (False,))]


def build_hit_reaction_update(entity_id, shared_types, *, index, yaw,
                              previous_id=0, local=False):
    """Build one standalone update with a caller-supplied uint8 and float32.

    Index's descriptor 0x6e02810 declares the full 0..255 wire range; fitting
    that range does not establish a meaningful animation index. Yaw is a raw
    finite big-endian float32; no angle unit, wrapping or bounds are inferred.
    There are no serializer bool fields in this payload.
    """
    bits = hit_reaction_traversal_bits(shared_types)
    _integer(entity_id, 'entity_id', 0, 0xffffffff)
    _integer(previous_id, 'previous_id', 0, 0xfffffe)
    _integer(index, 'index', 0, 0xff)
    if type(local) is not bool:
        raise ValueError('local must be a bool')
    if (entity_id >> 24 != (0xfe if local else 0x01)
            or not 1 <= (entity_id & 0xffffff) <= 0xfffffe):
        raise ValueError('entity id must have a valid counter and match its local category')
    if type(yaw) not in (int, float):
        raise ValueError('yaw must be a finite float32 number')
    try:
        if not math.isfinite(yaw):
            raise ValueError('yaw must be a finite float32 number')
        values = struct.pack('>Bf', index, yaw)
    except (OverflowError, struct.error) as error:
        raise ValueError('yaw must fit a finite float32 number') from error

    # Defer the import so shard_stub can consume the helper without a cycle.
    import shard_stub
    return shard_stub.build_entity_message(
        entity_id & 0xffffff,
        shard_stub.pack_type_and_flags(shard_stub.ENTITY_ROLE_CLIENT_SIMULATION),
        payload=shard_stub.build_replication_record(bits) + values,
        previous_id=previous_id, local=local)
