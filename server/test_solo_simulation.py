"""The shard's opt-in wiring of combat.py, creature_ai.py and hunter_ai.py.

Everything here is offline: no sockets, no navmesh load, injected clocks.
The accepted baselines must be unaffected when both flags are off.
"""
import math
import struct
import unittest
from unittest.mock import patch

import client_input
import shard_stub as shard
import solo_modes
import test_client_input
from practice_population import SPITTER_SHARED_TYPES, DUMMY_SHARED_TYPES


def rpc(entity, component, rpc_id, data, delivery=3):
    return shard.EntityRpc(delivery, entity, component, rpc_id, data)


def projectile_result(projectile, *targets):
    return bytes([projectile, len(targets)]) + b''.join(
        t.to_bytes(4, 'big') for t in targets)


FLAGS_OFF = {'CRUCIBLE_SOLO_COMBAT': '0', 'CRUCIBLE_SOLO_AI': '0',
             'CRUCIBLE_SOLO_MODE': 'practice'}
COMBAT_ON = {'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_AI': '0',
             'CRUCIBLE_SOLO_MODE': 'practice'}
AI_ON = {'CRUCIBLE_SOLO_COMBAT': '0', 'CRUCIBLE_SOLO_AI': '1',
         'CRUCIBLE_SOLO_MODE': 'practice'}


class ClientInputObservationTests(unittest.TestCase):
    def test_player_position_is_tracked_from_the_connection_entity(self):
        state = {}
        with patch.object(shard, 'log'):
            shard.observe_client_input(
                rpc(shard.PLAYER_CONNECTION_ID, 172, 1, test_client_input.STILL), state)
        self.assertEqual(state['player_position'],
                         client_input.latest_position(test_client_input.STILL))

    def test_other_entities_and_shapes_do_not_set_a_position(self):
        state = {}
        shard.observe_client_input(
            rpc(shard.PLAYER_CHARACTER_ID, 172, 1, test_client_input.STILL), state)
        shard.observe_client_input(
            rpc(shard.PLAYER_CONNECTION_ID, 172, 1, b'\x00' * 36), state)
        self.assertNotIn('player_position', state)
        self.assertEqual(state['input_unknown_shape'], 1)


class SoloSimulationStartTests(unittest.TestCase):
    @patch.dict('os.environ', FLAGS_OFF)
    def test_flags_off_build_nothing(self):
        state = {}
        shard.start_solo_simulation(state)
        self.assertEqual(state, {})

    @patch.dict('os.environ', COMBAT_ON)
    def test_practice_combat_registers_player_spitters_and_targets(self):
        state = {}
        shard.start_solo_simulation(state)
        arena = state['combat']
        self.assertNotIn('squad', state)
        self.assertNotIn('creatures', state)
        self.assertEqual(arena.health(shard.PLAYER_CHARACTER_ID), 800.0)
        self.assertEqual(arena.health(0x1000003), 400.0)
        self.assertEqual(arena.health(0xfe00004b), 600.0)
        self.assertEqual(len(arena.entities), 1 + 2 + 8)

    @patch.dict('os.environ', AI_ON)
    def test_practice_ai_builds_a_creature_director_only(self):
        state = {}
        shard.start_solo_simulation(state)
        self.assertNotIn('combat', state)
        self.assertNotIn('squad', state)   # Practice has no hunters
        self.assertEqual(sorted(state['creatures'].creatures), [0x1000003, 0x1000004])
        self.assertEqual(state['creature_types'][0x1000003], (SPITTER_SHARED_TYPES, False))

    @patch.dict('os.environ', dict(AI_ON, CRUCIBLE_SOLO_MODE='hoth-guide'))
    def test_the_squad_walks_the_selected_maps_mesh(self):
        state = {}
        with patch('hunter_patrol.squad', return_value=FakeSquad()) as squad:
            shard.start_solo_simulation(state)
        self.assertEqual(squad.call_args.kwargs['level'], 'HotH_02')
        self.assertIs(state['squad'], squad.return_value)

    @patch.dict('os.environ', dict(AI_ON, CRUCIBLE_SOLO_MODE='tutorial'))
    def test_a_map_whose_points_do_not_route_keeps_standing_hunters(self):
        state = {}
        with patch('hunter_patrol.squad', side_effect=ValueError('not connected')), \
                patch.object(shard, 'log'):
            shard.start_solo_simulation(state)
        self.assertNotIn('squad', state)
        self.assertEqual(state['simulation_errors'], {'hunter squad': 1})

    @patch.dict('os.environ', dict(AI_ON, CRUCIBLE_SOLO_MODE='tutorial',
                                   CRUCIBLE_SOLO_PATROL='1'))
    def test_unroutable_patrol_probes_keep_the_authored_points(self):
        with patch('hunter_patrol.routes', side_effect=ValueError('not connected')), \
                patch.object(shard, 'log'), \
                patch.object(shard, 'build_practice_character_probe',
                             side_effect=lambda **kw: kw['position']) as probe:
            positions = shard.build_solo_hunter_probes()
        self.assertEqual(tuple(positions), solo_modes.TUTORIAL_02.bot_positions)
        self.assertEqual(probe.call_count, 3)


