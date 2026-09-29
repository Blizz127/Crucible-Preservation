#!/usr/bin/env python3
"""Recover `AnimationParameterComponent`'s replicated-property table statically.

The client registers each animation parameter in one straight-line block of
`.text`.  Each registration is the same five-instruction shape:

    movdqa xmm0, [rip + K]          ; 16-byte property descriptor constant
    lea    rcx, [rbx + OFF]         ; the property member in the component
    mov    dword [rbp-0x38], FLAGS
    lea    rNN, "float" / "bool"    ; the reflected type name (hoisted)
    call   <property factory>
    lea    r9,  [rbp-0x40]
    lea    r8,  "<parameter name>"  ; the animation-graph parameter name
    call   <register>

so walking the block in address order yields the registration order, each
parameter's member offset inside the component, its reflected scalar type and
its flag word.  That is the ordering `docs/22-creature-ai.md` recovered by hand;
this reproduces it mechanically and adds the types and flags.

Read-only: disassembles the installed executable, never writes or launches it.

Usage:
  python3 tools/inspect_anim_params.py                     # default block
  python3 tools/inspect_anim_params.py --range 0x7e2bd0:0x7e3599
  python3 tools/inspect_anim_params.py --json out.json
"""
import argparse
import json
import os
import struct
import sys

import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_OP_MEM
from capstone.x86_const import X86_REG_RIP

EXE = os.environ.get('CRUCIBLE_EXE', os.path.expanduser(
    '~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe'))

# AnimationParameterComponent's parameter registration block, named in
# docs/22-creature-ai.md.
DEFAULT_RANGE = (0x7e2bd0, 0x7e3599)


def cstring(pe, rva, limit=96):
    try:
        raw = pe.get_data(rva, limit)
    except Exception:
        return None
    raw = raw.split(b'\0', 1)[0]
    if not raw or any(c < 0x20 or c > 0x7e for c in raw):
        return None
    return raw.decode('ascii')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--range', default=None,
                    help='hex START:END inside .text (default the anim block)')
    ap.add_argument('--json')
    args = ap.parse_args()

    start, end = DEFAULT_RANGE
    if args.range:
        start, end = (int(v, 16) for v in args.range.split(':'))

    pe = pefile.PE(EXE, fast_load=True)
    text = next(s for s in pe.sections
                if s.Name.rstrip(b'\0') == b'.text')
    base = pe.OPTIONAL_HEADER.ImageBase
    tbase = text.VirtualAddress
    code = text.get_data()[start - tbase:end - tbase]

    cap = Cs(CS_ARCH_X86, CS_MODE_64)
    cap.detail = True

    # Type-name strings are hoisted into callee-saved registers ("float" into
    # r15, "bool" into r14 in this build), so track every register that holds a
    # known type-name pointer and report whichever one the property used last.
    regtype = {}
    pending = {}
    rows = []

    for ins in cap.disasm(code, base + start):
        rva = ins.address - base
        target = None
        for op in ins.operands:
            if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                target = (ins.address + ins.size + op.mem.disp) - base

        # lea rcx, [rbx + OFF] -> the property member inside the component.
        # Not RIP-relative, so it is handled before the string-lea branch.
        if (ins.mnemonic == 'lea' and target is None
                and 'rbx +' in ins.op_str):
            dest = ins.op_str.split(',')[0].strip()
            try:
                offset = int(ins.op_str.split('+')[1].strip(' ]'), 16)
            except ValueError:
                offset = None
            if offset is not None and dest in ('rcx', 'rdx'):
                pending['member'] = offset
            continue

        if ins.mnemonic == 'lea' and target is not None:
            text_value = cstring(pe, target)
            dest = ins.op_str.split(',')[0].strip()
            if text_value in ('float', 'bool', 'int', 'double',
                              'unsigned int', 'Vector3', 'Vector2'):
                regtype[dest] = text_value
                continue
            if text_value and dest == 'r8':
                # the parameter name; close out the pending registration
                rows.append({
                    'index': len(rows),
                    'name': text_value,
                    'name_rva': hex(target),
                    'member_offset': (hex(pending['member'])
                                      if 'member' in pending else None),
                    'type': pending.get('type'),
                    'flags': (hex(pending['flags'])
                              if 'flags' in pending else None),
                    'descriptor_rva': (hex(pending['descriptor'])
                                       if 'descriptor' in pending else None),
                    'register_site': hex(rva),
                })
                pending = {}
                continue
            # lea rcx, [rbx + OFF] -> the property member inside the component
            if ('rcx' in ins.op_str.split(',')[0] and 'rbx' in ins.op_str
                    and '+' in ins.op_str):
                try:
                    pending['member'] = int(ins.op_str.split('+')[1]
                                            .strip(' ]'), 16)
                except ValueError:
                    pass
                # whichever type register was loaded most recently and is still
                # live is the one this property will pass
                continue

        if ins.mnemonic == 'movdqa' and target is not None:
            pending['descriptor'] = target
        if (ins.mnemonic == 'mov' and 'dword ptr [rbp - 0x38]' in ins.op_str):
            literal = ins.op_str.rsplit(',', 1)[1].strip()
            try:
                pending['flags'] = int(literal, 0)
            except ValueError:
                pending['flags'] = 0        # zeroed register (r13d == 0 here)
        if ins.mnemonic == 'mov' and 'qword ptr [rbp - 0x20]' in ins.op_str:
            reg = ins.op_str.rsplit(',', 1)[1].strip()
            if reg in regtype:
                pending['type'] = regtype[reg]

    pe.close()

    print(f'# {EXE}')
    print(f'# registration block [{start:#x},{end:#x}]  '
          f'{len(rows)} parameters')
    print(f'{"idx":>3} {"name":28} {"member":>9} {"type":8} {"flags":>9} descriptor')
    for r in rows:
        print(f'{r["index"]:>3} {r["name"]:28} {str(r["member_offset"]):>9} '
              f'{str(r["type"]):8} {str(r["flags"]):>9} {r["descriptor_rva"]}')

    if args.json:
        with open(args.json, 'w') as handle:
            json.dump({'executable': EXE,
                       'range': [hex(start), hex(end)],
                       'parameters': rows}, handle, indent=2)
        print(f'# wrote {args.json}')


if __name__ == '__main__':
    main()
