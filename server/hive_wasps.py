import math


class IdPool:

    def __init__(self, first, last, gap):
        self.ids = tuple(range(first, last + 1))
        self.gap = gap
        self.live = {}
        self.free_at = {}
        self.cursor = 0
        self.exhausted = 0

    def take(self, now):
        for step in range(len(self.ids)):
            entity = self.ids[(self.cursor + step) % len(self.ids)]
            if entity not in self.live and now >= self.free_at.get(entity, -math.inf):
                self.cursor = (self.cursor + step + 1) % len(self.ids)
                self.live[entity] = now
                return entity
        self.exhausted += 1
        return None

    def release(self, entity, now):
        self.live.pop(entity, None)
        self.free_at[entity] = now + self.gap


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _unit(a):
    length = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    return (0.0, 0.0, 1.0) if length < 1e-9 else (a[0] / length, a[1] / length, a[2] / length)


def _turn(current, wanted, limit):

    dot = max(-1.0, min(1.0, sum(c * w for c, w in zip(current, wanted))))
    angle = math.acos(dot)
    if angle <= limit or angle < 1e-9:
        return wanted
    side = _sub(wanted, _scale(current, dot))
    length = math.sqrt(sum(s * s for s in side))
    if length < 1e-9:
        return current
    side = _scale(side, 1.0 / length)
    return _unit(_add(_scale(current, math.cos(limit)), _scale(side, math.sin(limit))))


def _segment_distance(a, b, point):

    ab = _sub(b, a)
    length2 = sum(c * c for c in ab)
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, sum(
        x * y for x, y in zip(_sub(point, a), ab)) / length2))
    return math.dist(_add(a, _scale(ab, t)), point)
