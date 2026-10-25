import math
import unittest

import level_geometry


def clear(terrain, start, end, **options):
    return level_geometry.terrain_clear(terrain, start, end, **options)


class Recorder:
    """A terrain that records where it is asked and never has ground."""

    def __init__(self):
        self.points = []

    def height(self, x, y):
        self.points.append((x, y))
        return None


class RayTests(unittest.TestCase):
    def test_samples_are_even_interior_and_never_further_apart_than_step(self):
        for start, end, step in (((0., 0., 0.), (10.3, 0., 0.), 0.5),
                                 ((1., 2., 3.), (4., 6., 15.), 0.5),
                                 ((0., 0., 0.), (0.9, 0., 0.), 0.5),
                                 ((5., 5., 0.), (5., 5., 7.), 2.)):
            recorder = Recorder()
            self.assertTrue(clear(recorder, start, end, step=step, epsilon=0.))
            length = math.dist(start, end)
            count = math.ceil(length / step)
            self.assertLessEqual(length / count, step)
            self.assertEqual(len(recorder.points), count - 1, (start, end))
            for index, (x, y) in enumerate(recorder.points, 1):
                self.assertAlmostEqual(x, start[0] + (end[0] - start[0]) * index / count)
                self.assertAlmostEqual(y, start[1] + (end[1] - start[1]) * index / count)


if __name__ == '__main__':
    unittest.main()
