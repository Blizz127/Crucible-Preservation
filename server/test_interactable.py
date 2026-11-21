"""Test the repeated publication of each changed state."""
import unittest

from interactable import Tracker


class TrackerTests(unittest.TestCase):
    def test_two_successive_states_each_get_five_sends(self):
        tracker = Tracker(initial_state='initial')
        entity = 'sample'
        tracker.want(entity, 'initial')
        self.assertIsNone(tracker.due(entity))
        for state in ('first', 'second'):
            tracker.want(entity, state)
            for _ in range(5):
                self.assertEqual(tracker.due(entity), state)
            self.assertIsNone(tracker.due(entity))


if __name__ == '__main__':
    unittest.main()
