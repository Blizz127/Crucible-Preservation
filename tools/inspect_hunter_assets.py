#!/usr/bin/env python3
"""Read-only dump of named fields from installed Lumberyard ObjectStream assets.

Binary ObjectStream elements carry a CRC32 of the reflected field's *display*
name, not the name itself, so an asset on its own is a tree of anonymous
numbers. The reflection registers those display names as literals in the
client, so this builds a crc32 -> name dictionary by harvesting every printable
run out of Crucible.exe and hashing it, then labels the asset tree with it.

Authority for the container format is aws/lumberyard,
AzCore/Serialization/ObjectStream.cpp (ObjectStreamImpl::ReadElement), already
implemented by the companion tools/objectstream.py. Nothing installed is
written; the dictionary cache is the only file produced.

Usage:
  python3 inspect_hunter_assets.py misc.pak 'assets/player/marine/.*movementconfig'
  python3 inspect_hunter_assets.py misc.pak 'ai/guide_dummies/marine.*' --json out.json
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import objectstream  # noqa: E402
import pak  # noqa: E402

CRUCIBLE = Path.home() / '.local/share/Steam/steamapps/common/Crucible'
# The dictionary is ~5 MB and regenerable, so it is cached outside the repo.
CACHE = Path(os.environ.get('CRUCIBLE_NAME_CACHE')
             or Path.home() / '.cache/crucible-revival/reflection-names.json')
# Reflected display names are words, digits, spaces and underscores.
WORD = re.compile(rb'[A-Za-z][A-Za-z0-9_ ]{2,63}')


def dictionary(exe=None, cache=CACHE):
    """crc32(lowercased) -> shortest matching literal from the client binary."""
    if cache and Path(cache).exists():
        return {int(k): v for k, v in json.loads(Path(cache).read_text()).items()}
    data = (Path(exe) if exe else CRUCIBLE / 'bin/Crucible.exe').read_bytes()
    names = {}
    for match in WORD.finditer(data):
        text = match.group().decode()
        key = zlib.crc32(text.lower().encode())
        if key not in names or (len(text), text) < (len(names[key]), names[key]):
            names[key] = text
    if cache:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_text(json.dumps(names))
    return names


def decode(value):
    """Best-effort scalar decoding; ObjectStream scalars are big endian."""
    if len(value) == 1:
        return bool(value[0]) if value[0] in (0, 1) else value[0]
    if len(value) == 4:
        number = struct.unpack('>f', value)[0]
        return number if -1e9 < number < 1e9 else struct.unpack('>I', value)[0]
    if len(value) in (8, 12, 16) and len(value) % 4 == 0:
        return list(struct.unpack('>%df' % (len(value) // 4), value))
    if value[:4] == b'\x01\xff\xff\xff':
        return value[8:].decode('utf-8', 'replace')
    return None


def fields(nodes, names, path=()):
    for node in nodes:
        label = names.get(node.name, '%08x' % node.name)
        here = (*path, label)
        value = decode(node.value)
        if node.value:
            yield {'field': '/'.join(here), 'offset': hex(node.offset),
                   'raw': node.value.hex(), 'value': value}
        yield from fields(node.children, names, here)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pak')
    parser.add_argument('pattern', nargs='+')
    parser.add_argument('--exe')
    parser.add_argument('--cache', default=CACHE)
    parser.add_argument('--json')
    args = parser.parse_args()

    names = dictionary(args.exe, args.cache)
    path = Path(args.pak)
    if not path.exists():
        path = CRUCIBLE / args.pak
    wanted = [re.compile(p) for p in args.pattern]
    report = []
    for entry in pak.load(path):
        name = entry['name'].decode('utf-8', 'replace')
        if not any(p.search(name) for p in wanted):
            continue
        data = pak.entry_bytes(entry)
        try:
            roots = objectstream.parse(data)
        except ValueError as error:
            report.append({'asset': name, 'error': str(error)})
            continue
        report.append({'asset': name, 'pak': path.name,
                       'sha256': hashlib.sha256(data).hexdigest(),
                       'fields': list(fields(roots, names))})
    text = json.dumps(report, indent=1)
    if args.json:
        Path(args.json).write_text(text)
        print('wrote %s' % args.json, file=sys.stderr)
    else:
        print(text)


if __name__ == '__main__':
    main()
