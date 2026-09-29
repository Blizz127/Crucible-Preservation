#!/usr/bin/env python3
"""Verify the fixed retail animation serializer against a saved live graph.

Read-only with respect to the game: no process access, launch, or packets.
Optional --json saves the verified source map. This follows the generated
shared serializer's descriptor accesses, not animation registration order.
The RVA anchors below deliberately accept only the captured executable hash.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import struct

import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_OP_IMM, CS_OP_MEM
from capstone.x86_const import X86_REG_RAX, X86_REG_RIP


ROOT = Path(__file__).resolve().parents[1]
EXE = os.environ.get('CRUCIBLE_EXE', os.path.expanduser(
    '~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe'))
CAPTURE = ROOT / 'snapshots/systems-20260922/live-animation-20260922T174627Z.json'
EXE_SHA256 = 'e28643747df4a4eccf6a0f5490a3a1c4ab0a6a75b2cee62d3c62f2b191d18203'

# Exact name-LEA, serializer-call, descriptor-pointer-load sites. The last
# site accesses componentInfo->properties[slot] then reads its record at +20.
# SyncInfo's name LEA lives in its wrapper, reached by the listed call site.
ANCHORS = (
    (0x7e3c25, 0x7e3c3d, 0x7e3c51),
    (0x7e3c86, 0x7e3c9e, 0x7e3cb2),
    (0x7e3ce8, 0x7e3d00, 0x7e3d14),
    (0x7e3d4a, 0x7e3d62, 0x7e3d76),
    (0x7e3dac, 0x7e3dc4, 0x7e3dd8),
    (0x7e3e0e, 0x7e3e26, 0x7e3e3a),
    (0x7e3e70, 0x7e3e88, 0x7e3e9c),
    (0x7e3ed2, 0x7e3eea, 0x7e3efe),
    (0x7e3f34, 0x7e3f4c, 0x7e3f60),
    (0x7e3f96, 0x7e3fae, 0x7e3fc2),
    (0x7e3ff8, 0x7e4010, 0x7e4024),
    (0x7e405a, 0x7e4072, 0x7e4086),
    (0x7e40bc, 0x7e40d4, 0x7e40e8),
    (0x7e411e, 0x7e4136, 0x7e414a),
    (0x7e4180, 0x7e4198, 0x7e41ac),
    (0x7e41e2, 0x7e41fa, 0x7e420e),
    (0x7e4244, 0x7e425c, 0x7e4270),
    (0x7e42a9, 0x7e42c1, 0x7e42d5),
    (0x7e430e, 0x7e4326, 0x7e433a),
    (0x7e4373, 0x7e438b, 0x7e439f),
    (0x7f3055, 0x7e4431, 0x7e4487),
    (0x7e4499, 0x7e44b5, 0x7e44c6),
    (0x7e4507, 0x7e451f, 0x7e4533),
    (0x7e4560, 0x7e457c, 0x7e458d),
    (0x7e45ce, 0x7e45e6, 0x7e45fa),
    (0x7e4653, 0x7e469f, 0x7e4793),
)
HELPERS = {
    0x7f32c0: ('float', 'IEEE754 binary32, big endian, 4 bytes'),
    0x786620: ('bool', 'LSB-first bit in shared bool cursor; not a byte per field'),
    0x7f33c0: ('vec3', 'three big-endian binary32 values, X/Y/Z, 12 bytes'),
    0x7f3040: ('sync_info', 'Size range 0..5, then selected uint32 payload elements'),
    0x4f45a30: ('packed_quat', 'network branch packed quaternion uint32 big endian'),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inspect(executable, capture):
    image = Path(executable).read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    require(digest == EXE_SHA256, 'executable does not match the fixed RVA evidence')
    capture_bytes = Path(capture).read_bytes()
    saved = json.loads(capture_bytes)
    require(saved['executable_sha256'] == digest, 'capture executable hash differs')
    components = [component for entity in saved['entities']
                  for component in entity['components']
                  if component.get('getter_bytes', '').startswith('b00dc3')]
    require(bool(components), 'capture contains no type-13 components')
    records = components[0]['property_record_indices']
    require(records == list(range(1, 22)) + list(range(28, 33)),
            'unexpected property descriptor vector')
    for component in components:
        require(component['vtable'] == '0x66caf20', 'unexpected animation vtable')
        require(component['property_record_indices'] == records,
                'animation descriptor vectors disagree')
        graph = component['replication_graph']
        require(len(graph) == 34 and graph[1] == [0, 0, 2, 0, 0],
                'unexpected animation graph root')
        node, visited = graph[1][2], []
        while node:
            require(node < len(graph) and node not in visited, 'invalid graph links')
            visited.append(node)
            record, sibling, child, _, _ = graph[node]
            require(child == (23 if record == 21 else 0),
                    'unexpected root property children')
            node = sibling
        require([graph[node][0] for node in visited] == records,
                'root traversal disagrees with descriptor vector')
        require([graph[i][0:4] for i in range(23, 29)] ==
                [[i - 1, i + 1 if i < 28 else 0, 0, 1] for i in range(23, 29)],
                'unexpected SyncInfo child graph')

    pe = pefile.PE(data=image, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    cap = Cs(CS_ARCH_X86, CS_MODE_64)
    cap.detail = True

    def instruction(rva):
        result = next(cap.disasm(pe.get_data(rva, 15), base + rva), None)
        require(result is not None, f'no instruction at {rva:#x}')
        return result

    def text_at(rva):
        return pe.get_data(rva, 160).split(b'\0', 1)[0].decode('ascii')

    def call_target(rva):
        ins = instruction(rva)
        require(ins.mnemonic == 'call' and ins.operands[0].type == CS_OP_IMM,
                f'expected direct call at {rva:#x}')
        return ins.operands[0].imm - base

    def pointer(rva):
        return struct.unpack('<Q', pe.get_data(rva, 8))[0] - base

    require(pointer(0x66caf20 + 0x80) == 0x7e35a0, 'unexpected shared delta entry')
    require(call_target(0x7e37e5) == 0x7e3a10, 'unexpected shared serializer')
    for slot, target in ((0x28, 0x4f56730), (0x38, 0x4f566f0), (0x78, 0x4f565d0)):
        require(pointer(0x6d875b0 + slot) == target, 'unexpected input serializer slot')

    rows = []
    for expected_slot, (name_site, call_site, descriptor_site) in enumerate(ANCHORS):
        ins = instruction(name_site)
        require(ins.mnemonic == 'lea' and ins.operands[1].type == CS_OP_MEM
                and ins.operands[1].mem.base == X86_REG_RIP,
                f'expected RIP name LEA at {name_site:#x}')
        name_rva = ins.address + ins.size + ins.operands[1].mem.disp - base
        name = text_at(name_rva)
        helper = call_target(call_site)
        require(helper in HELPERS, f'unknown serializer helper {helper:#x}')
        kind, wire = HELPERS[helper]
        descriptor = instruction(descriptor_site)
        require(descriptor.mnemonic == 'mov' and descriptor.operands[1].type == CS_OP_MEM
                and descriptor.operands[1].mem.base == X86_REG_RAX
                and descriptor.operands[1].mem.disp == 8 * expected_slot,
                f'wrong descriptor slot at {descriptor_site:#x}')
        record_read = instruction(descriptor_site + descriptor.size)
        require(record_read.mnemonic == 'movzx' and record_read.operands[1].size == 2
                and record_read.operands[1].mem.disp == 0x20,
                f'expected descriptor record read after {descriptor_site:#x}')
        rows.append(dict(name=name, descriptor_slot=expected_slot,
                         record_index=records[expected_slot], kind=kind, wire=wire,
                         encoder_supported=kind in ('float', 'bool'),
                         name_rva=hex(name_rva), name_lea_rva=hex(name_site),
                         serializer_call_rva=hex(call_site), helper_rva=hex(helper),
                         descriptor_load_rva=hex(descriptor_site)))
    require(len({row['name'] for row in rows}) == 26, 'duplicate property names')
    require(not any(row['name'] == 'velocity' for row in rows),
            'unexpected velocity property')

    # Include the actual instructions, not just the inferred descriptions.
    spans = ((0x7f32c0, 0x7f3375), (0x786620, 0x7866d5),
             (0x4f56730, 0x4f567ad), (0x4f565d0, 0x4f566c0))
    evidence = {f'{start:#x}:{end:#x}': [
        f'{ins.address - base:#x} {ins.mnemonic} {ins.op_str}'
        for ins in cap.disasm(pe.get_data(start, end - start), base + start)]
        for start, end in spans}
    pe.parse_data_directories(directories=[1])
    ntohl = [f'{entry.dll.decode()}!{item.name.decode()}'
             for entry in pe.DIRECTORY_ENTRY_IMPORT for item in entry.imports
             if item.address == base + 0x6244728 and item.name]
    require(ntohl == ['WS2_32.dll!ntohl'], 'float reader import differs')
    require(struct.unpack('<2Q', pe.get_data(0x6e02680, 16)) == (0, 5),
            'SyncInfo size range differs')
    require(struct.unpack('<2Q', pe.get_data(0x6e02b70, 16)) == (0, 0xffffffff),
            'SyncInfo element payload range differs')
    element_names = [text_at(pointer(0x6604350 + i * 8)) for i in range(5)]
    pe.close()
    return dict(
        inspected_at=datetime.now(timezone.utc).isoformat(),
        executable=str(Path(executable).resolve()), executable_sha256=digest,
        capture=str(Path(capture).resolve()),
        capture_sha256=hashlib.sha256(capture_bytes).hexdigest(),
        capture_observed_at=saved['observed_at'], matched_components=len(components),
        method='fixed-build instruction anchors joined to captured descriptor indices',
        component_type=13, vtable_rva='0x66caf20', delta_slot='0x80',
        delta_serializer_rva='0x7e35a0', shared_serializer_rva='0x7e3a10',
        property_record_indices=records, properties=rows,
        scalar_instruction_evidence=evidence,
        float_ntohl_call_rva='0x4f56770', float_ntohl_import=ntohl[0],
        sync_info_children=[dict(name='Size', record_index=22, range=[0, 5])] +
                           [dict(name=name, record_index=23 + i, wire='uint32 big endian')
                            for i, name in enumerate(element_names)],
        unresolved=['Meaning of AnimationSyncInfo element payload bits',
                    'Native policy/writer for velocity and movement time',
                    'Visible runtime acceptance of authored animation updates'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', default=EXE)
    parser.add_argument('--capture', type=Path, default=CAPTURE)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    try:
        result = inspect(args.exe, args.capture)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f'animation wire verification failed: {error}\n')
    output = json.dumps(result, indent=2) + '\n'
    if args.json:
        args.json.write_text(output)
        print(f'Verified {len(result["properties"])} properties across '
              f'{result["matched_components"]} captured components: {args.json}')
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
