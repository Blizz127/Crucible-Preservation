"""Solo hunter patrol routes over the shipped navigation, plus the bot squad.

Routes come from `navigation.installed_mesh(level)`, which reads the
selected level's retail pak read-only. The behaviour itself lives in
`hunter_ai`; this module only turns the authored bot points into routes and
wires the squad together.
"""
import functools
import math

import hunter_ai
import navigation
import solo_modes

# Resampling step for the funnelled routes, in metres. One metre keeps every
# emitted position within 0.05 m of the installed surface (test_navigation).
ROUTE_STEP = 1.0


class Unroutable(ValueError):
    """A level's bot points are not all connected by its navigation mesh."""


def level_key(level=None):
    """A level as the routes are cached under: None for April2018 (also the
    default), else the map profile's own name. Every spelling of one level
    ('HotH_02', 'hoth_02') then shares one entry, and one parsed mesh."""
    if level is None:
        return None
    profile = solo_modes.MAPS[str(level).lower()]
    return None if profile is solo_modes.APRIL2018 else profile.level


def routes(level=None):
    """One funnelled leg per bot point, each to the next one, on the level's
    own navigation mesh (April2018 by default)."""
    return _routes(level_key(level))


@functools.lru_cache(maxsize=4)
def _routes(level):
    mesh = navigation.installed_mesh(level)
    points = solo_modes.MAPS[(level or 'april2018').lower()].bot_positions
    result = []
    for index, start in enumerate(points):
        leg = mesh.path(start, points[(index + 1) % len(points)], step=ROUTE_STEP)
        if leg is None:
            raise Unroutable('hunter patrol endpoints are not connected by navigation')
        result.append(tuple(leg))
    return tuple(result)


def circuits(level=None):
    """Per-bot closed circuits through all three bot points.

    Each bot starts on a different leg, so the three never walk in step, and
    every circuit has real corners for the funnel to cut rather than the
    single straight leg a pairwise route reduces to.
    """
    return _circuits(level_key(level))


@functools.lru_cache(maxsize=4)
def _circuits(level):
    legs = _routes(level)
    result = []
    for index in range(len(legs)):
        circuit = []
        for offset in range(len(legs)):
            leg = legs[(index + offset) % len(legs)]
            circuit.extend(leg[1:] if circuit else leg)
        result.append(tuple(circuit))
    return tuple(result)


# A grounded character's origin sits ON the navmesh, not above it. Measured
# from the player's own replicated input positions during the 2026-09-21 23:08
# April2018 run: 5,783 samples of (player Z - mesh surface Z) have their mode
# at -0.03 m with 1,577 samples; the +0.49/+0.92/+1.15 m clusters are jumps.
# So no standing offset is applied. The authored DropPoint transforms are NOT
# usable as a standing reference - they are drop pods, and one of the three
# sits 0.5 m below the walkable surface.
STANDING_HEIGHT_OFFSET = 0.0

# NavigationParams/RepulsionMarginMin on ai/guide_dummies/marine_guide_dummy.ai
# (ObjectStream element 0x8fb). Bots that share a target otherwise converge
# into one body - observed live as three hunters standing inside each other.
SEPARATION = 2.0
# TargetApproachParams/StopDesiredDistance (0x9f6); StopAllowedDistance is 3.0.
STOP_DISTANCE = 1.8


def ground(position, mesh=None, *, tolerance=3.0):
    """Re-project a position onto the navmesh surface at its own X/Y.

    `hunter_ai.advance` walks in straight lines between waypoints, which can
    pass below a convex surface. Projection corrects the height when a nearby
    surface exists; it does not validate the path or perform wall collision.

    Falls back to the input when the point is off-mesh (a jump target, or a
    gap), because forcing a height there would be worse than leaving it.
    """
    mesh = navigation.installed_mesh() if mesh is None else mesh
    located = mesh.locate(position, vertical_tolerance=tolerance)
    if located is None:
        return position
    return (position[0], position[1], located[1][2] + STANDING_HEIGHT_OFFSET)


def separate(positions, *, distance=SEPARATION, passes=32, mesh=None):
    """Push overlapping bots apart, projecting proposed positions to walkable ground.

    Retail does this with a constraint solver and repulsion margins; this is a
    bounded relaxation over the same authored margin, within 1e-7 metres for
    the three-bot squad when space permits. Additional passes resolve contacts
    disturbed by later pairs. With a mesh, each proposal is projected onto a
    nearby walkable triangle. This still cannot guarantee clearance from walls
    or resolve a crowded point with no nearby walkable space.
    """
    result = [list(p) for p in positions]

    def projected(candidate):
        if mesh is None:
            return candidate
        located = mesh.locate(candidate, vertical_tolerance=3.0)
        return None if located is None else list(located[1])

    for _ in range(passes):
        moved = False
        for i in range(len(result)):
            for j in range(i + 1, len(result)):
                a, b = result[i], result[j]
                dx, dy = b[0] - a[0], b[1] - a[1]
                span = math.hypot(dx, dy)
                if span >= distance - 1e-7:
                    continue
                if span == 0.:
                    # Choose a deterministic direction without inventing a
                    # distance: coincident bots need the entire margin.
                    ux, uy = 1., 0.
                else:
                    ux, uy = dx / span, dy / span
                push = (distance - span) / 2.0
                candidate_a = projected([a[0] - ux * push, a[1] - uy * push, a[2]])
                candidate_b = projected([b[0] + ux * push, b[1] + uy * push, b[2]])
                if candidate_a is not None and candidate_b is not None:
                    a[:], b[:] = candidate_a, candidate_b
                elif candidate_a is not None:
                    candidate_a = projected(
                        [a[0] - ux * 2.0 * push, a[1] - uy * 2.0 * push, a[2]])
                    if candidate_a is None:
                        continue
                    a[:] = candidate_a
                elif candidate_b is not None:
                    candidate_b = projected(
                        [b[0] + ux * 2.0 * push, b[1] + uy * 2.0 * push, b[2]])
                    if candidate_b is None:
                        continue
                    b[:] = candidate_b
                else:
                    continue
                moved = True
        if not moved:
            break
    return [tuple(p) for p in result]


def squad(entities=(0x01000003, 0x01000004, 0x01000005), tuning=None, mesh=None,
          level=None):
    """Build the hunter bots on their authored circuits, on the level's mesh."""
    tuning = tuning or hunter_ai.MARINE
    level = level_key(level)
    mesh = mesh if mesh is not None else navigation.installed_mesh(level)
    loops = circuits(level)
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
