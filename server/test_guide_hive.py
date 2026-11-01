"""HOTH_GUIDE's hive behind CRUCIBLE_SOLO_GUIDE_HIVE=1 (docs/37)."""
import os
import unittest
from unittest.mock import patch

import combat
import death_events
import shard_stub as shard
import solo_modes

HIVE = shard.HIVE_ENTITY_ID
ENV = {'CRUCIBLE_SOLO_MODE': 'hoth-guide-april2018', 'CRUCIBLE_SOLO_GUIDE_HIVE': '1',
       'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_AI': '0', 'CRUCIBLE_SOLO_HERO': 'saboteur'}
SPOT = (1250.0, 1300.0, 30.0)


def rpc(entity, component, rpc_id, data, delivery=3):
    return shard.EntityRpc(delivery, entity, component, rpc_id, data)


def hit_on(projectile, target):
    return rpc(shard.PLAYER_CHARACTER_ID, 115, 4,
               bytes([projectile & 0xff, 1]) + target.to_bytes(4, 'big'))


class Mesh:
    """Flat ground at z = 30 over x >= 1200; nothing west of it."""
    def locate(self, point, tolerance=2.):
        if point[0] < 1200.:
            return None
        return 'tile', (point[0], point[1], 30.)


@patch.object(shard, 'log')
class GuideHiveTests(unittest.TestCase):
    def start(self, env=ENV):
        state = {'guide_hive': {'position': SPOT, 'next_heal': None, 'regens': 0}}
        with patch.dict('os.environ', env, clear=True):
            shard.start_solo_simulation(state)
        return state

    def test_the_slice_hash_is_the_level_librarys(self, _log):
        # levels/april2018/commondata/stringhashlibrary.xml: Key="3885309295"
        # Value="slices/creatures/hive/hive".
        self.assertEqual(solo_modes.level_name_hash('slices/creatures/hive/hive'),
                         shard.HIVE_SLICE_HASH)

    def test_the_spawn_message(self, _log):
        message = shard.build_guide_hive_probe(SPOT, previous_id=3)
        counter, offset = shard.decode_entity_id(message.header, previous_id=3)
        self.assertEqual(counter, 6)
        self.assertEqual(message.header[offset], 0x11)
        self.assertEqual(int.from_bytes(message.header[offset+1:offset+5], 'big'),
                         shard.HIVE_SLICE_HASH)
        self.assertEqual(message.payload,
                         shard.build_replication_record(shard.location_bits(shard.HIVE_SHARED_TYPES))
                         + shard.pack_practice_spawn_transform(SPOT))

    def test_only_a_hoth_guide_profile_with_the_switch(self, _log):
        for env, on in ((ENV, True), (dict(ENV, CRUCIBLE_SOLO_GUIDE_HIVE='0'), False),
                        (dict(ENV, CRUCIBLE_SOLO_MODE='alpha-hunters'), False)):
            with self.subTest(env=env), patch.dict('os.environ', env, clear=True):
                self.assertEqual(shard.guide_hive_enabled(), on)

    def test_its_spot_is_off_the_hunters_side_and_on_the_mesh(self, _log):
        import navigation
        with patch.dict('os.environ', ENV, clear=True), \
                patch.object(navigation, 'installed_mesh', return_value=Mesh()):
            profile = solo_modes.selected()
            spot = shard.guide_hive_position(profile)
        drop, bot = profile.player_position, profile.bot_positions[0]
        self.assertAlmostEqual(math_dist2(spot, drop), shard.HIVE_DROP_DISTANCE, places=6)
        self.assertEqual(spot[2], 30.)
        # Farther from the first bot point than the drop is, when the mesh allows.
        if spot[0] >= 1200.:
            self.assertGreater(math_dist2(spot, bot), math_dist2(drop, bot) - 1e-6)

    def test_it_is_registered_as_its_own_entity(self, _log):
        state = self.start()
        arena = state['combat']
        entity = arena.entities[HIVE]
        self.assertEqual((entity.shared_types, entity.maximum, entity.team),
                         (shard.HIVE_SHARED_TYPES, 5000., shard.HIVE_TEAM))

    def test_every_message_from_hit_to_death_uses_the_hives_types(self, _log):
        env = dict(ENV, CRUCIBLE_SOLO_DEATH_EVENTS='authored', CRUCIBLE_SOLO_HIT_REACTION='1',
                   CRUCIBLE_SOLO_COMBAT_ANIMATION='1', CRUCIBLE_SOLO_HIT_RANGE='1',
                   CRUCIBLE_SOLO_HIT_CONFIRM='1', CRUCIBLE_SOLO_KILL_CONFIRM='1')
        state = self.start(env)
        state['player_position'] = (SPOT[0] - 10., SPOT[1], SPOT[2])
        built = []

        def record(original):
            def wrapper(entity_id, shared_types, *args, **kwargs):
                built.append((entity_id, tuple(shared_types)))
                return original(entity_id, shared_types, *args, **kwargs)
            return wrapper
        import animation_events
        with patch.dict('os.environ', env, clear=True), \
                patch.object(combat, 'build_health_update', record(combat.build_health_update)), \
                patch.object(death_events, 'build_death_update', record(death_events.build_death_update)), \
                patch.object(animation_events, 'build_animation_update',
                             record(animation_events.build_animation_update)):
            for projectile in range(1, 200):
                shard.observe_solo_combat(hit_on(projectile, HIVE), state)
                shard.solo_simulation_messages(state, 1. + projectile * .1)
                if not state['combat'].alive(HIVE):
                    break
        self.assertFalse(state['combat'].alive(HIVE))
        to_hive = [types for entity, types in built if entity == HIVE]
        self.assertTrue(to_hive)
        self.assertEqual(set(to_hive), {shard.HIVE_SHARED_TYPES})
        self.assertNotIn(HIVE, state.get('hit_reaction', {}))

    def animation_sent(self, state, times, before_each=None):
        """The hive's animation values each tick sent, by tick time."""
        import animation_events
        sent = {}
        original = animation_events.build_animation_update

        def record(entity_id, shared_types, *, values, **kwargs):
            if entity_id == HIVE:
                self.assertEqual(tuple(shared_types), shard.HIVE_SHARED_TYPES)
                sent.setdefault(now, []).append(dict(values))
            return original(entity_id, shared_types, values=values, **kwargs)
        with patch.dict('os.environ', ENV, clear=True), \
                patch.object(animation_events, 'build_animation_update', record):
            for now in times:
                if before_each is not None:
                    before_each(now)
                shard.solo_simulation_messages(state, now)
        return sent

    def test_it_emerges_1_55_s_after_its_first_tick_once(self, _log):
        # hive.animgraph leaves underground0 for emerge0 only on is_active.
        state = self.start()
        sent = self.animation_sent(state, (1.0, 2.5, 2.55, 3.0, 10.0))
        self.assertEqual(sent, {2.55: [{'is_active': True}]})

    def test_is_dead_goes_once_when_it_dies(self, _log):
        state = self.start()
        state['player_position'] = (SPOT[0] - 10., SPOT[1], SPOT[2])
        projectile = [0]

        def shoot(now):
            if now >= 5. and state['combat'].alive(HIVE):
                projectile[0] += 1
                shard.observe_solo_combat(hit_on(projectile[0], HIVE), state)
        times = [1.0, 2.55] + [5. + i * .1 for i in range(400)]
        sent = self.animation_sent(state, times, before_each=shoot)
        self.assertFalse(state['combat'].alive(HIVE))
        values = [v for per_tick in sent.values() for v in per_tick]
        self.assertEqual(values, [{'is_active': True}, {'is_dead': True}])

    def test_a_hive_killed_before_it_emerged_gets_both_at_once(self, _log):
        state = self.start()
        state['combat'].entities[HIVE].health = 0.
        sent = self.animation_sent(state, (1.0, 1.1))
        self.assertEqual(sent, {1.0: [{'is_active': True, 'is_dead': True}]})

    def test_it_regenerates_after_19_5_s_without_a_hit(self, _log):
        state = self.start(dict(ENV, CRUCIBLE_SOLO_HIT_RANGE='1'))
        state['player_position'] = (SPOT[0] - 10., SPOT[1], SPOT[2])
        arena = state['combat']
        with patch.dict('os.environ', ENV, clear=True):
            shard.observe_solo_combat(hit_on(1, HIVE), state)
            damaged = arena.health(HIVE)
            self.assertLess(damaged, 5000.)
            hit_at = state['guide_hive']['next_heal'] - shard.HIVE_REGEN_AFTER
            shard.solo_simulation_messages(state, hit_at + 19.4)
            self.assertEqual(arena.health(HIVE), damaged)
            shard.solo_simulation_messages(state, hit_at + 19.5)
            self.assertEqual(arena.health(HIVE), min(5000., damaged + 100.))
            shard.solo_simulation_messages(state, hit_at + 20.0)
            self.assertEqual(arena.health(HIVE), min(5000., damaged + 100.))
            shard.solo_simulation_messages(state, hit_at + 20.5)
            self.assertEqual(arena.health(HIVE), min(5000., damaged + 200.))


