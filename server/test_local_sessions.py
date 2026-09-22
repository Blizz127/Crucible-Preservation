import tempfile
import unittest
import os
from unittest.mock import patch
from pathlib import Path

import local_sessions as sessions
import match_stub as hub


class LocalSessionTests(unittest.TestCase):
    def test_hub_disconnect_preserves_one_time_shard_handoff(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'CRUCIBLE_LOCAL_SESSIONS': directory,
                'CRUCIBLE_HUB_SHARDADDR': 'reply'}):
            state = {'key': 'handoff', 'peer': '127.0.0.1'}
            hub.hubrpc_reply(hub.build_hub(5, 1, (123).to_bytes(4, 'big') + b'\0'), state)
            hub.hubrpc_reply(hub.build_hub(11, 2, b''), state)
            hub.finish_local_session(state)
            self.assertEqual(sessions.claim(directory, '127.0.0.1'), 123)
            self.assertIsNone(sessions.claim(directory, '127.0.0.1'))
            # A late heartbeat cannot resurrect an already claimed handoff.
            sessions.publish(directory, 'handoff', '127.0.0.1', 123)
            self.assertIsNone(sessions.claim(directory, '127.0.0.1'))

    def test_hub_handoff_is_per_connection_and_requires_shard_request(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'CRUCIBLE_LOCAL_SESSIONS': directory,
                'CRUCIBLE_HUB_SHARDADDR': 'reply'}):
            state = {'key': 'one', 'peer': '127.0.0.1'}
            hub.hubrpc_reply(hub.build_hub(5, 1, (123).to_bytes(4, 'big') + b'\0'), state)
            self.assertIsNone(sessions.lookup(directory, '127.0.0.1'))
            hub.hubrpc_reply(hub.build_hub(11, 2, b''), state)
            self.assertEqual(sessions.lookup(directory, '127.0.0.1'), 123)
            # A heartbeat cannot change the identity established by Connect.
            hub.hubrpc_reply(hub.build_hub(8, 3, (456).to_bytes(4, 'big')), state)
            self.assertIsNone(sessions.lookup(directory, '127.0.0.1'))

    def test_fresh_unique_identity_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(sessions.lookup(directory, '127.0.0.1', now=100))
            sessions.publish(directory, 'one', '127.0.0.1', 123, now=100)
            self.assertEqual(sessions.lookup(directory, '127.0.0.1', now=110), 123)
            self.assertIsNone(sessions.lookup(directory, '127.0.0.1', now=161))
            self.assertIsNone(sessions.lookup(directory, '127.0.0.1', now=99))
            sessions.remove(directory, 'one')
            self.assertIsNone(sessions.lookup(directory, '127.0.0.1', now=110))

    def test_ambiguous_and_nonlocal_sessions_do_not_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            sessions.publish(directory, 'one', '127.0.0.1', 123, now=100)
            sessions.publish(directory, 'two', '127.0.0.1', 456, now=100)
            self.assertIsNone(sessions.lookup(directory, '127.0.0.1', now=110))
            self.assertIsNone(sessions.lookup(directory, '192.0.2.1', now=110))
            with self.assertRaises(ValueError):
                sessions.publish(directory, 'remote', '192.0.2.1', 1, now=100)

    def test_corrupt_record_prevents_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            sessions.publish(directory, 'one', '127.0.0.1', 123, now=100)
            Path(directory, 'bad.json').write_text('{')
            self.assertIsNone(sessions.lookup(directory, '127.0.0.1', now=110))


if __name__ == '__main__':
    unittest.main()
