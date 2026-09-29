#!/usr/bin/env python3
"""Read-only prototype: the retail PhysX collision world of a Crucible level.

Crucible builds levels from Lumberyard PhysX-gem components, so the shipped
collision data is cooked PhysX, not CryPhysics/CGF proxies:

  * terrain   levels.pak:levels/<L>/terrain/terrain.pxheightfield
              (ObjectStream-less 8-byte prefix + PhysX 3.4 `NXS\\1HFHF` v1)
  * props     MeshColliderComponent {F3C7996A-...} -> objects.pak *.pxmesh
              (ObjectStream MeshAssetData wrapper around `NXS\\1MESH` v15 or
              `NXS\\1CVXM`)
  * primitives Box {881D85FC-...}, Sphere {108CD341-...}, Capsule {E0622390-...}
  * transforms TransformComponent world transform (4 big-endian float3
              columns: basis x, basis y, basis z, translation)
  * layers    misc.pak:default.physxconfiguration

Format authorities (public source):
  PhysX 3.4 PhysXCooking/src/mesh/TriangleMeshBuilder.cpp (::save),
            PhysXCooking/src/mesh/HeightFieldCooking.cpp (saveHeightField),
            PhysXCooking/src/convex/ConvexHullBuilder.cpp (::save),
            GeomUtils/src/mesh/GuMeshData.h (IMSF_* flags)
  Lumberyard 1.20 Gems/PhysX/Code/Source/Utils.cpp (CreateTerrain: local pose
            Rz(90)*Rx(90), heightScale=scale.z, rowScale=scale.x,
            columnScale=scale.y, actor at the origin)

Nothing installed is written and nothing extracted is saved except derived
metrics. Pure Python (no numpy) so the same code can move into server/.

Usage:
  python3 inspect_collision_geometry.py summary [--json OUT]
  python3 inspect_collision_geometry.py ray X,Y,Z X,Y,Z [--group AwarenessLoS]
  python3 inspect_collision_geometry.py navedges [--json OUT] [--limit N]
  python3 inspect_collision_geometry.py agreement [--json OUT] [--pairs N]
"""
import argparse
import collections
import json
import math
import mmap
from pathlib import Path
import random
import re
import struct
import sys
import time
import zlib

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS.parent / 'server'))
from objectstream import parse as parse_objectstream, crc, walk  # noqa: E402

GAME = Path.home() / '.local/share/Steam/steamapps/common/Crucible'

TRANSFORM = '22b10178-39b6-4c12-bb37-77db45fdd3b6'
MESH_COLLIDER = 'f3c7996a-f9b8-4afd-b2a1-6de971efda11'
BOX_COLLIDER = '881d85fc-7d85-4e4f-b58c-80bd4c94a51a'
SPHERE_COLLIDER = '108cd341-e5c3-4ae1-b712-21e81ed6c277'
CAPSULE_COLLIDER = 'e0622390-4e66-43d5-8f2f-5a928c9d1dcc'
COLLIDER_CONFIGURATION = '16206828-f867-4da9-9e4e-549b7b2c6174'
ASSET_REFERENCE = '77a19d40-8731-4d3c-9041-1b43047366a4'
COLLIDERS = {MESH_COLLIDER: 'mesh', BOX_COLLIDER: 'box',
             SPHERE_COLLIDER: 'sphere', CAPSULE_COLLIDER: 'capsule'}

# misc.pak:default.physxconfiguration, CollisionLayers / CollisionGroups.
LAYERS = {0: 'Default', 1: 'InvisibleGeo', 2: 'CustomGameplayCollision',
          3: 'MaterialOverride', 4: 'Characters', 5: 'WorldStaticDamageable',
          8: 'WorldStatic', 10: 'Interactable'}
GROUPS = {'All': 0xFFFFFFFFFFFFFFFF, 'AwarenessLoS': 0xFFFFFFFFFFFFFFE1,
          'Projectiles': 0xFFFFFFFFFFFFFFE5, 'VisibleGeo': 0xFFFFFFFFFFFFFFE5,
          'PlayerMovement': 0xFFFFFFFFFFFFFFFF}
HOLE = 127  # PxHeightFieldMaterial::eHOLE
ASSET_PAKS = ('objects.pak', 'architecture.pak', 'animations.pak', 'vfx.pak')


# --------------------------------------------------------------- pak access
class Pak:
    """Central-directory ZIP reader over mmap; reads one entry at a time.

    tools/pak.py reads the whole archive into memory, which is 2.5 GB for
    objects.pak. Same CryEngine quirk handling: names come from the central
    directory, data offsets from the local header's own name/extra lengths.
    """

    def __init__(self, path):
        self.file = open(path, 'rb')
        self.map = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        end = self.map.rfind(b'PK\x05\x06')
        if end < 0:
            raise ValueError('no end of central directory in %s' % path)
        count, _, offset = struct.unpack('<HII', self.map[end + 10:end + 20])
        self.entries = {}
        for _ in range(count):
            if self.map[offset:offset + 4] != b'PK\x01\x02':
                raise ValueError('bad central directory record')
            (method, _, _, _, size, _, name_len, extra_len, comment_len,
             _, _, _, local) = struct.unpack('<HHHIIIHHHHHII', self.map[offset + 10:offset + 46])
            name = self.map[offset + 46:offset + 46 + name_len].decode('utf-8', 'replace')
            self.entries[name.lower()] = (method, size, local)
            offset += 46 + name_len + extra_len + comment_len

    def read(self, name):
        method, size, local = self.entries[name.lower()]
        if self.map[local:local + 4] != b'PK\x03\x04':
            raise ValueError('bad local header for %s' % name)
        name_len, extra_len = struct.unpack('<HH', self.map[local + 26:local + 30])
        start = local + 30 + name_len + extra_len
        raw = self.map[start:start + size]
        if method == 8:
            return zlib.decompress(raw, -15)
        if method == 0:
            return bytes(raw)
        raise ValueError('unsupported method %d' % method)


