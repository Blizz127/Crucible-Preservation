import math
import os
import random
import struct
import unittest

import navigation

SLOW = os.environ.get('CRUCIBLE_SKIP_INSTALLED_NAV') != '1'


def encode(tiles, tile_size=(8, 8, 8), origin=(0., 0., 0.)):
    """Build a one-agent, one-mesh MNM v7 blob from plain tile descriptions.

    `tiles` is a list of (xyz, vertices, triangles) where vertices are metres
    relative to the tile corner and each triangle is (i, j, k, [link words]).
    """
    def string(value):
        return struct.pack('<I', len(value)) + value

    body = b''
    for xyz, vertices, triangles in tiles:
        links, packed = [], []
        for i, j, k, words in triangles:
            packed.append(struct.pack('<4HI', i, j, k,
                                      len(words) | (len(links) << 4), 1))
            links.extend(words)
        blob = struct.pack('<3HIH', *xyz, 0, len(triangles)) + b''.join(packed)
        blob += struct.pack('<H', len(vertices))
        blob += b''.join(struct.pack('<3H', *(round(v * 2048) for v in p))
                         for p in vertices)
        blob += struct.pack('<H', len(links)) + b''.join(
            struct.pack('<H', v) for v in links)
        body += blob + struct.pack('<H', 0)
    mesh = struct.pack('<IfIII', 1, 10, 0, 0, len(tiles))
    mesh += struct.pack('<3f3i3fI', *origin, *tile_size, .125, .125, .125, 1024)
    mesh += body
    mesh = struct.pack('<I', 1) + string(b'area') + struct.pack('<II', 2, len(mesh)) + mesh
    agent = struct.pack('<I', 1) + mesh
    return (struct.pack('<HIII', 7, 6, 0, 1) + string(b'MediumSizedCharacters')
            + struct.pack('<I', len(agent)) + agent)


def link(side, edge, index):
    return side | (edge << 4) | (index << 6)


def square():
    """Two linked triangles forming a square, plus a disconnected third."""
    return encode([((0, 0, 0),
                    [(0, 0, 0), (2, 0, 0), (0, 2, 0), (2, 2, 0),
                     (5, 5, 0), (6, 5, 0), (5, 6, 0)],
                    [(0, 1, 2, [link(15, 1, 1)]),
                     (1, 3, 2, [link(15, 2, 0)]),
                     (4, 5, 6, [])])])


def split_border(cut=2.0):
    """One triangle meeting two smaller neighbours across the x=8 tile border.

    The neighbours' border edges split at `cut`, so the source edge midpoint
    at y=4 lies outside the first neighbour entirely. Clipping is mandatory.
    """
    return encode([
        ((0, 0, 0), [(0, 4, 0), (8, 0, 0), (8, 8, 0)],
         [(0, 1, 2, [link(0, 1, 0), link(0, 1, 1)])]),
        ((1, 0, 0), [(0, 0, 0), (8, 0, 0), (0, cut, 0), (0, 8, 0)],
         [(0, 1, 2, [link(7, 2, 0)]), (2, 1, 3, [link(7, 2, 0)])]),
    ])


def stacked(upper):
    """Two triangles sharing a plan edge across the vertical tile border."""
    return encode([
        ((0, 0, 0), [(0, 0, 7.9), (4, 0, 7.9), (0, 4, 7.9)],
         [(0, 1, 2, [link(6, 0, 0)])]),
        ((0, 0, 1), [(0, 0, upper - 8), (4, 0, upper - 8), (0, 4, upper - 8)],
         [(0, 1, 2, [link(13, 0, 0)])]),
    ])


class GeometryTests(unittest.TestCase):
    def test_clipped_portal_replaces_an_out_of_target_midpoint(self):
        mesh = navigation.read_mnm(split_border())
        source = (1, 0, 0, 0, 0)
        first, second = sorted(mesh.links[source], key=lambda e: e[0])
        # The old behaviour used the source edge midpoint for both links.
        midpoint = (8., 4., 0.)
        self.assertIsNone(navigation.height(mesh.triangles[first[0]], 8., 4.))
        for target, portal in (first, second):
            self.assertNotEqual(portal[0], portal[1])
            middle = tuple((a + b) / 2 for a, b in zip(*portal))
            self.assertIsNotNone(
                navigation.height(mesh.triangles[target], middle[0], middle[1]))
            self.assertLess(math.dist(portal[0], portal[1]), 8.)
        self.assertEqual(sorted(round(math.dist(*p), 6) for _, p in (first, second)),
                         [2., 6.])
        self.assertNotIn(midpoint, [p for _, portal in (first, second) for p in portal])

    def test_partial_overlap_route_stays_on_the_mesh(self):
        mesh = navigation.read_mnm(split_border())
        start, end = (5., 4., 0.), (10., 4., 0.)
        path = mesh.path(start, end, step=.5)
        self.assertIsNotNone(path)
        for point in path:
            self.assertIsNotNone(mesh.locate(point, .001), point)

    def test_shared_edge_rejects_corner_touches_and_stacked_floors(self):
        # A corner-only contact has no collinear edge at all.
        triangle = ((0, 0, 0), (2, 0, 0), (0, 2, 0))
        corner = ((2, 0, 0), (4, 2, 0), (3, -2, 0))
        self.assertIsNone(navigation.shared_edge(triangle, 1, corner))
        self.assertEqual(navigation.read_mnm(stacked(11.)).links, {})
        touching = navigation.read_mnm(stacked(8.))
        self.assertEqual(len(touching.links), 2)
        self.assertEqual(len(touching.components()), 1)

    def test_link_census_counts_every_raw_word(self):
        mesh = navigation.read_mnm(split_border())
        counts = mesh.counts
        self.assertEqual(counts['raw'], 4)
        self.assertEqual(counts['border'], 4)
        self.assertEqual(counts['internal'] + counts['off_mesh'], 0)
        self.assertEqual(counts['dropped'], 0)
        # Only the wide source edge needs clipping; the two shorter neighbour
        # edges already lie wholly inside it on the way back.
        self.assertEqual(counts['clipped'], 2)


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


