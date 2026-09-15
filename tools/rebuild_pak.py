#!/usr/bin/env python3
"""Replace entries inside a .pak without disturbing anything else.

Why not 7z/zipfile rewrite: this game's pak uses backslash names in local
headers vs forward slashes in the central directory; generic rewriters
normalize them. This tool raw-copies every unpatched entry (local header +
data + central-dir record byte-for-byte) and only rebuilds the records of
patched entries, preserving original names/extra/attrs/timestamps.

Patched entries are recompressed with raw deflate (method 8, level 6) with
fresh CRC/sizes; all other header fields are copied from the original.

Usage:
  python3 rebuild_pak.py --pak PATH --set cd/name.js:/tmp/new.js [--set ...]
  python3 rebuild_pak.py --verify-only --pak PATH   # parse + CRC-check all

A backup <pak>.pre-<tag> is written once (never overwritten).
"""
import argparse
import os
import shutil
import struct
import sys
import zlib

CD_FMT = "<HHHHHHIIIHHHHHII"
CD_SIG = b"PK\x01\x02"
LH_SIG = b"PK\x03\x04"
EOCD_SIG = b"PK\x05\x06"


def parse(pak_path):
    d = open(pak_path, "rb").read()
    eocd = d.rfind(EOCD_SIG)
    assert eocd >= 0, "no EOCD"
    (disk, cddisk, nthis, ntotal, cdsize, cdoff,
     cmtlen) = struct.unpack("<HHHHIIH", d[eocd + 4:eocd + 22])
    assert nthis == ntotal
    comment = d[eocd + 22:eocd + 22 + cmtlen]
    entries = []
    o = cdoff
    for _ in range(ntotal):
        assert d[o:o + 4] == CD_SIG, o
        f = struct.unpack(CD_FMT, d[o + 4:o + 46])
        (made, need, flag, method, mtime, mdate, crc, cs, us, fnlen,
         xtralen, cmtlen2, disk2, intattr, extattr,
         lho) = f
        name = d[o + 46:o + 46 + fnlen]
        extra = d[o + 46 + fnlen:o + 46 + fnlen + xtralen]
        cmt = d[o + 46 + fnlen + xtralen:o + 46 + fnlen + xtralen + cmtlen2]
        rec = d[o:o + 46 + fnlen + xtralen + cmtlen2]
        # local header
        assert d[lho:lho + 4] == LH_SIG, (name, lho)
        (lver, lflag, lmethod, lmt, lmd, lcrc, lcs, lus, lfnlen,
         lextralen) = struct.unpack("<HHHHHIIIHH", d[lho + 4:lho + 30])
        lname = d[lho + 30:lho + 30 + lfnlen]
        lextra = d[lho + 30 + lfnlen:lho + 30 + lfnlen + lextralen]
        data_start = lho + 30 + lfnlen + lextralen
        blob = d[lho:data_start + cs]
        entries.append(dict(
            name=name, made=made, need=need, flag=flag, method=method,
            mtime=mtime, mdate=mdate, crc=crc, cs=cs, us=us, extra=extra,
            cmt=cmt, disk=disk2, intattr=intattr, extattr=extattr, lho=lho,
            rec=rec, lver=lver, lflag=lflag, lmethod=lmethod, lmt=lmt,
            lmd=lmd, lname=lname, lextra=lextra, blob=blob))
        o += 46 + fnlen + xtralen + cmtlen2
    return d, dict(ntotal=ntotal, comment=comment,
                   eocd_extra=d[eocd + 22 + cmtlen:]), entries


def verify_all(entries):
    for e in entries:
        blob = e["blob"]
        head = 30 + len(e["lname"]) + len(e["lextra"])
        raw = blob[head:]
        if e["method"] == 8:
            data = zlib.decompress(raw, -15)
        elif e["method"] == 0:
            data = raw
        else:
            continue
        assert len(data) == e["us"], e["name"]
        assert (zlib.crc32(data) & 0xffffffff) == e["crc"], e["name"]
    print("verified %d entries (crc+size)" % len(entries))


def rebuild(pak_path, replacements, tag):
    raw, tail, entries = parse(pak_path)
    out = bytearray()
    new_offsets = []
    cd_recs = []
    for e in entries:
        cname = e["name"].decode()
        if cname in replacements:
            new_data = open(replacements[cname], "rb").read()
            co = zlib.compressobj(6, zlib.DEFLATED, -15)
            comp = co.compress(new_data) + co.flush()
            newcrc = zlib.crc32(new_data) & 0xffffffff
            lh = (struct.pack("<IHHHHHIIIHH", 0x04034B50, e["lver"],
                              e["lflag"], 8, e["lmt"], e["lmd"], newcrc,
                              len(comp), len(new_data), len(e["lname"]),
                              len(e["lextra"]))
                  + e["lname"] + e["lextra"] + comp)
            new_offsets.append(len(out))
            out += lh
            cd = (struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, e["made"],
                              e["need"], e["flag"], 8, e["mtime"],
                              e["mdate"], newcrc, len(comp), len(new_data),
                              len(e["name"]), len(e["extra"]),
                              len(e["cmt"]), e["disk"], e["intattr"],
                              e["extattr"], new_offsets[-1])
                  + e["name"] + e["extra"] + e["cmt"])
            cd_recs.append(cd)
            print("patched %s: %d -> %d bytes" % (cname, e["us"],
                                                  len(new_data)))
        else:
            new_offsets.append(len(out))
            out += e["blob"]
            cd = (struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, e["made"],
                              e["need"], e["flag"], e["method"], e["mtime"],
                              e["mdate"], e["crc"], e["cs"], e["us"],
                              len(e["name"]), len(e["extra"]),
                              len(e["cmt"]), e["disk"], e["intattr"],
                              e["extattr"], new_offsets[-1])
                  + e["name"] + e["extra"] + e["cmt"])
            cd_recs.append(cd)
    cdoff = len(out)
    for cd in cd_recs:
        out += cd
    cdsize = len(out) - cdoff
    out += (struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, tail["ntotal"],
                        tail["ntotal"], cdsize, cdoff,
                        len(tail["comment"]))
            + tail["comment"] + tail["eocd_extra"])
    backup = pak_path + ".pre-" + tag
    if not os.path.exists(backup):
        shutil.copy2(pak_path, backup)
        print("backup: %s" % backup)
    else:
        print("backup exists: %s" % backup)
    tmp = pak_path + ".new"
    open(tmp, "wb").write(out)
    os.replace(tmp, pak_path)
    print("wrote %s (%d bytes)" % (pak_path, len(out)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pak", required=True)
    ap.add_argument("--set", action="append", default=[],
                    help="cd/path:name=/local/file (repeatable)")
    ap.add_argument("--tag", default="patch")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    if args.verify_only:
        _, _, entries = parse(args.pak)
        verify_all(entries)
        return
    replacements = {}
    for spec in args.set:
        name, _, local = spec.partition("=")
        if not name or not local:
            sys.exit("bad --set spec: %r" % spec)
        replacements[name] = local
    rebuild(args.pak, replacements, args.tag)


if __name__ == "__main__":
    main()

