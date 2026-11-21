import unittest

from medpack import Medpack


class MedpackTests(unittest.TestCase):
    def test_whatever_the_ticks_the_heal_adds_up(self):
        amount, rate = 19.0, 2.5
        for step in (0.125, 1.25, 12.0):
            with self.subTest(step=step):
                pack = Medpack(count=0, maximum=5, heal_rate=rate, heals=[[amount, 0.]])
                total, now = 0., 0.
                while now < amount / rate + step:
                    now += step
                    total += pack.heal(now)
                self.assertAlmostEqual(total, amount)
                self.assertEqual(pack.heal_left, 0)

    def test_a_pickup_stops_at_the_configured_maximum(self):
        pack = Medpack(count=5, maximum=7, heal_rate=2.5)
        self.assertTrue(pack.add())
        self.assertTrue(pack.add())
        self.assertEqual(pack.count, 7)
        self.assertFalse(pack.add())
        self.assertEqual(pack.count, 7)


if __name__ == '__main__':
    unittest.main()
