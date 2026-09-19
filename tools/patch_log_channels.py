#!/usr/bin/env python3
"""Force every NovaNet log channel ON (revival diagnostic tool, reversible).

Why
---
The shard handshake fails with no explanation anywhere in any log. The reason
is now known precisely. In `UdpBaseConnection::ProcessReceived`, immediately
after the Crc32 compare succeeds:

    0x4f20bbc  call 0x4f21570            ; -> UdpPacketIdWindow::UpdateForReceivedPacket
    0x4f20bc1  test al, al
    0x4f20bc3  je   0x4f20a51            ; return false - SILENTLY, no log

and inside that window check the two rejection paths are

    "[NET_Debug] Discarding old packet, sequence is too old to process"
    "[NET_Debug] Discarding packet due to duplicated sequence id"

Both are gated on the `NET_Debug` channel being enabled, and it is not. So a
packet that fails the sequence window is dropped without a trace - which is
exactly the symptom we have chased for three sessions.

There are 27 such channels (NET_CorePackets, NET_ClientConnection,
NET_DebugDtls, NET_Issues, ...). None can be turned on from a cvar: the
registry is thread-local and the enable test is a runtime lookup, not a
console variable. `net_debugVerboseLevel`, `log_Verbosity` and friends were
all tried and do nothing (see autoexec.cfg).

What it changes
---------------
Three bytes at the entry of the channel-enabled predicate:

    0x906300  mov [rsp+0x18], rbx ...    ; 48 89 5c  ->  b0 01 c3
                                         ;              mov al,1 ; ret

Every `call 0x906300 / test al,al / je skip` site then takes the logging
branch. Emission itself happens at the call sites (`0x4f2bf70`), not in here,
so nothing else is bypassed. The function it short-circuits, `0x4f2ced0`, is
a locked list lookup that answers "is this channel in the enabled set" - the
only thing lost by skipping it is the answer we are overriding.

This is loud on purpose. Expect a very large log.

Scope and safety
----------------
- Operates on YOUR OWN local copy, for interoperability with a local server
  standing in for a service shut down in 2020. Same basis as the pak patches
  and as tools/patch_crc_gate.py.
- **No anti-cheat component is modified.** This is a logging predicate.
  EasyAntiCheat is untouched and does not start in this build anyway.
- Backs up to `Crucible.exe.pre-logchan`; `--revert` restores it.

Usage:
  python3 tools/patch_log_channels.py            # apply
  python3 tools/patch_log_channels.py --revert   # undo
  python3 tools/patch_log_channels.py --check    # report current state
"""
import argparse
import os
import shutil
import sys

EXE = os.environ.get("CRUCIBLE_EXE", os.path.expanduser(
    "~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe"))
BACKUP = EXE + ".pre-logchan"

# Entry of the channel-enabled predicate (rva 0x906300).
OFFSET = 0x905700
ORIGINAL = bytes.fromhex("48895c")        # mov [rsp+0x18], rbx  (first 3 bytes)
PATCHED = bytes.fromhex("b001c3")         # mov al, 1 ; ret
# Guard: the full prologue, so a different build cannot be patched by accident.
GUARD = bytes.fromhex("48895c24185748 83ec50".replace(" ", ""))


def read(path, off, n):
    with open(path, "rb") as f:
        f.seek(off)
        return f.read(n)


def state():
    if not os.path.exists(EXE):
        return "missing"
    cur = read(EXE, OFFSET, 3)
    if cur == PATCHED:
        return "patched"
    if read(EXE, OFFSET, len(GUARD)) == GUARD:
        return "original"
    return "unrecognised"


def apply_patch():
    st = state()
    if st == "patched":
        print("already patched")
        return
    if st != "original":
        sys.exit("refusing: bytes at %#x are %s, not this build's prologue"
                 % (OFFSET, read(EXE, OFFSET, 8).hex()))
    if not os.path.exists(BACKUP):
        shutil.copy2(EXE, BACKUP)
        print("backed up -> %s" % BACKUP)
    with open(EXE, "r+b") as f:
        f.seek(OFFSET)
        f.write(PATCHED)
    print("patched %#x: %s -> %s (all NovaNet log channels now report enabled)"
          % (OFFSET, ORIGINAL.hex(), PATCHED.hex()))


def revert():
    if not os.path.exists(BACKUP):
        sys.exit("no backup at %s" % BACKUP)
    shutil.copy2(BACKUP, EXE)
    print("restored from %s" % BACKUP)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
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
