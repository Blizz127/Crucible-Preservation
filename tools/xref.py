#!/usr/bin/env python3
"""Crucible static-RE helper: disassemble true (pdata-bounded) functions with
string + import annotations. No execution needed.

Usage:
  ./tools/xref.py --strings gamesparks_creds,connect   # find string RVAs + LEA xrefs
  ./tools/xref.py --func 0xb7a030                      # disassemble one function
  ./tools/xref.py --around NAME                        # disassemble funcs referencing a string
EXE default: Steam install of Crucible (override with CRUCIBLE_EXE).
"""
import os, re, struct, sys, bisect
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_OP_MEM
from capstone.x86_const import X86_REG_RIP

EXE = os.environ.get("CRUCIBLE_EXE",
    os.path.expanduser("~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe"))

def load():
    pe = pefile.PE(EXE)
    img = open(EXE, 'rb').read()
    secs = {s.Name.rstrip(b'\0').decode(): s for s in pe.sections}
    text = secs['.text']
    tbase, traw = text.VirtualAddress, text.get_data()
    praw = secs['.pdata'].get_data()
    funcs = []
    for i in range(0, len(praw) - 11, 12):
        b, e, _u = struct.unpack('<III', praw[i:i+12])
        if e > b:
            funcs.append((b, e))
    funcs.sort()
    iat = {}  # VA -> "dll!name"
    ibase = pe.OPTIONAL_HEADER.ImageBase
    for e in pe.DIRECTORY_ENTRY_IMPORT:
        dll = e.dll.decode()
        for f in e.imports:
            nm = f.name.decode() if f.name else f"ord{f.ordinal}"
            iat[f.address] = f"{dll}!{nm}"
    return pe, img, tbase, traw, funcs, iat, ibase

def va_str(img, pe, rva, maxlen=120):
    try:
        off = pe.get_offset_from_rva(rva)
    except Exception:
        return None
    raw = img[off:off+maxlen].split(b'\0', 1)[0]
    if len(raw) < 4 or any(c < 9 or (c < 32 and c not in (9, 10, 13)) or c > 126 for c in raw):
        return None
    return raw.decode()

def find_string(img, pat):
    offs, start = [], 0
    while True:
        i = img.find(pat, start)
        if i < 0:
            return offs
        offs.append(i)
        start = i + 1

def md():
    c = Cs(CS_ARCH_X86, CS_MODE_64)
    c.detail = True
    return c

def disasm_func(cap, pe, img, tbase, traw, iat, ibase, rva, before=0x250, after=0x120, full=False):
    secs = {s.Name.rstrip(b'\0').decode(): s for s in pe.sections}
    praw = secs['.pdata'].get_data()
    funcs = []
    for i in range(0, len(praw) - 11, 12):
        b, e, _u = struct.unpack('<III', praw[i:i+12])
        if e > b:
            funcs.append((b, e))
    funcs.sort()
    idx = bisect.bisect_right([f[0] for f in funcs], rva) - 1
    b, e = funcs[idx]
    end = e if full else min(e, rva + after)
    start = b if full else max(b, rva - before)
    code = traw[start - tbase:end - tbase]
    out = [f"func[{b:#x},{e:#x}] len={e-b:#x}"]
    for ins in cap.disasm(code, ibase + start):
        r = ins.address - ibase
        note = ""
        for op in ins.operands:
            if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:  # RIP-relative
                tgt = (ins.address + ins.size + op.mem.disp) - ibase
                if ins.mnemonic == 'lea':
                    s = va_str(img, pe, tgt)
                    note = f'               ; "{s}"' if s else f'               ; rva={tgt:#x}'
                elif ins.mnemonic in ('call', 'jmp') and (tgt + ibase) in iat:
                    note = f'               ; {iat[tgt + ibase]}'
                elif ins.mnemonic == 'mov':
                    s = va_str(img, pe, tgt)
                    if s:
                        note = f'               ; "{s[:80]}"'
                    elif (tgt + ibase) in iat:
                        note = f'               ; {iat[tgt + ibase]}'
        mark = '>>>' if start <= rva < ins.address - ibase + ins.size and r == rva or \
            (r <= rva < r + ins.size) else '   '
        out.append(f"{mark} {r:#10x} {ins.mnemonic:10s} {ins.op_str}{note}")
    return "\n".join(out)

def main():
    a = sys.argv[1:]
    pe, img, tbase, traw, funcs, iat, ibase = load()
    cap = md()
    if a[:1] == ['--strings'] and len(a) == 2:
        for pat in a[1].split(','):
            bpat = pat.encode()
            for off in find_string(img, bpat)[:8]:
                rva = pe.get_rva_from_offset(off)
                # LEA scan for this RVA
                d = traw
                hits = []
                i = 0
                n = len(d)
                while i < n - 7:
                    if d[i] == 0x8D and (d[i+1] & 0xC7) == 0x05:
                        disp = struct.unpack('<i', d[i+2:i+6])[0]
                        if tbase + i + 6 + disp == rva:
                            hits.append(tbase + i)
                        i += 6
                    else:
                        i += 1
                print(f"{pat!r} rva={rva:#x} lea_hits={[hex(h) for h in hits[:12]]}")
    elif a[:1] == ['--func'] and len(a) == 2:
        print(disasm_func(cap, pe, img, tbase, traw, iat, ibase, int(a[1], 16), full=True))
    elif a[:1] == ['--around'] and len(a) == 2:
        bpat = a[1].encode()
        offs = find_string(img, bpat)[:4]
        for off in offs:
            rva = pe.get_rva_from_offset(off)
            d = traw
            i, n = 0, len(d)
            while i < n - 7:
                if d[i] == 0x8D and (d[i+1] & 0xC7) == 0x05:
                    disp = struct.unpack('<i', d[i+2:i+6])[0]
                    if tbase + i + 6 + disp == rva:
                        print(disasm_func(cap, pe, img, tbase, traw, iat, ibase, tbase + i))
                        print()
                    i += 6
                else:
                    i += 1
    else:
        print(__doc__)
    pe.close()

if __name__ == '__main__':
    main()
