import math
import random
import unittest

import hunter_ai
import navigation


def tuning(**overrides):
    values = dict(source='synthetic test tuning', patrol_speed=2., pursue_speed=4.,
                  aggro_near=5., aggro_far=20., deaggro=25., attack_delay=1.,
                  range_min=.1, range_max=10., attack_duration=.5,
                  round_interval=.5, turn_rate=math.tau)
    values.update(overrides)
    return hunter_ai.Tuning(**values)


def straight(length=100.):
    return ((0., 0., 0.), (length, 0., 0.))


def ticks(bot, count, step=.05, target=None, position=None, start=0.):
    return [bot.update(start + (i + 1) * step, target, position)
            for i in range(count)]


class TuningTests(unittest.TestCase):
    def test_incoherent_tuning_is_rejected(self):
        for bad in (dict(aggro_near=30.), dict(deaggro=1.), dict(range_min=99.),
                    dict(patrol_speed=0.), dict(attack_delay=-1.),
                    dict(attack_duration=0.), dict(round_interval=0.),
                    dict(attack_angle=0.), dict(range_vertical_max=0.),
                    dict(min_time_between_attacks=-1.), dict(turn_rate=0.)):
            with self.assertRaises(ValueError):
                tuning(**bad)


class WalkTests(unittest.TestCase):
    def test_advance_turns_the_corner_and_reports_the_overshoot(self):
        points = [(0., 0., 0.), (3., 0., 0.), (3., 4., 0.)]
        position, index, direction, left = hunter_ai.advance(points, 1, points[0], 5.)
        self.assertEqual(position, (3., 2., 0.))
        self.assertEqual(index, 2)
        self.assertEqual(direction, (0., 1., 0.))
        self.assertEqual(left, 0.)
        end = hunter_ai.advance(points, 1, points[0], 9.)
        self.assertEqual(end[0], (3., 4., 0.))
        self.assertAlmostEqual(end[3], 2.)

    def test_advance_rejects_impossible_requests(self):
        with self.assertRaises(ValueError):
            hunter_ai.advance([(0., 0., 0.)], 1, (0., 0., 0.), 1.)
        with self.assertRaises(ValueError):
            hunter_ai.advance([(0., 0., 0.), (1., 0., 0.)], 1, (0., 0., 0.), -1.)

    def test_yaw_is_measured_from_native_forward_and_turns_the_short_way(self):
        self.assertAlmostEqual(hunter_ai.yaw_of((0., 1., 0.)), 0.)
        self.assertAlmostEqual(hunter_ai.yaw_of((1., 0., 0.)), math.pi / 2)
        self.assertAlmostEqual(hunter_ai.yaw_of((-1., 0., 0.)), -math.pi / 2)
        # Crossing the +-pi seam must take the short arc, not the long one.
        self.assertAlmostEqual(
            hunter_ai.turn_towards(3.0, -3.0, math.tau), -3.0, places=6)
        self.assertAlmostEqual(
            hunter_ai.turn_towards(3.0, -3.0, .1), 3.1)


