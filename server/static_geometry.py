"""Static collision geometry for line of sight: walls, rocks and buildings.

Retail tests sight with a PhysX raycast against the level's colliders: every
static prop is a MeshColliderComponent whose cooked .pxmesh ships in
objects.pak, architecture.pak or animations.pak (docs/36). The
AwarenessLoS collision group skips layers 1-4 (InvisibleGeo,
CustomGameplayCollision, MaterialOverride, Characters); that the bots use
that group is inferred from its name.

Reading those paks and the 66 MB level slice takes tens of seconds, so it
happens offline: tools/build_static_geometry.py writes a compact cache, and
the shard only loads the cache (CRUCIBLE_SOLO_LOS=static). Nothing installed
is written. Pure standard library: the shard runs the host's system Python.
"""
import functools
import math
import os
import struct
from pathlib import Path

MAGIC = b'CRSG'
VERSION = 1
CACHE_DIR = Path.home() / '.cache' / 'crucible-revival'
# default.physxconfiguration group masks (bit = collision layer index).
AWARENESS_LOS = 0xFFFFFFFFFFFFFFE1
PROJECTILES = 0xFFFFFFFFFFFFFFE5
CELL = 8.0              # instance grid cell, metres (not authored)
LEAF = 4                # triangles per BVH leaf (not authored)


def cache_path(level='april2018'):
    return CACHE_DIR / ('static-geometry-%s.bin' % level.lower())


# -- the cache file -----------------------------------------------------------

def dump(level, meshes, instances):
    """Serialise meshes [(vertices, triangles)] and instances
    [(mesh, layer, matrix12)], matrix12 = columns c0, c1, c2 and the
    translation, so world = x*c0 + y*c1 + z*c2 + t."""
    name = level.encode()
    out = [MAGIC, struct.pack('<II', VERSION, len(name)), name,
           struct.pack('<I', len(meshes))]
    for vertices, triangles in meshes:
        out.append(struct.pack('<II', len(vertices), len(triangles)))
        out.append(struct.pack('<%df' % (3 * len(vertices)), *(v for p in vertices for v in p)))
        out.append(struct.pack('<%dI' % (3 * len(triangles)), *(i for t in triangles for i in t)))
    out.append(struct.pack('<I', len(instances)))
    for mesh, layer, matrix in instances:
        out.append(struct.pack('<IB12f', mesh, layer, *matrix))
    return b''.join(out)


def parse(data):
    """(level, meshes, instances) from dump()'s bytes."""
    if data[:4] != MAGIC:
        raise ValueError('not a static geometry cache')
    version, length = struct.unpack_from('<II', data, 4)
    if version != VERSION:
        raise ValueError('static geometry cache version %d, want %d' % (version, VERSION))
    offset = 12
    level = data[offset:offset + length].decode()
    offset += length
    (count,) = struct.unpack_from('<I', data, offset)
    offset += 4
    meshes = []
    for _ in range(count):
        nv, nt = struct.unpack_from('<II', data, offset)
        offset += 8
        flat = struct.unpack_from('<%df' % (3 * nv), data, offset)
        offset += 12 * nv
        index = struct.unpack_from('<%dI' % (3 * nt), data, offset)
        offset += 12 * nt
        vertices = [flat[i:i + 3] for i in range(0, 3 * nv, 3)]
        triangles = [index[i:i + 3] for i in range(0, 3 * nt, 3)]
        if any(i >= nv for i in index):
            raise ValueError('triangle index out of range')
        meshes.append((vertices, triangles))
    (count,) = struct.unpack_from('<I', data, offset)
    offset += 4
    instances = []
    for _ in range(count):
        mesh, layer, *matrix = struct.unpack_from('<IB12f', data, offset)
        offset += 53
        if mesh >= len(meshes):
            raise ValueError('instance mesh out of range')
        instances.append((mesh, layer, tuple(matrix)))
    if offset != len(data):
        raise ValueError('trailing bytes in static geometry cache')
    return level, meshes, instances


# -- queries ------------------------------------------------------------------

