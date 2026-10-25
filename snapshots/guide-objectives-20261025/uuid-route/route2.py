"""UUID -> {literal} -> parse init(s) -> (global G | inline) -> Uuid() fn F -> jmp thunk -> vtable (RTTI sig) -> slot 14 getter."""
import re, struct
from collections import defaultdict
from pelib import *
from pelib import _cs

HEXTBL = b'0123456789ABCDEFabcdef'
UUID_RE = re.compile(rb'\{([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\}')

class Route:
    def __init__(self):
        self.im = Img()
        im = self.im
        self.xr = Xrefs(im)
        self.ri = RipIndex(im)
        # literal table
        self.lits = defaultdict(list)
        lo = im.rdata[0]; hi = im.data[1]
        blob = bytes(im.mem[lo:hi])
        for m in UUID_RE.finditer(blob):
            self.lits[m.group(1).decode().lower()].append(lo + m.start())
        # qword pointer index: all 8-aligned qwords in .rdata/.data pointing into .text
        a = np.frombuffer(im.mem, dtype=np.uint64, count=(hi - lo) // 8, offset=lo)
        t0, t1 = im.text
        m = (a >= BASE + t0) & (a < BASE + t1)
        pos = (np.nonzero(m)[0] * 8 + lo).astype(np.int64)
        val = (a[m] - BASE).astype(np.int64)
        o = np.argsort(val, kind='stable')
        self.pv, self.pp = val[o], pos[o]
        # jmp rel32 thunk index
        arr = np.frombuffer(im.mem, dtype=np.uint8, count=t1 - t0 + 8, offset=t0)
        n = t1 - t0
        idx = np.nonzero(arr[:n - 5] == 0xE9)[0]
        d = np.zeros(len(idx), dtype=np.int64)
        for k in range(4):
            d |= arr[idx + 1 + k].astype(np.int64) << (8 * k)
        d = np.where(d >= 2**31, d - 2**32, d)
        tg = idx + 5 + d + t0
        o = np.argsort(tg, kind='stable')
        self.jt, self.js = tg[o], (idx + t0)[o]
        self._fcache = {}

    # ---------- helpers
    def ptrs_to(self, T):
        lo = np.searchsorted(self.pv, T, 'left'); hi = np.searchsorted(self.pv, T, 'right')
        return [int(x) for x in self.pp[lo:hi]]
    def thunks_to(self, F):
        lo = np.searchsorted(self.jt, F, 'left'); hi = np.searchsorted(self.jt, F, 'right')
        out = []
        for s in self.js[lo:hi]:
            s = int(s)
            if self.im.u8(s - 1) in (0xCC, 0xC3) or self.im.u8(s + 5) == 0xCC:
                out.append(s)
        return out
    def init_sites(self, s):
        out = []
        for d in range(0, 8):
            out += self.xr.lea_refs(s + d)
        return sorted(set(out))
    def analyze_fn(self, b, e):
        """Return (has_hextbl, [store globals], [load globals], n_ins)."""
        key = (b, e)
        if key in self._fcache: return self._fcache[key]
        im = self.im
        code = im.bytes(b, e - b)
        stores, loads, tbl, n = [], [], False, 0
        for ins in _cs.disasm(code, BASE + b):
            n += 1
            for op in ins.operands:
                if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                    tgt = ins.address + ins.size + op.mem.disp - BASE
                    if ins.mnemonic in ('movdqa', 'movups', 'movaps', 'movdqu'):
                        if ins.op_str.startswith('xmmword ptr'):
                            stores.append(tgt)
                        else:
                            loads.append(tgt)
                    elif ins.mnemonic == 'lea' and im.cstr(tgt, 24) == HEXTBL:
                        tbl = True
        r = (tbl, stores, loads, n)
        self._fcache[key] = r
        return r
    def is_rtti_vtable(self, p):
        """p = candidate slot-0 pointer location. Check slot 1 = lea rax,[name]; ret."""
        im = self.im
        s1 = im.ptr_rva(p + 8)
        if s1 is None or not im.in_text(s1): return None
        b = im.bytes(s1, 8)
        if b[0:3] == b'\x48\x8d\x05' and b[7] == 0xC3:
            tgt = s1 + 7 + im.i32(s1 + 3)
            nm = im.cstr(tgt, 120)
            if len(nm) >= 3 and all(32 <= c < 127 for c in nm):
                return nm.decode()
        return None
    def getter(self, vt, k=14):
        im = self.im
        g = im.ptr_rva(vt + 8 * k)
        if g is None: return ('none', None, None)
        g0 = g
        for _ in range(4):
            b = im.bytes(g, 6)
            if b[0] == 0xE9:
                g = g + 5 + im.i32(g + 1); continue
            break
        b = im.bytes(g, 4)
        if b[0] == 0xB0 and b[2] == 0xC3: return ('type', b[1], g0)
        if b[0] == 0x32 and b[1] == 0xC0 and b[2] == 0xC3: return ('zero', 0, g0)
        return ('other', None, g0)

    # ---------- the route
    def resolve(self, u):
        u = u.lower(); im = self.im
        ev = {'uuid': u, 'literals': [hex(x) for x in self.lits.get(u, [])], 'inits': [], 'F': set(), 'cands': []}
        Fs = {}   # F -> how found
        for s in self.lits.get(u, []):
            for site in self.init_sites(s):
                f = im.func_of(site)
                if not f: continue
                tbl, stores, loads, n = self.analyze_fn(*f)
                if not tbl: continue
                ev['inits'].append((hex(f[0]), [hex(x) for x in stores]))
                if stores:
                    # (A) init stores to global G; F = functions that load G
                    for G in set(stores):
                        for (site2, mn, op) in self.ri.find(G):
                            if mn in ('movaps', 'movups', 'movdqa', 'movdqu') and op.startswith('xmm'):
                                f2 = im.func_of(site2)
                                if f2 and f2[0] != f[0]:
                                    Fs.setdefault(f2[0], ('G', G, f2))
                    # (B) the init fn itself (inline parse + static) might be the vtable-referenced fn
                    Fs.setdefault(f[0], ('inline', None, f))
                else:
                    Fs.setdefault(f[0], ('inline', None, f))
        # candidate F filter: small functions
        seen_vt = set()
        for F, (how, G, f) in Fs.items():
            targets = [F] + self.thunks_to(F)
            for T in targets:
                for p in self.ptrs_to(T):
                    nm = self.is_rtti_vtable(p)
                    if nm is None: continue
                    if p in seen_vt: continue
                    seen_vt.add(p)
                    gt = self.getter(p)
                    ev['cands'].append({'vt': p, 'name': nm, 'F': F, 'thunk': T, 'how': how, 'G': G, 'getter': gt})
        ev['F'] = sorted(Fs)
        return ev
