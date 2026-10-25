"""Damage-number and incoming-hit ring encoders and their slot policy."""
import json
from pathlib import Path
import struct
import unittest

import feedback_rings as rings
import hero_loadout
import shard_stub as shard

SCHEMA = (Path(__file__).resolve().parents[1] / 'snapshots' / 'gamemode-20260919' /
          'component-replication-schema.json')


class LayoutTests(unittest.TestCase):
    def test_subtree_sizes_match_the_client_schema(self):
        bits = {c['type_id']: c['expanded_record_bits'][2]
                for c in json.loads(SCHEMA.read_text())['components']}
        self.assertEqual(len(rings.floater_bits({1})), bits[rings.DAMAGE_FLOATER])
        self.assertEqual(len(rings.indicator_bits({1})), bits[rings.HIT_INDICATOR])
        self.assertTrue(all(bits[t] > 0 for t in rings.AUTONOMOUS_TYPES))

    def test_bits_select_the_written_slots_and_the_index(self):
        self.assertEqual(rings.floater_bits({1}),
                         [True, True, False, True] + [False] * 30 + [False, True])
        self.assertEqual(rings.indicator_bits({15}),
                         [True, True, True] + [False] * 15 + [True])
        bits = rings.autonomous_bits({36: rings.floater_bits({1})})
        self.assertEqual(len(bits), 24 + 36)
        self.assertEqual(bits[:7], [False] * 7)        # roots 6..27 precede 36
        with self.assertRaises(ValueError):
            rings.floater_bits({32})
        with self.assertRaises(ValueError):
            rings.indicator_bits(set())

    def test_floater_update_literal_bytes(self):
        message = rings.build_floater_update({1: (0x01000003, 40., 0)}, 1)
        shared = shard.build_replication_record([False] * len(hero_loadout.SHARED_TYPES))
        autonomous = shard.build_replication_record(
            rings.autonomous_bits({36: rings.floater_bits({1})}))
        payload = shared + autonomous + bytes.fromhex('01000003 42200000 00') + b'\x01'
        # The player's own character, as ClientAutonomous (role 2).
        self.assertEqual(message, shard.build_entity_message(
            2, shard.pack_type_and_flags(shard.ENTITY_ROLE_CLIENT_AUTONOMOUS), payload=payload))

    def test_indicator_update_puts_the_index_before_the_entry(self):
        message = rings.build_indicator_update({1: (20., (1., 2., 3.))}, 1)
        values = message.payload[-(4 + 36):]
        self.assertEqual(values[:4], b'\0\0\0\x01')
        self.assertEqual(values[4:], struct.pack('>ffHH3f3f', 20., 1., 2, 1,
                                                 1., 2., 3., 1., 2., 3.))

    def test_both_rings_share_one_player_message_in_type_order(self):
        message = rings.build_ring_update(
            floaters=({1: (0x01000003, 40., 1)}, 1),
            indicators=({1: (20., (1., 2., 3.))}, 1))
        bits = rings.autonomous_bits({36: rings.floater_bits({1}),
                                      81: rings.indicator_bits({1})})
        self.assertEqual(len(bits), 23 + 36 + 19)
        values = (bytes.fromhex('01000003 42200000 01 01') + b'\0\0\0\x01'
                  + struct.pack('>ffHH3f3f', 20., 1., 2, 1, 1., 2., 3., 1., 2., 3.))
        self.assertTrue(message.payload.endswith(shard.build_replication_record(bits) + values))

    def test_the_index_must_name_a_written_slot(self):
        with self.assertRaises(ValueError):
            rings.build_floater_update({2: (0x01000003, 40., 0)}, 1)
        with self.assertRaises(ValueError):
            rings.build_indicator_update({2: (20., (0., 0., 0.))}, 3)


