"""Read-only terrain heightfield and a sight-line test against it.

`AggroCheckLineOfSight` is true on `marine_guide_dummy.ai` (0x107b), so a
retail Marine never acquires a target it cannot see. The retail engine answers
that with physics raycasts against the level's colliders. This module covers
the largest of them, the terrain, and nothing else: walls, rocks and buildings
are separate static colliders that are not decoded here, so a line this module
calls clear can still be blocked by one of them.

Source: `terrain/terrain.dat` in a level's `level.pak`, the compiled CryEngine
terrain (chunk version 0x1d). As measured on that entry in
NvGameSdk/levels/april2018/level.pak (41,234,900 bytes inflated):

* Header: version byte 0x1d, three flag bytes, then the uint32 chunk size,
  which equals the file length. The grid follows at offset 8 as four int32:
  2048 heightmap units, 1 m per unit, 32 m sectors, a 64 x 64 sector table.
* The static-object (vegetation) group table between the header and the node
  tree, 330,724 bytes of .cgf object names, has a variable layout, so the root
  node is located by its signature instead (version 8, a box spanning the whole
  map in plan).
* The node tree, file offsets 0x50bfc..0x22b30a8, is a quadtree of 44-byte
  node headers. Its 4096 leaves are 32 m x 32 m with 33 x 33 uint16 samples,
  so neighbouring leaves share their edge samples. Samples are stored x-major,
  `samples[ix * 33 + iy]`, and a height is `leaf.offset + raw / 100` metres
  (`leaf.offset` is a float32, the leaf's lowest height).

Why the decode is trusted, strongest evidence first:

1. The level's cooked PhysX collision heightfield, `levels.pak` entry
   levels/april2018/terrain/terrain.pxheightfield (2049 x 2049 int16 counts,
   index `y * 2049 + x`). Over all 4,460,544 leaf samples the decode minus
   count / 32 is -0.0018..+0.0619 m. A straight-line fit over its 4,198,401
   grid positions gives 0.031251 m per count and +0.019 m, and leaves residuals
   of -0.022..+0.026 m, about the size of the two quantisation steps (1/32 m
   counts, 0.01 m raws). HotH_02, tutorial_02 and tutorial_03 decode too, within
   0.062 m of count / 32 of their own heightfields.
2. Each leaf records its own height range, and it equals max(raw) / 100 to
   within 0.01 m for all 4096 leaves; neighbouring leaves agree on their shared
   edge samples to within 0.011 m everywhere.
3. The MNM navmesh follows the surface where it lies on the ground: over
   seeded random navmesh triangles the offset peaks at +0.05..+0.15 m above the
   decoded terrain. It does NOT follow it everywhere, and that is not a decode
   error: only about half of all triangles lie within -0.25..+0.5 m, because
   the navmesh also covers elevated surfaces (up to ~20 m above the terrain)
   and underground ones (6 to 9% lie more than 0.5 m under the surface), which
   a heightfield cannot describe.

practicearena (1024 units of 2 m, 64 m sectors) is rejected as an unsupported
grid; only 1 m units are decoded.

Limits of the ray test: it asks only whether the segment stays above the
surface, so a segment that runs below it is blocked, even one between two
characters inside the same tunnel. About 4% of navmesh points put the eye
point under the surface; callers decide what to do for characters there.

Nothing under the Steam install is written. Pure Python: no numpy.
"""
from array import array
import functools
import math
from pathlib import Path
import struct
import sys

LEVELS = (Path.home() / '.local/share/Steam/steamapps/common/Crucible'
          / 'NvGameSdk/levels')
DEFAULT_LEVEL = 'april2018'
TERRAIN_ENTRY = b'terrain/terrain.dat'
# Chunk header version byte, 0x1d on every installed level that has a terrain.
TERRAIN_VERSION = 0x1d
# Every terrain node starts with this int16 version; the root is found by it.
NODE_VERSION = 8
# Leaf samples are uint16 in 1/100 m above the leaf's own offset (see the
# module docstring: each leaf's recorded range equals max(raw) / 100).
RAW_PER_METRE = 100.

