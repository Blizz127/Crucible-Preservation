import math
from dataclasses import dataclass


def distance_to_capsule(point, feet, height):

    z = min(max(point[2], feet[2]), feet[2] + height)
    return math.dist(point, (feet[0], feet[1], z))


@dataclass
class Spit:
    shooter: int
    origin: tuple
    direction: tuple
    length: float
    target: tuple
    started: float
    walked: float = 0.0

    def position_at(self, distance, *, speed, gravity):
        x, y, z = (p + d * distance for p, d in zip(self.origin, self.direction))
        elapsed = distance / speed
        return x, y, z + 0.5 * gravity * elapsed * elapsed
