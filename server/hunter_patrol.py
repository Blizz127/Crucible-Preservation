import math


def separate(positions, *, distance, passes=32, mesh=None, vertical_tolerance=None):

    result = [list(p) for p in positions]

    def projected(candidate):
        if mesh is None:
            return candidate
        if vertical_tolerance is None:
            located = mesh.locate(candidate)
        else:
            located = mesh.locate(candidate, vertical_tolerance=vertical_tolerance)
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


def sample(route, seconds, *, speed):

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