# Sight-line anchors, both authored in AI/guide_dummies/marine_guide_dummy.ai
# (docs/21-hunter-ai.md, "Authored aim height"): AggroOriginOffsetLocal
# (0x103e) is (0, 0, 1), the observer's eye one metre above its root, and
# AggroTargetOffsetVertical (0x1061) is 1.0, the point aimed at one metre
# above the target's root.
EYE_HEIGHT = 1.0
TARGET_HEIGHT = 1.0

# Sampling for terrain_clear. Heightfield cells are 1 m, so half-metre spacing
# puts two samples in every cell. A sample blocks only when the surface is more
# than EPSILON above the line; 0.05 m is larger than the shipped PhysX
# heightfield's own 1/32 m height step, so quantisation alone never blocks a
# line that grazes the surface.
STEP = 0.5
EPSILON = 0.05
# Not authored: how far under the terrain an end point must be to count as
# inside geometry the terrain does not model (see sight_clear). An eye that
# stands on open ground is about 1.1 m above it, so any eye below the
# surface is inside a cave, tunnel or building. On April2018's walkable mesh
# 3.7% of spots put the eye under the terrain; 1.75% of them by under 0.5 m.
UNDERGROUND = 0.0


def _align(offset):
    """Round up to a multiple of four, as the engine pads its arrays."""
    return (offset + 3) & ~3


