"""Graph checks on guide_criticalpath.cdl (reads critpath-logic.json + the raw .cdl for node classes).

1. m_nodeClass per node name (1 = instant, 3 = latent, as in findings-notes of the bot-weapon track).
2. In-edge-less nodes of the unreachable portion, and what each reaches.
3. Whether any SpawnGuideBot node lies on a cycle (Tarjan SCC).
4. Fixed Delay sums on the shortest path to each spawn, and between landmarks.
"""
import sys, json, collections
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import objectstream
from inspect_creature_ai import name_dictionary, _preferred, decode_value

sp = sys.argv[1]
lg = json.load(open(sp + '/critpath-logic.json'))
nodes = {n['instance']: n for n in lg['nodes']}
succ = collections.defaultdict(list); pred = collections.defaultdict(list)
for e in lg['edges']:
    succ[e['from']].append(e['to']); pred[e['to']].append(e['from'])

# 1. node classes from the raw stream
names = name_dictionary()
roots = objectstream.parse(open(sp + '/guide_criticalpath.cdl.bin', 'rb').read())
def lab(e): return _preferred(names.get(e.name)) or f"crc:{e.name:08x}"
def kid(e, n):
    for c in e.children:
        if lab(c) == n: return c
cls = collections.defaultdict(set)
def holder(item):
    h = item
    while kid(h, 'm_nodeClass') is None and kid(h, 'BaseClass1') is not None:
        h = kid(h, 'BaseClass1')
    return h
root = roots[0]
for el in kid(root, 'm_scriptPortionsData').children:
    v2 = kid(el, 'Value2')
    for g in ('m_nodes', 'm_conditions'):
        for item in kid(v2, g).children:
            h = holder(item)
            nn = kid(h, 'm_nodeName'); nc = kid(h, 'm_nodeClass')
            cls[nn.value.decode() if nn is not None else g].add(int.from_bytes(nc.value, 'big') if nc is not None else None)
print("m_nodeClass by node name (1 instant, 3 latent):")
for k in sorted(cls):
    print(f"  {k:32s} {sorted(cls[k], key=lambda x: (x is None, x))}")

# 2. unreachable portion: in-edge-less nodes and what they reach
entry = 1
seen = {entry}; st = [entry]
while st:
    u = st.pop()
    for v in succ[u]:
        if v not in seen: seen.add(v); st.append(v)
unreach = set(nodes) - seen
def reach(src):
    r = {src}; s = [src]
    while s:
        u = s.pop()
        for v in succ[u]:
            if v not in r: r.add(v); s.append(v)
    return r
SPAWN = ('SpawnGuideBot', 'LocalDistributedSpawning', 'SpawnSlice', 'SpawnNodes_AIOverlord')
print("\nunreachable nodes with no incoming edge ('roots') and the spawn nodes each reaches:")
for i in sorted(unreach):
    if not pred[i]:
        r = reach(i)
        sp_hit = sorted(j for j in r if nodes[j]['name'] in SPAWN)
        print(f"  [{i}] {nodes[i]['name']:26s} reaches {len(r)-1} nodes; spawn nodes reached (incl. itself): {sp_hit}")

# 3. SCCs
index = {}; low = {}; onst = set(); stack = []; sccs = []; counter = [0]
sys.setrecursionlimit(10000)
def strong(v):
    index[v] = low[v] = counter[0]; counter[0] += 1; stack.append(v); onst.add(v)
    for w in succ[v]:
        if w not in index:
            strong(w); low[v] = min(low[v], low[w])
        elif w in onst:
            low[v] = min(low[v], index[w])
    if low[v] == index[v]:
        comp = []
        while True:
            w = stack.pop(); onst.discard(w); comp.append(w)
            if w == v: break
        sccs.append(comp)
for v in nodes:
    if v not in index: strong(v)
cyc = {v: c for c in sccs if len(c) > 1 for v in c}
print("\ncycle membership of spawn/despawn nodes:")
for i, n in sorted(nodes.items()):
    if n['name'] in ('SpawnGuideBot', 'Despawn', 'SpawnNodes_AIOverlord', 'AddToTeam', 'GetTeamId'):
        print(f"  [{i}] {n['name']:22s} on a cycle: {i in cyc}")
print("  nodes with no outgoing edge among reachable nodes (script end points):",
      sorted(i for i in seen if not succ[i] and i != 1))
print("  [622] successors:", succ[622], " predecessors:", pred[622])

# 4. delays on the shortest path
parent = {entry: None}; dq = collections.deque([entry])
while dq:
    u = dq.popleft()
    for v in sorted(succ[u]):
        if v not in parent: parent[v] = u; dq.append(v)
def path_to(t):
    p = []; c = t
    while c is not None: p.append(c); c = parent.get(c)
    return p[::-1]
def dur(i):
    n = nodes[i]
    if n['name'] == 'Delay':
        for v in n['variables']:
            if v and v.get('slot') == 'Duration': return v['literal']
    return 0.0
for t in (252, 580, 7, 133):
    p = path_to(t)
    total = sum(dur(i) for i in p)
    print(f"\nshortest path to [{t}] has {len(p)} nodes; fixed Delay nodes sum to {total:.2f} s (player-driven waits excluded)")
# between landmarks on the ally path
p252 = path_to(252)
def seg(a, b):
    ia, ib = p252.index(a), p252.index(b)
    return sum(dur(i) for i in p252[ia:ib])
print("  GoToHive complete [541] -> Duelist [252]:", seg(541, 252), "s of Delay nodes")
print("  SpawnNodes_AIOverlord [64] -> GoToHive objective [136]:", seg(64, 136), "s")
print("  enter [1] -> SpawnNodes_AIOverlord [64]:", seg(1, 64), "s fixed Delay (plus detect, camp, harvester, level-2 waits)")
