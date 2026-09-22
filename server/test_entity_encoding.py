"""SliceEntryId vectors derived from retail serializer RVA 0x4bfb1d0."""
import unittest
import lz4.block
import shard_stub as shard


class SliceEntryTests(unittest.TestCase):
    def test_entry_boundaries(self):
        vectors = [(-1, 'f0'), (0, '00'), (127, '7f'), (128, '8080'),
                   (16383, 'bfff'), (16384, 'c04000'),
                   (2097151, 'dfffff'), (2097152, 'e0200000'),
                   (268435455, 'efffffff')]
        for index, wire in vectors:
            with self.subTest(index=index):
                self.assertEqual(shard.build_slice_entry_id(0x12345678, index),
                                 bytes.fromhex('12345678' + wire))

    def test_index_is_required(self):
        with self.assertRaises(ValueError):
            shard.build_entity_message(1, b'\x13', slice_hash=501823405)

    def test_entry_precedes_payload_size(self):
        self.assertEqual(shard.build_entity_message(
            1, b'\x13', slice_hash=501823405, slice_entry_index=128,
            payload=b'\xaa\xbb').header.hex(), '04131de937ad80800002')

    def test_invalid_index(self):
        for index in (-2, 268435456):
            with self.subTest(index=index), self.assertRaises(ValueError):
                shard.build_slice_entry_id(0, index)


class EntityIdTests(unittest.TestCase):
    def test_retail_outer_transform_vectors(self):
        # Independent reader model: zigzag decode, arithmetic >> 1, add prior.
        for counter, previous, expected in [(1, 0, '04'), (2, 1, '04'),
                                             (1, 2, '03'), (100, 100, '00'),
                                             (64, 0, '8002')]:
            with self.subTest(counter=counter, previous=previous):
                wire = shard.encode_entity_id(counter, previous)
                self.assertEqual(wire.hex(), expected)
                raw, _ = shard.decode_varint(wire)
                relative = (raw >> 1) ^ -(raw & 1)
                self.assertEqual((relative >> 1) + previous, counter)
        # The failed live packet used 02, which decodes to reserved counter 0.
        with self.assertRaises(ValueError):
            shard.decode_entity_id(b'\x02')

    def test_reserved_ids_and_negative_delta_boundary(self):
        for counter in [0, -1, 0xffffff]:
            with self.assertRaises(ValueError):
                shard.encode_entity_id(counter)
        wire = shard.encode_entity_id(1, 0xfffffe)
        self.assertEqual(shard.decode_entity_id(wire, previous_id=0xfffffe)[0], 1)
        self.assertEqual(shard.encode_entity_id(1, local=True), b'\x06')



class EntityPayloadLayoutTests(unittest.TestCase):
    def test_headers_then_sized_shared_payload(self):
        messages = [
            shard.build_entity_message(1, b'\x01', payload=b'\xaa\xbb'),
            shard.build_entity_message(2, b'\x01', previous_id=1, payload=b'\xcc'),
        ]
        packet = shard.build_entity_updates(b'\x00', 1, 0, messages)
        body = lz4.block.decompress(packet[1:], uncompressed_size=4096)[11:-4]
        # Retail 4b9df30 reads every header before 4aa8380 reads PackingBuffer.
        self.assertEqual(body.hex(), '00000000000204010002040100010003aabbcc')

    def test_empty_packet_still_has_packing_buffer_length(self):
        packet = shard.build_entity_updates(b'\x00', 1, 0, [])
        body = lz4.block.decompress(packet[1:], uncompressed_size=4096)[11:-4]
        self.assertEqual(body, bytes(8))

    def test_shared_payload_capacity(self):
        message = shard.build_entity_message(1, b'\x01', payload=bytes(6145))
        with self.assertRaises(ValueError):
            shard.build_entity_updates(b'\x00', 1, 0, [message])


if __name__ == '__main__':
    unittest.main()
