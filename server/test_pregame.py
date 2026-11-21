import unittest

import pregame


class ScheduleTests(unittest.TestCase):
    def test_a_table_without_a_requested_phase_runs_none(self):
        phases = [("first", 1, 1.0), ("last", 2, 2.0)]
        self.assertEqual(pregame.schedule(phases, ["first", "middle", "last"]), [])


if __name__ == "__main__":
    unittest.main()
