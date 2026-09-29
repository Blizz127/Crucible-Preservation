import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Shot:

    shooter: int
    target: int
    time: float
    origin: tuple
    direction: tuple
    range: float


@dataclass(frozen=True)
class Step:

    entity: int
    position: tuple
    facing: tuple
    yaw: float
    speed: float
    state: str
    target: int = None
    shots: tuple = ()
    firing: bool = False


def plan_distance(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def advance(points, index, position, distance):

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

    if abs(direction[0]) < 1e-12 and abs(direction[1]) < 1e-12:
        return 0.
    return math.atan2(direction[0], direction[1])


def turn_towards(current, wanted, limit):

    delta = (wanted - current + math.pi) % math.tau - math.pi
    return (current + max(-limit, min(limit, delta)) + math.pi) % math.tau - math.pi


class Squad:

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
