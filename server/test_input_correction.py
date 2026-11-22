"""Check retries and transitions through the callback interface."""
import unittest

import input_correction


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.state = {}

    def tick(self, suppress, now):
        sends = []
        input_correction.refresh_request(self.state, suppress, now, sends.append)
        return sends

    def test_first_release_is_sent(self):
        self.assertEqual(self.tick(False, 1.), [False])
        self.assertFalse(self.state['input_suppress']['suppress'])

    def test_begin_in_a_burst_then_once_a_second(self):
        sends = [i for i in range(60) if self.tick(True, 2. + i * .05)]
        self.assertEqual(sends[:3], [0, 1, 2])
        self.assertEqual(len(sends), 5)
        self.assertTrue(all(20 <= b - a <= 22 for a, b in zip(sends[2:], sends[3:])), sends)

    def test_release_is_refreshed_after_the_burst(self):
        self.assertEqual(self.tick(True, 2.), [True])
        for i in range(input_correction.END_BURST):
            self.assertEqual(self.tick(False, 3. + i * .05), [False])
        self.assertEqual(self.tick(False, 4.), [])
        self.assertEqual(self.tick(False, 4.5), [False])

    def test_new_request_interrupts_release(self):
        self.assertEqual(self.tick(True, 2.), [True])
        self.assertEqual(self.tick(False, 3.), [False])
        self.assertEqual(self.tick(True, 3.05), [True])


if __name__ == '__main__':
    unittest.main()
