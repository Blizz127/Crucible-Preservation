"""Reported combat events and timer ticks share one session-relative clock."""
import unittest
from unittest.mock import patch

import combat
import shard_stub as shard


FLAGS = {'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_AI': '1',
         'CRUCIBLE_SOLO_MODE': 'practice',
         'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character'}


def reported_hit(projectile, target):
    return shard.EntityRpc(3, shard.PLAYER_CHARACTER_ID, 115, 4,
                           bytes([projectile, 1]) + target.to_bytes(4, 'big'))


@patch.dict('os.environ', FLAGS, clear=True)
@patch.object(shard, 'log')
class SoloClockTests(unittest.TestCase):
    def start(self):
        state = {'character_links_sent': True, 'practice_npcs_sent': True,
                 'clock_epoch': 10000., 'clock_last_sent': 10000.,
                 'seq': 1, 'received_seq': 1, 'received_window': 1}
        shard.start_solo_simulation(state)
        return state

    def test_reported_kill_respawns_on_real_game_clock_deadline(self, _log):
        state = self.start()
        victim = 0xfe00004b
        with patch.object(shard.time, 'monotonic', return_value=10005.):
            for projectile in range(1, 31):
                shard.observe_solo_combat(reported_hit(projectile, victim), state)
        entity = state['combat'].entities[victim]
        respawn_delay = (combat.PRACTICE_TARGET_RESPAWN_SECONDS
                         + combat.PRACTICE_TARGET_TELEGRAPH_SECONDS)
        self.assertEqual(entity.respawn_at, 5. + respawn_delay)
        self.assertIsNotNone(shard.game_clock_reply(
            state, now=10005. + respawn_delay - .1))
        self.assertEqual(entity.health, 0.)
        self.assertIsNotNone(shard.game_clock_reply(
            state, now=10005. + respawn_delay))
        self.assertEqual(entity.health, entity.maximum)
        self.assertIsNone(entity.respawn_at)

    def test_creature_damage_stamps_share_tick_clock(self, _log):
        state = self.start()
        victim = 0x1000003
        creature = state['creatures'].creatures[victim]
        x, y, z = creature.position
        state['player_position'] = (x, y + 2., z)
        with patch.object(shard.time, 'monotonic', return_value=10005.1239):
            shard.observe_solo_combat(reported_hit(1, victim), state)
        # Timer ticks truncate session time to milliseconds. External damage
        # must enter that same domain before it sets acquisition/deadline data.
        expected = int((10005.1239 - 10000.) * 1000) / 1000.
        self.assertEqual(creature._target_since, expected)
        self.assertEqual(creature._forced_aggro_until,
                         expected + creature.profile.forced_aggro_seconds_on_damage)
        self.assertIsNotNone(shard.game_clock_reply(state, now=10006.5))
        self.assertEqual(creature._now, 6.5)
        self.assertGreaterEqual(creature._now - creature._target_since,
                                creature.profile.attack_delay_for_new_targets)


if __name__ == '__main__':
    unittest.main()