class SoloCombatTests(unittest.TestCase):
    @patch.dict('os.environ', COMBAT_ON)
    @patch.object(shard, 'log')
    def test_reported_hit_damages_and_replicates_absolute_health(self, _log):
        state = {}
        shard.start_solo_simulation(state)
        shard.observe_solo_combat(
            rpc(shard.PLAYER_CHARACTER_ID, 115, 4,
                projectile_result(1, 0xfe00004b, shard.PLAYER_CHARACTER_ID)), state)
        self.assertEqual(state['combat'].health(0xfe00004b), 580.0)
        messages = shard.solo_simulation_messages(state, 1.0)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].payload[-12:], struct.pack('>fff', 0, 600.0, 580.0))
        # Byte-identical to the nonlethal probe's encoding for the same value.
        self.assertEqual(messages[0].payload,
                         shard.build_practice_health_update(0xfe00004b, 580.0).payload)

    @patch.dict('os.environ', COMBAT_ON)
    @patch.object(shard, 'log')
    def test_death_reaches_zero_and_respawns_on_the_authored_timer(self, _log):
        import combat
        state = {}
        shard.start_solo_simulation(state)
        with patch.object(shard.time, 'monotonic', return_value=100.0):
            for projectile in range(1, 31):
                shard.observe_solo_combat(
                    rpc(shard.PLAYER_CHARACTER_ID, 115, 4,
                        projectile_result(projectile, 0xfe00004b)), state)
        self.assertEqual(state['combat'].health(0xfe00004b), 0.0)
        back = (100.0 + combat.PRACTICE_TARGET_RESPAWN_SECONDS
                + combat.PRACTICE_TARGET_TELEGRAPH_SECONDS)
        shard.solo_simulation_messages(state, back - 0.01)
        self.assertEqual(state['combat'].health(0xfe00004b), 0.0)
        shard.solo_simulation_messages(state, back)
        self.assertEqual(state['combat'].health(0xfe00004b), 600.0)

    @patch.dict('os.environ', COMBAT_ON)
    def test_wrong_entity_or_rpc_is_ignored(self):
        state = {}
        shard.start_solo_simulation(state)
        shard.observe_solo_combat(
            rpc(shard.PLAYER_CONNECTION_ID, 115, 4, projectile_result(1, 0xfe00004b)), state)
        shard.observe_solo_combat(
            rpc(shard.PLAYER_CHARACTER_ID, 172, 1, projectile_result(1, 0xfe00004b)), state)
        self.assertEqual(state['combat'].health(0xfe00004b), 600.0)


    @patch.dict('os.environ', COMBAT_ON)
    @patch.object(shard, 'log')
    def test_refused_reports_are_logged_at_powers_of_two(self, log):
        state = {}
        shard.start_solo_simulation(state)
        for projectile in range(1, 6):
            shard.observe_solo_combat(
                rpc(shard.PLAYER_CHARACTER_ID, 115, 4,
                    projectile_result(projectile, shard.PLAYER_CHARACTER_ID)), state)
        shard.observe_solo_combat(
            rpc(shard.PLAYER_CHARACTER_ID, 115, 4, b'\x01\x09'), state)
        lines = [call.args[0] for call in log.call_args_list]
        self.assertEqual(len(lines), 4)
        self.assertIn('shooter=01000002 (no live enemy, count 4): '
                      'projectile 4 after None, reported 01000002', lines[2])
        self.assertIn('(invalid vector, count 1): 0109', lines[3])
        self.assertEqual(state['refused_reports'][
            shard.PLAYER_CHARACTER_ID, 'no live enemy'], 5)

    @patch.object(shard, 'log')
    def test_unregistered_shooters_are_logged_only_with_bot_reports(self, log):
        report = rpc(0x01000009, 115, 4, projectile_result(1, shard.PLAYER_CHARACTER_ID))
        with patch.dict('os.environ', COMBAT_ON):
            state = {}
            shard.start_solo_simulation(state)
            shard.observe_solo_combat(report, state)
            log.assert_not_called()
        with patch.dict('os.environ', dict(COMBAT_ON, CRUCIBLE_SOLO_BOT_HIT_REPORTS='1')):
            shard.observe_solo_combat(report, state)
        log.assert_called_once()
        self.assertIn('shooter=01000009 (unregistered shooter, count 1): 010101000002',
                      log.call_args.args[0])


