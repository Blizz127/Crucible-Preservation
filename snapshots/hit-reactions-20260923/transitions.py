import sys, xml.etree.ElementTree as ET
sys.path.insert(0, '.')
import inspect_anim_graph as g
pakname, asset, *sms = sys.argv[1:]
blob, sha = g.load(pakname, asset)
root = ET.fromstring(blob.decode('utf-8', 'replace'))
nodes = g.all_nodes(root)
ids = {n['id']: n['name'] for n in nodes}
print('#', asset, sha)
FN = {'0':'>', '1':'>=', '2':'<', '3':'<=', '4':'!=', '5':'==', '6':'INRANGE', '7':'NOTINRANGE'}
for n in nodes:
    if n['class'] != 'AnimGraphStateMachine' or (sms and n['name'] not in sms):
        continue
    el = n['element']
    entry = None
    for lvl in g.base_chain(el):
        entry = g.value(lvl, 'entryStateId') or entry
    print('SM', n['name'], 'entry=', ids.get(entry, entry))
    for lvl in g.base_chain(el):
        tr = g.field(lvl, 'transitions')
        if tr is None: continue
        for t in tr:
            flat = {}
            for k, cls, v in g.leaf_fields(t):
                flat.setdefault(k, []).append(v)
            src = flat.get('sourceNodeId', ['?'])[0]
            dst = flat.get('targetNodeId', ['?'])[0]
            wc = flat.get('isWildcard', ['false'])[0]
            conds = []
            cl = g.field(t, 'conditions')
            if cl is not None:
                for c in cl:
                    cf = {k: v for k, cls, v in g.leaf_fields(c)}
                    conds.append('%s %s %s%s%s %s' % (c.get('name'), cf.get('parameterName', cf.get('motionNodeId','')), FN.get(cf.get('function'), cf.get('function')), cf.get('testValue',''), ('..'+cf['rangeValue']) if 'rangeValue' in cf and cf.get('function') in ('6','7') else '', ('t>='+cf['timeRequirement']) if cf.get('timeRequirement') else ('' if 'testCondition' not in cf else 'test='+cf['testCondition'])))
            print('  %s%s -> %s  blend=%s dis=%s  %s' % ('*' if wc=='true' else '', ids.get(src, src), ids.get(dst, dst), flat.get('transitionTime',['?'])[0], flat.get('isDisabled',['?'])[0], ' AND '.join(conds)))