class GuideHiveSwitchLineTests(unittest.TestCase):
    def test_b0_names_the_hearts_mode(self):
        import hunter_ai
        import hunter_patrol
        route = ((0., 0., 0.), (1., 0., 0.))
        for mode, token in (('1', 'guide-heart'), ('proximity', 'guide-heart-proximity')):
            env = dict(ENV, CRUCIBLE_SOLO_AI='1', CRUCIBLE_SOLO_GUIDE_HEART=mode)
            state = {'guide_hive': {'position': SPOT, 'next_heal': None, 'regens': 0}}
            squad = hunter_ai.Squad([hunter_ai.Bot(0x1000003, route, hunter_ai.MARINE)])
            with self.subTest(mode=mode), patch.dict('os.environ', env, clear=True), \
                    patch.object(hunter_patrol, 'squad', return_value=squad), \
                    patch.object(shard, 'level_sight', return_value=None), \
                    patch.object(shard, 'log') as log:
                shard.start_solo_simulation(state)
            lines = [c.args[0].strip() for c in log.call_args_list
                     if 'HUNTER AI switches' in c.args[0]]
            self.assertEqual(lines, ['HUNTER AI switches: guide-hive %s; sight off' % token])

    def test_b0_confirms_the_hive_only_where_it_spawned(self):
        import hunter_ai
        import hunter_patrol
        route = ((0., 0., 0.), (1., 0., 0.))
        env = dict(ENV, CRUCIBLE_SOLO_AI='1')
        for state, line in (({'guide_hive': {'position': SPOT, 'next_heal': None, 'regens': 0}},
                             'HUNTER AI switches: guide-hive; sight off'),
                            ({}, None)):
            squad = hunter_ai.Squad([hunter_ai.Bot(0x1000003, route, hunter_ai.MARINE)])
            with self.subTest(state=bool(state)), patch.dict('os.environ', env, clear=True), \
                    patch.object(hunter_patrol, 'squad', return_value=squad), \
                    patch.object(shard, 'level_sight', return_value=None), \
                    patch.object(shard, 'log') as log:
                shard.start_solo_simulation(state)
            lines = [c.args[0].strip() for c in log.call_args_list
                     if 'HUNTER AI switches' in c.args[0]]
            self.assertEqual(lines, [line] if line else [])

    def test_b0_names_the_despawns_only_where_the_hive_spawned(self):
        import hunter_ai
        import hunter_patrol
        route = ((0., 0., 0.), (1., 0., 0.))
        env = dict(ENV, CRUCIBLE_SOLO_AI='1', CRUCIBLE_SOLO_GUIDE_DESPAWN='1')
        for state, line in (({'guide_hive': {'position': SPOT, 'next_heal': None, 'regens': 0}},
                             'HUNTER AI switches: guide-hive guide-despawn; sight off'),
                            ({}, None)):
            squad = hunter_ai.Squad([hunter_ai.Bot(0x1000003, route, hunter_ai.MARINE)])
            with self.subTest(state=bool(state)), patch.dict('os.environ', env, clear=True), \
                    patch.object(hunter_patrol, 'squad', return_value=squad), \
                    patch.object(shard, 'level_sight', return_value=None), \
                    patch.object(shard, 'log') as log:
                shard.start_solo_simulation(state)
            lines = [c.args[0].strip() for c in log.call_args_list
                     if 'HUNTER AI switches' in c.args[0]]
            self.assertEqual(lines, [line] if line else [])


