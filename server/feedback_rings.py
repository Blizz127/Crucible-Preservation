"""Damage numbers (component 36) and incoming-hit arcs (component 81).

Both live on the player's own character and replicate Authority -> Autonomous
as ring buffers (docs/27 section 9; consumer trace in docs/36). The client
keeps a read cursor starting at 0. Each tick it walks slots cursor+1 ..
LastServerIndex, wrapping at the ring size, and stops only on equality. So:

- the server writes a new entry into slot (LastServerIndex + 1) % size, then
  publishes that slot number as LastServerIndex; the first entry is slot 1;
- LastServerIndex must stay below the ring size. 36's reader masks its cursor
  with & 0x1f (0xdabb56) and 81's with & 0xf (0xdae01d), so a larger value is
  never reached and the client loops forever.

DamageFloaterAutonomousSystem (UpdateEntity 0xdab820) keys entries by target.
While a target's floater lives, which is 224 ms after its last entry
(`common_damagefloaters.floatersettings` DirectAccumulationWindow), a new
entry *replaces* the shown amount. So the server sends a running total per
target. m_Flags bit 0 is crit and bit 1 damage over time.

HitIndicatorAutonomousSystem (UpdateEntity 0xdade60) draws the HUD arc toward
m_IndicatorPosition and the screen-edge effect toward m_ScreenEdgePosition.
Both are world positions; the shard sends the shooter's position. The
client recomputes m_IndicatorScale from damage. HitIndicatorType 2 is
Projectile; HealthChangeType 1 is Direct.
"""
import math
import os
import struct

import hero_loadout

DAMAGE_FLOATER = 36
FLOATER_SLOTS = 32
HIT_INDICATOR = 81
INDICATOR_SLOTS = 16
# DirectAccumulationWindow, assets/player/_common/common_damagefloaters.floatersettings.
FLOATER_ACCUMULATION_SECONDS = 0.224
FLAG_CRIT = 0x01
FLAG_DAMAGE_OVER_TIME = 0x02
INDICATOR_PROJECTILE = 2       # HitIndicatorType (table 0x65c4b70)
HEALTH_CHANGE_DIRECT = 1       # HealthChangeType (table 0x65c4340)

# The character's autonomous roots in ascending network type. These are the
# 25 types with server-to-autonomous bits in the client schema
# (snapshots/gamemode-20260919/component-replication-schema.json) among the
# player character's components in live-character-spawn.json, whose type
# getters are `mov al, type; ret`.
AUTONOMOUS_TYPES = (6, 10, 20, 21, 24, 25, 27, 36, 46, 48, 50, 61, 70, 81, 82,
                    84, 101, 110, 115, 117, 120, 145, 156, 158, 164)
assert len(AUTONOMOUS_TYPES) == hero_loadout.AUTONOMOUS_ROOTS


def damage_numbers_enabled():
    return os.environ.get('CRUCIBLE_SOLO_DAMAGE_NUMBERS') == '1'


def hit_direction_enabled():
    return os.environ.get('CRUCIBLE_SOLO_HIT_DIRECTION') == '1'


def _slots(selected, size):
    if not selected or any(type(s) is not int or not 0 <= s < size for s in selected):
        raise ValueError('ring slots must be within the ring')
    return [index in selected for index in range(size)]


def floater_bits(slots, *, enabled=None):
    """Component 36: root, the 32-entry array, its entries, FloatersEnabled, index.

    With no slots the array and index are skipped (a 0 skips a subtree); a
    FloatersEnabled value of None leaves that record out.
    """
    entries = [True, *_slots(slots, FLOATER_SLOTS)] if slots else [False]
    if not slots and enabled is None:
        raise ValueError('a floater update needs entries or FloatersEnabled')
    return [True, *entries, enabled is not None, bool(slots)]


def indicator_bits(slots):
    """Component 81: root, LastServerIndex, the 16-entry array, its entries."""
    return [True, True, True, *_slots(slots, INDICATOR_SLOTS)]


def autonomous_bits(subtrees):
    """The character's autonomous record with {component: subtree} selected."""
    if not subtrees or set(subtrees) - set(AUTONOMOUS_TYPES):
        raise ValueError('not an autonomous root of the character')
    bits = []
    for root in AUTONOMOUS_TYPES:
        bits.extend(subtrees.get(root, (False,)))
    return bits


def _floater_values(entries, last_index, enabled=None):
    """Serializer 0x86eae0: each selected entry as u32 target, f32 damage,
    u8 flags (0x870d20), then FloatersEnabled as a packed bool (a fresh
    byte, bit 0: no other bool precedes it here), then LastServerIndex u8."""
    if entries and last_index not in entries:
        raise ValueError('LastServerIndex must name a slot this update writes')
    values = b''
    for slot in sorted(entries):
        target, damage, flags = entries[slot]
        if not 0 < target <= 0xffffffff or not math.isfinite(damage) or not 0 <= flags <= 0xff:
            raise ValueError('invalid floater entry')
        values += struct.pack('>IfB', target, damage, flags)
    if enabled is not None:
        values += bytes([int(bool(enabled))])
    return values + (bytes([last_index]) if entries else b'')


