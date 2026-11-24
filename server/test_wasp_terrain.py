import math
import unittest

import wasp_terrain


def flat():
    triangle = ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.))
    return wasp_terrain.Terrain(2, 2, 1., 1., lambda row, column: [triangle])


class SurfaceTests(unittest.TestCase):
    def test_face_entry_and_zero_length_overlap(self):
        t = flat()
        self.assertAlmostEqual(t.first_contact((.4, .4, 1), (.4, .4, 0), .25), .75)
        self.assertEqual(t.first_contact((.2, .2, .25), (.2, .2, .25), .25), 0.)
        self.assertIsNone(t.first_contact((.2, .2, .251), (.2, .2, .251), .25))
        self.assertIsNone(t.first_contact((.2, .2, 1), (.2, .2, .26), .25))

    def test_underside_does_not_hit_a_cave_ceiling(self):
        t = flat()
        self.assertIsNone(t.first_contact((.2, .2, -.5), (.2, .2, 1.), .25))
        self.assertIsNone(t.first_contact((-.5, .3, -.1), (.8, .3, -.1), .25))

    def test_edge_and_corner_contact_before_centre_reaches_the_cell(self):
        t = flat()
        self.assertAlmostEqual(t.first_contact((-.5, .4, .1), (.5, .4, .1), .25),
                               .5-math.sqrt(.25**2-.1**2))
        self.assertAlmostEqual(t.first_contact((-.5, -.5, .12), (0, 0, .12), .25),
                               1.-math.sqrt((.25**2-.12**2)/.5))
        self.assertAlmostEqual(t.first_contact((-.5, .4, .25), (0., .4, .25), .25), 1.)

    def test_flight_crossing_the_plane_in_open_space_can_hit_the_upper_lip(self):
        # Cross z=0 before reaching the edge at x=0, then touch its upper side.
        start, end = (-1., .4, -.2), (.5, .4, .3)
        expected = (3.2 - math.sqrt(3.2**2 - 10*(1.04 - .25**2))) / 5.
        self.assertAlmostEqual(flat().first_contact(start, end, .25), expected)


if __name__ == '__main__':
    unittest.main()
