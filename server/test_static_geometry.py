"""Static collision geometry: the cache format, queries, and the shard's use of it."""
import math
import os
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import shard_stub as shard
import solo_modes
import static_geometry as sg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_static_geometry as build  # noqa: E402

IDENTITY = (1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.)
# The installed sweeps' switch (test_level_geometry): set
# CRUCIBLE_SKIP_INSTALLED_NAV=1 to skip the 20 s rebuild below.
SLOW = os.environ.get('CRUCIBLE_SKIP_INSTALLED_NAV') != '1'


def at(x, y, z, matrix=IDENTITY):
    return matrix[:9] + (x, y, z)


def world(*instances, meshes=None):
    """A World with a 2 m cube (mesh 0) placed per (layer, matrix)."""
    meshes = meshes or [build.box_mesh((1., 1., 1.))]
    return sg.World('test', meshes, [(0, layer, matrix) for layer, matrix in instances])


class QueryTests(unittest.TestCase):
    def test_a_box_blocks_a_line_through_it_and_not_one_beside_it(self):
        w = world((8, at(10., 0., 0.)))
        self.assertTrue(w.blocked((0., 0., 0.), (20., 0., 0.)))
        self.assertFalse(w.blocked((0., 3., 0.), (20., 3., 0.)))
        self.assertFalse(w.blocked((0., 0., 0.), (8.5, 0., 0.)))     # stops short
        self.assertTrue(w.blocked((20., 0., 0.), (0., 0., 0.)))     # either way

    def test_back_faces_do_not_block(self):
        # PhysX skips back faces unless asked: a line leaving a box is clear.
        w = world((8, at(10., 0., 0.)))
        self.assertFalse(w.blocked((10., 0., 0.), (20., 0., 0.)))

    def test_a_mirrored_instance_still_blocks_from_outside_only(self):
        # A negative determinant reverses the winding in world space only.
        # The query runs in the mesh's own space, so it needs no flip.
        w = world((8, (-1., 0., 0., 0., 1., 0., 0., 0., 1., 10., 0., 0.)))
        self.assertTrue(w.blocked((0., 0., 0.), (20., 0., 0.)))
        self.assertFalse(w.blocked((10., 0., 0.), (20., 0., 0.)))

    def test_the_awareness_group_skips_layers_1_to_4(self):
        for layer, blocks in ((0, True), (1, False), (2, False), (3, False),
                              (4, False), (5, True), (8, True)):
            with self.subTest(layer=layer):
                w = world((layer, at(10., 0., 0.)))
                self.assertEqual(w.blocked((0., 0., 0.), (20., 0., 0.)), blocks)
        # Layer 2 (CustomGameplayCollision) stops rounds, not sight.
        w = world((2, at(10., 0., 0.)))
        self.assertTrue(w.blocked((0., 0., 0.), (20., 0., 0.), sg.PROJECTILES))

    def test_a_rotated_scaled_instance_is_placed_like_physx(self):
        # 45 degrees about z, scaled 3x along its own x: the box's far corner
        # moves to (3/sqrt2, 3/sqrt2) * 1 and beyond.
        c, s = math.cos(math.pi / 4), math.sin(math.pi / 4)
        matrix = build.compose((3 * c, 3 * s, 0., -s, c, 0., 0., 0., 1., 50., 50., 0.),
                               None, None, None)
        w = world((8, matrix))
        far = (50. + 2.5 * c, 50. + 2.5 * s, 0.)          # inside the stretched axis
        self.assertTrue(w.blocked((far[0] - 5 * s, far[1] + 5 * c, 0.),
                                  (far[0] + 5 * s, far[1] - 5 * c, 0.)))
        beside = (50. + 2.5 * c - 1.5 * s, 50. + 2.5 * s + 1.5 * c, 0.)
        self.assertFalse(w.blocked((beside[0] - 5 * c, beside[1] - 5 * s, 0.),
                                   (beside[0] - 4 * c, beside[1] - 4 * s, 0.)))

    def test_a_long_line_finds_a_box_many_cells_away_in_any_direction(self):
        rng = random.Random(4)
        for _ in range(50):
            angle = rng.uniform(0., 2 * math.pi)
            target = (300 * math.cos(angle), 300 * math.sin(angle), 1.)
            w = world((8, at(*target)))
            far = tuple(2 * v for v in target[:2]) + (1.,)
            self.assertTrue(w.blocked((0., 0., 1.), far), angle)

    def test_the_offset_and_rotation_compose_after_the_scale(self):
        # world = R (Q (S A v) + S p) + T, with R = I, T = 0, S = 2, a 90
        # degree Q about z, offset p = (1, 0, 0) and asset scale A = (3, 1, 1).
        matrix = build.compose((2., 0., 0., 0., 2., 0., 0., 0., 2., 0., 0., 0.),
                               (1., 0., 0.), (0., 0., math.sin(math.pi / 4), math.cos(math.pi / 4)),
                               (3., 1., 1.))
        point = sg._apply(matrix, (1., 0., 0.))
        for got, want in zip(point, (2., 6., 0.)):
            self.assertAlmostEqual(got, want, places=5)


