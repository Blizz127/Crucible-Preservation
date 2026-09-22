#!/usr/bin/env python3
"""Read-only inspector for Lumberyard binary ObjectStream v3 assets.

Format authority: aws/lumberyard, AzCore/Serialization/ObjectStream.cpp,
ObjectStreamImpl::ReadElement. UUID bytes are in printed UUID order; scalar
values use big endian. Entity array positions are NOT network SliceEntryIds.
"""
import argparse
from dataclasses import dataclass, field
from pathlib import Path
import uuid
import zlib

ENTITY = '75651658-8663-478d-9090-2432dfcafa44'


def crc(name):
    return zlib.crc32(name.lower().encode())


@dataclass
class Element:
    offset: int
    name: int
    version: int
    type_id: str
    value: bytes
    children: list = field(default_factory=list)

    def child(self, name):
        return next((x for x in self.children if x.name == crc(name)), None)


def parse(data):
    """Return roots; reject unsupported streams, malformed tags and truncation."""
    if data[:5] != b'\x00\x00\x00\x00\x03':
        raise ValueError('expected binary ObjectStream version 3')
    pos = 5

    def take(size):
        nonlocal pos
        if pos + size > len(data):
            raise ValueError(f'truncated ObjectStream at {pos:#x}')
        result = data[pos:pos + size]
        pos += size
        return result

    def elements(depth=0):
        if depth > 128:
            raise ValueError('ObjectStream nesting exceeds 128')
        result = []
        while True:
            offset = pos
            flags = take(1)[0]
            if flags == 0:
                return result
            if not flags & 8 or flags & 32 and not flags & 16:
                raise ValueError(f'invalid element flags at {offset:#x}')
            name = int.from_bytes(take(4), 'big') if flags & 64 else 0
            version = take(1)[0] if flags & 128 else 0
            type_id = str(uuid.UUID(bytes=take(16)))
            size = flags & 7 if flags & 16 else 0
            if flags & 32:
                if size not in (1, 2, 4):
                    raise ValueError(f'invalid size field at {offset:#x}')
                size = int.from_bytes(take(size), 'big')
            value = take(size)
            result.append(Element(offset, name, version, type_id, value,
                                  elements(depth + 1)))

    roots = elements()
    if pos != len(data):
        raise ValueError(f'trailing data at {pos:#x}')
    return roots


def walk(roots):
    for node in roots:
        yield node
        yield from walk(node.children)


def entities(roots, component=None):
    for node in walk(roots):
        if node.type_id != ENTITY:
            continue
        components = node.child('components')
        if not components:
            continue
        if component and not any(c.type_id == component for c in components.children):
            continue
        yield node


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--component', help='UUID of a direct entity component')
    args = parser.parse_args()
    component = str(uuid.UUID(args.component)) if args.component else None
    for entity in entities(parse(args.file.read_bytes()), component):
        name = entity.child('name')
        identity = entity.child('id')
        scalar = identity.child('id') if identity else None
        print(f'{entity.offset:#x} name={name.value.decode(errors="replace")!r}'
              f' asset_entity_id={scalar.value.hex() if scalar else "unknown"}')
        for c in entity.child('components').children:
            print(f'  {c.offset:#x} {c.type_id} version={c.version}')


if __name__ == '__main__':
    main()
