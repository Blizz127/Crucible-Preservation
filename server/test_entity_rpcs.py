"""Source-derived RPC framing and strict bounds; no identity strings in fixtures."""
import unittest
import shard_stub as shard


class EntityRpcTests(unittest.TestCase):
    def test_multiple_interleaved_messages(self):
        payload = bytes.fromhex('00020301000001a90100036162630301000001a9020000')
        messages = shard.parse_entity_rpcs(payload)
        self.assertEqual([(m.delivery, m.entity_id, m.component, m.rpc, len(m.data))
                          for m in messages],
                         [(3, 0x1000001, 169, 1, 3), (3, 0x1000001, 169, 2, 0)])
        self.assertEqual(messages[0].data, b'abc')

    def test_rejects_truncation_trailing_data_and_range_violations(self):
        valid = bytes.fromhex('00010301000001a9020000')
        invalid = [valid[:i] for i in range(len(valid))]
        invalid += [valid + b'\0', bytes.fromhex('0801'),
                    bytes.fromhex('00010501000001a9020000'),
                    bytes.fromhex('00010301000001a9021801')]
        for payload in invalid:
            with self.subTest(payload=payload.hex()), self.assertRaises(ValueError):
                shard.parse_entity_rpcs(payload)
        self.assertEqual(shard.parse_entity_rpcs(b'\0\0'), [])

    def test_activation_signal_rejects_other_entities_and_rpc_shapes(self):
        import struct
        from unittest.mock import patch
        for delivery, entity, component, rpc, data in (
                (3, 0x1000002, 169, 2, b''), (2, 0x1000001, 169, 2, b''),
                (3, 0x1000001, 169, 2, b'x'), (3, 0x1000001, 172, 1, b'')):
            payload = b'\0\1' + struct.pack('>BIBBH', delivery, entity, component, rpc, len(data)) + data
            region = bytes.fromhex('000b0001000000000000010000') + payload
            state = {'bootstrap_sent': True}
            with patch.object(shard, 'log'):
                shard.observe_client_rpcs(region, state)
            self.assertNotIn('local_player_activation_received', state)

    def test_reliable_retry_with_new_packet_sequence_is_not_dispatched_twice(self):
        from unittest.mock import patch
        region = bytes.fromhex('000b000100000000000001000000010301000001a9020000')
        state = {'bootstrap_sent': True}
        with patch.object(shard, 'log'):
            shard.observe_client_rpcs(region, state)
            retry = region[:2] + b'\0\2' + region[4:]
            shard.observe_client_rpcs(retry, state)
        self.assertEqual(state['client_rpc_counts'], {(169, 2): 1})
        self.assertTrue(state['local_player_activation_received'])
