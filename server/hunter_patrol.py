import math


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
