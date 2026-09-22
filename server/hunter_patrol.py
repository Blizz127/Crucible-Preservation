"""Solo hunter patrol routes over the shipped navigation, plus the bot squad.

Routes come from `navigation.installed_mesh()`, which reads the retail level
pak read-only. The behaviour itself lives in `hunter_ai`; this module only
turns the authored drop points into routes and wires the squad together.
"""
import functools
import math

import hunter_ai
import navigation
import solo_modes

# Resampling step for the funnelled routes, in metres. One metre keeps every
# emitted position within 0.05 m of the installed surface (test_navigation).
ROUTE_STEP = 1.0


@functools.lru_cache(maxsize=1)
def routes():
    """One funnelled leg per authored drop point, each to the next one."""
    mesh = navigation.installed_mesh()
    points = solo_modes.HIVES.bot_positions
    result = []
    for index, start in enumerate(points):
        leg = mesh.path(start, points[(index + 1) % len(points)], step=ROUTE_STEP)
        if leg is None:
            raise ValueError('hunter patrol endpoints are not connected by navigation')
        result.append(tuple(leg))
    return tuple(result)


@functools.lru_cache(maxsize=1)
def circuits():
    """Per-bot closed circuits through all three authored drop points.

    Each bot starts on a different leg, so the three never walk in step, and
    every circuit has real corners for the funnel to cut rather than the
    single straight leg a pairwise route reduces to.
    """
    legs = routes()
    result = []
    for index in range(len(legs)):
        circuit = []
        for offset in range(len(legs)):
            leg = legs[(index + offset) % len(legs)]
            circuit.extend(leg[1:] if circuit else leg)
        result.append(tuple(circuit))
    return tuple(result)


def squad(entities=(0x01000003, 0x01000004, 0x01000005), tuning=None, mesh=None):
    """Build the three Alpha Hunters bots on their authored circuits."""
    tuning = tuning or hunter_ai.MARINE
    mesh = mesh if mesh is not None else navigation.installed_mesh()
    loops = circuits()
    return hunter_ai.Squad([
        hunter_ai.Bot(entity, loops[index % len(loops)], tuning, mesh=mesh,
                      position=loops[index % len(loops)][0], wander=True)
        for index, entity in enumerate(entities)])


def sample(route, seconds, *, speed=None):
    """Move back and forth along a route at `speed` metres per second.

    Retained for the stateless patrol probe. `hunter_ai.Bot` is the stateful
    replacement; this stays because it is a pure function of absolute time and
    therefore survives a dropped tick without drifting.
    """
    speed = hunter_ai.MARINE.patrol_speed if speed is None else speed
    if len(route) < 2 or not math.isfinite(seconds) or seconds < 0 or speed <= 0:
        raise ValueError('invalid patrol')
    lengths = [math.dist(a, b) for a, b in zip(route, route[1:])]
    total = sum(lengths)
    if total <= 0:
        raise ValueError('empty patrol distance')
    progress = seconds * speed % (2 * total)
    reverse = progress > total
    if reverse:
        progress = 2 * total - progress
    for a, b, length in zip(route, route[1:], lengths):
        if length == 0:
            continue
        if progress <= length:
            fraction = progress / length
            position = tuple(a[i] + (b[i]-a[i])*fraction for i in range(3))
            direction = tuple((b[i]-a[i])*(-1 if reverse else 1) for i in range(3))
            return position, direction
        progress -= length
    return route[-1], tuple(route[-1][i]-route[-2][i] for i in range(3))


def unpack_position(packed):
    """Inverse of `shard_stub.pack_practice_spawn_transform`'s position half.

    Retail packer 4f47e10 puts X and Y in 22 bits each over +/-4500 metres and
    Z in 20 bits over +/-1300. Accepts the 8-byte position or the full 12-byte
    transform, so an inbound autonomous Location update can be turned straight
    into the target position `hunter_ai.Bot.update` wants.
    """
    if len(packed) not in (8, 12):
        raise ValueError('expected an 8 or 12 byte packed transform')
    value = int.from_bytes(packed[:8], 'big')
    return (((value >> 42) & 0x3fffff) * 4500 / 2097151.5 - 4500,
            ((value >> 20) & 0x3fffff) * 4500 / 2097151.5 - 4500,
            (value & 0xfffff) * 1300 / 524287.5 - 1300)
