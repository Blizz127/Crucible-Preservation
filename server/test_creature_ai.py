"""Authored-value creature AI checks. Unit tests, not live verification."""
import math
import unittest

import creature_ai as ai
import practice_population as population


class ProfileTests(unittest.TestCase):
    def test_spitter_profile_matches_the_installed_ai_asset(self):
        profile = ai.SPITTER
        self.assertEqual(profile.ai_asset, 'ai/spitter.ai')
        self.assertFalse(profile.movement_enabled)
        self.assertFalse(profile.can_patrol)
        self.assertEqual((profile.aggro_radius_near, profile.aggro_radius_far),
                         (5.0, 20.0))
        self.assertEqual(profile.deaggro_radius, 25.0)
        self.assertEqual(profile.attack_delay_for_new_targets, 1.0)
        self.assertEqual((profile.attack.range_min, profile.attack.range_max),
                         (0.1, 40.0))
        self.assertEqual(profile.attack.duration, 3.0)
        self.assertEqual(profile.health_max, 400.0)

    def test_attack_script_timeline_comes_from_the_attack_cdl(self):
        script = ai.SPITTER.script
        self.assertEqual(script.windup_seconds, 1.0)
        self.assertEqual(script.aim_to_fire_seconds, 0.1)
        self.assertAlmostEqual(script.fire_seconds, 1.1)
        self.assertAlmostEqual(script.alert_seconds, 2.35)
        self.assertEqual(script.alert_radius, 10.0)
        self.assertEqual(script.muzzle_offset_z, 2.75)
        self.assertLess(script.alert_seconds, ai.SPITTER.attack.duration)

    def test_megadrone_and_snurtle_attack_scripts_are_traced(self):
        megadrone = ai.MEGADRONE.script
        self.assertEqual(megadrone.kind, 'projectile')
        self.assertAlmostEqual(megadrone.fire_seconds, 0.65)
        self.assertEqual(megadrone.target_offset_z, 1.0)
        # The megadrone attack graph raises no AIAlertedEvent of its own.
        self.assertIsNone(megadrone.alert_seconds)
        snurtle = ai.SNURTLE.script
        self.assertEqual(snurtle.kind, 'melee')
        self.assertAlmostEqual(snurtle.fire_seconds, 1.1)
        self.assertEqual(snurtle.radius, 8.0)
        for profile in (ai.MEGADRONE, ai.SNURTLE):
            self.assertLess(profile.script.fire_seconds, profile.attack.duration)

    def test_a_melee_attack_reports_its_area_instead_of_a_projectile(self):
        creature = ai.Creature(7, ai.SNURTLE, (0.0, 0.0, 0.0))
        target = {9: (0.0, 5.0, 0.0)}
        creature.step(0.0, target)          # AttackDelayForNewTargets is 0.0
        self.assertEqual(creature.state, ai.ATTACKING)
        # The authored delays sum to just over 1.1 seconds in float32 terms.
        self.assertEqual(creature.step(1.1, target), [])
        events = creature.step(1.15, target)
        shots = [event for event in events if isinstance(event, ai.CreatureAttack)]
        self.assertEqual(len(shots), 1)
        self.assertEqual((shots[0].kind, shots[0].radius), ('melee', 8.0))


def spitter(position=(0.0, 0.0, 0.0)):
    return ai.Creature(0x1000003, ai.SPITTER, position)


