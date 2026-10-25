"""Scale reported damage against a caller-supplied maximum."""
import unittest
from types import SimpleNamespace

import shooter_feedback


class HitScaleTests(unittest.TestCase):
    def test_scale_is_the_damage_fraction_of_the_maximum(self):
        first = SimpleNamespace(damage_max=128.0)
        second = SimpleNamespace(damage_max=256.0)
        self.assertEqual(shooter_feedback.hit_scale(128.0, first), 1.0)
        self.assertEqual(shooter_feedback.hit_scale(64.0, first), 0.5)
        self.assertEqual(shooter_feedback.hit_scale(192.0, second), 0.75)


if __name__ == '__main__':
    unittest.main()
