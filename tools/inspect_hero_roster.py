#!/usr/bin/env python3
"""Read-only reader for the installed hunter roster, slice hashes and loadouts.

Nothing is written to the Steam install; every pak entry is decompressed in
memory. Use this to re-derive the table hard-coded in `server/hero_loadout.py`
after a game update, or to check a claim in `docs/24-hero-and-abilities.md`.

    python3 tools/inspect_hero_roster.py            # roster + hashes + slots
    python3 tools/inspect_hero_roster.py --components  # slice component diff
    python3 tools/inspect_hero_roster.py --classes     # CharacterClass enum

Sources:
  misc.pak  assets/gamesystems/characterrosters/commoncharacterroster.roster
  misc.pak  assets/player/<hero>/<hero>_abilities.loadout
  misc.pak  slices/player/<hero>/<hero>_character.dynamicslice
  levels.pak levels/<level>/commondata/stringhashlibrary.xml
  bin/Crucible.exe .rdata 0x6c1de08  (CharacterClass name pointer table)
"""
import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pak                                                    # noqa: E402
import objectstream as objs                                   # noqa: E402

GAME = os.environ.get(
    'CRUCIBLE_DIR',
    os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible'))
EXE = os.environ.get('CRUCIBLE_EXE', os.path.join(GAME, 'bin', 'Crucible.exe'))

# commoncharacterroster.roster field CRCs (zlib.crc32 of the lowered name).
ROSTER_ENTRIES = 0x1442a607      # 'characterroster'
ROSTER_CLASS = 0xa7927d1c        # 'characterclass'
ROSTER_SLICE = 0x77771c26        # 'characterslice'

# CharacterClass name pointer table; see the accessor at RVA 0x432a380.
CLASS_TABLE_RVA = 0x6c1de08
CLASS_TABLE_COUNT = 38


def read(pak_name, entry_name):
    for entry in pak.load(os.path.join(GAME, pak_name)):
        if entry['name'].decode('utf-8', 'replace') == entry_name:
            return pak.entry_bytes(entry)
    raise KeyError('%s: %s' % (pak_name, entry_name))


def entries(pak_name, pattern):
    rx = re.compile(pattern)
    out = {}
    for entry in pak.load(os.path.join(GAME, pak_name)):
        name = entry['name'].decode('utf-8', 'replace')
        if rx.search(name):
            out[name] = pak.entry_bytes(entry)
    return out


def baked_name(value):
    """A BakedName element: u8 version, int24 index (-1 when unbaked),
    u32 big-endian length, then the characters."""
    if len(value) < 8:
        raise ValueError('short BakedName element')
    index = int.from_bytes(value[1:4], 'big', signed=False)
    length = int.from_bytes(value[4:8], 'big')
    return (None if index == 0xFFFFFF else index,
            value[8:8 + length].decode('latin1'))


def asset_hint(node):
    match = re.search(rb'[0-9a-z_/]+[0-9a-z_]\.[a-z]+', node.value)
    return match.group(0).decode() if match else ''


def roster():
    """Ordered (display name, character slice path) from the shipped roster."""
    roots = objs.parse(read('misc.pak', 'assets/gamesystems/characterrosters/'
                                       'commoncharacterroster.roster'))
    table = next(n for n in objs.walk(roots) if n.name == ROSTER_ENTRIES)
    out = []
    for entry in table.children:
        fields = {c.name: c for c in entry.children}
        _, display = baked_name(fields[ROSTER_CLASS].value)
        slice_hint = asset_hint(fields[ROSTER_SLICE])
        out.append((display, slice_hint))
    return out


def slice_hashes(level='practicearena'):
    xml = read('levels.pak',
               'levels/%s/commondata/stringhashlibrary.xml' % level).decode(
                   'utf-8', 'replace')
    found = {}
    for key, value in re.findall(r'Key="(\d+)"\s+Value="([^"]+)"', xml):
        if re.fullmatch(r'slices/player/[a-z]+/[a-z]+_character', value):
            found[value] = int(key)
    return found


def loadout_abilities(hero, filename=None):
    name = filename or 'assets/player/%s/%s_abilities.loadout' % (hero, hero)
    roots = objs.parse(read('misc.pak', name))
    out = []
    for node in objs.walk(roots):
        if not node.value:
            continue
        match = re.search(rb'assets/[ -~]+?\.ability', node.value)
        if match:
            out.append(match.group(0).decode())
    return out


def character_components():
    """Component UUID list of each installed `<hero>_character` entity."""
    out = {}
    for name, blob in sorted(entries(
            'misc.pak',
            r'^slices/player/[a-z]+/[a-z]+_character\.dynamicslice$').items()):
        hero = name.split('/')[2]
        for entity in objs.entities(objs.parse(blob)):
            label = entity.child('name').value.decode('latin1')
            if label.lower() != '%s_character' % hero:
                continue
            out[hero] = [c.type_id
                         for c in entity.child('components').children]
    return out


def class_table():
    """CharacterClass enum names, read out of the executable's .rdata."""
    import pefile
    pe = pefile.PE(EXE)
    try:
        image = open(EXE, 'rb').read()
        base = pe.OPTIONAL_HEADER.ImageBase
        offset = pe.get_offset_from_rva(CLASS_TABLE_RVA)
        names = []
        for index in range(CLASS_TABLE_COUNT):
            va, = struct.unpack('<Q', image[offset + index * 8:
                                            offset + index * 8 + 8])
            text = pe.get_offset_from_rva(va - base)
            names.append(image[text:text + 64].split(b'\0')[0].decode('latin1'))
        return names
    finally:
        pe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--components', action='store_true',
                        help='diff each hunter slice against marine')
    parser.add_argument('--classes', action='store_true',
                        help='dump the native CharacterClass name table')
    parser.add_argument('--level', default='practicearena')
    args = parser.parse_args()

    if args.classes:
        for index, name in enumerate(class_table()):
            print('%3d %s' % (index, name))
        return 0

    if args.components:
        sets = character_components()
        marine = set(sets['marine'])
        for hero in sorted(sets):
            other = set(sets[hero])
            print('%-14s %3d components  missing=%s  extra=%s'
                  % (hero, len(sets[hero]),
                     sorted(marine - other) or '-',
                     sorted(other - marine) or '-'))
        return 0

    hashes = slice_hashes(args.level)
    classes = class_table()
    print('# roster order, %s hashes' % args.level)
    for index, (display, slice_hint) in enumerate(roster()):
        hero = display.lower()
        path = 'slices/player/%s/%s_character' % (hero, hero)
        try:
            abilities = loadout_abilities(
                hero, 'assets/player/duelist/deulist_abilities.loadout'
                if hero == 'duelist' else None)
        except KeyError:
            abilities = []
        print('%2d %-12s class=%-3s hash=%-11s abilities=%-3d %s'
              % (index, display,
                 classes.index(display) if display in classes else '?',
                 hashes.get(path, '-'), len(abilities), slice_hint))
    return 0


if __name__ == '__main__':
    sys.exit(main())
