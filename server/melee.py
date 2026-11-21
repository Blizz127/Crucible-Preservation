"""Check overlap between a horizontal attack box and standing targets."""

import math


def in_reach(melee, origin, facing, target, *, radius, height):
    """Whether a capsule standing at `target` is inside `melee`'s box.

    `origin` and `target` are feet positions; `facing` is the attacker's
    forward vector, of which only the horizontal part is used. False when
    the facing has no horizontal part.
    """
    length = math.hypot(facing[0], facing[1])
    if length < 1e-6:
        return False
    forward = (facing[0] / length, facing[1] / length)
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    ahead = dx * forward[0] + dy * forward[1]
    aside = abs(dx * forward[1] - dy * forward[0])
    if not -radius <= ahead <= melee.reach + radius:
        return False
    if aside > melee.width / 2. + radius:
        return False
    low = origin[2] + melee.origin_elevation - melee.height / 2.
    return target[2] <= low + melee.height and target[2] + height >= low


def targets_in_reach(melee, origin, facing, positions, *, radius, height):
    """The ids in {id: feet position} that the swing reaches, nearest first."""
    hit = [(math.dist(origin, position), entity)
           for entity, position in positions.items()
           if in_reach(melee, origin, facing, position, radius=radius, height=height)]
    return [entity for _, entity in sorted(hit)]
