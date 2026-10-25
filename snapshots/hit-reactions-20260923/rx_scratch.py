"""Scratch static-RE helper (read-only). Caches .text/.rdata/.pdata as npy."""
import os, struct, bisect, sys, pickle
import numpy as np
from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_OP_MEM, CS_OP_IMM
from capstone.x86_const import X86_REG_RIP

EXE = os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe')
CACHE = '/tmp/claude-1000/hr/cache.pkl'

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

def cstr(rva, n=200):
    b = read(rva, n).split(b'\0', 1)[0]
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

EA_NPY = '/tmp/claude-1000/hr/ea.npy'
_EA = None
def _ea():
    global _EA
    if _EA is None:
        if not os.path.exists(EA_NPY):
            t = np.frombuffer(TEXT, dtype=np.uint8)
            n = len(t) - 4
            d = np.frombuffer(TEXT[:n + 3], dtype=np.uint8)
            # little-endian int32 at every byte offset
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

def insn_at_ref(ref_rva):
    """Find the instruction containing ref field at ref_rva by trying starts."""
    cap = md()
    for back in range(1, 9):
        start = ref_rva - back
        code = TEXT[start - TVA:start - TVA + 16]
        for ins in cap.disasm(code, IBASE + start):
            if ins.address - IBASE + ins.size > ref_rva + 4 - 1 and ins.address - IBASE <= ref_rva - 1:
                for op in ins.operands:
                    if (op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP) or (
                            ins.mnemonic in ('call', 'jmp') or ins.mnemonic.startswith('j')):
                        return ins
            break
    return None

def xrefs(target):
    out = []
    for r in refs(target):
        ins = insn_at_ref(r)
        if ins is None:
            continue
        a = ins.address - IBASE
        ok = False
        for op in ins.operands:
            if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                ok = ins.address + ins.size + op.mem.disp - IBASE == target
            elif op.type == CS_OP_IMM and ins.mnemonic in ('call', 'jmp') or ins.mnemonic.startswith('j'):
                ok = ok or (op.type == CS_OP_IMM and op.imm - IBASE == target)
        if ok:
            out.append((a, ins.mnemonic + ' ' + ins.op_str))
    return out

def md():
    c = Cs(CS_ARCH_X86, CS_MODE_64)
    c.detail = True
    return c

_ALL = None
def find_bytes(pat, limit=20):
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

def note(ins):
    for op in ins.operands:
        if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
            tgt = ins.address + ins.size + op.mem.disp - IBASE
            s = cstr(tgt)
            if s:
                return f'  ; {tgt:#x} "{s[:90]}"'
            if ins.mnemonic.startswith(('movss', 'movsd', 'mulss', 'addss', 'subss', 'divss', 'comiss', 'ucomiss', 'minss', 'maxss', 'cvt', 'andps', 'xorps', 'movaps', 'movups')):
                try:
                    f = struct.unpack('<f', read(tgt, 4))[0]
                    return f'  ; {tgt:#x} f32={f!r}'
                except Exception:
                    pass
            return f'  ; {tgt:#x}'
    return ''

def dis(start, end=None, show=True):
    if end is None:
        b, e = func_of(start)
        start, end = b, e
    cap = md()
    lines = []
    for ins in cap.disasm(TEXT[start - TVA:end - TVA], IBASE + start):
        a = ins.address - IBASE
        op = ins.op_str
        if ins.mnemonic in ('call', 'jmp') or ins.mnemonic.startswith('j'):
            op = op.replace(hex(IBASE)[:-7], '0x') if False else op
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

def strings_rva(pat):
    return find_bytes(pat.encode() if isinstance(pat, str) else pat)

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
