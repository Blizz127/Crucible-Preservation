"""Exhaustive forward map: every RTTI vtable in the exe (slot1 = lea rax,[name];ret) -> UUID (via slot0 -> F -> G -> init literal) + slot-14 getter."""
import json, sys
from collections import defaultdict, Counter
from decide import *
from pelib import _cs

R = Route(); im = R.im
schema = {e['type_id']: e['name'] for e in load_schema()['components']}

# literal interior map: any address in [s, s+8) -> uuid
lit_at = {}
for u, ss in R.lits.items():
    for s in ss:
        for d in range(0, 8):
            lit_at[s + d] = u

def uuid_of_fn(F):
    """Derive the UUID text a Uuid() function F yields. Returns (uuid or None, how)."""
    f = im.func_of(F)
    if not f: return (None, 'no-pdata')
    b, e = f
    code = im.bytes(b, e - b)
    tbl = False; lits = set(); loadG = []
    for ins in _cs.disasm(code, BASE + b):
        for op in ins.operands:
            if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                tgt = ins.address + ins.size + op.mem.disp - BASE
                if ins.mnemonic == 'lea':
                    if im.cstr(tgt, 24) == HEXTBL: tbl = True
                    elif tgt in lit_at: lits.add(lit_at[tgt])
                elif ins.mnemonic in ('movaps', 'movups', 'movdqa', 'movdqu') and not ins.op_str.startswith('xmmword'):
                    loadG.append(tgt)
    if tbl and len(lits) == 1:
        return (next(iter(lits)), 'inline')
    # (A): F loads global G; find initializer stores to G
    us = set()
    for G in loadG:
        for s, mn, op in R.ri.find(G):
            if mn in ('movdqa', 'movups', 'movaps', 'movdqu') and op.startswith('xmmword'):
                f2 = im.func_of(s)
                if not f2: continue
                code2 = im.bytes(f2[0], f2[1] - f2[0])
                t2 = False; l2 = set()
                for ins in _cs.disasm(code2, BASE + f2[0]):
                    for o in ins.operands:
                        if o.type == CS_OP_MEM and o.mem.base == X86_REG_RIP and ins.mnemonic == 'lea':
                            tg = ins.address + ins.size + o.mem.disp - BASE
                            if im.cstr(tg, 24) == HEXTBL: t2 = True
                            elif tg in lit_at: l2.add(lit_at[tg])
                if t2 and len(l2) == 1: us.add(next(iter(l2)))
    if len(us) == 1: return (next(iter(us)), 'global')
    if len(us) > 1: return (sorted(us), 'multi')
    return (None, 'underived')

# enumerate vtable candidates
cands = []
pp = R.pp; pv = R.pv
poss = sorted(set(int(x) for x in pp))
posset = set(poss)
for p in poss:
    if (p + 8) not in posset: continue
    s1 = im.ptr_rva(p + 8)
    b = im.bytes(s1, 8)
    if not (b[0:3] == b'\x48\x8d\x05' and b[7] == 0xC3): continue
    tgt = s1 + 7 + im.i32(s1 + 3)
    nm = im.cstr(tgt, 120)
    if not (len(nm) >= 3 and all(32 <= c < 127 for c in nm)): continue
    cands.append((p, nm.decode()))
print('RTTI-signature vtable candidates:', len(cands))
res = []
fcache = {}
for p, nm in cands:
    gt = R.getter(p)
    t0 = im.ptr_rva(p)
    F = t0
    for _ in range(4):
        b = im.bytes(F, 5)
        if b[0] == 0xE9: F = F + 5 + im.i32(F + 1)
        else: break
    if F not in fcache: fcache[F] = uuid_of_fn(F)
    u, how = fcache[F]
    res.append({'vt': p, 'name': nm, 'getter': gt[:2], 'F': F, 'uuid': u, 'how': how})
json.dump(res, open('forward_map.json', 'w'), default=str)
print('done; kinds:', Counter((r['getter'][0], r['how']) for r in res).most_common(20))