class MeshTree:
    """A bounding-volume hierarchy over one mesh's triangles, in local space."""

    def __init__(self, vertices, triangles):
        self.triangles = [(vertices[a], vertices[b], vertices[c]) for a, b, c in triangles]
        # Nodes: [lo, hi, left, right, first, count]; leaves have left = -1.
        self.nodes = []
        order = list(range(len(self.triangles)))
        if order:
            self._build(order, 0, len(order))
        self.order = order

    def _bounds(self, order, start, end):
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for index in order[start:end]:
            for point in self.triangles[index]:
                for axis in range(3):
                    if point[axis] < lo[axis]:
                        lo[axis] = point[axis]
                    if point[axis] > hi[axis]:
                        hi[axis] = point[axis]
        return lo, hi

    def _build(self, order, start, end):
        lo, hi = self._bounds(order, start, end)
        node = len(self.nodes)
        self.nodes.append([lo, hi, -1, -1, start, end - start])
        if end - start <= LEAF:
            return node
        axis = max(range(3), key=lambda a: hi[a] - lo[a])
        order[start:end] = sorted(order[start:end],
                                  key=lambda i: sum(p[axis] for p in self.triangles[i]))
        middle = (start + end) // 2
        left = self._build(order, start, middle)
        right = self._build(order, middle, end)
        self.nodes[node][2], self.nodes[node][3] = left, right
        return node

    def blocks(self, origin, direction):
        """True when origin + t * direction hits a triangle for 0 <= t <= 1.

        The test runs in the mesh's own space, where its authored winding
        holds. A line entering the solid still enters it after any invertible
        placement, a mirrored one included, so no instance needs a flip.
        """
        if not self.nodes:
            return False
        inverse = tuple(1. / d if d else math.inf for d in direction)
        stack = [0]
        while stack:
            lo, hi, left, right, first, count = self.nodes[stack.pop()]
            if not _slab(origin, inverse, lo, hi):
                continue
            if left < 0:
                for index in self.order[first:first + count]:
                    if _triangle(origin, direction, *self.triangles[index]):
                        return True
            else:
                stack.append(left)
                stack.append(right)
        return False


def _slab(origin, inverse, lo, hi):
    """Segment origin..origin + 1/inverse against an axis-aligned box."""
    near, far = 0., 1.
    for axis in range(3):
        o, inv = origin[axis], inverse[axis]
        if inv == math.inf:
            if o < lo[axis] or o > hi[axis]:
                return False
            continue
        t1, t2 = (lo[axis] - o) * inv, (hi[axis] - o) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > near:
            near = t1
        if t2 < far:
            far = t2
        if near > far:
            return False
    return True


def _triangle(origin, direction, a, b, c, epsilon=1e-12):
    """Moller-Trumbore for 0 <= t <= 1, front faces only.

    PhysX raycasts skip back faces unless asked (PxHitFlag::eMESH_BOTH_SIDES),
    so a line leaving a closed rock is not stopped by it. The front face is
    the one (b - a) x (c - a) points away from: every closed, consistently
    wound mesh of April2018's 599 has positive signed volume. Here
    det = -direction . normal, so a front-face hit has det > 0.
    """
    e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    e2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    p = (direction[1] * e2[2] - direction[2] * e2[1],
         direction[2] * e2[0] - direction[0] * e2[2],
         direction[0] * e2[1] - direction[1] * e2[0])
    det = e1[0] * p[0] + e1[1] * p[1] + e1[2] * p[2]
    if det < epsilon:
        return False
    inv = 1. / det
    s = (origin[0] - a[0], origin[1] - a[1], origin[2] - a[2])
    u = (s[0] * p[0] + s[1] * p[1] + s[2] * p[2]) * inv
    if u < 0. or u > 1.:
        return False
    q = (s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2], s[0] * e1[1] - s[1] * e1[0])
    v = (direction[0] * q[0] + direction[1] * q[1] + direction[2] * q[2]) * inv
    if v < 0. or u + v > 1.:
        return False
    t = (e2[0] * q[0] + e2[1] * q[1] + e2[2] * q[2]) * inv
    return 0. <= t <= 1.