ALPHA_COMBAT = {'CRUCIBLE_SOLO_COMBAT': '1', 'CRUCIBLE_SOLO_AI': '0',
                'CRUCIBLE_SOLO_MODE': 'alpha-hunters'}


def report_until_dead(state, shooter, target, first_projectile=1):
    projectile = first_projectile
    while state['combat'].alive(target):
        shard.observe_solo_combat(
            rpc(shooter, 115, 4, projectile_result(projectile & 0xff, target)), state)
        projectile += 1
    return projectile


@patch.object(shard, 'log')
class SoloMatchEndTests(unittest.TestCase):
    def finished_match(self, env):
        state = {}
        with patch.dict('os.environ', env):
            shard.start_solo_simulation(state)
            if env.get('CRUCIBLE_SOLO_BOT_HIT_REPORTS') == '1':
                report_until_dead(state, shard.HUNTER_BOT_IDS[0], shard.PLAYER_CHARACTER_ID)
            else:
                projectile = 1
                for hunter in shard.HUNTER_BOT_IDS:
                    projectile = report_until_dead(
                        state, shard.PLAYER_CHARACTER_ID, hunter, projectile)
            first = shard.solo_simulation_messages(state, 1.0)
            later = [shard.solo_simulation_messages(state, 1.0 + i / 20) for i in range(1, 5)]
        return state, first, later

    def test_elimination_ends_the_match_and_repeats_game_over(self, _log):
        state, first, later = self.finished_match(ALPHA_COMBAT)
        self.assertEqual(state['match_result']['winner_team'], 1)
        game_over = shard.build_solo_match_phase_update(
            shard.solo_modes.selected(), shard.solo_modes.MATCH_PHASE['GameOver'])
        self.assertTrue(first[-1].payload.endswith(b'\x00\x09'))
        for messages in later:
            self.assertEqual([m.payload for m in messages], [game_over.payload])

    def test_player_hits_use_the_selected_heros_primary(self, _log):
        import combat
        for hero, weapon in (('saboteur', combat.SABOTEUR_PRIMARY),
                             ('marine', combat.MARINE_PRIMARY)):
            with self.subTest(hero=hero), patch.dict(
                    'os.environ', dict(ALPHA_COMBAT, CRUCIBLE_SOLO_HERO=hero)):
                state = {}
                shard.start_solo_simulation(state)
                hunter = shard.HUNTER_BOT_IDS[0]
                shard.observe_solo_combat(
                    rpc(shard.PLAYER_CHARACTER_ID, 115, 4, projectile_result(1, hunter)), state)
                self.assertEqual(state['combat'].health(hunter),
                                 shard.MARINE_HEALTH_MAX - weapon.damage_max)

    def test_hunter_hits_stay_marine_whatever_the_hero(self, _log):
        import combat
        with patch.dict('os.environ', dict(ALPHA_COMBAT, CRUCIBLE_SOLO_HERO='saboteur',
                                           CRUCIBLE_SOLO_BOT_HIT_REPORTS='1')):
            state = {}
            shard.start_solo_simulation(state)
            shard.observe_solo_combat(
                rpc(shard.HUNTER_BOT_IDS[0], 115, 4,
                    projectile_result(1, shard.PLAYER_CHARACTER_ID)), state)
        self.assertEqual(state['combat'].health(shard.PLAYER_CHARACTER_ID),
                         shard.MARINE_HEALTH_MAX - combat.MARINE_PRIMARY.damage_max)

    def test_rank_one_rides_alternate_ticks_after_a_win(self, log):
        state, _, later = self.finished_match(dict(ALPHA_COMBAT, CRUCIBLE_SOLO_MATCH_RANK='1'))
        self.assertEqual([len(messages) for messages in later], [1, 2, 1, 2])
        self.assertEqual(later[1][1].payload, bytes.fromhex('0800071800000001'))
        self.assertEqual(later[1][1].payload, shard.build_player_rank_update(1).payload)
        published = [c.args[0] for c in log.call_args_list if 'rank' in c.args[0]]
        self.assertEqual(len(published), 1)
        self.assertIn('rank 1 published', published[0])

    def test_rank_two_after_the_hunters_eliminate_the_player(self, _log):
        state, _, later = self.finished_match(dict(
            ALPHA_COMBAT, CRUCIBLE_SOLO_MATCH_RANK='1', CRUCIBLE_SOLO_BOT_HIT_REPORTS='1'))
        self.assertEqual(state['match_result']['winner_team'], 2)
        self.assertEqual(later[1][1].payload[-4:], b'\x00\x00\x00\x02')

    def test_rank_chains_after_the_baked_phase_entity(self, _log):
        state, _, later = self.finished_match(dict(ALPHA_COMBAT, CRUCIBLE_SOLO_MATCH_RANK='1'))
        with patch.dict('os.environ', ALPHA_COMBAT):
            profile = shard.solo_modes.selected()
        chained = shard.build_player_rank_update(
            1, previous_id=profile.phase_entry + profile.baked_counter_bias)
        self.assertEqual(later[1][1].header, chained.header)
        self.assertEqual(shard.decode_entity_id(
            chained.header, previous_id=profile.phase_entry + profile.baked_counter_bias)[0], 1)


