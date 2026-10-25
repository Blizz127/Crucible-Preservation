"""Server-resolved hunter hits (docs/35): offline, seeded, no sockets."""
import math
import unittest

import hunter_hits
from combat import MARINE_PRIMARY
from hunter_ai import Shot


def shot(time, distance, shooter=0x1000003):
    return Shot(shooter, 0x1000002, time, (0., 0., 1.), (1., 0., 0.), distance)


class BloomTests(unittest.TestCase):
    def test_bloom_increment_follows_the_authored_keys(self):
        self.assertAlmostEqual(hunter_hits.bloom_increment(0.0), 0.05)
        self.assertAlmostEqual(hunter_hits.bloom_increment(0.480234), 0.139486)
        self.assertAlmostEqual(hunter_hits.bloom_increment(1.0), 0.217935)
        self.assertTrue(0.05 < hunter_hits.bloom_increment(0.2) < 0.139486)

    def test_cone_widens_while_firing_and_recovers_when_idle(self):
        gun = hunter_hits.HunterGunnery(seed=1)
        for i in range(20):
            gun.resolve(shot(i * 0.096, 15.))
        self.assertEqual(gun.bloom[0x1000003], 1.0)
        gun.resolve(shot(19 * 0.096 + 0.31, 15.))
        # A 0.31 s pause resets bloom before that round, which adds one increment.
        self.assertAlmostEqual(gun.bloom[0x1000003], 0.05)


class HitTests(unittest.TestCase):
    def test_same_seed_gives_the_same_match(self):
        def run():
            gun = hunter_hits.HunterGunnery(seed=7)
            return [gun.resolve(shot(i * 0.096, 15.)) for i in range(200)]
        self.assertEqual(run(), run())

    def test_landing_carries_flight_time_and_falloff(self):
        gun = hunter_hits.HunterGunnery(seed=3, accuracy_scale=1.0, target_radius=100.)
        near = gun.resolve(shot(1.0, 15.))
        self.assertEqual((near.target, near.damage), (0x1000002, 20.0))
        self.assertAlmostEqual(near.time, 1.0 + 15. / MARINE_PRIMARY.speed)
        far = gun.resolve(shot(2.0, 70., shooter=0x1000004))
        self.assertEqual(far.damage, 10.0)

    def test_out_of_reach_or_zero_scale_never_hits(self):
        gun = hunter_hits.HunterGunnery(seed=3, accuracy_scale=1.0, target_radius=100.)
        self.assertIsNone(gun.resolve(shot(0., hunter_hits.MAX_REACH + 1)))
        gun = hunter_hits.HunterGunnery(seed=3, accuracy_scale=0.0)
        self.assertFalse(any(gun.resolve(shot(i * 0.096, 5.)) for i in range(200)))

    def test_hit_rate_matches_the_cone_and_scale(self):
        # A sustained burst sits at full bloom: 3.5 degrees.
        gun = hunter_hits.HunterGunnery(seed=11, accuracy_scale=1.0)
        hits = sum(gun.resolve(shot(i * 0.096, 15.)) is not None for i in range(20000))
        cone = math.tan(math.radians(hunter_hits.SPREAD_MAX_DEGREES))
        expected = (hunter_hits.TARGET_RADIUS / 15. / cone) ** 2
        self.assertAlmostEqual(hits / 20000, expected, delta=0.01)
        gun = hunter_hits.HunterGunnery(seed=11, accuracy_scale=0.25)
        scaled = sum(gun.resolve(shot(i * 0.096, 15.)) is not None for i in range(20000))
        self.assertAlmostEqual(scaled / 20000, expected * 0.25, delta=0.01)

    def test_scale_outside_unit_range_is_rejected(self):
        with self.assertRaises(ValueError):
            hunter_hits.HunterGunnery(accuracy_scale=1.5)


if __name__ == '__main__':
    unittest.main()


class GunTests(unittest.TestCase):
    """Each bot fires its own hero's primary (docs/37)."""

    def test_a_saboteur_bot_lands_its_own_rounds(self):
        import combat
        gunnery = hunter_hits.HunterGunnery(
            seed=1, accuracy_scale=1.0, target_radius=10.,
            guns={0x1000003: hunter_hits.SABOTEUR_GUN})
        landing = gunnery.resolve(Shot(0x1000003, 0x1000002, 1.0,
                                       (0., 0., 1.), (0., 1., 0.), 10.))
        self.assertAlmostEqual(landing.time, 1.0 + 10. / 780.)
        self.assertEqual(landing.damage,
                         combat.immediate_damage(combat.SABOTEUR_PRIMARY, distance=10.))
        self.assertAlmostEqual(hunter_hits.SABOTEUR_GUN.reach, 780. * 0.4)   # 312 m

    def test_the_saboteurs_bloom_and_spread_are_its_own(self):
        gun = hunter_hits.SABOTEUR_GUN
        self.assertAlmostEqual(hunter_hits.spread_degrees(0., gun), 0.5)
        self.assertAlmostEqual(hunter_hits.spread_degrees(1., gun), 2.0)
        self.assertAlmostEqual(hunter_hits.bloom_increment(0.5, gun.bloom_per_shot),
                               (0.15271739661693573 + 0.29195651412010193) / 2)
