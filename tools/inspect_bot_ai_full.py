#!/usr/bin/env python3
"""Complete, labelled, tooltip-annotated dump of installed ObjectStream assets.

Companion to inspect_hunter_assets.py (which labels a subset). This one emits
EVERY element of the asset tree -- containers included -- and attaches, where
the client binary carries it, the reflected EditContext metadata for the field:
display name, tooltip description and unit suffix.

How the metadata is recovered (all static, read-only):

* Field names. ObjectStream stores crc32(lower(serialize name)). Every
  identifier-like literal in bin/Crucible.exe is hashed to build the reverse
  dictionary (same method as inspect_hunter_assets.py).
* SerializeContext::ClassBuilder::Field("Name", &T::member) compiles to
      mov r8d, <member offset> ; lea rdx, [rip+"Name"] ; call Field
  and, for class-typed members, to an inlined form that loads the name with
  lea r9 and writes the member offset into the new ClassElement.
* EditContext::ClassBuilder::DataElement(ui, &T::member, "Display", "Tooltip")
  compiles to
      lea rax, [rip+"Tooltip"] ; lea r9, [rip+"Display"] ;
      mov [rsp+20h], rax ; mov r8d, <member offset> ; call DataElement
  optionally followed by Attribute(Suffix, " meters") = lea r8, [rip+" meters"].
* A serialize record and an edit record describe the same member when they
  carry the same member offset and sit in the same Reflect() body (the edit
  block immediately follows the serialize block). The pairing distance is
  emitted so every attachment can be audited.

Nothing under the Steam install is written or executed.

Usage:
  python3 tools/inspect_bot_ai_full.py misc.pak 'ai/guide_dummies/marine_guide_dummy.ai' --json out.json
  python3 tools/inspect_bot_ai_full.py misc.pak 'ai/.*\\.ai$' --summary
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import objectstream  # noqa: E402
import pak  # noqa: E402

INSTALL = Path(os.environ.get(
    'CRUCIBLE_INSTALL',
    os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible')))
EXE = Path(os.environ.get('CRUCIBLE_EXE', INSTALL / 'bin' / 'Crucible.exe'))

# AzTypeInfo UUIDs of the scalar types seen in these assets (Lumberyard 1.x).
SCALARS = {
    'a0ca880c-afe4-43cb-926c-59ac48496112': ('bool', lambda v: bool(v[0])),
    'ea2c3e90-afbe-44d4-a90d-faaf79baf93d': ('float', lambda v: struct.unpack('>f', v)[0]),
    # AZ::Vector2 (inspect_creature_ai.py labels this UUID 'double'; the Marine
    # projectile's HealthChangeImmediateOnHit 41a00000 41200000 = (20, 10)
    # matches docs/20's Vec2 reading, so Vector2 is correct).
    '3d80f623-c85c-4741-90d0-e4e66164e6bf': ('Vector2', lambda v: list(struct.unpack('>2f', v))),
    '110c4b14-11a8-4e9d-8638-5051013a56ac': ('double', lambda v: struct.unpack('>d', v)[0]),
    '72b9409a-7d1a-4831-9cfe-fcb3fadd3426': ('u8', lambda v: v[0]),
    'ecfdbcc5-d7f5-4b25-9eb2-c7b3ceba7b8d': ('u16', lambda v: int.from_bytes(v, 'big')),
    '43da906b-7def-4ca8-9790-854106d3f983': ('u32', lambda v: int.from_bytes(v, 'big')),
    'd6597933-47cd-4fc8-b911-63f3e2b0993a': ('u64', lambda v: int.from_bytes(v, 'big')),
    '58422c0e-1e47-4854-98e6-34098f6fe12d': ('s8', lambda v: int.from_bytes(v, 'big', signed=True)),
    'b8a56d56-a10d-4dce-9f63-405ee243dd3c': ('s32', lambda v: int.from_bytes(v, 'big', signed=True)),
    '70d8a282-a1ea-462d-9d04-51edbd84c1c9': ('s64', lambda v: int.from_bytes(v, 'big', signed=True)),
    '8379eb7d-01fa-4538-b64b-a6543b4be73d': ('Vector3', lambda v: list(struct.unpack('>3f', v))),
}
STRING_PREFIX = b'\x01\xff\xff\xff'
IDENT = re.compile(rb'[A-Za-z][A-Za-z0-9_ ]{2,63}')


# --------------------------------------------------------------------------
# client binary: names and reflection metadata

class Binary:
    def __init__(self, path=EXE):
        import numpy as np
        import pefile
        self.np = np
        self.pe = pefile.PE(str(path), fast_load=True)
        self.image = Path(path).read_bytes()
        sections = {s.Name.rstrip(b'\0').decode(): s for s in self.pe.sections}
        text = sections['.text']
        self.text_rva = text.VirtualAddress
        self.text = text.get_data()
        self._rdata = [(s.VirtualAddress, s.VirtualAddress + s.Misc_VirtualSize,
                        s.PointerToRawData) for s in self.pe.sections]
        self._scan_lea()

    def _scan_lea(self):
        """Every RIP-relative `lea r64, [rip+disp32]` (REX.W 8D /r, mod=00 rm=101)."""
        np = self.np
        a = np.frombuffer(self.text, dtype=np.uint8)
        rex = (a[:-7] == 0x48) | (a[:-7] == 0x4c)
        hit = np.nonzero(rex & (a[1:-6] == 0x8d) & ((a[2:-5] & 0xc7) == 0x05))[0]
        disp = np.array([struct.unpack_from('<i', self.text, int(i) + 3)[0] for i in hit],
                        dtype=np.int64)
        site = hit.astype(np.int64) + self.text_rva
        self.lea_site = site
        self.lea_target = site + 7 + disp
        self.lea_reg = (((a[hit + 2] >> 3) & 7) + np.where(a[hit] == 0x4c, 8, 0)).astype(np.int64)

    def offset_of(self, rva):
        for start, end, raw in self._rdata:
            if start <= rva < end:
                return rva - start + raw
        return None

    def cstr(self, rva, limit=800):
        off = self.offset_of(int(rva))
        if off is None:
            return None
        raw = self.image[off:off + limit].split(b'\0', 1)[0]
        if len(raw) < 1 or any(c < 32 or c > 126 for c in raw):
            return None
        return raw.decode()

    def text_bytes(self, rva, size):
        start = rva - self.text_rva
        return self.text[max(0, start):start + size]

    def member_in_call(self, here):
        """Member offset passed in r8d to the call that consumes the LEA at `here`.

        Scans from the previous `call rel32` to the next one and takes
        `mov r8d, imm32` (41 B8) or `xor r8d, r8d` (45 33 C0 -> offset 0).
        Byte-level, so a false 0xE8 inside an operand can shorten the window;
        that only ever loses a record, it cannot invent one.
        """
        before = self.text_bytes(here - 0x20, 0x20)
        after = self.text_bytes(here + 7, 0x20)
        k = before.rfind(b'\xe8')
        if k >= 0 and k + 5 <= len(before):
            before = before[k + 5:]
        k = after.find(b'\xe8')
        if k >= 0:
            after = after[:k]
        pattern = re.compile(rb'\x41\xb8(....)|\x45\x33\xc0', re.S)
        found = pattern.search(after)
        if not found:
            hits = list(pattern.finditer(before))
            found = hits[-1] if hits else None
        if not found:
            return None
        return struct.unpack('<I', found.group(1))[0] if found.group(1) else 0

    def names(self):
        table = {}
        for match in IDENT.finditer(self.image):
            text = match.group().decode()
            table.setdefault(zlib.crc32(text.lower().encode()), set()).add(text)
        return {k: sorted(v, key=lambda s: (' ' in s, s.islower(), len(s), s))
                for k, v in table.items()}

    def reflection(self, wanted):
        """Serialize and edit records for field names in `wanted`.

        Returns (serialize, edits): serialize is a list of dicts
        {name, member, site, form}; edits is a list of dicts
        {display, tooltip, suffix, member, site}.
        """
        np = self.np
        order = np.argsort(self.lea_site)
        site, target, reg = self.lea_site[order], self.lea_target[order], self.lea_reg[order]
        text_cache = {}

        def s(rva):
            rva = int(rva)
            if rva not in text_cache:
                text_cache[rva] = self.cstr(rva)
            return text_cache[rva]

        serialize, edits = [], []
        wanted_lower = {w.lower() for w in wanted}
        # rdx (2) and r9 (9) loads of candidate names.
        for j in np.nonzero((reg == 2) | (reg == 9))[0]:
            name = s(target[j])
            if not name:
                continue
            here = int(site[j])
            if name.lower() in wanted_lower:
                if reg[j] == 2:
                    member = self.member_in_call(here)
                    if member is not None:
                        serialize.append({'name': name, 'member': member, 'site': here,
                                          'form': 'Field'})
                elif reg[j] == 9:
                    after = self.text_bytes(here, 0x70)
                    m = re.search(rb'[\x48\x49]\xc7[\x40-\x47]\x28(....)', after, re.S)
                    if m:
                        member = struct.unpack('<I', m.group(1))[0]
                        serialize.append({'name': name, 'member': member, 'site': here,
                                          'form': 'Field(inline)'})
            if reg[j] == 9:
                # DataElement: tooltip in rax just before, member in r8d just after.
                member = self.member_in_call(here)
                if member is None:
                    continue
                tooltip = None
                lo = np.searchsorted(site, here - 0x14)
                for i in range(lo, j):
                    if reg[i] in (0, 1):
                        tooltip = s(target[i])
                suffix = None
                hi = np.searchsorted(site, here + 0x90)
                for i in range(j + 1, hi):
                    if reg[i] == 9:
                        break
                    if reg[i] == 8:
                        text = s(target[i])
                        if text and text.startswith(' ') and len(text) < 20:
                            suffix = text.strip()
                            break
                edits.append({'display': name, 'tooltip': tooltip, 'suffix': suffix,
                              'member': member, 'site': here})
        return serialize, edits


def pair(serialize, edits, window=0x3000):
    """Attach to each serialize record the edit record for the same member.

    Field() blocks are followed by their class's DataElement block; inlined
    class-member Field()s come after it. So: first edit with the same member
    offset after a Field() site, else last one before an inlined site.
    """
    edits = sorted(edits, key=lambda e: e['site'])
    for rec in serialize:
        same = [e for e in edits if e['member'] == rec['member']
                and abs(e['site'] - rec['site']) <= window]
        after = [e for e in same if e['site'] > rec['site']]
        before = [e for e in same if e['site'] < rec['site']]
        chosen = None
        if rec['form'] == 'Field' and after:
            chosen = after[0]
        elif rec['form'] != 'Field' and before:
            chosen = before[-1]
        rec['edit'] = chosen
    return serialize


def runs(serialize, gap=0x200):
    """Group serialize records into class bodies by code adjacency."""
    ordered = sorted(serialize, key=lambda r: r['site'])
    group, out = [], []
    for rec in ordered:
        if group and rec['site'] - group[-1]['site'] > gap:
            out.append(group)
            group = []
        group.append(rec)
    if group:
        out.append(group)
    for index, g in enumerate(out):
        for rec in g:
            rec['run'] = index
    return out


# --------------------------------------------------------------------------
# asset tree

def value_offset(data, offset):
    flags = data[offset]
    pos = offset + 1 + (4 if flags & 64 else 0) + (1 if flags & 128 else 0) + 16
    if flags & 32:
        pos += flags & 7
    return pos


def decode(node):
    kind = SCALARS.get(node.type_id)
    if kind and node.value:
        try:
            return kind[0], kind[1](node.value)
        except (struct.error, IndexError):
            pass
    if node.value[:4] == STRING_PREFIX and len(node.value) >= 8:
        length = int.from_bytes(node.value[4:8], 'big')
        if length == len(node.value) - 8:
            return 'string', node.value[8:].decode('utf-8', 'replace')
    if not node.value:
        return None, None
    if len(node.value) == 4:
        return 'u32?', int.from_bytes(node.value, 'big')
    if all(32 <= b < 127 for b in node.value) and len(node.value) > 2:
        return 'text', node.value.decode('ascii')
    return 'raw', node.value.hex()


def label(names, crc):
    cands = names.get(crc)
    return cands[0] if cands else 'crc:%08x' % crc


def annotate(roots, data, names, by_name):
    rows = []

    def visit(node, path, siblings):
        name = label(names, node.name) if node.name else '(root)'
        here = path + (name,)
        kind, value = decode(node)
        row = {'field': '/'.join(here), 'name': name, 'crc': '%08x' % node.name,
               'offset': hex(node.offset), 'value_offset': hex(value_offset(data, node.offset)),
               'type_id': node.type_id, 'version': node.version,
               'size': len(node.value), 'raw': node.value.hex(),
               'kind': kind, 'value': value, 'children': len(node.children)}
        cands = by_name.get(name.lower(), [])
        if cands:
            # prefer the class body whose field set best covers the siblings
            def score(rec):
                body = rec['_body']
                # most siblings covered, then the tightest class body (so a
                # 3-field BackUpParams is not labelled from 12-field StrafeParams)
                return (len(siblings & body), -len(body - siblings),
                        rec['edit'] is not None)
            best = max(cands, key=score)
            row['reflect'] = {'member_offset': hex(best['member']),
                              'field_site': hex(best['site']), 'form': best['form'],
                              'class_overlap': '%d/%d' % (len(siblings & best['_body']),
                                                          len(siblings))}
            if best['edit']:
                e = best['edit']
                row['reflect'].update({'display': e['display'], 'tooltip': e['tooltip'],
                                       'suffix': e['suffix'], 'edit_site': hex(e['site'])})
        rows.append(row)
        child_names = {label(names, c.name).lower() for c in node.children}
        for child in node.children:
            visit(child, here, child_names)

    for root in roots:
        visit(root, (), set())
    return rows


def load(pak_name, pattern):
    path = Path(pak_name)
    if not path.exists():
        path = INSTALL / pak_name
    rx = re.compile(pattern)
    for entry in pak.load(path):
        name = entry['name'].decode('utf-8', 'replace')
        if rx.search(name):
            yield name, path.name, pak.entry_bytes(entry)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pak')
    parser.add_argument('pattern')
    parser.add_argument('--json', help='write the full annotated dump here')
    parser.add_argument('--summary', action='store_true',
                        help='print one line per scalar instead of JSON')
    args = parser.parse_args()

    assets = list(load(args.pak, args.pattern))
    if not assets:
        raise SystemExit('no asset matched')
    binary = Binary()
    names = binary.names()
    crcs = set()
    parsed = []
    for asset, pak_name, data in assets:
        roots = objectstream.parse(data)
        parsed.append((asset, pak_name, data, roots))
        crcs.update(n.name for n in objectstream.walk(roots))
    wanted = {c for crc in crcs for c in names.get(crc, [])}
    serialize, edits = binary.reflection(wanted)
    pair(serialize, edits)
    bodies = runs(serialize)
    body_names = [{r['name'].lower() for r in g} for g in bodies]
    by_name = {}
    for rec in serialize:
        rec['_body'] = body_names[rec['run']]
        by_name.setdefault(rec['name'].lower(), []).append(rec)

    report = []
    for asset, pak_name, data, roots in parsed:
        report.append({'asset': asset, 'pak': pak_name, 'bytes': len(data),
                       'sha256': hashlib.sha256(data).hexdigest(),
                       'elements': annotate(roots, data, names, by_name)})
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1))
        print('wrote %s' % args.json, file=sys.stderr)
    if args.summary or not args.json:
        for item in report:
            print('# %s sha256=%s bytes=%d' % (item['asset'], item['sha256'], item['bytes']))
            for row in item['elements']:
                r = row.get('reflect', {})
                print('%-7s %-8s %-70s %-28s %s | %s' % (
                    row['offset'], row['kind'] or '-', row['field'][:70],
                    repr(row['value'])[:28] if row['kind'] else '(%d children)' % row['children'],
                    r.get('display') or '', (r.get('tooltip') or '')[:100]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
