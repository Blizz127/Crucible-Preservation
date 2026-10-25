"""Which nodes belong to which script portion of guide_criticalpath.cdl, from the raw stream.

Independent of inspect_cdl_logic.py: walks the ObjectStream directly.
"""
import sys, json, collections
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import objectstream
from inspect_creature_ai import name_dictionary, _preferred, decode_value

data = open(sys.argv[1], "rb").read()
names = name_dictionary()
roots = objectstream.parse(data)
def lab(e): return _preferred(names.get(e.name)) or f"crc:{e.name:08x}"
def kid(e, name):
    for c in e.children:
        if lab(c) == name:
            return c
    return None

root = roots[0]
sp = kid(root, 'm_scriptPortionsData')
out = {}
for el in sp.children:
    key = decode_value(kid(el, 'Value1'))[1]
    v2 = kid(el, 'Value2')
    nodes = {}
    for group in ('m_nodes', 'm_conditions'):
        g = kid(v2, group)
        for item in g.children:
            # node body may be nested under BaseClass1
            body = item
            base = kid(item, 'BaseClass1')
            holder = item
            inst = None
            nm = None
            # search for m_InstanceId and m_nodeName in item or its BaseClass1
            def find(e, name):
                x = kid(e, name)
                if x is not None:
                    return x
                b = kid(e, 'BaseClass1')
                return find(b, name) if b is not None else None
            iid = find(item, 'm_InstanceId')
            nn = find(item, 'm_nodeName')
            inst = decode_value(iid)[1] if iid is not None else None
            nm = nn.value.decode(errors='replace') if nn is not None else None
            nodes[inst] = (group, nm)
    conns = []
    for c in kid(v2, 'm_connections').children:
        s = kid(c, 'm_source'); t = kid(c, 'm_target')
        sn = decode_value(kid(s, 'm_nodeInstanceId'))[1]
        tn = decode_value(kid(t, 'm_nodeInstanceId'))[1]
        conns.append((sn, tn))
    out[key] = {'startNodeUId': kid(v2, 'm_startNodeUId').value.hex(),
                'nodes': nodes, 'conns': conns}
    print(f"portion key={key} start={out[key]['startNodeUId']} nodes={len(nodes)} conns={len(conns)}")
    # cross-portion connections?
allnodes = {}
for k, p in out.items():
    for i in p['nodes']:
        allnodes[i] = k
cross = []
for k, p in out.items():
    for s, t in p['conns']:
        if allnodes.get(s) != k or allnodes.get(t) != k:
            cross.append((k, s, t, allnodes.get(s), allnodes.get(t)))
print("cross-portion connections:", cross)
json.dump({str(k): {'start': v['startNodeUId'],
                    'nodes': {str(i): list(nv) for i, nv in v['nodes'].items()},
                    'conns': v['conns']} for k, v in out.items()},
          open(sys.argv[2], 'w'), indent=1)
