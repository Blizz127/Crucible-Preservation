"""Exercise the actual pak reader contract with a small authored fixture."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

import inspect_anim_graph


class AnimationGraphLoadTests(unittest.TestCase):
    def test_reads_graph_from_pak_and_hashes_uncompressed_bytes(self):
        blob = b'<ObjectStream version="3" />'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.pak'
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('animations/test.animgraph', blob)
            self.assertEqual(inspect_anim_graph.load(path, 'animations/test.animgraph'),
                             (blob, hashlib.sha256(blob).hexdigest()))
            with self.assertRaisesRegex(SystemExit, 'missing.animgraph not in'):
                inspect_anim_graph.load(path, 'missing.animgraph')


if __name__ == '__main__':
    unittest.main()