def math_dist2(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** .5


if __name__ == '__main__':
    unittest.main()


HEART = shard.HEART_ENTITY_ID
HEART_ENV = dict(ENV, CRUCIBLE_SOLO_GUIDE_HEART='1', CRUCIBLE_SOLO_HIT_RANGE='1')


def tick_ids(messages, previous=0):
    """The entity counters a tick's chained messages address, in order."""
    ids = []
    for message in messages:
        previous, _ = shard.decode_entity_id(message.header, previous_id=previous)
        ids.append(previous)
    return ids


@patch.object(shard, 'log')
class GuideHeartTests(unittest.TestCase):
    def killed_hive(self, env=HEART_ENV):
        state = {'guide_hive': {'position': SPOT, 'next_heal': None, 'regens': 0}}
        with patch.dict('os.environ', env, clear=True):
            shard.start_solo_simulation(state)
            state['player_position'] = (SPOT[0] - 10., SPOT[1], SPOT[2])
            projectile = 0
            while state['combat'].alive(HIVE):
                projectile += 1
                shard.observe_solo_combat(hit_on(projectile, HIVE), state)
        return state

    def test_the_slice_hash_is_the_level_librarys(self, _log):
        # levels/april2018/commondata/stringhashlibrary.xml: Key="3871577199"
        # Value="slices/interactive/cruciheart".
        self.assertEqual(solo_modes.level_name_hash('slices/interactive/cruciheart'),
                         shard.HEART_SLICE_HASH)

    def test_the_spawn_message(self, _log):
        message = shard.build_guide_heart_probe(SPOT, previous_id=6)
        counter, offset = shard.decode_entity_id(message.header, previous_id=6)
        self.assertEqual(counter, 7)
        self.assertEqual(int.from_bytes(message.header[offset+1:offset+5], 'big'),
                         shard.HEART_SLICE_HASH)
        self.assertEqual(message.payload,
                         shard.build_replication_record(shard.location_bits(shard.HEART_SHARED_TYPES))
                         + shard.pack_practice_spawn_transform(SPOT))

    def test_only_with_the_hive_in_hoth_guide(self, _log):
        for env, on in ((HEART_ENV, True), (dict(HEART_ENV, CRUCIBLE_SOLO_GUIDE_HIVE='0'), False),
                        (dict(HEART_ENV, CRUCIBLE_SOLO_MODE='alpha-hunters'), False),
                        (dict(HEART_ENV, CRUCIBLE_SOLO_GUIDE_HEART='0'), False)):
            with self.subTest(env=env), patch.dict('os.environ', env, clear=True):
                self.assertEqual(shard.guide_heart_enabled(), on)

    def test_the_hives_death_spawns_it_once_where_the_hive_stood(self, _log):
        state = self.killed_hive()
        self.assertEqual(state['guide_heart']['position'], SPOT)
        self.assertNotIn(HEART, state['combat'].entities)      # no health: not in the arena
        sent = []
        with patch.dict('os.environ', HEART_ENV, clear=True):
            for i in range(1, 40):
                sent.append(tick_ids(shard.solo_simulation_messages(state, 50. + i * .05)))
        self.assertEqual(sum(ids.count(7) for ids in sent), 1)   # the spawn, and nothing else
        self.assertEqual(sent[0].count(7), 1)
        # A reported hit on it is refused: it is not a combat entity.
        with patch.dict('os.environ', HEART_ENV, clear=True):
            shard.observe_solo_combat(hit_on(250, HEART), state)
        self.assertNotIn(HEART, state['combat'].entities)

    def test_ten_seconds_on_it_capture_it_and_the_match_ends_12_s_later(self, log):
        state = self.killed_hive()
        with patch.dict('os.environ', HEART_ENV, clear=True):
            shard.solo_simulation_messages(state, 50.)               # the spawn
            state['player_position'] = (SPOT[0] + 2., SPOT[1], SPOT[2])
            now = 51.
            state['player_interact_at'] = now
            shard.solo_simulation_messages(state, now)               # starts
            state['player_position'] = (SPOT[0] + 5., SPOT[1], SPOT[2])
            shard.solo_simulation_messages(state, now + 4.)          # left: starts again
            state['player_position'] = (SPOT[0] + 2., SPOT[1], SPOT[2])
            state['player_interact_at'] = now + 5.
            shard.solo_simulation_messages(state, now + 5.)
            shard.solo_simulation_messages(state, now + 14.9)
            self.assertIsNone(state['guide_heart']['captured_at'])
            shard.solo_simulation_messages(state, now + 15.)
            self.assertEqual(state['guide_heart']['captured_at'], now + 15.)
            self.assertEqual(state['team_scores'], {1: 1})
            shard.solo_simulation_messages(state, now + 26.9)
            self.assertNotIn('match_result', state)
            shard.solo_simulation_messages(state, now + 27.)
            self.assertEqual(state['match_result']['winner_team'], 1)
            self.assertEqual(state['match_result']['reason'], 'heart')
        lines = [c.args[0].strip() for c in log.call_args_list if 'GUIDE HEART' in c.args[0]]
        self.assertIn('GUIDE HEART capture started at 51.0 s (interact)', lines)
        self.assertIn('GUIDE HEART capture interrupted (count 1)', lines)
        self.assertIn('GUIDE HEART match over: winner team 1', lines)

    def test_a_dead_player_does_not_capture(self, _log):
        state = self.killed_hive()
        state['combat'].entities[shard.PLAYER_CHARACTER_ID].health = 0.
        with patch.dict('os.environ', HEART_ENV, clear=True):
            shard.solo_simulation_messages(state, 50.)
            state['player_position'] = SPOT
            for i in range(1, 300):
                state['player_interact_at'] = 50. + i * .1
                shard.solo_simulation_messages(state, 50. + i * .1)
        self.assertIsNone(state['guide_heart']['capture_started'])

    def test_standing_briefly_without_interact_starts_nothing_unless_proximity(self, log):
        for mode, started in (('1', False), ('proximity', True)):
            env = dict(HEART_ENV, CRUCIBLE_SOLO_GUIDE_HEART=mode)
            state = self.killed_hive(env)
            with self.subTest(mode=mode), patch.dict('os.environ', env, clear=True):
                shard.solo_simulation_messages(state, 50.)
                state['player_position'] = SPOT
                for i in range(1, 30):
                    shard.solo_simulation_messages(state, 50. + i * .1)
                self.assertEqual(state['guide_heart']['capture_started'] is not None, started)

    def test_a_press_in_a_longer_payload_is_recorded(self, log):
        import test_client_input
        state = self.killed_hive()
        data = bytearray(test_client_input.MOVING)
        data[58] |= 0x80                                    # Interact, frame 0
        longer = bytes(data[:78]) + b'\x01\x02' + bytes(data[78:])
        message = shard.EntityRpc(3, shard.PLAYER_CONNECTION_ID, 172, 1, longer)
        with patch.dict('os.environ', HEART_ENV, clear=True):
            shard.observe_client_input(message, state)
        self.assertIsNotNone(state.get('player_interact_at'))
        self.assertEqual(state.get('input_unknown_shape'), 1)  # no position from it
        lines = [c.args[0].strip() for c in log.call_args_list]
        self.assertIn('CLIENT INPUT shape 328 bytes (count 1)', lines)
        self.assertIn('CLIENT INPUT action Interact rose (count 1)', lines)

    def test_every_bit_rise_is_logged(self, log):
        import test_client_input
        state = self.killed_hive()
        with patch.dict('os.environ', HEART_ENV, clear=True):
            for byte0 in (0x20, 0x20, 0x00, 0x20, 0x01):        # fire, held, off, fire, jump
                data = bytearray(test_client_input.MOVING)
                data[58] = byte0
                shard.observe_client_input(
                    shard.EntityRpc(3, shard.PLAYER_CONNECTION_ID, 172, 1, bytes(data)), state)
        lines = [c.args[0].strip() for c in log.call_args_list if 'rose' in c.args[0]]
        self.assertEqual(lines, ['CLIENT INPUT action PrimaryFire rose (count 1)',
                                 'CLIENT INPUT action PrimaryFire rose (count 2)',
                                 'CLIENT INPUT action Jump rose (count 1)'])

    def test_in_range_15_s_without_a_press_falls_back(self, log):
        state = self.killed_hive()
        with patch.dict('os.environ', HEART_ENV, clear=True):
            shard.solo_simulation_messages(state, 50.)
            state['player_position'] = SPOT
            for i in range(1, 151):
                shard.solo_simulation_messages(state, 50. + i * .1)    # in range from 50.1 s
            self.assertIsNone(state['guide_heart']['capture_started'])  # 14.9 s
            shard.solo_simulation_messages(state, 65.1)
            self.assertEqual(state['guide_heart']['capture_started'], 65.1)
        lines = [c.args[0].strip() for c in log.call_args_list if 'capture started' in c.args[0]]
        self.assertEqual(lines, ['GUIDE HEART capture started at 65.1 s (proximity fallback)'])

    def test_an_interact_input_is_recorded(self, log):
        import test_client_input
        state = self.killed_hive()
        data = bytearray(test_client_input.MOVING)
        data[58] |= 0x80                                    # Interact, frame 0
        message = shard.EntityRpc(3, shard.PLAYER_CONNECTION_ID, 172, 1, bytes(data))
        with patch.dict('os.environ', HEART_ENV, clear=True):
            shard.observe_client_input(message, state)
        self.assertIsNotNone(state.get('player_interact_at'))
        lines = [c.args[0].strip() for c in log.call_args_list]
        self.assertIn('CLIENT INPUT interact pressed (count 1)', lines)


@patch.object(shard, 'log')
class GuideHeartPacketTests(unittest.TestCase):
    def test_the_split_tick_packet_carries_the_hearts_create_intact(self, _log):
        import lz4.block
        env = dict(HEART_ENV, CRUCIBLE_SHARD_BOOTSTRAP='practice-character',
                   CRUCIBLE_SHARD_SPLIT_UPDATES='1')
        state = GuideHeartTests.killed_hive(None, env)
        state.update({'character_links_sent': True, 'clock_epoch': 100.0,
                      'clock_last_sent': 100.0, 'seq': 10, 'received_seq': 1,
                      'received_window': 1, 'practice_npcs_sent': True})
        with patch.dict('os.environ', env, clear=True):
            packets = shard.game_clock_packets(state, now=101.0)
        raws = [lz4.block.decompress(p[1:], uncompressed_size=8192) for p in packets]
        create = b'\x11' + shard.HEART_SLICE_HASH.to_bytes(4, 'big')
        carrying = [raw for raw in raws if create in raw]
        self.assertEqual(len(carrying), 1)
        # The entity id just before it decodes to counter 7, whatever came
        # first in that packet (RelativeIds restart per split packet).
        self.assertTrue(any(shard.encode_entity_id(7, previous, local=False) + create
                            in carrying[0] for previous in range(0, 8)))
        self.assertTrue(state['guide_heart']['sent'])


DESPAWN_ENV = dict(HEART_ENV, CRUCIBLE_SOLO_GUIDE_DESPAWN='1')


def deletes(messages, previous=0):
    """The entity counters a tick's chained messages delete (bit 7), in order."""
    found = []
    for message in messages:
        previous, offset = shard.decode_entity_id(message.header, previous_id=previous)
        if message.header[offset] & 0x80:
            found.append(previous)
    return found


@patch.object(shard, 'log')
class GuideDespawnTests(unittest.TestCase):
    def test_the_delete_message(self, _log):
        message = shard.build_entity_delete(HIVE, previous_id=3)
        counter, offset = shard.decode_entity_id(message.header, previous_id=3)
        self.assertEqual(counter, 6)
        # Bit 7 with the spawn's type 1, no slice id, and a PayloadSize of 0.
        self.assertEqual(message.header[offset:], b'\x81\x00\x00')
        self.assertEqual(message.payload, b'')

    def test_only_with_its_switch_where_the_hive_can_spawn(self, _log):
        for env, on in ((DESPAWN_ENV, True), (HEART_ENV, False),
                        (dict(DESPAWN_ENV, CRUCIBLE_SOLO_GUIDE_HIVE='0'), False),
                        (dict(DESPAWN_ENV, CRUCIBLE_SOLO_MODE='alpha-hunters'), False)):
            with self.subTest(env=env), patch.dict('os.environ', env, clear=True):
                self.assertEqual(shard.guide_despawn_enabled(), on)

    def test_without_it_nothing_is_deleted(self, _log):
        state = GuideHeartTests.killed_hive(None, HEART_ENV)
        self.assertIsNone(state['guide_hive'].get('despawn_at'))
        with patch.dict('os.environ', HEART_ENV, clear=True):
            ticks = [shard.solo_simulation_messages(state, 50. + i * .5) for i in range(40)]
        self.assertFalse(any(deletes(tick) for tick in ticks))
        self.assertIn(HIVE, state['combat'].entities)

    def test_the_hive_goes_5_5_s_after_its_death_and_is_never_addressed_again(self, log):
        state = GuideHeartTests.killed_hive(None, DESPAWN_ENV)
        due = state['guide_hive']['despawn_at']
        with patch.dict('os.environ', DESPAWN_ENV, clear=True):
            before = shard.solo_simulation_messages(state, due - .1)
            self.assertIn(6, tick_ids(before))                 # its health still repeats
            self.assertEqual(deletes(before), [])
            tick = shard.solo_simulation_messages(state, due)
            self.assertEqual(tick_ids(tick).count(6), 1)       # the delete, alone
            self.assertEqual(deletes(tick), [6])
            self.assertNotIn(HIVE, state['combat'].entities)
            later = [tick_ids(shard.solo_simulation_messages(state, due + i * .05))
                     for i in range(1, 100)]
        self.assertFalse(any(6 in ids for ids in later))
        lines = [c.args[0].strip() for c in log.call_args_list if 'GUIDE HIVE deleted' in c.args[0]]
        self.assertEqual(lines, ['GUIDE HIVE deleted at %.1f s' % due])

    def test_a_hit_reported_on_the_deleted_hive_is_refused(self, log):
        state = GuideHeartTests.killed_hive(None, DESPAWN_ENV)
        with patch.dict('os.environ', DESPAWN_ENV, clear=True):
            shard.solo_simulation_messages(state, state['guide_hive']['despawn_at'])
            self.assertIsNone(shard.reported_hit_range(state, shard.PLAYER_CHARACTER_ID)(HIVE))
            log.reset_mock()
            shard.observe_solo_combat(hit_on(250, HIVE), state)
        self.assertEqual(state['combat'].last_rejection, 'no live enemy')
        self.assertNotIn(HIVE, state['combat'].entities)
        self.assertFalse([c for c in log.call_args_list if 'hit target=01000006' in c.args[0]])

    def test_the_heart_goes_1_s_after_its_capture_and_the_match_still_ends(self, log):
        state = GuideHeartTests.killed_hive(None, DESPAWN_ENV)
        due = state['guide_hive']['despawn_at']
        with patch.dict('os.environ', DESPAWN_ENV, clear=True):
            shard.solo_simulation_messages(state, due - 5.)        # the heart's spawn
            state['player_position'] = (SPOT[0] + 2., SPOT[1], SPOT[2])
            state['player_interact_at'] = due - 4.
            shard.solo_simulation_messages(state, due - 4.)        # the capture starts
            self.assertEqual(deletes(shard.solo_simulation_messages(state, due)), [6])
            shard.solo_simulation_messages(state, due + 6.)
            captured = state['guide_heart']['captured_at']
            self.assertEqual(captured, due + 6.)
            self.assertEqual(deletes(shard.solo_simulation_messages(state, captured + .9)), [])
            tick = shard.solo_simulation_messages(state, captured + 1.)
            self.assertEqual(deletes(tick), [7])
            self.assertEqual(tick_ids(tick).count(7), 1)
            later = [tick_ids(shard.solo_simulation_messages(state, captured + 1. + i * .5))
                     for i in range(1, 22)]
            self.assertFalse(any(7 in ids for ids in later))
            self.assertNotIn('match_result', state)
            shard.solo_simulation_messages(state, captured + 12.)
        self.assertEqual(state['match_result']['reason'], 'heart')
        lines = [c.args[0].strip() for c in log.call_args_list if 'deleted at' in c.args[0]]
        self.assertEqual(lines, ['GUIDE HIVE deleted at %.1f s' % due,
                                 'GUIDE HEART deleted at %.1f s' % (captured + 1.)])


@patch.object(shard, 'log')
class GuideDespawnPacketTests(unittest.TestCase):
    def test_the_split_tick_packet_carries_the_delete_intact(self, _log):
        import lz4.block
        env = dict(DESPAWN_ENV, CRUCIBLE_SHARD_BOOTSTRAP='practice-character',
                   CRUCIBLE_SHARD_SPLIT_UPDATES='1')
        state = GuideHeartTests.killed_hive(None, env)
        state['guide_hive']['despawn_at'] = .5                 # due before this 1.0 s tick
        state.update({'character_links_sent': True, 'clock_epoch': 100.0,
                      'clock_last_sent': 100.0, 'seq': 10, 'received_seq': 1,
                      'received_window': 1, 'practice_npcs_sent': True})
        groups, split = [], shard.split_entity_messages

        def record(messages):
            groups.extend(split(messages))
            return split(messages)
        with patch.dict('os.environ', env, clear=True), \
                patch.object(shard, 'split_entity_messages', record):
            packets = shard.game_clock_packets(state, now=101.0)
        self.assertTrue(state['guide_hive']['deleted'])
        # One message for the hive in the whole tick, and it is the delete.
        addressed = [counter for group in groups for counter in tick_ids(group)]
        self.assertEqual(addressed.count(6), 1)
        self.assertEqual([c for group in groups for c in deletes(group)], [6])
        raws = [lz4.block.decompress(p[1:], uncompressed_size=8192) for p in packets]
        carrying = [raw for raw in raws
                    if any(shard.encode_entity_id(6, previous) + b'\x81\x00\x00' in raw
                           for previous in range(0, 8))]
        self.assertEqual(len(carrying), 1)