class RingPolicyTests(unittest.TestCase):
    def test_first_entry_is_slot_one_and_the_index_never_reaches_the_size(self):
        ring = rings.FloaterRing()
        seen = []
        for hit in range(70):
            ring.hit(0x01000003, 40., hit * 1.)
            seen.append(ring.last_index)
            self.assertEqual(ring.take(hit * 1.)[1], ring.last_index)
        self.assertEqual(seen[0], 1)
        self.assertEqual(seen[31], 0)
        self.assertEqual(max(seen), 31)
        indicators = rings.IndicatorRing()
        for hit in range(40):
            indicators.hit(20., (0., 0., 0.), hit * 1.)
            indicators.take(hit * 1.)
            self.assertLess(indicators.last_index, 16)

    def test_damage_numbers_accumulate_within_the_window(self):
        ring = rings.FloaterRing()
        for now in (0., .1, .2):
            ring.hit(0x01000003, 40., now)
        ring.hit(0x01000004, 40., .2)
        entries, last = ring.take(.2)
        self.assertEqual(last, 4)
        self.assertEqual(sorted(entry[:2] for entry in entries.values()),
                         [(0x01000003, 40.), (0x01000003, 80.),
                          (0x01000003, 120.), (0x01000004, 40.)])
        ring.hit(0x01000003, 40., .2 + rings.FLOATER_ACCUMULATION_SECONDS)
        self.assertEqual(ring.take(.5)[0][5][1], 40.)

    def test_recent_entries_are_resent_for_half_a_second_then_dropped(self):
        ring = rings.IndicatorRing()
        ring.hit(20., (1., 2., 3.), 10.)
        first = ring.take(10.)
        self.assertEqual(ring.take(10.4), first)
        self.assertIsNone(ring.take(10.6))
        for k in range(12):
            ring.hit(5., (0., 0., 0.), 11. + k * .01)
        self.assertEqual(len(ring.take(11.2)[0]), 12)     # each sent once
        entries, last = ring.take(11.3)                   # then re-sends only
        self.assertEqual(len(entries), rings.RESEND_ENTRIES)
        self.assertIn(last, entries)
        self.assertEqual(sorted(entries), sorted((last - k) % 16 for k in range(8)))


from unittest.mock import patch

import hunter_ai
import hunter_hits

ALPHA = {'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_MODE': 'alpha-hunters',
         'CRUCIBLE_SOLO_HERO': 'saboteur'}
BOTS = (0x1000003, 0x1000004, 0x1000005)


def squad_at(*positions):
    route = ((0., 0., 0.), (1., 0., 0.))
    return hunter_ai.Squad([hunter_ai.Bot(e, route, hunter_ai.MARINE, position=p)
                            for e, p in zip(BOTS, positions)])


def player_hit(projectile, target):
    return shard.EntityRpc(3, shard.PLAYER_CHARACTER_ID, 115, 4,
                           bytes([projectile, 1]) + target.to_bytes(4, 'big'))


