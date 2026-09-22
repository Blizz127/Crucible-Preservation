#!/usr/bin/env python3
"""Read-only dump of a .projectile / .ability ObjectStream asset with real
field names.

Lumberyard writes element names as crc32 of the **lowercased** field name, so
a raw ObjectStream dump is a list of hashes. The executable's own reflection
tables carry every field name as a plain string, so hashing every identifier
in the image rebuilds the lookup exactly — no guessing and no name list to
maintain. Verified against the four names docs/17 already had:
HealthChangeImmediateOnHit -> cb2eead5, CriticalMultiplier -> 8d5f9b11,
SpeedUnitsPerSecond -> acccef5c, TargetFilter -> 4957288d.

The asset itself must be extracted first; this never touches the install.

  python3 tools/pak.py cat <game>/misc.pak \\
      assets/player/marine/marine_projectile_primary.projectile --out /tmp/p
  python3 tools/inspect_combat_projectile.py /tmp/p
"""
import argparse
import hashlib
import os
import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from objectstream import parse  # noqa: E402

EXE = os.environ.get('CRUCIBLE_EXE', os.path.expanduser(
    '~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe'))


def name_table(exe=EXE):
    """crc32(lowercased) -> names, over every identifier in the image."""
    image = Path(exe).read_bytes()
    table = {}
    for match in re.finditer(rb'[A-Za-z_][A-Za-z0-9_]{2,63}', image):
        text = match.group().decode()
        table.setdefault(zlib.crc32(text.lower().encode()), set()).add(text)
    return table


def describe(value):
    """Scalars are big endian; AZStd strings carry an 8-byte header."""
    if len(value) == 1:
        return 'u8=%d' % value[0]
    if len(value) == 4:
        return 'f32=%g u32=%d' % (struct.unpack('>f', value)[0],
                                  struct.unpack('>I', value)[0])
    if len(value) in (8, 12, 16) and len(value) % 4 == 0:
        return 'f32x%d=%s' % (len(value) // 4,
                              struct.unpack('>%df' % (len(value) // 4), value))
    if len(value) > 8 and value[:4] == b'\x01\xff\xff\xff':
        return 'str=%r' % value[8:]
    return repr(value[:32])


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('file', type=Path)
    parser.add_argument('--field', action='append', default=[],
                        help='print only these field names (repeatable)')
    args = parser.parse_args()
    data = args.file.read_bytes()
    print('%s  %d bytes  sha256=%s'
          % (args.file, len(data), hashlib.sha256(data).hexdigest()))
    names = name_table()
    wanted = {f.lower() for f in args.field}

    def walk(nodes, depth=0):
        for node in nodes:
            resolved = sorted(names.get(node.name, ()))
            label = resolved[0] if resolved else '?%08x' % node.name
            if not wanted or label.lower() in wanted:
                print('%s%06x %-34s %s'
                      % ('  ' * depth, node.offset, label, describe(node.value)))
            walk(node.children, depth + 1)

    walk(parse(data))


if __name__ == '__main__':
    main()
