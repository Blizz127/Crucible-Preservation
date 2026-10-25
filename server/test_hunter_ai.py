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


class AccelerationTests(unittest.TestCase):
    """MovementPararams/StartStopAcceleration, 10 m/s^2 for the Marine."""

    def bot(self, accelerate=True, **kwargs):
        bot = hunter_ai.Bot(3, straight(20.), tuning(patrol_speed=5.5,
                                                    start_stop_acceleration=10., **kwargs))
        bot.accelerate = accelerate
        return bot

    def test_speed_ramps_at_the_authored_rate_and_brakes_to_a_stop(self):
        bot = self.bot()
        bot.update(.05)                                    # tick 1 has no clock
        speeds, positions = [], []
        for i in range(2, 120):
            positions.append(bot.update(.05 * i).position[0])
            speeds.append(bot._speed)
        self.assertAlmostEqual(speeds[0], .5, places=9)    # 10 m/s^2 for 50 ms
        self.assertTrue(all(abs(b - a) <= .5 + 1e-9 for a, b in zip(speeds, speeds[1:])))
        self.assertAlmostEqual(max(speeds), 5.5, places=9)
        self.assertLessEqual(speeds.index(max(speeds)), 11)    # 0.55 s to 5.5 m/s
        # It comes to a stop at the end of the 20 m leg, then turns back.
        end = max(range(len(positions)), key=positions.__getitem__)
        self.assertAlmostEqual(positions[end], 20., places=6)
        self.assertLess(speeds[end], .01)
        self.assertLess(positions[-1], positions[end] - 1.)

    def test_without_the_switch_speed_changes_at_once(self):
        speeds = [step.speed for step in ticks(self.bot(accelerate=False), 5)][1:]
        for speed in speeds:
            self.assertAlmostEqual(speed, 5.5, places=9)

    def test_the_config_model_ramps_in_0_15_s_and_stops_at_once(self):
        # marine_default: Acceleration 0.15 s to full speed; Brake Friction 100.
        bot = hunter_ai.Bot(3, straight(), tuning(patrol_speed=5.5, config_ramp_seconds=.15))
        bot.accelerate, bot.accel_model = True, 'config'
        bot.update(.05)
        speeds = [bot.update(.05 * i).speed for i in range(2, 6)]
        for got, want in zip(speeds, (5.5 / 3, 2 * 5.5 / 3, 5.5, 5.5)):
            self.assertAlmostEqual(got, want, places=9)

    def test_under_the_config_model_an_attacker_stops_without_sliding(self):
        # The mirror of the ai model's slide: friction stops it on the spot.
        bot = hunter_ai.Bot(3, straight(), tuning(config_ramp_seconds=.15,
                                                  aggro_far=30., deaggro=40.))
        bot.accelerate, bot.accel_model = True, 'config'
        steps = [bot.update(.05 * (i + 1), 2, (0., 16., 0.)) for i in range(80)]
        attack = next(i for i, s in enumerate(steps) if s.state == hunter_ai.ATTACK)
        self.assertGreater(steps[attack - 1].speed, 0.)
        self.assertEqual(steps[attack].speed, 0.)

    def test_the_chase_cap_holds_a_pursuit_to_the_default_config(self):
        for cap, want in ((None, 8.), (5.5, 5.5)):
            bot = hunter_ai.Bot(3, straight(), tuning(pursue_speed=8., aggro_far=30.,
                                                      deaggro=40.))
            bot.pursue_cap = cap
            steps = [bot.update(.05 * (i + 1), 2, (0., 25., 0.)) for i in range(4)]
            with self.subTest(cap=cap):
                self.assertEqual(steps[-1].state, hunter_ai.PURSUE)
                self.assertAlmostEqual(steps[-1].speed, want, places=9)

    def test_an_attacker_slides_to_a_stop_still_facing_its_target(self):
        bot = hunter_ai.Bot(3, straight(), tuning(start_stop_acceleration=10.,
                                                  aggro_far=30., deaggro=40.))
        bot.accelerate = True
        target = (0., 16., 0.)
        steps = [bot.update(.05 * (i + 1), 2, target) for i in range(80)]
        attack = next(i for i, s in enumerate(steps) if s.state == hunter_ai.ATTACK)
        sliding = [s.speed for s in steps[attack:attack + 12]]
        self.assertGreater(sliding[0], 0.)                            # still moving at first
        self.assertTrue(all(b <= a + 1e-9 for a, b in zip(sliding, sliding[1:])))
        self.assertEqual(sliding[-1], 0.)
        for step in steps[attack:attack + 12]:
            want = math.atan2(target[0] - step.position[0], target[1] - step.position[1])
            self.assertAlmostEqual(step.yaw, want, places=6)


