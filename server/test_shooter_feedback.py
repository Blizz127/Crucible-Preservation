"""ConfirmHitIndicator / ConfirmKillIndicator encoders and their shard wiring."""
import unittest
from unittest.mock import patch

import lz4.block

import shard_stub as shard
import shooter_feedback as feedback
from combat import MARINE_PRIMARY, SABOTEUR_PRIMARY


def player_hit(projectile, target):
    return shard.EntityRpc(3, shard.PLAYER_CHARACTER_ID, 115, 4,
                           bytes([projectile, 1]) + target.to_bytes(4, 'big'))


def decode_packet(packet):
    """BasePacketType byte, then the LZ4 block of header + payload + CRC."""
    region = lz4.block.decompress(packet[1:], uncompressed_size=4096)[:-4]
    packet_type = int.from_bytes(region[:2], 'big')
    return packet_type, int.from_bytes(region[2:4], 'big'), region[11:]


ALPHA = {'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_MODE': 'alpha-hunters',
         'CRUCIBLE_SOLO_HERO': 'saboteur'}


class EncoderTests(unittest.TestCase):
    def test_hit_indicator_literal_bytes(self):
        self.assertEqual(feedback.build_confirm_hit_indicator(
            scale=1.0, projectile_type_index=6),
            bytes.fromhex('3f800000 02 01 80000006'))

    def test_crit_without_direct_damage_and_negative_index(self):
        self.assertEqual(feedback.build_confirm_hit_indicator(
            scale=0.5, projectile_type_index=-1, is_crit=True,
            direct_damage=False, damage_type=feedback.DAMAGE_EXPLOSION),
            bytes.fromhex('3f000000 01 02 7fffffff'))

    def test_kill_indicator_defaults_to_no_entity(self):
        self.assertEqual(feedback.build_confirm_kill_indicator(), b'\xff')
        self.assertEqual(feedback.build_confirm_kill_indicator(3), b'\x03')

    def test_invalid_values_are_refused(self):
        for changes in ({'scale': float('nan')}, {'scale': -1.0},
                        {'damage_type': 5}, {'is_crit': 1},
                        {'projectile_type_index': 2**31}):
            arguments = dict(scale=1.0, projectile_type_index=4)
            arguments.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                feedback.build_confirm_hit_indicator(**arguments)
        with self.assertRaises(ValueError):
            feedback.build_confirm_kill_indicator(128)

    def test_scale_is_the_damage_fraction_of_the_close_range_maximum(self):
        self.assertEqual(feedback.hit_scale(40.0, SABOTEUR_PRIMARY), 1.0)
        self.assertEqual(feedback.hit_scale(20.0, SABOTEUR_PRIMARY), 0.5)
        self.assertEqual(feedback.hit_scale(15.0, MARINE_PRIMARY), 0.75)


class EntityRpcsTests(unittest.TestCase):
    def test_built_payload_parses_back(self):
        messages = [shard.EntityRpc(2, shard.PLAYER_CHARACTER_ID, 115, 2, b'\1\2'),
                    shard.EntityRpc(2, shard.PLAYER_CHARACTER_ID, 115, 3, b'\xff')]
        payload = shard.build_entity_rpcs(messages)
        self.assertEqual(payload, bytes.fromhex(
            '0002 02 01000002 73 02 0002 0102 02 01000002 73 03 0001 ff'))
        self.assertEqual(shard.parse_entity_rpcs(payload), messages)

    def test_rpc_reply_sends_queued_rpcs_once_with_the_next_sequence(self):
        state = {'seq': 41, 'received_seq': 7, 'received_window': 1}
        self.assertIsNone(shard.rpc_reply(state))
        shard.queue_player_rpc(state, 115, 3, b'\xff')
        packet_type, sequence, payload = decode_packet(shard.rpc_reply(state))
        self.assertEqual((packet_type, sequence), (11, 42))
        self.assertEqual(shard.parse_entity_rpcs(payload),
                         [shard.EntityRpc(2, shard.PLAYER_CHARACTER_ID, 115, 3, b'\xff')])
        self.assertIsNone(shard.rpc_reply(state))
        self.assertEqual(state['seq'], 42)


