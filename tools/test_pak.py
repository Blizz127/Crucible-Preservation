"""tools/pak.py: the seeking reader agrees with the whole-file one."""
import tempfile
import unittest
import zipfile
from pathlib import Path

import pak


class SeekReaderTests(unittest.TestCase):
    def test_index_and_read_match_load_for_stored_and_deflated_entries(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'test.pak'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('a/stored.bin', bytes(range(256)) * 3, zipfile.ZIP_STORED)
                archive.writestr('b/deflated.txt', b'crucible ' * 500, zipfile.ZIP_DEFLATED)
                archive.writestr('c/empty', b'', zipfile.ZIP_DEFLATED)
            whole = {e['name']: pak.entry_bytes(e) for e in pak.load(path)}
            index = pak.index(path)
            self.assertEqual(set(index), set(whole))
            self.assertEqual(pak.read(path, list(index), index), whole)
            self.assertEqual(pak.read(path, [b'b/deflated.txt']),
                             {b'b/deflated.txt': b'crucible ' * 500})

    def test_a_file_that_is_not_a_pak_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'not.pak'
            path.write_bytes(b'\0' * 100)
            with self.assertRaises(ValueError):
                pak.index(path)


if __name__ == '__main__':
    unittest.main()
