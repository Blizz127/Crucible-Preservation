"""Deterministic hunter-bot behaviour over the shipped navigation mesh.

No wall clock and no sockets live in here. `Bot.update(now, target)` is a pure
function of the tick sequence it is given, so the whole state machine is unit
testable: inject the time, inject the player's last replicated position.

Every tuning number in `Tuning` must carry its evidence in `source`. Nothing in
this module has been observed in the running game; see docs/21-hunter-ai.md for
which parts are asset-backed and which are still unproven.
"""
from dataclasses import dataclass, field
import math
import random

PATROL = 'patrol'
PURSUE = 'pursue'
ATTACK = 'attack'
# Not authored: the asset's approach/stop distances are still untraced
# (docs/21). Stabilises the PURSUE/ATTACK boundary observed flapping live.
ATTACK_ENTRY_MARGIN = 1.0
RETURN = 'return'
# Not authored: the largest height change one strafe step may take. A 0.225 m
# step (4.5 m/s at 20 Hz) on a 45-degree slope rises 0.225 m.
STRAFE_STEP_HEIGHT = 0.5


@dataclass(frozen=True)
class Tuning:
    """Authored behaviour constants. `source` names the installed evidence."""
    source: str
    patrol_speed: float
    pursue_speed: float
    aggro_near: float
    aggro_far: float
    deaggro: float
    attack_delay: float
    range_min: float
    range_max: float
    attack_duration: float
    round_interval: float
    min_time_between_attacks: float = 0.
    attack_angle: float = math.tau         # full cone width, radians
    range_vertical_max: float = math.inf
    aggro_angle: float = math.tau          # full cone width, radians
    aggro_origin_offset_local: tuple = (0., 0., 0.)
    aggro_target_offset_vertical: float = 0.
    patrol_near: float = 5.
    patrol_far: float = 15.
    patrol_anchor_limit: float = 50.
    patrol_attempts: int = 5
    repath_interval: float = 0.5
    repath_distance: float = 1.0
    turn_rate: float = math.tau            # radians/second
    move_turn_rate: float = 0.             # radians/second while moving; 0: turn_rate
    forced_aggro_on_damage: float = 0.     # seconds; 0: damage changes nothing
    forget_without_sight: float = math.inf  # seconds unseen before dropping a target
    abandon_attack_without_sight: float = math.inf  # seconds unseen ending an attack
    strafe_speed: float = 0.               # metres/second; 0: no strafing
    strafe_near: tuple = (0., 0.)          # influence 0 at [0], 1 at [1]
    strafe_far: tuple = (0., 0.)           # influence 1 at [0], 0 at [1]
    strafe_duration: tuple = (1., 1.)      # seconds before a new direction
    approach_stop: float = 0.              # metres short of the target
    magazine: int = 0                      # rounds; 0: no magazine authored
    reload_fill: float = 0.                # seconds from reload start to refill
    reload_animation: float = 0.           # ... to the end of using_reload
    reload_duration: float = 0.            # ... until the primary may fire again

    def __post_init__(self):
        ordered = (self.aggro_near <= self.aggro_far <= self.deaggro
                   and self.range_min < self.range_max
                   and 0 <= self.patrol_near <= self.patrol_far
                   and 0 <= self.reload_fill <= self.reload_duration
                   and 0 <= self.reload_animation <= self.reload_duration
                   and self.magazine >= 0 and self.forced_aggro_on_damage >= 0
                   and self.move_turn_rate >= 0
                   and self.forget_without_sight > 0
                   and self.abandon_attack_without_sight > 0
                   and self.strafe_speed >= 0 and self.approach_stop >= 0
                   and 0 <= self.strafe_near[0] <= self.strafe_near[1]
                   and self.strafe_far[0] <= self.strafe_far[1]
                   and (not self.strafe_speed
                        or self.strafe_near[1] <= self.strafe_far[0])
                   and 0 < self.strafe_duration[0] <= self.strafe_duration[1])
        positive = min(self.patrol_speed, self.pursue_speed, self.attack_duration,
                       self.round_interval, self.repath_interval, self.turn_rate,
                       self.aggro_angle, self.attack_angle, self.range_vertical_max,
                       self.patrol_attempts) > 0
        if (not ordered or not positive or self.attack_delay < 0
                or self.min_time_between_attacks < 0):
            raise ValueError('incoherent hunter tuning')


