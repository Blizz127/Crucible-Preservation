"""Check aggregate motion records with synthetic positions and times."""
import tempfile
import unittest

import dit_motion


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
            actor = ('bot', 'test-bot-2')
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
            actor = ('bot', 'test-bot-1')
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


if __name__ == '__main__':
    unittest.main()
