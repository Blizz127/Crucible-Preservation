#!/usr/bin/env python3
"""Read-only string-hash-library reader and level-hash calculator.

The shard's per-entity `slice_hash` field (see `docs/13-entity-replication.md`)
is the 32-bit key the client's string hash library maps back to an asset path.
Each shipped level carries its own copy of that library at
`levels/<name>/commondata/stringhashlibrary.xml` inside `levels.pak`.

The key function was recovered by fitting candidate 32-bit string hashes to
all 942 distinct Key/Value pairs across the six shipped libraries. Exactly one
family matches every pair: **Jenkins one-at-a-time over the ASCII-lowercased
string** (see `joaat_lower` below). 942/942, zero misses. The twelve entries
whose Value is stored in mixed case (`GameModeEntity`, `R_wrist_JNT`,
`StandaloneAutoStruct.*`, ...) only match once the string is lowercased, which
is what pins the lowercasing down rather than leaving it undetermined.

Level-name keys are the hash of `levels/<name>/commondata/level`.

Usage:
  python3 inspect_map_hashes.py --hash 'levels/hoth_02/commondata/level'
  python3 inspect_map_hashes.py --levels            # every shipped level key
  python3 inspect_map_hashes.py --verify LIB.xml    # refit the function
  python3 inspect_map_hashes.py --lookup 1289380704 --library LIB.xml
"""
import argparse
import re
import sys

MASK = 0xFFFFFFFF

# The six levels shipped in levels.pak. Only these can be loaded.
INSTALLED_LEVELS = ('april2018', 'HotH_02', 'practicearena',
                    'tutorial_02', 'tutorial_03', 'mainmenu')


def joaat_lower(text):
    """Jenkins one-at-a-time over the ASCII-lowercased string."""
    value = 0
    for byte in text.lower().encode('utf-8'):
        value = (value + byte) & MASK
        value = (value + (value << 10)) & MASK
        value ^= value >> 6
    value = (value + (value << 3)) & MASK
    value ^= value >> 11
    value = (value + (value << 15)) & MASK
    return value


def level_hash(level_name):
    """slice_hash for a level's baked entity array."""
    return joaat_lower('levels/%s/commondata/level' % level_name)


def read_library(path):
    """{key: value} from a stringhashlibrary.xml. Empty values are dropped."""
    text = open(path, 'r', encoding='utf-8', errors='replace').read()
    pairs = {}
    for match in re.finditer(r'Key="(\d+)"\s+Value="([^"]*)"', text):
        if match.group(2):
            pairs[int(match.group(1))] = match.group(2)
    return pairs


def verify(paths):
    """Refit joaat_lower against shipped libraries. Returns (checked, misses)."""
    seen, misses = {}, []
    for path in paths:
        for key, value in read_library(path).items():
            seen[value] = key
    for value, key in seen.items():
        if joaat_lower(value) != key:
            misses.append((value, key, joaat_lower(value)))
    return len(seen), misses


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--hash', action='append', default=[],
                        help='string to hash')
    parser.add_argument('--level', action='append', default=[],
                        help='level name; hashes levels/<n>/commondata/level')
    parser.add_argument('--levels', action='store_true',
                        help='hash every installed level name')
    parser.add_argument('--verify', nargs='+', metavar='LIB',
                        help='refit the hash against shipped libraries')
    parser.add_argument('--lookup', type=int, help='key to resolve')
    parser.add_argument('--library', help='library for --lookup')
    args = parser.parse_args()

    for text in args.hash:
        print('%10d  0x%08x  %s' % (joaat_lower(text), joaat_lower(text), text))
    names = list(args.level) + (list(INSTALLED_LEVELS) if args.levels else [])
    for name in names:
        path = 'levels/%s/commondata/level' % name
        print('%-14s %10d  0x%08x  %s'
              % (name, level_hash(name), level_hash(name), path.lower()))
    if args.verify:
        checked, misses = verify(args.verify)
        print('joaat_lower matched %d/%d distinct entries'
              % (checked - len(misses), checked))
        for value, key, got in misses:
            print('  MISS %r shipped=%d computed=%d' % (value, key, got))
        return 1 if misses else 0
    if args.lookup is not None:
        if not args.library:
            print('--lookup needs --library', file=sys.stderr)
            return 2
        print(read_library(args.library).get(args.lookup, '<absent>'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
