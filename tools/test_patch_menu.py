#!/usr/bin/env python3
"""Committed check: M1-M4 menu patches apply exactly once to the live pak.

Run: python3 tools/test_patch_menu.py [--pak PATH]
Fails loudly if a patch site is missing (retail build drift) or a patch
was applied twice.
"""
import argparse
import sys

sys.path.insert(0, "tools")
from patch_menu import ENTRY, PAK, patch, read_entry

MARKERS = {
    # M1: disconnect modal gated on LAN store
    "M1": 't&&!l&&e("DISCONNECTED")',
    # M2: loadout-set resolved locally
    "M2": "src:()=>Promise.resolve()",
    # M3: explicit LAN endpoint for matchserver.connect
    "M3": 'ipAddress:"127.0.0.1",port:18877',
    # M4: analytics-data invoke races native bridge vs local fallback
    "M4": 'id:"analytics-data",src:()=>Promise.race(',
}

UNPATCHED_SITES = {
    # M4's original invoke must be gone after patching
    "M4-orig": ('src:()=>Promise.all([s.promise("data.get",'
                 '{key:"session"}),s.promise("buildinfo.get")])'),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pak", default=PAK)
    args = ap.parse_args()
    data = read_entry(args.pak, ENTRY)
    # Keep bytes for the size report: menu.js contains a literal U+FFFD, so
    # len() of the decoded str would understate the on-disk size by 2.
    patched_bytes = patch(data)
    patched = patched_bytes.decode("utf-8")
    failures = []
    for name, marker in MARKERS.items():
        n = patched.count(marker)
        if n != 1:
            failures.append("%s: marker found %d times, want 1" % (name, n))
    for name, site in UNPATCHED_SITES.items():
        if site in patched:
            failures.append("%s: original site still present" % name)
    if failures:
        print("FAIL")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: M1-M4 applied once each (%d -> %d bytes)"
          % (len(data), len(patched_bytes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
