import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from stage_player_checkpoint import stage


class PlayerCheckpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.archive = self.root / 'backup.tar.gz'
        self.stage_root = self.root / 'rehearsal'
        self.name = 'state/active-hero.json'
        self.hero = b'{"character":"saboteur"}'

    def write_snapshot(self, checksum=None):
        checksum = checksum or hashlib.sha256(self.hero).hexdigest()
        manifest = json.dumps({'sha256': {self.name: checksum}}).encode()
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data in [('manifest.json', manifest), (self.name, self.hero)]:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_retry_preserves_staged_data_and_other_checkpoints(self):
        hash_value = self.write_snapshot()
        self.stage_root.mkdir()
        other = self.stage_root / 'previous-save'
        other.write_bytes(b'previous')
        result = stage(self.archive, hash_value, self.stage_root)
        file = result / self.name
        before = file.stat()
        self.assertEqual(file.read_bytes(), self.hero)
        self.assertEqual(stage(self.archive, hash_value, self.stage_root), result)
        self.assertEqual(file.stat().st_ino, before.st_ino)
        self.assertEqual(other.read_bytes(), b'previous')

    def test_changed_staged_save_is_preserved_and_reported(self):
        hash_value = self.write_snapshot()
        result = stage(self.archive, hash_value, self.stage_root)
        file = result / self.name
        file.write_bytes(b'updated')
        with self.assertRaisesRegex(ValueError, 'differs'):
            stage(self.archive, hash_value, self.stage_root)
        self.assertEqual(file.read_bytes(), b'updated')

    def test_receipt_and_member_hash_failures_do_not_install_a_checkpoint(self):
        hash_value = self.write_snapshot(checksum='0' * 64)
        with self.assertRaisesRegex(ValueError, 'source backup receipt'):
            stage(self.archive, 'f' * 64, self.stage_root)
        self.assertFalse(self.stage_root.exists())
        with self.assertRaisesRegex(ValueError, 'differs from checkpoint'):
            stage(self.archive, hash_value, self.stage_root)
        self.assertFalse(any(self.stage_root.glob('checkpoint-*')))

    def test_stage_refuses_link_or_parent_entries(self):
        for name, kind in [('state/hero', tarfile.SYMTYPE), ('../hero', tarfile.REGTYPE)]:
            with self.subTest(name=name):
                with tarfile.open(self.archive, 'w:gz') as archive:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    if entry.issym():
                        entry.linkname = '../hero'
                    archive.addfile(entry)
                checksum = hashlib.sha256(self.archive.read_bytes()).hexdigest()
                with self.assertRaisesRegex(ValueError, 'unexpected checkpoint member'):
                    stage(self.archive, checksum, self.stage_root)
                self.assertFalse(self.stage_root.exists())
