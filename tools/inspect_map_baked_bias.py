#!/usr/bin/env python3
"""Read-only, one-launch measurement of a level's baked network-ID bias.

Why this exists: `server/solo_modes.py` has to send, for each baked level
entity, BOTH the SliceEntryId (the direct Entities-array position, which
`tools/inspect_map_entities.py` reads statically out of the asset) AND the
0xFE000000-category network EntityId the client keeps that entity under.
On practicearena the network counter is `entry + 2`; on april2018 it is
`entry + 787`. That per-map offset has not been derivable from the level
asset (see the eliminated hypotheses in docs/23-maps-and-modes.md), so it
has to be observed once per map.

Run this while the user has the client sitting in the target level. It walks
the client's replication tree and prints every node whose network EntityId is
in the 0xFE000000 category together with its slice hash, its SliceEntryId and
`network_counter - slice_entry`. If that difference is the same constant for
every node of the loaded level, that constant is the map's bias and it goes
into the MapProfile with source 'live'.

This process is read-only: it opens /proc/<pid>/mem O_RDONLY and pread()s.
It never writes to the process, never launches or terminates it, and never
reads strings or identity tokens. Offsets follow retail 4aa8700/4aa8600, the
same build-specific layout as tools/inspect_match_startup.py.
"""
import json
import os
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inspect_replication_schema import find_client  # noqa: E402

CRUCIBLE = Path('/home/blizz/.local/share/Steam/steamapps/common/Crucible'
                '/bin/Crucible.exe')
BAKED_CATEGORY = 0xFE000000
CATEGORY_MASK = 0xFF000000


def main():
    pid, base = find_client(CRUCIBLE)
    handle = os.open('/proc/%d/mem' % pid, os.O_RDONLY)

    def read(address, size):
        data = os.pread(handle, size, address)
        if len(data) != size:
            raise ValueError('short read at %#x' % address)
        return data

    def qword(address):
        return struct.unpack('<Q', read(address, 8))[0]

    def dword(address):
        return struct.unpack('<I', read(address, 4))[0]

    if read(base, 2) != b'MZ':
        raise SystemExit('not the expected image')
    game = qword(base + 0x760bb18)
    agent = qword(game + 0x78)
    if not agent:
        raise SystemExit('client network agent not ready')
    replication = qword(agent + 0x4112488)
    if not replication:
        raise SystemExit('client replication manager not ready')

    nodes = []
    stack = [qword(replication + 0x78) & ~1]
    seen = set()
    while stack:
        node = stack.pop()
        if not node or node in seen:
            continue
        seen.add(node)
        if len(seen) > 200000:
            raise ValueError('replication tree walk did not terminate')
        stack.extend([qword(node + 8) & ~1, qword(node + 0x10) & ~1])
        key = dword(node + 0x18)
        if key & CATEGORY_MASK != BAKED_CATEGORY:
            continue
        record = qword(node + 0x20)
        entity = qword(record + 0x40)
        counter = key & ~CATEGORY_MASK
        entry = dword(record + 0xf0)
        nodes.append({
            'network_id': hex(key),
            'network_counter': counter,
            'slice_hash': dword(record + 0x68),
            'slice_entry': entry,
            'counter_minus_entry': counter - entry,
            'active_state': read(entity + 0x60, 1)[0],
        })
    os.close(handle)

    nodes.sort(key=lambda n: n['network_counter'])
    biases = Counter((n['slice_hash'], n['counter_minus_entry']) for n in nodes)
    print(json.dumps({
        'pid': pid,
        'game_state': dword(game + 0xa0),
        'baked_nodes': nodes,
        'bias_histogram': [
            {'slice_hash': slice_hash, 'counter_minus_entry': bias,
             'nodes': count}
            for (slice_hash, bias), count in biases.most_common()],
    }, indent=2))
    if len(biases) == 1:
        (slice_hash, bias), _ = biases.most_common(1)[0]
        print('# single consistent bias for slice hash %d: %d'
              % (slice_hash, bias), file=sys.stderr)
    else:
        print('# bias is NOT a single constant here; do not record one',
              file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
