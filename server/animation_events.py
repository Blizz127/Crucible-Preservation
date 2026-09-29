"""Encode explicit scalar AnimationParameter properties on an existing entity.

Authority: retail shared serializer 0x7e3a10, reached from the captured type-13
vtable 0x66caf20 + 0x80 through 0x7e35a0. The two saved live graphs under
snapshots/systems-20260922 establish 26 property roots and the six children of
AnimationSyncInfo. Parameter registration alone omits that extra root.

This pure standalone encoder assigns no gameplay defaults or timing policy.
It does not establish runtime animation acceptance. Non-scalar properties
remain excluded, and there is no serialized velocity property in this graph.
"""
from collections.abc import Mapping
import math
import struct


ANIMATION_COMPONENT = 13

# (exact serialized name, record index, wire kind), in descriptor-vector order.
# Float wrapper 0x7f32c0 uses serializer +0x28; bool wrapper 0x786620 uses +0x78.
# The direct turn_angle/is_falling members use those same scalar slots.
ANIMATION_PROPERTIES = (
    ('melee_swipe', 1, 'float'), ('emote', 2, 'float'),
    ('is_crouching', 3, 'bool'), ('is_dead', 4, 'bool'),
    ('using_medpack', 5, 'bool'), ('using_quick_melee', 6, 'bool'),
    ('using_primary2', 7, 'bool'), ('using_primary1', 8, 'bool'),
    ('stance', 9, 'float'), ('using_ability3', 10, 'bool'),
    ('using_ability2', 11, 'bool'), ('using_ability1', 12, 'bool'),
    ('is_active', 13, 'bool'), ('is_interacting', 14, 'bool'),
    ('ability_target1', 15, 'vec3'),
    ('time_since_movement_input', 16, 'float'), ('is_aiming', 17, 'bool'),
    ('ammo_ratio', 18, 'float'), ('spawn_ratio', 19, 'float'),
    ('using_aim_ik', 20, 'bool'), ('AnimationSyncInfo', 21, 'sync_info'),
    ('turn_angle', 28, 'float'), ('resource1_ratio', 29, 'float'),
    ('is_falling', 30, 'bool'), ('using_reload', 31, 'bool'),
    ('AimDirection', 32, 'packed_quat'),
)
_SCALAR_NAMES = frozenset(name for name, _, kind in ANIMATION_PROPERTIES
                          if kind in ('float', 'bool'))


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')


def animation_traversal_bits(shared_types, property_names):
    """Select known scalar roots; skip SyncInfo and all six of its children."""
    types = tuple(shared_types)
    if (not types or any(type(t) is not int or not 0 <= t <= 255 for t in types)
            or any(a >= b for a, b in zip(types, types[1:]))
            or ANIMATION_COMPONENT not in types):
        raise ValueError('shared component types must be ascending, unique, and include 13')
    names = tuple(property_names)
    if not names or any(type(name) is not str or name not in _SCALAR_NAMES
                        for name in names):
        raise ValueError('select at least one supported scalar animation property')
    if len(set(names)) != len(names):
        raise ValueError('animation property names must be unique')
    selected = set(names)
    bits = []
    for component in types:
        if component == ANIMATION_COMPONENT:
            bits.append(True)
            bits.extend(name in selected for name, _, _ in ANIMATION_PROPERTIES)
        else:
            bits.append(False)
    return bits


def _float_bytes(name, value):
    if type(value) not in (int, float):
        raise ValueError(f'{name} must be a finite float32 number')
    try:
        if not math.isfinite(value):
            raise ValueError(f'{name} must be a finite float32 number')
        return struct.pack('>f', value)
    except (OverflowError, struct.error) as error:
        raise ValueError(f'{name} must fit a finite float32 number') from error


def build_animation_update(entity_id, shared_types, *, values,
                           previous_id=0, local=False):
    """Build one standalone scalar-only animation EntityMessage.

    `values` maps exact property names to explicit bools or finite float32
    numbers. Mapping insertion order is ignored. Other component roots and
    animation properties stay absent; no animation state is inferred.

    NetworkInputSerializer 0x4f56730 reads big-endian float bits. Its bool reader
    0x4f565d0 retains an LSB-first cursor across intervening scalars: reserve one
    byte at each eighth-bool boundary, then fill it at subsequent bool fields.
    This payload starts with an empty bool cursor and must not be concatenated
    with other property value streams without sharing that cursor.
    """
    if not isinstance(values, Mapping):
        raise ValueError('values must map animation property names to explicit values')
    bits = animation_traversal_bits(shared_types, values)
    _integer(entity_id, 'entity_id', 0, 0xffffffff)
    _integer(previous_id, 'previous_id', 0, 0xfffffe)
    if type(local) is not bool:
        raise ValueError('local must be a bool')
    if (entity_id >> 24 != (0xfe if local else 0x01)
            or not 1 <= (entity_id & 0xffffff) <= 0xfffffe):
        raise ValueError('entity id must have a valid counter and match its local category')

    data = bytearray()
    bool_count = 0
    bool_offset = 0
    for name, _, kind in ANIMATION_PROPERTIES:
        if name not in values:
            continue
        value = values[name]
        if kind == 'float':
            data.extend(_float_bytes(name, value))
        else:  # traversal validation admits only float and bool properties
            if type(value) is not bool:
                raise ValueError(f'{name} must be a bool')
            if bool_count % 8 == 0:
                bool_offset = len(data)
                data.append(0)
            data[bool_offset] |= int(value) << (bool_count % 8)
            bool_count += 1

    # Defer the import because shard_stub may consume this helper.
    import shard_stub
    return shard_stub.build_entity_message(
        entity_id & 0xffffff,
        shard_stub.pack_type_and_flags(shard_stub.ENTITY_ROLE_CLIENT_SIMULATION),
        payload=shard_stub.build_replication_record(bits) + data,
        previous_id=previous_id, local=local)
