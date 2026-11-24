import math
from dataclasses import dataclass


def distance_to_capsule(point, feet, height):

    z = min(max(point[2], feet[2]), feet[2] + height)
    return math.dist(point, (feet[0], feet[1], z))


def capsule_entry(start, end, feet, height, radius, *, sphere_entry):



    dx, dy, dz = (b - a for a, b in zip(start, end))
    x, y, z = (a - b for a, b in zip(start, feet))
    move = (dx, dy, dz)

    bottom = sphere_entry((x, y, z), move, radius)
    top = sphere_entry((x, y, z - height), move, radius)
    side = sphere_entry((x, y, 0.), (dx, dy, 0.), radius)
    if side is not None and not 0. <= z + side * dz <= height:
        side = None
    return min((t for t in (bottom, top, side) if t is not None), default=None)


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