class CacheTests(unittest.TestCase):
    def test_round_trip(self):
        meshes = [build.box_mesh((1., 2., 3.)), build.capsule_mesh(.5, 3.)]
        instances = [(0, 8, at(1., 2., 3.)), (1, 5, at(4., 5., 6.))]
        data = sg.dump('test', meshes, instances)
        level, got_meshes, got_instances = sg.parse(data)
        self.assertEqual(level, 'test')
        self.assertEqual([len(t) for _, t in got_meshes], [12, len(meshes[1][1])])
        self.assertEqual([(m, layer) for m, layer, _ in got_instances], [(0, 8), (1, 5)])

    def test_damaged_caches_are_refused(self):
        data = sg.dump('test', [build.box_mesh((1., 1., 1.))], [(0, 8, IDENTITY)])
        for bad in (b'XXXX' + data[4:], data[:4] + b'\x09' + data[5:], data + b'\0',
                    data[:-53] + b'\x05' + data[-52:]):
            with self.assertRaises(ValueError):
                sg.parse(bad)

    def test_the_primitive_meshes_are_closed_and_wound_outward(self):
        for vertices, triangles in (build.box_mesh((1., 2., 3.)), build.capsule_mesh(.5, 3.),
                                    build.capsule_mesh(1., 2.)):
            volume = sum(sum(a * b for a, b in zip(
                vertices[i], (vertices[j][1] * vertices[k][2] - vertices[j][2] * vertices[k][1],
                              vertices[j][2] * vertices[k][0] - vertices[j][0] * vertices[k][2],
                              vertices[j][0] * vertices[k][1] - vertices[j][1] * vertices[k][0])))
                for i, j, k in triangles) / 6
            self.assertGreater(volume, 0.)


class CookedMeshTests(unittest.TestCase):
    def blob(self, vertices, triangles, bounds=None):
        import struct
        flat = [v for p in vertices for v in p]
        lo = [min(p[i] for p in vertices) for i in range(3)]
        hi = [max(p[i] for p in vertices) for i in range(3)]
        return (b'wrapper' + b'NXS\x01MESH' + struct.pack('<5I', 15, 1, 4, len(vertices), len(triangles))
                + struct.pack('<%df' % len(flat), *flat)
                + bytes(i for t in triangles for i in t) + b'\0' * 7
                + struct.pack('<6f', *(bounds or (lo + hi))))

    def test_a_cooked_mesh_decodes_and_is_checked_against_its_bounds(self):
        vertices, triangles = build.box_mesh((1., 2., 3.))
        got_vertices, got_triangles = build.decode_pxmesh(self.blob(vertices, triangles))
        self.assertEqual([tuple(v) for v in got_vertices], [tuple(map(float, v)) for v in vertices])
        self.assertEqual([tuple(t) for t in got_triangles], triangles)
        with self.assertRaises(ValueError):
            build.decode_pxmesh(self.blob(vertices, triangles, bounds=[0.] * 6))
        with self.assertRaises(ValueError):
            build.decode_pxmesh(self.blob(vertices, [(0, 1, 8)]))


