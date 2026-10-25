#!/usr/bin/env python3
"""Build the static line-of-sight cache for one level (docs/36).

Reads, and never writes, the installed game:
- levels.pak: levels/<level>/commondata/level.dynamicslice, whose PhysX
  collider components (mesh, box, sphere, capsule) carry each collider's
  layer, trigger flag, local offset and entity transform;
- objects.pak, architecture.pak, animations.pak and vfx.pak: the cooked
  PhysX 3.4 triangle meshes (.pxmesh) the mesh colliders name.

Writes ~/.cache/crucible-revival/static-geometry-<level>.bin (mode 600),
which the shard loads with CRUCIBLE_SOLO_LOS=static. Parsing the level slice
takes about 20 s and 30 s of CPU, so run it at idle priority:

  nice -n 19 ionice -c3 python3 tools/build_static_geometry.py april2018
"""
import argparse
import collections
import math
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'server'))
import inspect_map_entities as entities  # noqa: E402
import objectstream  # noqa: E402
import pak  # noqa: E402
import static_geometry  # noqa: E402

INSTALL = Path.home() / '.local/share/Steam/steamapps/common/Crucible'
MESH_PAKS = ('objects.pak', 'architecture.pak', 'animations.pak', 'vfx.pak')
COLLIDERS = {'f3c7996a-f9b8-4afd-b2a1-6de971efda11': 'mesh',
             '881d85fc-7d85-4e4f-b58c-80bd4c94a51a': 'box',
             '108cd341-e5c3-4ae1-b712-21e81ed6c277': 'sphere',
             'e0622390-4e66-43d5-8f2f-5a928c9d1dcc': 'capsule'}
# Layers no query group here reaches: 1 InvisibleGeo, 3 MaterialOverride,
# 4 Characters (default.physxconfiguration). Layer 2 blocks rounds only.
SKIPPED_LAYERS = {layer for layer in range(64)
                  if not ((static_geometry.AWARENESS_LOS | static_geometry.PROJECTILES) >> layer) & 1}


def decode_pxmesh(blob):
    """Vertices and triangles of a cooked PhysX 3.4 mesh (NXS MESH v15).

    After the 'NXS\\x01MESH' magic: version, midphase id, serial flags,
    vertex and triangle counts (u32 LE), the float vertices, then the
    indices as bytes (flag 4), u16 (flag 8) or u32. The cooked blob later
    stores the bounds as six floats; finding them exactly validates the
    decode.
    """
    start = blob.find(b'NXS\x01MESH')
    if start < 0:
        raise ValueError('no cooked PhysX mesh')
    version, _, flags, nv, nt = struct.unpack_from('<5I', blob, start + 8)
    if version != 15:
        raise ValueError('cooked mesh version %d' % version)
    offset = start + 28
    flat = struct.unpack_from('<%df' % (3 * nv), blob, offset)
    offset += 12 * nv
    size, kind = (1, 'B') if flags & 4 else (2, 'H') if flags & 8 else (4, 'I')
    index = struct.unpack_from('<%d%s' % (3 * nt, kind), blob, offset)
    offset += size * 3 * nt
    vertices = [flat[i:i + 3] for i in range(0, 3 * nv, 3)]
    if not all(math.isfinite(v) for v in flat) or any(i >= nv for i in index):
        raise ValueError('invalid cooked mesh')
    lo = [min(p[i] for p in vertices) for i in range(3)]
    hi = [max(p[i] for p in vertices) for i in range(3)]
    if blob.find(struct.pack('<6f', *lo, *hi), offset) < 0:
        raise ValueError('cooked mesh bounds not found')
    return vertices, [index[i:i + 3] for i in range(0, 3 * nt, 3)]


