"""Bounded bootstrap probe: framing and launch isolation, not gameplay proof."""
import os
import unittest
from unittest.mock import patch
import zlib
import tempfile
import local_sessions
import lz4.block
import shard_stub as shard


def client_packet(kind, seq, ack=0, window=0):
    body = (kind.to_bytes(2, 'big') + seq.to_bytes(2, 'big')
            + ack.to_bytes(2, 'big') + window.to_bytes(4, 'big') + b'\0\0')
    return lz4.block.compress(body + zlib.crc32(body).to_bytes(4, 'big'), store_size=False)


class BootstrapTests(unittest.TestCase):
    def test_client_probe_waits_for_identity_and_packs_both_entities(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-client',
                'CRUCIBLE_LOCAL_SESSIONS': directory}):
            state = {'peer_ip': '127.0.0.1'}
            shard.shard_reply(client_packet(5, 1), state)
            for seq in range(2, 12):
                packet = client_packet(2, seq, ack=1, window=1)
                shard.shard_reply(packet, state)
                self.assertIsNone(shard.bootstrap_reply(packet, state))
            self.assertNotIn('bootstrap_sent', state)
            local_sessions.publish(directory, 'test', '127.0.0.1', 123)
            packet = client_packet(2, 12, ack=1, window=1)
            shard.shard_reply(packet, state)
            out = shard.bootstrap_reply(packet, state)
            self.assertIsNotNone(out)
            raw = lz4.block.decompress(out[1:], uncompressed_size=4096)
            self.assertEqual(raw[15:17], b'\0\2')
            self.assertEqual(raw[37:39], b'\0\x0d')
            self.assertEqual(raw[39:-4], bytes.fromhex('060380060a800106000000007b'))
            self.assertIsNone(shard.bootstrap_reply(packet, state))

    def test_solo_phase_needs_only_own_activated_player(self):
        with patch.dict(os.environ, {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-solo'}):
            state = {'seq': 20, 'received_seq': 15, 'received_window': 1,
                     'accept': b'accepted', 'bootstrap_sent': True}
            self.assertIsNone(shard.solo_phase_reply(state))
            state['local_player_activation_received'] = True
            reply = shard.solo_phase_reply(state)
            self.assertIsNotNone(reply)
            raw = lz4.block.decompress(reply[1:], uncompressed_size=4096)
            self.assertEqual(raw[:2], b'\0\x0a')
            # One baked MatchPhase message, one component with CurrentPhase 8.
            self.assertEqual(raw[15:17], b'\0\1')
            self.assertEqual(raw[17:27], bytes.fromhex('f603114cda63607b0004'))
            self.assertEqual(raw[27:-4], bytes.fromhex('000404050008'))
            self.assertIsNone(shard.solo_phase_reply(state))
            self.assertNotIn('gameplay_ready', state)

    def test_phase_probe_does_not_run_in_prior_probe_modes(self):
        for mode in ('practice-client', 'practice-gamemode', ''):
            with patch.dict(os.environ, {'CRUCIBLE_SHARD_BOOTSTRAP': mode}):
                self.assertIsNone(shard.solo_phase_reply({
                    'bootstrap_sent': True, 'local_player_activation_received': True}))

    def test_match_store_probe_is_once_and_requires_own_activation(self):
        state = {'bootstrap_sent': True, 'seq': 10, 'received_seq': 9, 'received_window': 1}
        with patch.dict(os.environ, {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-roster'}):
            self.assertIsNone(shard.match_store_reply(state))
            state['local_player_activation_received'] = True
            reply = shard.match_store_reply(state)
            self.assertIsNotNone(reply)
            raw = lz4.block.decompress(reply[1:], uncompressed_size=4096)
            self.assertEqual(raw[15:17], b'\0\2')
            self.assertIsNone(shard.match_store_reply(state))
        with patch.dict(os.environ, {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-solo'}):
            self.assertIsNone(shard.match_store_reply(dict(state, match_store_sent=False)))

    def test_match_store_and_local_slot_use_separate_entity_records(self):
        store = shard.build_practice_match_store_probe()
        self.assertEqual(store.header.hex(), '9e04114cda636080850002')
        self.assertEqual(store.payload, bytes.fromhex('0200'))
        slot = shard.build_local_match_slot_probe(previous_id=135)
        self.assertEqual(slot.header.hex(), '9704020006')
        # Native int8 reader 4f56640 copies raw bytes: slot 1 is 01.
        self.assertEqual(slot.payload.hex(), '090c00060001')

    def test_one_player_roster_uses_captured_graph_and_unspawned_status(self):
        message = shard.build_single_player_roster_probe(b'Hunter')
        # Live graph: DataStore root, six unchanged properties, PlayerList,
        # its Size and entry 0, three unchanged properties, NumPlayersInMatch,
        # two unchanged properties, then unchanged EntityFlags root.
        self.assertEqual(message.payload[:4].hex(), '11812300')
        self.assertEqual(message.payload[4:6], b'\1\1')  # size 1, player ID 1
        self.assertIn(b'\x06Hunter', message.payload)
        # Unknown character/status until a hero actually exists; team/party 1,
        # drop master true, respawn index -1, and exactly one player in match.
        self.assertTrue(message.payload.endswith(bytes.fromhex('0000000101017fff0000000001')))
        for name in (b'x' * 128, b'bad\0name'):
            with self.assertRaises(ValueError):
                shard.build_single_player_roster_probe(name)

    def test_composite_client_uses_two_directions_and_session_user(self):
        message = shard.build_composite_client_probe(676364177, previous_id=129)
        # Network counter 1 after baked counter 129, autonomous role, slice 0.
        self.assertEqual(message.header[:3], bytes.fromhex('ff0312'))
        self.assertEqual(int.from_bytes(message.header[3:7], 'big'), 646954285)
        self.assertEqual(message.header[7:], bytes.fromhex('000009'))
        # Eight shared roots; Player is last, with UserId present and Name absent.
        self.assertEqual(message.payload[:3], bytes.fromhex('0a8001'))
        # Six autonomous roots are unchanged. Values follow BOTH records.
        self.assertEqual(message.payload[3:5], bytes.fromhex('0600'))
        self.assertEqual(int.from_bytes(message.payload[5:], 'big'), 676364177)

    def test_composite_client_requires_nonzero_explicit_user(self):
        for user in (0, -1, 2**32):
            with self.assertRaises(ValueError):
                shard.build_composite_client_probe(user)

    def test_lsb_record_crosses_byte_and_varint_boundaries(self):
        bits = [False] * 129
        for i in [0, 7, 8, 128]: bits[i] = True
        self.assertEqual(shard.build_replication_record(bits),
                         b'\x81\x01\x81\x01' + bytes(14) + b'\x01')
        with self.assertRaises(ValueError):
            shard.build_replication_record([False] * 1025)

    def test_practice_configuration_wire(self):
        message = shard.build_practice_gamemode_probe()
        self.assertEqual(message.header[:3], b'\x86\x04\x11')
        self.assertEqual(int.from_bytes(message.header[3:7], 'big'), 1289380704)
        self.assertEqual(message.header[7:], bytes.fromhex('7f0004'))
        # Ranged signed int16 is value - (-32768), not raw signed bytes.
        self.assertEqual(int.from_bytes(message.payload[-2:], 'big') - 32768, 6)

    @patch.dict(os.environ, {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-gamemode'})
    def test_only_after_accept_ack_and_unique_heartbeats_then_once(self):
        state = {}
        packet = client_packet(2, 1)
        shard.shard_reply(packet, state)
        self.assertIsNone(shard.bootstrap_reply(packet, state))
        shard.shard_reply(client_packet(5, 2), state)
        for seq in range(3, 6):
            packet = client_packet(2, seq)  # no Accept acknowledgement
            shard.shard_reply(packet, state)
            self.assertIsNone(shard.bootstrap_reply(packet, state))
        for seq in range(6, 16):
            packet = client_packet(2, seq, ack=2, window=1)
            shard.shard_reply(packet, state)
            result = shard.bootstrap_reply(packet, state)
            if seq < 15:
                self.assertIsNone(result)
                self.assertIsNone(shard.bootstrap_reply(packet, state))
        self.assertIsNotNone(result)
        raw = lz4.block.decompress(result[1:], uncompressed_size=4096)
        self.assertEqual(raw[:2], b'\0\x0a')
        self.assertEqual(int.from_bytes(raw[4:6], 'big'), 15)
        self.assertEqual(raw[15:17], b'\0\x01')
        self.assertEqual(raw[17:-4], bytes.fromhex('8604114cda63607f0004000406038006'))
        self.assertEqual(zlib.crc32(raw[:-4]), int.from_bytes(raw[-4:], 'big'))
        self.assertIsNone(shard.bootstrap_reply(client_packet(2, 16, 15, 0xffffffff), state))

    @patch.dict(os.environ, {}, clear=True)
    def test_probe_disabled_by_default(self):
        self.assertIsNone(shard.bootstrap_reply(client_packet(2, 1), {}))


if __name__ == '__main__':
    unittest.main()
