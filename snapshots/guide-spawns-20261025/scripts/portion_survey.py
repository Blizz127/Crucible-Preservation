"""Survey the script-portion layout of every .cdl in a pak (read-only).

For each .cdl: portion keys (Value1), whether the portion has a start node,
node counts, and how many nodes are reachable from the enter trigger over
the connections.  Used to learn what a start-less key-0 portion means.
"""
import sys, json, collections, zlib
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import objectstream, pak

def C(n): return zlib.crc32(n.lower().encode())
K = {n: C(n) for n in ['m_scriptPortionsData', 'Value1', 'Value2', 'm_nodes', 'm_conditions', 'm_connections',
                       'm_startNodeUId', 'm_source', 'm_target', 'm_nodeInstanceId', 'm_enterTrigger',
                       'BaseClass1', 'm_InstanceId', 'm_nodeName', 'm_conditions']}

def kid(e, n):
    c = K[n]
    for x in e.children:
        if x.name == c:
            return x
    return None

def u32(e): return int.from_bytes(e.value, 'big')

def find_inst(item):
    e = item
    while e is not None:
        x = kid(e, 'm_InstanceId')
        if x is not None:
            return u32(x)
        e = kid(e, 'BaseClass1')
    return None

def find_name(item):
    e = item
    while e is not None:
        x = kid(e, 'm_nodeName')
        if x is not None:
            return x.value.decode('utf8', 'replace')
        e = kid(e, 'BaseClass1')
    return None

pakpath = sys.argv[1]
only = sys.argv[2] if len(sys.argv) > 2 else None
res = []
for ent in pak.load(pakpath):
    name = ent['name'].decode('utf-8', 'replace')
    if not name.endswith('.cdl'):
        continue
    try:
        data = pak.entry_bytes(ent)
        roots = objectstream.parse(data)
    except Exception as ex:
        res.append({'asset': name, 'error': repr(ex)})
        continue
    root = roots[0]
    sp = kid(root, 'm_scriptPortionsData')
    if sp is None:
        res.append({'asset': name, 'portions': None})
        continue
    entry = kid(root, 'm_enterTrigger')
    entry_id = find_inst(entry) if entry is not None else None
    portions = []
    allnodes = {}
    conns = []
    for el in sp.children:
        v1 = kid(el, 'Value1'); v2 = kid(el, 'Value2')
        if v1 is None or v2 is None:
            continue
        key = u32(v1)
        ids = set()
        for g in ('m_nodes', 'm_conditions'):
            gg = kid(v2, g)
            if gg is None: continue
            for item in gg.children:
                i = find_inst(item)
                ids.add(i)
                allnodes[i] = key
        pc = []
        cc = kid(v2, 'm_connections')
        if cc is not None:
            for c in cc.children:
                s = kid(c, 'm_source'); t = kid(c, 'm_target')
                pc.append((u32(kid(s, 'm_nodeInstanceId')), u32(kid(t, 'm_nodeInstanceId'))))
        conns += pc
        st = kid(v2, 'm_startNodeUId')
        portions.append({'key': key, 'nodes': len(ids), 'conns': len(pc),
                         'start': st.value.hex() if st is not None and any(st.value) else None, 'ids': ids})
    # reachability from entry
    succ = collections.defaultdict(list)
    for s, t in conns:
        succ[s].append(t)
    seen = set()
    if entry_id is not None:
        seen = {entry_id}; st_ = [entry_id]
        while st_:
            u = st_.pop()
            for v in succ[u]:
                if v not in seen:
                    seen.add(v); st_.append(v)
    out = []
    for p in portions:
        out.append({'key': p['key'], 'nodes': p['nodes'], 'conns': p['conns'], 'start': p['start'],
                    'reachable_from_enter': len(p['ids'] & seen)})
    res.append({'asset': name, 'bytes': len(data), 'portions': out})

json.dump(res, open(sys.argv[3] if len(sys.argv) > 3 else '/dev/stdout', 'w'), indent=0)
print("done", len(res), file=sys.stderr)
