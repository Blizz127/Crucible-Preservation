"""Build a compact, readable flow view of guide_criticalpath.cdl from the logic JSON.

Reads critpath-logic.json (written by inspect_cdl_logic.py --json). Prints:
  - the one-line label of every node
  - which nodes are reachable from the enter trigger over the execution edges
  - for each spawn node, the shortest execution path from the enter trigger
Condition outputs: out0 TRUE, out1 FALSE; operators 0 == 1 != 2 < 3 <= 4 > 5 >=.
"""
import json, sys, collections

src = sys.argv[1]
d = json.load(open(src))
nodes = {n['instance']: n for n in d['nodes']}
OPS = {0: '==', 1: '!=', 2: '<', 3: '<=', 4: '>', 5: '>='}

def baked(v):
    if isinstance(v, str) and v.startswith('01ffffff') and len(v) > 16:
        try:
            return bytes.fromhex(v[16:]).decode('ascii')
        except Exception:
            return v
    return v

def val(v):
    if v is None:
        return '?'
    if 'ref' in v:
        s = '$' + str(v['ref'])
        if v.get('target') and v['target'] != '@Self':
            s += ' on ' + v['target']
        return s
    lit = v.get('literal')
    if isinstance(lit, dict) and set(lit) == {'Element'}:
        return '[' + ','.join(str(baked(x)) for x in lit['Element']) + ']'
    return repr(baked(lit))

def var(n, slot):
    for v in n['variables']:
        if v and v.get('slot') == slot:
            return v
    return None

def label(i):
    n = nodes[i]
    name = n['name']
    k = n['kind']
    if k == 'condition':
        els = ' AND '.join(f"{val(e['lhs'])} {OPS.get(e['op'], e['op'])} {val(e['rhs'])}" for e in n['elements'])
        return f"IF {els}" + (f"  # {n['comment']}" if n['comment'] else '')
    extra = ''
    if name == 'Delay':
        extra = f" {var(n,'Duration')['literal']:.3g}s"
    elif name == 'QueueVoiceEvent':
        extra = ' ' + val(var(n, 'EventName'))
    elif name in ('NewTutorialObjectives',):
        extra = ' ' + val(var(n, 'NewObjectivesList'))
    elif name in ('RemoveTutorialObjectives',):
        extra = ' ' + val(var(n, 'RemoveObjectivesList'))
    elif name == 'CompleteTutorialObjectives':
        extra = ' ' + val(var(n, 'TargetObjective'))
    elif name == 'Set':
        extra = f" {val(var(n,'TargetVariable'))} = {val(var(n,'ValueToWrite'))}"
    elif name == 'WaitForEvent':
        extra = ' ' + val(var(n, 'EventType'))
    elif name == 'SpawnGuideBot':
        extra = f" BotID={val(var(n,'BotID'))} Transform={val(var(n,'Transform'))} Radius={val(var(n,'Radius'))}"
    elif name == 'LocalDistributedSpawning':
        extra = f" Transform={val(var(n,'Transform'))} Radius={val(var(n,'Radius'))}"
    elif name in ('QueryEntities', 'SphereShapeEntityQuery'):
        extra = f" types={val(var(n,'EntityTypeValues'))} flags={val(var(n,'EntityFlagValues'))}"
    elif name == 'HasEntityType':
        extra = ' ' + val(var(n, 'EntityType'))
    elif name == 'ExternalGet':
        extra = ' ' + val(var(n, 'SourceVariable'))
    elif name == 'GetContainerValue':
        extra = f" idx={val(var(n,'Index'))} of {val(var(n,'Container'))}"
    elif name == 'ForEach':
        extra = f" over {val(var(n,'Input'))}"
    elif name == 'SpawnNodes_AIOverlord':
        extra = f" Category={val(var(n,'Category'))} Count={val(var(n,'Count'))} Telegraph={val(var(n,'TelegraphTime'))} SingleUse={val(var(n,'SingleUse'))} Occupy={val(var(n,'OccupySpawn'))}"
    elif name == 'SpawnSlice':
        extra = f" Telegraph={val(var(n,'TelegraphTime'))}"
    elif name == 'Despawn':
        extra = ' ' + val(var(n, 'TargetEntityId'))
    elif name == 'AddToTeam':
        extra = f" team={val(var(n,'TeamId'))} entity={val(var(n,'EntityId'))}"
    elif name == 'GetTimePoint':
        extra = ' offsetms=' + str(var(n, 'OffsetMilliseconds')['literal'])
    elif name == 'WaitUntilTimePoint':
        extra = ' ' + val(var(n, 'EndTimePoint'))
    elif name == 'AddEntityToAITargetBlackList':
        extra = f" on {val(var(n,'BlackListRequests'))} target={val(var(n,'TargetEntityId'))}"
    if n['comment']:
        extra += f"  # {n['comment']}"
    return f"{name}{extra}"

succ = collections.defaultdict(list)
pred = collections.defaultdict(list)
for e in d['edges']:
    succ[e['from']].append((e['out'], e['to']))
    pred[e['to']].append((e['out'], e['from']))

entry = [i for i, n in nodes.items() if n['kind'] == 'enter'][0]

# reachability + BFS parents
parent = {entry: None}
dq = collections.deque([entry])
while dq:
    u = dq.popleft()
    for out, v in sorted(succ[u]):
        if v not in parent:
            parent[v] = (u, out)
            dq.append(v)
reach = set(parent)

mode = sys.argv[2] if len(sys.argv) > 2 else 'summary'
if mode == 'labels':
    for i in sorted(nodes):
        print(f"[{i}] {'R' if i in reach else '-'} {label(i)}")
elif mode == 'summary':
    print('entry', entry, label(entry))
    print('nodes', len(nodes), 'reachable', len(reach), 'unreachable', len(nodes) - len(reach))
    print('--- unreachable nodes ---')
    for i in sorted(nodes):
        if i not in reach:
            ins = [(o, f) for o, f in pred[i]]
            outs = succ[i]
            print(f"[{i}] in={ins} out={outs}  {label(i)}")
    print('--- spawn-type nodes ---')
    for i in sorted(nodes):
        if nodes[i]['name'] in ('SpawnGuideBot', 'LocalDistributedSpawning', 'SpawnSlice', 'SpawnNodes_AIOverlord', 'Despawn'):
            print(f"[{i}] reachable={i in reach}  {label(i)}")
elif mode == 'path':
    for t in map(int, sys.argv[3:]):
        print(f"=== shortest exec path to [{t}] ===")
        chain = []
        cur = t
        while cur is not None:
            chain.append(cur)
            p = parent.get(cur)
            cur = p[0] if p else None
        chain.reverse()
        for i in chain:
            p = parent.get(i)
            how = f"(out{p[1]} of [{p[0]}])" if p else ''
            print(f"  [{i}] {label(i)} {how}")
