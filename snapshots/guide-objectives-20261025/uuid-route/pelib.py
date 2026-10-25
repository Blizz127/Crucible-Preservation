"""Shared helpers: map the Crucible PE into an RVA-addressable image (read-only)."""
import os, struct, bisect, json
import pefile
EXE = os.path.expanduser("~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe")
REPO = "/home/blizz/dev/Crucible Revival"
BASE = 0x140000000

class Img:
    def __init__(self):
        self.pe = pefile.PE(EXE, fast_load=True)
        raw = open(EXE, 'rb').read()
        self.raw = raw
        self.size = self.pe.OPTIONAL_HEADER.SizeOfImage
        self.mem = bytearray(self.size)
        self.secs = []
        for s in self.pe.sections:
            name = s.Name.rstrip(b'\0').decode()
            va, vs, ro, rs = s.VirtualAddress, s.Misc_VirtualSize, s.PointerToRawData, s.SizeOfRawData
            n = min(vs, rs) if vs else rs
            self.mem[va:va+n] = raw[ro:ro+n]
            self.secs.append((name, va, max(vs, rs), s.Characteristics))
        self.sec = {n: (va, sz) for n, va, sz, _ in self.secs}
        t = self.sec['.text']; self.text = (t[0], t[0]+t[1])
        r = self.sec['.rdata']; self.rdata = (r[0], r[0]+r[1])
        d = self.sec['.data']; self.data = (d[0], d[0]+d[1])
        # pdata function table
        pd_va, pd_sz = self.sec['.pdata']
        self.funcs = []
        for i in range(0, pd_sz - 11, 12):
            b, e, u = struct.unpack_from('<III', self.mem, pd_va + i)
            if e > b:
                self.funcs.append((b, e, u))
        self.funcs.sort()
        self.fstarts = [f[0] for f in self.funcs]
    def u8(self, rva): return self.mem[rva]
    def u16(self, rva): return struct.unpack_from('<H', self.mem, rva)[0]
    def u32(self, rva): return struct.unpack_from('<I', self.mem, rva)[0]
    def i32(self, rva): return struct.unpack_from('<i', self.mem, rva)[0]
    def u64(self, rva): return struct.unpack_from('<Q', self.mem, rva)[0]
    def bytes(self, rva, n): return bytes(self.mem[rva:rva+n])
    def ptr_rva(self, rva):
        """Read a 64-bit absolute VA at rva and return it as an RVA (or None if not an image VA)."""
        v = self.u64(rva)
        if BASE <= v < BASE + self.size:
            return v - BASE
        return None
    def in_text(self, rva): return self.text[0] <= rva < self.text[1]
    def func_of(self, rva):
        i = bisect.bisect_right(self.fstarts, rva) - 1
        if i < 0: return None
        b, e, u = self.funcs[i]
        return (b, e) if b <= rva < e else None
    def cstr(self, rva, maxlen=300):
        end = self.mem.find(b'\0', rva, rva+maxlen)
        if end < 0: end = rva+maxlen
        return bytes(self.mem[rva:end])

def uuid_bytes(u):
    """AZ::Uuid in-memory layout (big-endian bytes of the canonical 16)."""
    import uuid
    return uuid.UUID(u).bytes

def load_marine():
    d = json.load(open(f"{REPO}/snapshots/guide-objectives-20261025/marine-uuid-types.json"))
    return d['components']

def load_live():
    d = json.load(open(f"{REPO}/snapshots/gamemode-20260919/hives-first-live.json"))
    return d

def load_targets():
    return json.load(open(f"{REPO}/snapshots/guide-objectives-20261025/uncovered-uuids.json"))

def load_schema():
    return json.load(open(f"{REPO}/snapshots/gamemode-20260919/component-replication-schema.json"))

