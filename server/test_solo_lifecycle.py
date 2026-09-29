"""Combat life state gates AI without letting paused clocks jump on revival."""
import unittest
from unittest.mock import patch

import combat
import hunter_ai
import shard_stub as shard


class SoloLifecycleTests(unittest.TestCase):
    def test_inactive_bot_does_not_move_or_fire_and_resumes_without_catchup(self):
        bot = hunter_ai.Bot(0x1000003, ((0., 0., 0.), (0., 100., 0.)), hunter_ai.MARINE)
        squad = hunter_ai.Squad([bot])
        squad.update(0.)
        self.assertEqual(squad.update(100., active_entities=set()), [])
        self.assertEqual(bot.position, (0., 0., 0.))
        step, = squad.update(100.1, active_entities={bot.entity})
        self.assertAlmostEqual(step.position[1], hunter_ai.MARINE.patrol_speed * .1)

    @patch.object(shard, 'log')
    @patch('hunter_patrol.ground', side_effect=lambda p, **kwargs: p)
    def test_dead_hunter_and_player_are_excluded_from_simulation(self, _ground, _log):
        bot = hunter_ai.Bot(0x1000003, ((0., 0., 0.), (0., 100., 0.)), hunter_ai.MARINE)
        match = combat.Combat()
        for entity, team in ((bot.entity, 2), (shard.PLAYER_CHARACTER_ID, 1)):
            match.register(entity, shard.MARINE_SHARED_TYPES, maximum=800., team=team)
            match.entities[entity].health = 0.
        state = {'combat': match, 'squad': hunter_ai.Squad([bot]),
                 'player_position': (0., 5., 0.)}
        self.assertEqual(shard.solo_simulation_messages(state, 0.), [])
        self.assertEqual(shard.solo_simulation_messages(state, .3), [])
        self.assertIsNone(bot.attack_started)
        match.entities[bot.entity].health = 800.
        shard.solo_simulation_messages(state, .4)
        self.assertIsNone(bot.target)  # player is still dead

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_AI': '1',
                              'CRUCIBLE_SOLO_MODE': 'practice'}, clear=True)
    @patch.object(shard, 'log')
    def test_creature_ai_reinitializes_when_combat_respawns_it(self, _log):
        state = {}
        shard.start_solo_simulation(state)
        entity = next(iter(state['creatures'].creatures))
        original = state['creatures'].creatures[entity]
        original.kill(0.)
        state['combat'].entities[entity].health = 0.
        state['combat'].entities[entity].respawn_at = 5.
        shard.solo_simulation_messages(state, 4.)
        self.assertTrue(state['creatures'].creatures[entity].dead)
        shard.solo_simulation_messages(state, 5.)
        revived = state['creatures'].creatures[entity]
        self.assertFalse(revived.dead)
        self.assertEqual(revived.position, original.position)
        self.assertEqual(revived.profile.name, 'spitter_noaggro')


if __name__ == '__main__':
    unittest.main()
