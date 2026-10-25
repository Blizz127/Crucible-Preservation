"""Sample caller-supplied height grids and test line segments against them."""
import math


class Terrain:

    def __init__(self, leaves, size, sector, units_per_metre, span=None):
        if units_per_metre <= 0:
            raise ValueError("units_per_metre must be positive")
        self.units_per_metre = units_per_metre
        self.leaves, self.size, self.sector, self.span = leaves, size, sector, span

    def height(self, x, y):
        if not (0 <= x < self.size and 0 <= y < self.size):
            return None
        s = self.sector
        lx, ly = int(x // s), int(y // s)
        leaf = self.leaves.get((lx, ly))
        if leaf is None:
            return None
        offset, samples, n = leaf
        u, v = x - lx * s, y - ly * s
        i, j = min(int(u), n - 2), min(int(v), n - 2)
        fu, fv = u - i, v - j
        row, nxt = i * n, (i + 1) * n
        raw = (samples[row + j] * (1 - fu) * (1 - fv)
               + samples[nxt + j] * fu * (1 - fv)
               + samples[row + j + 1] * (1 - fu) * fv
               + samples[nxt + j + 1] * fu * fv)
        return offset + raw / self.units_per_metre


def sight_line(observer_feet, target_feet, *, eye_height, target_height):
    return ((observer_feet[0], observer_feet[1], observer_feet[2] + eye_height),
            (target_feet[0], target_feet[1], target_feet[2] + target_height))


def terrain_clear(terrain, start, end, *, step, epsilon):
    if step <= 0:
        raise ValueError('step must be positive')
    count = math.ceil(math.dist(start, end) / step)
    for index in range(1, count):
        f = index / count
        ground = terrain.height(start[0] + (end[0] - start[0]) * f,
                                start[1] + (end[1] - start[1]) * f)
        if ground is not None and ground > start[2] + (end[2] - start[2]) * f + epsilon:
            return False
    return True
