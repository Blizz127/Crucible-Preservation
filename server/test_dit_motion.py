"""DIT motion observation: bout bookkeeping, and that it can never cost a tick."""
import tempfile
import unittest
from unittest.mock import patch

import dit_motion
import shard_stub as shard


class MotionTransportTests(unittest.TestCase):
    def test_a_walk_then_a_stop_records_a_bout_without_error(self):
        with tempfile.TemporaryDirectory() as root:
            transport = dit_motion.MotionTransport(root)
            actor = ('human', 'local-player')
            now = 0.
            for step in range(40):                     # 10 s walking at 2 m/s
                now = step * .25
                transport.observe(actor, (step * .5, 0., 0.), now)
            for step in range(1, 13):                   # then 3 s standing still
                transport.observe(actor, (19.5, 0., 0.), now + step * .25)
            track = transport.tracks[actor]
            self.assertFalse(track['moving'])
            self.assertEqual(len(track['move_bouts']), 1)

    def test_a_flush_after_60_s_writes_a_document_that_reloads(self):
        with tempfile.TemporaryDirectory() as root:
            transport = dit_motion.MotionTransport(root)
            actor = ('bot', 'marine-hunter-2')
            now, x = 0., 0.
            while now < dit_motion.FLUSH_PERIOD + 5:
                for _ in range(8):                      # 2 s moving, 2 s still
                    now += .25
                    x += .5
                    transport.observe(actor, (x, 0., 0.), now)
                for _ in range(8):
                    now += .25
                    transport.observe(actor, (x, 0., 0.), now)
            track = transport.tracks[actor]
            self.assertGreater(track['flush_at'], 0.)
            reloaded = transport._load(actor, now)
            self.assertEqual(list(reloaded['move_bouts'])[-3:], list(track['move_bouts'])[-3:])
            self.assertEqual(reloaded['samples'] > 0, True)

    def test_bout_history_stays_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            transport = dit_motion.MotionTransport(root)
            actor = ('bot', 'marine-hunter-1')
            now, x = 0., 0.
            for bout in range(dit_motion.MAX_BOUTS + 5):
                for _ in range(4):                      # 1 s moving
                    now += .25
                    x += .5
                    transport.observe(actor, (x, 0., 0.), now)
                for _ in range(4):                      # 1 s still
                    now += .25
                    transport.observe(actor, (x, 0., 0.), now)
            track = transport.tracks[actor]
            self.assertEqual(len(track['move_bouts']), dit_motion.MAX_BOUTS)


class ShardGuardTests(unittest.TestCase):
    def test_a_failing_transport_cannot_cost_the_player_input(self):
        class Broken:
            def observe(self, *args, **kwargs):
                raise TypeError('broken transport')
        state = {}
        import client_input
        import test_client_input
        message = shard.EntityRpc(client_input.INPUT_DELIVERY, shard.PLAYER_CONNECTION_ID,
                                  172, 1, test_client_input.STILL)
        with patch('dit_motion.transport_for_state', return_value=Broken()), \
                patch.object(shard, 'log'):
            shard.observe_client_input(message, state)
        self.assertIn('player_position', state)
        self.assertEqual(state['simulation_errors'], {'DIT observation': 1})


if __name__ == '__main__':
    unittest.main()
