"""Keep prediction time advancing without fabricating processed-input ACKs."""
import unittest
from unittest.mock import patch, Mock
import lz4.block
import shard_stub as shard


class GameClockTests(unittest.TestCase):
    def state(self):
        return {'character_links_sent': True, 'clock_epoch': 100.0,
                'clock_last_sent': 100.0, 'seq': 65535,
                'received_seq': 42, 'received_window': 3,
                'last_valid_monotonic': 100.0}

    @patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character'})
    def test_advances_beyond_old_frame_limit_without_entity_mutations(self):
        state = self.state()
        self.assertIsNone(shard.game_clock_reply(state, now=100.02))
        reply = shard.game_clock_reply(state, now=105.0)
        raw = lz4.block.decompress(reply[1:], uncompressed_size=4096)
        self.assertEqual(raw[:6], bytes.fromhex('000a0000002a'))
        self.assertEqual(int.from_bytes(raw[11:15], 'big'), 7000)
        self.assertEqual(raw[15:-4], b'\0\0\0\0')  # zero entities, empty buffer
        self.assertIsNone(shard.game_clock_reply(state, now=104.0))
        reply = shard.game_clock_reply(state, now=110.0)
        raw = lz4.block.decompress(reply[1:], uncompressed_size=4096)
        self.assertEqual(int.from_bytes(raw[11:15], 'big'), 12000)
        self.assertNotIn('last_processed_game_time_ms', state)

    def test_tick_interval_defaults_to_50_ms_and_takes_the_retail_32(self):
        for value, seconds in ((None, .05), ('32', .032), ('5', .016), ('500', .1),
                               ('junk', .05)):
            env = {} if value is None else {'CRUCIBLE_SHARD_TICK_MS': value}
            with self.subTest(value=value), patch.dict('os.environ', env, clear=True):
                self.assertEqual(shard.clock_interval(), seconds)
        env = {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character', 'CRUCIBLE_SHARD_TICK_MS': '32'}
        with patch.dict('os.environ', env, clear=True):
            state = self.state()
            self.assertIsNone(shard.game_clock_reply(state, now=100.031))
            self.assertIsNotNone(shard.game_clock_reply(state, now=100.0325))

    def test_not_enabled_before_character_links_or_in_prior_modes(self):
        for mode in ('', 'practice-roster', 'practice-solo'):
            with patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': mode}):
                self.assertIsNone(shard.game_clock_reply(self.state(), now=101.0))
        with patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character'}):
            self.assertIsNone(shard.game_clock_reply({}, now=101.0))
            self.assertIsNone(shard.game_clock_reply(dict(self.state(), character_links_sent=False), now=101.0))

    @patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character'})
    def test_timer_sends_without_input_packets_and_stops_for_idle_peer(self):
        sock = Mock()
        state = self.state()
        states = {('127.0.0.1', 1234): state}
        shard.send_game_clocks(sock, states, idle_reset=10, now=101.0)
        self.assertEqual(sock.sendto.call_count, 1)
        shard.send_game_clocks(sock, states, idle_reset=10, now=111.0)
        self.assertEqual(sock.sendto.call_count, 1)
