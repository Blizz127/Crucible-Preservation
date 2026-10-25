from route2 import *

def decide(ev):
    """Selection rule over RTTI-signature vtable candidates. Returns (verdict, type, vt, note)."""
    cands = ev['cands']
    if not cands:
        return ('unreachable', None, None, 'no RTTI vtable reached')
    typ = [c for c in cands if c['getter'][0] == 'type']
    zer = [c for c in cands if c['getter'][0] == 'zero']
    if len({c['getter'][1] for c in typ}) > 1:
        return ('ambiguous', sorted({c['getter'][1] for c in typ}), [c['vt'] for c in typ], 'several distinct types')
    if typ:
        note = ''
        if len(typ) > 1: note = f'{len(typ)} vtables agree'
        return ('network', typ[0]['getter'][1], typ[0]['vt'], note + (' (+zero-stub vtable ignored)' if zer else ''))
    if zer:
        # only generic return-0 stub: type 0 candidate, needs corroboration by name
        return ('network?', 0, zer[0]['vt'], 'getter is xor al,al; ret (type 0 / generic stub)')
    return ('not-network', None, cands[0]['vt'], 'slot 14 is a real function or absent: ' + ','.join(sorted({c['getter'][0] for c in cands})))