# Every number below is read out of the installed retail assets by
# tools/inspect_hunter_assets.py; the offsets are recorded in docs/21-hunter-ai.md
# and snapshots/hunter-ai-20260921/hunter-asset-fields.json. None of it has been
# observed in a running game.
MARINE = Tuning(
    source='ai/guide_dummies/marine_guide_dummy.ai + '
           'assets/player/marine/marine_default.movementconfig',
    # Movement Speeds[0] of marine_default.movementconfig (0x54): a Marine that
    # is not sprinting cannot exceed 5.5 m/s, so the guide AI's SpeedWander of
    # 8.0 is used for the chase only, where marine_sprint allows 8.5.
    patrol_speed=5.5,
    pursue_speed=8.0,                      # MovementPararams/SpeedChase, 0x7c2
    aggro_near=5.0,                        # AggroRadiusNear, 0x1092
    aggro_far=40.0,                        # AggroRadiusFar, 0x10ac
    aggro_angle=math.radians(120.),        # AggroAngleFar, 0x10c6
    deaggro=50.0,                          # DeaggroRadius, 0x10e0
    # ForcedAggroDurationOnDamage (0x1114): "When damaged by an entity,
    # consider it as a possible target for this many seconds, even if it is
    # outside our aggro range."
    forced_aggro_on_damage=6.0,
    # AggroCheckLineOfSight True (0x107b): no acquisition without sight.
    # TimeWithoutLoSToForgetCurrentTarget 8.0 (0x12da) and Attacks[0]
    # TimeWithoutLoSToTargetUntilAbandoningAttack 3.0 (0x1420). These act
    # only when the shard injects a sight test (Bot.visible).
    forget_without_sight=8.0, abandon_attack_without_sight=3.0,
    # MovementPararams/StrafeParams (0xaba..0xbcc) and TargetApproachParams,
    # with the retail editor tooltips (bot-authored-params.md): strafing has
    # 0% influence at 1 m (DistanceNearMin), 100% from 2 m (DistanceNearMax)
    # to 20 m (DistanceFarMin) and 0% again at 30 m (DistanceFarMax); speed
    # 4.5 m/s; "strafe for at least this long before changing direction"
    # U(1, 2) s. The approach goal radius is StopDesiredDistance 1.8 m.
    # Attacks[0].TranslationDisallowedPeriod 0/0: firing never locks movement.
    strafe_speed=4.5, strafe_near=(1.0, 2.0), strafe_far=(20.0, 30.0),
    strafe_duration=(1.0, 2.0), approach_stop=1.8,
    attack_delay=0.25,                     # AttackDelayForNewTargets, 0x12a6
    range_min=0.0,                         # Attacks[0].RangeMin, 0x1339
    range_max=30.0,                        # Attacks[0].RangeMax, 0x1353
    range_vertical_max=20.0,               # Attacks[0].RangeVerticalMax, 0x136d
    # AI/guide_dummies/marine_guide_dummy.ai: AggroOriginOffsetLocal (0x103e)
    # and AggroTargetOffsetVertical (0x1061). The authored values are both 1 m.
    aggro_origin_offset_local=(0., 0., 1.),
    aggro_target_offset_vertical=1.0,
    # Attacks[0].Angle (0x131f), read as a full cone width like AggroAngleFar.
    attack_angle=math.radians(90.),
    # An attack holds the primary for Attacks[0].Duration (0x1387); with
    # MinTimeBetweenAttacks 0.0 (0x12c0) attacks run back to back.
    attack_duration=2.0,
    min_time_between_attacks=0.0,
    # cdl/player/marine/ability/marine_primary_onfire.cdl (sha256 10ecfb3b...)
    # fires CreateProjectile on entry, then loops Delay 0.064 + Delay 0.032 +
    # WaitUntilTimePoint(GetTimePoint +0x60 ms): one round every 96 ms while
    # the input is held, i.e. 21 rounds in a 2.0 s attack.
    round_interval=0.096,
    patrol_near=5.0,                       # PatrolParams/MinDistance, 0x2c2
    patrol_far=15.0,                       # PatrolParams/MaxDistance, 0x2dc
    patrol_anchor_limit=50.0,              # PatrolParams/MaxDistanceFromCurrent, 0x310
    patrol_attempts=5,                     # MaxAttemptsForFindingPatrolDestination, 0x2ab
    # PassiveTurnParams/TurnInPlaceSpeed (0xc93) turns a standing bot. A
    # moving one steers through its spoofed player inputs, so its body turns
    # at marine_default.movementconfig's Max Turn Speed (0x143), 1440 deg/s.
    turn_rate=math.radians(90.),
    move_turn_rate=math.radians(1440.),
    # assets/player/marine/marine_resources.resourcesdata: Ammunition 0..24
    # (m_max 0xf9, m_Value 0x113); marine_primary.ability costs 1 per shot,
    # taken by the rifle loop 96 ms after each CreateProjectile.
    # cdl/player/marine/ability/marine_reload.cdl (sha256 a3a8c702...)
    # reloads by itself at Ammunition == min: FillResource after Delay
    # 0.224 + 0.384 + 0.608 = 1.216 s, using_reload off at +0.384 = 1.6 s and
    # Reload_Active off, which the rifle loop's entry waits on, at 1.984 s.
    magazine=24, reload_fill=1.216, reload_animation=1.6, reload_duration=1.984)