# ------------------------------------------------------------ PhysX formats
def _header(data, pos, tag):
    if data[pos:pos + 4] != b'NXS\x01' or data[pos + 4:pos + 8] != tag:
        raise ValueError('expected NXS %r at %d' % (tag, pos))
    return struct.unpack_from('<I', data, pos + 8)[0], pos + 12


class Heightfield:
    """PhysX HFHF v1 samples mapped to Crucible world space.

    CreateTerrain applies local pose Rz(90)*Rx(90) with the actor at the
    origin, which sends PhysX row -> world +Y, column -> world +X and height
    -> world +Z. So world(x, y) reads sample[row=y/rowScale][col=x/colScale].
    """

    def __init__(self, data, scale):
        pos = data.find(b'NXS\x01HFHF')
        if pos < 0:
            raise ValueError('no HFHF block')
        version, pos = _header(data, pos, b'HFHF')
        if version != 1:
            raise ValueError('unsupported HFHF version %d' % version)
        (self.rows, self.columns, _, _, _, self.thickness, _,
         self.flags, self.format) = struct.unpack_from('<IIfffffHI', data, pos)
        pos += struct.calcsize('<IIfffffHI')
        self.bounds = struct.unpack_from('<6f', data, pos)
        pos += 24
        stride, count, self.min_height, self.max_height = struct.unpack_from('<IIff', data, pos)
        pos += 16
        if stride != 4 or count != self.rows * self.columns:
            raise ValueError('unexpected HFHF sample layout')
        raw = data[pos:pos + 4 * count]
        if len(raw) != 4 * count:
            raise ValueError('truncated HFHF samples')
        self.heights = struct.unpack('<' + 'hBB' * count, raw)  # h, m0, m1 flat
        self.trailing = len(data) - (pos + 4 * count)
        self.row_scale, self.column_scale, self.height_scale = scale

    def sample(self, row, column):
        i = 3 * (row * self.columns + column)
        return self.heights[i], self.heights[i + 1], self.heights[i + 2]

    def vertex(self, row, column):
        return (column * self.column_scale, row * self.row_scale,
                self.heights[3 * (row * self.columns + column)] * self.height_scale)

    def cell_triangles(self, row, column):
        """The (up to) two solid triangles of cell (row, column), world space.

        PhysX: bit 7 of material0 is the tessellation flag. When set, the
        diagonal joins vertex 0 (r,c) and vertex 3 (r+1,c+1); otherwise it
        joins vertex 1 (r+1,c) and vertex 2 (r,c+1). Material 127 = hole.
        """
        if not (0 <= row < self.rows - 1 and 0 <= column < self.columns - 1):
            return ()
        _, m0, m1 = self.sample(row, column)
        v0, v1 = self.vertex(row, column), self.vertex(row + 1, column)
        v2, v3 = self.vertex(row, column + 1), self.vertex(row + 1, column + 1)
        if m0 & 0x80:
            pair = ((v0, v1, v3), (v0, v3, v2))
        else:
            pair = ((v0, v1, v2), (v1, v3, v2))
        return tuple(t for t, m in zip(pair, (m0 & 0x7f, m1 & 0x7f)) if m != HOLE)

    def height(self, x, y):
        """Surface height at world (x, y), or None over a hole / off the grid."""
        column, row = x / self.column_scale, y / self.row_scale
        c, r = math.floor(column), math.floor(row)
        for triangle in self.cell_triangles(r, c):
            z = _plan_height(triangle, x, y)
            if z is not None:
                return z
        return None


def _plan_height(triangle, x, y):
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = triangle
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-12:
        return None
    u = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d
    v = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d
    if min(u, v, 1 - u - v) < -1e-6:
        return None
    return u * az + v * bz + (1 - u - v) * cz


def read_cooked(data):
    """Cooked PhysX blob -> ('mesh', vertices, triangles) or ('convex', vertices, planes)."""
    pos = data.find(b'NXS\x01')
    if pos < 0:
        raise ValueError('no NXS block')
    tag = data[pos + 4:pos + 8]
    if tag == b'MESH':
        version, pos = _header(data, pos, b'MESH')
        if version != 15:
            raise ValueError('unsupported MESH version %d' % version)
        _midphase, flags, nv, nt = struct.unpack_from('<IIII', data, pos)
        pos += 16
        flat = struct.unpack_from('<%df' % (3 * nv), data, pos)
        pos += 12 * nv
        if flags & 4:
            idx = struct.unpack_from('<%dB' % (3 * nt), data, pos); pos += 3 * nt
        elif flags & 8:
            idx = struct.unpack_from('<%dH' % (3 * nt), data, pos); pos += 6 * nt
        else:
            idx = struct.unpack_from('<%dI' % (3 * nt), data, pos); pos += 12 * nt
        if idx and max(idx) >= nv:
            raise ValueError('MESH index out of range')
        vertices = [flat[i:i + 3] for i in range(0, len(flat), 3)]
        triangles = [idx[i:i + 3] for i in range(0, len(idx), 3)]
        return 'mesh', vertices, triangles
    if tag == b'CVXM':
        _, pos = _header(data, pos, b'CVXM')
        pos += 4  # serial flags
        _, pos = _header(data, pos, b'CLHL')
        _, pos = _header(data, pos, b'CVHL')
        nv, ne, npoly, _nref = struct.unpack_from('<IIII', data, pos)
        pos += 16
        flat = struct.unpack_from('<%df' % (3 * nv), data, pos)
        pos += 12 * nv
        planes = []
        for _ in range(npoly):  # Gu::HullPolygonData: PxPlane, u16 vref8, u8 nb, u8 min
            planes.append(struct.unpack_from('<4f', data, pos))
            pos += 20
        return 'convex', [flat[i:i + 3] for i in range(0, len(flat), 3)], planes
    raise ValueError('unknown cooked tag %r' % tag)