SERVER_HITS = dict(ALPHA_COMBAT, CRUCIBLE_SOLO_BOT_DAMAGE='server')


@patch.object(shard, 'log')
class ServerResolvedHunterHitTests(unittest.TestCase):
    def test_landed_round_damages_the_player_on_its_flight_tick(self, log):
        from hunter_hits import Landing
        with patch.dict('os.environ', SERVER_HITS):
            state = {}
            shard.start_solo_simulation(state)
            state['hunter_landings'] = [Landing(0x1000003, shard.PLAYER_CHARACTER_ID, 1.5, 20.0)]
            shard.solo_simulation_messages(state, 1.0)
            self.assertEqual(state['combat'].health(shard.PLAYER_CHARACTER_ID),
                             shard.MARINE_HEALTH_MAX)
            messages = shard.solo_simulation_messages(state, 1.5)
        self.assertEqual(state['combat'].health(shard.PLAYER_CHARACTER_ID),
                         shard.MARINE_HEALTH_MAX - 20.0)
        self.assertEqual(state['hunter_landings'], [])
        self.assertTrue(messages)
        self.assertIn('SOLO COMBAT hunter 01000003 hit target=01000002 damage=20 health=780',
                      [c.args[0].strip() for c in log.call_args_list][-1])

    def test_rounds_that_kill_the_player_end_the_match_as_a_loss(self, _log):
        from hunter_hits import Landing
        with patch.dict('os.environ', SERVER_HITS):
            state = {}
            shard.start_solo_simulation(state)
            state['hunter_landings'] = [Landing(0x1000004, shard.PLAYER_CHARACTER_ID,
                                                1.0 + i / 100, 20.0) for i in range(40)]
            shard.solo_simulation_messages(state, 2.0)
        self.assertEqual(state['match_result']['winner_team'], 2)

    def test_server_mode_ignores_client_hunter_reports(self, log):
        with patch.dict('os.environ', dict(SERVER_HITS, CRUCIBLE_SOLO_BOT_HIT_REPORTS='1')):
            state = {}
            shard.start_solo_simulation(state)
            shard.observe_solo_combat(
                rpc(shard.HUNTER_BOT_IDS[0], 115, 4,
                    projectile_result(1, shard.PLAYER_CHARACTER_ID)), state)
        self.assertEqual(state['combat'].health(shard.PLAYER_CHARACTER_ID),
                         shard.MARINE_HEALTH_MAX)
        log.assert_not_called()

    def test_gunnery_reads_the_scale_once_and_logs_it(self, log):
        with patch.dict('os.environ', dict(SERVER_HITS, CRUCIBLE_SOLO_BOT_ACCURACY='0.5')):
            state = {}
            gunnery = shard.hunter_gunnery(state)
            self.assertIs(shard.hunter_gunnery(state), gunnery)
        self.assertEqual(gunnery.accuracy_scale, 0.5)
        self.assertIn('accuracy scale 0.50', log.call_args.args[0])


