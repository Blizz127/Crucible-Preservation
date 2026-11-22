#!/usr/bin/env python3
"""Archive a quiesced Crucible progression directory on its service host.

Run as the service user after stopping the supplied writer units and draining
pending writes. Stop any unmanaged writers as well. The checks cover only the
user service units supplied by the operator. The archive preserves
raw store bytes for tools/stage_player_checkpoint.py, including quarantined data.
The separate SHA-256 receipt is printed on stdout. Save it with the backup.
"""
import argparse
from contextlib import ExitStack
import fcntl
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile

from stage_player_checkpoint import digest


PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'Job',
              'InactiveExitTimestampMonotonic', 'InactiveEnterTimestampMonotonic')


def inactive_writers(writer_units):
    snapshots = {}
    for unit in writer_units:
        proc = subprocess.run(['systemctl', '--user', 'show', '--all',
                               '--property=' + ','.join(PROPERTIES), '--', unit],
                              capture_output=True, text=True, timeout=5)
        if proc.returncode:
            raise ValueError(f'cannot verify {unit} with the user service manager')
        props = dict(row.split('=', 1) for row in proc.stdout.splitlines() if '=' in row)
        if set(props) != set(PROPERTIES) or props['Id'] != unit:
            raise ValueError(f'incomplete service state for {unit}')
        if (props['LoadState'] != 'loaded' or props['ActiveState'] != 'inactive'
                or props['SubState'] != 'dead' or props['MainPID'] != '0'
                or props['ControlPID'] != '0' or props['Job'] not in ('', '0')):
            raise ValueError(f'{unit} must be loaded and fully stopped with no job pending')
        snapshots[unit] = props
    return snapshots


def signature(st):
    # Access timestamps may update when a file is read.
    return st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def inventory(rootfd):
    entries = {}
    for name in sorted(os.listdir(rootfd)):
        st = os.stat(name, dir_fd=rootfd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f'unexpected non-regular store entry: {name}')
        entries[name] = signature(st)
    return entries


def backup(source, archive, writer_units):
    writer_units = tuple(writer_units)
    if not writer_units:
        raise ValueError('supply at least one writer service unit')
    source, archive = Path(source), Path(archive)
    if '..' in source.parts or '..' in archive.parts:
        raise ValueError('use paths without parent traversal')
    # Resolve the containing directory only, so a final symlink cannot redirect a read or write.
    source = source.parent.resolve(strict=True) / source.name
    archive = archive.parent.resolve(strict=True) / archive.name
    if archive == source or source in archive.parents:
        raise ValueError('put the archive outside the progression store')
    if os.path.lexists(archive):
        raise FileExistsError(f'destination already exists: {archive}')
    initial_units = inactive_writers(writer_units)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with ExitStack() as resources:
        rootfd = os.open(source, flags | os.O_DIRECTORY)
        resources.callback(os.close, rootfd)
        initial_root = signature(os.fstat(rootfd))
        entries = inventory(rootfd)
        if '.lock' in entries:
            lockfd = os.open('.lock', flags, dir_fd=rootfd)
            resources.callback(os.close, lockfd)
            if signature(os.fstat(lockfd)) != entries['.lock']:
                raise ValueError('store lock changed during backup')
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with tempfile.NamedTemporaryFile(dir=archive.parent, prefix='.crucible-backup-') as draft:
            hashes = {}
            with tarfile.open(fileobj=draft, mode='w:gz') as bundle:
                for name, expected in entries.items():
                    if name == '.lock':
                        continue
                    with os.fdopen(os.open(name, flags, dir_fd=rootfd), 'rb') as stream:
                        st = os.fstat(stream.fileno())
                        if signature(st) != expected:
                            raise ValueError(f'store entry changed before copying: {name}')
                        member = tarfile.TarInfo('progress/' + name)
                        member.size, member.mode = st.st_size, 0o600
                        hashes[member.name] = digest(stream)
                        stream.seek(0)
                        bundle.addfile(member, stream)
                        if signature(os.fstat(stream.fileno())) != expected:
                            raise ValueError(f'store entry changed during copying: {name}')
                manifest = json.dumps({'kind': 'crucible-progress', 'sha256': hashes},
                                      sort_keys=True).encode() + b'\n'
                member = tarfile.TarInfo('manifest.json')
                member.size, member.mode = len(manifest), 0o600
                bundle.addfile(member, io.BytesIO(manifest))
            draft.flush()
            os.fsync(draft.fileno())
            draft.seek(0)
            checksum = digest(draft)
            if (inactive_writers(writer_units) != initial_units
                    or signature(os.stat(source, follow_symlinks=False)) != initial_root
                    or inventory(rootfd) != entries):
                raise ValueError('store or service state changed during backup')
            # Link only after all checks. An existing destination from another
            # backup cannot be replaced, even if it appeared after preflight.
            os.link(draft.name, archive)
        destfd = os.open(archive.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(destfd)
        finally:
            os.close(destfd)
    return checksum


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('store', type=Path, help='absolute or relative path of the quiesced store')
    parser.add_argument('archive', type=Path, help='new archive path outside the store')
    parser.add_argument('--unit', action='append', required=True,
                        help='user service unit that writes to the store (repeat for each writer)')
    args = parser.parse_args()
    try:
        checksum = backup(args.store, args.archive, args.unit)
    except (OSError, ValueError, subprocess.TimeoutExpired, tarfile.TarError) as error:
        parser.exit(1, f'Progress backup failed: {error}\n')
    print(f'{checksum}  {args.archive.name}')
