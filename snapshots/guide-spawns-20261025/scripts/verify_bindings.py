"""Independently verify the Transform / BotID bindings of the four SpawnGuideBot nodes.

For each SpawnGuideBot node, find its 'Transform' and 'BotID' variables, take m_Value/m_data/m_variableId,
and look that id up in every blackboard (user, generated, hidden-literals, member). Print which blackboard holds
it and its display name.  Uses tools/objectstream.py only (no other inspector code).
"""
import sys, zlib
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import objectstream
from inspect_creature_ai import name_dictionary, _preferred, decode_value

data = open(sys.argv[1], 'rb').read()
names = name_dictionary()
roots = objectstream.parse(data)
def lab(e): return _preferred(names.get(e.name)) or f"crc:{e.name:08x}"
def kid(e, n):
    for c in e.children:
        if lab(c) == n:
            return c
    return None
root = roots[0]

# variableId -> (blackboard, displayName, value element)
table = {}
for bb in ('m_userBlackboard', 'm_generatedBlackboard', 'm_hiddenLiteralsBlackboard', 'm_usedMemberVariablesBlackboard'):
    b = kid(root, bb)
    ent = kid(b, 'm_blackboardEntries')
    for e in ent.children:
        v = kid(e, 'm_variable')
        if v is None: continue
        vid = kid(v, 'm_variableId')
        dn = kid(v, 'm_displayName')
        val = kid(v, 'm_Value')
        table[vid.value] = (bb, dn.value.decode() if dn is not None else None, val)

def inst(e):
    for c in e.children:
        if lab(c) == 'm_InstanceId': return decode_value(c)[1]
        if lab(c) == 'BaseClass1':
            r = inst(c)
            if r is not None: return r
    return None

sp = kid(root, 'm_scriptPortionsData')
for el in sp.children:
    v2 = kid(el, 'Value2')
    for item in kid(v2, 'm_nodes').children:
        nn = kid(item, 'm_nodeName')
        if nn is None or nn.value != b'SpawnGuideBot':
            continue
        print(f"node [{inst(item)}] SpawnGuideBot")
        for var in kid(item, 'm_variables').children:
            dname = kid(var, 'm_displayName')
            dname = dname.value.decode() if dname is not None else None
            if dname not in ('BotID', 'Transform', 'Radius', 'OwnerId'):
                continue
            val = kid(var, 'm_Value')
            d = kid(val, 'm_data') if val is not None else None
            vid = kid(d, 'm_variableId') if d is not None else None
            if vid is None:
                print(f"   {dname}: inline value {d.value.hex() if d is not None else None}")
                continue
            bb, disp, _ = table.get(vid.value, (None, None, None))
            extra = ''
            if bb == 'm_hiddenLiteralsBlackboard':
                # the literal's value lives under its own m_Value/m_data
                vv = _[0:0] if False else None
                ent_val = table[vid.value][2]
                dd = kid(ent_val, 'm_data')
                extra = ' value=' + (repr(decode_value(dd)) if dd is not None and not dd.children else 'composite')
                if dd is not None and dd.children:
                    # baked name string children
                    extra = ' value=' + ' '.join(f"{lab(c)}={c.value[:40]!r}" for c in dd.children[:3])
            print(f"   {dname}: id={vid.value.hex()} -> {bb} '{disp}'{extra}")
