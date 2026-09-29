"""Opt-in Marine fire reaches component 115 with per-shooter ring slots."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hunter_ai
import projectile_events
import shard_stub as shard


class HunterFireTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict('os.environ', {'CRUCIBLE_SOLO_MOVEMENT_ANIMATION': '0'}))
        self.shot = hunter_ai.Shot(0x1000003, shard.PLAYER_CHARACTER_ID,
                                  10., (1., 2., 3.), (0., 1., 0.), 5.)
        self.step = SimpleNamespace(entity=self.shot.shooter, state='attack',
                                    shots=(self.shot,))
        self.location = shard.build_bot_location_message(3, self.shot.origin, (0., 1., 0.))

    def messages(self, state):
        with patch.object(shard, 'log'), patch.object(
                shard, 'build_hunter_ai_updates', return_value=([self.location], [self.step])):
            return shard.solo_simulation_messages(state, 10.)

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_FIRE': '0'})
    def test_disabled_fire_preserves_location_only_output(self):
        state = {'squad': object()}
        self.assertEqual(self.messages(state), [self.location])
        self.assertNotIn('hunter_projectile_slots', state)

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_FIRE': '1'})
    def test_primary_slot_four_and_none_attachment_wrap_ten_activation_slots(self):
        state = {'squad': object()}
        for index in range(11):
            messages = self.messages(state)
            self.assertEqual(len(messages), 2)
            expected = projectile_events.build_hybrid_activation_update(
                self.shot.shooter, shard.MARINE_SHARED_TYPES, slot=index % 10,
                allowed_projectile_index=4, fire_origin=self.shot.origin,
                ideal_origin=self.shot.origin, ideal_target_position=(1., 7., 3.),
                relative_time_ms=0, has_first_update_hit=False,
                deflected=False, attachment_id=0, previous_id=3)
            self.assertEqual(messages[1], expected)
        self.assertEqual(state['hunter_projectile_slots'], {self.shot.shooter: 1})

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_FIRE': '1'})
    def test_each_shooter_gets_one_activation_in_the_same_clock_packet(self):
        from dataclasses import replace
        other = replace(self.shot, shooter=0x1000004)
        steps = [self.step, SimpleNamespace(entity=other.shooter, state='attack', shots=(other,))]
        locations = [self.location, shard.build_bot_location_message(
            4, other.origin, (0., 1., 0.), previous_id=3)]
        state = {'squad': object()}
        with patch.object(shard, 'log'), patch.object(
                shard, 'build_hunter_ai_updates', return_value=(locations, steps)):
            messages = shard.solo_simulation_messages(state, 10.)
        self.assertEqual(len(messages), 4)  # two locations, two activations
        expected = [projectile_events.build_hybrid_activation_update(
            shot.shooter, shard.MARINE_SHARED_TYPES, slot=0,
            allowed_projectile_index=4, fire_origin=shot.origin,
            ideal_origin=shot.origin, ideal_target_position=(1., 7., 3.),
            relative_time_ms=0, has_first_update_hit=False,
            deflected=False, attachment_id=0, previous_id=previous)
            for shot, previous in ((self.shot, 4), (other, 3))]
        self.assertEqual(messages[2:], expected)
        self.assertEqual(state['pending_hunter_shots'], [])

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_FIRE': '1'})
    def test_a_shooters_second_round_waits_and_keeps_its_creation_time(self):
        from dataclasses import replace
        later_round = replace(self.shot, time=10.096)
        state = {'squad': object(), 'pending_hunter_shots': [self.shot, later_round]}
        with patch.object(shard, 'log'), patch.object(
                shard, 'build_hunter_ai_updates', return_value=([], [])):
            first = shard.solo_simulation_messages(state, 10.1)
            second = shard.solo_simulation_messages(state, 10.15)
        self.assertEqual([len(first), len(second)], [1, 1])
        self.assertEqual(second[0], projectile_events.build_hybrid_activation_update(
            later_round.shooter, shard.MARINE_SHARED_TYPES, slot=1,
            allowed_projectile_index=4, fire_origin=later_round.origin,
            ideal_origin=later_round.origin, ideal_target_position=(1., 7., 3.),
            relative_time_ms=54, has_first_update_hit=False,
            deflected=False, attachment_id=0))

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_FIRE': '1'})
    def test_stalled_clock_does_not_emit_an_already_expired_projectile(self):
        state = {'squad': object(), 'pending_hunter_shots': [self.shot]}
        with patch.object(shard, 'log'), patch.object(
                shard, 'build_hunter_ai_updates', return_value=([], [])):
            self.assertEqual(shard.solo_simulation_messages(state, 10.5), [])
        self.assertEqual(state['pending_hunter_shots'], [])
        self.assertNotIn('hunter_projectile_slots', state)


if __name__ == '__main__':
    unittest.main()
