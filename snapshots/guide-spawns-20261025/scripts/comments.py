"""Attribute the designer comment boxes of guide_criticalpath.cdl to nodes by geometry.

m_Position / m_topLeft / m_bottomRight are 8 bytes: two big-endian float32 (x, y).
A node belongs to a comment box when its position lies inside the box rectangle.
Reads the raw .cdl bytes with tools/objectstream.py only.
"""
import sys, struct, json, re
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import objectstream
from inspect_creature_ai import name_dictionary, _preferred, decode_value

data = open(sys.argv[1], "rb").read()
names = name_dictionary()
roots = objectstream.parse(data)
def lab(e): return _preferred(names.get(e.name)) or f"crc:{e.name:08x}"
def kid(e, n):
    for c in e.children:
        if lab(c) == n:
            return c
    return None
def xy(e):
    return struct.unpack('>ff', e.value)
def text(e):
    raw = e.value
    try:
        s = raw.decode('utf-8')
    except Exception:
        return raw.hex()
    if re.fullmatch(r'[0-9a-f]{20,}', s):
        try:
            return bytes.fromhex(s).decode('utf-8')
        except Exception:
            return s
    return s

root = roots[0]
def inst(e):
    for c in e.children:
        if lab(c) == 'm_InstanceId':
            return decode_value(c)[1]
        if lab(c) == 'BaseClass1':
            r = inst(c)
            if r is not None:
                return r
    return None

nodes = {}
# enter trigger
et = kid(root, 'm_enterTrigger')
nodes[inst(et)] = {'name': 'ENTER', 'pos': xy(kid(et, 'm_Position')) if kid(et, 'm_Position') else None, 'portion': None}
sp = kid(root, 'm_scriptPortionsData')
for el in sp.children:
    key = decode_value(kid(el, 'Value1'))[1]
    v2 = kid(el, 'Value2')
    for g in ('m_nodes', 'm_conditions'):
        for item in kid(v2, g).children:
            # conditions may nest the base node one level down
            holder = item
            while kid(holder, 'm_Position') is None and kid(holder, 'BaseClass1') is not None:
                holder = kid(holder, 'BaseClass1')
            pos = xy(kid(holder, 'm_Position')) if kid(holder, 'm_Position') is not None else None
            nm = kid(holder, 'm_nodeName')
            nodes[inst(item)] = {'name': nm.value.decode() if nm is not None else None, 'pos': pos, 'portion': key}

boxes = []
for c in kid(root, 'm_commentNodes').children:
    tl = xy(kid(c, 'm_topLeft')); br = xy(kid(c, 'm_bottomRight'))
    boxes.append({'text': text(kid(c, 'm_comment')), 'tl': tl, 'br': br,
                  'area': abs((br[0]-tl[0])*(br[1]-tl[1]))})

def inside(p, b):
    x0, x1 = sorted((b['tl'][0], b['br'][0])); y0, y1 = sorted((b['tl'][1], b['br'][1]))
    return p is not None and x0 <= p[0] <= x1 and y0 <= p[1] <= y1

out = {'nodes': {}, 'boxes': boxes}
for i, n in nodes.items():
    inb = sorted([b for b in boxes if inside(n['pos'], b)], key=lambda b: b['area'])
    out['nodes'][str(i)] = {'name': n['name'], 'pos': n['pos'], 'portion': n['portion'],
                            'boxes': [b['text'][:90] for b in inb]}
json.dump(out, open(sys.argv[2], 'w'), indent=1)
mode = sys.argv[3] if len(sys.argv) > 3 else 'spawn'
if mode == 'spawn':
    for i, n in sorted(nodes.items()):
        if n['name'] in ('SpawnGuideBot', 'LocalDistributedSpawning', 'SpawnSlice', 'SpawnNodes_AIOverlord', 'Despawn', 'AddToTeam', 'AddEntityToAITargetBlackList', 'GetTeamId', 'SphereShapeEntityQuery') :
            print(f"[{i}] {n['name']} portion={n['portion']} pos={n['pos']}")
            for b in out['nodes'][str(i)]['boxes']:
                print("      in box:", b)
elif mode == 'boxes':
    for b in sorted(boxes, key=lambda b: (b['tl'][1], b['tl'][0])):
        cnt = sum(1 for n in nodes.values() if inside(n['pos'], b))
        print(f"tl={b['tl']} br={b['br']} nodes_inside={cnt}  {b['text'][:110]}")
