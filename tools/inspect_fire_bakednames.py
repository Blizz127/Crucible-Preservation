#!/usr/bin/env python3
"""Dump a Crucible baked-name table (`assets/bakednames/*.names`) as id -> name.

`Crucible::AttachmentPointName` is serialised on the wire as a bare `uint16_t`
(retail `0x42e8850`, `Crucible::AttachmentPointName::Serialize`) and validated
against this table by `HybridProjectileSystemClient::ValidateNetData`
(`0x140bc3680`, the `0x42e8800` call). The table itself is shipped content, not
code, so the id/name mapping has to be read out of the pak.

The files are binary ObjectStream v3: a fixed 21-byte class-id prefix repeats
before every element, then a single length byte and an ASCII name. The engine
prepends static names from the executable before appending this file. Wire IDs
are recovered for attachmentpoint, abilitynames, and healthchangesource;
other tables are explicitly reported as file indices only.

Read-only. Nothing under the Steam install is written and the game is never
launched.

Usage:
  python3 tools/inspect_fire_bakednames.py attachmentpoint
  python3 tools/inspect_fire_bakednames.py attachmentpoint --grep muzzle
  python3 tools/inspect_fire_bakednames.py --list
  python3 tools/inspect_fire_bakednames.py --request \
      assets/player/marine/marine_projectile_request.projectilereq
"""
import argparse
import hashlib
import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pak import entry_bytes  # noqa: E402
from rebuild_pak import parse  # noqa: E402
from objectstream import parse as parse_objectstream  # noqa: E402

DEFAULT_PAK = os.path.expanduser(
    "~/.local/share/Steam/steamapps/common/Crucible/misc.pak")
DEFAULT_EXE = os.environ.get('CRUCIBLE_EXE', os.path.expanduser(
    '~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe'))
PREFIX = "assets/bakednames/"
# Ensure*HandlerExists installs these vtables. Slots 0x20 and 0x28 return
# the static-name pointer array and its length; InitStaticNames 0x33ffaf0
# inserts them first, then InitNames 0x33fffa4 appends the pak entries.
STATIC_HANDLERS = {'attachmentpoint': 0x6c1f1a0,
                   'abilitynames': 0x6c1edb8,
                   'healthchangesource': 0x6c1e300}

# The per-element ObjectStream class-id run. Identical for every one of these
# tables; taken verbatim from attachmentpoint.names rather than reconstructed.
ELEMENT_KEY = bytes.fromhex("7941405e3903aaab3f5c475a669ebcd5fa4db353c9")


def tables(pak_path):
    """{short name: raw bytes} for every baked-name table in the pak."""
    found = {}
    _, _, entries = parse(pak_path)
    for entry in entries:
        name = entry["name"].decode("utf-8", "replace").replace("\\", "/")
        if name.startswith(PREFIX) and name.endswith(".names"):
            found[name[len(PREFIX):-len(".names")]] = entry
    return found


def names(raw):
    """Ordered asset names; their index excludes the executable's prefix."""
    chunks = raw.split(ELEMENT_KEY)
    if len(chunks) < 2:
        raise ValueError("not a baked-name table, or an unsupported layout")
    result = []
    for chunk in chunks[1:]:
        if not chunk:
            raise ValueError("truncated baked-name element")
        length = chunk[0]
        text = chunk[1:1 + length]
        if len(text) != length or b"\0" in text:
            raise ValueError("truncated baked-name element")
        result.append(text.decode("ascii", "replace"))
    return result


def static_names(table, exe=DEFAULT_EXE):
    """Decode a known handler's two tiny getters; reject executable drift."""
    import pefile
    with pefile.PE(exe, fast_load=True) as pe:
        base = pe.OPTIONAL_HEADER.ImageBase
        getter, counter = struct.unpack('<QQ', pe.get_data(STATIC_HANDLERS[table] + 0x20, 16))
        getter -= base
        count_code = pe.get_data(counter - base, 6)
        pointer_code = pe.get_data(getter, 8)
        if (count_code[:1] != b'\xb8' or count_code[-1:] != b'\xc3'
                or pointer_code[:3] != b'\x48\x8d\x05'
                or pointer_code[-1:] != b'\xc3'):
            raise ValueError('unsupported baked-name static getter; executable changed')
        count = int.from_bytes(count_code[1:5], 'little')
        if not 0 < count < 256:
            raise ValueError('invalid static-name count')
        array = getter + 7 + struct.unpack('<i', pointer_code[3:7])[0]
        pointers = struct.unpack(f'<{count}Q', pe.get_data(array, 8 * count))
        result = []
        for pointer in pointers:
            raw = pe.get_data(pointer - base, 128).split(b'\0', 1)[0]
            if not raw or any(c < 32 or c > 126 for c in raw):
                raise ValueError('invalid static baked name')
            result.append(raw.decode('ascii'))
        return result


