"""Authoritative bot state must agree with corrected positions on the wire."""
import math
import unittest
from unittest.mock import patch

import hunter_ai
import hunter_patrol
import navigation
import shard_stub as shard


class HunterReplicationTests(unittest.TestCase):
    def test_grounding_updates_bot_step_and_shot_origin_together(self):
        mesh = navigation.Mesh({0: ((-10., -10., 1.), (30., -10., 1.),
                                    (-10., 30., 1.))}, {})
        bot = hunter_ai.Bot(0x1000003, ((0., 0., 0.), (0., 10., 0.)),
                            hunter_ai.MARINE, mesh=mesh)
        squad = hunter_ai.Squad([bot])
        target = (0., 5., 1.)
        with patch('hunter_patrol.ground', return_value=(0., 0., 1.)):
            shard.build_hunter_ai_updates(squad, 0., target)
            messages, steps = shard.build_hunter_ai_updates(squad, 0.3, target)
        self.assertEqual(bot.position, (0., 0., 1.))
        self.assertEqual(steps[0].position, bot.position)
        expected_origin = (bot.position[0], bot.position[1], bot.position[2] + 1.)
        expected_target = (target[0], target[1], target[2] + 1.)
        self.assertEqual(steps[0].shots[0].origin, expected_origin)
        self.assertEqual(steps[0].shots[0].range, math.dist(expected_origin, expected_target))
        self.assertEqual(messages[0], shard.build_bot_location_message(
            3, bot.position, steps[0].facing))

    def test_entity_identity_does_not_depend_on_squad_order(self):
        bot = hunter_ai.Bot(0x1000005, ((0., 0., 0.), (0., 10., 0.)), hunter_ai.MARINE)
        with patch('hunter_patrol.ground', side_effect=lambda p: p):
            messages, _ = shard.build_hunter_ai_updates(hunter_ai.Squad([bot]), 0., None)
        entity, _ = shard.decode_entity_id(messages[0].header)
        self.assertEqual(entity, 5)

    def test_off_mesh_separation_retains_grounded_original_positions(self):
        mesh = navigation.Mesh({0: ((0., 0., 0.), (1., 0., 0.),
                                    (0., 1., 0.))}, {})
        bots = [hunter_ai.Bot(0x1000003 + i, ((.2, .2, .1), (.3, .3, 0.)),
                              hunter_ai.MARINE, mesh=mesh) for i in range(2)]
        # A two-metre margin cannot fit this surface. It must not displace
        # either authoritative bot off the mesh to satisfy the margin.
        with patch('navigation.installed_mesh', side_effect=AssertionError(
                'must use the bot mesh')):
            messages, steps = shard.build_hunter_ai_updates(
                hunter_ai.Squad(bots), 0., None)
        for bot, step in zip(bots, steps):
            self.assertEqual(bot.position, (.2, .2, 0.))
            self.assertEqual(step.position, bot.position)
            self.assertIsNotNone(mesh.locate(bot.position))
        self.assertEqual(len(messages), 2)

    def test_grounding_failure_is_isolated_and_reported_once(self):
        meshes = [navigation.Mesh({0: ((0., 0., 0.), (1., 0., 0.),
                                        (0., 1., 0.))}, {}) for _ in range(2)]
        bots = [hunter_ai.Bot(0x1000003 + i, ((.2, .2, .1), (.3, .3, 0.)),
                              hunter_ai.MARINE, mesh=mesh)
                for i, mesh in enumerate(meshes)]
        original_ground = hunter_patrol.ground

        def ground(position, mesh=None):
            if mesh is meshes[0]:
                raise RuntimeError('test projection failure')
            return original_ground(position, mesh=mesh)

        squad = hunter_ai.Squad(bots)
        with patch('hunter_patrol.ground', side_effect=ground), \
                patch('shard_stub.log') as log:
            for _ in range(2):
                messages, steps = shard.build_hunter_ai_updates(squad, 0., None)
        self.assertEqual(bots[0].position, (.2, .2, .1))
        self.assertEqual(bots[1].position, (.2, .2, 0.))
        self.assertEqual([step.position for step in steps],
                         [bot.position for bot in bots])
        self.assertEqual(len(messages), 2)
        self.assertEqual(log.call_count, 1)
        self.assertIn('01000003', log.call_args.args[0])
        self.assertIn('test projection failure', log.call_args.args[0])

    def test_meshless_bots_do_not_load_an_installed_mesh(self):
        bot = hunter_ai.Bot(0x1000003, ((0., 0., 0.), (0., 10., 0.)),
                            hunter_ai.MARINE)
        with patch('navigation.installed_mesh') as load_mesh, \
                patch('hunter_patrol.ground') as ground:
            messages, steps = shard.build_hunter_ai_updates(
                hunter_ai.Squad([bot]), 0., None)
        load_mesh.assert_not_called()
        ground.assert_not_called()
        self.assertEqual(steps[0].position, (0., 0., 0.))

    def test_active_entity_filter_reaches_the_squad(self):
        class Squad:
            bots = []

            def update(self, now, target, position, *, active_entities=None):
                self.active_entities = active_entities
                return []

        squad = Squad()
        active = {0x1000005}
        self.assertEqual(shard.build_hunter_ai_updates(
            squad, 0., None, active_entities=active), ([], []))
        self.assertEqual(squad.active_entities, active)

    def test_existing_squad_without_filter_keyword_still_works(self):
        class Squad:
            bots = []

            def update(self, now, target, position):
                return []

        self.assertEqual(shard.build_hunter_ai_updates(Squad(), 0., None), ([], []))


if __name__ == '__main__':
    unittest.main()