@dataclass(frozen=True)
class Shot:
    """A bot fired. `server/combat.py` owns validation, damage and death."""
    shooter: int
    target: int
    time: float
    origin: tuple
    direction: tuple
    range: float


@dataclass(frozen=True)
class Step:
    """One tick of bot output, ready to be packed into a Location update."""
    entity: int
    position: tuple
    facing: tuple
    yaw: float
    speed: float
    state: str
    target: int = None
    shots: tuple = ()
    firing: bool = False     # the primary is held: an attack is in progress
    reloading: bool = False  # inside marine_reload.cdl's using_reload window


def plan_distance(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def advance(points, index, position, distance):
    """Walk `distance` metres from `position` along points[index:].

    Returns (position, index, direction, remaining). `remaining` is the
    distance left over once the last waypoint is reached, which lets the caller
    decide whether to reverse a patrol or end a pursuit.
    """
    if index >= len(points) or distance < 0:
        raise ValueError('invalid walk')
    direction = (0., 0., 0.)
    while index < len(points):
        leg = math.dist(position, points[index])
        if leg <= 1e-9:
            index += 1
            continue
        unit = tuple((points[index][i] - position[i]) / leg for i in range(3))
        direction = unit
        if distance < leg:
            return (tuple(position[i] + unit[i]*distance for i in range(3)),
                    index, unit, 0.)
        position, distance = points[index], distance - leg
        index += 1
    return position, len(points) - 1, direction, distance


def yaw_of(direction):
    """Native forward is +Y, so yaw is measured from +Y towards +X."""
    if abs(direction[0]) < 1e-12 and abs(direction[1]) < 1e-12:
        return 0.
    return math.atan2(direction[0], direction[1])


def turn_towards(current, wanted, limit):
    """Shortest-arc yaw step, clamped to `limit` radians and normalised."""
    delta = (wanted - current + math.pi) % math.tau - math.pi
    return (current + max(-limit, min(limit, delta)) + math.pi) % math.tau - math.pi


@dataclass
class Bot:
    entity: int
    route: tuple
    tuning: Tuning
    mesh: object = None
    position: tuple = None
    yaw: float = 0.
    wander: bool = False
    rng: object = None
    anchor: tuple = None
    state: str = PATROL
    target: int = None
    acquired_at: float = None
    attack_started: float = None
    attack_ended: float = None
    rounds_fired: int = 0
    # Rounds left in the magazine. None (the default) models no magazine, so
    # the rifle never reloads; the shard opts in with tuning.magazine.
    ammo: int = None
    reload_started: float = None
    forced_until: float = None
    strafe: bool = False          # opt-in: move while attacking (StrafeParams)
    move_turn: bool = False       # opt-in: steer at tuning.move_turn_rate when moving
    # Opt-in sight test visible(eye, target) -> bool. None: everything is seen.
    visible: object = None
    sight_interval: float = 0.2   # not authored: how often the sight line is re-tested
    # _seen_at also counts damage, so a target that just hit the bot is not
    # forgotten; _sighted_at counts the sight test only and gates the attack.
    _seen_at: float = None
    _sighted_at: float = None
    _sight_checked: float = None
    _sight: bool = True
    _strafe_sign: float = 0.
    _strafe_until: float = None
    _round_anchor: float = None
    _anchor_rounds: int = 0
    _clock: float = None
    _plan: list = field(default_factory=list)
    _index: int = 1
    _planned_at: float = None
    _planned_for: tuple = None
    _forward: bool = True
    dit_patrol_pause_range: tuple = ()
    dit_patrol_move_range: tuple = ()
    _dit_patrol_pause_until: float = None
    _dit_patrol_move_until: float = None

    def __post_init__(self):
        if len(self.route) < 2:
            raise ValueError('a patrol route needs at least two points')
        if self.position is None:
            self.position = self.route[0]
        if self.anchor is None:
            self.anchor = self.position
        if self.wander:
            if self.mesh is None:
                raise ValueError('wandering needs a navigation mesh')
            if self.rng is None:
                # Seeded per entity so a restart replays the same patrol.
                self.rng = random.Random(self.entity)
        if not self._plan:
            self._patrol_plan()

    # -- planning ---------------------------------------------------------

    def _navigate(self, destination):
        """Funnelled navmesh path to `destination`, or a straight line."""
        if self.mesh is not None:
            path = self.mesh.path(self.position, destination)
            if path:
                return path
            return None
        return [self.position, destination]

    def _repath(self, now, destination):
        fresh = (self._planned_at is not None
                 and now - self._planned_at < self.tuning.repath_interval)
        near = (self._planned_for is not None
                and math.dist(self._planned_for, destination)
                < self.tuning.repath_distance)
        if fresh and near and self._index < len(self._plan):
            return True
        plan = self._navigate(destination)
        if plan is None or len(plan) < 2:
            return False
        self._plan, self._index = plan, 1
        self._planned_at, self._planned_for = now, destination
        return True

    def _patrol_plan(self):
        """Next patrol leg: an authored wander hop, or the fixed route.

        PatrolParams in marine_guide_dummy.ai describe a wander -- up to five
        attempts at a destination 5..15 m away and no more than 50 m from the
        anchor -- not a fixed path. The route remains the fallback for the
        stateless patrol probe and whenever no mesh is injected.
        """
        if self.wander:
            found = self.mesh.wander_point(
                self.position, self.tuning.patrol_near, self.tuning.patrol_far,
                self.rng, self.tuning.patrol_attempts,
                anchor=self.anchor, anchor_limit=self.tuning.patrol_anchor_limit)
            if found is not None:
                self._plan, self._index = list(found[1]), 1
                self._planned_at = self._planned_for = None
                return
            # Every attempt failed: stand still this leg and retry next tick.
            self._plan, self._index = [self.position, self.position], 1
            self._planned_at = self._planned_for = None
            return
        points = list(self.route if self._forward else self.route[::-1])
        self._plan, self._index = points, 1
        self._planned_at = self._planned_for = None

    # -- detection --------------------------------------------------------

    def _sense(self, now, target, distance, aim, seen=True):
        """Acquire or drop a target on the authored radii and aggro cone.

        AggroRadiusNear acquires in any direction; out to AggroRadiusFar the
        target must also be inside AggroAngleFar of the bot's facing.
        `AggroCheckLineOfSight` needs a sight test: with `visible` injected, an
        unseen target is not acquired, and a held one is forgotten after
        TimeWithoutLoSToForgetCurrentTarget unless forced aggro holds it.
        """
        if target is None or distance is None:
            self._disengage()
            return False
        forced = (self.forced_until is not None and now < self.forced_until
                  and self.target == target)
        if self.target is not None:
            if seen:
                self._seen_at = self._sighted_at = now
            elif (not forced and self._seen_at is not None
                  and now - self._seen_at > self.tuning.forget_without_sight):
                self._disengage()
                return False
            if distance > self.tuning.deaggro and not forced:
                self._disengage()
                return False
            return True
        if not seen:
            return False
        if distance > self.tuning.aggro_far:
            return False
        if distance > self.tuning.aggro_near:
            offset = (yaw_of(aim) - self.yaw + math.pi) % math.tau - math.pi
            if abs(offset) > self.tuning.aggro_angle / 2:
                return False
        self.target, self.acquired_at, self._seen_at = target, now, now
        self._sighted_at = now
        self.attack_started = self.attack_ended = None
        return True

    def _disengage(self):
        self.target = self.acquired_at = None
        self.attack_started = self.attack_ended = None
        self.rounds_fired = 0
        self.forced_until = None
        self._seen_at = self._sighted_at = None

    def note_damage(self, now, attacker):
        """ForcedAggroDurationOnDamage: whoever hit the bot becomes its target.

        A new target restarts AttackDelayForNewTargets; the deaggro radius is
        ignored until the forced window ends. Line of sight is not modelled,
        so it cannot end the window early either.
        """
        if not self.tuning.forced_aggro_on_damage:
            return
        if self.target != attacker:
            self.target, self.acquired_at = attacker, now
            self.attack_started = self.attack_ended = None
            self.rounds_fired = 0
            self._sighted_at = None
        self._seen_at = now
        self.forced_until = now + self.tuning.forced_aggro_on_damage

    def _may_attack(self, now, distance, aim):
        """Attacks[0]'s start gate: range, vertical range, cone and delays."""
        if self.target is None or self.acquired_at is None:
            return False
        if not self.tuning.range_min <= distance <= self.tuning.range_max:
            return False
        if abs(aim[2]) > self.tuning.range_vertical_max:
            return False
        if any(aim[:2]):
            offset = (yaw_of(aim) - self.yaw + math.pi) % math.tau - math.pi
            if abs(offset) > self.tuning.attack_angle / 2:
                return False
        if now - self.acquired_at < self.tuning.attack_delay:
            return False
        return (self.attack_ended is None or now - self.attack_ended
                >= self.tuning.min_time_between_attacks)

    def _rounds(self, now, target_position):
        """Rounds due by `now` in the current attack, at their scripted times.

        A finite attack runs its whole Duration once started: the asset's
        AbandonInfiniteAttackIfTargetOutOfMaxRange applies to infinite attacks
        only. It ends early only when the target is dropped.
        """
        shots = []
        end = self.attack_started + self.tuning.attack_duration
        while True:
            due = self._round_anchor + self._anchor_rounds * self.tuning.round_interval
            if self.reload_started is not None:
                # The held trigger fires again only once Reload_Active clears.
                ready = self.reload_started + self.tuning.reload_duration
                if ready > now or ready >= end - 1e-9:
                    break
                self._finish_reload(ready)
                due = self._round_anchor + self._anchor_rounds * self.tuning.round_interval
            if due > now or due >= end - 1e-9:
                break
            origin = tuple(self.position[i] + self.tuning.aggro_origin_offset_local[i]
                           for i in range(3))
            target = (target_position[0], target_position[1],
                      target_position[2] + self.tuning.aggro_target_offset_vertical)
            aim = tuple(target[i] - origin[i] for i in range(3))
            length = math.dist((0., 0., 0.), aim) or 1.
            shots.append(Shot(self.entity, self.target, due, origin,
                              tuple(v / length for v in aim), length))
            self.rounds_fired += 1
            self._anchor_rounds += 1
            if self.ammo is not None:
                self.ammo -= 1
                if self.ammo <= 0:
                    # The loop's Ammunition -1 empties the magazine 96 ms
                    # after this projectile; the reload script starts then.
                    self.reload_started = due + self.tuning.round_interval
        if now >= end - 1e-9:
            self.attack_started, self.attack_ended = None, end
            self.rounds_fired = 0
        return shots

    def _finish_reload(self, now):
        """Refill once a reload has run its course; resume the loop from there."""
        if self.reload_started is None:
            return
        ready = self.reload_started + self.tuning.reload_duration
        if now < ready:
            return
        self.ammo, self.reload_started = self.tuning.magazine, None
        if self.attack_started is not None:
            due = self._round_anchor + self._anchor_rounds * self.tuning.round_interval
            if due < ready:
                self._round_anchor, self._anchor_rounds = ready, 0

    def strafe_influence(self, distance):
        """StrafeParams influence on movement, 0..1, by distance to the target."""
        (near_in, near_out), (far_in, far_out) = (self.tuning.strafe_near,
                                                  self.tuning.strafe_far)
        if distance <= near_in or distance >= far_out:
            return 0.
        if distance < near_out:
            return (distance - near_in) / (near_out - near_in)
        if distance <= far_in:
            return 1.
        return (far_out - distance) / (far_out - far_in)

    def _strafe_step(self, now, delta, aim, distance):
        """One tick of attack movement: sideways by influence, inward by the rest.

        The inward share uses patrol_speed, the Marine's non-sprint forward
        cap, rather than the chase speed, and stops at the approach goal
        radius. A sidestep that would leave the navigation mesh reverses
        the strafe direction (StopWhenBlocked is False). Returns metres moved.
        """
        tuning = self.tuning
        if self.rng is None:
            self.rng = random.Random(self.entity)
        if self._strafe_until is None or now >= self._strafe_until:
            self._strafe_sign = self.rng.choice((-1., 1.))
            self._strafe_until = now + self.rng.uniform(*tuning.strafe_duration)
        plan = math.hypot(aim[0], aim[1])
        if plan < 1e-9:
            return 0.
        toward = (aim[0] / plan, aim[1] / plan)
        side = (toward[1], -toward[0])
        influence = self.strafe_influence(distance)
        lateral = tuning.strafe_speed * influence * delta
        inward = min(tuning.patrol_speed * (1. - influence) * delta,
                     max(0., plan - tuning.approach_stop))
        here = (None if self.mesh is None
                else self.mesh.locate(self.position, vertical_tolerance=2.0))
        for sign in (self._strafe_sign, -self._strafe_sign):
            candidate = (self.position[0] + side[0] * sign * lateral + toward[0] * inward,
                         self.position[1] + side[1] * sign * lateral + toward[1] * inward,
                         self.position[2])
            if self.mesh is not None:
                # A sidestep stays on the walkable surface the bot stands on:
                # its own triangle or a linked one, within half a metre of its
                # height, so it can neither climb nor drop a ledge.
                found = self.mesh.locate(candidate, vertical_tolerance=STRAFE_STEP_HEIGHT)
                if found is None:
                    continue
                if (here is not None and found[0] != here[0]
                        and found[0] not in {key for key, _ in self.mesh.neighbours(here[0])}):
                    continue
                candidate = found[1]
            self._strafe_sign = sign
            moved = math.dist(self.position, candidate)
            self.position = candidate
            return moved
        return 0.

    def _can_see(self, now, aim_target):
        """The injected sight test from the eye to the aim point, re-run at most
        every sight_interval seconds. Without one, everything is visible."""
        if self.visible is None:
            return True
        if self._sight_checked is None or now - self._sight_checked >= self.sight_interval:
            eye = tuple(self.position[i] + self.tuning.aggro_origin_offset_local[i]
                        for i in range(3))
            self._sight = bool(self.visible(eye, aim_target))
            self._sight_checked = now
        return self._sight

    def reloading(self, now):
        """True inside the reload's using_reload window."""
        return (self.reload_started is not None and self.reload_started <= now
                < self.reload_started + self.tuning.reload_animation)

    # -- tick -------------------------------------------------------------

    def update(self, now, target=None, target_position=None):
        """Advance to absolute time `now`. Returns a `Step`.

        `target` is a network entity id and `target_position` its last
        replicated position; pass both or neither. The first call only
        establishes the clock epoch: it senses and aims but covers no ground.
        """
        if not math.isfinite(now):
            raise ValueError('invalid tick time')
        if self._clock is None:
            self._clock = now
        if now < self._clock:
            raise ValueError('hunter clock moved backwards')
        delta, self._clock = now - self._clock, now
        if (target is None) != (target_position is None):
            raise ValueError('target id and position must be given together')
        self._finish_reload(now)

        origin = tuple(self.position[i] + self.tuning.aggro_origin_offset_local[i]
                       for i in range(3))
        aim_target = (None if target_position is None else
                      (target_position[0], target_position[1],
                       target_position[2] + self.tuning.aggro_target_offset_vertical))
        distance = None if aim_target is None else math.dist(origin, aim_target)
        aim = (None if aim_target is None
               else tuple(aim_target[i] - origin[i] for i in range(3)))
        # Sight decides nothing for a target that is neither held nor within
        # acquisition range, so the ray is skipped there.
        seen = (aim_target is not None
                and (self.target is not None or distance <= self.tuning.aggro_far)
                and self._can_see(now, aim_target))
        engaged = self._sense(now, target, distance, aim, seen)

        shots, speed, heading = (), 0., None
        if not engaged:
            if self.state in (PURSUE, ATTACK):
                self.state = RETURN
                if not self._repath(now, self._home()):
                    self.state = PATROL
                    self._patrol_plan()
            speed = self.tuning.patrol_speed
            if (self.state == PATROL and self.dit_patrol_pause_range
                    and self.dit_patrol_move_range and self.rng is not None):
                if self._dit_patrol_pause_until is not None:
                    if now < self._dit_patrol_pause_until:
                        speed = 0.0
                    else:
                        self._dit_patrol_pause_until = None
                        low, high = self.dit_patrol_move_range
                        self._dit_patrol_move_until = now + self.rng.uniform(low, high)
                elif self._dit_patrol_move_until is None:
                    low, high = self.dit_patrol_move_range
                    self._dit_patrol_move_until = now + self.rng.uniform(low, high)
                elif now >= self._dit_patrol_move_until:
                    self._dit_patrol_move_until = None
                    low, high = self.dit_patrol_pause_range
                    self._dit_patrol_pause_until = now + self.rng.uniform(low, high)
                    speed = 0.0
            elif self.state != PATROL:
                self._dit_patrol_pause_until = self._dit_patrol_move_until = None
            elif self._dit_patrol_pause_until is not None:
                if now < self._dit_patrol_pause_until:
                    speed = 0.0
                else:
                    self._dit_patrol_pause_until = None
        elif distance <= self.tuning.range_max - (
                0. if self.state == ATTACK else ATTACK_ENTRY_MARGIN):
            # In range, including closer than RangeMin: stand and face. A
            # pursuing bot closes ATTACK_ENTRY_MARGIN inside RangeMax before
            # stopping, so a target drifting at the range edge does not flip
            # it between PURSUE and ATTACK every tick.
            self.state, heading = ATTACK, aim
        else:
            self.state, speed = PURSUE, self.tuning.pursue_speed
            if not self._repath(now, target_position):
                self.state, speed, heading = ATTACK, 0., aim  # unreachable

        moved = 0.
        if speed > 0 and delta > 0:
            before = self.position
            self.position, self._index, direction, left = advance(
                self._plan, self._index, self.position, speed * delta)
            moved = math.dist(before, self.position)
            if any(direction):
                heading = direction
            if left > 0:
                if self.state == PATROL:
                    if (self.dit_patrol_pause_range and self.rng is not None
                            and not self.dit_patrol_move_range):
                        low, high = self.dit_patrol_pause_range
                        self._dit_patrol_pause_until = now + self.rng.uniform(low, high)
                    self._forward = self.wander or not self._forward
                    self._patrol_plan()
                elif self.state == RETURN:
                    self.state = PATROL
                    self._patrol_plan()
        elif (self.strafe and engaged and self.state == ATTACK and delta > 0
              and self.tuning.strafe_speed > 0
              and distance <= self.tuning.range_max):
            # Facing stays on the target (heading = aim) while the feet move.
            moved = self._strafe_step(now, delta, aim, distance)
            self._planned_at = self._planned_for = None

        if heading is not None and any(heading[:2]):
            rate = (self.tuning.move_turn_rate
                    if self.move_turn and moved > 0 and self.tuning.move_turn_rate
                    else self.tuning.turn_rate)
            self.yaw = turn_towards(self.yaw, yaw_of(heading), rate * max(delta, 0.))

        shots = []
        if engaged:
            if (self.attack_started is not None and self._sighted_at is not None
                    and now - self._sighted_at > self.tuning.abandon_attack_without_sight):
                # TimeWithoutLoSToTargetUntilAbandoningAttack
                self.attack_started, self.attack_ended = None, now
                self.rounds_fired = 0
            if self.attack_started is not None:
                shots.extend(self._rounds(now, target_position))
            current_origin = tuple(
                self.position[i] + self.tuning.aggro_origin_offset_local[i]
                for i in range(3))
            aim = tuple(aim_target[i] - current_origin[i] for i in range(3))
            # TimeWithoutLoSToTargetUntilAbandoningAttack: a held target out of
            # sight for less than the window still draws back-to-back attacks;
            # past it the attack is abandoned (above) and none restarts. Damage
            # does not open the window: an attacker never seen is not fired on.
            in_sight_window = seen or (
                self._sighted_at is not None
                and now - self._sighted_at <= self.tuning.abandon_attack_without_sight)
            if (self.attack_started is None and in_sight_window
                    and self._may_attack(now, math.dist(current_origin, aim_target), aim)):
                self.attack_started, self.rounds_fired = now, 0
                self._round_anchor, self._anchor_rounds = now, 0
                shots.extend(self._rounds(now, target_position))

        # marine_primary_onfire.cdl clears using_primary1 when its loop exits
        # for the reload; marine_reload.cdl holds using_reload meanwhile.
        empty = self.reload_started is not None and self.reload_started <= now
        return Step(self.entity, self.position, self.facing(), self.yaw,
                    moved / delta if delta > 0 else 0., self.state,
                    self.target, tuple(shots),
                    self.attack_started is not None and not empty,
                    self.reloading(now))

    def facing(self):
        return (math.sin(self.yaw), math.cos(self.yaw), 0.)

    def _home(self):
        """Where a bot goes once it loses its target."""
        if self.wander:
            return self.anchor
        return min(self.route, key=lambda p: math.dist(self.position, p))


class Squad:
    """All bots on one tick, sharing one injected clock and player position."""

    def __init__(self, bots):
        self.bots = list(bots)

    def update(self, now, target=None, target_position=None, *, active_entities=None):
        steps = []
        for bot in self.bots:
            if active_entities is not None and bot.entity not in active_entities:
                if not math.isfinite(now) or (bot._clock is not None and now < bot._clock):
                    raise ValueError('invalid suspended hunter clock')
                bot._clock = now
                bot._disengage()
                continue
            steps.append(bot.update(now, target, target_position))
        return steps

    def shots(self, steps):
        return [shot for step in steps for shot in step.shots]