class Terrain:
    """Decoded compiled terrain: one uint16 grid per sector-sized leaf.

    `leaves` maps (leaf x, leaf y) to (offset, samples, n). `samples` holds
    n * n uint16 values stored x-major, `samples[ix * n + iy]`, with
    n = sector + 1 so that neighbouring leaves share their edge samples; a
    sample's height is offset + raw / 100 metres. `size` and `sector` are in
    metres. `span` is the node tree's file offsets when decoded from a file.
    """

    def __init__(self, leaves, size, sector, span=None):
        self.leaves, self.size, self.sector, self.span = leaves, size, sector, span

    def height(self, x, y):
        """Bilinear terrain height at world (x, y), or None off the map."""
        if not (0 <= x < self.size and 0 <= y < self.size):
            return None
        s = self.sector
        lx, ly = int(x // s), int(y // s)
        leaf = self.leaves.get((lx, ly))
        if leaf is None:
            return None
        offset, samples, n = leaf
        u, v = x - lx * s, y - ly * s
        i, j = min(int(u), n - 2), min(int(v), n - 2)
        fu, fv = u - i, v - j
        row, nxt = i * n, (i + 1) * n
        raw = (samples[row + j] * (1 - fu) * (1 - fv)
               + samples[nxt + j] * fu * (1 - fv)
               + samples[row + j + 1] * (1 - fu) * fv
               + samples[nxt + j + 1] * fu * fv)
        return offset + raw / RAW_PER_METRE

    @staticmethod
    def from_level_pak(path):
        """Decode terrain/terrain.dat out of a level.pak. Nothing is written."""
        # The read-only PAK reader copes with CryEngine's local-header separators.
        tools = str(Path(__file__).resolve().parents[1] / 'tools')
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import pak
        entries = pak.load(Path(path))
        entry = next((e for e in entries if e['name'] == TERRAIN_ENTRY), None)
        if entry is None:
            raise ValueError('no %s in %s' % (TERRAIN_ENTRY.decode(), path))
        return read_terrain(pak.entry_bytes(entry))


def read_terrain(data):
    """Parse terrain.dat's header and node tree. Raises on any inconsistency."""
    try:
        return _read_terrain(data)
    except struct.error as error:
        raise ValueError('truncated compiled terrain') from error


def _read_terrain(data):
    version, _, _, _, chunk = struct.unpack_from('<4bI', data, 0)
    if version != TERRAIN_VERSION or chunk != len(data):
        raise ValueError('unsupported compiled terrain')
    size, unit, sector, table = struct.unpack_from('<4i', data, 8)
    if unit != 1 or size <= 0 or sector <= 0 or size % sector or table * sector != size:
        raise ValueError('unsupported terrain grid')
    # The root's box spans the whole heightmap in plan, so its max x and max y
    # are `size` as two adjacent float32 (box layout: min xyz, then max xyz).
    root = struct.pack('<ff', float(size), float(size))
    start = None
    search = 0
    while True:
        found = data.find(root, search)
        if found < 0:
            break
        box = found - 12
        if box >= 4 and struct.unpack_from('<h', data, box - 4)[0] == NODE_VERSION \
                and struct.unpack_from('<2f', data, box) == (0., 0.):
            start = box - 4
            break
        search = found + 1
    if start is None:
        raise ValueError('terrain node tree not found')
    leaves = {}

    def node(offset, x, y, extent):
        version, _ = struct.unpack_from('<hh', data, offset)
        if version != NODE_VERSION:
            raise ValueError('bad terrain node at 0x%x' % offset)
        base, _range, n, types = struct.unpack_from('<ffii', data, offset + 28)
        offset += 44
        if n:
            if extent != sector or n != sector + 1:
                raise ValueError('unexpected terrain leaf')
            samples = array('H')
            samples.frombytes(data[offset:offset + n * n * 2])
            if sys.byteorder != 'little':
                samples.byteswap()
            leaves[(x // sector, y // sector)] = (base, samples, n)
            offset = _align(offset + n * n * 2)
            offset = _align(offset + n * n * 6)      # surface type items
        offset += 20                                 # five LOD error floats
        offset = _align(offset + types)
        if extent > sector:
            half = extent // 2
            for dx, dy in ((0, 0), (half, 0), (0, half), (half, half)):
                offset = node(offset, x + dx, y + dy, half)
        return offset

    end = node(start, 0, 0, size)
    if end > len(data):
        raise ValueError('truncated terrain node tree')
    if len(leaves) != (size // sector) ** 2:
        raise ValueError('incomplete terrain tree')
    return Terrain(leaves, size, sector, (start, end))


def level_pak(level=DEFAULT_LEVEL):
    """Path of an installed level's pak: NvGameSdk/levels/<level>/level.pak."""
    return LEVELS / (level or DEFAULT_LEVEL) / 'level.pak'


def installed_terrain(level=DEFAULT_LEVEL):
    """Decode the installed level's compiled terrain once. Nothing is written."""
    return _decoded(str(level_pak(level)))


@functools.lru_cache(maxsize=4)
def _decoded(path):
    # Cached on the pak path string: installed_terrain(), installed_terrain(None)
    # and installed_terrain('april2018') are one entry here, whereas
    # lru_cache on the public function would key them apart and decode the
    # 41 MB chunk once per spelling.
    return Terrain.from_level_pak(path)


def sight_line(observer_feet, target_feet):
    """The (eye, target) sight points for characters standing at the given feet."""
    return ((observer_feet[0], observer_feet[1], observer_feet[2] + EYE_HEIGHT),
            (target_feet[0], target_feet[1], target_feet[2] + TARGET_HEIGHT))


def sight_clear(terrain, start, end, *, underground=UNDERGROUND):
    """terrain_clear for gameplay, except that an end under the terrain is clear.

    About 4% of April2018's navmesh puts a character's eye below the
    heightfield: caves, tunnels and building interiors, where static meshes
    the terrain model does not decode carry the walkable surface. A literal
    terrain test would hide such a character from every bot for good, and
    block every round at it. An end point more than `underground` metres
    below the surface at its own x/y (any depth, by default) therefore
    skips the terrain test.
    """
    for x, y, z in (start, end):
        ground = terrain.height(x, y)
        if ground is not None and z < ground - underground:
            return True
    return terrain_clear(terrain, start, end)


def terrain_clear(terrain, start, end, *, step=STEP, epsilon=EPSILON):
    """True when the segment start -> end stays above the terrain.

    `start` and `end` are world (x, y, z) points in metres, normally the two
    sight points from sight_line(). The segment's interior is sampled at even
    spacing of at most `step` metres along its 3D length; the endpoints are not
    tested. A sample blocks when the surface there is more than `epsilon` above
    the line. Samples off the map block nothing: the heightfield says nothing
    about them. The test is symmetric in start and end.
    """
    if step <= 0:
        raise ValueError('step must be positive')
    count = math.ceil(math.dist(start, end) / step)
    for index in range(1, count):
        f = index / count
        ground = terrain.height(start[0] + (end[0] - start[0]) * f,
                                start[1] + (end[1] - start[1]) * f)
        if ground is not None and ground > start[2] + (end[2] - start[2]) * f + epsilon:
            return False
    return True
