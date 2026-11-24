import unittest

import static_geometry as geometry


FLOOR = ((0., 0., 0.), (2., 0., 0.), (0., 2., 0.))
IDENTITY = (1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.)


def placed(*, vertices=FLOOR, triangles=((0, 1, 2),)):
    return geometry.World('test', [(vertices, triangles)], [(0, 0, IDENTITY)])


class PropSphereTests(unittest.TestCase):
    def test_horizontal_sweep_touches_edge_while_centreline_misses(self):
        world = placed()
        start, end = ((-1.0, 0.5, 0.15), (1.0, 0.5, 0.15))
        self.assertIsNone(world.first_hit(start, end, mask=1))
        self.assertAlmostEqual(world.first_sphere_hit(start, end, 0.25, mask=1), 0.4)
        self.assertIsNone(world.first_sphere_hit((-1.0, 0.5, 0.251), (1.0, 0.5, 0.251), 0.25, mask=1))

    def test_front_overlap_backface_exit_and_finite_step(self):
        world = placed()
        self.assertEqual(world.first_sphere_hit((0.5, 0.5, 0.1), (0.5, 0.5, 0.1), 0.25, mask=1), 0.0)
        self.assertIsNone(world.first_sphere_hit((0.5, 0.5, -0.1), (0.5, 0.5, 1.0), 0.25, mask=1))
        self.assertIsNone(world.first_sphere_hit((0.5, 0.5, 1.0), (0.5, 0.5, 0.251), 0.25, mask=1))
        self.assertAlmostEqual(world.first_sphere_hit((0.5, 0.5, 1.0), (0.5, 0.5, 0.25), 0.25, mask=1), 1.0)

    def test_degenerate_faces_do_not_create_capsules_or_mask_a_later_face(self):
        vertices = FLOOR + ((0.5, 0.5, 1.0), (0.0, 0.5, 0.8), (1.0, 0.5, 0.8), (2.0, 0.5, 0.8))
        world = placed(vertices=vertices, triangles=((3, 3, 3), (4, 5, 6), (0, 1, 2)))
        self.assertAlmostEqual(world.first_sphere_hit((0.5, 0.5, 2.0), (0.5, 0.5, -2.0), 0.25, mask=1), 0.4375)


if __name__ == '__main__':
    unittest.main()