def read_pxmesh(data):
    """Lumberyard MeshAssetData wrapper: the cooked blob is one element."""
    roots = parse_objectstream(data)
    blobs = [n.value for n in walk(roots) if n.value[4:8] == b'NXS\x01'
             or n.value[:4] == b'NXS\x01']
    if len(blobs) != 1:
        raise ValueError('expected one cooked blob, found %d' % len(blobs))
    return read_cooked(blobs[0])


# ----------------------------------------------------------- level colliders
def _floats(node, count):
    return struct.unpack('>%df' % count, node.value)


def _hint(node):
    """Asset hint path stored at the tail of an Asset<T> reference.

    Layout observed: AssetId(guid16 + subId u32) + asset type guid16 + ... +
    big-endian u64 hint length + hint bytes (end of value).
    """
    value = node.value
    for size in range(1, min(len(value) - 8, 512)):
        if int.from_bytes(value[-size - 8:-size], 'big') == size:
            text = value[-size:]
            if re.fullmatch(rb'[\x20-\x7e]+', text):
                return text.decode()
    return None


def level_colliders(slice_data):
    """Every PhysX collider in a level slice with its world placement."""
    roots = parse_objectstream(slice_data)
    array = roots[0].child('components').children[0].child('entities').children
    out = []
    for index, entity in enumerate(array):
        components = entity.child('components')
        if not components:
            continue
        by_type = {c.type_id: c for c in components.children}
        transform = by_type.get(TRANSFORM)
        if transform is None:
            continue
        world = transform.child('transform')
        if world is None or len(world.value) != 48:
            continue
        matrix = _floats(world, 12)
        name = entity.child('name')
        name = name.value.decode('utf-8', 'replace') if name else ''
        for type_id, kind in COLLIDERS.items():
            component = by_type.get(type_id)
            if component is None:
                continue
            configuration = next(n for n in walk([component]) if n.type_id == COLLIDER_CONFIGURATION)
            layer = configuration.child('collisionlayer').children[0].value[0]
            trigger = configuration.child('trigger').value[0] != 0
            position = _floats(configuration.child('position'), 3)
            rotation = _floats(configuration.child('rotation'), 4)
            record = dict(entry=index, name=name, kind=kind, layer=layer,
                          trigger=trigger, matrix=matrix, position=position,
                          rotation=rotation)
            scales = [n for n in walk([component]) if n.name == crc('scale')]
            record['shape_scale'] = _floats(scales[0], 3) if scales else (1., 1., 1.)
            if kind == 'mesh':
                asset = next((n for n in walk([component])
                              if n.type_id == ASSET_REFERENCE and _hint(n)
                              and _hint(n).endswith('.pxmesh')), None)
                record['asset'] = _hint(asset) if asset else None
                extra = [n for n in walk([component]) if n.name == 0x55079057]
                record['asset_scale'] = _floats(extra[0], 3) if extra else (1., 1., 1.)
            elif kind == 'box':
                dims = [n for n in walk([component]) if n.name == crc('configuration')
                        and len(n.value) == 12]
                record['dimensions'] = _floats(dims[0], 3)
            else:
                radius = [n for n in walk([component]) if n.name == crc('radius')]
                height = [n for n in walk([component]) if n.name == crc('height')]
                record['radius'] = _floats(radius[0], 1)[0] if radius else 0.5
                record['height'] = _floats(height[0], 1)[0] if height else 0.
            out.append(record)
    return out


def terrain_scale(slice_data):
    """(rowScale, columnScale, heightScale) from the PhysX Terrain entity."""
    roots = parse_objectstream(slice_data)
    for node in walk(roots):
        if node.type_id == '0b545165-8629-4237-842a-59f1fe3fb6fa':  # TerrainConfiguration
            x, y, z = _floats(node.child('scale'), 3)
            return x, y, z
    raise ValueError('no PhysX TerrainConfiguration in slice')


# ------------------------------------------------------------------ geometry
def _mat_apply(m, p):
    return (m[0] * p[0] + m[3] * p[1] + m[6] * p[2] + m[9],
            m[1] * p[0] + m[4] * p[1] + m[7] * p[2] + m[10],
            m[2] * p[0] + m[5] * p[1] + m[8] * p[2] + m[11])


