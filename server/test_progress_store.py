import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import progress_store as store


class DocumentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.documents = store.Store(self.root)
        self.path = self.root / 'example.json'
        logger = patch.object(store, 'log')
        logger.start()
        self.addCleanup(logger.stop)

    def test_an_update_is_atomic_and_leaves_no_temporary_file(self):
        self.documents.transaction('example', lambda doc: doc.update(notes='saved'))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['.lock', 'example.json'])
        document = json.loads(self.path.read_text())
        self.assertEqual(document['version'], store.VERSION)
        self.assertEqual(document['notes'], 'saved')

    def test_a_corrupt_document_is_kept_not_overwritten(self):
        garbage = b'{"notes": "partial'
        self.path.write_bytes(garbage)
        self.documents.transaction('example', lambda doc: doc.update(notes='recovered'))
        kept = list(self.root.glob('example.json.corrupt-*'))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), garbage)
        self.assertEqual(json.loads(self.path.read_text())['notes'], 'recovered')

    def test_a_newer_document_is_read_only(self):
        original = json.dumps({'version': store.VERSION + 1, 'notes': 'keep me'})
        self.path.write_text(original)
        with self.assertRaises(store.StoreError):
            self.documents.transaction('example', lambda doc: doc.update(notes='changed'))
        self.assertEqual(self.path.read_text(), original)

    def test_an_older_or_unversioned_document_is_filled_and_keeps_its_keys(self):
        self.path.write_text(json.dumps({'notes': 'keep me'}))
        self.documents.transaction('example', lambda doc: doc.update(extra='added'))
        document = json.loads(self.path.read_text())
        self.assertEqual(document['notes'], 'keep me')
        self.assertEqual(document['extra'], 'added')
        self.assertEqual(document['version'], store.VERSION)


class ProgressJournalTests(unittest.TestCase):
    def test_a_short_write_is_finished_and_a_damaged_tail_is_not_glued(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name, 'file.jsonl')
        real = os.write

        def short(descriptor, data):
            return real(descriptor, bytes(data)[:7])
        with patch('os.write', short):
            store.append_line(path, '{"whole": "line one"}\n')
        self.assertEqual(path.read_text(), '{"whole": "line one"}\n')
        # A writer that died halfway: the next line starts on its own line.
        with open(path, 'a') as stream:
            stream.write('{"id": "half')
        store.append_line(path, '{"whole": "line two"}\n')
        self.assertEqual(path.read_text().split('\n'),
                         ['{"whole": "line one"}', '{"id": "half', '{"whole": "line two"}', ''])


if __name__ == '__main__':
    unittest.main()
