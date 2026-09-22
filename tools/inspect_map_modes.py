#!/usr/bin/env python3
"""Read-only enumeration of the shipped game-mode configuration list.

`design/gamemodes/gamemodes.gamemodeconfiglist` inside `misc.pak` is the table
the client indexes with the 16-bit configuration selector the shard sends in
the GameMode entity's properties (`server/shard_stub.py` adds 32768). The list
is an ordered array, so the index of each element *is* that selector value;
this tool prints the index, the mode name, the referenced `.gamemodeconfig`
path, and the fields a solo bootstrap has to agree with:

  * MinimumPlayers      -- how many connections the mode waits for
  * PhaseOrder          -- the phase sequence, with per-phase Time (-1 = none)
  * MatchScoreData      -- TargetScore / MatchPointThreshold
  * GameModeFlags       -- notably AutoSkipPhaseToGameplay and DoNotSpawnDropPod
  * GameModeValues      -- named integers, e.g. MaxGameDuration
  * AlliancesEnabled / EnableRespawn / UIModeOverride

Usage:
  python3 inspect_map_modes.py [--pak PATH] [--json OUT.json] [--mode NAME]

Nothing under the Steam install is written or executed.
"""
import argparse
import json
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import objectstream  # noqa: E402
import pak as pakmod  # noqa: E402