class FleeTests(unittest.TestCase):
    """marine_guide_on_hit.cdl's flight from a losing fight (docs/36)."""

    def bot(self, half=100., **kwargs):
        # The Marine's own aggro geometry: a 120-degree cone, 5 m near, 40 m
        # far, 50 m deaggro. A flight ends 20-40 m off, facing away.
        values = dict(flee_time=4., flee_speed=8.5, flee_near=20., flee_far=40.,
                      flee_attempts=30, aggro_near=5., aggro_far=40.,
                      aggro_angle=math.radians(120.), deaggro=50.)
        values.update(kwargs)
        bot = hunter_ai.Bot(3, straight(), tuning(**values), mesh=WanderTests.Flat(half),
                            rng=random.Random(5), position=(0., 0., 0.))
        bot.flee = True
        return bot

    def test_the_scripts_condition(self):
        # Flee when the attacker is healthier and sqrt(distance) * ratio < 1.5.
        for attacker, own, other, flees in (((0., 9., 0.), .4, .8, True),    # 3 * .4 = 1.2
                                            ((0., 25., 0.), .4, .8, False),  # 5 * .4 = 2.0
                                            ((0., 4., 0.), .6, .5, False),   # it is healthier
                                            ((0., 4., 0.), .5, .5, False)):  # not strictly
            with self.subTest(attacker=attacker, own=own, other=other):
                self.assertEqual(self.bot().consider_flee(1., attacker, own, other), flees)
        bot = self.bot()
        bot.flee = False
        self.assertFalse(bot.consider_flee(1., (0., 9., 0.), .4, .8))

    def test_it_runs_away_without_firing_then_fights_again(self):
        bot = self.bot()
        player = (0., 8., 0.)
        for i in range(1, 30):                        # engaged and firing
            bot.update(.05 * i, 2, player)
        self.assertEqual(bot.target, 2)
        self.assertTrue(bot.consider_flee(1.5, player, .3, .9))
        steps = [bot.update(1.5 + .05 * i, 2, player) for i in range(1, 80)]
        flight = [s for s in steps if s.state == hunter_ai.FLEE]
        self.assertEqual(len(flight), 79)             # 4 s at 50 ms, minus the last tick
        self.assertFalse([s for s in flight if s.shots or s.firing])
        self.assertTrue(all(s.target == 2 for s in flight))   # the target is kept
        self.assertAlmostEqual(max(s.speed for s in flight), 8.5, places=6)
        away = [math.dist(s.position, player) for s in flight]
        self.assertGreater(away[-1], 20.)
        after = [bot.update(5.5 + .05 * i, 2, player) for i in range(1, 30)]
        # IgnoreTargetsTime over: inside the 50 m deaggro radius it fights on,
        # though it faces away, outside its 120-degree acquisition cone.
        self.assertEqual(after[-1].target, 2)
        self.assertIn(after[-1].state, (hunter_ai.PURSUE, hunter_ai.ATTACK))

    def blocked_bot(self, duration=2., window=(0., 2.)):
        # Attacks[0]: FleeingBlockedWindows over the attack, a 2.5 s memory.
        return self.bot(attack_duration=duration, round_interval=.1, attack_delay=.25,
                        flee_ignore_targets=4., flee_blocked_window=window,
                        flee_memory=2.5)

    def engage(self, bot, player):
        now = 0.
        while bot.attack_started is None:
            now += .05
            bot.update(now, 2, player)
            self.assertLess(now, 5.)
        return now

    def test_a_hit_mid_attack_waits_for_the_attack_to_end(self):
        bot, player = self.blocked_bot(), (0., 8., 0.)
        now = started = self.engage(bot, player)
        now += .5
        bot.update(now, 2, player)
        self.assertTrue(bot.consider_flee(now, player, .3, .9))
        self.assertNotEqual(bot.state, hunter_ai.FLEE)    # the attack runs on
        steps = []
        while now < started + 3.:
            now += .05
            steps.append((now, bot.update(now, 2, player)))
        before = [s for t, s in steps if s.state != hunter_ai.FLEE]
        flight = [(t, s) for t, s in steps if s.state == hunter_ai.FLEE]
        self.assertTrue(any(s.shots for s in before))     # it finished its burst
        self.assertTrue(flight)
        self.assertFalse([s for t, s in flight if s.shots or s.firing])
        first = flight[0][0]
        self.assertGreaterEqual(first, started + 2. - 1e-9)
        self.assertLess(first, started + 2.1)
        self.assertEqual(bot.flees, 1)
        self.assertAlmostEqual(bot._flee_until, first + 4.)  # IgnoreTargetsTime

    def test_a_decision_outlasting_its_memory_is_dropped(self):
        # A 3 s window closes 2.9 s after this decision, past the 2.5 s memory.
        bot, player = self.blocked_bot(duration=3., window=(0., 3.)), (0., 8., 0.)
        now = started = self.engage(bot, player)
        now += .1
        bot.update(now, 2, player)
        self.assertTrue(bot.consider_flee(now, player, .3, .9))
        while now < started + 3.5:
            now += .05
            self.assertNotEqual(bot.update(now, 2, player).state, hunter_ai.FLEE)
        self.assertEqual(bot.flees, 0)
        self.assertIsNotNone(bot.attack_started)          # fighting again

    def test_a_hit_after_the_window_settles_the_waiting_decision(self):
        # The attack has run its 2 s, but no tick has cleared it yet.
        bot, player = self.blocked_bot(), (0., 8., 0.)
        now = started = self.engage(bot, player)
        self.assertTrue(bot.consider_flee(now + .5, player, .3, .9))
        self.assertIsNotNone(bot._flee_wanted)
        self.assertTrue(bot.consider_flee(started + 2.01, player, .3, .9))
        path = list(bot._plan)
        bot.update(started + 2.05, 2, player)
        self.assertEqual((bot.flees, bot._plan), (1, path))

    def test_outside_an_attack_it_goes_at_once(self):
        bot = self.blocked_bot()
        self.assertTrue(bot.consider_flee(1., (0., 9., 0.), .4, .8))
        self.assertEqual((bot.state, bot.flees), (hunter_ai.FLEE, 1))

    def test_a_hit_during_the_flight_extends_it_on_the_same_path(self):
        bot = self.bot()
        self.assertTrue(bot.consider_flee(1., (0., 9., 0.), .4, .8))
        path = list(bot._plan)
        self.assertTrue(bot.consider_flee(2., (0., 9., 0.), .3, .8))
        self.assertEqual((bot.flees, bot._flee_until, bot._plan), (1, 6., path))

    def test_damage_during_the_flight_does_not_aggro(self):
        bot = self.bot(forced_aggro_on_damage=6.)
        self.assertTrue(bot.consider_flee(1., (0., 9., 0.), .4, .8))
        bot.note_damage(1.5, 7)                      # a different attacker
        self.assertIsNone(bot.target)
        self.assertIsNone(bot.forced_until)

    def test_without_a_reachable_destination_it_stays(self):
        bot = self.bot(half=5.)                       # nowhere 20-40 m away
        self.assertFalse(bot.consider_flee(1., (0., 3., 0.), .2, .9))
        self.assertNotEqual(bot.state, hunter_ai.FLEE)

    def test_destinations_in_another_component_are_never_searched(self):
        class Split(WanderTests.Flat):
            """Flat ground cut at x = 0 into two unconnected halves."""
            searches = 0

            def locate(self, point, tolerance=2.):
                located = super().locate(point, tolerance)
                if located is None:
                    return None
                return ('west' if point[0] < 0. else 'east'), located[1]

            def path(self, start, end, step=1.):
                self.searches += 1
                return [start, end]

            def component_index(self):
                return {'west': 0, 'east': 1}

        # Away from (-14, 0) is east: every candidate, even at the widest
        # 60-degree spread, lands across the cut, where no path leads.
        bot = self.bot()
        bot.mesh, bot.position = Split(), (-5., 0., 0.)
        self.assertFalse(bot.consider_flee(1., (-14., 0., 0.), .2, .9))
        self.assertEqual(bot.mesh.searches, 0)
        # Fleeing west from (4, 0) stays on its own side and goes.
        bot.position = (-5., 0., 0.)
        self.assertTrue(bot.consider_flee(1., (4., 0., 0.), .2, .9))
        self.assertEqual(bot.mesh.searches, 1)


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

    def rests(self, bot, steps=6000, dt=.05):
        """Durations of the still stretches between moves, in seconds."""
        bot.update(dt)                  # the first tick has no clock to measure speed
        stretches, still = [], 0
        for index in range(1, steps):
            if bot.update(dt * (index + 1)).speed == 0.:
                still += 1
            elif still:
                stretches.append(still * dt)
                still = 0
        return stretches

    def test_an_idle_marine_rests_one_authored_idle_between_hops(self):
        bot = self.bot(idle_durations=(3.0, 6.0), idle_extra=1.0)
        bot.idle = True
        rests = self.rests(bot)
        self.assertGreater(len(rests), 5)
        for rest in rests:
            # One idle animation plus ExtraIdleTimeAtEndOfAnim, to the tick.
            self.assertTrue(any(abs(rest - want) <= .1 for want in (4.0, 7.0)), rest)
        self.assertEqual({round(r) for r in rests}, {4, 7})
        # The shard logs this count; a rest still running at the end counts too.
        self.assertIn(bot.rests, (len(rests), len(rests) + 1))

    def test_without_the_switch_a_marine_never_rests(self):
        bot = self.bot(idle_durations=(3.0,), idle_extra=1.0)
        self.assertEqual(self.rests(bot, steps=2000), [])

    def test_a_target_ends_the_rest(self):
        bot = self.bot(idle_durations=(30.0,), idle_extra=1.0)
        bot.idle = True
        now = 0.
        while bot._idle_until is None:
            now += .05
            bot.update(now)
        target = tuple(bot.position[i] + (3., 0., 0.)[i] for i in range(3))
        step = bot.update(now + .05, 2, target)
        self.assertEqual(step.target, 2)
        self.assertIsNone(bot._idle_until)

    def test_an_anchored_marine_patrols_5_to_15_m_around_its_anchor(self):
        # The editor tooltips: MinDistance/MaxDistance "from anchor pos",
        # MaxDistanceFromCurrent 50 m "from current pos".
        bot = self.bot()
        bot.anchored = True
        destinations, farthest = set(), 0.
        for index in range(4000):
            step = bot.update(.05 * (index + 1))
            destinations.add(tuple(bot._plan[-1]))
            farthest = max(farthest, math.dist(step.position, bot.anchor))
        destinations.discard(tuple(bot.anchor))
        self.assertGreater(len(destinations), 10)
        for point in destinations:
            self.assertGreaterEqual(math.dist(point, bot.anchor), 5. - 1e-6)
            self.assertLessEqual(math.dist(point, bot.anchor), 15. + 1e-6)
        self.assertLessEqual(farthest, 15. + 1e-6)     # a disc holds its chords

    def test_a_marine_stranded_far_from_its_ring_walks_back_after_50_attempts(self):
        # 70 m out, every ring point is beyond the 50 m from-current limit.
        bot = hunter_ai.Bot(3, straight(), tuning(patrol_near=5., patrol_far=15.,
                                                  patrol_ignore_current_after=50),
                            mesh=self.Flat(), wander=True, rng=random.Random(1),
                            position=(70., 0., 0.), anchor=(0., 0., 0.))
        bot.anchored = True
        for index in range(9):                     # nine searches, 45 attempts
            bot.update(.05 * (index + 1))
        self.assertEqual(bot.position, (70., 0., 0.))
        for index in range(9, 1400):                # 63 m back at 2 m/s, then patrol
            step = bot.update(.05 * (index + 1))
        self.assertLessEqual(math.dist(step.position, bot.anchor), 15. + 1e-6)

    def test_the_default_wander_still_roams_beyond_15_m(self):
        # The earlier, inverted reading stays the default (deployed baseline).
        bot = self.bot()
        farthest = max(math.dist(bot.update(.05 * (i + 1)).position, bot.anchor)
                       for i in range(4000))
        self.assertGreater(farthest, 15.)

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

    def strafer(self, target_distance, mesh=None):
        bot = hunter_ai.Bot(3, straight(), hunter_ai.MARINE, mesh=mesh, strafe=True)
        # Settle facing the target before measuring.
        bot.update(.05, 2, (0., target_distance, 0.))
        return bot

    def test_a_moving_marine_steers_at_the_movement_turn_speed(self):
        marine = hunter_ai.MARINE
        self.assertAlmostEqual(math.degrees(marine.move_turn_rate), 1440.)
        # Walking a 90-degree corner: the body follows within two ticks, but
        # only with the switch; without it the 90 deg/s turn lags behind.
        corner = ((0., 0., 0.), (0., 1., 0.), (5., 1., 0.))
        for move_turn, reaches in ((True, True), (False, False)):
            bot = hunter_ai.Bot(3, corner, marine, move_turn=move_turn)
            steps = [bot.update(.05 * i) for i in range(1, 8)]
            turned = [s for s in steps if abs(s.yaw - math.radians(90.)) < 1e-9]
            self.assertEqual(bool(turned), reaches)
        # Attacking a target 4 m behind it (inside AggroRadiusNear), it stands
        # and turns in place at 90 deg/s.
        standing = hunter_ai.Bot(3, straight(), marine, move_turn=True)
        standing.update(.05, 2, (0., -4., 0.))
        step = standing.update(.55, 2, (0., -4., 0.))
        self.assertEqual((step.state, step.speed), (hunter_ai.ATTACK, 0.))
        self.assertAlmostEqual(abs(step.yaw), math.radians(45.))

    def test_strafe_influence_follows_the_authored_radii(self):
        bot = hunter_ai.Bot(3, straight(), hunter_ai.MARINE)
        for distance, influence in ((.5, 0.), (1., 0.), (1.5, .5), (2., 1.), (10., 1.),
                                    (20., 1.), (25., .5), (30., 0.), (35., 0.)):
            with self.subTest(distance=distance):
                self.assertAlmostEqual(bot.strafe_influence(distance), influence)

    def test_a_strafing_marine_moves_sideways_and_keeps_facing_the_target(self):
        bot = self.strafer(15.)
        start = bot.position
        steps = [bot.update(.05 + .05 * i, 2, (0., 15., 0.)) for i in range(1, 21)]
        self.assertEqual({s.state for s in steps}, {hunter_ai.ATTACK})
        # Full influence at 15 m: 4.5 m/s sideways, no closing. The first
        # strafe direction holds for U(1, 2) s, so one second is one leg,
        # tangent to the circle round the target.
        self.assertAlmostEqual(sum(s.speed * .05 for s in steps), 4.5, places=6)
        self.assertGreater(abs(bot.position[0] - start[0]), 4.4)
        self.assertAlmostEqual(math.dist(bot.position[:2], (0., 15.)), 15., delta=.05)
        self.assertTrue(all(s.speed > 0 for s in steps))
        aim = (0. - bot.position[0], 15. - bot.position[1])
        self.assertLess(abs(steps[-1].yaw - hunter_ai.yaw_of(aim + (0.,))), math.radians(5.))

    def test_between_20_and_30_metres_it_closes_in_while_strafing(self):
        bot = self.strafer(25.)
        before = math.dist(bot.position[:2], (0., 25.))
        for i in range(1, 21):
            bot.update(.05 + .05 * i, 2, (0., 25., 0.))
        after = math.dist(bot.position[:2], (0., 25.))
        self.assertLess(after, before - 1.)
        self.assertGreater(after, 20.)

    def test_a_sidestep_off_the_mesh_reverses_the_strafe(self):
        class HalfPlane:
            def locate(self, point, vertical_tolerance=2.0):
                if abs(point[0]) > .3:
                    return None
                return 0, point
            def path(self, start, end, step=1.):
                return [start, end]
            def wander_point(self, *args, **kwargs):
                return None
        bot = self.strafer(15., mesh=HalfPlane())
        for i in range(1, 41):
            bot.update(.05 + .05 * i, 2, (0., 15., 0.))
            self.assertLessEqual(abs(bot.position[0]), .3)

    def test_a_sidestep_never_climbs_or_drops_a_ledge(self):
        class Ledge:
            """Ground at z=0 for x <= .3, a platform at z=1.5 beyond it."""
            def locate(self, point, vertical_tolerance=2.0):
                surface, key = (0., 'ground') if point[0] <= .3 else (1.5, 'platform')
                if abs(point[2] - surface) > vertical_tolerance:
                    return None
                return key, (point[0], point[1], surface)
            def neighbours(self, key):
                return ()
            def path(self, start, end, step=1.):
                return [start, end]
            def wander_point(self, *args, **kwargs):
                return None
        bot = self.strafer(15., mesh=Ledge())
        for i in range(1, 41):
            bot.update(.05 + .05 * i, 2, (0., 15., 0.))
            self.assertEqual(bot.position[2], 0.)
            self.assertLessEqual(bot.position[0], .3)

    def test_without_the_switch_an_attacking_marine_stands(self):
        bot = hunter_ai.Bot(3, straight(), hunter_ai.MARINE)
        bot.update(.05, 2, (0., 15., 0.))
        start = bot.position
        steps = [bot.update(.05 + .05 * i, 2, (0., 15., 0.)) for i in range(1, 21)]
        self.assertEqual(bot.position, start)
        self.assertTrue(all(s.speed == 0. for s in steps))

    def sighted(self, seen):
        """A Marine whose sight test answers `seen[0]` and counts its calls."""
        calls = []

        def visible(eye, target):
            calls.append((eye, target))
            return seen[0]
        bot = hunter_ai.Bot(3, straight(), hunter_ai.MARINE, visible=visible)
        return bot, calls

    def test_an_unseen_target_is_not_acquired(self):
        self.assertEqual((hunter_ai.MARINE.forget_without_sight,
                          hunter_ai.MARINE.abandon_attack_without_sight), (8., 3.))
        bot, calls = self.sighted([False])
        self.assertIsNone(bot.update(.05, 2, (0., 4., 0.)).target)
        # The eye is AggroOriginOffsetLocal above the feet; the aim point is
        # AggroTargetOffsetVertical above the target's.
        self.assertEqual(calls[0], ((0., 0., 1.), (0., 4., 1.)))

    def test_an_attack_needs_sight_to_start_and_is_abandoned_after_3_s(self):
        seen = [True]
        bot, _ = self.sighted(seen)
        steps = [bot.update(.05 * i, 2, (0., 8., 0.)) for i in range(1, 11)]
        self.assertTrue(steps[-1].firing)
        seen[0] = False
        blind = [bot.update(.5 + .05 * i, 2, (0., 8., 0.)) for i in range(1, 81)]
        # Last seen at about .5 s (the throttled test re-runs every .2 s).
        # The attack runs on, a new one starts at its end while the target
        # is still inside the 3 s window, and firing stops once 3 s pass.
        times = [shot.time for s in blind for shot in s.shots]
        self.assertTrue([t for t in times if 2.3 <= t < 3.4])   # restarted unseen
        self.assertFalse([t for t in times if t > 3.75])        # then abandoned
        self.assertFalse(blind[-1].firing)
        self.assertEqual(blind[-1].target, 2)

    def test_an_unseen_attacker_is_held_but_not_fired_on(self):
        seen = [False]
        bot, _ = self.sighted(seen)
        steps = []
        for i in range(1, 61):                  # hit every .5 s for 3 s, unseen
            now = .05 * i
            if i % 10 == 1:
                bot.note_damage(now, 2)
            steps.append(bot.update(now, 2, (0., 8., 0.)))
        self.assertEqual(steps[-1].target, 2)   # forced aggro holds it
        self.assertFalse([s for s in steps if s.shots or s.firing])
        seen[0] = True                          # once it is seen, the attack starts
        later = [bot.update(3. + .05 * i, 2, (0., 8., 0.)) for i in range(1, 11)]
        self.assertTrue(any(s.shots for s in later))

    def test_a_target_unseen_for_8_s_is_forgotten_unless_forced(self):
        for forced, kept in ((False, False), (True, True)):
            seen = [True]
            bot, _ = self.sighted(seen)
            bot.update(.05, 2, (0., 4., 0.))
            seen[0] = False
            if forced:
                bot.note_damage(8., 2)
            step = bot.update(8.35, 2, (0., 4., 0.))
            self.assertEqual(step.target is not None, kept)

    def test_the_sight_line_is_retested_at_most_every_0_2_s(self):
        bot, calls = self.sighted([True])
        for i in range(1, 21):
            bot.update(.05 * i, 2, (0., 4., 0.))
        self.assertEqual(len(calls), 5)

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