class CreatureAITickTests(unittest.TestCase):
    @patch.dict('os.environ', AI_ON)
    @patch.object(shard, 'log')
    def test_player_inside_aggro_radius_turns_the_spitter(self, _log):
        from practice_population import SPITTER_POSITIONS
        state = {}
        shard.start_solo_simulation(state)
        sx, sy, sz = SPITTER_POSITIONS[0]
        # Well past WakeUp, standing 8 m in +X of the first spitter.
        state['player_position'] = (sx + 8.0, sy, sz)
        rotations = []
        for tick in range(0, 200):
            rotations = shard.solo_simulation_messages(state, tick * 0.05)
            if rotations:
                break
        self.assertTrue(rotations, 'spitter never turned toward the player')
        message = rotations[0]
        entity, offset = shard.decode_entity_id(message.header)
        self.assertEqual(entity, 0x1000003 & 0xffffff)
        self.assertEqual(message.header[offset], shard.ENTITY_ROLE_CLIENT_SIMULATION)
        record = shard.build_replication_record(shard.location_bits(SPITTER_SHARED_TYPES))
        self.assertTrue(message.payload.startswith(record))
        self.assertEqual(len(message.payload), len(record) + 12)
        # Position half is the authored placement, untouched.
        self.assertEqual(message.payload[len(record):len(record) + 8],
                         shard.pack_practice_spawn_transform(SPITTER_POSITIONS[0])[:8])

    @patch.dict('os.environ', AI_ON)
    def test_no_player_position_means_no_rotation_traffic(self):
        state = {}
        shard.start_solo_simulation(state)
        for tick in range(0, 100):
            self.assertEqual(shard.solo_simulation_messages(state, tick * 0.05), [])


def FakeStep(entity, position, facing, state='pursue', shots=()):
    from hunter_ai import Step
    return Step(entity, position, facing, 0., 0., state, shots=shots)


class FakeSquad:
    def __init__(self):
        self.calls = []
        self.bots = []

    def update(self, now, target, target_position, *, active_entities=None):
        self.calls.append((now, target, target_position))
        steps = [FakeStep(0x1000003, (1.0, 2.0, 3.0), (0.0, 1.0, 0.0)),
                 FakeStep(0x1000004, (4.0, 5.0, 6.0), (1.0, 0.0, 0.0)),
                 FakeStep(0x1000005, (7.0, 8.0, 9.0), (0.0, -1.0, 0.0))]
        return [step for step in steps
                if active_entities is None or step.entity in active_entities]


class HunterAITickTests(unittest.TestCase):
    def test_updates_chain_counters_3_to_5_and_pass_the_player_target(self):
        squad = FakeSquad()
        messages, steps = shard.build_hunter_ai_updates(squad, 2.5, (10.0, 20.0, 30.0))
        self.assertEqual(squad.calls, [(2.5, shard.PLAYER_CHARACTER_ID, (10.0, 20.0, 30.0))])
        previous = 0
        for counter, message in zip((3, 4, 5), messages):
            actual, offset = shard.decode_entity_id(message.header, previous_id=previous)
            self.assertEqual(actual, counter)
            self.assertEqual(message.header[offset], shard.ENTITY_ROLE_CLIENT_SIMULATION)
            previous = counter
        # The fake positions are far apart and off-mesh, so separation is a
        # no-op and grounding falls back to the input: the encoding must then
        # match the accepted stateless patrol byte for byte.
        expected = shard.build_bot_location_message(3, (1.0, 2.0, 3.0), (0.0, 1.0, 0.0))
        self.assertEqual(messages[0].payload, expected.payload)


