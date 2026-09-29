"""Opt-in movement animation uses observed bot speed and native 0/1 timing."""
import struct
import unittest
from unittest.mock import patch

import hunter_ai
import shard_stub as shard


class HunterAnimationTests(unittest.TestCase):
    def tick(self, state, now, speeds):
        steps = [hunter_ai.Step(entity, (0., 0., 0.), (0., 1., 0.),
                                0., speed, hunter_ai.ATTACK)
                 for entity, speed in speeds]
        locations = []
        previous = 0
        for step in steps:
            locations.append(shard.build_bot_location_message(
                step.entity & 0xffffff, step.position, step.facing, previous_id=previous))
            previous = step.entity & 0xffffff
        with patch.object(shard, 'log'), patch.object(
                shard, 'build_hunter_ai_updates', return_value=(locations, steps)):
            return shard.solo_simulation_messages(state, now)

    @patch.dict('os.environ', {}, clear=True)
    def test_default_wire_path_remains_location_only(self):
        state = {'squad': object()}
        self.assertEqual(len(self.tick(state, 0., [(0x1000003, 0.)])), 1)
        self.assertEqual(len(self.tick(state, 1., [(0x1000003, 0.)])), 1)
        self.assertNotIn('hunter_animation_idle_since_ms', state)

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MOVEMENT_ANIMATION': '1'}, clear=True)
    def test_timer_switches_from_zero_to_one_at_native_delay(self):
        state = {'squad': object()}
        for now, flag in ((10., 0.), (10.099, 0.), (10.1, 1.), (400., 1.)):
            messages = self.tick(state, now, [(0x1000003, 0.)])
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[-1].payload[-4:], struct.pack('>f', flag))

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MOVEMENT_ANIMATION': '1'}, clear=True)
    def test_moving_bot_resets_delay_independently_of_stationary_bot(self):
        state = {'squad': object()}
        for now in (0., .1, .2, .3):
            messages = self.tick(state, now, [(0x1000003, 5.5), (0x1000004, 0.)])
            self.assertEqual(messages[-2].payload[-4:], struct.pack('>f', 0.))
            self.assertEqual(messages[-1].payload[-4:], struct.pack('>f', float(now >= .1)))
        for now, flag in ((.399, 0.), (.4, 1.), (.5, 0.)):
            speed = 5.5 if now == .5 else 0.
            messages = self.tick(state, now, [(0x1000003, speed)])
            self.assertEqual(messages[-1].payload[-4:], struct.pack('>f', flag))

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MOVEMENT_ANIMATION': '1'}, clear=True)
    def test_absent_dead_bot_has_no_animation_and_revival_starts_fresh(self):
        state = {'squad': object()}
        self.tick(state, 0., [(0x1000003, 0.)])
        self.tick(state, 1., [(0x1000003, 0.)])
        self.assertEqual(self.tick(state, 2., []), [])
        self.assertEqual(state['hunter_animation_idle_since_ms'], {})
        messages = self.tick(state, 100., [(0x1000003, 0.)])
        self.assertEqual(messages[-1].payload[-4:], struct.pack('>f', 0.))

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MOVEMENT_ANIMATION': '1'}, clear=True)
    def test_animation_headers_chain_after_all_location_updates(self):
        import animation_events
        state = {'squad': object()}
        messages = self.tick(state, 0., [(0x1000003, 0.), (0x1000005, 0.)])
        previous = 0
        for message, expected in zip(messages, (3, 5, 3, 5)):
            actual, _ = shard.decode_entity_id(message.header, previous_id=previous)
            self.assertEqual(actual, expected)
            previous = actual
        expected = animation_events.build_animation_update(
            0x1000003, shard.MARINE_SHARED_TYPES,
            values={'time_since_movement_input': 0.}, previous_id=5)
        self.assertEqual(messages[2], expected)

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MOVEMENT_ANIMATION': '1'}, clear=True)
    def test_disable_clears_policy_state_and_restores_baseline(self):
        state = {'squad': object()}
        self.tick(state, 0., [(0x1000003, 0.)])
        with patch.dict('os.environ', {'CRUCIBLE_SOLO_MOVEMENT_ANIMATION': '0'}):
            self.assertEqual(len(self.tick(state, 1., [(0x1000003, 0.)])), 1)
            self.assertNotIn('hunter_animation_idle_since_ms', state)

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MOVEMENT_ANIMATION': '1',
                              'CRUCIBLE_SOLO_FIRE': '1'}, clear=True)
    def test_animation_fire_and_health_preserve_nonzero_relative_id_chain(self):
        import combat
        from dataclasses import replace
        arena = combat.Combat()
        for entity in (shard.PLAYER_CHARACTER_ID, 0x1000003, 0x1000005):
            arena.register(entity, shard.MARINE_SHARED_TYPES, maximum=800.,
                           team=1 if entity == shard.PLAYER_CHARACTER_ID else 2)
        arena.entities[0x1000003].dirty = True
        shot = hunter_ai.Shot(0x1000003, shard.PLAYER_CHARACTER_ID,
                              10., (0., 0., 0.), (0., 1., 0.), 5.)
        first = hunter_ai.Step(0x1000003, shot.origin, shot.direction,
                               0., 0., hunter_ai.ATTACK, shots=(shot,))
        second = replace(first, entity=0x1000005, shots=())
        locations = [shard.build_bot_location_message(3, shot.origin, shot.direction,
                                                       previous_id=7),
                     shard.build_bot_location_message(5, shot.origin, shot.direction,
                                                       previous_id=3)]
        state = {'squad': object(), 'combat': arena}
        with patch.object(shard, 'log'), patch.object(
                shard, 'build_hunter_ai_updates', return_value=(locations, [first, second])):
            messages = shard.solo_simulation_messages(state, 10., previous_id=7)
        self.assertEqual(len(messages), 6)
        previous = 7
        for message, expected in zip(messages, (3, 5, 3, 5, 3, 3)):
            actual, _ = shard.decode_entity_id(message.header, previous_id=previous)
            self.assertEqual(actual, expected)
            previous = actual
        self.assertEqual(messages[-1].payload[-12:], struct.pack('>fff', 0., 800., 800.))


if __name__ == '__main__':
    unittest.main()
