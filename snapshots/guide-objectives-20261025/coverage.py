"""Coverage of objective slices by the live-attributed Marine UUID->type table.

Read-only: reads misc.pak from the default Steam install and writes nothing.
Usage: .venv/bin/python -I snapshots/guide-objectives-20261025/coverage.py
"""
import json, os, sys
sys.dont_write_bytecode = True
REPO = '/home/blizz/dev/Crucible Revival'
sys.path.insert(0, os.path.join(REPO, 'tools'))
import pak, objectstream as objs
SP = os.path.dirname(os.path.abspath(__file__))
MISC = os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible/misc.pak')
attr = json.load(open(os.path.join(SP, 'marine-uuid-types.json')))['components']
schema = {c['type_id']: c for c in json.load(open(os.path.join(REPO, 'snapshots/gamemode-20260919/component-replication-schema.json')))['components']}
table = {}
for row in attr:
    table[row['uuid']] = (('net', row['network_type']) if row['network_type'] is not None
                          else ('non-net', None))
idx = pak.index(MISC)
names = sorted(k for k in idx if k.startswith(b'slices/') and k.endswith(b'.dynamicslice')
               and any(w in k.lower() for w in (b'hive', b'heart', b'harvester', b'objective')))
for name in names:
    blob = pak.read(MISC, [name], idx)[name]
    root = objs.parse(blob)[0]
    sc = root.child('components').children[0]
    ents = sc.child('Entities').children
    print('#', name.decode(), 'entities', len(ents), 'prefabs', len(sc.child('Prefabs').children))
    for i, e in enumerate(ents):
        uuids = [str(c.type_id).lower().strip('{}') for c in e.child('components').children]
        cov = [table.get(u) for u in uuids]
        net = sorted({t for c in cov if c and c[0] == 'net' for t in [c[1]]})
        shared = [t for t in net if schema.get(t, {}).get('expanded_record_bits', [0])[0]]
        unknown = [u for u, c in zip(uuids, cov) if c is None]
        print('   [%d] %-38s comps %2d covered %2d (net %s; shared %s) uncovered %d' % (
            i, e.child('name').value.decode('latin1')[:38], len(uuids), len(uuids) - len(unknown),
            net, shared, len(unknown)))
        if unknown:
            print('        uncovered:', ' '.join(unknown))
