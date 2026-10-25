import math
import unittest
from unittest.mock import patch

import hunter_ai
import hunter_patrol
import navigation
import shard_stub as shard
import solo_modes

STUB_ROUTE = ((0., 0., 0.), (3., 0., 0.))


class HunterSeparationTests(unittest.TestCase):
    def assert_margin(self, positions, distance=2.):
        for index, point in enumerate(positions):
            self.assertTrue(all(math.isfinite(value) for value in point))
            for other in positions[index + 1:]:
                self.assertGreaterEqual(math.dist(point[:2], other[:2]),
                                        distance - 1e-6)

    def test_coincident_pair_resolves_in_one_pass(self):
        points = [(10., 10., 1.), (10., 10., 2.)]
        separated = hunter_patrol.separate(points, passes=1)
        self.assert_margin(separated)
        self.assertEqual([point[2] for point in separated], [1., 2.])
        self.assertEqual(points, [(10., 10., 1.), (10., 10., 2.)])

    def test_three_bots_reach_the_full_authored_margin(self):
        cases = (
            [(10., 10., 1.)] * 3,
            [(0., 0., 0.), (1., 0., 0.), (2., 0., 0.)],
            [(0., 0., 0.), (.2, .3, 0.), (.4, -.1, 0.)],
        )
        for points in cases:
            with self.subTest(points=points):
                separated = hunter_patrol.separate(points)
                self.assert_margin(separated)
                self.assertEqual(separated, hunter_patrol.separate(points))
                for axis in (0, 1):
                    self.assertAlmostEqual(sum(p[axis] for p in separated),
                                           sum(p[axis] for p in points))

    def test_near_coincident_pair_keeps_its_existing_direction(self):
        points = [(0., 0., 0.), (-1e-8, 0., 0.)]
        separated = hunter_patrol.separate(points, passes=1)
        self.assert_margin(separated)
        self.assertGreater(separated[0][0], separated[1][0])

    def test_mesh_projection_preserves_walkable_positions(self):
        mesh = navigation.Mesh({
            0: ((-5., -5., 0.), (5., -5., 0.), (5., 5., 0.)),
            1: ((-5., -5., 0.), (5., 5., 0.), (-5., 5., 0.)),
        }, {})
        separated = hunter_patrol.separate(
            [(0., 0., 0.), (.25, 0., 0.)], mesh=mesh)
        self.assert_margin(separated)
        self.assertTrue(all(mesh.locate(point) is not None for point in separated))

    def test_one_sided_push_resolves_when_only_one_side_has_walkable_space(self):
        mesh = navigation.Mesh({
            0: ((0., 0., 0.), (5., 0., 0.), (5., 5., 0.)),
            1: ((0., 0., 0.), (5., 5., 0.), (0., 5., 0.)),
        }, {})
        separated = hunter_patrol.separate(
            [(.1, .2, 0.), (.3, .2, 0.)], mesh=mesh)
        self.assert_margin(separated)
        self.assertTrue(all(mesh.locate(point) is not None for point in separated))


class HunterGroundingTests(unittest.TestCase):
    def test_grounding_follows_the_surface_between_convex_waypoints(self):
        # A ridge at x=1 lies above the straight path joining x=0 and x=2.
        mesh = navigation.Mesh({
            0: ((0., 0., 0.), (1., 0., 1.), (1., 1., 1.)),
            1: ((1., 0., 1.), (2., 0., 0.), (1., 1., 1.)),
        }, {})
        for x in (.25, .75, 1., 1.25, 1.75):
            point = (x, .1, 0.)
            grounded = hunter_patrol.ground(point, mesh)
            self.assertEqual(grounded[:2], point[:2])
            self.assertAlmostEqual(grounded[2], 1. - abs(1. - x))

    def test_grounding_does_not_bridge_gaps_or_large_vertical_differences(self):
        mesh = navigation.Mesh({0: ((0., 0., 0.), (2., 0., 0.), (0., 2., 0.))}, {})
        for point in ((3., 3., 0.), (.5, .5, 4.)):
            self.assertEqual(hunter_patrol.ground(point, mesh), point)