class PatrolTests(unittest.TestCase):
    def test_the_first_tick_only_establishes_the_clock_epoch(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        first = bot.update(1000.)
        self.assertEqual(first.position, bot.route[0])
        self.assertEqual(first.speed, 0.)
        self.assertAlmostEqual(bot.update(1000.5).position[0], 1.)

    def test_patrol_covers_ground_at_the_authored_speed(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        steps = ticks(bot, 21)
        self.assertAlmostEqual(steps[-1].position[0], 2.)
        self.assertEqual({s.state for s in steps}, {hunter_ai.PATROL})
        self.assertAlmostEqual(steps[-1].speed, 2.)
        self.assertAlmostEqual(steps[-1].yaw, math.pi / 2)

    def test_patrol_reverses_at_the_end_of_the_route(self):
        bot = hunter_ai.Bot(3, straight(4.), tuning())
        ticks(bot, 41)  # 40 moving ticks * .05 s * 2 m/s = 4 m, the far end
        self.assertAlmostEqual(bot.position[0], 4.)
        ticks(bot, 10, start=2.05)
        self.assertLess(bot.position[0], 4.)
        self.assertAlmostEqual(bot.position[0], 3.)
        self.assertAlmostEqual(bot.yaw, -math.pi / 2)

    def test_clock_must_not_move_backwards(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        bot.update(1.)
        with self.assertRaises(ValueError):
            bot.update(.5)
        with self.assertRaises(ValueError):
            bot.update(float('nan'))

    def test_target_id_and_position_travel_together(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        with self.assertRaises(ValueError):
            bot.update(1., 7, None)
        with self.assertRaises(ValueError):
            bot.update(1., None, (1., 1., 1.))

    def test_a_route_needs_two_points(self):
        with self.assertRaises(ValueError):
            hunter_ai.Bot(3, ((0., 0., 0.),), tuning())


class DetectionTests(unittest.TestCase):
    def test_target_outside_the_far_radius_is_not_acquired(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        step = bot.update(.05, 2, (0., 21., 0.))
        self.assertEqual(step.state, hunter_ai.PATROL)
        self.assertIsNone(step.target)

    def test_target_inside_the_far_radius_is_acquired_and_pursued(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        bot.update(.05, 2, (0., 19., 0.))
        step = bot.update(.1, 2, (0., 19., 0.))
        self.assertEqual(step.state, hunter_ai.PURSUE)
        self.assertEqual(step.target, 2)
        self.assertAlmostEqual(step.speed, 4.)

    def test_acquisition_holds_between_the_far_and_deaggro_radii(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        bot.update(.05, 2, (0., 19., 0.))
        held = bot.update(.1, 2, (0., 24., 0.))
        self.assertEqual(held.target, 2)
        dropped = bot.update(.15, 2, (0., 26., 0.))
        self.assertIsNone(dropped.target)
        self.assertEqual(dropped.state, hunter_ai.RETURN)

    def test_losing_the_target_returns_the_bot_to_its_patrol(self):
        bot = hunter_ai.Bot(3, straight(40.), tuning())
        ticks(bot, 40, target=2, position=(6., 0., 0.))
        self.assertEqual(bot.state, hunter_ai.ATTACK)
        steps = ticks(bot, 200, start=2.)
        self.assertEqual(steps[-1].state, hunter_ai.PATROL)
        self.assertIsNone(steps[-1].target)


class AggroConeTests(unittest.TestCase):
    """AggroAngleFar only gates acquisition beyond AggroRadiusNear."""

    def cone(self):
        return tuning(aggro_near=5., aggro_far=20., aggro_angle=math.radians(120.))

    def test_a_target_behind_the_bot_is_not_acquired_at_range(self):
        bot = hunter_ai.Bot(3, straight(), self.cone())  # yaw 0 == facing +Y
        self.assertIsNone(bot.update(.05, 2, (0., -10., 0.)).target)

    def test_a_target_ahead_within_the_cone_is_acquired(self):
        bot = hunter_ai.Bot(3, straight(), self.cone())
        self.assertEqual(bot.update(.05, 2, (0., 10., 0.)).target, 2)

    def test_the_cone_edge_is_half_of_the_authored_angle(self):
        inside = hunter_ai.Bot(3, straight(), self.cone())
        edge = math.radians(59.)
        self.assertEqual(inside.update(.05, 2, (10*math.sin(edge),
                                                10*math.cos(edge), 0.)).target, 2)
        outside = hunter_ai.Bot(3, straight(), self.cone())
        past = math.radians(61.)
        self.assertIsNone(outside.update(.05, 2, (10*math.sin(past),
                                                  10*math.cos(past), 0.)).target)

    def test_the_near_radius_ignores_the_cone_entirely(self):
        bot = hunter_ai.Bot(3, straight(), self.cone())
        self.assertEqual(bot.update(.05, 2, (0., -4., 0.)).target, 2)

    def test_the_marine_profile_carries_its_asset_evidence(self):
        marine = hunter_ai.MARINE
        self.assertIn('marine_guide_dummy.ai', marine.source)
        self.assertIn('marine_default.movementconfig', marine.source)
        self.assertEqual((marine.aggro_near, marine.aggro_far, marine.deaggro),
                         (5., 40., 50.))
        self.assertAlmostEqual(math.degrees(marine.aggro_angle), 120.)
        self.assertAlmostEqual(math.degrees(marine.turn_rate), 90.)
        self.assertEqual((marine.patrol_speed, marine.pursue_speed), (5.5, 8.))
        self.assertEqual((marine.range_min, marine.range_max), (0., 30.))
        self.assertEqual(marine.attack_delay, .25)
        self.assertEqual((marine.attack_duration, marine.min_time_between_attacks,
                          marine.round_interval), (2., 0., .096))
        self.assertAlmostEqual(math.degrees(marine.attack_angle), 90.)
        self.assertEqual(marine.range_vertical_max, 20.)


class WanderTests(unittest.TestCase):
    """PatrolParams author a bounded random walk, not a fixed route."""

    class Flat:
        """A 200 m square of flat ground with straight-line paths."""
        def __init__(self, half=100.):
            self.half = half

        def locate(self, point, tolerance=2.):
            if max(abs(point[0]), abs(point[1])) > self.half:
                return None
            return 'tile', (point[0], point[1], 0.)

        def path(self, start, end, step=1.):
            return [start, end]

        def wander_point(self, origin, low, high, rng, attempts=5,
                         anchor=None, anchor_limit=None, vertical_tolerance=2.):
            return navigation.Mesh.wander_point(
                self, origin, low, high, rng, attempts, anchor, anchor_limit,
                vertical_tolerance)

    def bot(self, seed=1, **kwargs):
        return hunter_ai.Bot(3, straight(), tuning(patrol_near=5., patrol_far=15.,
                                                   **kwargs),
                             mesh=self.Flat(), wander=True,
                             rng=random.Random(seed), position=(0., 0., 0.))

    def test_wandering_needs_a_mesh(self):
        with self.assertRaises(ValueError):
            hunter_ai.Bot(3, straight(), tuning(), wander=True)

    def test_hops_stay_inside_the_authored_distance_band(self):
        bot = self.bot()
        visited, previous = [], bot.position
        for index in range(2000):
            bot.update(.05 * (index + 1))
            if bot._index == 1 and bot._plan[-1] != previous:
                visited.append(bot._plan[-1])
                previous = bot._plan[-1]
        self.assertGreater(len(visited), 5)
        for a, b in zip([(0., 0., 0.), *visited], visited):
            self.assertGreaterEqual(math.dist(a, b), 5. - 1e-6)
            self.assertLessEqual(math.dist(a, b), 15. + 1e-6)

    def test_the_bot_never_strays_past_the_anchor_limit(self):
        bot = self.bot(patrol_anchor_limit=20.)
        for index in range(4000):
            step = bot.update(.05 * (index + 1))
            self.assertLessEqual(math.dist(step.position, bot.anchor), 20. + 1e-6)

    def test_the_same_seed_replays_the_same_patrol(self):
        def run(seed):
            bot = self.bot(seed)
            return [bot.update(.05 * (i + 1)).position for i in range(400)]
        self.assertEqual(run(7), run(7))
        self.assertNotEqual(run(7), run(8))
        self.assertGreater(len(set(run(7))), 100)

    def test_a_wandering_bot_goes_home_to_its_anchor_after_a_chase(self):
        bot = self.bot()
        for index in range(60):
            bot.update(.05 * (index + 1), 2, (0., 15., 0.))
        self.assertIn(bot.state, (hunter_ai.PURSUE, hunter_ai.ATTACK))
        step = bot.update(4., None, None)
        self.assertEqual(step.state, hunter_ai.RETURN)
        self.assertEqual(bot._plan[-1], bot.anchor)

    def test_a_failed_search_holds_position_rather_than_forcing_a_hop(self):
        class Nowhere(WanderTests.Flat):
            def locate(self, point, tolerance=2.):
                return None if point != (0., 0., 0.) else ('tile', point)
        bot = hunter_ai.Bot(3, straight(), tuning(), mesh=Nowhere(), wander=True,
                            rng=random.Random(1), position=(0., 0., 0.))
        for index in range(20):
            step = bot.update(.05 * (index + 1))
            self.assertEqual(step.position, (0., 0., 0.))
            self.assertEqual(step.state, hunter_ai.PATROL)


class AttackTests(unittest.TestCase):
    def test_first_shot_waits_for_the_authored_attack_delay(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        steps = ticks(bot, 40, target=2, position=(0., 8., 0.))
        times = [s.time for step in steps for s in step.shots]
        self.assertEqual({step.state for step in steps}, {hunter_ai.ATTACK})
        self.assertTrue(times)
        self.assertAlmostEqual(times[0], 1.05)  # acquired at t=.05, delay 1 s
        self.assertAlmostEqual(times[1] - times[0], .5)

    def test_a_marine_attack_fires_the_scripted_96_ms_burst(self):
        bot = hunter_ai.Bot(3, straight(), tuning(attack_duration=2.,
                                                  round_interval=.096,
                                                  attack_delay=.25))
        steps = ticks(bot, 60, target=2, position=(0., 8., 0.))
        times = [s.time for step in steps for s in step.shots]
        # Acquired at t=.05; the attack starts on the first tick past .25 s.
        first = [s for s in steps if s.shots][0]
        self.assertAlmostEqual(times[0], .3)
        self.assertTrue(first.firing)
        burst = [t for t in times if t < times[0] + 2.]
        self.assertEqual(len(burst), 21)
        for earlier, later in zip(burst, burst[1:]):
            self.assertAlmostEqual(later - earlier, .096)
        # Back to back: MinTimeBetweenAttacks is zero, so the next attack
        # starts on the tick the previous one ends.
        self.assertAlmostEqual(times[21], 2.3)
        self.assertTrue(all(step.firing for step in steps[5:]))

    def test_no_attack_starts_outside_the_authored_cone(self):
        bot = hunter_ai.Bot(3, straight(), tuning(attack_angle=math.radians(90.),
                                                  turn_rate=math.radians(80.),
                                                  attack_delay=0.))
        # Directly behind: 135 degrees of turning at 80 deg/s is 1.6875 s from
        # the epoch tick at .05, so the target enters +-45 degrees at 1.7375
        # and the first tick past that is 1.75.
        steps = ticks(bot, 60, target=2, position=(0., -8., 0.))
        first = next(step for step in steps if step.shots)
        self.assertAlmostEqual(first.shots[0].time, 1.75)
        self.assertTrue(all(not step.firing for step in steps[:steps.index(first)]))

    def test_no_attack_starts_past_the_vertical_range(self):
        bot = hunter_ai.Bot(3, straight(), tuning(aggro_far=30., deaggro=35.,
                                                  range_max=30.,
                                                  range_vertical_max=20.))
        steps = ticks(bot, 60, target=2, position=(0., 8., 25.))
        self.assertEqual({s.state for s in steps}, {hunter_ai.ATTACK})
        self.assertFalse([s for step in steps for s in step.shots])

    def test_dropping_the_target_ends_the_attack(self):
        bot = hunter_ai.Bot(3, straight(), tuning(attack_duration=2.,
                                                  round_interval=.1, attack_delay=0.))
        engaged = ticks(bot, 5, target=2, position=(0., 8., 0.))
        self.assertTrue(engaged[-1].firing)
        dropped = ticks(bot, 5, start=.25)
        self.assertFalse([s for step in dropped for s in step.shots])
        self.assertFalse(any(step.firing for step in dropped))

    def test_shot_carries_shooter_target_and_a_unit_aim(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        # Inside RangeMax less the entry margin, so the bot fires from home.
        shots = [s for step in ticks(bot, 40, target=2, position=(0., 4.8, 3.6))
                 for s in step.shots]
        shot = shots[0]
        self.assertEqual((shot.shooter, shot.target), (3, 2))
        self.assertAlmostEqual(math.dist((0., 0., 0.), shot.direction), 1.)
        self.assertAlmostEqual(shot.range, 6.)
        self.assertEqual(shot.origin, bot.route[0])

    def test_out_of_range_targets_are_chased_not_shot(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        steps = ticks(bot, 5, target=2, position=(0., 15., 0.))
        self.assertEqual({s.state for s in steps}, {hunter_ai.PURSUE})
        self.assertFalse([s for step in steps for s in step.shots])

    def test_the_bot_stops_and_faces_a_target_it_cannot_reach(self):
        class Blocked:
            def path(self, start, end, step=1.):
                return None
        bot = hunter_ai.Bot(3, straight(), tuning(), mesh=Blocked())
        step = bot.update(.05, 2, (0., 15., 0.))
        self.assertEqual(step.state, hunter_ai.ATTACK)
        self.assertEqual(step.speed, 0.)
        self.assertAlmostEqual(step.yaw, 0.)

    def test_a_target_at_the_range_edge_does_not_flip_the_state(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        # Just inside RangeMax but short of the entry margin: keep closing.
        edge = 10. - hunter_ai.ATTACK_ENTRY_MARGIN / 2
        self.assertEqual(bot.update(.05, 2, (0., edge, 0.)).state, hunter_ai.PURSUE)
        steps = ticks(bot, 60, target=2, position=(0., edge, 0.), start=.05)
        states = [s.state for s in steps]
        first = states.index(hunter_ai.ATTACK)
        self.assertEqual(set(states[first:]), {hunter_ai.ATTACK})
        # Once attacking, the target may drift back to RangeMax without a flip.
        near = steps[-1].position[1]
        steps = [bot.update(3.05 + i * .05, 2, (0., near + 10. - (i % 2) * .4, 0.))
                 for i in range(20)]
        self.assertEqual({s.state for s in steps}, {hunter_ai.ATTACK})

    def test_a_marine_magazine_empties_reloads_and_resumes(self):
        marine = hunter_ai.MARINE
        self.assertEqual((marine.magazine, marine.reload_fill, marine.reload_animation,
                          marine.reload_duration), (24, 1.216, 1.6, 1.984))
        bot = hunter_ai.Bot(3, straight(), tuning(
            attack_duration=2., round_interval=.096, attack_delay=.25, magazine=24,
            reload_fill=1.216, reload_animation=1.6, reload_duration=1.984), ammo=24)
        steps = ticks(bot, 140, target=2, position=(0., 8., 0.))
        times = [s.time for step in steps for s in step.shots]
        # 21 rounds in the first attack, three more in the second: the 24th
        # is at 2.3 + 2 * .096. The loop empties the magazine 96 ms later.
        self.assertAlmostEqual(times[23], 2.492)
        empty = 2.492 + .096
        ready = empty + 1.984
        self.assertAlmostEqual(times[24], ready)
        self.assertAlmostEqual(times[25] - times[24], .096)
        self.assertFalse([t for t in times if 2.5 < t < ready - 1e-9])
        # The second magazine empties the same way.
        self.assertEqual(len(times), 48)
        empties = (empty, times[47] + .096)
        for step, now in zip(steps, (.05 * (i + 1) for i in range(140))):
            with self.subTest(now=now):
                self.assertEqual(step.reloading,
                                 any(e <= now < e + 1.6 for e in empties))
                if any(e <= now < e + 1.984 for e in empties):
                    self.assertFalse(step.firing)
        self.assertEqual(bot.ammo, 0)

    def test_damage_forces_aggro_from_outside_the_cone_and_radius(self):
        self.assertEqual(hunter_ai.MARINE.forced_aggro_on_damage, 6.)
        bot = hunter_ai.Bot(3, straight(), tuning(aggro_angle=math.radians(120.),
                                                  forced_aggro_on_damage=6.))
        behind = (0., -22., 0.)          # past AggroRadiusFar, behind the bot
        self.assertIsNone(bot.update(.05, 2, behind).target)
        bot.note_damage(.06, 2)
        step = bot.update(.1, 2, behind)
        self.assertEqual((step.target, step.state), (2, hunter_ai.PURSUE))
        # Past the deaggro radius the forced window still holds the target...
        far = lambda: (bot.position[0], bot.position[1] - 30., 0.)
        self.assertEqual(bot.update(6., 2, far()).target, 2)
        # ...and once it runs out, the ordinary deaggro test applies again.
        self.assertIsNone(bot.update(6.1, 2, far()).target)

    def test_damage_changes_nothing_without_an_authored_duration(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        bot.note_damage(.05, 2)
        self.assertIsNone(bot.update(.1, 2, (0., -22., 0.)).target)

    def test_without_a_magazine_the_rifle_never_reloads(self):
        bot = hunter_ai.Bot(3, straight(), tuning(
            attack_duration=2., round_interval=.096, attack_delay=.25, magazine=24,
            reload_fill=1.216, reload_animation=1.6, reload_duration=1.984))
        steps = ticks(bot, 140, target=2, position=(0., 8., 0.))
        times = [s.time for step in steps for s in step.shots]
        # Two full attacks, then a third that starts one tick late (the
        # previous attack's end time lands a float hair after its tick).
        self.assertEqual(len(times), 70)
        self.assertFalse(any(step.reloading for step in steps))
        self.assertIsNone(bot.ammo)

    def test_targets_closer_than_range_min_are_still_engaged(self):
        bot = hunter_ai.Bot(3, straight(), tuning(range_min=3.))
        steps = ticks(bot, 40, target=2, position=(0., 1., 0.))
        self.assertEqual({s.state for s in steps}, {hunter_ai.ATTACK})
        self.assertFalse([s for step in steps for s in step.shots])


class PursuitTests(unittest.TestCase):
    def test_pursuit_closes_the_distance_and_then_opens_fire(self):
        bot = hunter_ai.Bot(3, straight(), tuning())
        player = (0., 18., 0.)
        first = bot.update(.05, 2, player)
        self.assertEqual(first.state, hunter_ai.PURSUE)
        steps = ticks(bot, 200, target=2, position=player, start=.05)
        distances = [math.dist(s.position, player) for s in steps]
        self.assertLess(distances[-1], distances[0])
        self.assertLessEqual(distances[-1], 10.)
        self.assertEqual(steps[-1].state, hunter_ai.ATTACK)
        self.assertTrue([s for step in steps for s in step.shots])

    def test_a_moving_target_forces_a_replan_only_after_the_interval(self):
        seen = []

        class Recording:
            def path(self, start, end, step=1.):
                seen.append(end)
                return [start, end]
        bot = hunter_ai.Bot(3, straight(), tuning(repath_interval=.5,
                                                  repath_distance=1.),
                            mesh=Recording())
        for index in range(20):
            bot.update(.05 * (index + 1), 2, (0., 18. + index * .01, 0.))
        self.assertEqual(len(seen), 2)  # t=0.05 and t>=0.55

    def test_the_squad_ticks_every_bot_from_one_clock(self):
        squad = hunter_ai.Squad([hunter_ai.Bot(i, straight(), tuning())
                                 for i in (3, 4, 5)])
        squad.update(1., 2, (0., 8., 0.))
        steps = squad.update(2.1, 2, (0., 8., 0.))
        self.assertEqual([s.entity for s in steps], [3, 4, 5])
        self.assertEqual(len(squad.shots(steps)), 3)


class DeterminismTests(unittest.TestCase):
    def test_identical_tick_sequences_produce_identical_output(self):
        def run():
            bot = hunter_ai.Bot(3, straight(12.), tuning())
            out = []
            for index in range(400):
                now = .05 * (index + 1)
                player = (0., 9., 0.) if 100 <= index < 200 else None
                out.append(bot.update(now, 2 if player else None, player))
            return out
        self.assertEqual(run(), run())


class InstalledMeshPursuitTests(unittest.TestCase):
    """One bot chasing across the shipped April2018 mesh; nothing is written."""

    def test_pursuit_over_real_navigation_stays_on_the_surface(self):
        import navigation
        import solo_modes
        if not navigation.LEVEL_PAK.exists():
            self.skipTest('Crucible is not installed')
        mesh = navigation.installed_mesh()
        home = mesh.locate(solo_modes.HIVES.bot_positions[0])[1]
        player = mesh.locate(solo_modes.HIVES.player_position)[1]
        route = tuple(mesh.path(home, solo_modes.HIVES.bot_positions[1]))
        bot = hunter_ai.Bot(0x01000003, route, tuning(aggro_far=40., deaggro=60.),
                            mesh=mesh, position=home)
        steps = [bot.update(.05 * (i + 1), 0x01000002, player) for i in range(400)]
        self.assertEqual(steps[0].state, hunter_ai.PURSUE)
        self.assertEqual(steps[-1].state, hunter_ai.ATTACK)
        for step in steps:
            self.assertIsNotNone(mesh.locate(step.position, .05), step.position)
        self.assertTrue([s for step in steps for s in step.shots])


if __name__ == '__main__':
    unittest.main()