def _invert(matrix):
    """The inverse of the affine x*c0 + y*c1 + z*c2 + t, in the same layout."""
    c0, c1, c2, t = matrix[0:3], matrix[3:6], matrix[6:9], matrix[9:12]
    m = ((c0[0], c1[0], c2[0]), (c0[1], c1[1], c2[1]), (c0[2], c1[2], c2[2]))
    det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
           - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
           + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    if abs(det) < 1e-12:
        return None
    inv = [[0.] * 3 for _ in range(3)]
    for row in range(3):
        for col in range(3):
            r = [i for i in range(3) if i != col]
            c = [i for i in range(3) if i != row]
            minor = m[r[0]][c[0]] * m[r[1]][c[1]] - m[r[0]][c[1]] * m[r[1]][c[0]]
            inv[row][col] = (-1) ** (row + col) * minor / det
    shift = tuple(-(inv[i][0] * t[0] + inv[i][1] * t[1] + inv[i][2] * t[2]) for i in range(3))
    return tuple(inv[i][j] for j in range(3) for i in range(3)) + shift


def _apply(matrix, point):
    x, y, z = point
    return tuple(x * matrix[i] + y * matrix[3 + i] + z * matrix[6 + i] + matrix[9 + i]
                 for i in range(3))


class World:
    """The placed colliders of one level, queried by segment."""

    def __init__(self, level, meshes, instances):
        self.level = level
        self.trees = [MeshTree(v, t) for v, t in meshes]
        self.instances = []
        self.grid = {}
        for mesh, layer, matrix in instances:
            inverse = _invert(matrix)
            tree = self.trees[mesh]
            if inverse is None or not tree.nodes:
                continue
            lo, hi = tree.nodes[0][0], tree.nodes[0][1]
            corners = [_apply(matrix, (x, y, z)) for x in (lo[0], hi[0])
                       for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
            box = (tuple(min(c[i] for c in corners) for i in range(3)),
                   tuple(max(c[i] for c in corners) for i in range(3)))
            number = len(self.instances)
            self.instances.append((tree, layer, inverse, box))
            for gx in range(int(box[0][0] // CELL), int(box[1][0] // CELL) + 1):
                for gy in range(int(box[0][1] // CELL), int(box[1][1] // CELL) + 1):
                    self.grid.setdefault((gx, gy), []).append(number)

    def _candidates(self, start, end):
        """Instances in the grid cells the segment's XY projection crosses."""
        seen = set()
        x0, y0, x1, y1 = start[0] / CELL, start[1] / CELL, end[0] / CELL, end[1] / CELL
        gx, gy = int(math.floor(x0)), int(math.floor(y0))
        last = (int(math.floor(x1)), int(math.floor(y1)))
        dx, dy = x1 - x0, y1 - y0
        step_x, step_y = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        t_dx = abs(1. / dx) if dx else math.inf
        t_dy = abs(1. / dy) if dy else math.inf
        t_x = ((gx + (dx > 0) - x0) / dx) if dx else math.inf
        t_y = ((gy + (dy > 0) - y0) / dy) if dy else math.inf
        for _ in range(4096):
            for number in self.grid.get((gx, gy), ()):
                if number not in seen:
                    seen.add(number)
                    yield number
            if (gx, gy) == last or min(t_x, t_y) > 1.:
                return
            if t_x < t_y:
                t_x += t_dx
                gx += step_x
            else:
                t_y += t_dy
                gy += step_y

    def blocked(self, start, end, mask=AWARENESS_LOS):
        """True when a collider on a layer in `mask` crosses start -> end."""
        direction = tuple(end[i] - start[i] for i in range(3))
        inverse_direction = tuple(1. / d if d else math.inf for d in direction)
        for number in self._candidates(start, end):
            tree, layer, inverse, (lo, hi) = self.instances[number]
            if not (mask >> layer) & 1 or not _slab(start, inverse_direction, lo, hi):
                continue
            origin = _apply(inverse, start)
            local_end = _apply(inverse, end)
            if tree.blocks(origin, tuple(local_end[i] - origin[i] for i in range(3))):
                return True
        return False


@functools.lru_cache(maxsize=2)
def installed_world(level='april2018'):
    """The cached World for a level, or None when no cache has been built."""
    path = cache_path(level)
    if not path.exists():
        return None
    _, meshes, instances = parse(path.read_bytes())
    return World(level, meshes, instances)


def write_cache(level, data):
    """Write a cache file privately (mode 600, its directory 700)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CACHE_DIR, 0o700)
    path = cache_path(level)
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'wb') as handle:
        os.chmod(temporary, 0o600)
        handle.write(data)
    os.replace(temporary, path)
    return path
