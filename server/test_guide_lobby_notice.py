import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import guide_lobby_notice as notice


class GuideLobbyNoticeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.marker = self.root / 'guide.notice'
        self.enterContext(mock.patch.dict(os.environ, CRUCIBLE_GUIDE_NOTICE_FILE=str(self.marker)))

    def test_separate_readers_see_live_holder_and_kernel_clears_it_on_exit(self):
        self.assertFalse(notice.guide_is_serving())
        self.assertFalse(self.marker.exists())
        script = """
import os, sys
sys.path.insert(0, sys.argv[1])
import guide_lobby_notice as notice
fd, retry = notice.acquire()
assert fd is not None and not retry
print('ready', flush=True)
sys.stdin.buffer.read(1)
os._exit(1)
"""
        with subprocess.Popen([sys.executable, '-IB', '-c', script,
                               str(Path(notice.__file__).resolve().parent)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE) as child:
            try:
                self.assertEqual(child.stdout.readline(), b'ready\n')
                self.assertTrue(notice.guide_is_serving())
                self.assertEqual(notice.acquire(), (None, True))
                self.assertEqual(self.marker.stat().st_size, 0)
                self.assertEqual(self.marker.stat().st_mode & 0o777, 0o600)
            finally:
                child.stdin.close()
                child.wait(timeout=3)
        self.assertEqual(child.returncode, 1)
        self.assertTrue(self.marker.exists())
        self.assertFalse(notice.guide_is_serving())
        fd, retry = notice.acquire()
        self.assertIsNotNone(fd)
        try:
            self.assertFalse(retry)
            self.assertTrue(notice.guide_is_serving())
        finally:
            os.close(fd)
        self.assertFalse(notice.guide_is_serving())

    def test_simultaneous_readers_do_not_publish(self):
        self.marker.touch(mode=0o600)
        with self.marker.open('rb') as reader:
            fcntl.flock(reader, fcntl.LOCK_SH)
            self.assertFalse(notice.guide_is_serving())
            self.assertEqual(notice.acquire(), (None, True))
        fd, retry = notice.acquire()
        self.assertIsNotNone(fd)
        os.close(fd)
        self.assertFalse(retry)

    def test_unconfigured_unsafe_and_nonempty_paths_omit_the_notice(self):
        unsafe = self.root / 'public'
        unsafe.mkdir()
        unsafe.chmod(0o755)
        contents = self.root / 'preserved'
        contents.write_bytes(b'keep')
        contents.chmod(0o600)
        symlink = self.root / 'alias'
        symlink.symlink_to(contents)
        fifo = self.root / 'pipe'
        os.mkfifo(fifo, 0o600)
        readable = self.root / 'public-file'
        readable.touch()
        readable.chmod(0o644)
        for candidate in ('', 'relative', unsafe / 'guide', self.root / 'missing' / 'guide',
                          contents, symlink, fifo, readable):
            with self.subTest(candidate=candidate), mock.patch.dict(
                    os.environ, CRUCIBLE_GUIDE_NOTICE_FILE=str(candidate)):
                self.assertFalse(notice.guide_is_serving())
                self.assertEqual(notice.acquire(), (None, False))
        self.assertEqual(contents.read_bytes(), b'keep')
        self.assertFalse((unsafe / 'guide').exists())

    def test_unsupported_locks_omit_the_notice(self):
        self.marker.touch(mode=0o600)
        with mock.patch.object(fcntl, 'flock', side_effect=OSError('unsupported')):
            self.assertFalse(notice.guide_is_serving())
            self.assertEqual(notice.acquire(), (None, False))