class HunterPatrolTests(unittest.TestCase):
    @patch('hunter_patrol.routes', return_value=(STUB_ROUTE,) * 3)
    def test_native_update_preserves_spawn_property_mask_and_faces_motion(self, _routes):
        baseline = shard.build_practice_character_probe(autonomous=False).payload[:-12]
        # One time on the outbound leg and one after the route reverses, so the
        # decoded rotation is checked against both travel directions.
        for seconds in (.1, .8):
            _, direction = hunter_patrol.sample(STUB_ROUTE, seconds)
            previous = 0
            for counter, message in enumerate(shard.build_hunter_patrol_updates(seconds), 3):
                actual, offset = shard.decode_entity_id(message.header, previous_id=previous)
                self.assertEqual(actual, counter)
                self.assertEqual(message.header[offset], shard.ENTITY_ROLE_CLIENT_SIMULATION)
                self.assertEqual(message.payload[:-12], baseline)
                packed = int.from_bytes(message.payload[-4:], 'big')
                largest = packed >> 30
                remaining = [(((packed >> s) & 1023)-511.5)/723.3699951171875
                             for s in (20, 10, 0)]
                remaining.insert(largest, math.sqrt(1-sum(v*v for v in remaining)))
                w, x, y, z = remaining
                # Quaternion rotates the native +Y forward onto the travel axis.
                self.assertAlmostEqual(2*(x*y-w*z), math.copysign(1., direction[0]),
                                       delta=.005)
                previous = actual

    def test_constant_speed_across_corners_and_return(self):
        route = [(0, 0, 0), (3, 0, 0), (3, 4, 0)]
        self.assertEqual(hunter_patrol.sample(route, 2, speed=2)[0], (3, 1, 0))
        self.assertEqual(hunter_patrol.sample(route, 5, speed=2)[0], (3, 1, 0))
        self.assertEqual(hunter_patrol.sample(route, 7, speed=2)[0], (0, 0, 0))

    def test_default_speed_is_the_authored_marine_walk(self):
        self.assertEqual(hunter_ai.MARINE.patrol_speed, 5.5)
        route = [(0., 0., 0.), (100., 0., 0.)]
        self.assertAlmostEqual(hunter_patrol.sample(route, 2.)[0][0], 11.)

    def test_invalid_paths_and_times_rejected(self):
        for route, time in [([], 1), ([(0,0,0)], 1), ([(0,0,0),(1,0,0)], -1)]:
            with self.assertRaises(ValueError):
                hunter_patrol.sample(route, time)
        with self.assertRaises(ValueError):
            hunter_patrol.sample(STUB_ROUTE, 1, speed=0)

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE':'alpha-hunters',
                              'CRUCIBLE_SOLO_PATROL':'1',
                              'CRUCIBLE_SHARD_BOOTSTRAP':'practice-character'})
    @patch('hunter_patrol.routes', return_value=(STUB_ROUTE,) * 3)
    def test_clock_sends_only_spawned_bot_movement(self, _routes):
        import lz4.block
        state={'character_links_sent':True, 'clock_epoch':100,
               'clock_last_sent':100, 'seq':1, 'received_seq':1,'received_window':1}
        raw=lz4.block.decompress(shard.game_clock_reply(state,now=101)[1:],uncompressed_size=4096)
        self.assertEqual(int.from_bytes(raw[15:17],'big'),0)
        state['practice_npcs_sent']=True
        raw=lz4.block.decompress(shard.game_clock_reply(state,now=102)[1:],uncompressed_size=4096)
        self.assertEqual(int.from_bytes(raw[15:17],'big'),3)


class TransformTests(unittest.TestCase):
    def test_position_unpack_inverts_the_retail_packer(self):
        import solo_modes
        for position in (solo_modes.HIVES.player_position,
                         solo_modes.PRACTICE.player_position,
                         (0., 0., 0.), (-4499., 4499., -1299.)):
            packed = shard.pack_practice_spawn_transform(position)
            for blob in (packed, packed[:8]):
                for actual, expected in zip(hunter_patrol.unpack_position(blob), position):
                    self.assertAlmostEqual(actual, expected, delta=.003)

    def test_position_unpack_rejects_wrong_lengths(self):
        for blob in (b'', b'\0' * 7, b'\0' * 9, b'\0' * 13):
            with self.assertRaises(ValueError):
                hunter_patrol.unpack_position(blob)


