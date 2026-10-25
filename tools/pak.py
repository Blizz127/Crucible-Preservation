#!/usr/bin/env python3
"""List / extract entries from a Crucible .pak (read-only companion to rebuild_pak.py).

These paks are ZIPs with a CryEngine quirk: central-directory names use '/',
local headers use '\\'. unzip warns, Python's zipfile raises BadZipFile on
read(), 7z copes. This reads them directly and decodes both methods the game
uses (8 = raw deflate, 0 = stored).

Usage:
  python3 pak.py list PAK [--grep REGEX] [--long]
  python3 pak.py cat  PAK NAME [--out FILE]
  python3 pak.py grep PAK REGEX [--ext .js,.json] [--max N]

Examples:
  python3 pak.py list gamedata.pak --grep '^ui/dist/.*\\.js$'
  python3 pak.py grep misc.pak practicearena
"""
import argparse
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rebuild_pak import parse  # noqa: E402


def entry_bytes(e):
    """Decompressed contents of a parsed entry."""
    blob = e["blob"]
    head = 30 + len(e["lname"]) + len(e["lextra"])
    raw = blob[head:]
    if e["method"] == 8:
        return zlib.decompress(raw, -15)
    if e["method"] == 0:
        return raw
    raise ValueError("unsupported method %d for %s" % (e["method"], e["name"]))


def load(pak_path):
    _, _, entries = parse(pak_path)
    return entries


def index(pak_path):
    """{name: (local_header_offset, compressed_size, method)} from the central
    directory alone, without reading entry data. load() reads the whole pak
    into memory; this seeks, for paks of gigabytes (objects.pak is 2.5 GB)."""
    import struct
    with open(pak_path, 'rb') as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - 65558))
        tail = handle.read()
        eocd = tail.rfind(b'PK\x05\x06')
        if eocd < 0:
            raise ValueError('no end of central directory in %s' % pak_path)
        _, _, _, total, cd_size, cd_offset, _ = struct.unpack('<HHHHIIH', tail[eocd + 4:eocd + 22])
        handle.seek(cd_offset)
        directory = handle.read(cd_size)
    result, offset = {}, 0
    for _ in range(total):
        if directory[offset:offset + 4] != b'PK\x01\x02':
            raise ValueError('bad central directory record at %d' % offset)
        (method, compressed, name_length, extra_length, comment_length,
         local) = (struct.unpack('<H', directory[offset + 10:offset + 12])[0],
                   struct.unpack('<I', directory[offset + 20:offset + 24])[0],
                   *struct.unpack('<HHH', directory[offset + 28:offset + 34]),
                   struct.unpack('<I', directory[offset + 42:offset + 46])[0])
        name = directory[offset + 46:offset + 46 + name_length]
        result[name] = (local, compressed, method)
        offset += 46 + name_length + extra_length + comment_length
    return result


def read(pak_path, names, entries=None):
    """{name: bytes} for the named entries only, by seeking (see index())."""
    import struct
    entries = index(pak_path) if entries is None else entries
    result = {}
    with open(pak_path, 'rb') as handle:
        for name in names:
            local, compressed, method = entries[name]
            handle.seek(local)
            header = handle.read(30)
            if header[:4] != b'PK\x03\x04':
                raise ValueError('bad local header for %r' % name)
            name_length, extra_length = struct.unpack('<HH', header[26:30])
            handle.seek(local + 30 + name_length + extra_length)
            raw = handle.read(compressed)
            if method == 8:
                result[name] = zlib.decompress(raw, -15)
            elif method == 0:
                result[name] = raw
            else:
                raise ValueError('unsupported method %d for %r' % (method, name))
    return result


def cmd_list(args):
    entries = load(args.pak)
    rx = re.compile(args.grep) if args.grep else None
    shown = 0
    for e in entries:
        name = e["name"].decode("utf-8", "replace")
        if rx and not rx.search(name):
            continue
        if args.long:
            print("%10d %10d %s" % (e["us"], e["cs"], name))
        else:
            print(name)
        shown += 1
    print("-- %d/%d entries" % (shown, len(entries)), file=sys.stderr)


def cmd_cat(args):
    entries = load(args.pak)
    want = args.name if args.name.startswith("/") else args.name
    want = want.lstrip("/")
    for e in entries:
        name = e["name"].decode("utf-8", "replace")
        if name == want:
            data = entry_bytes(e)
            if args.out:
                open(args.out, "wb").write(data)
                print("wrote %s (%d bytes)" % (args.out, len(data)),
                      file=sys.stderr)
            else:
                sys.stdout.buffer.write(data)
            return 0
    print("not found: %s" % want, file=sys.stderr)
    return 1


def cmd_grep(args):
    entries = load(args.pak)
    rx = re.compile(args.regex)
    exts = tuple(x for x in (args.ext or "").split(",") if x)
    hits = 0
    for e in entries:
        name = e["name"].decode("utf-8", "replace")
        if exts and not name.endswith(exts):
            continue
        try:
            data = entry_bytes(e)
        except Exception:
            continue
        # search as text, both ASCII and UTF-16LE (engine configs use both)
        for enc in ("utf-8", "utf-16-le"):
            try:
                text = data.decode(enc, "ignore")
            except Exception:
                continue
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                s = max(0, m.start() - 60)
                ctx = text[s:m.end() + 60].replace("\n", "\\n")
                print("%s:%d:%s[%s] %s" % (name, line, enc, m.group(0), ctx))
                hits += 1
                if hits >= args.max:
                    print("-- hit limit reached", file=sys.stderr)
                    return 0
    print("-- %d hits" % hits, file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("pak")
    p.add_argument("--grep")
    p.add_argument("--long", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("cat")
    p.add_argument("pak")
    p.add_argument("name")
    p.add_argument("--out")
    p.set_defaults(func=cmd_cat)

    p = sub.add_parser("grep")
    p.add_argument("pak")
    p.add_argument("regex")
    p.add_argument("--ext", default="")
    p.add_argument("--max", type=int, default=40)
    p.set_defaults(func=cmd_grep)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
