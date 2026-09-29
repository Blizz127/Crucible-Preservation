"""One explicit HybridProjectileActivations slot in a standalone entity update.

Wire authority: retail serializers 0x9e2b80 / 0x553d450 / 0x357cbf0 and the
43-node shared graph captured in snapshots/gamemode-20260919/live-combat-layout.json.
This is a pure encoder, not a firing policy or a live-accepted projectile path.
Callers must supply source-backed projectile and attachment ids themselves;
there are deliberately no weapon-specific defaults or runtime enable switch.
"""
import math
import struct


PROJECTILE_COMPONENT = 115
ACTIVATION_SLOTS = 10


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')


def activation_traversal_bits(shared_types, slot):
    """Select exactly one complete slot; fixed arrays have no Size decision.

    The captured tree is component -> array -> ten elements, each with three
    atomic children: m_Common, m_Deflect, m_Owner. All other components and
    slots remain absent. There are 15 decisions for component 115 itself.
    """
    _integer(slot, 'slot', 0, ACTIVATION_SLOTS - 1)
    types = tuple(shared_types)
    if (not types or any(type(t) is not int or not 0 <= t <= 255 for t in types)
            or any(a >= b for a, b in zip(types, types[1:]))
            or PROJECTILE_COMPONENT not in types):
        raise ValueError('shared component types must be ascending, unique, and include 115')
    bits = []
    for component in types:
        if component != PROJECTILE_COMPONENT:
            bits.append(False)
            continue
        bits.extend((True, True))  # component and array
        for index in range(ACTIVATION_SLOTS):
            bits.extend((True, True, True, True) if index == slot else (False,))
    return bits


def _position(position, name):
    if not isinstance(position, (tuple, list)) or len(position) != 3:
        raise ValueError(f'{name} must contain exactly three coordinates')
    for value, limit in zip(position, (4500., 4500., 1300.)):
        if (type(value) not in (int, float) or not -limit <= value <= limit
                or not math.isfinite(value)):
            raise ValueError(f'{name} is outside the finite packed-position domain')


def build_hybrid_activation_update(
        entity_id, shared_types, *, slot, allowed_projectile_index, fire_origin,
        ideal_origin, ideal_target_position, relative_time_ms,
        has_first_update_hit, deflected, attachment_id, previous_id=0, local=False):
    """Build one complete slot on the shooter's already-spawned entity.

    relative_time_ms is packet scope time minus creation time, in milliseconds.
    Only valid deltas strictly inside +/-0x7fffffff are supported; the invalid
    sentinel and wrapping/out-of-range time calculations are rejected.

    Attachment is checked for uint16 width only: its identity and suitability
    for this shot remain the caller's responsibility. Likewise, an allowed
    projectile index under 16 satisfies the native range check but still must
    refer to the shooter's authored allowed-projectile array.

    This standalone shape starts with an empty boolean cursor. Combining its
    value bytes with other selected properties would require sharing that
    cursor and is intentionally unsupported here.
    """
    bits = activation_traversal_bits(shared_types, slot)
    _integer(entity_id, 'entity_id', 0, 0xffffffff)
    _integer(previous_id, 'previous_id', 0, 0xfffffe)
    _integer(allowed_projectile_index, 'allowed_projectile_index', 0, 15)
    _integer(attachment_id, 'attachment_id', 0, 0xffff)
    _integer(relative_time_ms, 'relative_time_ms', -0x7ffffffe, 0x7ffffffe)
    for name, value in (('has_first_update_hit', has_first_update_hit),
                        ('deflected', deflected), ('local', local)):
        if type(value) is not bool:
            raise ValueError(f'{name} must be a bool')
    if (entity_id >> 24 != (0xfe if local else 0x01)
            or not 1 <= (entity_id & 0xffffff) <= 0xfffffe):
        raise ValueError('entity id must have a valid counter and match its local category')
    vectors = ((fire_origin, 'fire_origin'), (ideal_origin, 'ideal_origin'),
               (ideal_target_position, 'ideal_target_position'))
    for vector, name in vectors:
        _position(vector, name)

    import shard_stub
    # RelativeGameTime serializer 0x4a13ac0 uses signed zigzag + ULEB128.
    time = shard_stub.encode_varint((relative_time_ms << 1) ^ (relative_time_ms >> 31))
    # Reader 0x4f565d0 retains one bool byte across scalar/object boundaries:
    # HasFirstUpdateHit is bit 0; m_Deflected reuses that byte at bit 1.
    flags = int(has_first_update_hit) | (int(deflected) << 1)
    values = (bytes([allowed_projectile_index])
              + b''.join(shard_stub.pack_practice_spawn_transform(p)[:8]
                         for p, _ in vectors)
              + time + bytes([flags]) + struct.pack('>HI', attachment_id, entity_id))
    return shard_stub.build_entity_message(
        entity_id & 0xffffff,
        shard_stub.pack_type_and_flags(shard_stub.ENTITY_ROLE_CLIENT_SIMULATION),
        payload=shard_stub.build_replication_record(bits) + values,
        previous_id=previous_id, local=local)