def _mat_inverse(m):
    a, b, c = m[0:3], m[3:6], m[6:9]  # columns
    det = (a[0] * (b[1] * c[2] - b[2] * c[1]) - b[0] * (a[1] * c[2] - a[2] * c[1])
           + c[0] * (a[1] * b[2] - a[2] * b[1]))
    if abs(det) < 1e-12:
        return None
    # inverse rows = cross products / det
    r0 = [(b[1] * c[2] - b[2] * c[1]) / det, (b[2] * c[0] - b[0] * c[2]) / det, (b[0] * c[1] - b[1] * c[0]) / det]
    r1 = [(c[1] * a[2] - c[2] * a[1]) / det, (c[2] * a[0] - c[0] * a[2]) / det, (c[0] * a[1] - c[1] * a[0]) / det]
    r2 = [(a[1] * b[2] - a[2] * b[1]) / det, (a[2] * b[0] - a[0] * b[2]) / det, (a[0] * b[1] - a[1] * b[0]) / det]
    t = m[9:12]
    tx = -(r0[0] * t[0] + r0[1] * t[1] + r0[2] * t[2])
    ty = -(r1[0] * t[0] + r1[1] * t[1] + r1[2] * t[2])
    tz = -(r2[0] * t[0] + r2[1] * t[1] + r2[2] * t[2])
    # back to column layout
    return (r0[0], r1[0], r2[0], r0[1], r1[1], r2[1], r0[2], r1[2], r2[2], tx, ty, tz)


def _quat_matrix(q):
    x, y, z, w = q
    return (1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w),
            2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w),
            2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y))


def instance_matrix(record):
    """Local asset space -> world, per the PhysX gem's shape construction.

    Actor pose = entity rotation+translation (scale removed); shape local pose
    = collider position/rotation; geometry scale = shape scale (the entity's
    column lengths, verified) * asset scale.
    """
    m = record['matrix']
    columns = [m[0:3], m[3:6], m[6:9]]
    lengths = [math.sqrt(sum(v * v for v in col)) or 1. for col in columns]
    unit = [tuple(v / l for v in col) for col, l in zip(columns, lengths)]
    r = _quat_matrix(record['rotation'])
    rc = [r[0:3], r[3:6], r[6:9]]
    scale = [s * a for s, a in zip(record['shape_scale'], record.get('asset_scale', (1., 1., 1.)))]

    def rotate(v):
        return tuple(unit[0][i] * v[0] + unit[1][i] * v[1] + unit[2][i] * v[2] for i in range(3))
    out_cols = []
    for axis in range(3):
        local = tuple(rc[axis][i] * scale[axis] for i in range(3))
        out_cols.append(rotate(local))
    offset = rotate(record['position'])
    t = tuple(m[9 + i] + offset[i] for i in range(3))
    return (*out_cols[0], *out_cols[1], *out_cols[2], *t)


def _segment_aabb(p, d, lo, hi, t_max=1.0):
    t0, t1 = 0.0, t_max
    for i in range(3):
        if abs(d[i]) < 1e-12:
            if p[i] < lo[i] or p[i] > hi[i]:
                return None
            continue
        a, b = (lo[i] - p[i]) / d[i], (hi[i] - p[i]) / d[i]
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return None
    return t0


def _segment_triangle(p, d, a, b, c):
    """Moller-Trumbore on segment p + t d, t in [0,1]; double sided."""
    e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    e2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    h = (d[1] * e2[2] - d[2] * e2[1], d[2] * e2[0] - d[0] * e2[2], d[0] * e2[1] - d[1] * e2[0])
    det = e1[0] * h[0] + e1[1] * h[1] + e1[2] * h[2]
    if abs(det) < 1e-12:
        return None
    inv = 1.0 / det
    s = (p[0] - a[0], p[1] - a[1], p[2] - a[2])
    u = inv * (s[0] * h[0] + s[1] * h[1] + s[2] * h[2])
    if u < 0 or u > 1:
        return None
    q = (s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2], s[0] * e1[1] - s[1] * e1[0])
    v = inv * (d[0] * q[0] + d[1] * q[1] + d[2] * q[2])
    if v < 0 or u + v > 1:
        return None
    t = inv * (e2[0] * q[0] + e2[1] * q[1] + e2[2] * q[2])
    return t if 0 <= t <= 1 else None


class Shape:
    """One cooked asset in its own space with a flat BVH over triangles."""

    LEAF = 8

    def __init__(self, kind, vertices, payload):
        self.kind, self.vertices = kind, vertices
        if kind == 'convex':
            self.planes = payload
            self.triangles = []
        else:
            self.triangles = payload
        pts = vertices or [(0, 0, 0)]
        self.lo = tuple(min(v[i] for v in pts) for i in range(3))
        self.hi = tuple(max(v[i] for v in pts) for i in range(3))
        self.nodes = self._build() if kind == 'mesh' else None

    def _build(self):
        vs = self.vertices
        items = []
        for t in self.triangles:
            a, b, c = vs[t[0]], vs[t[1]], vs[t[2]]
            lo = tuple(min(a[i], b[i], c[i]) for i in range(3))
            hi = tuple(max(a[i], b[i], c[i]) for i in range(3))
            items.append((lo, hi, (a, b, c)))
        nodes = []

        def build(chunk):
            lo = tuple(min(x[0][i] for x in chunk) for i in range(3))
            hi = tuple(max(x[1][i] for x in chunk) for i in range(3))
            slot = len(nodes)
            nodes.append(None)
            if len(chunk) <= self.LEAF:
                nodes[slot] = (lo, hi, [x[2] for x in chunk], None, None)
                return slot
            axis = max(range(3), key=lambda i: hi[i] - lo[i])
            chunk.sort(key=lambda x: x[0][axis] + x[1][axis])
            half = len(chunk) // 2
            left, right = build(chunk[:half]), build(chunk[half:])
            nodes[slot] = (lo, hi, None, left, right)
            return slot
        if items:
            build(items)
        return nodes

    def raycast(self, p, d):
        if _segment_aabb(p, d, self.lo, self.hi) is None:
            return None
        if self.kind == 'convex':
            t0, t1 = 0.0, 1.0
            for nx, ny, nz, dist in self.planes:
                num = -(nx * p[0] + ny * p[1] + nz * p[2] + dist)
                den = nx * d[0] + ny * d[1] + nz * d[2]
                if abs(den) < 1e-12:
                    if num < 0:
                        return None
                    continue
                t = num / den
                if den < 0:
                    t0 = max(t0, t)
                else:
                    t1 = min(t1, t)
                if t0 > t1:
                    return None
            return t0
        best, stack = None, [0] if self.nodes else []
        while stack:
            lo, hi, tris, left, right = self.nodes[stack.pop()]
            enter = _segment_aabb(p, d, lo, hi, best if best is not None else 1.0)
            if enter is None:
                continue
            if tris is not None:
                for a, b, c in tris:
                    t = _segment_triangle(p, d, a, b, c)
                    if t is not None and (best is None or t < best):
                        best = t
            else:
                stack.extend((left, right))
        return best