@patch.object(shard, 'log')
class ShardRingTests(unittest.TestCase):
    def start(self):
        state = {}
        shard.start_solo_simulation(state)
        return state

    def test_switches_off_add_no_ring_message(self, _log):
        with patch.dict('os.environ', ALPHA, clear=True):
            state = self.start()
            shard.observe_solo_combat(player_hit(1, 0x1000003), state)
            self.assertNotIn('floater_ring', state)
            self.assertIsNone(shard.feedback_ring_message(state, 1.))

    def test_a_player_hit_leads_the_next_tick_with_its_damage_number(self, _log):
        env = dict(ALPHA, CRUCIBLE_SOLO_DAMAGE_NUMBERS='1', CRUCIBLE_SHARD_SPLIT_UPDATES='1')
        with patch.dict('os.environ', env, clear=True):
            state = self.start()
            state['clock_epoch'] = 9.     # session time 1.0 s
            with patch.object(shard.time, 'monotonic', return_value=10.):
                shard.observe_solo_combat(player_hit(1, 0x1000003), state)
                shard.observe_solo_combat(player_hit(2, 0x1000003), state)
            messages = shard.solo_simulation_messages(state, 1.)
            self.assertEqual(messages[0], rings.build_floater_update(
                {1: (0x1000003, 40., 0), 2: (0x1000003, 80., 0)}, 2))
            # Re-sent for half a second against a lost update, then dropped.
            self.assertEqual(shard.solo_simulation_messages(state, 1.4)[0], messages[0])
            self.assertNotIn(messages[0], shard.solo_simulation_messages(state, 1.6))

    def test_a_hunter_round_on_the_player_draws_an_arc_from_the_shooter(self, _log):
        env = dict(ALPHA, CRUCIBLE_SOLO_HIT_DIRECTION='1', CRUCIBLE_SHARD_SPLIT_UPDATES='1')
        with patch.dict('os.environ', env, clear=True):
            state = self.start()
            state['squad'] = squad_at((5., 6., 7.), (0., 0., 0.), (0., 0., 0.))
            state['hunter_landings'] = [hunter_hits.Landing(0x1000003, shard.PLAYER_CHARACTER_ID,
                                                            .5, 20.)]
            with patch.object(shard, 'build_hunter_ai_updates', return_value=([], [])):
                messages = shard.solo_simulation_messages(state, 1.)
            self.assertEqual(messages[0], rings.build_indicator_update(
                {1: (20., (5., 6., 7.))}, 1))

    def test_rings_stay_off_without_the_split(self, _log):
        env = dict(ALPHA, CRUCIBLE_SOLO_DAMAGE_NUMBERS='1')
        with patch.dict('os.environ', env, clear=True):
            state = self.start()
            shard.observe_solo_combat(player_hit(1, 0x1000003), state)
            self.assertIn('floater_ring', state)
            self.assertIsNone(shard.feedback_ring_message(state, 0.))
            self.assertTrue(state['rings_need_split_logged'])

    def test_terrain_sight_blocks_server_resolved_rounds(self, _log):
        env = dict(ALPHA, CRUCIBLE_SOLO_BOT_DAMAGE='server', CRUCIBLE_SOLO_FIRE='1')
        shot = hunter_ai.Shot(0x1000003, shard.PLAYER_CHARACTER_ID, 1., (0., 0., 1.),
                              (0., 1., 0.), 10.)
        step = hunter_ai.Step(0x1000003, (0., 0., 0.), (0., 1., 0.), 0., 0.,
                              hunter_ai.ATTACK, target=shard.PLAYER_CHARACTER_ID,
                              shots=(shot,), firing=True)
        for clear, landed in ((True, 1), (False, 0)):
            with self.subTest(clear=clear), patch.dict('os.environ', env, clear=True):
                state = self.start()
                state['squad'] = squad_at((0., 0., 0.), (0., 0., 0.), (0., 0., 0.))
                state['sight'] = lambda eye, target: clear
                state['hunter_gunnery'] = hunter_hits.HunterGunnery(seed=1, accuracy_scale=1.)
                with patch.object(shard, 'build_hunter_ai_updates',
                                  return_value=([], [step])):
                    shard.solo_simulation_messages(state, 1.)
                self.assertEqual(len(state.get('hunter_landings', [])), landed)
                self.assertEqual(state.get('hunter_rounds_blocked', 0), 1 - landed)

    def test_terrain_sight_is_given_to_every_bot(self, _log):
        import hunter_patrol
        squad = squad_at((0., 0., 0.), (0., 0., 0.), (0., 0., 0.))
        env = dict(ALPHA, CRUCIBLE_SOLO_AI='1', CRUCIBLE_SOLO_LOS='terrain')
        with patch.dict('os.environ', env, clear=True), \
                patch.object(hunter_patrol, 'squad', return_value=squad), \
                patch.object(shard, 'level_sight', return_value=lambda e, t: True):
            state = self.start()
        self.assertTrue(all(bot.visible is state['sight'] for bot in squad.bots))



class UnsentEntryTests(unittest.TestCase):
    def test_a_burst_larger_than_the_resend_cap_is_sent_whole_once(self):
        ring = rings.FloaterRing()
        for k in range(12):
            ring.hit(0x01000003 + k % 3, 10., 5.)
        entries, last = ring.take(5.)
        self.assertEqual(len(entries), 12)
        self.assertEqual(last, 12)
        # Afterwards only the re-send window remains.
        self.assertEqual(len(ring.take(5.1)[0]), rings.RESEND_ENTRIES)


