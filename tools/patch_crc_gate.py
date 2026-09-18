#!/usr/bin/env python3
"""Force the UDP receive CRC check to pass (revival tool, reversible).

Why
---
The shard link (docs/12) runs every datagram through OpenSSL before anything
reads it: `NovaNet::DtlsEndpoint::DecodePacket` writes into an SSL BIO, and
`UdpBaseConnection::ProcessReceived` then checksums the UNWRAPPED buffer, not
the bytes on the wire. That is why no checksum we compute is ever accepted -
we are matching the output of a transform we do not apply - and why none of
`net_UdpUseEncryption`, `net_EnableDtlsPreSharedKeys`, `net_UdpCompressor` or
`net_UseDtlsCookies` changes anything.

This patch removes the checksum as a variable so the rest of the handshake
can be worked on. It is a diagnostic, not a fix: if the client then accepts
our Accept, the checksum was the only blocker and the real work is reversing
the unwrap. If it still refuses, the problem is elsewhere and the checksum
was a red herring.

What it changes
---------------
One byte. In `ProcessReceived`:

    0x4f20b5a  cmp ebx, dword [rsp+0xb8]   ; computed vs received Crc32
    0x4f20b61  je  0x4f20baf               ; equal -> continue
                 ^ 74 4c   becomes   eb 4c  (jmp, always continue)

so the mismatch path - "Crc32 mismatch, discarding packet" - becomes
unreachable. Nothing else is touched.

Scope and safety
----------------
- Operates on YOUR OWN local copy, for interoperability with a local server
  standing in for a service shut down in 2020. Same basis as the pak patches
  in tools/patch_menu.py.
- **No anti-cheat component is modified.** This byte is in NovaNet's UDP
  transport. EasyAntiCheat is untouched and, in this build, does not start
  anyway ("EAC Client failed to start" appears in every boot log).
- Writes `Crucible.exe.pre-crcgate` first and `--revert` restores it, so the
  change is undoable without re-downloading 21 GB.

Usage:
  python3 tools/patch_crc_gate.py            # apply
  python3 tools/patch_crc_gate.py --revert   # undo
  python3 tools/patch_crc_gate.py --check    # report current state
"""
import argparse
import os
import shutil
import sys

EXE = os.environ.get("CRUCIBLE_EXE", os.path.expanduser(
    "~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe"))
BACKUP = EXE + ".pre-crcgate"

# File offset of the `je` after the Crc32 comparison, and the bytes either way.
OFFSET = 0x4F1FF61
ORIGINAL = bytes.fromhex("744c")   # je  +0x4c
PATCHED = bytes.fromhex("eb4c")    # jmp +0x4c
# A few bytes before it, as a guard against patching the wrong build.
CONTEXT_OFFSET = 0x4F1FF5A
CONTEXT = bytes.fromhex("3b9c24b8000000")   # cmp ebx, [rsp+0xb8]


def read(path, off, n):
    with open(path, "rb") as f:
        f.seek(off)
        return f.read(n)


def state():
    if not os.path.exists(EXE):
        return "missing"
    if read(EXE, CONTEXT_OFFSET, len(CONTEXT)) != CONTEXT:
        return "unrecognised"
    cur = read(EXE, OFFSET, 2)
    if cur == ORIGINAL:
        return "original"
    if cur == PATCHED:
        return "patched"
    return "unknown"


def apply_patch():
    st = state()
    if st == "unrecognised":
        sys.exit("refusing: bytes around %#x are not what this build should "
                 "have - wrong version or already modified" % OFFSET)
    if st == "patched":
        print("already patched")
        return
    if st != "original":
        sys.exit("refusing: unexpected bytes %s at %#x"
                 % (read(EXE, OFFSET, 2).hex(), OFFSET))
    if not os.path.exists(BACKUP):
        shutil.copy2(EXE, BACKUP)
        print("backed up -> %s" % BACKUP)
    with open(EXE, "r+b") as f:
        f.seek(OFFSET)
        f.write(PATCHED)
    print("patched %#x: %s -> %s (CRC mismatch path now unreachable)"
          % (OFFSET, ORIGINAL.hex(), PATCHED.hex()))


def revert():
    if not os.path.exists(BACKUP):
        sys.exit("no backup at %s" % BACKUP)
    shutil.copy2(BACKUP, EXE)
    print("restored from %s" % BACKUP)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        print("state: %s (%s)" % (state(), EXE))
    elif a.revert:
        revert()
    else:
        apply_patch()


if __name__ == "__main__":
    main()
