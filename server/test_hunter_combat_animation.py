"""Opt-in combat animation: is_dead, and the primary held for each attack."""
import struct
import unittest
from unittest.mock import patch

import animation_events
import combat
import hunter_ai
import shard_stub as shard


BOTS = (0x1000003, 0x1000004, 0x1000005)


def arena_with_bots():
    arena = combat.Combat()
    arena.register(shard.PLAYER_CHARACTER_ID, shard.MARINE_SHARED_TYPES,
                   maximum=800., team=1)
    for entity in BOTS:
        arena.register(entity, shard.MARINE_SHARED_TYPES, maximum=800., team=2)
    return arena


def expected(entity, previous_id, **values):
    return animation_events.build_animation_update(
        entity, shard.MARINE_SHARED_TYPES, values=values, previous_id=previous_id)


class HunterCombatAnimationTests(unittest.TestCase):
    def tick(self, state, now, bots):
        """`bots` is (entity, state, firing) for each bot the squad stepped."""
        steps = []
        for entity, bot_state, firing in bots:
            shots = ((hunter_ai.Shot(entity, shard.PLAYER_CHARACTER_ID, now,
                                     (0., 0., 0.), (0., 1., 0.), 5.),) if firing else ())
            steps.append(hunter_ai.Step(entity, (0., 0., 0.), (0., 1., 0.), 0., 0.,
                                        bot_state, shots=shots, firing=firing))
        locations = []
        previous = 0
        for step in steps:
            locations.append(shard.build_bot_location_message(
                step.entity & 0xffffff, step.position, step.facing, previous_id=previous))
            previous = step.entity & 0xffffff
        with patch.object(shard, 'log'), patch.object(
                shard, 'build_hunter_ai_updates', return_value=(locations, steps)):
            return shard.solo_simulation_messages(state, now)[len(locations):]

    @patch.dict('os.environ', {}, clear=True)
    def test_disabled_policy_adds_no_animation_even_for_dead_bot(self):
        arena = arena_with_bots()
        arena.entities[0x1000004].health = 0.
        state = {'squad': object(), 'combat': arena}
        messages = self.tick(state, 0., [(0x1000003, hunter_ai.ATTACK, True)])
        self.assertEqual(messages, [])

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT_ANIMATION': '1'}, clear=True)
    def test_primary_and_aim_ik_are_held_for_the_whole_attack(self):
        # marine_primary_onfire.cdl sets both on entry and clears the primary
        # in OnExit, so they follow the attack, not individual rounds.
        state = {'squad': object(), 'combat': arena_with_bots()}
        for now in (0., .05, .1, 1.95):
            held = self.tick(state, now, [(0x1000003, hunter_ai.ATTACK, True)])
            self.assertEqual(held, [expected(0x1000003, 3, is_dead=False,
                                             using_primary1=True, using_aim_ik=True)])
        released = self.tick(state, 2., [(0x1000003, hunter_ai.ATTACK, False)])
        self.assertEqual(released, [expected(0x1000003, 3, is_dead=False,
                                             using_primary1=False, using_aim_ik=False)])

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT_ANIMATION': '1',
                               'CRUCIBLE_SOLO_BOT_RELOAD': '1'}, clear=True)
    def test_reload_flag_publishes_using_reload_for_live_and_dead_bots(self):
        arena = arena_with_bots()
        arena.entities[0x1000004].health = 0.
        state = {'squad': object(), 'combat': arena}
        step = hunter_ai.Step(0x1000003, (0., 0., 0.), (0., 1., 0.), 0., 0.,
                              hunter_ai.ATTACK, reloading=True)
        location = shard.build_bot_location_message(3, step.position, step.facing)
        with patch.object(shard, 'log'), patch.object(
                shard, 'build_hunter_ai_updates', return_value=([location], [step])):
            messages = shard.solo_simulation_messages(state, 0.)[1:]
        self.assertEqual(messages, [
            expected(0x1000003, 3, is_dead=False, using_primary1=False,
                     using_aim_ik=False, using_reload=True),
            expected(0x1000004, 3, is_dead=True, using_primary1=False,
                     using_aim_ik=False, using_reload=False)])

    def test_reload_flag_loads_each_hunter_magazine(self):
        import hunter_patrol
        route = ((0., 0., 0.), (1., 0., 0.))
        for flags, ammo in (({}, None), ({'CRUCIBLE_SOLO_BOT_RELOAD': '1'}, 24)):
            squad = hunter_ai.Squad([hunter_ai.Bot(e, route, hunter_ai.MARINE)
                                     for e in BOTS])
            env = dict(flags, CRUCIBLE_SOLO_AI='1', CRUCIBLE_SOLO_MODE='alpha-hunters')
            with self.subTest(flags=flags), patch.dict('os.environ', env, clear=True), \
                    patch.object(hunter_patrol, 'squad', return_value=squad):
                state = {}
                shard.start_solo_simulation(state)
                self.assertEqual([bot.ammo for bot in state['squad'].bots], [ammo] * 3)

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT_ANIMATION': '1'}, clear=True)
    def test_a_bot_that_is_chasing_while_its_attack_runs_keeps_firing(self):
        state = {'squad': object(), 'combat': arena_with_bots()}
        messages = self.tick(state, 0., [(0x1000003, hunter_ai.PURSUE, True)])
        self.assertEqual(messages, [expected(0x1000003, 3, is_dead=False,
                                             using_primary1=True, using_aim_ik=True)])

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT_ANIMATION': '1'}, clear=True)
    def test_bots_never_aim_down_sights(self):
        state = {'squad': object(), 'combat': arena_with_bots()}
        for bot_state in (hunter_ai.PATROL, hunter_ai.PURSUE, hunter_ai.ATTACK,
                          hunter_ai.RETURN):
            message, = self.tick(state, 0., [(0x1000003, bot_state, False)])
            self.assertEqual(message, expected(0x1000003, 3, is_dead=False,
                                               using_primary1=False, using_aim_ik=False))

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT_ANIMATION': '1'}, clear=True)
    def test_dead_bot_repeats_is_dead_until_respawn_clears_it(self):
        arena = arena_with_bots()
        state = {'squad': object(), 'combat': arena}
        arena.entities[0x1000004].health = 0.
        arena.entities[0x1000004].respawn_at = 5.
        live = [(0x1000003, hunter_ai.ATTACK, True), (0x1000005, hunter_ai.PATROL, False)]
        for now in (1., 1.05):
            messages = self.tick(state, now, live)
            # Health is not dirty here, so the list is animation only.
            self.assertEqual(messages, [
                expected(0x1000003, 5, is_dead=False, using_primary1=True, using_aim_ik=True),
                expected(0x1000005, 3, is_dead=False, using_primary1=False, using_aim_ik=False),
                expected(0x1000004, 5, is_dead=True, using_primary1=False, using_aim_ik=False)])
        revived = self.tick(state, 5., live + [(0x1000004, hunter_ai.PATROL, False)])
        self.assertTrue(arena.alive(0x1000004))
        # Respawn marks health dirty: three animation updates, then health.
        self.assertEqual(revived[:3], [
            expected(0x1000003, 4, is_dead=False, using_primary1=True, using_aim_ik=True),
            expected(0x1000005, 3, is_dead=False, using_primary1=False, using_aim_ik=False),
            expected(0x1000004, 5, is_dead=False, using_primary1=False, using_aim_ik=False)])
        self.assertEqual(revived[3].payload[-12:], struct.pack('>fff', 0., 800., 800.))

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT_ANIMATION': '1'}, clear=True)
    def test_dead_bot_still_stepped_is_reported_dead_not_firing(self):
        arena = arena_with_bots()
        arena.entities[0x1000003].health = 0.
        state = {'squad': object(), 'combat': arena}
        messages = self.tick(state, 0., [(0x1000003, hunter_ai.ATTACK, True)])
        self.assertEqual(messages, [expected(0x1000003, 3, is_dead=True,
                                             using_primary1=False, using_aim_ik=False)])

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT_ANIMATION': '1'}, clear=True)
    def test_without_combat_no_bot_is_dead(self):
        state = {'squad': object()}
        messages = self.tick(state, 0., [(0x1000003, hunter_ai.PATROL, False)])
        self.assertEqual(messages, [expected(0x1000003, 3, is_dead=False,
                                             using_primary1=False, using_aim_ik=False)])

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_COMBAT_ANIMATION': '1',
                              'CRUCIBLE_SOLO_MOVEMENT_ANIMATION': '1'}, clear=True)
    def test_movement_and_combat_share_one_update_with_literal_packing(self):
        state = {'squad': object(), 'combat': arena_with_bots()}
        self.tick(state, 0., [(0x1000003, hunter_ai.ATTACK, False)])
        messages = self.tick(state, .1, [(0x1000003, hunter_ai.ATTACK, True)])
        self.assertEqual(messages, [expected(
            0x1000003, 3, time_since_movement_input=1., is_dead=False,
            using_primary1=True, using_aim_ik=True)])
        # Records 4, 8 and 20 are the bools (is_dead, using_primary1,
        # using_aim_ik); their cursor byte is reserved before record 16's float.
        self.assertEqual(messages[0].payload[-5:], b'\x06' + struct.pack('>f', 1.))


if __name__ == '__main__':
    unittest.main()
