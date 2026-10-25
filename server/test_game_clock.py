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


class GuardTests(unittest.TestCase):
    @patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character'})
    def test_a_simulation_error_is_logged_and_the_clock_still_ticks(self):
        state = {'character_links_sent': True, 'clock_epoch': 100.0,
                 'clock_last_sent': 100.0, 'seq': 1, 'received_seq': 1,
                 'received_window': 1, 'practice_npcs_sent': True}
        lines = []
        with patch.object(shard, 'log', side_effect=lines.append), \
                patch.object(shard, 'solo_simulation_messages',
                             side_effect=ValueError('bad entry')):
            for k in range(1, 4):
                self.assertIsNotNone(shard.game_clock_reply(state, now=100.0 + .1 * k))
        self.assertEqual(state['simulation_errors'], {'simulation tick': 3})
        # Logged at counts 1 and 2, with the traceback the extractor counts.
        self.assertEqual(len(lines), 2)
        self.assertIn('Traceback (most recent call last)', lines[0])
        self.assertIn('ValueError: bad entry', lines[0])

    @patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character',
                               'CRUCIBLE_SOLO_MODE': 'alpha-hunters'}, clear=True)
    def test_a_simulation_that_keeps_failing_ends_the_match_with_game_over(self):
        state = {'character_links_sent': True, 'clock_epoch': 100.0,
                 'clock_last_sent': 100.0, 'seq': 1, 'received_seq': 1,
                 'received_window': 1, 'practice_npcs_sent': True}
        real = shard.solo_simulation_messages

        def broken(state, now, previous_id=0):
            if 'match_result' in state:
                return real(state, now, previous_id=previous_id)
            raise ValueError('bad entry')
        with patch.object(shard, 'log'), \
                patch.object(shard, 'solo_simulation_messages', side_effect=broken):
            # 60 ms steps, so every call is a tick. The first failure is at
            # 0.06 s; 2 s later (k = 35, 2.10 s) the match ends.
            for k in range(1, 35):
                shard.game_clock_reply(state, now=100.0 + .06 * k)
            self.assertNotIn('match_result', state)
            shard.game_clock_reply(state, now=100.0 + .06 * 35)
            self.assertEqual(state['match_result']['winner_team'], 0)
            packet = shard.game_clock_reply(state, now=100.0 + .06 * 36)
        raw = lz4.block.decompress(packet[1:], uncompressed_size=4096)
        game_over = shard.build_solo_match_phase_update(
            shard.solo_modes.selected(), shard.solo_modes.MATCH_PHASE['GameOver'])
        self.assertIn(game_over.payload, raw)

    @patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character',
                               'CRUCIBLE_SOLO_MODE': 'alpha-hunters'}, clear=True)
    def test_intermittent_failures_also_end_the_match(self):
        # The pattern a bug that fails only some ticks produces (the DIT
        # deque bug failed 25-59% of ticks): every other tick.
        state = {'character_links_sent': True, 'clock_epoch': 100.0,
                 'clock_last_sent': 100.0, 'seq': 1, 'received_seq': 1,
                 'received_window': 1, 'practice_npcs_sent': True}
        outcomes = iter([ValueError('x'), []] * 40)

        def flaky(state, now, previous_id=0):
            if 'match_result' in state:
                return []
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        with patch.object(shard, 'log'), \
                patch.object(shard, 'solo_simulation_messages', side_effect=flaky):
            for k in range(1, 70):
                shard.game_clock_reply(state, now=100.0 + .06 * k)
        self.assertEqual(state['match_result']['reason'], 'simulation error')

    @patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character'})
    def test_a_rare_failure_does_not_end_the_match(self):
        state = {'character_links_sent': True, 'clock_epoch': 100.0,
                 'clock_last_sent': 100.0, 'seq': 1, 'received_seq': 1,
                 'received_window': 1, 'practice_npcs_sent': True}
        outcomes = iter(([ValueError('x')] + [[]] * 9) * 10)    # one tick in ten

        def flaky(state, now, previous_id=0):
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        with patch.object(shard, 'log'), \
                patch.object(shard, 'solo_simulation_messages', side_effect=flaky):
            for k in range(1, 101):                # 6 s at 10% failing ticks
                shard.game_clock_reply(state, now=100.0 + .06 * k)
        self.assertNotIn('match_result', state)


def message(counter, previous, tag, local=False):
    return shard.build_entity_message(counter, shard.pack_type_and_flags(1),
                                      payload=tag, previous_id=previous, local=local)


def entities(packet_messages):
    """(counter, local, payload) per message, decoding RelativeIds from 0."""
    previous, out = 0, []
    for m in packet_messages:
        raw, _ = shard.decode_varint(m.header, 0)
        relative = (raw >> 1) ^ -(raw & 1)
        counter = (relative >> 1) + previous
        out.append((counter, bool(relative & 1), m.payload))
        previous = counter
    return out


