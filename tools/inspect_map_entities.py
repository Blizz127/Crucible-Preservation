#!/usr/bin/env python3
"""Read-only enumeration of a level's baked entity array.

Reads `levels/<name>/commondata/level.dynamicslice` (extract it with
`tools/pak.py cat`) and reports, per direct Entities-array position:

  * the authored entity name,
  * the asset EntityId (NOT a network EntityId),
  * the component type UUIDs,
  * the authored world translation when a TransformComponent is present.

Array positions here are the `SliceEntryId` indices the client validates in
`CreateReplicatedIndexedEntity` (retail 0x4bf5120) against the vector built by
0x4bf4d60 from `AZ::SliceComponent::GetEntities` (0x530a110). Both inspected
level slices have an empty slice-reference list (field CRC 0xafa37bc9), so
GetEntities prepends nothing and array position == SliceEntryId.

Network EntityIds are 0xFE000000 | counter and are NOT derived here; see
`docs/23-maps-and-modes.md` for what is and is not proven about that mapping.

Usage:
  python3 inspect_map_entities.py LEVEL.dynamicslice [--name REGEX]
      [--component UUID] [--range LO:HI] [--json OUT.json] [--positions]
"""
import argparse
import json
import re
import struct
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from objectstream import parse, walk, crc, ENTITY  # noqa: E402

# AzFramework::TransformComponent. Verified on practicearena entry 695
# (PlayerStartSpawn): its "Transform" field holds twelve BIG-endian floats
# (row-major 3x4, translation last) and decodes to
# (501.09075927734375, 564.5346069335938, 45.27315902709961), the exact
# authored start recorded in docs/16-entity-bootstrap.md.
TRANSFORM_COMPONENT = '22b10178-39b6-4c12-bb37-77db45fdd3b6'
TRANSFORM_FIELD_WORLD = 0x77b281ad   # crc32('transform')
TRANSFORM_FIELD_LOCAL = 0xcae6ee55   # crc32('localtransform')
TRANSFORM_FIELD_PARENT = 0x3d8e604f  # crc32('parent')


def slice_component(roots):
    """The level slice's SliceComponent element (UUID afd304e4-...)."""
    root = roots[0]
    components = root.child('components')
    if not components or not components.children:
        raise ValueError('level slice root has no components')
    return components.children[0]


def entity_array(roots):
    entities = slice_component(roots).child('entities')
    if entities is None:
        raise ValueError('SliceComponent has no Entities array')
    return entities.children


def has_nested_slices(roots):
    """True when GetEntities would prepend nested-instance entities."""
    references = slice_component(roots).child('slices')
    if references is None:
        references = next((c for c in slice_component(roots).children
                           if c.name == 0xafa37bc9), None)
    return bool(references and references.children)


def transform(entity):
    """(world_translation, parent_asset_entity_id) or (None, None)."""
    components = entity.child('components')
    if not components:
        return None, None
    for component in components.children:
        if component.type_id != TRANSFORM_COMPONENT:
            continue
        translation = None
        parent = None
        for node in component.children:
            if node.name == TRANSFORM_FIELD_WORLD and len(node.value) == 48:
                floats = struct.unpack('>12f', node.value)
                translation = [floats[9], floats[10], floats[11]]
            elif node.name == TRANSFORM_FIELD_PARENT:
                scalar = node.child('id')
                if scalar and len(scalar.value) == 8:
                    parent = scalar.value.hex()
        return translation, parent
    return None, None


def describe(index, entity, want_positions=False):
    name = entity.child('name')
    identity = entity.child('id')
    scalar = identity.child('id') if identity else None
    components = entity.child('components')
    record = {
        'entry': index,
        'name': name.value.decode('utf-8', 'replace') if name else None,
        'asset_entity_id': scalar.value.hex() if scalar else None,
        'components': [c.type_id for c in components.children] if components else [],
    }
    if want_positions:
        record['translation'], record['parent'] = transform(entity)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('file')
    parser.add_argument('--name', help='regex matched against the entity name')
    parser.add_argument('--component', help='UUID of a direct component')
    parser.add_argument('--range', help='LO:HI array-position slice')
    parser.add_argument('--positions', action='store_true')
    parser.add_argument('--near', help='X,Y,Z: only entities within --radius')
    parser.add_argument('--radius', type=float, default=30.0)
    parser.add_argument('--json')
    args = parser.parse_args()
    origin = tuple(float(x) for x in args.near.split(',')) if args.near else None
    if origin:
        args.positions = True

    roots = parse(open(args.file, 'rb').read())
    array = entity_array(roots)
    lo, hi = 0, len(array)
    if args.range:
        lo, hi = (int(x) for x in args.range.split(':'))
    pattern = re.compile(args.name, re.I) if args.name else None
    component = str(uuid.UUID(args.component)) if args.component else None

    records = []
    for index in range(max(lo, 0), min(hi, len(array))):
        entity = array[index]
        name = entity.child('name')
        text = name.value.decode('utf-8', 'replace') if name else ''
        if pattern and not pattern.search(text):
            continue
        if component:
            components = entity.child('components')
            if not components or not any(c.type_id == component
                                        for c in components.children):
                continue
        record = describe(index, entity, args.positions)
        if origin:
            place = record.get('translation')
            if not place:
                continue
            distance = sum((a - b) ** 2 for a, b in zip(place, origin)) ** 0.5
            if distance > args.radius:
                continue
            record['distance'] = distance
        records.append(record)
    if origin:
        records.sort(key=lambda r: r['distance'])

    report = {
        'file': args.file,
        'entity_array_length': len(array),
        'has_nested_slice_instances': has_nested_slices(roots),
        'entities': records,
    }
    if args.json:
        with open(args.json, 'w') as handle:
            json.dump(report, handle, indent=2)
        print('wrote %s (%d entities of %d)' % (args.json, len(records), len(array)),
              file=sys.stderr)
        return 0
    print('%s: %d entities, nested instances=%s'
          % (args.file, len(array), report['has_nested_slice_instances']),
          file=sys.stderr)
    for record in records:
        line = '%6d %-44s %s' % (record['entry'], record['name'],
                                 record['asset_entity_id'])
        if args.positions and record.get('translation'):
            line += ' (%.3f,%.3f,%.3f)' % tuple(record['translation'])
        if 'distance' in record:
            line += ' d=%.2fm' % record['distance']
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
