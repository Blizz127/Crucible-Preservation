#!/usr/bin/env python3
"""Verify a private Crucible migration checkpoint and stage it for operator review."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile


def digest(stream):
    checksum = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        checksum.update(chunk)
    return checksum.hexdigest()


def checkpoint_files(archive):
    members = {}
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if (not member.isfile() or path.is_absolute() or '..' in path.parts
                or str(path) != member.name or member.name in members):
            raise ValueError('unexpected checkpoint member: ' + member.name)
        members[member.name] = member
    if 'manifest.json' not in members:
        raise ValueError('missing checkpoint manifest')
    with archive.extractfile(members['manifest.json']) as stream:
        manifest_bytes = stream.read()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or not isinstance(manifest.get('sha256'), dict):
        raise ValueError('invalid checkpoint inventory')
    expected = manifest['sha256']
    if set(expected) != members.keys() - {'manifest.json'}:
        raise ValueError('checkpoint inventory differs from its manifest')
    if not all(isinstance(h, str) and re.fullmatch('[a-f0-9]{64}', h)
               for h in expected.values()):
        raise ValueError('invalid checkpoint file digest')
    progress_backup = manifest.get('kind') == 'crucible-progress'
    if progress_backup and any(PurePosixPath(name).parent != PurePosixPath('progress')
                               or name == 'progress/.lock' for name in expected):
        raise ValueError('invalid progression store inventory')
    expected['manifest.json'] = hashlib.sha256(manifest_bytes).hexdigest()
    return members, expected, progress_backup


def verify_staged(directory, expected, progress_backup):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('staging destination is not a directory')
    if progress_backup and not (directory / 'progress').is_dir():
        raise ValueError('staged progression directory is missing')
    found = set()
    for entry in directory.rglob('*'):
        name = entry.relative_to(directory).as_posix()
        if entry.is_symlink():
            raise ValueError('unexpected symbolic link: ' + name)
        if entry.is_dir():
            continue
        if name not in expected or not entry.is_file():
            raise ValueError('unexpected file in staging directory: ' + name)
        with entry.open('rb') as stream:
            if digest(stream) != expected[name]:
                raise ValueError('staged file differs from checkpoint: ' + name)
        found.add(name)
    if found != set(expected):
        raise ValueError('staged checkpoint is incomplete')


def stage(source, expected_sha256, staging_root):
    if not re.fullmatch('[a-f0-9]{64}', expected_sha256):
        raise ValueError('provide the SHA-256 from the source backup receipt')
    with source.open('rb') as stream:
        if digest(stream) != expected_sha256:
            raise ValueError('archive differs from the source backup receipt')
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode='r:gz') as archive:
            members, expected, progress_backup = checkpoint_files(archive)
            staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            target = staging_root / ('checkpoint-' + expected_sha256)
            with (staging_root / '.checkpoint-stage.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if target.exists() or target.is_symlink():
                    verify_staged(target, expected, progress_backup)
                    return target
                with tempfile.TemporaryDirectory(dir=staging_root) as temp:
                    draft = Path(temp) / 'data'
                    draft.mkdir(mode=0o700)
                    if progress_backup:
                        (draft / 'progress').mkdir(mode=0o700)
                    for name, member in members.items():
                        path = draft / name
                        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        with archive.extractfile(member) as payload, path.open('xb') as out:
                            path.chmod(0o600)
                            shutil.copyfileobj(payload, out)
                    verify_staged(draft, expected, progress_backup)
                    draft.rename(target)
                return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--sha256', required=True, help='trusted source archive checksum')
    parser.add_argument('--staging-root', required=True, type=Path,
                        help='operator-owned directory separate from the active save store')
    args = parser.parse_args()
    try:
        print(stage(args.archive, args.sha256, args.staging_root))
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        parser.exit(1, f'Checkpoint staging failed: {error}\n')
