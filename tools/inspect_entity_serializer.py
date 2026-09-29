#!/usr/bin/env python3
"""Verify the retail UDP entity-message serializer scope, without process access.

Fixed-build instruction anchors connect the UDP provider subobject to the
packet callback and per-entity factory. This establishes construction/reset
scope; it does not claim a live packet or visual gameplay acceptance.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import struct

import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64


EXE_SHA256 = 'e28643747df4a4eccf6a0f5490a3a1c4ab0a6a75b2cee62d3c62f2b191d18203'
ANCHORS = (
    (0xe21b76, 'lea', 'rdx, [rcx + 8]'),
    (0xe21baf, 'add', 'rcx, 0x38'),
    (0xe21bc0, 'call', '0x144f226f0'),
    (0x4f22728, 'mov', 'qword ptr [rcx + 8], rdx'),
    (0xe21beb, 'lea', 'rax, [rip + 0x5f5f3e6]'),
    (0xe21bf2, 'mov', 'qword ptr [rdi + 0xf8], rax'),
    (0x4f1e6ec, 'mov', 'r13, rcx'),
    (0x4f1ed24, 'mov', 'rdx, qword ptr [r13 + 8]'),
    (0x4f1ed39, 'lea', 'rcx, [r13 + 0xc0]'),
    (0x4f1ed40, 'call', '0x144f20d90'),
    (0x4f20dc0, 'mov', 'rbx, rcx'),
    (0x4f20dce, 'mov', 'r14, rdx'),
    (0x4f20e91, 'mov', 'r8, r14'),
    (0x4f20e94, 'mov', 'rdx, rbx'),
    (0x4f20e97, 'call', '0x144f249b0'),
    (0x4f249fa, 'mov', 'qword ptr [rbp - 0x48], r8'),
    (0x4f249fe, 'mov', 'qword ptr [rbp - 0x50], rdx'),
    (0x4f2570c, 'mov', 'r14, qword ptr [rbp - 0x50]'),
    (0x4f25724, 'mov', 'rcx, qword ptr [rbp - 0x48]'),
    (0x4f25734, 'mov', 'rdx, r14'),
    (0x4f2573a, 'call', 'qword ptr [rax + 0x10]'),
    (0xe233f5, 'mov', 'rsi, rdx'),
    (0xe23402, 'mov', 'rcx, rsi'),
    (0xe23405, 'call', '0x140e24300'),
    (0xe24339, 'mov', 'qword ptr [rbp - 0x80], rcx'),
    (0xe2515a, 'mov', 'r13, qword ptr [rbp - 0x80]'),
    (0xe251cf, 'mov', 'rdx, r13'),
    (0xe251d2, 'call', '0x144bb5370'),
    (0x4bb539d, 'mov', 'rbx, rdx'),
    (0x4bb544c, 'mov', 'rax, qword ptr [rbx]'),
    (0x4bb5471, 'call', 'qword ptr [rax + 0x38]'),
    (0x4f20819, 'lea', 'rcx, [rip + 0x183f3c0]'),
    (0x4f20824, 'mov', 'qword ptr [rax], rcx'),
    (0x4f20827, 'mov', 'dword ptr [rax + 0x10], 0'),
    (0x4f20839, 'mov', 'dword ptr [rax + 0x24], 0'),
    (0x4f20840, 'mov', 'byte ptr [rax + 0x28], 0'),
)


def inspect(executable):
    image = Path(executable).read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    if digest != EXE_SHA256:
        raise ValueError('executable does not match fixed RVA evidence')
    pe = pefile.PE(data=image, fast_load=True)
    cap = Cs(CS_ARCH_X86, CS_MODE_64)
    base = pe.OPTIONAL_HEADER.ImageBase
    evidence = []
    for rva, mnemonic, operands in ANCHORS:
        ins = next(cap.disasm(pe.get_data(rva, 15), base + rva), None)
        if ins is None or (ins.mnemonic, ins.op_str) != (mnemonic, operands):
            raise ValueError(f'instruction anchor differs at {rva:#x}')
        evidence.append(f'{rva:#x} {mnemonic} {operands}')
    pointers = ((0x67a2810 + 0x10, 0xe233e0),
                (0x6d80fd8 + 0x38, 0x4f207c0),
                (0x675fbe0 + 0x78, 0xc52180),
                (0x675fbe0 + 0x28, 0xc51ec0))
    for address, target in pointers:
        if struct.unpack('<Q', pe.get_data(address, 8))[0] - base != target:
            raise ValueError(f'vtable target differs at {address:#x}')
    # Include complete factory/reader bytes as disassembly for independent review.
    spans = ((0x4f207c0, 0x4f2085e), (0xc52180, 0xc52210),
             (0xc51ec0, 0xc51fa0),
             (0xe2518d, 0xe25234), (0x4bb57da, 0x4bb580f))
    detail = {f'{start:#x}:{end:#x}': [
        f'{ins.address-base:#x} {ins.mnemonic} {ins.op_str}'
        for ins in cap.disasm(pe.get_data(start, end-start), base+start)]
        for start, end in spans}
    pe.close()
    return dict(inspected_at=datetime.now(timezone.utc).isoformat(),
                executable_sha256=digest, evidence_kind='static retail control flow',
                provider_agent_offset='0xf8', provider_vtable_rva='0x6d80fd8',
                factory_rva='0x4f207c0', serializer_vtable_rva='0x675fbe0',
                boolean_reader_rva='0xc52180', scope='one serializer per entity message',
                float_reader_rva='0xc51ec0',
                anchors=evidence, instruction_evidence=detail,
                live_call_observed=False, process_access=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', default=os.environ.get('CRUCIBLE_EXE',
        os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe')))
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    try:
        output = json.dumps(inspect(args.exe), indent=2) + '\n'
    except (OSError, ValueError) as error:
        parser.exit(1, f'entity serializer verification failed: {error}\n')
    if args.json:
        args.json.write_text(output)
        print(f'Verified UDP entity serializer scope: {args.json}')
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
