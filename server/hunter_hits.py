import math
import random
from dataclasses import dataclass


def bloom_increment(bloom, points):
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if bloom <= x1:
            return y0 + (y1 - y0) * (max(bloom, x0) - x0) / (x1 - x0)
    return points[-1][1]


def spread_degrees(bloom, gun):
    return gun.spread_min + (gun.spread_max - gun.spread_min) * bloom


@dataclass(frozen=True)
class Landing:
    shooter: int
    target: int
    time: float
    damage: float


class HunterGunnery:

    def __init__(self, *, default_gun, damage_for, accuracy_scale, seed=0,
                 target_radius):
        if not 0.0 <= accuracy_scale <= 1.0:
            raise ValueError('accuracy scale must be within 0..1')
        self.rng = random.Random(seed)
        self.accuracy_scale = accuracy_scale
        self.target_radius = target_radius
        self.default_gun = default_gun
        self.damage_for = damage_for
        self.bloom = {}
        self.last_shot = {}

    def resolve(self, shot):

        gun = self.default_gun
        bloom = self.bloom.get(shot.shooter, 0.0)
        last = self.last_shot.get(shot.shooter)
        if last is not None and shot.time - last >= gun.bloom_recovery:
            bloom = 0.0
        cone = math.tan(math.radians(spread_degrees(bloom, gun)))
        self.bloom[shot.shooter] = min(1.0, bloom + bloom_increment(bloom, gun.bloom_per_shot))
        self.last_shot[shot.shooter] = shot.time

        deviation = cone * math.sqrt(self.rng.random())
        aimed = self.rng.random() < self.accuracy_scale
        if (not aimed or shot.range <= 0 or shot.range > gun.reach
                or deviation > self.target_radius / shot.range):
            return None
        return Landing(shot.shooter, shot.target,
                       shot.time + shot.range / gun.weapon.speed,
                       self.damage_for(gun.weapon, distance=shot.range))
