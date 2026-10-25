"""Shared component sets of the objective slices, from the exe-derived table.

Read-only: reads misc.pak from the default Steam install and writes nothing.
Usage: .venv/bin/python -I snapshots/guide-objectives-20261025/coverage.py

A UUID in uuid-type-table.json is a network component of that type. The
Marine's ten non-network components and the ten targets the exe route found
to be non-network are listed as such. Anything else is printed as
unclassified (none, as of 25 October).
"""
import json, os, sys
sys.dont_write_bytecode = True
REPO = '/home/blizz/dev/Crucible Revival'
sys.path.insert(0, os.path.join(REPO, 'tools'))
import pak, objectstream as objs
SP = os.path.dirname(os.path.abspath(__file__))
MISC = os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible/misc.pak')
table = json.load(open(os.path.join(SP, 'uuid-type-table.json')))
nonnet = {r['uuid'] for r in json.load(open(os.path.join(SP, 'marine-uuid-types.json')))['components']
          if r['network_type'] is None}
nonnet |= {r['uuid'] for r in json.load(open(os.path.join(SP, 'uncovered-uuids.json')))
           if r['resolved_type'] is None}
schema = {c['type_id']: c for c in json.load(open(os.path.join(
    REPO, 'snapshots/gamemode-20260919/component-replication-schema.json')))['components']}
idx = pak.index(MISC)
names = sorted(k for k in idx if k.startswith(b'slices/') and k.endswith(b'.dynamicslice')
               and any(w in k.lower() for w in (b'hive', b'heart', b'harvester', b'objective')))
for name in names:
    blob = pak.read(MISC, [name], idx)[name]
    sc = objs.parse(blob)[0].child('components').children[0]
    ents = sc.child('Entities').children
    print('#', name.decode(), 'entities', len(ents), 'prefabs', len(sc.child('Prefabs').children))
    for i, e in enumerate(ents):
        uuids = [str(c.type_id).lower().strip('{}') for c in e.child('components').children]
        net = sorted({table[u]['type'] for u in uuids if u in table})
        shared = [t for t in net if schema[t]['expanded_record_bits'][0]]
        unknown = [u for u in uuids if u not in table and u not in nonnet]
        print('   [%d] %-38s comps %2d network %2d shared %s' % (
            i, e.child('name').value.decode('latin1')[:38], len(uuids), len(net), shared))
        if unknown:
            print('        unclassified:', ' '.join(unknown))
