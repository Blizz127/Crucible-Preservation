"""Regression tests from captured retail datagrams, 2026-09-19."""
import unittest
import zlib
import lz4.block
import shard_stub as shard


class ShardTransportTests(unittest.TestCase):
    def test_captured_compressed_disconnect(self):
        # This includes back-references, unlike the all-literal heartbeat.
        parsed = shard.parse_shard(bytes.fromhex('530001000600010050089714f298'))
        self.assertIsNotNone(parsed)
        prefix, region, crc = parsed
        self.assertEqual(region.hex(), '00010006000000000000000008')
        self.assertEqual(zlib.crc32(region), int.from_bytes(crc, 'big'))

    def test_reject_corrupt_capture(self):
        data = bytes.fromhex('f0010002000200000000000000000d4f5729')
        self.assertIsNone(shard.parse_shard(data))

    def test_connect_retry_reuses_accept_sequence(self):
        def connect(seq):
            raw = bytes.fromhex('0005') + seq.to_bytes(2, 'big') + bytes(7)
            return lz4.block.compress(raw + zlib.crc32(raw).to_bytes(4, 'big'), store_size=False)
        state = {}
        first = shard.shard_reply(connect(1), state)
        retry = shard.shard_reply(connect(3), state)
        self.assertEqual(first, retry)

    def test_accept_acknowledges_reliable_connect(self):
        raw = bytes.fromhex('00050001000000000000010000')
        wire = lz4.block.compress(raw + zlib.crc32(raw).to_bytes(4, 'big'), store_size=False)
        reply = lz4.block.decompress(shard.shard_reply(wire, {})[1:], uncompressed_size=4096)
        self.assertEqual(reply[4:10].hex(), '000100000001')

    def test_ack_window_handles_loss_reordering_and_wrap(self):
        state = {}
        def heartbeat(seq):
            raw = b'\x00\x02' + seq.to_bytes(2, 'big') + bytes(8)
            wire = lz4.block.compress(raw + zlib.crc32(raw).to_bytes(4, 'big'), store_size=False)
            return lz4.block.decompress(shard.shard_reply(wire, state)[1:], uncompressed_size=4096)
        heartbeat(65534)
        heartbeat(0)  # Lost 65535, then wrap.
        reply = heartbeat(65535)  # Late arrival fills the gap.
        self.assertEqual(reply[4:10].hex(), '000000000007')
        reply = heartbeat(40)  # Older history falls outside the window.
        self.assertEqual(reply[4:10].hex(), '002800000001')

    def test_game_requests_receive_ack_without_waiting_for_heartbeat(self):
        # Player activation switches from heartbeat to reliable EntityRpcs.
        # A no-data AcknowledgeControlledEntity RPC (169/2), from source.
        raw = bytes.fromhex('000b0010000c0000000101000000010301000001a9020000')
        wire = lz4.block.compress(raw + zlib.crc32(raw).to_bytes(4, 'big'), store_size=False)
        state = {'accept': b'accepted', 'bootstrap_sent': True}
        reply = shard.shard_reply(wire, state)
        self.assertIsNotNone(reply)
        decoded = lz4.block.decompress(reply[1:], uncompressed_size=4096)
        self.assertEqual(decoded[:2], b'\x00\x02')
        self.assertEqual(decoded[4:10].hex(), '001000000001')
        self.assertEqual(decoded[11:-4], b'\x00')
        self.assertEqual(state['client_rpc_counts'], {(169, 2): 1})
        # A retransmission receives an ack but does not dispatch twice.
        self.assertIsNotNone(shard.shard_reply(wire, state))
        self.assertEqual(state['client_rpc_counts'], {(169, 2): 1})
        self.assertNotIn('match_started', state)

    def test_malformed_blocks_are_ignored(self):
        for wire in (b'', b'\xf0', b'\x00\x00\x00', b'\xff' * 32):
            with self.subTest(wire=wire):
                self.assertIsNone(shard.parse_shard(wire))

    def test_server_packet_decompresses_after_base_type(self):
        packet = shard.build_shard(b'\0', 6, 1, 0, b'\0')
        self.assertEqual(packet[0], 0)
        raw = lz4.block.decompress(packet[1:], uncompressed_size=4096)
        self.assertEqual(raw[:-4].hex(), '000600010000000000000000')
        self.assertEqual(zlib.crc32(raw[:-4]), int.from_bytes(raw[-4:], 'big'))

if __name__ == '__main__':
    unittest.main()
