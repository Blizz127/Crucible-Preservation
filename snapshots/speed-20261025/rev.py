"""Static-RE helper (read-only): numpy rel32 xref scan + capstone over Crucible.exe.

The scan cache goes to $REV_CACHE_DIR or ~/.cache/crucible-revival/rev (about
400 MB); nothing is written next to the exe.
"""
import os, struct, sys, pickle
import numpy as np
from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_OP_MEM, CS_OP_IMM, CS_OP_REG
from capstone.x86_const import X86_REG_RIP

EXE = os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe')
SP = os.environ.get('REV_CACHE_DIR') or os.path.expanduser('~/.cache/crucible-revival/rev')
os.makedirs(SP, exist_ok=True)
CACHE = SP + '/cache.pkl'
EA_NPY = SP + '/ea.npy'

def _load():
    if os.path.exists(CACHE):
        return pickle.load(open(CACHE, 'rb'))
    import pefile
    pe = pefile.PE(EXE, fast_load=True)
    secs = []
    for s in pe.sections:
        secs.append((s.Name.rstrip(b'\0').decode(), s.VirtualAddress, s.PointerToRawData,
                     s.SizeOfRawData, s.Misc_VirtualSize))
    d = dict(ibase=pe.OPTIONAL_HEADER.ImageBase, secs=secs)
    pickle.dump(d, open(CACHE, 'wb'))
    return d

META = _load()
IBASE = META['ibase']
IMG = np.memmap(EXE, dtype=np.uint8, mode='r')
SECS = {n: (va, raw, rs, vs) for n, va, raw, rs, vs in META['secs']}
TVA, TRAW, TRS, _ = SECS['.text']
TEXT = bytes(IMG[TRAW:TRAW + TRS])

def rva2off(rva):
    for n, (va, raw, rs, vs) in SECS.items():
        if va <= rva < va + max(rs, vs):
            return raw + rva - va
    return None

def off2rva(off):
    for n, (va, raw, rs, vs) in SECS.items():
        if raw <= off < raw + rs:
            return va + off - raw
    return None

def read(rva, n):
    o = rva2off(rva)
    return bytes(IMG[o:o + n])

def u32(rva): return struct.unpack('<I', read(rva, 4))[0]
def u64(rva): return struct.unpack('<Q', read(rva, 8))[0]
def f32(rva): return struct.unpack('<f', read(rva, 4))[0]

def cstr(rva, n=200):
    try:
        b = read(rva, n).split(b'\0', 1)[0]
    except Exception:
        return None
    try:
        s = b.decode()
    except Exception:
        return None
    if len(s) >= 3 and all(31 < ord(c) < 127 or c in '\t\n' for c in s):
        return s
    return None

_PDATA = None
def funcs():
    global _PDATA
    if _PDATA is None:
        va, raw, rs, vs = SECS['.pdata']
        a = np.frombuffer(bytes(IMG[raw:raw + rs - rs % 12]), dtype='<u4').reshape(-1, 3)
        a = a[a[:, 1] > a[:, 0]]
        a = a[np.argsort(a[:, 0])]
        _PDATA = a
    return _PDATA

def func_of(rva):
    f = funcs()
    i = np.searchsorted(f[:, 0], rva, side='right') - 1
    if i < 0:
        return None
    return int(f[i, 0]), int(f[i, 1])

_EA = None
def _ea():
    global _EA
    if _EA is None:
        if not os.path.exists(EA_NPY):
            n = len(TEXT) - 4
            d32 = np.lib.stride_tricks.as_strided(
                np.frombuffer(TEXT, dtype=np.uint8), shape=(n, 4), strides=(1, 1))
            v = d32.copy().view('<i4').reshape(-1)
            v += (np.arange(n, dtype=np.int32) + np.int32(TVA + 4))
            np.save(EA_NPY, v)
        _EA = np.load(EA_NPY, mmap_mode='r')
    return _EA

def refs(target, extra=(0, 1, 2, 4)):
    ea = _ea()
    out = set()
    for k in extra:
        out.update(int(h) + TVA for h in np.nonzero(ea == np.int32(target - k))[0])
    return sorted(out)

def md():
    c = Cs(CS_ARCH_X86, CS_MODE_64)
    c.detail = True
    return c

_CAP = md()

def _refs_target(ins, target):
    for op in ins.operands:
        if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
            if ins.address + ins.size + op.mem.disp - IBASE == target:
                return True
        elif op.type == CS_OP_IMM and (ins.mnemonic in ('call', 'jmp') or ins.mnemonic.startswith('j')):
            if op.imm - IBASE == target:
                return True
    return False

