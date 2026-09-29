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

    def __post_init__(self):
        ordered = (self.aggro_near <= self.aggro_far <= self.deaggro
                   and self.range_min < self.range_max
                   and 0 <= self.patrol_near <= self.patrol_far)
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
    # PassiveTurnParams/TurnInPlaceSpeed (0xc93). The movement config's own
    # Max Turn Speed is 1440 deg/s; this is the AI's turn, which is slower.
    turn_rate=math.radians(90.))


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

    def _sense(self, now, target, distance, aim):
        """Acquire or drop a target on the authored radii and aggro cone.

        AggroRadiusNear acquires in any direction; out to AggroRadiusFar the
        target must also be inside AggroAngleFar of the bot's facing.
        `AggroCheckLineOfSight` is true in the asset but no collision geometry
        has been decoded, so no occlusion test is applied.
        """
        if target is None or distance is None:
            self._disengage()
            return False
        if self.target is not None:
            if distance > self.tuning.deaggro:
                self._disengage()
                return False
            return True
        if distance > self.tuning.aggro_far:
            return False
        if distance > self.tuning.aggro_near:
            offset = (yaw_of(aim) - self.yaw + math.pi) % math.tau - math.pi
            if abs(offset) > self.tuning.aggro_angle / 2:
                return False
        self.target, self.acquired_at = target, now
        self.attack_started = self.attack_ended = None
        return True

    def _disengage(self):
        self.target = self.acquired_at = None
        self.attack_started = self.attack_ended = None
        self.rounds_fired = 0

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
            due = self.attack_started + self.rounds_fired * self.tuning.round_interval
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
        if now >= end - 1e-9:
            self.attack_started, self.attack_ended = None, end
            self.rounds_fired = 0
        return shots

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

        origin = tuple(self.position[i] + self.tuning.aggro_origin_offset_local[i]
                       for i in range(3))
        aim_target = (None if target_position is None else
                      (target_position[0], target_position[1],
                       target_position[2] + self.tuning.aggro_target_offset_vertical))
        distance = None if aim_target is None else math.dist(origin, aim_target)
        aim = (None if aim_target is None
               else tuple(aim_target[i] - origin[i] for i in range(3)))
        engaged = self._sense(now, target, distance, aim)

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

        if heading is not None and any(heading[:2]):
            self.yaw = turn_towards(self.yaw, yaw_of(heading),
                                    self.tuning.turn_rate * max(delta, 0.))

        shots = []
        if engaged:
            if self.attack_started is not None:
                shots.extend(self._rounds(now, target_position))
            current_origin = tuple(
                self.position[i] + self.tuning.aggro_origin_offset_local[i]
                for i in range(3))
            aim = tuple(aim_target[i] - current_origin[i] for i in range(3))
            if (self.attack_started is None
                    and self._may_attack(now, math.dist(current_origin, aim_target), aim)):
                self.attack_started, self.rounds_fired = now, 0
                shots.extend(self._rounds(now, target_position))

        return Step(self.entity, self.position, self.facing(), self.yaw,
                    moved / delta if delta > 0 else 0., self.state,
                    self.target, tuple(shots), self.attack_started is not None)

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