class MeshTests(unittest.TestCase):
    def test_route_crosses_shared_edge_and_preserves_endpoints(self):
        mesh = navigation.read_mnm(square())
        start, end = (.2, .2, 0), (1.8, 1.8, 0)
        route = mesh.route(start, end)
        self.assertEqual(len(route), 3)
        for actual, expected in zip(route, [start, (1., 1., 0.), end]):
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b, places=9)
        # The funnel sees straight through the same corridor.
        self.assertEqual(len(mesh.path(start, end, step=0)), 2)

    def test_disconnected_and_off_mesh_destinations_are_rejected(self):
        mesh = navigation.read_mnm(square())
        for method in (mesh.route, mesh.path):
            self.assertIsNone(method((.2, .2, 0), (5.1, 5.1, 0)))
            self.assertIsNone(method((.2, .2, 0), (20, 20, 0)))
            self.assertIsNone(method((.2, .2, 0), (1, 1, 20)))

    def test_components_separate_the_disconnected_island(self):
        mesh = navigation.read_mnm(square())
        self.assertEqual([len(c) for c in mesh.components()], [2, 1])

    def test_parser_rejects_wrong_version_truncation_and_trailing_bytes(self):
        data = square()
        for invalid in (b'\x08\0' + data[2:], data[:-1], data + b'\0'):
            with self.assertRaises(ValueError):
                navigation.read_mnm(invalid)


@unittest.skipUnless(SLOW, 'installed navigation sweep disabled')
class InstalledMeshTests(unittest.TestCase):
    """One slow sweep over the shipped April2018 mesh; nothing is written."""

    @classmethod
    def setUpClass(cls):
        if not navigation.LEVEL_PAK.exists():
            raise unittest.SkipTest('Crucible is not installed')
        cls.mesh = navigation.installed_mesh()

    def test_declared_mesh_size_and_link_census(self):
        self.assertEqual(len(self.mesh.triangles), 43130)
        self.assertEqual(self.mesh.counts['raw'], 99256)
        self.assertEqual(self.mesh.counts['internal'], 73424)
        self.assertEqual(self.mesh.counts['border'], 25832)
        # 1141 border links needed clipping and 430 are corner touches; both
        # were silently wrong under the previous edge-midpoint portals.
        self.assertEqual(self.mesh.counts['clipped'], 1141)
        self.assertEqual(self.mesh.counts['dropped'], 430)

    def test_largest_component_dominates_the_mesh(self):
        components = self.mesh.components()
        self.assertGreater(len(components[0]), .96 * len(self.mesh.triangles))

    def test_random_pairs_route_and_every_sample_lies_on_the_mesh(self):
        rng = random.Random(20260921)
        keys = sorted(self.mesh.components()[0])
        for trial in range(30):
            with self.subTest(trial=trial):
                _, start = self._point(rng, keys)
                _, end = self._point(rng, keys)
                path = self.mesh.path(start, end, step=1.)
                self.assertIsNotNone(path)
                self.assertEqual(path[0][:2], start[:2])
                self.assertEqual(path[-1][:2], end[:2])
                for point in path:
                    self.assertIsNotNone(self.mesh.locate(point, .05), point)
                pulled = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
                self.assertGreaterEqual(pulled + 1e-6, math.dist(start, end))
                route = self.mesh.route(start, end)
                corridor = sum(math.dist(a, b) for a, b in zip(route, route[1:]))
                self.assertLessEqual(pulled, corridor + 1e-6)
                # No zero-length or backtracking micro-steps in the output.
                for a, b in zip(path, path[1:]):
                    self.assertLessEqual(math.dist(a[:2], b[:2]), 1.0001)

    def _point(self, rng, keys):
        key = rng.choice(keys)
        a, b, c = self.mesh.triangles[key]
        u, v = rng.random(), rng.random()
        if u + v > 1:
            u, v = 1 - u, 1 - v
        return key, tuple(a[i] + (b[i]-a[i])*u + (c[i]-a[i])*v for i in range(3))


if __name__ == '__main__':
    unittest.main()
