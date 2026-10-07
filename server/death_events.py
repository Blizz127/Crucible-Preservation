"""Encode the decoded death-metadata surface of CrucibleHealthComponent.

The captured graph and serializers establish the wire shape, not that the
client will visibly die. Runtime acceptance is still required. Metadata is
explicit: a uint16 fitting the wire does not establish a valid enum meaning.
See docs/27-damage-and-death-events.md and live-combat-layout.json.
"""

import math
import struct


HEALTH_NETWORK_TYPE = 79
DEATH_HEALTH_BITS = (
    True,                   # component root
    False,                  # record 1 OutsideCurrentHot
    True,                   # record 2 KilledByDamageSource, atomic >HH
    True,                   # record 3 KillerId, >I
    False,                  # record 4 OutsideCurrentDot
    False,                  # record 5 SelfPersistentHealthChangeRequests
    True, True, False, True, # records 23..26, health value group >fff
)


def _uint(name, value, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an unsigned integer')
    if not 0 <= value <= maximum:
        raise ValueError(f'{name} outside [0, {maximum}]')


def death_traversal_bits(shared_types):
    """Select metadata, killer and health in an ascending component layout.

    Record 2 has no children in the captured graph. Its two serialized enum
    fields therefore contribute no additional traversal decisions.
    """
    types = tuple(shared_types)
    for component in types:
        _uint('component type', component, 0xff)
    if types != tuple(sorted(set(types))):
        raise ValueError('component types must be unique and ascending')
    if HEALTH_NETWORK_TYPE not in types:
        raise ValueError('entity has no Health component to update')
    return [bit for component in types
            for bit in (DEATH_HEALTH_BITS if component == HEALTH_NETWORK_TYPE
                        else (False,))]


def build_death_update(entity_id, shared_types, *, source_name, ability_name,
                       killer_id, maximum, value, minimum=0.0, local=False,
                       previous_id=0, autonomous=False):
    """Return one EntityMessage containing explicit death metadata and health.

    Metadata is encoded by 0x93d520 as two full-range uint16 enums. KillerId
    uses 0x7cb2c0's full-range uint32, followed by 0x35dc910's float triplet.
    All are big endian. No meaning or default is assigned to enum value zero.
    This helper does not send packets or alter the existing health-only path.
    """
    _uint('entity_id', entity_id, 0xffffffff)
    _uint('previous_id', previous_id, 0xffffff)
    _uint('source_name', source_name, 0xffff)
    _uint('ability_name', ability_name, 0xffff)
    _uint('killer_id', killer_id, 0xffffffff)
    bits = death_traversal_bits(shared_types)

    health = (minimum, maximum, value)
    try:
        if any(isinstance(number, bool) or not isinstance(number, (int, float))
               or not math.isfinite(number) for number in health):
            raise ValueError('health must contain finite numbers')
        if not minimum <= value <= maximum:
            raise ValueError('health outside [minimum, maximum]')
        health_bytes = struct.pack('>fff', *health)
    except (OverflowError, struct.error) as error:
        raise ValueError('health must fit finite float32 values') from error

    # shard_stub consumes this helper; defer the import to avoid a cycle.
    import shard_stub
    from combat import autonomous_record, entity_role
    payload = (shard_stub.build_replication_record(bits)
               + autonomous_record(autonomous)
               + struct.pack('>HHI', source_name, ability_name, killer_id)
               + health_bytes)
    return shard_stub.build_entity_message(
        entity_id & 0xffffff,
        shard_stub.pack_type_and_flags(entity_role(autonomous)),
        payload=payload, previous_id=previous_id, local=local)