def xrefs(target):
    out = []
    for r in refs(target):
        for back in range(8, 0, -1):
            start = r - back
            code = TEXT[start - TVA:start - TVA + 16]
            got = None
            for ins in _CAP.disasm(code, IBASE + start):
                got = ins
                break
            if got is None:
                continue
            a = got.address - IBASE
            if a < r + 1 and a + got.size >= r + 4 and _refs_target(got, target):
                out.append((a, got.mnemonic + ' ' + got.op_str))
                break
    return out

def note(ins):
    for op in ins.operands:
        if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
            tgt = ins.address + ins.size + op.mem.disp - IBASE
            s = cstr(tgt)
            if s:
                return f'  ; {tgt:#x} "{s[:90]}"'
            if ins.mnemonic.startswith(('movss', 'mulss', 'addss', 'subss', 'divss', 'comiss', 'ucomiss', 'minss', 'maxss', 'cvt', 'andps', 'xorps', 'movaps', 'movups', 'sqrtss')):
                try:
                    return f'  ; {tgt:#x} f32={f32(tgt)!r}'
                except Exception:
                    pass
            return f'  ; {tgt:#x}'
    return ''

def dis(start, end=None, show=True):
    if end is None:
        b, e = func_of(start)
        start, end = b, e
    lines = []
    for ins in _CAP.disasm(TEXT[start - TVA:end - TVA], IBASE + start):
        a = ins.address - IBASE
        op = ins.op_str
        if ins.mnemonic in ('call', 'jmp') or ins.mnemonic.startswith('j'):
            try:
                t = int(op, 16) - IBASE
                op = hex(t)
            except ValueError:
                pass
        lines.append(f'{a:#10x}  {ins.mnemonic:8s} {op}{note(ins)}')
    s = '\n'.join(lines)
    if show:
        print(s)
    return s

_ALL = None
def find_bytes(pat, limit=40):
    global _ALL
    if _ALL is None:
        _ALL = open(EXE, 'rb').read()
    b = _ALL
    res, s = [], 0
    while len(res) < limit:
        i = b.find(pat, s)
        if i < 0:
            break
        res.append(off2rva(i))
        s = i + 1
    return res

def strings_rva(pat):
    return find_bytes(pat.encode() if isinstance(pat, str) else pat)

def callers(target):
    """call/jmp rel32 sites to target."""
    return [(a, s) for a, s in xrefs(target)]

def ptr_refs(target_rva):
    """8-byte absolute pointers (IBASE+rva) to target in all non-text sections (vtables, tables)."""
    pat = struct.pack('<Q', IBASE + target_rva)
    res = []
    all_ = open(EXE, 'rb').read() if _ALL is None else _ALL
    s = 0
    while True:
        i = all_.find(pat, s)
        if i < 0:
            break
        res.append(off2rva(i))
        s = i + 1
    return res

if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'dis':
        a = int(sys.argv[2], 16)
        e = int(sys.argv[3], 16) if len(sys.argv) > 3 else None
        if e is None:
            print('func', [hex(x) for x in func_of(a)])
        dis(a, e)
    elif cmd == 'x':
        for a in sys.argv[2:]:
            t = int(a, 16)
            for r, s in xrefs(t):
                f = func_of(r)
                print(f'{t:#x} <- {r:#x} [{f[0]:#x}] {s}')
    elif cmd == 's':
        for p in sys.argv[2:]:
            for r in strings_rva(p):
                print(p, hex(r), [(hex(a), s) for a, s in xrefs(r)])
    elif cmd == 'p':
        for a in sys.argv[2:]:
            t = int(a, 16)
            print(hex(t), [hex(x) for x in ptr_refs(t)])


