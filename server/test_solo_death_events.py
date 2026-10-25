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


AUTHORED = {'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_MODE': 'alpha-hunters',
            'CRUCIBLE_SOLO_HERO': 'saboteur', 'CRUCIBLE_SOLO_DEATH_EVENTS': 'authored'}


@patch.object(shard, 'log')
class AuthoredDeathMetadataTests(unittest.TestCase):
    def death_tag(self, state, victim):
        arena = state['combat']
        message, = arena.build_messages([arena.entities[victim]])
        return struct.unpack('>HHI', message.payload[-20:-12])

    @patch.dict('os.environ', AUTHORED, clear=True)
    def test_each_killer_carries_its_own_on_hit_tag(self, _log):
        state = {}
        shard.start_solo_simulation(state)
        arena = state['combat']
        self.assertIsNone(arena.death_metadata)
        for projectile in range(1, 21):
            shard.observe_solo_combat(shard.EntityRpc(
                3, shard.PLAYER_CHARACTER_ID, 115, 4,
                bytes([projectile, 1]) + (0x1000003).to_bytes(4, 'big')), state)
        # Ajonah kills a Marine: Ability (28), saboteur_primary (86).
        self.assertEqual(self.death_tag(state, 0x1000003), (28, 86, 0x1000002))
        while arena.alive(shard.PLAYER_CHARACTER_ID):
            arena.apply_damage(0x1000004, shard.PLAYER_CHARACTER_ID, 20., now=1.)
        # A Marine kills the player: Ability (28), marine_primary (51).
        self.assertEqual(self.death_tag(state, shard.PLAYER_CHARACTER_ID),
                         (28, 51, 0x1000004))

    @patch.dict('os.environ', dict(AUTHORED, CRUCIBLE_SOLO_HERO='duelist'), clear=True)
    def test_an_unread_hero_kills_without_metadata(self, _log):
        state = {}
        shard.start_solo_simulation(state)
        arena = state['combat']
        arena.entities[0x1000003].health = 20.
        arena.apply_damage(shard.PLAYER_CHARACTER_ID, 0x1000003, 20., now=1.)
        message, = arena.build_messages([arena.entities[0x1000003]])
        self.assertEqual(message, combat.build_health_update(
            0x1000003, shard.MARINE_SHARED_TYPES, maximum=800., value=0.))


if __name__ == '__main__':
    unittest.main()