def box_mesh(half):
    x, y, z = half
    vertices = [(sx * x, sy * y, sz * z) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    # Outward winding (positive signed volume), like the cooked meshes.
    faces = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
    triangles = []
    for a, b, c, d in faces:
        triangles += [(a, b, c), (a, c, d)]
    return vertices, triangles


def capsule_mesh(radius, height, sides=12, rings=3):
    """A Z-axis capsule of total height `height`, as a closed triangle mesh."""
    half = max(height / 2. - radius, 0.)
    vertices = [(0., 0., half + radius)]
    for ring in range(1, rings + 1):            # upper cap, then the lower one
        angle = math.pi / 2 * ring / rings
        for k in range(sides):
            a = 2 * math.pi * k / sides
            vertices.append((radius * math.sin(angle) * math.cos(a),
                             radius * math.sin(angle) * math.sin(a),
                             half + radius * math.cos(angle)))
    for ring in range(rings, 0, -1):
        angle = math.pi / 2 * ring / rings
        for k in range(sides):
            a = 2 * math.pi * k / sides
            vertices.append((radius * math.sin(angle) * math.cos(a),
                             radius * math.sin(angle) * math.sin(a),
                             -half - radius * math.cos(angle)))
    vertices.append((0., 0., -half - radius))
    bottom = len(vertices) - 1
    triangles = [(0, 1 + k, 1 + (k + 1) % sides) for k in range(sides)]
    for ring in range(2 * rings - 1):
        top, low = 1 + ring * sides, 1 + (ring + 1) * sides
        for k in range(sides):
            k2 = (k + 1) % sides
            triangles += [(top + k, low + k, low + k2), (top + k, low + k2, top + k2)]
    last = 1 + (2 * rings - 1) * sides
    triangles += [(bottom, last + (k + 1) % sides, last + k) for k in range(sides)]
    return vertices, triangles


def quaternion(x, y, z, w):
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def compose(transform, offset, rotation, asset_scale):
    """The collider's world affine, columns c0, c1, c2 then translation.

    The entity transform's columns are its scaled axes (their lengths equal
    the shape configuration's Scale for every April2018 collider). PhysX
    scales the geometry, then applies the collider's local pose, then the
    entity's unscaled pose:
    world = R (Q (S A v) + S p) + T.
    """
    columns = [transform[0:3], transform[3:6], transform[6:9]]
    scale = [math.sqrt(sum(v * v for v in c)) for c in columns]
    unit = [[v / s for v in c] if s else c for c, s in zip(columns, scale)]
    rot = [[unit[j][i] for j in range(3)] for i in range(3)]          # R[row][col]
    q = quaternion(*rotation) if rotation else ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    stretch = [scale[i] * (asset_scale[i] if asset_scale else 1.) for i in range(3)]
    linear = [[sum(rot[r][k] * q[k][c] for k in range(3)) * stretch[c] for c in range(3)]
              for r in range(3)]
    shift = [scale[i] * (offset[i] if offset else 0.) for i in range(3)]
    translation = [sum(rot[r][k] * shift[k] for k in range(3)) + transform[9 + r] for r in range(3)]
    return tuple(linear[r][c] for c in range(3) for r in range(3)) + tuple(translation)


def colliders(slice_bytes):
    """Every collider component in a level slice, as a dict."""
    crc = objectstream.crc

    def find(node, name):
        want = (crc(name.lower()), crc(name))
        stack = list(reversed(node.children))
        while stack:
            child = stack.pop()
            if child.name in want:
                return child
            stack.extend(reversed(child.children))
        return None

    def floats(node, count):
        return (list(struct.unpack('>%df' % count, node.value))
                if node is not None and len(node.value) == 4 * count else None)

    roots = objectstream.parse(slice_bytes)
    for index, entity in enumerate(entities.entity_array(roots)):
        components = entity.child('components')
        if not components:
            continue
        transform = None
        for component in components.children:
            if str(component.type_id) == entities.TRANSFORM_COMPONENT:
                for node in component.children:
                    if node.name == entities.TRANSFORM_FIELD_WORLD and len(node.value) == 48:
                        transform = struct.unpack('>12f', node.value)
        for component in components.children:
            kind = COLLIDERS.get(str(component.type_id))
            if kind is None or transform is None:
                continue
            layer = find(component, 'CollisionLayer')
            trigger = find(component, 'Trigger')
            record = {
                'entry': index, 'kind': kind, 'transform': transform,
                'layer': (int.from_bytes(layer.children[0].value, 'big')
                          if layer is not None and layer.children else 0),
                'trigger': bool(trigger.value[0]) if trigger is not None and trigger.value else False,
                'offset': floats(find(component, 'Position'), 3),
                'rotation': floats(find(component, 'Rotation'), 4),
            }
            if kind == 'mesh':
                value = find(component, 'PhysicsAsset')
                value = value.value if value is not None else b''
                record['asset'] = None
                for k in range(len(value) - 4, 0, -1):     # trailing u32-prefixed hint
                    if int.from_bytes(value[k:k + 4], 'big') == len(value) - k - 4 > 0:
                        record['asset'] = value[k + 4:].decode('utf-8', 'replace').lower()
                        break
                record['asset_scale'] = floats(find(component, 'AssetScale'), 3)
            elif kind == 'box':
                dimensions = find(component, 'Dimensions')
                record['dimensions'] = floats(dimensions.children[-1] if dimensions is not None
                                              and dimensions.children else None, 3)
            else:
                radius = find(component, 'Radius')
                height = find(component, 'Height')
                record['radius'] = struct.unpack('>f', radius.value)[0] if radius is not None else 0.
                record['height'] = struct.unpack('>f', height.value)[0] if height is not None else 0.
            yield record


def build(level, install=INSTALL, log=print):
    """The cache bytes for one level, and a summary of what went in."""
    started = time.monotonic()
    levels = install / 'levels.pak'
    index = pak.index(levels)
    want = ('levels/%s/commondata/level.dynamicslice' % level).lower()
    name = next((n for n in index if n.decode('utf-8', 'replace').lower() == want), None)
    if name is None:
        raise SystemExit('no %s in %s' % (want, levels))
    slice_bytes = pak.read(levels, [name], index)[name]
    found = [c for c in colliders(slice_bytes)
             if not c['trigger'] and c['layer'] not in SKIPPED_LAYERS]
    log('%d blocking colliders read in %.0f s' % (len(found), time.monotonic() - started))

    wanted = {c['asset'] for c in found if c['kind'] == 'mesh' and c['asset']}
    blobs = {}
    for pak_name in MESH_PAKS:
        path = install / pak_name
        if not path.exists() or not wanted - set(blobs):
            continue
        index = pak.index(path)
        names = {n.decode('utf-8', 'replace').lower(): n for n in index}
        here = [names[a] for a in sorted(wanted - set(blobs)) if a in names]
        for raw, data in pak.read(path, here, index).items():
            blobs[raw.decode('utf-8', 'replace').lower()] = data
    meshes, numbers, summary = [], {}, collections.Counter()
    for asset in sorted(blobs):
        numbers[asset] = len(meshes)
        meshes.append(decode_pxmesh(blobs[asset]))
    instances = []
    for c in found:
        if c['kind'] == 'mesh':
            if c['asset'] not in numbers:
                summary['mesh asset missing'] += 1
                continue
            mesh = numbers[c['asset']]
        else:
            if c['kind'] == 'box':
                shape = box_mesh([d / 2. for d in c['dimensions'] or (0., 0., 0.)])
            elif c['kind'] == 'sphere':
                shape = capsule_mesh(c['radius'], 2 * c['radius'])
            else:
                shape = capsule_mesh(c['radius'], c['height'])
            mesh = len(meshes)
            meshes.append(shape)
        summary['%s layer %d' % (c['kind'], c['layer'])] += 1
        instances.append((mesh, c['layer'], compose(c['transform'], c['offset'],
                                                    c['rotation'], c.get('asset_scale'))))
    log('%d instances of %d meshes; %s; %.0f s' % (
        len(instances), len(meshes), dict(sorted(summary.items())), time.monotonic() - started))
    return static_geometry.dump(level, meshes, instances), summary


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('level', nargs='?', default='april2018')
    parser.add_argument('--install', type=Path, default=INSTALL)
    parser.add_argument('--out', type=Path, help='write here instead of the cache directory')
    args = parser.parse_args()
    data, _ = build(args.level, args.install)
    if args.out:
        args.out.write_bytes(data)
        path = args.out
    else:
        path = static_geometry.write_cache(args.level, data)
    print('wrote %s (%d bytes)' % (path, len(data)))


if __name__ == '__main__':
    main()