class AggroTests(unittest.TestCase):
    def test_idle_until_a_player_enters_the_authored_far_radius(self):
        creature = spitter()
        self.assertEqual(creature.state, ai.IDLE)
        self.assertEqual(creature.step(0.0, {9: (0.0, 25.0, 0.0)}), [])
        self.assertEqual(creature.state, ai.IDLE)
        events = creature.step(1.0, {9: (0.0, 19.0, 0.0)})
        self.assertEqual([event.kind for event in events][0], 'aggro')
        self.assertEqual(creature.state, ai.AGGRO)
        self.assertEqual(creature.target, 9)

    def test_near_radius_ignores_the_facing_cone(self):
        creature = spitter()
        creature.yaw = math.pi  # facing away from the target
        narrow = ai.replace(ai.SPITTER, aggro_angle_far_degrees=30.0)
        creature.profile = narrow
        self.assertEqual(creature.step(0.0, {9: (0.0, 15.0, 0.0)}), [])
        events = creature.step(1.0, {9: (0.0, 4.0, 0.0)})
        self.assertEqual(events[0].kind, 'aggro')

    def test_line_of_sight_gate_is_honoured_when_supplied(self):
        creature = spitter()
        blind = creature.step(0.0, {9: (0.0, 4.0, 0.0)}, visible=lambda c, e: False)
        self.assertEqual(blind, [])
        self.assertEqual(creature.state, ai.IDLE)

    def test_target_is_dropped_past_the_authored_deaggro_radius(self):
        creature = spitter()
        creature.step(0.0, {9: (0.0, 4.0, 0.0)})
        events = creature.step(0.5, {9: (0.0, 26.0, 0.0)})
        self.assertEqual(events[-1].kind, 'deaggro')
        self.assertEqual(creature.state, ai.IDLE)
        self.assertEqual(creature.target, 0)

    def test_a_target_that_stops_being_reported_is_dropped(self):
        # shard_stub has no player position until the first client input
        # arrives; an empty mapping must deaggro, never freeze the target.
        creature = spitter()
        creature.step(0.0, {9: (0.0, 4.0, 0.0)})
        events = creature.step(0.5, {})
        self.assertEqual(events[-1].kind, 'deaggro')
        self.assertEqual((creature.state, creature.target), (ai.IDLE, 0))

    def test_a_target_lost_mid_attack_terminates_the_attack(self):
        creature = spitter()
        creature.step(0.0, {9: (0.0, 10.0, 0.0)})
        creature.step(1.0, {9: (0.0, 10.0, 0.0)})
        self.assertEqual(creature.state, ai.ATTACKING)
        events = creature.step(1.5, {})
        self.assertEqual(events[-1].kind, 'attack_terminated')
        self.assertEqual(creature.state, ai.AGGRO)

    def test_damage_forces_aggro_for_the_authored_duration(self):
        creature = spitter()
        event = creature.note_damage(0.0, 7)
        self.assertEqual((event.kind, event.target_id), ('aggro', 7))
        # 30 metres is outside DeaggroRadius 25 but inside the 4 second
        # forced window - and still inside the authored attack range of 40.
        creature.step(0.5, {7: (0.0, 30.0, 0.0)})
        self.assertEqual(creature.target, 7)
        creature.step(1.0, {7: (0.0, 30.0, 0.0)})
        self.assertEqual(creature.state, ai.ATTACKING)
        creature.step(4.0, {7: (0.0, 30.0, 0.0)})  # attack ends, window over
        events = creature.step(4.5, {7: (0.0, 30.0, 0.0)})
        self.assertEqual(events[-1].kind, 'deaggro')


class AttackTests(unittest.TestCase):
    def run_attack(self, times, position=(0.0, 10.0, 0.0)):
        creature = spitter()
        seen = []
        for now in times:
            seen.extend(creature.step(now, {9: position}))
        return creature, seen

    def test_first_attack_waits_attack_delay_for_new_targets(self):
        creature, events = self.run_attack([0.0, 0.5, 0.9])
        self.assertNotIn('attack_started', [event.kind for event in events
                                            if hasattr(event, 'kind')])
        events = creature.step(1.0, {9: (0.0, 10.0, 0.0)})
        self.assertEqual(events[0].kind, 'attack_started')

    def test_projectile_fires_at_the_scripted_offset_and_muzzle_height(self):
        # Aggro at 0.0, attack starts at 1.0 (AttackDelayForNewTargets), and
        # the script fires 1.1 seconds into the attack.
        times = [0.0, 1.0, 2.0, 2.1]
        creature, events = self.run_attack(times)
        shots = [event for event in events if isinstance(event, ai.CreatureAttack)]
        self.assertEqual(len(shots), 1)
        shot = shots[0]
        self.assertAlmostEqual(shot.time, 2.1)
        self.assertEqual((shot.creature_id, shot.target_id), (0x1000003, 9))
        self.assertEqual(shot.origin, (0.0, 0.0, 2.75))
        self.assertEqual((shot.kind, shot.attack_asset),
                         ('projectile',
                          'assets/creature/spitter/spitter_projectile.projectile'))

    def test_attack_alerts_then_ends_on_the_authored_cadence(self):
        # Aggro 0.0, attack 1.0..4.0, fire 2.1, nearby-player alert 3.35.
        times = [0.0, 1.0, 2.1, 3.35, 4.0, 4.1, 5.2]
        creature, events = self.run_attack(times)
        kinds = [(event.kind, round(event.time, 2)) for event in events
                 if isinstance(event, ai.CreatureEvent)]
        self.assertIn(('alert_nearby', 3.35), kinds)
        self.assertIn(('attack_ended', 4.0), kinds)
        # MinTimeBetweenAttacks is 0.0: the next tick starts another attack.
        self.assertIn(('attack_started', 4.1), kinds)
        shots = [event for event in events if isinstance(event, ai.CreatureAttack)]
        self.assertEqual([round(shot.time, 2) for shot in shots], [2.1, 5.2])

    def test_targets_inside_the_authored_minimum_range_are_not_attacked(self):
        # RangeMin is 0.1; a target closer than that aggroes but cannot be hit.
        creature, events = self.run_attack([0.0, 1.0, 2.0], position=(0.0, 0.05, 0.0))
        self.assertEqual(creature.state, ai.AGGRO)
        self.assertEqual([event.kind for event in events].count('attack_started'), 0)

    def test_rotation_is_locked_for_the_authored_period_then_tracks(self):
        creature = spitter()
        creature.step(0.0, {9: (0.0, 10.0, 0.0)})
        creature.step(1.0, {9: (0.0, 10.0, 0.0)})
        self.assertEqual(creature.state, ai.ATTACKING)
        locked = creature.yaw
        creature.step(1.2, {9: (10.0, 0.0, 0.0)})
        self.assertEqual(creature.yaw, locked)  # inside RotationDisallowed 0.5
        creature.step(1.6, {9: (10.0, 0.0, 0.0)})
        self.assertNotEqual(creature.yaw, locked)

    def test_clock_may_not_move_backwards(self):
        creature = spitter()
        creature.step(5.0, {})
        with self.assertRaises(ValueError):
            creature.step(4.0, {})