class World:
    CELL = 16.0

    def __init__(self, heightfield=None):
        self.heightfield = heightfield
        self.instances = []  # (lo, hi, matrix, inverse, shape, record)
        self.grid = collections.defaultdict(list)

    def add(self, shape, matrix, record):
        inverse = _mat_inverse(matrix)
        if inverse is None:
            return
        corners = [_mat_apply(matrix, (x, y, z)) for x in (shape.lo[0], shape.hi[0])
                   for y in (shape.lo[1], shape.hi[1]) for z in (shape.lo[2], shape.hi[2])]
        lo = tuple(min(c[i] for c in corners) for i in range(3))
        hi = tuple(max(c[i] for c in corners) for i in range(3))
        slot = len(self.instances)
        self.instances.append((lo, hi, matrix, inverse, shape, record))
        for gx in range(math.floor(lo[0] / self.CELL), math.floor(hi[0] / self.CELL) + 1):
            for gy in range(math.floor(lo[1] / self.CELL), math.floor(hi[1] / self.CELL) + 1):
                self.grid[(gx, gy)].append(slot)

    def _cells(self, a, b):
        """2D grid cells touched by segment a->b (conservative DDA)."""
        x0, y0, x1, y1 = a[0] / self.CELL, a[1] / self.CELL, b[0] / self.CELL, b[1] / self.CELL
        steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
        seen = []
        for i in range(steps + 1):
            f = i / steps
            x, y = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
            for cx in (math.floor(x - 0.01), math.floor(x + 0.01)):
                for cy in (math.floor(y - 0.01), math.floor(y + 0.01)):
                    if (cx, cy) not in seen:
                        seen.append((cx, cy))
        return seen

    def raycast(self, a, b, mask=GROUPS['AwarenessLoS'], terrain=True):
        """First hit on a->b: (t in [0,1], point, description) or None."""
        d = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        best = None
        tested = set()
        for cell in self._cells(a, b):
            for slot in self.grid.get(cell, ()):
                if slot in tested:
                    continue
                tested.add(slot)
                lo, hi, matrix, inverse, shape, record = self.instances[slot]
                if not mask >> record['layer'] & 1 or record['trigger']:
                    continue
                if _segment_aabb(a, d, lo, hi, best[0] if best else 1.0) is None:
                    continue
                pa, pb = _mat_apply(inverse, a), _mat_apply(inverse, b)
                t = shape.raycast(pa, (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]))
                if t is not None and (best is None or t < best[0]):
                    best = (t, record)
        if terrain and self.heightfield is not None:
            t = self._terrain(a, d, best[0] if best else 1.0)
            if t is not None:
                best = (t, {'kind': 'terrain', 'name': 'PhysX Terrain', 'layer': 0})
        if best is None:
            return None
        t, record = best
        point = tuple(a[i] + d[i] * t for i in range(3))
        return t, point, record

    def _terrain(self, a, d, limit):
        hf = self.heightfield
        length = math.hypot(d[0], d[1])
        steps = max(1, int(length / min(hf.row_scale, hf.column_scale) * 2) + 1)
        seen = set()
        best = None
        for i in range(steps + 1):
            f = i / steps
            x, y = a[0] + d[0] * f, a[1] + d[1] * f
            for c in (math.floor(x / hf.column_scale - 1e-4), math.floor(x / hf.column_scale + 1e-4)):
                for r in (math.floor(y / hf.row_scale - 1e-4), math.floor(y / hf.row_scale + 1e-4)):
                    if (r, c) in seen:
                        continue
                    seen.add((r, c))
                    for tri in hf.cell_triangles(r, c):
                        t = _segment_triangle(a, d, *tri)
                        if t is not None and t <= limit and (best is None or t < best):
                            best = t
        return best

    def line_of_sight(self, a, b, mask=GROUPS['AwarenessLoS']):
        return self.raycast(a, b, mask) is None


