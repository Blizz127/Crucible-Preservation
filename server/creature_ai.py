import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CreatureEvent:
    kind: str
    time: float
    creature_id: int
    target_id: int = 0


@dataclass(frozen=True)
class CreatureAttack:

    time: float
    creature_id: int
    target_id: int
    attack_asset: str
    origin: tuple
    target_position: tuple
    kind: str = 'projectile'
    radius: float = 0.0


def lowest(low, high):

    return low


def horizontal_distance(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def distance(a, b):
    return math.dist(a[:3], b[:3])


def facing_yaw(origin, target):

    return math.atan2(-(target[0] - origin[0]), target[1] - origin[1])


def angle_between(yaw, origin, target):

    delta = facing_yaw(origin, target) - yaw
    return abs(math.degrees(math.atan2(math.sin(delta), math.cos(delta))))