# ---- displacement scan: find memory operands with [reg + disp32] for a given disp -----
def disp_hits(disp, mnemonics=None, base_ok=None):
    """Return list of (rva, text) instructions in .text having a non-rip memory operand with
    displacement == disp (encoded as disp32 or disp8 if it fits)."""
    import re as _re
    res = []
    if -128 <= disp <= 127:
        pat = struct.pack('<b', disp)
        width = 1
    else:
        pat = struct.pack('<i', disp)
        width = 4
    t = np.frombuffer(TEXT, dtype=np.uint8)
    # candidate positions of the disp bytes
    if width == 4:
        m = (t[:-3] == pat[0]) & (t[1:-2] == pat[1]) & (t[2:-1] == pat[2]) & (t[3:] == pat[3])
        pos = np.nonzero(m)[0]
    else:
        pos = np.nonzero(t == (pat[0]))[0]
    for p in pos:
        p = int(p)
        end = p + width  # offset (in TEXT) just after the displacement, if no immediate follows
        # instruction may be followed by an immediate (0..4 bytes); try a few starts
        found = None
        for back in range(2, 11):
            start = p - back + 1 if False else p - back
            if start < 0:
                continue
            code = TEXT[start:start + 15]
            for ins in _CAP.disasm(code, IBASE + TVA + start):
                # check this instruction has a mem operand with matching disp and that disp bytes lie at p
                if ins.disp_size == width and ins.disp_offset == (p - start):
                    for op in ins.operands:
                        if op.type == CS_OP_MEM and op.mem.disp == disp and op.mem.base != X86_REG_RIP:
                            found = ins
                break
            if found is not None:
                break
        if found is not None:
            if mnemonics is None or found.mnemonic in mnemonics:
                res.append((found.address - IBASE, f'{found.mnemonic} {found.op_str}'))
    return res


# ---- better displacement scan: raw byte hits grouped per function, then linear decode ----
def raw_disp_positions(disp):
    t = np.frombuffer(TEXT, dtype=np.uint8)
    if -128 <= disp <= 127:
        raise ValueError('use disp32 only')
    pat = struct.pack('<i', disp)
    m = (t[:-3] == pat[0]) & (t[1:-2] == pat[1]) & (t[2:-1] == pat[2]) & (t[3:] == pat[3])
    return np.nonzero(m)[0]  # offsets in TEXT

def funcs_with_disps(disps):
    """dict func_start -> set of disps whose raw 4-byte pattern occurs inside the function."""
    f = funcs()
    starts = f[:, 0]
    out = {}
    for d in disps:
        pos = raw_disp_positions(d) + TVA
        idx = np.searchsorted(starts, pos, side='right') - 1
        for p, i in zip(pos, idx):
            if i < 0:
                continue
            b, e = int(f[i, 0]), int(f[i, 1])
            if b <= p < e:
                out.setdefault(b, {}).setdefault(d, []).append(int(p))
    return out

def linear_dis(start, end):
    """Linear decode [start,end) rva; returns list of capstone insns."""
    return list(_CAP.disasm(TEXT[start - TVA:end - TVA], IBASE + start))

def mem_disp_insns(start, end, disp_set):
    out = []
    for ins in linear_dis(start, end):
        for op in ins.operands:
            if op.type == CS_OP_MEM and op.mem.base != X86_REG_RIP and op.mem.disp in disp_set:
                out.append(ins)
                break
    return out


# ---- aligned displacement scan using .pdata range starts for instruction alignment ----
_RANGE_CACHE = {}
def _range_insns(b, e):
    k = (b, e)
    r = _RANGE_CACHE.get(k)
    if r is None:
        r = linear_dis(b, e)
        if len(_RANGE_CACHE) > 20000:
            _RANGE_CACHE.clear()
        _RANGE_CACHE[k] = r
    return r

def disp_hits2(disp, fl_only=False, extra_filter=None):
    """Aligned scan: list of (rva, text, regs) for instructions with a non-RIP memory operand
    having displacement == disp. Uses raw byte matches to pick .pdata ranges, then decodes them."""
    f = funcs()
    starts = f[:, 0]
    pos = raw_disp_positions(disp) + TVA
    idx = np.searchsorted(starts, pos, side='right') - 1
    ranges = set()
    for p, i in zip(pos, idx):
        if i >= 0 and f[i, 0] <= p < f[i, 1]:
            ranges.add((int(f[i, 0]), int(f[i, 1])))
    res = []
    for (b, e) in sorted(ranges):
        for ins in _range_insns(b, e):
            for op in ins.operands:
                if op.type == CS_OP_MEM and op.mem.base != X86_REG_RIP and op.mem.disp == disp:
                    res.append(ins)
                    break
    out = []
    FL = ('movss','mulss','divss','addss','subss','comiss','ucomiss','minss','maxss','cvtss2sd','sqrtss','cvttss2si','cvtss2si','movaps','movups','andps','xorps')
    for ins in res:
        if fl_only and ins.mnemonic not in FL:
            continue
        out.append((ins.address - IBASE, f'{ins.mnemonic} {ins.op_str}'))
    return out