# ------------------------------------------------------------------- loading
def load_level(level='april2018', progress=print):
    levels = Pak(GAME / 'levels.pak')
    # .pxmesh assets live beside their render meshes: objects.pak, but also
    # architecture.pak (147 of April2018's 636), animations.pak and vfx.pak.
    asset_paks = [Pak(GAME / name) for name in ASSET_PAKS]

    def read_asset(path):
        for pak in asset_paks:
            if path.lower() in pak.entries:
                return pak.read(path)
        raise KeyError(path)
    started = time.time()
    slice_data = levels.read('levels/%s/commondata/level.dynamicslice' % level)
    colliders = level_colliders(slice_data)
    progress('colliders: %d in %.1fs' % (len(colliders), time.time() - started))
    hf = Heightfield(levels.read('levels/%s/terrain/terrain.pxheightfield' % level),
                     terrain_scale(slice_data))
    progress('heightfield: %dx%d in %.1fs' % (hf.rows, hf.columns, time.time() - started))
    world = World(hf)
    shapes, failures = {}, collections.Counter()
    for record in colliders:
        if record['kind'] == 'mesh':
            path = record.get('asset')
            if path is None:
                failures['no asset reference'] += 1
                continue
            if path not in shapes:
                try:
                    kind, vertices, payload = read_pxmesh(read_asset(path))
                    shapes[path] = Shape(kind, vertices, payload)
                except KeyError:
                    shapes[path] = None
                    failures['missing asset'] += 1
                except (ValueError, struct.error) as error:
                    shapes[path] = None
                    failures[str(error)[:60]] += 1
            shape = shapes[path]
            if shape is None:
                continue
            world.add(shape, instance_matrix(record), record)
        elif record['kind'] == 'box':
            hx, hy, hz = (v / 2 for v in record['dimensions'])
            planes = [(1, 0, 0, -hx), (-1, 0, 0, -hx), (0, 1, 0, -hy), (0, -1, 0, -hy),
                      (0, 0, 1, -hz), (0, 0, -1, -hz)]
            corners = [(x, y, z) for x in (-hx, hx) for y in (-hy, hy) for z in (-hz, hz)]
            world.add(Shape('convex', corners, planes), instance_matrix(record), record)
        # spheres/capsules: 47 solid ones in April2018, omitted by the prototype
    progress('world: %d instances, %d unique shapes in %.1fs' % (
        len(world.instances), sum(1 for s in shapes.values() if s), time.time() - started))
    world.colliders, world.shapes, world.failures = colliders, shapes, failures
    return world