class TransformTests(unittest.TestCase):
    def decode(self, packed):
        value = int.from_bytes(packed, 'big')
        largest = value >> 30
        parts = [(float((value >> shift) & 1023) - 511.5) / 723.3699951171875
                 for shift in (20, 10, 0)]
        parts.insert(largest, math.sqrt(max(0.0, 1 - sum(p * p for p in parts))))
        return parts

    def test_facing_rotation_points_the_forward_axis_at_the_target(self):
        yaw = ai.facing_yaw((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
        w, x, y, z = self.decode(ai.pack_rotation(yaw))
        # Rotate Cry's +Y forward axis by the decoded quaternion.
        forward = (2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x))
        self.assertAlmostEqual(forward[0], 1.0, delta=0.005)
        self.assertAlmostEqual(forward[1], 0.0, delta=0.005)
        up = (2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y))
        self.assertAlmostEqual(up[2], 1.0, delta=0.005)

    def test_identity_yaw_matches_the_accepted_spitter_spawn_rotation(self):
        self.assertEqual(ai.pack_rotation(0.0).hex(),
                         (((512 << 20) | (512 << 10) | 512)).to_bytes(4, 'big').hex())

    def test_packed_position_matches_the_verified_shard_packing(self):
        import shard_stub
        position = population.SPITTER_POSITIONS[0]
        self.assertEqual(ai.pack_position(position),
                         shard_stub.pack_practice_spawn_transform(position)[:8])


class DirectorTests(unittest.TestCase):
    def test_practice_population_drives_only_the_two_spitters(self):
        placements = population.creatures('practicearena')
        self.assertEqual([place.entity_id for place in placements],
                         [0x1000003, 0x1000004])
        director = ai.CreatureDirector(placements)
        player = population.SPITTER_POSITIONS[0][:2] + (39.5,)
        events = director.step(0.0, {0x1000002: player})
        self.assertEqual({event.target_id for event in events}, {0x1000002})
        self.assertEqual([event.kind for event in events].count('aggro'), 2)
        self.assertEqual(director.creatures[0x1000003].state, ai.AGGRO)
        # The second spitter is 9.1 metres away: inside AggroRadiusFar too.
        self.assertEqual(director.creatures[0x1000004].state, ai.AGGRO)

    def test_rotations_only_include_creatures_with_a_target(self):
        director = ai.CreatureDirector(population.creatures('practicearena'))
        self.assertEqual(director.rotations(), {})
        director.step(0.0, {0x1000002: population.SPITTER_POSITIONS[0][:2] + (39.5,)})
        packed = director.rotations()
        self.assertEqual(sorted(packed), [0x1000003, 0x1000004])
        self.assertTrue(all(len(value) == 12 for value in packed.values()))

    def test_alert_fan_out_uses_the_scripted_radius(self):
        director = ai.CreatureDirector(population.creatures('practicearena'))
        events = director.alert_near(0.0, population.SPITTER_POSITIONS[0],
                                     ai.SPITTER.script.alert_radius, 0x1000003)
        self.assertEqual([event.creature_id for event in events], [0x1000004])
        self.assertEqual(director.creatures[0x1000004].state, ai.AGGRO)

    def test_unaudited_maps_declare_no_creature_population(self):
        for level in ('april2018', 'hoth_02', 'tutorial_02', 'tutorial_03'):
            self.assertEqual(population.population(level), ())
        self.assertEqual(population.population('unknown-map'), ())


if __name__ == '__main__':
    unittest.main()
