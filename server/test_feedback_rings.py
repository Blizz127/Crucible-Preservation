import unittest

import feedback_rings as rings


class RingPolicyTests(unittest.TestCase):
    def test_indicator_index_stays_inside_the_supplied_ring(self):
        size = 5
        indicators = rings.IndicatorRing(size)
        for hit in range(size * 3):
            indicators.hit(1.0, (0.0, 0.0, 0.0), float(hit))
            indicators.take(float(hit))
            self.assertLess(indicators.last_index, size)


if __name__ == "__main__":
    unittest.main()