# ------------------------------------------------------------------ commands
def cmd_summary(args):
    world = load_level(args.level)
    colliders, shapes = world.colliders, world.shapes
    hf = world.heightfield
    by = collections.Counter((c['kind'], LAYERS.get(c['layer'], str(c['layer'])), c['trigger'])
                             for c in colliders)
    kinds = collections.Counter(s.kind for s in shapes.values() if s)
    tri_counts = sorted(len(s.triangles) for s in shapes.values() if s and s.kind == 'mesh')
    instanced = sum(len(inst[4].triangles) for inst in world.instances)
    solid = [inst for inst in world.instances if not inst[5]['trigger']
             and GROUPS['AwarenessLoS'] >> inst[5]['layer'] & 1]
    holes = sum(1 for i in range(1, len(hf.heights), 3) if hf.heights[i] & 0x7f == HOLE)
    # transform-layout proof: shape scale must equal column (not row) norms
    proof = collections.Counter()
    for c in colliders:
        m = c['matrix']
        col = [math.sqrt(sum(v * v for v in m[3 * j:3 * j + 3])) for j in range(3)]
        row = [math.sqrt(m[j] ** 2 + m[3 + j] ** 2 + m[6 + j] ** 2) for j in range(3)]
        if max(abs(col[j] - row[j]) for j in range(3)) < 1e-2:
            continue
        s = c['shape_scale']
        proof['columns' if max(abs(s[j] - col[j]) for j in range(3)) < 1e-3 else
              'rows' if max(abs(s[j] - row[j]) for j in range(3)) < 1e-3 else 'neither'] += 1
    samples = {}  # authored z: 17d 23.670, 17c 22.709, 17a 23.625, 17b 20.875
    for label, (x, y) in {'Player_DropPoint_17d': (1312.711, 1271.156),
                          'Player_DropPoint_17c': (1316.273, 1257.578),
                          'Player_DropPoint_17a': (1313.471, 1264.975),
                          'Player_DropPoint_17b': (1318.480, 1248.327)}.items():
        samples[label] = dict(xy=round(hf.height(x, y), 3),
                              transposed=round(hf.height(y, x) or float('nan'), 3))
    result = dict(
        level=args.level,
        heightfield=dict(rows=hf.rows, columns=hf.columns, format=hf.format,
                         thickness=hf.thickness, scale=[hf.row_scale, hf.column_scale, hf.height_scale],
                         min_height_m=hf.min_height * hf.height_scale,
                         max_height_m=hf.max_height * hf.height_scale,
                         hole_cells=holes, cells=(hf.rows - 1) * (hf.columns - 1),
                         trailing_bytes=hf.trailing,
                         drop_point_heights=samples),
        colliders={'%s/%s/%s' % (k[0], k[1], 'trigger' if k[2] else 'solid'): v
                   for k, v in sorted(by.items(), key=lambda x: -x[1])},
        unique_pxmesh=len(shapes), decoded=dict(kinds), failures=dict(world.failures),
        unique_mesh_triangles=dict(total=sum(tri_counts), median=tri_counts[len(tri_counts) // 2] if tri_counts else 0,
                                   max=max(tri_counts) if tri_counts else 0),
        instances=len(world.instances), los_blocking_instances=len(solid),
        instanced_triangles=instanced,
        transform_layout_proof=dict(proof),
    )
    text = json.dumps(result, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + '\n')


def _point(text):
    return tuple(float(v) for v in text.split(','))


def cmd_ray(args):
    world = load_level(args.level)
    hit = world.raycast(_point(args.a), _point(args.b), GROUPS[args.group])
    print('clear' if hit is None else 'hit t=%.3f at (%.2f, %.2f, %.2f) by %s %s (layer %s)' % (
        hit[0], *hit[1], hit[2]['kind'], hit[2]['name'], hit[2]['layer']))


def _boundary_edges(mesh):
    """Navmesh triangle edges that carry no walkable link (plan-view test)."""
    out = []
    for key, tri in mesh.triangles.items():
        portals = [p for _, p in mesh.links.get(key, ())]
        for e in range(3):
            a, b = tri[e], tri[(e + 1) % 3]
            length = math.dist(a[:2], b[:2])
            if length < 0.2:
                continue
            covered = 0.0
            for p, q in portals:
                mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                cross = (b[0] - a[0]) * (mid[1] - a[1]) - (b[1] - a[1]) * (mid[0] - a[0])
                if abs(cross) / length < 0.02:
                    covered += math.dist(p[:2], q[:2])
            if covered < 0.5 * length:
                out.append((key, e, a, b))
    return out


def cmd_navedges(args):
    import navigation
    world = load_level(args.level)
    mesh = navigation.installed_mesh()
    edges = _boundary_edges(mesh)
    rng = random.Random(1)
    sample = edges if args.limit <= 0 or args.limit >= len(edges) else rng.sample(edges, args.limit)
    classes = collections.Counter()
    by_hit = collections.Counter()
    started = time.time()
    for key, e, a, b in sample:
        tri = mesh.triangles[key]
        centre = mesh.centres[key]
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
        nx, ny = b[1] - a[1], -(b[0] - a[0])
        norm = math.hypot(nx, ny)
        nx, ny = nx / norm, ny / norm
        if nx * (mid[0] - centre[0]) + ny * (mid[1] - centre[1]) < 0:
            nx, ny = -nx, -ny
        blocked = {}
        for h in (0.5, 1.0, 1.7):
            start = (mid[0] - 0.1 * nx, mid[1] - 0.1 * ny, mid[2] + h)
            end = (mid[0] + args.probe * nx, mid[1] + args.probe * ny, mid[2] + h)
            hit = world.raycast(start, end, GROUPS['AwarenessLoS'])
            blocked[h] = hit
            if hit:
                by_hit[(h, hit[2]['kind'])] += 1
        if blocked[1.7] and blocked[1.0]:
            cls = 'tall_obstacle (blocks 1.0 m and 1.7 m)'
        elif blocked[1.0] or blocked[0.5]:
            cls = 'low_obstacle (blocks <=1.0 m, clear at 1.7 m)'
        elif blocked[1.7]:
            cls = 'overhang (blocks 1.7 m only)'
        else:
            ground = world.heightfield.height(mid[0] + args.probe * nx, mid[1] + args.probe * ny)
            if ground is None:
                cls = 'open: terrain hole / off map'
            elif ground < mid[2] - 0.5:
                cls = 'open: ledge (drop > 0.5 m)'
            elif ground > mid[2] + 0.5:
                cls = 'open: rising ground (> 0.5 m, below 0.5 m probe)'
            else:
                cls = 'open: level ground (steep/unwalkable or agent margin)'
        classes[cls] += 1
    total = len(sample)
    result = dict(level=args.level, boundary_edges=len(edges), sampled=total,
                  probe_m=args.probe, seconds=round(time.time() - started, 1),
                  classes={k: dict(count=v, fraction=round(v / total, 4))
                           for k, v in classes.most_common()},
                  hits_by_height_and_kind={'%s m/%s' % k: v for k, v in sorted(by_hit.items())})
    text = json.dumps(result, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + '\n')


def _navmesh_los(mesh, a, b):
    """MNM-style plan-view raycast: walk linked triangles from a towards b."""
    start = mesh.locate(a)
    goal = mesh.locate(b)
    if start is None or goal is None:
        return None
    key, target = start[0], goal[0]
    visited = 0
    px, py = a[0], a[1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    t_in = 0.0
    while visited < 4000:
        visited += 1
        if key == target:
            return True
        tri = mesh.triangles[key]
        exit_t, exit_edge = None, None
        for e in range(3):
            p, q = tri[e], tri[(e + 1) % 3]
            ex, ey = q[0] - p[0], q[1] - p[1]
            den = dx * ey - dy * ex
            if abs(den) < 1e-12:
                continue
            t = ((p[0] - px) * ey - (p[1] - py) * ex) / den
            s = ((p[0] - px) * dy - (p[1] - py) * dx) / den
            if t > t_in + 1e-9 and -1e-6 <= s <= 1 + 1e-6 and (exit_t is None or t < exit_t):
                exit_t, exit_edge = t, e
        if exit_t is None or exit_t >= 1.0:
            # segment ends inside this triangle but it is not the goal's
            return key == target or mesh.locate(b, 2.0) is not None and \
                _plan_height(tri, b[0], b[1]) is not None
        x, y = px + dx * exit_t, py + dy * exit_t
        nxt = None
        for other, (p, q) in mesh.links.get(key, ()):
            if math.dist(p[:2], q[:2]) < 1e-6:
                continue
            ux, uy = q[0] - p[0], q[1] - p[1]
            f = ((x - p[0]) * ux + (y - p[1]) * uy) / (ux * ux + uy * uy)
            off = abs((x - p[0]) * uy - (y - p[1]) * ux) / math.hypot(ux, uy)
            if -0.02 <= f <= 1.02 and off < 0.05:
                nxt = other
                break
        if nxt is None:
            return False
        key, t_in = nxt, exit_t
    return False


def cmd_agreement(args):
    import navigation
    world = load_level(args.level)
    mesh = navigation.installed_mesh()
    main = list(mesh.components()[0])
    rng = random.Random(7)
    confusion = collections.Counter()
    examples = collections.defaultdict(list)
    started = time.time()
    tries = 0
    while sum(confusion.values()) < args.pairs and tries < args.pairs * 20:
        tries += 1
        k1 = rng.choice(main)
        c1 = mesh.centres[k1]
        angle = rng.uniform(-math.pi, math.pi)
        dist = rng.uniform(args.min, args.max)
        found = mesh.locate((c1[0] + math.cos(angle) * dist, c1[1] + math.sin(angle) * dist, c1[2]), 3.0)
        if found is None:
            continue
        a, b = mesh.centres[k1], found[1]
        eye_a, eye_b = (a[0], a[1], a[2] + 1.7), (b[0], b[1], b[2] + 1.7)
        geo = world.line_of_sight(eye_a, eye_b)
        nav = _navmesh_los(mesh, a, b)
        if nav is None:
            continue
        key = ('geometry_clear' if geo else 'geometry_blocked',
               'navmesh_clear' if nav else 'navmesh_blocked')
        confusion[key] += 1
        if len(examples[key]) < 5:
            examples[key].append([[round(v, 2) for v in eye_a], [round(v, 2) for v in eye_b]])
    total = sum(confusion.values())
    agree = confusion[('geometry_clear', 'navmesh_clear')] + confusion[('geometry_blocked', 'navmesh_blocked')]
    result = dict(level=args.level, pairs=total, distance_m=[args.min, args.max],
                  eye_height_m=1.7, seconds=round(time.time() - started, 1),
                  agreement=round(agree / total, 4) if total else None,
                  confusion={'%s/%s' % k: v for k, v in sorted(confusion.items())},
                  examples={'%s/%s' % k: v for k, v in sorted(examples.items())})
    text = json.dumps(result, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + '\n')


def cmd_validate(args):
    """Cross-check placement against the independently baked MNM navmesh.

    The navmesh was voxelised by the editor from this same collision world, so
    with correct transforms (a) a short downward ray from above a random
    navmesh point lands on the collision surface close to the navmesh height,
    and (b) the 1.8 m column above it is mostly free. Wrong axis order,
    rotation or scale would break both, especially over prop floors (bridges,
    catwalks) where no terrain exists.
    """
    import navigation
    world = load_level(args.level)
    mesh = navigation.installed_mesh()
    keys = list(mesh.triangles)
    rng = random.Random(3)
    floor = collections.Counter()
    errors = collections.defaultdict(list)
    headroom = collections.Counter()
    for _ in range(args.points):
        tri = mesh.triangles[rng.choice(keys)]
        u, v = rng.random(), rng.random()
        if u + v > 1:
            u, v = 1 - u, 1 - v
        p = tuple(tri[0][i] + u * (tri[1][i] - tri[0][i]) + v * (tri[2][i] - tri[0][i]) for i in range(3))
        hit = world.raycast((p[0], p[1], p[2] + 1.0), (p[0], p[1], p[2] - 1.0), GROUPS['PlayerMovement'])
        if hit is None:
            floor['no surface within 1 m'] += 1
            continue
        kind = hit[2]['kind']
        error = hit[1][2] - p[2]
        errors[kind].append(abs(error))
        floor['%s within 0.3 m' % kind if abs(error) <= 0.3 else '%s off by > 0.3 m' % kind] += 1
        up = world.raycast((p[0], p[1], p[2] + 0.3), (p[0], p[1], p[2] + 1.8), GROUPS['PlayerMovement'])
        headroom['clear' if up is None else 'blocked by %s' % up[2]['kind']] += 1
    stats = {}
    for kind, values in errors.items():
        values.sort()
        stats[kind] = dict(n=len(values), median=round(values[len(values) // 2], 3),
                           p90=round(values[int(len(values) * 0.9)], 3))
    result = dict(level=args.level, points=args.points, floor=dict(floor.most_common()),
                  abs_height_error_m=stats, headroom_0p3_to_1p8_m=dict(headroom.most_common()))
    text = json.dumps(result, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--level', default='april2018')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('summary'); p.add_argument('--json'); p.set_defaults(func=cmd_summary)
    p = sub.add_parser('ray'); p.add_argument('a'); p.add_argument('b')
    p.add_argument('--group', default='AwarenessLoS', choices=sorted(GROUPS)); p.set_defaults(func=cmd_ray)
    p = sub.add_parser('navedges'); p.add_argument('--json'); p.add_argument('--limit', type=int, default=4000)
    p.add_argument('--probe', type=float, default=1.5); p.set_defaults(func=cmd_navedges)
    p = sub.add_parser('validate'); p.add_argument('--json'); p.add_argument('--points', type=int, default=2000)
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser('agreement'); p.add_argument('--json'); p.add_argument('--pairs', type=int, default=1000)
    p.add_argument('--min', type=float, default=5.0); p.add_argument('--max', type=float, default=40.0)
    p.set_defaults(func=cmd_agreement)
    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