def combine_names(prefix, asset_names):
    """The retail append order, rejecting duplicates that would shift IDs."""
    result = list(prefix) + list(asset_names)
    if len({name.lower() for name in result}) != len(result):
        raise ValueError('duplicate baked names require explicit engine resolution')
    return result


def asset_hint(raw):
    """Asset-reference path: 48-byte identity/type header, BE u64 length."""
    if len(raw) < 56 or len(raw) != 56 + int.from_bytes(raw[48:56], 'big'):
        raise ValueError('invalid projectile asset-reference path length')
    return raw[56:].decode('utf-8')


def request_assets(raw):
    """The authored 16-slot AllowedAssets array, preserving empty slots."""
    roots = parse_objectstream(raw)
    allowed = roots[0].child('AllowedAssets') if len(roots) == 1 else None
    if allowed is None or len(allowed.children) != 16:
        raise ValueError('expected one projectile request with 16 AllowedAssets')
    return [{'index': index, 'offset': child.offset,
             'asset': asset_hint(child.value)}
            for index, child in enumerate(allowed.children)]


def projectile_attachment(raw):
    """Read authored Attachment; this does not infer the network attachment."""
    roots = parse_objectstream(raw)
    node = roots[0].child('Attachment') if len(roots) == 1 else None
    if node is None:
        raise ValueError('projectile has no Attachment field')
    value = node.value
    if (len(value) < 8 or value[:4] != b'\x01\xff\xff\xff'
            or len(value) != 8 + int.from_bytes(value[4:8], 'big')):
        raise ValueError('invalid projectile Attachment string')
    return value[8:].decode('utf-8')


def request_report(pak_path, asset, exe=DEFAULT_EXE):
    """Read request slots and referenced projectile fields, without extraction."""
    _, _, entries = parse(pak_path)
    by_name = {e['name'].decode('utf-8').replace('\\', '/').lower(): e
               for e in entries}
    asset = asset.replace('\\', '/').lower()
    if asset not in by_name:
        raise ValueError(f'projectile request not in pak: {asset}')
    raw = entry_bytes(by_name[asset])
    attachment_names = names(entry_bytes(by_name[PREFIX + 'attachmentpoint.names']))
    wire_names = combine_names(static_names('attachmentpoint', exe), attachment_names)
    slots = request_assets(raw)
    for slot in slots:
        if not slot['asset']:
            continue
        key = slot['asset'].replace('\\', '/').lower()
        if key not in by_name:
            slot['status'] = 'PROJECTILE_ASSET_MISSING'
            continue
        projectile = entry_bytes(by_name[key])
        attachment = projectile_attachment(projectile)
        slot.update(projectile_sha256=hashlib.sha256(projectile).hexdigest(),
                    attachment=attachment,
                    attachment_table_index=(attachment_names.index(attachment)
                                            if attachment in attachment_names else None),
                    attachment_wire_id=(wire_names.index(attachment)
                                        if attachment in wire_names else None))
    return {'asset': asset, 'sha256': hashlib.sha256(raw).hexdigest(),
            'slots': slots,
            'network_attachment_status':
                'Nondeflected shots use projectile Attachment; payload attachment 0 is valid None'}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("table", nargs="?", help="e.g. attachmentpoint")
    parser.add_argument("--pak", default=DEFAULT_PAK)
    parser.add_argument("--exe", default=DEFAULT_EXE,
                        help="retail executable for the static baked-name prefix")
    parser.add_argument("--list", action="store_true",
                        help="list the available tables and exit")
    parser.add_argument("--grep", help="case-insensitive regex over the names")
    parser.add_argument("--request", help="inspect a .projectilereq path inside --pak")
    args = parser.parse_args()

    if args.request:
        try:
            print(json.dumps(request_report(args.pak, args.request, args.exe), indent=2))
        except (OSError, ValueError, KeyError) as error:
            parser.exit(1, f'cannot inspect {args.request}: {error}\n')
        return

    try:
        available = tables(args.pak)
    except OSError as error:
        parser.exit(1, f"cannot read {args.pak}: {error}\n")

    if args.list or not args.table:
        for name in sorted(available):
            print(name)
        return
    if args.table not in available:
        parser.exit(1, f"no such baked-name table: {args.table}\n")

    try:
        values = names(entry_bytes(available[args.table]))
        if args.table in STATIC_HANDLERS:
            values = combine_names(static_names(args.table, args.exe), values)
        else:
            print('# File indices only: executable static prefix has not been decoded')
    except (OSError, ValueError) as error:
        parser.exit(1, f"{args.table}: {error}\n")

    pattern = re.compile(args.grep, re.I) if args.grep else None
    for index, value in enumerate(values):
        if pattern and not pattern.search(value):
            continue
        print(f"{index:5d}  {value}")


if __name__ == "__main__":
    main()
