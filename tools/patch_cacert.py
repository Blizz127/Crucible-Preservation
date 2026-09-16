#!/usr/bin/env python3
"""Trust the local test CAs in the ENGINE's own TLS stack (match server).

STATUS: superseded, probably unnecessary - do not run without reading this.
The real blocker was certificate PINNING (the engine's
ValidatePinnedCertificate() compared our cert against a pinned one and gave
up), fixed by `net_SslEnablePinning 0` in the game-root user.cfg. See
docs/10-match-server-tls.md. The self-signed failure this tool was written to
address was already being cleared by `net_SslAllowSelfSigned`. Whether the
bundle change is needed at all is UNVERIFIED; test by restoring
gamedata.pak.pre-cacert and reconnecting.

Why this exists
---------------
`matchserver.connect` does its own TLS inside the engine, and that stack trusts
`certs/cacert.pem` from gamedata.pak (155 public roots) rather than the
Windows/Wine root store. This appends the local test certs from
server/.crucible-test-ca/ to that bundled list. Same shape as the other
patch_*.py tools: idempotent, matched by DER equality, written to a file for
tools/rebuild_pak.py to install.

Scope: local only. The bundle is additive here - we never remove a public root,
and nothing outside 127.0.0.1 is affected. Revert by restoring the pak backup
rebuild_pak.py writes (<pak>.pre-<tag>).

Usage:
  python3 patch_cacert.py [--pak PATH] [--out /tmp/cacert.patched.pem]
"""
import argparse
import glob
import os
import re
import ssl
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CADIR = os.path.join(HERE, "..", "server", ".crucible-test-ca")
ENTRY = "certs/cacert.pem"

PAK = os.environ.get(
    "CRUCIBLE_PAK",
    os.path.expanduser("~/.local/share/Steam/steamapps/common/"
                       "Crucible/gamedata.pak"))

CERT_RE = re.compile(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)


def read_entry(pak_path, name):
    """Decompressed bytes of one pak entry (reuses the reader from pak.py)."""
    sys.path.insert(0, HERE)
    from pak import load, entry_bytes
    for e in load(pak_path):
        if e["name"].decode("utf-8", "replace") == name:
            return entry_bytes(e)
    raise KeyError("%s not found in %s" % (name, pak_path))


def fingerprints(pem_bytes):
    """Set of DER blobs for every certificate present in a PEM bundle."""
    out = set()
    for m in CERT_RE.finditer(pem_bytes):
        try:
            out.add(ssl.PEM_cert_to_DER_cert(m.group(0).decode("ascii")))
        except Exception:
            continue
    return out


def patch(data):
    """Append any missing local test cert to the bundle. Idempotent."""
    present = fingerprints(data)
    certs = sorted(glob.glob(os.path.join(CADIR, "*.crt")))
    if not certs:
        raise SystemExit("no test certs in %s - run server/tls_stub.py or "
                         "server/match_stub.py once to generate them" % CADIR)

    additions = []
    for path in certs:
        text = open(path, encoding="utf-8").read().strip()
        der = ssl.PEM_cert_to_DER_cert(text)
        if der in present:
            print("%-10s already in bundle, skipping" % os.path.basename(path))
            continue
        additions.append((os.path.basename(path), text))
        present.add(der)

    if not additions:
        return data, 0

    body = data if data.endswith(b"\n") else data + b"\n"
    for name, text in additions:
        body += (text + "\n").encode("ascii")
        print("%-10s appended" % name)
    return body, len(additions)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pak", default=PAK)
    ap.add_argument("--out", default="/tmp/cacert.patched.pem")
    args = ap.parse_args()

    data = read_entry(args.pak, ENTRY)
    patched, n = patch(data)
    open(args.out, "wb").write(patched)
    print("certs/cacert.pem: %d -> %d bytes, %d certs in, %d added: %s"
          % (len(data), len(patched), len(fingerprints(patched)), n, args.out))
    if n:
        print("install with:\n  python3 tools/rebuild_pak.py --pak %r "
              "--tag cacert --set %s=%s" % (args.pak, ENTRY, args.out))


if __name__ == "__main__":
    main()
