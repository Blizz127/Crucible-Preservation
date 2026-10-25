"""HOTH_GUIDE's respawn behind CRUCIBLE_SOLO_GUIDE_RESPAWN=1 (docs/37)."""
import unittest
from unittest.mock import patch

import combat
import shard_stub as shard
import solo_modes

PLAYER = shard.PLAYER_CHARACTER_ID
ENV = {'CRUCIBLE_SOLO_MODE': 'hoth-guide-april2018', 'CRUCIBLE_SOLO_GUIDE_RESPAWN': '1',
       'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_AI': '0'}


@patch.object(shard, 'log')
class GuideRespawnTests(unittest.TestCase):
    def dead_player(self, env=ENV):
        state = {}
        with patch.dict('os.environ', env, clear=True):
            shard.start_solo_simulation(state)
            arena = state['combat']
            arena.apply_damage(shard.HUNTER_BOT_IDS[0], PLAYER, 10000., now=1.)
        self.assertFalse(arena.alive(PLAYER))
        return state

    def rpcs(self, state):
        return [(m.delivery, m.entity_id, m.component, m.rpc, m.data)
                for m in state.pop('pending_rpcs', [])]

    def test_only_in_hoth_guide_with_the_switch(self, _log):
        for env, on in ((ENV, True), (dict(ENV, CRUCIBLE_SOLO_GUIDE_RESPAWN='0'), False),
                        (dict(ENV, CRUCIBLE_SOLO_MODE='alpha-hunters'), False)):
            with self.subTest(env=env), patch.dict('os.environ', env, clear=True):
                self.assertEqual(shard.guide_respawn_enabled(), on)

    def test_the_two_rpcs_their_timing_and_the_health(self, log):
        state = self.dead_player()
        arena = state['combat']
        drop = None
        sent = {}
        with patch.dict('os.environ', ENV, clear=True):
            drop = solo_modes.selected().player_position
            for i in range(0, 160):
                now = 2. + i * .1
                shard.solo_simulation_messages(state, now)
                for rpc in self.rpcs(state):
                    sent.setdefault(rpc[2:4], []).append((round(now, 1), rpc))
        died = 2.                                              # seen on the first tick
        (wave_at, wave), = sent[(120, 0)]
        self.assertEqual(wave_at, died + 10.)
        self.assertEqual(wave, (2, PLAYER, 120, 0, shard.pack_practice_spawn_transform(drop)))
        landed = sent[(120, 1)]
        self.assertEqual([at for at, _ in landed], [died + 11., died + 11.5, died + 12.])
        self.assertTrue(all(rpc == (2, PLAYER, 120, 1, b'') for _, rpc in landed))
        self.assertTrue(arena.alive(PLAYER))
        self.assertEqual(arena.health(PLAYER), arena.entities[PLAYER].maximum)
        lines = [c.args[0].strip() for c in log.call_args_list if 'GUIDE RESPAWN' in c.args[0]]
        self.assertEqual(lines, [
            'GUIDE RESPAWN player died at 2.0 s; respawn in 10 s',
            'GUIDE RESPAWN wave expired sent at 12.0 s',
            'GUIDE RESPAWN pod landed sent and health restored at 13.0 s (count 1)'])
        self.assertIsNone(state['guide_respawn']['stage'])    # ready for the next death

    def test_the_restored_health_is_framed_as_the_players_own(self, _log):
        state = self.dead_player()
        built = []
        original = combat.build_health_update

        def record(entity_id, shared_types, **kwargs):
            built.append((entity_id, kwargs.get('autonomous'), kwargs.get('value')))
            return original(entity_id, shared_types, **kwargs)
        with patch.dict('os.environ', ENV, clear=True), \
                patch.object(combat, 'build_health_update', record):
            for i in range(0, 120):
                shard.solo_simulation_messages(state, 2. + i * .1)
        restored = [b for b in built if b[0] == PLAYER and b[2] == state['combat'].entities[PLAYER].maximum]
        self.assertTrue(restored)
        self.assertTrue(all(autonomous for _, autonomous, _ in restored))

    def test_the_clients_respawn_ack_is_logged_while_armed(self, log):
        import test_client_input
        state = self.dead_player()
        with patch.dict('os.environ', ENV, clear=True):
            shard.solo_simulation_messages(state, 2.)          # armed: dead
            data = bytearray(test_client_input.MOVING)
            data[93] = 4                                       # frame 0's respawn ack
            shard.observe_client_input(
                shard.EntityRpc(3, shard.PLAYER_CONNECTION_ID, 172, 1, bytes(data)), state)
        lines = [c.args[0].strip() for c in log.call_args_list]
        self.assertIn('CLIENT INPUT respawn ack 0 -> 4', lines)

    def test_how_far_the_player_moved_after_a_revival_is_logged(self, log):
        state = self.dead_player()
        with patch.dict('os.environ', ENV, clear=True):
            state['player_position'] = (100., 200., 30.)
            for i in range(0, 120):                            # revived at 13.0 s
                shard.solo_simulation_messages(state, 2. + i * .1)
            state['player_position'] = (103., 204., 30.)        # 5 m away
            for i in range(120, 240):
                shard.solo_simulation_messages(state, 2. + i * .1)
        lines = [c.args[0].strip() for c in log.call_args_list if 'moved' in c.args[0]]
        self.assertEqual(lines, ['GUIDE RESPAWN moved 5.0 m in the 10 s after revival 1'])

    def test_b0_names_it(self, _log):
        import hunter_ai
        import hunter_patrol
        route = ((0., 0., 0.), (1., 0., 0.))
        squad = hunter_ai.Squad([hunter_ai.Bot(0x1000003, route, hunter_ai.MARINE)])
        with patch.dict('os.environ', dict(ENV, CRUCIBLE_SOLO_AI='1'), clear=True), \
                patch.object(hunter_patrol, 'squad', return_value=squad), \
                patch.object(shard, 'level_sight', return_value=None), \
                patch.object(shard, 'log') as log:
            shard.start_solo_simulation({})
        lines = [c.args[0].strip() for c in log.call_args_list if 'HUNTER AI switches' in c.args[0]]
        self.assertEqual(lines, ['HUNTER AI switches: guide-respawn; sight off'])


if __name__ == '__main__':
    unittest.main()
