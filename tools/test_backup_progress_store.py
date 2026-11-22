import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backup_progress_store as backup
from stage_player_checkpoint import stage


class ProgressBackupTests(unittest.TestCase):
    writer_units = ('writer-one.service', 'writer-two.service')

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.store = self.root / 'progress'
        self.store.mkdir()
        self.archive = self.root / 'snapshot.tar.gz'
        self.manager = self.enterContext(patch.object(backup.subprocess, 'run'))
        self.manager.side_effect = self.inactive_service

    @staticmethod
    def inactive_service(cmd, **kwargs):
        state = {'Id': cmd[-1], 'LoadState': 'loaded', 'ActiveState': 'inactive',
                 'SubState': 'dead', 'MainPID': '0', 'ControlPID': '0', 'Job': '',
                 'InactiveExitTimestampMonotonic': '100', 'InactiveEnterTimestampMonotonic': '101'}
        return subprocess.CompletedProcess(cmd, 0, ''.join(f'{k}={v}\n' for k, v in state.items()))

    def test_roundtrip_preserves_raw_store(self):
        data = {'account.json': b'{"future":{"unknown":32},"version":903}\n',
                'account.json.corrupt-keep': b'\xff\x00invalid saved bytes\n',
                'applied.jsonl': b'{"id":"m1","account":"account"}\n',
                'spool.jsonl': b'{"id":"m1","future":true}\n{"incomplete"',
                'future.store': b'\x01\x08not json'}
        for name, content in data.items():
            (self.store / name).write_bytes(content)
        (self.store / '.lock').touch()
        receipt = backup.backup(self.store, self.archive, self.writer_units)
        copy = stage(self.archive, receipt, self.root / 'staged') / 'progress'
        self.assertEqual({p.name: p.read_bytes() for p in copy.iterdir()}, data)
        manifest = json.loads((copy.parent / 'manifest.json').read_bytes())
        self.assertEqual(set(manifest['sha256']), {'progress/' + name for name in data})
        self.assertEqual([call.args[0][-1] for call in self.manager.call_args_list],
                         list(self.writer_units) * 2)

    def test_empty_or_lock_only_store_still_exists_after_restore(self):
        for name in ('empty', 'lock_only'):
            with self.subTest(name=name):
                if name == 'lock_only':
                    (self.store / '.lock').touch()
                dest = self.root / (name + '.tgz')
                receipt = backup.backup(self.store, dest, self.writer_units)
                staging = self.root / (name + '-staging')
                folder = stage(dest, receipt, staging) / 'progress'
                self.assertEqual(list(folder.iterdir()), [])
                folder.rmdir()
                with self.assertRaisesRegex(ValueError, 'directory is missing'):
                    stage(dest, receipt, staging)

    def test_active_restarting_unavailable_and_ambiguous_services(self):
        for old, new in [('inactive', 'active'), ('SubState=dead', 'SubState=auto-restart'),
                         ('ControlPID=0', 'ControlPID=14'), ('Job=\n', 'Job=233\n'),
                         ('LoadState=loaded', 'LoadState=not-found'), ('MainPID=0\n', '')]:
            def changed_state(cmd, **kwargs):
                value = self.inactive_service(cmd)
                if cmd[-1] == self.writer_units[1]:
                    value.stdout = value.stdout.replace(old,new)
                return value
            with self.subTest(new=new):
                self.manager.side_effect = changed_state
                with self.assertRaises(ValueError):
                    backup.backup(self.store, self.archive, self.writer_units)
                self.assertFalse(self.archive.exists())
        self.manager.side_effect = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, '')
        with self.assertRaisesRegex(ValueError, 'service manager'):
            backup.backup(self.store, self.archive, self.writer_units)

    def test_restart_or_changed_store_during_snapshot_aborts_publication(self):
        for action in ('restart', 'new-file'):
            calls = 0
            def state_change(cmd, **kwargs):
                nonlocal calls
                calls += 1
                result = self.inactive_service(cmd)
                if calls == 3 and action == 'restart':
                    result.stdout = result.stdout.replace('Monotonic=100', 'Monotonic=122')
                if calls == 3 and action == 'new-file':
                    (self.store / 'saved-later').write_bytes(b'newer data')
                return result
            with self.subTest(action=action):
                self.manager.side_effect = state_change
                with self.assertRaisesRegex(ValueError, 'changed'):
                    backup.backup(self.store, self.archive, self.writer_units)
                self.assertFalse(self.archive.exists())

    def test_refuses_symlinks_created_destinations_and_nonregular_sources(self):
        (self.root / 'separate').write_bytes(b'previous')
        (self.store / 'link').symlink_to(self.root / 'separate')
        with self.assertRaisesRegex(ValueError, 'non-regular'):
            backup.backup(self.store, self.archive, self.writer_units)
        (self.store / 'link').unlink()
        with self.assertRaises(ValueError):
            backup.backup(self.store, self.store / 'self.tar.gz', self.writer_units)
        with self.assertRaises(FileNotFoundError):
            backup.backup(self.root / 'missing', self.archive, self.writer_units)
        self.archive.symlink_to(self.root / 'separate')
        with self.assertRaises(FileExistsError):
            backup.backup(self.store, self.archive, self.writer_units)
        self.assertEqual(self.archive.read_bytes(), b'previous')

    def test_competing_publication_is_left_intact(self):
        calls = 0
        def race(cmd, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.archive.write_bytes(b'winner')
            return self.inactive_service(cmd)
        self.manager.side_effect = race
        with self.assertRaises(FileExistsError):
            backup.backup(self.store, self.archive, self.writer_units)
        self.assertEqual(self.archive.read_bytes(), b'winner')
        self.assertFalse(list(self.root.glob('.crucible-backup-*')))