class ShardSightTests(unittest.TestCase):
    ENV = {'CRUCIBLE_SOLO_MODE': 'alpha-hunters', 'CRUCIBLE_SOLO_LOS': 'static'}

    def test_static_sight_adds_the_colliders_to_the_terrain(self):
        w = world((8, at(1310., 1260., 25.)))
        with patch.dict('os.environ', self.ENV, clear=True), \
                patch('level_geometry.installed_terrain', return_value=object()), \
                patch('level_geometry.sight_clear', return_value=True), \
                patch('static_geometry.installed_world', return_value=w), \
                patch.object(shard, 'log'):
            sight = shard.level_sight()
            self.assertFalse(sight((1300., 1260., 25.), (1320., 1260., 25.)))
            self.assertTrue(sight((1300., 1270., 25.), (1320., 1270., 25.)))

    def test_without_a_cache_static_falls_back_to_the_terrain(self):
        with patch.dict('os.environ', self.ENV, clear=True), \
                patch('level_geometry.installed_terrain', return_value=object()), \
                patch('level_geometry.sight_clear', return_value=False) as terrain, \
                patch('static_geometry.installed_world', return_value=None), \
                patch.object(shard, 'log') as log:
            sight = shard.level_sight()
            self.assertFalse(sight((0., 0., 0.), (1., 0., 0.)))
        self.assertTrue(terrain.called)
        self.assertIn('terrain only', log.call_args.args[0])

    def test_terrain_mode_never_loads_the_cache(self):
        with patch.dict('os.environ', dict(self.ENV, CRUCIBLE_SOLO_LOS='terrain'), clear=True), \
                patch('level_geometry.installed_terrain', return_value=object()), \
                patch('static_geometry.installed_world') as loader:
            self.assertIsNotNone(shard.level_sight())
        loader.assert_not_called()


@unittest.skipUnless(sg.cache_path('april2018').exists(),
                     'no April2018 static geometry cache (tools/build_static_geometry.py)')
class InstalledWorldTests(unittest.TestCase):
    """The built April2018 cache; read-only."""

    @classmethod
    def setUpClass(cls):
        import navigation
        cls.world = sg.installed_world('april2018')
        cls.mesh = navigation.installed_mesh()

    def eye(self, point):
        located = self.mesh.locate(point, vertical_tolerance=3.)[1]
        return (located[0], located[1], located[2] + 1.)     # AggroOriginOffsetLocal

    def test_no_collider_is_on_layer_2(self):
        # The one layer where sight and rounds differ; rounds share the sight test.
        _, _, instances = sg.parse(sg.cache_path('april2018').read_bytes())
        self.assertNotIn(2, {layer for _, layer, _ in instances})

    def test_the_alpha_hunters_drop_lines(self):
        player = self.eye(solo_modes.APRIL2018.player_position)
        bots = [self.eye(p) for p in solo_modes.APRIL2018.bot_positions]
        self.assertFalse(self.world.blocked(player, bots[0]))
        self.assertFalse(self.world.blocked(player, bots[1]))
        # rock_coquina_ledge_01_top rises between the player's drop and 17b.
        self.assertTrue(self.world.blocked(player, bots[2]))

    def test_a_query_examines_few_instances(self):
        # Work, not wall-clock time, so the bound holds on a busy host. In
        # dense 5-40 m segments around the Alpha Hunters area the grid hands
        # out a mean of 27 and at most 67 of 8,236 instances (0.6 ms p99).
        rng = random.Random(7)
        centres = [c for c in self.mesh.centres.values()
                   if abs(c[0] - 1300) < 150 and abs(c[1] - 1150) < 200]
        counts = []
        while len(counts) < 300:
            a, b = rng.choice(centres), rng.choice(centres)
            if not 5 <= math.dist(a[:2], b[:2]) <= 40:
                continue
            counts.append(len(list(self.world._candidates(
                (a[0], a[1], a[2] + 1.), (b[0], b[1], b[2] + 1.)))))
        self.assertLess(sum(counts) / len(counts), 60)
        self.assertLess(max(counts), 200)

    @unittest.skipUnless(SLOW and (build.INSTALL / 'objects.pak').exists(),
                         'installed sweeps disabled or the mesh paks are not installed')
    def test_the_builder_reproduces_the_installed_cache(self):
        # A stale cache would let a builder change pass the tests above.
        data, _ = build.build('april2018', log=lambda *args: None)
        self.assertEqual(data, sg.cache_path('april2018').read_bytes())


if __name__ == '__main__':
    unittest.main()