@patch.object(shard, 'log')
class FloaterSettingTests(unittest.TestCase):
    def test_floaters_enabled_bits_and_values(self, _log):
        self.assertEqual(rings.floater_bits(set(), enabled=False), [True, False, True, False])
        self.assertEqual(rings.floater_bits({1}, enabled=True)[-2:], [True, True])
        alone = rings.build_ring_update(floaters_enabled=False)
        self.assertTrue(alone.payload.endswith(
            shard.build_replication_record(rings.autonomous_bits(
                {36: [True, False, True, False]})) + b'\x00'))
        both = rings.build_ring_update(floaters=({1: (0x01000003, 40., 0)}, 1),
                                       floaters_enabled=True)
        self.assertTrue(both.payload.endswith(bytes.fromhex('01000003 42200000 00 01 01')))
        with self.assertRaises(ValueError):
            rings.floater_bits(set())

    def test_the_client_request_pauses_floaters_and_is_echoed(self, _log):
        env = dict(ALPHA, CRUCIBLE_SOLO_DAMAGE_NUMBERS='1', CRUCIBLE_SHARD_SPLIT_UPDATES='1')
        with patch.dict('os.environ', env, clear=True):
            state = {}
            shard.start_solo_simulation(state)
            off = shard.EntityRpc(3, shard.PLAYER_CHARACTER_ID, 36, 0, b'\x00')
            shard.observe_floater_setting(off, state)
            self.assertFalse(state['floaters_enabled'])
            shard.observe_solo_combat(player_hit(1, 0x1000003), state)
            self.assertNotIn('floater_ring', state)
            message = shard.feedback_ring_message(state, 1.)(0)
            self.assertEqual(message, rings.build_ring_update(floaters_enabled=False))
            for _ in range(shard.FLOATER_SETTING_RESEND_TICKS - 1):
                self.assertIsNotNone(shard.feedback_ring_message(state, 1.))
            self.assertIsNone(shard.feedback_ring_message(state, 1.))
            # Turned back on, hits feed the ring again.
            on = shard.EntityRpc(3, shard.PLAYER_CHARACTER_ID, 36, 0, b'\x01')
            shard.observe_floater_setting(on, state)
            self.assertTrue(state['floaters_enabled'])
            shard.observe_solo_combat(player_hit(2, 0x1000003), state)
            self.assertIn('floater_ring', state)
            # The echo goes alone; the number waits for it to finish.
            for _ in range(shard.FLOATER_SETTING_RESEND_TICKS):
                self.assertEqual(shard.feedback_ring_message(state, 1.)(0),
                                 rings.build_ring_update(floaters_enabled=True))
            entries, last = state['floater_ring'].take(1.)
            self.assertEqual(len(entries), 1)

    def test_a_client_packet_reaches_the_setting(self, _log):
        def packet(sequence, enabled):
            payload = (b'\0\1' + struct.pack('>BIBBH', 3, shard.PLAYER_CHARACTER_ID, 36, 0, 1)
                       + bytes([enabled]))
            return (bytes.fromhex('000b') + struct.pack('>H', sequence) + bytes(6)
                    + b'\1' + struct.pack('>H', sequence) + payload)
        with patch.dict('os.environ', {'CRUCIBLE_SOLO_DAMAGE_NUMBERS': '1'}, clear=True):
            state = {'bootstrap_sent': True}
            shard.observe_client_rpcs(packet(1, 0), state)
            self.assertEqual((state['floaters_enabled'], state['floaters_enabled_resend']),
                             (False, shard.FLOATER_SETTING_RESEND_TICKS))
            # The next tick's request, in a new packet, re-arms the echo.
            state['floaters_enabled_resend'] = 1
            shard.observe_client_rpcs(packet(2, 0), state)
            self.assertEqual(state['floaters_enabled_resend'],
                             shard.FLOATER_SETTING_RESEND_TICKS)

    def test_other_rpcs_are_not_the_setting(self, _log):
        state = {}
        with patch.dict('os.environ', {'CRUCIBLE_SOLO_DAMAGE_NUMBERS': '1'}):
            for rpc in (shard.EntityRpc(3, shard.PLAYER_CONNECTION_ID, 36, 0, b'\x00'),
                        shard.EntityRpc(3, shard.PLAYER_CHARACTER_ID, 36, 1, b'\x00'),
                        shard.EntityRpc(3, shard.PLAYER_CHARACTER_ID, 36, 0, b'\x00\x00')):
                shard.observe_floater_setting(rpc, state)
        self.assertEqual(state, {})

    def test_the_setting_is_left_alone_without_damage_numbers(self, _log):
        state = {}
        with patch.dict('os.environ', {}, clear=True):
            shard.observe_floater_setting(
                shard.EntityRpc(3, shard.PLAYER_CHARACTER_ID, 36, 0, b'\x00'), state)
        self.assertEqual(state, {})


if __name__ == '__main__':
    unittest.main()