def _indicator_values(entries, last_index):
    """Serializer 0x946e50: LastServerIndex as u32 first (0x947013), then each
    selected 36-byte entry (0x3519600): damage, scale, indicator type, damage
    type, indicator and screen-edge positions."""
    if last_index not in entries:
        raise ValueError('LastServerIndex must name a slot this update writes')
    values = struct.pack('>I', last_index)
    for slot in sorted(entries):
        damage, position = entries[slot]
        if not math.isfinite(damage) or len(position) != 3:
            raise ValueError('invalid hit indicator entry')
        values += struct.pack('>ffHH3f3f', damage, 1.0, INDICATOR_PROJECTILE,
                              HEALTH_CHANGE_DIRECT, *position, *position)
    return values


def build_ring_update(*, floaters=None, indicators=None, floaters_enabled=None,
                      previous_id=0):
    """One player message writing either ring or both.

    `floaters` and `indicators` are (entries, last_index). The autonomous
    record selects both components in type order, and their values follow
    in the same order, after an all-false shared record. `floaters_enabled`
    also replicates FloatersEnabled (record 34), answering the client's
    EnableDamageFloaters request.
    """
    import shard_stub
    subtrees, values = {}, b''
    if floaters is not None or floaters_enabled is not None:
        entries, last = floaters if floaters is not None else ({}, 0)
        subtrees[DAMAGE_FLOATER] = floater_bits(set(entries), enabled=floaters_enabled)
        values += _floater_values(entries, last, floaters_enabled)
    if indicators is not None:
        subtrees[HIT_INDICATOR] = indicator_bits(set(indicators[0]))
        values += _indicator_values(*indicators)
    shared = shard_stub.build_replication_record([False] * len(hero_loadout.SHARED_TYPES))
    payload = shared + shard_stub.build_replication_record(autonomous_bits(subtrees)) + values
    return shard_stub.build_entity_message(
        shard_stub.PLAYER_CHARACTER_ID & 0xffffff,
        shard_stub.pack_type_and_flags(shard_stub.ENTITY_ROLE_CLIENT_AUTONOMOUS),
        payload=payload, previous_id=previous_id)


def build_floater_update(entries, last_index, *, previous_id=0):
    return build_ring_update(floaters=(entries, last_index), previous_id=previous_id)


def build_indicator_update(entries, last_index, *, previous_id=0):
    return build_ring_update(indicators=(entries, last_index), previous_id=previous_id)


# Not authored: how long, and how many, recent entries are re-sent so that
# one lost update cannot make the client walk past a slot it never received.
# Re-sending a slot the client already consumed is harmless (it reads only
# slots between its cursor and LastServerIndex).
RESEND_SECONDS = 0.5
RESEND_ENTRIES = 8


class _Ring:
    size = None

    def __init__(self):
        self.last_index = 0
        self.written = 0
        self.recent = {}           # slot -> (entry, written at)
        self.unsent = set()        # slots written but not yet in any update

    def _write(self, entry, now):
        self.last_index = (self.last_index + 1) % self.size
        self.recent[self.last_index] = (entry, now)
        self.unsent.add(self.last_index)
        self.written += 1

    def take(self, now):
        """(entries, last_index) to send this tick, or None.

        Every entry not yet sent, plus re-sends of entries written in the
        last RESEND_SECONDS, newest first, up to RESEND_ENTRIES in all. An
        entry is never dropped before it has been sent once.
        """
        fresh = sorted(((written, slot) for slot, (_, written) in self.recent.items()
                        if now - written <= RESEND_SECONDS), reverse=True)
        keep = {slot for _, slot in fresh[:RESEND_ENTRIES]} | self.unsent
        self.recent = {slot: value for slot, value in self.recent.items() if slot in keep}
        self.unsent = set()
        if self.last_index not in self.recent:
            return None
        return {slot: entry for slot, (entry, _) in self.recent.items()}, self.last_index


class FloaterRing(_Ring):
    """Server side of the 32-slot damage-number ring."""
    size = FLOATER_SLOTS

    def __init__(self):
        super().__init__()
        self.totals = {}           # target -> (running total, time of last hit)

    def hit(self, target, damage, now, *, crit=False):
        total, last = self.totals.get(target, (0., None))
        if last is None or now - last >= FLOATER_ACCUMULATION_SECONDS:
            total = 0.
        total += damage
        self.totals[target] = (total, now)
        self._write((target, total, FLAG_CRIT if crit else 0), now)


class IndicatorRing(_Ring):
    """Server side of the 16-slot incoming-hit ring."""
    size = INDICATOR_SLOTS

    def hit(self, damage, source_position, now):
        self._write((damage, tuple(source_position)), now)
