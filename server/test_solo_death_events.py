"""Opt-in death records use explicit metadata; baseline health stays unchanged."""
import struct
import unittest
from unittest.mock import patch

import combat
import shard_stub as shard
from practice_population import DUMMY_SHARED_TYPES


def arena(**kwargs):
    result = combat.Combat(**kwargs)
    result.register(0x1000002, DUMMY_SHARED_TYPES, maximum=20., team=1)
    result.register(0x1000003, DUMMY_SHARED_TYPES, maximum=20., team=2,
                    respawn_seconds=1.)
    return result


class DeathIntegrationTests(unittest.TestCase):
    def test_explicit_metadata_repeats_while_dead_then_stops_at_respawn(self):
        match = arena(death_metadata=(7, 49))  # fixture ids, no gameplay meaning
        match.apply_reported_hit(0x1000002, 1, (0x1000003,), now=0.)
        first, = match.build_messages(match.tick(0.))
        again, = match.build_messages(match.tick(0.5))
        self.assertEqual(first, again)
        self.assertEqual(first.payload[-20:-12], struct.pack('>HHI', 7, 49, 0x1000002))
        restored, = match.build_messages(match.tick(1.))
        self.assertEqual(restored, combat.build_health_update(
            0x1000003, DUMMY_SHARED_TYPES, maximum=20., value=20.))

    def test_nonlethal_health_encoding_stays_identical(self):
        match = arena(death_metadata=(7, 49))
        match.entities[0x1000003].health = 40.
        match.entities[0x1000003].maximum = 40.
        match.apply_reported_hit(0x1000002, 1, (0x1000003,), now=0.)
        message, = match.build_messages(match.tick(0.))
        self.assertEqual(message, combat.build_health_update(
            0x1000003, DUMMY_SHARED_TYPES, maximum=40., value=20.))

    def test_dead_shooter_cannot_damage_another_entity(self):
        match = arena()
        match.entities[0x1000002].health = 0.
        self.assertIsNone(match.apply_reported_hit(0x1000002, 1, (0x1000003,), now=0.))
        self.assertEqual(match.health(0x1000003), 20.)

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_AI': '0',
                              'CRUCIBLE_SOLO_MODE': 'practice',
                              'CRUCIBLE_SOLO_DEATH_EVENTS': '1'}, clear=True)
    def test_missing_metadata_is_rejected_without_starting_simulation(self):
        state = {}
        with self.assertRaisesRegex(ValueError, 'explicit.*metadata'):
            shard.start_solo_simulation(state)
        self.assertNotIn('combat', state)

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_AI': '0',
                              'CRUCIBLE_SOLO_MODE': 'practice',
                              'CRUCIBLE_SOLO_DEATH_EVENTS': '1',
                              'CRUCIBLE_SOLO_DEATH_SOURCE_ID': '7',
                              'CRUCIBLE_SOLO_DEATH_ABILITY_ID': '49'}, clear=True)
    def test_explicit_configuration_reaches_combat(self):
        state = {}
        shard.start_solo_simulation(state)
        self.assertEqual(state['combat'].death_metadata, (7, 49))


if __name__ == '__main__':
    unittest.main()
