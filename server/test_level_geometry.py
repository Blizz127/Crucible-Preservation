import array
import math
import os
from pathlib import Path
import random
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import level_geometry
import navigation

SLOW = os.environ.get('CRUCIBLE_SKIP_INSTALLED_NAV') != '1'
PAK = level_geometry.level_pak('april2018')
# The PhysX collision heightfields live in the game-wide levels.pak, one
# directory above NvGameSdk.
LEVELS_PAK = level_geometry.LEVELS.parents[1] / 'levels.pak'


def build(size, sector, height):
    """A Terrain whose whole-metre samples are height(ix, iy), in 0.01 m units.

    Each leaf stores its lowest height as its offset, as the real data does.
    """
    n = sector + 1
    leaves = {}
    for lx in range(size // sector):
        for ly in range(size // sector):
            grid = [[height(lx * sector + i, ly * sector + j) for j in range(n)]
                    for i in range(n)]
            offset = min(min(column) for column in grid)
            samples = array.array('H', (round((h - offset) * 100)
                                        for column in grid for h in column))
            leaves[(lx, ly)] = (offset, samples, n)
    return level_geometry.Terrain(leaves, size, sector)


def ridge(ix, iy):
    """A 4 m crest along x = 8 on a plane that rises 0.25 m per metre of y.

    Every kink falls on a whole metre and every value is a multiple of 0.25, so
    the bilinear surface equals this formula at every real (x, y), exactly at
    the samples and to rounding between them.
    """
    return 0.25 * iy + max(0., 4. - abs(ix - 8))


def clear(terrain, start, end, **options):
    return level_geometry.terrain_clear(terrain, start, end, **options)


class Recorder:
    """A terrain that records where it is asked and never has ground."""

    def __init__(self):
        self.points = []

    def height(self, x, y):
        self.points.append((x, y))
        return None


class HeightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.terrain = build(16, 4, ridge)

    def test_height_is_exact_at_every_sample_point(self):
        for ix in range(16):
            for iy in range(16):
                self.assertEqual(self.terrain.height(ix, iy), ridge(ix, iy))
                self.assertEqual(self.terrain.height(float(ix), float(iy)), ridge(ix, iy))

    def test_height_follows_the_surface_between_samples_and_across_leaves(self):
        rng = random.Random(7)
        for _ in range(500):
            x, y = rng.uniform(0, 16), rng.uniform(0, 16)
            self.assertAlmostEqual(self.terrain.height(x, y), ridge(x, y), places=9)

    def test_bilinear_interpolation_of_a_twisted_cell(self):
        # Random heights make every cell non-planar, which the ridge never does.
        rng = random.Random(11)
        heights = {(ix, iy): rng.randrange(0, 600) / 100.
                   for ix in range(9) for iy in range(9)}
        terrain = build(8, 4, lambda ix, iy: heights[ix, iy])
        for _ in range(500):
            x, y = rng.uniform(0, 8), rng.uniform(0, 8)
            i, j = min(int(x), 7), min(int(y), 7)
            fx, fy = x - i, y - j
            expected = (heights[i, j] * (1 - fx) * (1 - fy)
                        + heights[i + 1, j] * fx * (1 - fy)
                        + heights[i, j + 1] * (1 - fx) * fy
                        + heights[i + 1, j + 1] * fx * fy)
            self.assertAlmostEqual(terrain.height(x, y), expected, places=9)

    def test_x_and_y_are_not_interchangeable(self):
        # The crest runs along y, so height depends on x alone, up to the plane.
        self.assertEqual(self.terrain.height(8, 2), 0.5 + 4.)
        self.assertEqual(self.terrain.height(2, 8), 2.)

    def test_off_the_map_has_no_height(self):
        for x, y in ((-0.001, 5), (5, -0.001), (16, 5), (5, 16), (100, 100),
                     (float('nan'), 5), (5, float('inf'))):
            self.assertIsNone(self.terrain.height(x, y), (x, y))
        for x, y in ((0, 0), (15.999, 15.999), (0, 15.5)):
            self.assertIsNotNone(self.terrain.height(x, y), (x, y))

    def test_a_missing_leaf_has_no_height(self):
        terrain = build(16, 4, ridge)
        del terrain.leaves[(1, 1)]
        self.assertIsNone(terrain.height(5, 5))
        self.assertIsNotNone(terrain.height(1, 1))


class SightLineTests(unittest.TestCase):
    def test_sight_points_are_raised_by_the_authored_offsets(self):
        # marine_guide_dummy.ai: AggroOriginOffsetLocal (0x103e) is (0, 0, 1),
        # AggroTargetOffsetVertical (0x1061) is 1.0.
        self.assertEqual(level_geometry.EYE_HEIGHT, 1.0)
        self.assertEqual(level_geometry.TARGET_HEIGHT, 1.0)
        self.assertEqual(level_geometry.sight_line((1., 2., 3.), (4., 5., 6.)),
                         ((1., 2., 4.), (4., 5., 7.)))

    def test_each_end_gets_its_own_offset(self):
        # The authored values are equal, so a swap would go unnoticed.
        with patch.object(level_geometry, 'EYE_HEIGHT', 1.7), \
                patch.object(level_geometry, 'TARGET_HEIGHT', 0.3):
            self.assertEqual(level_geometry.sight_line((1., 2., 3.), (4., 5., 6.)),
                             ((1., 2., 4.7), (4., 5., 6.3)))


class RayTests(unittest.TestCase):
    """Sight lines over a ridge. At y = 8 the ground is 2 m and the crest 6 m."""

    @classmethod
    def setUpClass(cls):
        cls.terrain = build(16, 4, ridge)

    def test_segments_over_the_ridge_are_clear(self):
        for start, end in (((2., 8., 8.), (14., 8., 8.)),
                           ((2., 8., 3.), (14., 8., 12.)),
                           ((1., 2., 12.), (15., 14., 12.)),
                           ((14., 8., 3.), (2., 8., 12.))):
            self.assertTrue(clear(self.terrain, start, end), (start, end))

    def test_segments_through_the_ridge_are_blocked(self):
        for start, end in (((2., 8., 3.), (14., 8., 3.)),
                           ((14., 8., 3.), (2., 8., 3.)),
                           ((1., 1., 3.), (15., 15., 6.)),
                           ((5., 8., 5.), (11., 8., 5.))):
            self.assertFalse(clear(self.terrain, start, end), (start, end))

    def test_epsilon_decides_grazing_lines(self):
        # A level line at the crest: the ground at x = 8 is 6.0 m.
        low, high = ((2., 8., 5.94), (14., 8., 5.94)), ((2., 8., 5.96), (14., 8., 5.96))
        self.assertFalse(clear(self.terrain, *low))
        self.assertTrue(clear(self.terrain, *high))
        self.assertFalse(clear(self.terrain, *high, epsilon=0.))
        self.assertTrue(clear(self.terrain, *low, epsilon=0.1))

    def test_endpoints_are_not_tested(self):
        # Starting 0.1 m under the ground is fine when the line climbs away.
        self.assertTrue(clear(self.terrain, (2., 8., 1.9), (3., 8., 20.)))
        # The first sample is what blocks a line that stays underground.
        self.assertFalse(clear(self.terrain, (2., 8., 1.0), (14., 8., 3.)))
        # A segment shorter than one step has no interior at all.
        self.assertTrue(clear(self.terrain, (2., 8., 0.), (2.2, 8., 0.)))
        self.assertTrue(clear(self.terrain, (2., 8., 3.), (2., 8., 3.)))

    def test_step_is_the_largest_spacing_between_samples(self):
        start, end = (2., 8., 3.), (14., 8., 3.)
        self.assertFalse(clear(self.terrain, start, end))
        self.assertFalse(clear(self.terrain, start, end, step=0.05))
        # With one step as long as the segment there is no interior to test.
        self.assertTrue(clear(self.terrain, start, end, step=12.))
        self.assertTrue(clear(self.terrain, start, end, step=100.))
        for step in (0, -1.):
            with self.assertRaises(ValueError):
                clear(self.terrain, start, end, step=step)

    def test_samples_are_even_interior_and_never_further_apart_than_step(self):
        for start, end, step in (((0., 0., 0.), (10.3, 0., 0.), 0.5),
                                 ((1., 2., 3.), (4., 6., 15.), 0.5),
                                 ((0., 0., 0.), (0.9, 0., 0.), 0.5),
                                 ((5., 5., 0.), (5., 5., 7.), 2.)):
            recorder = Recorder()
            self.assertTrue(clear(recorder, start, end, step=step))
            length = math.dist(start, end)
            count = math.ceil(length / step)
            self.assertLessEqual(length / count, step)
            self.assertEqual(len(recorder.points), count - 1, (start, end))
            for index, (x, y) in enumerate(recorder.points, 1):
                self.assertAlmostEqual(x, start[0] + (end[0] - start[0]) * index / count)
                self.assertAlmostEqual(y, start[1] + (end[1] - start[1]) * index / count)

    def test_a_segment_below_the_surface_is_not_clear(self):
        # The test is exactly "stays above"; underground callers must decide
        # for themselves (see the module docstring).
        self.assertFalse(clear(self.terrain, (2., 8., 0.5), (14., 8., 0.5)))
        self.assertFalse(clear(self.terrain, (2., 8., 0.5), (3., 8., 0.5)))

    def test_gameplay_sight_skips_the_terrain_for_an_underground_end(self):
        # Ground is 2 m at (2, 8) and (14, 8); the crest between is 6 m.
        over = ((2., 8., 3.), (14., 8., 3.))
        self.assertFalse(level_geometry.sight_clear(self.terrain, *over))
        # One end 1 m under the ground (a cave or a building the terrain does
        # not model): the terrain cannot decide, so the line is clear.
        cave = ((2., 8., 1.), (14., 8., 3.))
        self.assertFalse(clear(self.terrain, *cave))
        self.assertTrue(level_geometry.sight_clear(self.terrain, *cave))
        self.assertTrue(level_geometry.sight_clear(self.terrain, *cave[::-1]))
        # Any depth counts by default; a tolerance keeps shallow ends literal.
        shallow = ((2., 8., 1.6), (14., 8., 3.))
        self.assertTrue(level_geometry.sight_clear(self.terrain, *shallow))
        self.assertFalse(level_geometry.sight_clear(self.terrain, *shallow, underground=.5))

    def test_vertical_segments_use_their_3d_length(self):
        # The crest is 6 m at (8, 8); plan length is zero in both cases.
        self.assertFalse(clear(self.terrain, (8., 8., 20.), (8., 8., 3.)))
        self.assertTrue(clear(self.terrain, (8., 8., 20.), (8., 8., 7.)))

    def test_off_the_map_counts_as_clear(self):
        deep = -100.
        self.assertTrue(clear(self.terrain, (-10., 8., deep), (-2., 8., deep)))
        self.assertTrue(clear(self.terrain, (20., 8., deep), (30., 8., deep)))
        self.assertTrue(clear(self.terrain, (-9., -9., deep), (-1., 30., deep)))
        # Off the map at one end only: the on-map samples still decide.
        self.assertTrue(clear(self.terrain, (-4., 8., 10.), (2., 8., 3.)))
        self.assertFalse(clear(self.terrain, (-4., 8., 50.), (8., 8., 3.)))
        self.assertFalse(clear(self.terrain, (20., 8., 50.), (8., 8., 3.)))

    def test_the_answer_is_the_same_in_both_directions(self):
        rng = random.Random(23)
        answers = set()
        for _ in range(500):
            a = (rng.uniform(-2, 18), rng.uniform(-2, 18), rng.uniform(0, 10))
            b = (rng.uniform(-2, 18), rng.uniform(-2, 18), rng.uniform(0, 10))
            forward = clear(self.terrain, a, b)
            self.assertEqual(forward, clear(self.terrain, b, a), (a, b))
            answers.add(forward)
        self.assertEqual(answers, {True, False})


def encode(size=8, sector=4, leaf=None, filler=b'', bad_node=None, empty=None):
    """A minimal terrain.dat: header, grid, filler, node tree and a tail.

    `leaf(lx, ly)` gives (offset, raw samples). Returns the bytes, the node
    tree's (start, end) file offsets and each node's start offset in order.
    Nodes are padded to four bytes in absolute file offsets, as the engine does.
    """
    n = sector + 1
    blob = bytearray(struct.pack('<4bI', level_geometry.TERRAIN_VERSION, 0, 2, 0, 0))
    blob += struct.pack('<4i', size, 1, sector, size // sector)
    blob += filler
    start, nodes = len(blob), []

    def pad():
        blob.extend(bytes(-len(blob) % 4))

    def emit(x, y, extent):
        nodes.append(len(blob))
        has = extent == sector and (x // sector, y // sector) != empty
        base, raws = leaf(x // sector, y // sector) if has else (0., ())
        version = 9 if len(nodes) - 1 == bad_node else level_geometry.NODE_VERSION
        blob.extend(struct.pack('<hh', version, 0))
        blob.extend(struct.pack('<6f', x, y, 0., x + extent, y + extent, 9.))
        blob.extend(struct.pack('<ffii', base, max(raws, default=0) / 100.,
                                n if has else 0, 3))
        if has:
            blob.extend(struct.pack('<%dH' % len(raws), *raws))
            pad()
            blob.extend(bytes(n * n * 6))            # surface type items
            pad()
        blob.extend(bytes(20))                       # five LOD error floats
        blob.extend(bytes(3))                        # three surface type ids
        pad()
        if extent > sector:
            half = extent // 2
            for dx, dy in ((0, 0), (half, 0), (0, half), (half, half)):
                emit(x + dx, y + dy, half)

    emit(0, 0, size)
    end = len(blob)
    blob.extend(b'tail')                             # the chunk goes on past the tree
    struct.pack_into('<I', blob, 4, len(blob))
    return bytes(blob), (start, end), nodes


def mini_leaf(lx, ly):
    return float(lx + 2 * ly), [(37 * lx + 101 * ly + 7 * k) % 650 for k in range(25)]


# Two false roots ahead of the real one: a pair of floats equal to the map size
# with no node header before it, and a real-looking node whose box starts at
# (1, 1) instead of the origin.
DECOYS = (b'\xff' * 5 + struct.pack('<ff', 8., 8.) + b'\xff' * 9
          + struct.pack('<hh6f', 8, 0, 1., 1., 0., 8., 8., 9.))


class ParserTests(unittest.TestCase):
    def test_decodes_what_the_engine_wrote(self):
        data, span, nodes = encode(leaf=mini_leaf, filler=DECOYS)
        terrain = level_geometry.read_terrain(data)
        self.assertEqual((terrain.size, terrain.sector, len(terrain.leaves)), (8, 4, 4))
        self.assertEqual(terrain.span, span)
        self.assertEqual(len(nodes), 5)
        for lx in range(2):
            for ly in range(2):
                base, raws = mini_leaf(lx, ly)
                offset, samples, n = terrain.leaves[(lx, ly)]
                self.assertEqual((offset, list(samples), n), (base, raws, 5))
        # Samples are x-major: world (6, 3) is sample (2, 3) of leaf (1, 0).
        base, raws = mini_leaf(1, 0)
        self.assertEqual(terrain.height(6, 3), base + raws[2 * 5 + 3] / 100.)

    def test_rejects_inconsistent_chunks(self):
        data, span, nodes = encode(leaf=mini_leaf)

        def patched(offset, fmt, value):
            out = bytearray(data)
            struct.pack_into(fmt, out, offset, value)
            return bytes(out)

        def resized(body):
            out = bytearray(body)
            struct.pack_into('<I', out, 4, len(out))
            return bytes(out)

        cases = (
            ('version', patched(0, '<b', 0x1c), 'unsupported compiled terrain'),
            ('chunk size', data + b'\0', 'unsupported compiled terrain'),
            ('header', data[:6], 'truncated compiled terrain'),
            ('unit', patched(12, '<i', 2), 'unsupported terrain grid'),
            ('sector table', patched(20, '<i', 3), 'unsupported terrain grid'),
            ('no tree', resized(data[:span[0]] + b'tail'), 'node tree not found'),
            ('node version', patched(nodes[1], '<h', 9), 'bad terrain node'),
            ('leaf size', patched(nodes[1] + 36, '<i', 7), 'unexpected terrain leaf'),
            ('cut tree', resized(data[:span[1] - 8]), 'truncated terrain node tree'),
            ('empty leaf', encode(leaf=mini_leaf, empty=(1, 1))[0],
             'incomplete terrain tree'),
        )
        for name, blob, message in cases:
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, message):
                    level_geometry.read_terrain(blob)

    def test_reads_the_terrain_entry_out_of_a_level_pak(self):
        data, span, _ = encode(leaf=mini_leaf)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'level.pak'
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('mnmnavmission0.bai', b'not terrain')
                archive.writestr('terrain/terrain.dat', data)
            terrain = level_geometry.Terrain.from_level_pak(path)
            self.assertEqual((terrain.span, len(terrain.leaves)), (span, 4))
            self.assertEqual(level_geometry.Terrain.from_level_pak(str(path)).span, span)
            other = Path(directory) / 'other.pak'
            with zipfile.ZipFile(other, 'w') as archive:
                archive.writestr('mnmnavmission0.bai', b'not terrain')
            with self.assertRaisesRegex(ValueError, 'terrain/terrain.dat'):
                level_geometry.Terrain.from_level_pak(other)


class LevelLookupTests(unittest.TestCase):
    def test_level_pak_follows_the_navigation_layout(self):
        self.assertEqual(level_geometry.level_pak(), navigation.LEVEL_PAK)
        self.assertEqual(level_geometry.level_pak(None), navigation.LEVEL_PAK)
        self.assertEqual(level_geometry.level_pak('HotH_02'),
                         navigation.LEVEL_PAK.parents[1] / 'HotH_02' / 'level.pak')

    def test_every_spelling_of_one_level_shares_one_decode(self):
        data, _, _ = encode(leaf=mini_leaf)
        with tempfile.TemporaryDirectory() as directory:
            level = Path(directory) / 'april2018'
            level.mkdir()
            with zipfile.ZipFile(level / 'level.pak', 'w') as archive:
                archive.writestr('terrain/terrain.dat', data)
            self.addCleanup(level_geometry._decoded.cache_clear)
            with patch.object(level_geometry, 'LEVELS', Path(directory)):
                terrain = level_geometry.installed_terrain()
                self.assertEqual(len(terrain.leaves), 4)
                self.assertIs(level_geometry.installed_terrain('april2018'), terrain)
                self.assertIs(level_geometry.installed_terrain(level='april2018'), terrain)
                self.assertIs(level_geometry.installed_terrain(None), terrain)


def pak_module():
    tools = str(Path(__file__).resolve().parents[1] / 'tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import pak
    return pak


def grid_height(terrain, ix, iy):
    """The decoded sample at whole-metre grid position (ix, iy), edges included."""
    last = terrain.size // terrain.sector - 1
    lx, ly = min(ix // terrain.sector, last), min(iy // terrain.sector, last)
    offset, samples, n = terrain.leaves[(lx, ly)]
    return offset + samples[(ix - lx * terrain.sector) * n + (iy - ly * terrain.sector)] / 100.


def navmesh_offsets(mesh, terrain, count, seed):
    """Navmesh height minus terrain height at one random point in each of
    `count` distinct seeded random triangles; None where the point is off the map."""
    rng = random.Random(seed)
    offsets = []
    for key in rng.sample(sorted(mesh.triangles), count):
        a, b, c = mesh.triangles[key]
        u, v = rng.random(), rng.random()
        if u + v > 1:
            u, v = 1 - u, 1 - v
        point = tuple(a[i] + (b[i] - a[i]) * u + (c[i] - a[i]) * v for i in range(3))
        ground = terrain.height(point[0], point[1])
        offsets.append(None if ground is None else point[2] - ground)
    return offsets


@unittest.skipUnless(SLOW and PAK.exists(), 'installed terrain sweep disabled or '
                     'April2018 level.pak is not installed')
class InstalledTerrainTests(unittest.TestCase):
    """Decode the shipped April2018 terrain; nothing is written."""

    @classmethod
    def setUpClass(cls):
        cls.terrain = level_geometry.installed_terrain()

    def test_decodes_the_whole_authored_tree(self):
        terrain = self.terrain
        self.assertEqual((terrain.size, terrain.sector, len(terrain.leaves)),
                         (2048, 32, 4096))
        self.assertEqual(terrain.span, (0x50bfc, 0x22b30a8))
        # Every leaf stores its own lowest height as its offset.
        self.assertTrue(all(min(samples) == 0 for _, samples, _ in terrain.leaves.values()))
        low = min(offset for offset, _, _ in terrain.leaves.values())
        high = max(offset + max(samples) / 100. for offset, samples, _ in terrain.leaves.values())
        self.assertAlmostEqual(low, 0., places=2)
        self.assertAlmostEqual(high, 508.64, places=2)

    def test_neighbouring_leaves_agree_on_their_shared_edge_samples(self):
        terrain, worst = self.terrain, 0.
        for (lx, ly), (offset, samples, n) in terrain.leaves.items():
            right, above = terrain.leaves.get((lx + 1, ly)), terrain.leaves.get((lx, ly + 1))
            for k in range(n):
                if right:
                    here = offset + samples[(n - 1) * n + k] / 100.
                    worst = max(worst, abs(here - (right[0] + right[1][k] / 100.)))
                if above:
                    here = offset + samples[k * n + n - 1] / 100.
                    worst = max(worst, abs(here - (above[0] + above[1][k * n] / 100.)))
        # One 0.01 m raw step, plus float32 offsets; measured 0.00999.
        self.assertLess(worst, 0.011)

    def test_the_map_ends_where_the_leaves_end(self):
        terrain = self.terrain
        for x, y in ((-1, 5), (5, -1), (2048, 5), (5, 2048)):
            self.assertIsNone(terrain.height(x, y), (x, y))
        for x, y in ((0, 0), (2047.99, 2047.99), (1024, 0), (0, 1024)):
            self.assertIsNotNone(terrain.height(x, y), (x, y))

    @unittest.skipUnless(LEVELS_PAK.exists(), 'levels.pak is not installed')
    def test_decode_is_the_physx_collision_heightfield_up_to_quantisation(self):
        # levels.pak levels/april2018/terrain/terrain.pxheightfield: an 8 byte
        # ObjectStream prefix, then a PhysX 3.4 cooked heightfield (NXS 1,
        # HFHF, u32 version, u32 rows, u32 columns, ...) whose samples are the
        # last rows * columns * 4 bytes: int16 height and two material bytes,
        # row-major with the row along y.
        pak = pak_module()
        name = b'levels/april2018/terrain/terrain.pxheightfield'
        data = pak.entry_bytes(next(e for e in pak.load(LEVELS_PAK) if e['name'] == name))
        rows, columns = struct.unpack_from('<II', data, data.find(b'NXS\x01HFHF') + 12)
        start = len(data) - 4 * rows * columns
        terrain = self.terrain
        self.assertEqual((rows, columns), (terrain.size + 1, terrain.size + 1))
        rng = random.Random(20261025)
        edge = terrain.size
        points = [(0, 0), (edge, 0), (0, edge), (edge, edge)]
        points += [(rng.randrange(edge + 1), rng.randrange(edge + 1)) for _ in range(30000)]
        counts = [struct.unpack_from('<h', data, start + 4 * (y * columns + x))[0]
                  for x, y in points]
        decoded = [grid_height(terrain, x, y) for x, y in points]
        # Measured over every leaf sample: -0.0018 .. +0.0619 m of count / 32.
        differences = [d - c / 32. for d, c in zip(decoded, counts)]
        self.assertGreaterEqual(min(differences), -0.005)
        self.assertLessEqual(max(differences), 0.065)
        # And a straight line through the counts leaves residuals the size of
        # the two quantisation steps; measured -0.022 .. +0.026 m everywhere.
        n = len(points)
        mean_c, mean_d = sum(counts) / n, sum(decoded) / n
        slope = (sum((c - mean_c) * (d - mean_d) for c, d in zip(counts, decoded))
                 / sum((c - mean_c) ** 2 for c in counts))
        intercept = mean_d - slope * mean_c
        residual = max(abs(d - (slope * c + intercept)) for c, d in zip(counts, decoded))
        self.assertAlmostEqual(slope, 1 / 32., delta=1e-4)
        self.assertLess(residual, 0.03)
        # height() reads the same samples that the raw grid does.
        for (x, y), expected in list(zip(points, decoded))[:2000]:
            if x < edge and y < edge:
                self.assertEqual(terrain.height(x, y), expected, (x, y))


# Two ground-level navmesh points five metres apart in the open, found by
# scanning the April2018 mesh for a large flat triangle; both sit 0.09 and
# 0.11 m above the decoded terrain, and the sight line clears it by 1 m.
OPEN_A = (842.5, 1203.5, 60.729)
OPEN_B = (847.5, 1203.5, 60.912)
# Two ground-level points 69 m apart on one flat plain (17.7 m) with a hill
# between them: the terrain crests at 23.3 m, 4.6 m above the eye-to-eye line.
HILL_A = (1233.5, 941.5, 17.711)
HILL_B = (1212.5, 875.5, 17.711)


@unittest.skipUnless(SLOW and PAK.exists(), 'installed navigation sweep disabled or '
                     'April2018 level.pak is not installed')
class InstalledNavmeshTests(unittest.TestCase):
    """The decoded terrain against the shipped MNM navmesh; nothing is written."""

    @classmethod
    def setUpClass(cls):
        cls.terrain = level_geometry.installed_terrain()
        # installed_mesh() and installed_mesh('april2018') read the same pak
        # and share one cached mesh with the rest of the suite.
        cls.mesh = navigation.installed_mesh()
        cls.offsets = navmesh_offsets(cls.mesh, cls.terrain, 5000, 20261025)

    def test_navmesh_peaks_just_above_the_terrain_where_it_lies_on_the_ground(self):
        offsets = self.offsets
        self.assertEqual(len(offsets), 5000)
        self.assertNotIn(None, offsets)                 # every sample is over terrain
        within = sum(-0.25 <= d <= 0.5 for d in offsets) / len(offsets)
        # About half of the navmesh follows the ground: 0.49..0.52 over fourteen
        # seeded draws of 2000 and 5000 triangles. The rest is elevated or
        # underground. Transposed samples manage 0.20.
        self.assertGreaterEqual(within, 0.40)
        bins = {}
        for d in offsets:
            bins[math.floor(d / 0.05)] = bins.get(math.floor(d / 0.05), 0) + 1
        peak = max(bins, key=bins.get) * 0.05
        # The most common 0.05 m bin starts at +0.05 or +0.10 m in every draw.
        self.assertIn(round(peak, 2), (0.05, 0.10))

    def test_part_of_the_navmesh_lies_under_the_terrain(self):
        # The premise of the open question about characters standing under the
        # heightfield: 6..9% of samples are more than 0.5 m below the surface.
        under = sum(d < -0.5 for d in self.offsets) / len(self.offsets)
        self.assertGreater(under, 0.03)
        self.assertLess(under, 0.15)

    def test_open_ground_points_have_a_clear_sight_line_at_eye_height(self):
        for point in (OPEN_A, OPEN_B):
            self.assertIsNotNone(self.mesh.locate(point, 0.05), point)
            self.assertTrue(0. <= point[2] - self.terrain.height(point[0], point[1]) <= 0.25)
        eye, target = level_geometry.sight_line(OPEN_A, OPEN_B)
        self.assertEqual(math.dist(OPEN_A[:2], OPEN_B[:2]), 5.)
        self.assertTrue(clear(self.terrain, eye, target))
        self.assertTrue(clear(self.terrain, target, eye))

    def test_a_hill_blocks_the_sight_line_between_two_points_on_the_plain(self):
        for point in (HILL_A, HILL_B):
            self.assertIsNotNone(self.mesh.locate(point, 0.05), point)
        eye, target = level_geometry.sight_line(HILL_A, HILL_B)
        self.assertFalse(clear(self.terrain, eye, target))
        self.assertFalse(clear(self.terrain, target, eye))
        # The crest stands 4.6 m above the line: 3 m of extra height is not
        # enough to see over it and 6 m is.
        for lift, expected in ((3., False), (6., True), (30., True)):
            raised = [(x, y, z + lift) for x, y, z in (eye, target)]
            self.assertEqual(clear(self.terrain, *raised), expected, lift)


if __name__ == '__main__':
    unittest.main()
