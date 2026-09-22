"""Opt-in, single-loopback-client hub-to-shard identity handoff.

This is local server bookkeeping, not a replacement for retail authentication.
Each TLS connection owns a lease; ambiguous peers never select an identity.
The configured directory should live under XDG_RUNTIME_DIR, outside the repo.
"""
import ipaddress
import fcntl
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time


@contextmanager
def locked(root):
    with (root / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def publish(directory, key, peer, user_id, *, now=None):
    if not ipaddress.ip_address(peer).is_loopback or not 1 <= user_id <= 0xFFFFFFFF:
        raise ValueError('local session requires loopback peer and nonzero uint32 user')
    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {'peer': peer, 'user_id': user_id, 'updated': time.time() if now is None else now}
    fd, temporary = tempfile.mkstemp(prefix='lease-', suffix='.tmp', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(record, stream)
        with locked(root):
            if not (root / (key + '.claimed')).exists():
                os.replace(temporary, root / (key + '.json'))
    finally:
        Path(temporary).unlink(missing_ok=True)


def remove(directory, key):
    root = Path(directory)
    if root.exists():
        with locked(root):
            (root / (key + '.json')).unlink(missing_ok=True)


def selection(directory, peer, *, now=None):
    if not peer or not ipaddress.ip_address(peer).is_loopback:
        return None
    current = time.time() if now is None else now
    candidates = []
    try:
        for path in Path(directory).glob('*.json'):
            record = json.loads(path.read_text())
            age = current - record['updated']
            if record['peer'] == peer and 0 <= age <= 60:
                user = record['user_id']
                if type(user) is not int or not 1 <= user <= 0xFFFFFFFF:
                    return None
                candidates.append((path, user))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return candidates[0] if len(candidates) == 1 else None


def lookup(directory, peer, *, now=None):
    result = selection(directory, peer, now=now)
    return result[1] if result else None


def claim(directory, peer, *, now=None):
    root = Path(directory)
    if not root.exists():
        return None
    with locked(root):
        result = selection(directory, peer, now=now)
        if result is None:
            return None
        path, user = result
        # Tombstone also stops a racing hub heartbeat from republishing it.
        os.replace(path, path.with_suffix('.claimed'))
        return user
