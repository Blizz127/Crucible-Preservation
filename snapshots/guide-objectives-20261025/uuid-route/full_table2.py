import json
from collections import defaultdict
from pelib import *
res = json.load(open('forward_map.json'))
schema = {e['type_id']: e['name'] for e in load_schema()['components']}
def base(n):
    for suf in ('::ClientBase', '::Client'):
        if n.endswith(suf): return n[:-len(suf)]
    return n
tab = {}
conflicts = []
for r in res:
    g = r['getter']; n = r['name']
    if g[0] in ('type', 'zero') and isinstance(r['uuid'], str) and (n.endswith('::Client') or n.endswith('::ClientBase')) and schema.get(g[1]) == base(n):
        k = r['uuid']
        v = {'type': g[1], 'schema_name': schema[g[1]], 'class': n, 'vtable_rva': hex(r['vt'])}
        if k in tab and tab[k]['type'] != v['type']: conflicts.append((k, tab[k], v))
        tab[k] = v
print('uuids:', len(tab), 'client:', sum(1 for v in tab.values() if v['class'].endswith('::Client')), 'clientbase:', sum(1 for v in tab.values() if v['class'].endswith('::ClientBase')), 'types covered:', len({v['type'] for v in tab.values()}), 'uuid->multiple types conflicts:', len(conflicts))
json.dump(tab, open('uuid_type_table.json', 'w'), indent=1, sort_keys=True)