import numpy as np
class Xrefs:
    """RIP-relative reference index over .text (lea/mov/call/jmp/cmp etc with modrm rip) built with numpy."""
    def __init__(self, im):
        self.im = im
        t0, t1 = im.text
        arr = np.frombuffer(im.mem, dtype=np.uint8, count=t1-t0, offset=t0)
        n = len(arr)
        # candidate modrm rip-relative: modrm byte with mod=00 rm=101 => (b & 0xC7)==0x05
        # opcode positions: lea 8D, mov 8B/89/C7(imm)/..., we handle lea & mov & others by generic: any instr whose modrm is rip-rel.
        # Specialised: lea r64,[rip+d]: (48|4C) 8D modrm(05|0D|15|1D|25|2D|35|3D) d32
        idx = np.nonzero((arr[1:n-6] == 0x8D) & ((arr[2:n-5] & 0xC7) == 0x05) & ((arr[0:n-7] & 0xF8) == 0x48))[0]
        # idx = position of REX byte; opcode at idx+1, modrm idx+2, disp idx+3..idx+7 ; instr len 7
        self.lea_sites = idx + t0
        disp = np.zeros(len(idx), dtype=np.int64)
        for k in range(4):
            disp |= arr[idx+3+k].astype(np.int64) << (8*k)
        disp = np.where(disp >= 2**31, disp - 2**32, disp)
        self.lea_targets = (idx + 7 + disp) + t0
        order = np.argsort(self.lea_targets, kind='stable')
        self.lt_sorted = self.lea_targets[order]
        self.ls_sorted = self.lea_sites[order]
    def lea_refs(self, rva):
        lo = np.searchsorted(self.lt_sorted, rva, 'left')
        hi = np.searchsorted(self.lt_sorted, rva, 'right')
        return [int(x) for x in self.ls_sorted[lo:hi]]

from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_OP_MEM
from capstone.x86_const import X86_REG_RIP
_cs = Cs(CS_ARCH_X86, CS_MODE_64); _cs.detail = True
def disasm(im, rva, maxbytes=0x60, stop_ret=True, maxins=60):
    code = im.bytes(rva, maxbytes)
    out = []
    n = 0
    for ins in _cs.disasm(code, BASE + rva):
        r = ins.address - BASE
        note = ''
        for op in ins.operands:
            if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                tgt = (ins.address + ins.size + op.mem.disp) - BASE
                s = None
                try:
                    if 0 < tgt < im.size:
                        raw = im.cstr(tgt, 60)
                        if len(raw) >= 4 and all(32 <= c < 127 for c in raw):
                            s = raw.decode()
                except Exception: pass
                note = f'  ; rva={tgt:#x}' + (f' "{s}"' if s else '')
        out.append(f'  {r:#9x}  {ins.mnemonic:8s} {ins.op_str}{note}')
        n += 1
        if stop_ret and ins.mnemonic in ('ret', 'int3'):
            break
        if n >= maxins: break
    return '\n'.join(out)

class RipIndex:
    """All candidate RIP-relative displacement sites in .text (numpy). Verified lazily by capstone."""
    def __init__(self, im):
        self.im = im
        t0, t1 = im.text
        arr = np.frombuffer(im.mem, dtype=np.uint8, count=t1 - t0 + 8, offset=t0)
        n = t1 - t0
        cand = np.nonzero((arr[:n] & 0xC7) == 0x05)[0]          # modrm positions
        cand = cand[cand >= 3]
        d = np.zeros(len(cand), dtype=np.int64)
        for k in range(4):
            d |= arr[cand + 1 + k].astype(np.int64) << (8 * k)
        d = np.where(d >= 2**31, d - 2**32, d)
        self.pos = cand + t0                                     # rva of modrm byte
        self.end0 = self.pos + 5 + d                              # target if no trailing immediate
        self.d = d
    def find(self, target):
        """Return verified list of (instr_rva, mnemonic, op_str) referencing rva `target` via RIP-relative operand."""
        out = []
        for imm in (0, 1, 2, 4):
            m = np.nonzero(self.end0 + imm == target)[0]
            for i in m:
                p = int(self.pos[i])
                best = None
                for back in range(4, 0, -1):
                    s = p - back
                    code = self.im.bytes(s, 16)
                    for ins in _cs.disasm(code, BASE + s, 1):
                        if ins.size == back + 5 + imm:
                            for op in ins.operands:
                                if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                                    tgt = ins.address + ins.size + op.mem.disp - BASE
                                    if tgt == target and best is None:
                                        best = (s, ins.mnemonic, ins.op_str)
                        break
                if best: out.append(best)
        return sorted(set(out))