class SeparationTests(unittest.TestCase):
    def test_coincident_bots_are_pushed_to_the_authored_margin(self):
        import hunter_patrol
        same = [(10.0, 10.0, 0.0)] * 3
        out = hunter_patrol.separate(same)
        for i in range(3):
            for j in range(i + 1, 3):
                gap = math.dist(out[i][:2], out[j][:2])
                self.assertGreaterEqual(round(gap, 6), hunter_patrol.SEPARATION * 0.5,
                                        'bots %d and %d still overlapping' % (i, j))

    def test_already_separated_bots_are_left_alone(self):
        import hunter_patrol
        apart = [(0.0, 0.0, 5.0), (10.0, 0.0, 5.0), (20.0, 0.0, 5.0)]
        self.assertEqual(hunter_patrol.separate(apart), apart)

    def test_separation_is_deterministic(self):
        import hunter_patrol
        same = [(3.0, 4.0, 1.0)] * 3
        self.assertEqual(hunter_patrol.separate(same), hunter_patrol.separate(same))

    def test_separation_does_not_touch_z(self):
        import hunter_patrol
        out = hunter_patrol.separate([(0.0, 0.0, 7.5), (0.1, 0.0, 9.25)])
        self.assertEqual([p[2] for p in out], [7.5, 9.25])


class GroundingTests(unittest.TestCase):
    def test_a_point_under_the_surface_is_lifted_onto_it(self):
        import hunter_patrol, navigation, solo_modes
        mesh = navigation.installed_mesh()
        authored = solo_modes.HIVES.bot_positions[0]
        surface = mesh.locate(authored)[1][2]
        sunk = (authored[0], authored[1], surface - 1.0)
        self.assertAlmostEqual(hunter_patrol.ground(sunk)[2], surface, places=6)

    def test_grounding_preserves_x_and_y(self):
        import hunter_patrol, solo_modes
        authored = solo_modes.HIVES.bot_positions[1]
        out = hunter_patrol.ground(authored)
        self.assertAlmostEqual(out[0], authored[0])
        self.assertAlmostEqual(out[1], authored[1])

    def test_off_mesh_points_are_returned_unchanged(self):
        import hunter_patrol
        far = (-99999.0, -99999.0, 12.0)
        self.assertEqual(hunter_patrol.ground(far), far)

    def test_without_a_player_position_the_squad_gets_no_target(self):
        squad = FakeSquad()
        shard.build_hunter_ai_updates(squad, 0.0, None)
        self.assertEqual(squad.calls, [(0.0, None, None)])

    @patch.object(shard, 'log')
    def test_tick_chains_hunters_before_combat_health(self, _log):
        import combat
        state = {'squad': FakeSquad(), 'combat': combat.Combat()}
        state['combat'].register(shard.PLAYER_CHARACTER_ID, shard.MARINE_SHARED_TYPES,
                                 maximum=800.0, team=1)
        for entity in shard.HUNTER_BOT_IDS:
            state['combat'].register(entity, shard.MARINE_SHARED_TYPES,
                                     maximum=800.0, team=2)
        state['combat'].apply_reported_hit(shard.PLAYER_CHARACTER_ID, 1, (0x1000005,), now=0.0)
        messages = shard.solo_simulation_messages(state, 0.0)
        self.assertEqual(len(messages), 4)
        previous = 0
        for expected, message in zip((3, 4, 5, 5), messages):
            actual, _ = shard.decode_entity_id(message.header, previous_id=previous)
            self.assertEqual(actual, expected)
            previous = actual
        self.assertEqual(messages[-1].payload[-12:], struct.pack('>fff', 0, 800.0, 780.0))


if __name__ == '__main__':
    unittest.main()