INSTALL = os.environ.get(
    'CRUCIBLE_INSTALL',
    os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible'))
LIST_ASSET = 'design/gamemodes/gamemodes.gamemodeconfiglist'

BOOL = 'a0ca880c-afe4-43cb-926c-59ac48496112'
FLOAT = 'ea2c3e90-afbe-44d4-a90d-faaf79baf93d'
U32 = '43da906b-7def-4ca8-9790-854106d3f983'
S32 = 'b8a56d56-a10d-4dce-9f63-405ee243dd3c'

# Fields read out of each .gamemodeconfig, by CRC32 of the lowercased name.
WANTED = ('MinimumPlayers', 'PhaseType', 'Time', 'TargetScore',
          'MatchPointThreshold', 'AlliancesEnabled', 'EnableRespawn',
          'UIModeOverride', 'GameModeFlags', 'GameModeValues', 'Name',
          'Value', 'PhaseOrder', 'MatchScoreData', 'Mode', 'Config',
          'Configs', 'DefaultGameMode', 'EndCondtion', 'StaticAllianceMode',
          'UseRespawnPointsAsDropPoints', 'EnableMultipleDropMasters')
CRC = {zlib.crc32(name.lower().encode()): name for name in WANTED}


def open_pak(path):
    return {e['name'].decode('utf-8', 'replace'): e
            for e in pakmod.load(path)}


def text(node):
    """AZStd::string / hashed-string payload: 01 ffffff <be32 len> <bytes>."""
    value = node.value
    if len(value) >= 8 and value[:4] == b'\x01\xff\xff\xff':
        length = struct.unpack('>I', value[4:8])[0]
        return value[8:8 + length].decode('utf-8', 'replace')
    return None


def number(node):
    if node.type_id == BOOL and len(node.value) == 1:
        return bool(node.value[0])
    if node.type_id == FLOAT and len(node.value) == 4:
        return struct.unpack('>f', node.value)[0]
    if node.type_id in (U32, S32) and len(node.value) == 4:
        return struct.unpack('>i' if node.type_id == S32 else '>I',
                             node.value)[0]
    if len(node.value) == 4:
        return struct.unpack('>i', node.value)[0]
    return None


def named(nodes, label):
    want = zlib.crc32(label.lower().encode())
    return [n for n in nodes if n.name == want]


def asset_path(node):
    """Trailing path of a serialized AZ::Data::Asset reference."""
    value = node.value
    if len(value) < 8:
        return None
    length = struct.unpack('>I', value[-4:])[0] if False else None
    # The reference ends with <be64 length><utf8 path>; find the last run of
    # printable ASCII that looks like an asset path.
    tail = value[48:] if len(value) > 48 else b''
    if len(tail) >= 8:
        size = struct.unpack('>Q', tail[:8])[0]
        if 0 < size <= len(tail) - 8:
            return tail[8:8 + size].decode('utf-8', 'replace')
    return length


def config_fields(roots):
    """Flatten one .gamemodeconfig into the fields a bootstrap needs."""
    out = {'phases': [], 'flags': [], 'values': {}}
    phase = None
    for node in objectstream.walk(roots):
        label = CRC.get(node.name)
        if label == 'PhaseType':
            phase = {'phase': text(node)}
            out['phases'].append(phase)
        elif label == 'Time' and phase is not None:
            phase['time'] = number(node)
            phase = None
        elif label == 'MinimumPlayers':
            out['minimum_players'] = number(node)
        elif label == 'TargetScore':
            out['target_score'] = number(node)
        elif label == 'MatchPointThreshold':
            out['match_point_threshold'] = number(node)
        elif label in ('AlliancesEnabled', 'EnableRespawn',
                       'StaticAllianceMode', 'EnableMultipleDropMasters',
                       'UseRespawnPointsAsDropPoints'):
            out[label] = number(node)
        elif label == 'UIModeOverride':
            out['ui_mode_override'] = text(node)
        elif label == 'GameModeFlags':
            # Container: the flag strings are its unnamed child elements.
            for child in node.children or ():
                value = text(child)
                if value:
                    out['flags'].append(value)
            value = text(node)
            if value:
                out['flags'].append(value)
        elif label == 'Name':
            out.setdefault('_pending', []).append(text(node))
        elif label == 'Value' and out.get('_pending'):
            out['values'][out['_pending'].pop(0)] = number(node)
    out.pop('_pending', None)
    return out


def report(pak_path):
    entries = open_pak(pak_path)
    roots = objectstream.parse(pakmod.entry_bytes(entries[LIST_ASSET]))
    modes, default = [], None
    pending = None
    for node in objectstream.walk(roots):
        label = CRC.get(node.name)
        if label == 'Mode':
            pending = text(node)
        elif label == 'Config' and pending is not None:
            modes.append({'index': len(modes), 'mode': pending,
                          'config': asset_path(node)})
            pending = None
        elif label == 'DefaultGameMode':
            default = text(node)
    for record in modes:
        path = record['config']
        entry = entries.get(path)
        if not entry:
            record['error'] = 'asset not present in pak'
            continue
        record.update(config_fields(
            objectstream.parse(pakmod.entry_bytes(entry))))
    return {'pak': pak_path, 'list': LIST_ASSET,
            'default_game_mode': default, 'modes': modes}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pak', default=os.path.join(INSTALL, 'misc.pak'))
    parser.add_argument('--mode', help='only print this mode name')
    parser.add_argument('--json')
    args = parser.parse_args()

    data = report(args.pak)
    if args.json:
        with open(args.json, 'w') as handle:
            json.dump(data, handle, indent=2)
        print('wrote %s' % args.json, file=sys.stderr)
        return 0
    print('default game mode: %s' % data['default_game_mode'])
    for record in data['modes']:
        if args.mode and record['mode'] != args.mode:
            continue
        print('%2d %-20s %s' % (record['index'], record['mode'],
                                record['config']))
        print('     MinimumPlayers=%s TargetScore=%s MatchPoint=%s'
              ' Alliances=%s Respawn=%s UI=%s'
              % (record.get('minimum_players'), record.get('target_score'),
                 record.get('match_point_threshold'),
                 record.get('AlliancesEnabled'),
                 record.get('EnableRespawn'),
                 record.get('ui_mode_override')))
        print('     phases: %s' % ' '.join(
            '%s(%s)' % (p['phase'], p.get('time')) for p in record['phases']))
        if record['flags']:
            print('     flags: %s' % ', '.join(record['flags']))
        if record['values']:
            print('     values: %s' % ', '.join(
                '%s=%s' % kv for kv in record['values'].items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