class SplitTests(unittest.TestCase):
    def test_each_entity_appears_once_per_packet_in_order(self):
        tick = [message(2, 0, b'ring'), message(3, 2, b'loc3'), message(4, 3, b'loc4'),
                message(3, 4, b'anim3'), message(4, 3, b'anim4'), message(3, 4, b'fire3'),
                message(2, 3, b'health2')]
        groups = shard.split_entity_messages(tick)
        self.assertEqual([entities(g) for g in groups], [
            [(2, False, b'ring'), (3, False, b'loc3'), (4, False, b'loc4')],
            [(3, False, b'anim3'), (4, False, b'anim4'), (2, False, b'health2')],
            [(3, False, b'fire3')]])
        # Nothing but the RelativeId changes in each header.
        self.assertEqual(groups[1][2].header[1:], tick[6].header[1:])

    def test_a_local_entity_is_not_the_network_entity_with_its_counter(self):
        tick = [message(0x4b, 0, b'net'), message(0x4b, 0x4b, b'local', local=True)]
        self.assertEqual([entities(g) for g in shard.split_entity_messages(tick)],
                         [[(0x4b, False, b'net'), (0x4b, True, b'local')]])

    def test_the_clock_sends_one_packet_per_group_only_with_the_switch(self):
        tick = [message(3, 0, b'loc3'), message(3, 3, b'anim3')]
        for split, count in (('0', 1), ('1', 2)):
            env = {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character',
                   'CRUCIBLE_SHARD_SPLIT_UPDATES': split}
            state = {'character_links_sent': True, 'clock_epoch': 100.0,
                     'clock_last_sent': 100.0, 'seq': 7, 'received_seq': 1,
                     'received_window': 1, 'practice_npcs_sent': True}
            with self.subTest(split=split), patch.dict('os.environ', env, clear=True), \
                    patch.object(shard, 'solo_simulation_messages', return_value=tick):
                packets = shard.game_clock_packets(state, now=101.0)
                self.assertEqual(len(packets), count)
                sequences = [int.from_bytes(lz4.block.decompress(
                    p[1:], uncompressed_size=4096)[2:4], 'big') for p in packets]
                self.assertEqual(sequences, list(range(8, 8 + count)))
                self.assertEqual(state['seq'], 7 + count)



class SequenceLimitTests(unittest.TestCase):
    def state(self, seq):
        return {'character_links_sent': True, 'clock_epoch': 100.0,
                'clock_last_sent': 100.0, 'seq': seq, 'received_seq': 1,
                'received_window': 1, 'practice_npcs_sent': True}

    def test_the_match_ends_before_the_sequence_wraps(self):
        env = {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character',
               'CRUCIBLE_SOLO_MODE': 'alpha-hunters'}
        with patch.dict('os.environ', env, clear=True), patch.object(shard, 'log'):
            self.assertEqual(shard.sequence_limit(), 64000)
            state = self.state(63998)
            shard.game_clock_packets(state, now=101.0)          # sends 63999
            self.assertNotIn('match_result', state)
            shard.game_clock_packets(state, now=102.0)          # sends 64000
            self.assertEqual(state['match_result']['reason'], 'sequence limit')
            raw = lz4.block.decompress(shard.game_clock_packets(state, now=103.0)[0][1:],
                                       uncompressed_size=4096)
        game_over = shard.build_solo_match_phase_update(
            shard.solo_modes.selected(), shard.solo_modes.MATCH_PHASE['GameOver'])
        self.assertIn(game_over.payload, raw)

    def test_the_limit_can_be_moved_or_disabled(self):
        for value, limit in (('0', 0), ('1000', 1000), ('99999', 65000), ('x', 64000)):
            with self.subTest(value=value), \
                    patch.dict('os.environ', {'CRUCIBLE_SHARD_SEQUENCE_LIMIT': value}):
                self.assertEqual(shard.sequence_limit(), limit)
        env = {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character',
               'CRUCIBLE_SHARD_SEQUENCE_LIMIT': '0'}
        with patch.dict('os.environ', env, clear=True), patch.object(shard, 'log'):
            state = self.state(65000)
            shard.game_clock_packets(state, now=101.0)
            self.assertNotIn('match_result', state)


class MatchTimerTests(unittest.TestCase):
    def state(self):
        return {'character_links_sent': True, 'clock_epoch': 100.0,
                'clock_last_sent': 100.0, 'seq': 10, 'received_seq': 1,
                'received_window': 1, 'practice_npcs_sent': True}

    def test_the_timer_ends_the_match_after_that_much_play(self):
        env = {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character',
               'CRUCIBLE_SOLO_MODE': 'hoth-guide-april2018',
               'CRUCIBLE_SOLO_MATCH_MAX_SECONDS': '5'}
        with patch.dict('os.environ', env, clear=True), patch.object(shard, 'log'):
            state = self.state()
            shard.game_clock_packets(state, now=101.0)          # play starts at 1 s
            self.assertEqual(state['match_started_at'], 1.)
            shard.game_clock_packets(state, now=105.9)
            self.assertNotIn('match_result', state)
            shard.game_clock_packets(state, now=106.0)
            self.assertEqual(state['match_result'],
                             {'winner_team': 0, 'at_seconds': 6., 'reason': 'match timer'})
            raw = lz4.block.decompress(shard.game_clock_packets(state, now=107.0)[0][1:],
                                       uncompressed_size=4096)
        game_over = shard.build_solo_match_phase_update(
            shard.solo_modes.selected(), shard.solo_modes.MATCH_PHASE['GameOver'])
        self.assertIn(game_over.payload, raw)

    def test_no_timer_unless_a_positive_number_is_set(self):
        for value, seconds in ((None, None), ('', None), ('0', None), ('-3', None),
                               ('inf', None), ('nan', None), ('x', None), ('90', 90.)):
            env = {} if value is None else {'CRUCIBLE_SOLO_MATCH_MAX_SECONDS': value}
            with self.subTest(value=value), patch.dict('os.environ', env, clear=True):
                self.assertEqual(shard.match_max_seconds(), seconds)
        env = {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character'}
        with patch.dict('os.environ', env, clear=True), patch.object(shard, 'log'):
            state = self.state()
            shard.game_clock_packets(state, now=5000.0)
        self.assertNotIn('match_result', state)
        self.assertNotIn('match_started_at', state)


if __name__ == '__main__':
    unittest.main()