class InstalledRouteTests(unittest.TestCase):
    """Routes over the shipped mesh. Read-only; nothing is launched."""

    @classmethod
    def setUpClass(cls):
        if not navigation.LEVEL_PAK.exists():
            raise unittest.SkipTest('Crucible is not installed')

    def test_every_route_sample_lies_on_the_installed_surface(self):
        mesh = navigation.installed_mesh()
        legs = hunter_patrol.routes()
        self.assertEqual(len(legs), 3)
        for leg in legs:
            self.assertGreaterEqual(len(leg), 2)
            for point in leg:
                self.assertIsNotNone(mesh.locate(point, .05), point)
            for a, b in zip(leg, leg[1:]):
                self.assertLessEqual(math.dist(a[:2], b[:2]),
                                     hunter_patrol.ROUTE_STEP + 1e-4)

    def test_circuits_close_and_visit_every_authored_drop_point(self):
        legs = hunter_patrol.routes()
        for index, circuit in enumerate(hunter_patrol.circuits()):
            self.assertEqual(circuit[0], legs[index][0])
            self.assertEqual(circuit[-1], legs[index][0])
            self.assertGreater(hunter_ai.plan_distance(circuit),
                               max(hunter_ai.plan_distance(leg) for leg in legs))

    def test_squad_builds_three_marine_bots_on_the_mesh(self):
        squad = hunter_patrol.squad()
        self.assertEqual([bot.entity for bot in squad.bots],
                         [0x01000003, 0x01000004, 0x01000005])
        mesh = navigation.installed_mesh()
        for bot in squad.bots:
            self.assertIs(bot.tuning, hunter_ai.MARINE)
            self.assertTrue(bot.wander)
            self.assertIsNotNone(mesh.locate(bot.position, .05))
        steps = squad.update(0.)
        steps = squad.update(2.)
        for step in steps:
            self.assertEqual(step.state, hunter_ai.PATROL)
            self.assertIsNotNone(mesh.locate(step.position, .05), step.position)
            self.assertLessEqual(step.speed, hunter_ai.MARINE.patrol_speed + 1e-6)


class LevelRouteTests(unittest.TestCase):
    """Each map's own mesh and bot points (docs/37 stage 2). Read-only."""

    @classmethod
    def setUpClass(cls):
        if not navigation.level_pak('HotH_02').exists():
            raise unittest.SkipTest('HotH_02 is not installed')

    def test_hoth_02_ships_april2018s_navigation_mesh(self):
        # mnmnavmission0.bai is byte-identical in both paks (docs/37), so
        # retail's guide bots walked April2018's mesh on HotH_02.
        hoth = navigation.installed_mesh('HotH_02')
        self.assertIsNot(hoth, navigation.installed_mesh())
        self.assertEqual(hoth.triangles, navigation.installed_mesh().triangles)

    def test_every_spelling_of_a_level_shares_one_entry(self):
        self.assertIs(hunter_patrol.routes(), hunter_patrol.routes('april2018'))
        self.assertIs(hunter_patrol.routes(None), hunter_patrol.routes(level='APRIL2018'))
        self.assertIs(hunter_patrol.circuits('HotH_02'), hunter_patrol.circuits('hoth_02'))
        self.assertEqual(hunter_patrol.level_key('hoth_02'), 'HotH_02')
        with self.assertRaises(KeyError):
            hunter_patrol.level_key('nowhere')

    def test_hoth_02_hunters_start_on_their_guide_nodes(self):
        mesh = navigation.installed_mesh('HotH_02')
        nodes = dict(solo_modes.HOTH_02_GUIDE_BOT_SPAWNS)
        squad = hunter_patrol.squad(level='hoth_02')
        self.assertEqual(len(squad.bots), 3)
        for bot, entry in zip(squad.bots, solo_modes.HOTH_02_BOT_NODES):
            self.assertIs(bot.mesh, mesh)
            self.assertLess(math.dist(bot.position, nodes[entry]), 1.)
            self.assertIsNotNone(mesh.locate(bot.position, .05))

    def test_the_bot_nodes_are_the_routable_guide_nodes_nearest_the_drop(self):
        mesh = navigation.installed_mesh('HotH_02')
        lengths = {}
        for entry, node in solo_modes.HOTH_02_GUIDE_BOT_SPAWNS:
            path = mesh.path(solo_modes.HOTH_02.player_position, node)
            if path:
                lengths[entry] = hunter_ai.plan_distance(path)
        self.assertEqual(sorted(set(range(33, 45)) - set(lengths)), [33, 34, 41, 42])
        self.assertEqual(tuple(sorted(lengths, key=lengths.get)[:3]),
                         solo_modes.HOTH_02_BOT_NODES)

    def test_every_selection_with_hunters_routes_unless_listed(self):
        # The tutorials' bot points are creature and dummy nodes, mostly off
        # their small mesh (the practice arena's, shared): their hunters
        # stand where they spawn.
        squadless = {'tutorial_02', 'tutorial_03'}
        for name, pair in solo_modes.SELECTIONS.items():
            profile = solo_modes.compose(*pair)
            if not profile.bot_positions:
                continue
            with self.subTest(selection=name):
                if profile.level in squadless:
                    with self.assertRaises(ValueError):
                        hunter_patrol.circuits(profile.level)
                else:
                    self.assertEqual(len(hunter_patrol.circuits(profile.level)), 3)


if __name__ == '__main__':
    unittest.main()
