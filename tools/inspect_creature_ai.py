#!/usr/bin/env python3
"""Read-only inspector for creature AI / ability / projectile assets.

Lumberyard binary ObjectStream v3 elements carry a CRC32 of the lowercased
field name, not the name itself. This builds a reverse dictionary from the
identifier-like strings already present in the installed client executable
(read-only) and prints the asset tree with resolved names, so every constant
quoted in docs/22-creature-ai.md can be traced back to an offset in a named
installed asset.

Usage:
  python3 tools/inspect_creature_ai.py misc.pak ai/spitter.ai [--json out.json]
  python3 tools/inspect_creature_ai.py --crc SensingRadius

Nothing under the Steam install is ever written or executed.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import objectstream  # noqa: E402
import pak  # noqa: E402

INSTALL = Path(os.environ.get(
    'CRUCIBLE_INSTALL',
    os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible')))
EXE = Path(os.environ.get('CRUCIBLE_EXE', INSTALL / 'bin' / 'Crucible.exe'))

# AzCore type UUIDs seen in these assets (AzTypeInfo, Lumberyard 1.x).
SCALARS = {
    'a0ca880c-afe4-43cb-926c-59ac48496112': ('bool', lambda v: bool(v[0])),
    'ea2c3e90-afbe-44d4-a90d-faaf79baf93d': ('float', lambda v: struct.unpack('>f', v)[0]),
    '3d80f623-c85c-4741-90d0-e4e66164e6bf': ('double', lambda v: struct.unpack('>d', v)[0]),
    '72b9409a-7d1a-4831-9cfe-fcb3fadd3426': ('u8', lambda v: v[0]),
    '43da906b-7def-4ca8-9790-854106d3f983': ('u32', lambda v: int.from_bytes(v, 'big')),
    'b8a56d56-a10d-4dce-9f63-405ee243dd3c': ('s32', lambda v: int.from_bytes(v, 'big', signed=True)),
    '8379eb7d-01fa-4538-b64b-a6543b4be73d': ('Vector3', lambda v: struct.unpack('>3f', v)),
}
# AZStd::string and the engine's hashed-string wrappers all serialize the
# same way: 01 ffffff <be32 length> <utf8 bytes>.
STRINGISH = {
    'd31d0945-e328-dc71-e51c-049736ffdeb6',
    '03aaab3f-5c47-5a66-9ebc-d5fa4db353c9',
    '8fa041fd-1cdc-34d5-8dca-7d8aab7b1bd8',
    '2418189d-6407-9bd5-dc42-510d7f81e5e5',
}


def name_dictionary(exe=EXE):
    """CRC32(lowercased identifier) -> sorted candidate spellings."""
    image = exe.read_bytes()
    table = {}
    for match in re.finditer(rb'[A-Za-z_][A-Za-z0-9_]{2,63}', image):
        text = match.group(0).decode()
        table.setdefault(zlib.crc32(text.lower().encode()), set()).add(text)
    return {k: sorted(v) for k, v in table.items()}


def _preferred(candidates):
    """Prefer the CamelCase spelling an .ai field actually uses."""
    if not candidates:
        return None
    return sorted(candidates, key=lambda s: (s.islower(), s.isupper(), s))[0]


def decode_value(node):
    kind = SCALARS.get(node.type_id)
    if kind and len(node.value) in (1, 4, 8, 12):
        try:
            return kind[0], kind[1](node.value)
        except struct.error:
            pass
    if node.type_id in STRINGISH and len(node.value) >= 7:
        length = int.from_bytes(node.value[3:7], 'big')
        if length == len(node.value) - 7:
            return 'string', node.value[7:].decode('utf-8', 'replace')
    if not node.value:
        return None, None
    if all(32 <= b < 127 for b in node.value) and len(node.value) > 2:
        return 'text', node.value.decode('ascii')
    return 'raw', node.value.hex()


def describe(node, names, depth=0):
    label = _preferred(names.get(node.name)) or f'crc:{node.name:08x}'
    kind, value = decode_value(node)
    record = {'offset': node.offset, 'field': label, 'type_id': node.type_id,
              'version': node.version, 'children': []}
    if kind:
        record['kind'] = kind
        record['value'] = value
    for child in node.children:
        record['children'].append(describe(child, names, depth + 1))
    return record


def flatten(record, path=()):
    here = path + (record['field'],)
    if 'value' in record:
        yield '.'.join(here), record['offset'], record['kind'], record['value']
    for child in record['children']:
        yield from flatten(child, here)


def load_asset(pak_name, asset):
    pak_path = pak_name if os.path.sep in pak_name else str(INSTALL / pak_name)
    for entry in pak.load(pak_path):
        if entry['name'].decode('utf-8', 'replace') == asset:
            return pak.entry_bytes(entry)
    raise SystemExit(f'not found in {pak_path}: {asset}')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pak', nargs='?')
    parser.add_argument('asset', nargs='?')
    parser.add_argument('--crc', help='print CRC32 of a field name and exit')
    parser.add_argument('--json', help='write the resolved tree here')
    parser.add_argument('--flat', action='store_true', help='one line per scalar')
    args = parser.parse_args()

    if args.crc:
        print(f'{zlib.crc32(args.crc.lower().encode()):#010x} {args.crc}')
        return 0
    if not (args.pak and args.asset):
        parser.error('need PAK and ASSET (or --crc NAME)')

    data = load_asset(args.pak, args.asset)
    names = name_dictionary()
    tree = [describe(root, names) for root in objectstream.parse(data)]
    digest = hashlib.sha256(data).hexdigest()
    print(f'# {args.asset} sha256={digest} bytes={len(data)}', file=sys.stderr)
    if args.flat:
        for root in tree:
            for path, offset, kind, value in flatten(root):
                print(f'{offset:#08x} {kind:8s} {path} = {value}')
    else:
        print(json.dumps(tree, indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps(
            {'asset': args.asset, 'sha256': digest, 'tree': tree}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