class SaboteurTests(unittest.TestCase):
    """saboteur_guide.ai: HOTH_GUIDE's hostile guide bot (docs/37)."""

    def bot(self, **kwargs):
        mesh = WanderTests.Flat()
        return hunter_ai.Bot(3, straight(), hunter_ai.SABOTEUR, mesh=mesh, wander=True,
                             rng=random.Random(2), position=(0., 0., 0.), **kwargs)

    def test_its_authored_values(self):
        tuning = hunter_ai.SABOTEUR
        self.assertEqual((tuning.pursue_speed, tuning.chase_cap, tuning.patrols),
                         (6.5, 6.5, False))
        self.assertEqual((tuning.aggro_near, tuning.aggro_far, tuning.deaggro), (30., 60., 60.))
        self.assertAlmostEqual(math.degrees(tuning.aggro_angle), 180.)
        self.assertEqual((tuning.attack_duration, tuning.min_time_between_attacks), (2., 1.2))
        self.assertAlmostEqual(tuning.round_interval, 0.352, places=3)
        self.assertEqual((tuning.magazine, tuning.reload_duration, tuning.ammo_delay),
                         (8, 2.208, 0.))
        self.assertEqual((tuning.flee_time, tuning.flee_ignore_targets, tuning.flee_speed),
                         (3., 4., 6.5))
        self.assertIs(hunter_ai.TUNINGS['saboteur'], tuning)

    def test_it_stands_without_a_target_and_rests_never(self):
        bot = self.bot(idle=True)
        for i in range(1, 400):
            step = bot.update(.05 * i)
            self.assertEqual(step.speed, 0.)
        self.assertEqual((bot.position, bot.rests), ((0., 0., 0.), 0))

    def test_after_a_chase_it_walks_back_and_stands(self):
        bot = self.bot()
        player = (0., 50., 0.)
        for i in range(1, 60):                         # acquire and chase
            bot.update(.05 * i, 2, player)
        self.assertGreater(bot.position[1], 5.)
        now = 3.
        for i in range(1, 400):                        # the player is gone
            now += .05
            bot.update(now)
        self.assertLess(math.dist(bot.position, (0., 0., 0.)), .5)
        self.assertEqual(bot.update(now + .05).speed, 0.)

    def test_its_reload_starts_with_its_last_round(self):
        bot = self.bot()
        bot.ammo = 2
        player = (0., 10., 0.)
        rounds = []
        for i in range(1, 60):
            rounds.extend(bot.update(.05 * i, 2, player).shots)
            if bot.reload_started is not None:
                break
        self.assertEqual(len(rounds), 2)
        self.assertAlmostEqual(bot.reload_started, rounds[-1].time)