@patch.object(shard, 'log')
class ShardWiringTests(unittest.TestCase):
    def start(self):
        state = {}
        shard.start_solo_simulation(state)
        return state

    @patch.dict('os.environ', ALPHA, clear=True)
    def test_flags_off_queue_nothing(self, _log):
        state = self.start()
        shard.observe_solo_combat(player_hit(1, 0x1000003), state)
        self.assertEqual(state['combat'].health(0x1000003), 760.0)
        self.assertNotIn('pending_rpcs', state)

    @patch.dict('os.environ', dict(ALPHA, CRUCIBLE_SOLO_HIT_CONFIRM='1',
                                   CRUCIBLE_SOLO_KILL_CONFIRM='1'), clear=True)
    def test_each_accepted_hit_confirms_and_the_kill_adds_a_kill_marker(self, _log):
        state = self.start()
        for projectile in range(1, 21):
            shard.observe_solo_combat(player_hit(projectile, 0x1000003), state)
        self.assertFalse(state['combat'].alive(0x1000003))
        rpcs = state['pending_rpcs']
        hit = feedback.build_confirm_hit_indicator(scale=1.0, projectile_type_index=6)
        self.assertEqual([(r.delivery, r.entity_id, r.component, r.rpc, r.data)
                          for r in rpcs],
                         [(2, shard.PLAYER_CHARACTER_ID, 115, 2, hit)] * 20
                         + [(2, shard.PLAYER_CHARACTER_ID, 115, 3, b'\xff')])

    def test_forced_aggro_flag_hands_the_hit_to_the_bot(self, _log):
        import hunter_ai
        route = ((0., 0., 0.), (1., 0., 0.))
        # Without a session clock the shard's time is monotonic: 10 s + 6 s.
        for flag, expected in (('0', None), ('1', 16.)):
            squad = hunter_ai.Squad([hunter_ai.Bot(e, route, hunter_ai.MARINE)
                                     for e in (0x1000003, 0x1000004, 0x1000005)])
            env = dict(ALPHA, CRUCIBLE_SOLO_FORCED_AGGRO=flag)
            with self.subTest(flag=flag), patch.dict('os.environ', env, clear=True):
                state = self.start()
                state['squad'] = squad
                with patch.object(shard.time, 'monotonic', return_value=10.):
                    shard.observe_solo_combat(player_hit(1, 0x1000004), state)
                hit, other, _ = squad.bots[1], squad.bots[0], squad.bots[2]
                self.assertEqual(hit.forced_until, expected)
                self.assertEqual(hit.target, None if expected is None
                                 else shard.PLAYER_CHARACTER_ID)
                self.assertIsNone(other.forced_until)

    def test_range_switch_feeds_the_falloff_and_the_marker_scale(self, _log):
        import hunter_ai
        from combat import immediate_damage
        route = ((0., 0., 0.), (1., 0., 0.))
        for flag, damage in (('0', 40.), ('1', immediate_damage(SABOTEUR_PRIMARY, distance=100.))):
            squad = hunter_ai.Squad([hunter_ai.Bot(e, route, hunter_ai.MARINE,
                                                   position=(0., 100. * k, 0.))
                                     for k, e in enumerate((0x1000003, 0x1000004, 0x1000005))])
            env = dict(ALPHA, CRUCIBLE_SOLO_HIT_RANGE=flag, CRUCIBLE_SOLO_HIT_CONFIRM='1')
            with self.subTest(flag=flag), patch.dict('os.environ', env, clear=True):
                state = self.start()
                state['squad'], state['player_position'] = squad, (0., 0., 0.)
                shard.observe_solo_combat(player_hit(1, 0x1000004), state)
                self.assertAlmostEqual(state['combat'].health(0x1000004), 800. - damage)
                scale = feedback.hit_scale(damage, SABOTEUR_PRIMARY)
                self.assertEqual(state['pending_rpcs'][0].data,
                                 feedback.build_confirm_hit_indicator(
                                     scale=scale, projectile_type_index=6))
        # 100 m is inside Ajonah's 60..119 m falloff: about 26.5 damage.
        self.assertAlmostEqual(damage, 26.5, places=1)

    @patch.dict('os.environ', dict(ALPHA, CRUCIBLE_SOLO_HIT_CONFIRM='1'), clear=True)
    def test_refused_reports_and_kills_without_the_kill_flag(self, _log):
        state = self.start()
        shard.observe_solo_combat(player_hit(1, shard.PLAYER_CHARACTER_ID), state)
        self.assertNotIn('pending_rpcs', state)
        for projectile in range(2, 22):
            shard.observe_solo_combat(player_hit(projectile, 0x1000004), state)
        self.assertEqual({r.rpc for r in state['pending_rpcs']}, {2})


if __name__ == '__main__':
    unittest.main()
