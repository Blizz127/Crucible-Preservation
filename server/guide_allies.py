import math


def positions(state):
    """Return positions of the allies currently in play."""
    d = state.get('guide_allies')
    if d is None or d.squad is None:
        return {}
    live = {slot.entity for slot in d.live.values()}
    return {bot.entity: tuple(bot.position) for bot in d.squad.bots if bot.entity in live}


def ring_point(mesh, center, ring, rng, taken=(), *, attempts, search_distance):
    """Sample a separated walkable point in an annulus around center."""
    radius, inner, margin = ring
    index = getattr(mesh, 'component_index', None)
    index = index() if index is not None else None
    home = mesh.locate(center, search_distance)
    home = index.get(home[0]) if home is not None and index is not None else None
    for _ in range(attempts):
        angle = rng.uniform(-math.pi, math.pi)
        reach = math.sqrt(rng.uniform(inner * inner, radius * radius))
        candidate = (center[0] + math.cos(angle) * reach,
                     center[1] + math.sin(angle) * reach, center[2])
        found = mesh.locate(candidate, search_distance)
        if found is None or (home is not None and index.get(found[0]) != home):
            continue
        point = tuple(found[1])
        if any(math.dist(point[:2], other[:2]) < 2. * margin for other in taken):
            continue
        return point
    return None
