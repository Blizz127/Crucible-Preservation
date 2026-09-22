import math
import unittest

import navigation


class FunnelTests(unittest.TestCase):
    def test_straight_corridor_collapses_to_its_endpoints(self):
        gates = [((0., 1., 0.), (0., -1., 0.)), ((2., 1., 0.), (2., -1., 0.))]
        self.assertEqual(navigation.funnel((-1., 0., 0.), gates, (3., 0., 0.)),
                         [(-1., 0., 0.), (3., 0., 0.)])

    def test_corner_is_hugged_rather_than_cut_or_zigzagged(self):
        gates = [((2., 2., 0.), (2., 0., 0.)), ((2., 2., 0.), (4., 2., 0.))]
        start, end = (1., .5, 0.), (2.5, 3.5, 0.)
        points = navigation.funnel(start, gates, end)
        self.assertEqual(points, [start, (2., 2., 0.), end])
        pulled = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        midpoints = [start, *(tuple((a[i]+b[i])/2 for i in range(3))
                              for a, b in gates), end]
        centroids = sum(math.dist(a, b) for a, b in zip(midpoints, midpoints[1:]))
        self.assertLess(pulled, centroids)


if __name__ == '__main__':
    unittest.main()
