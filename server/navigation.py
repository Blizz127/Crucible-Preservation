"""Triangle mesh routing with caller-supplied triangles and links."""
import heapq
import math


INDEX_CELL = 8.0


def turn(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def height(triangle, x, y):
    a, b, c = triangle
    denominator = (b[1]-c[1])*(a[0]-c[0]) + (c[0]-b[0])*(a[1]-c[1])
    if abs(denominator) < 1e-10:
        return None
    u = ((b[1]-c[1])*(x-c[0]) + (c[0]-b[0])*(y-c[1])) / denominator
    v = ((c[1]-a[1])*(x-c[0]) + (a[0]-c[0])*(y-c[1])) / denominator
    if min(u, v, 1-u-v) < -1e-6:
        return None
    return u*a[2] + v*b[2] + (1-u-v)*c[2]


def shared_edge(source, edge, target, tolerance, vertical_tolerance, minimum_portal):
    p, q = source[edge], source[(edge + 1) % 3]
    span = math.dist(p[:2], q[:2])
    if span <= minimum_portal:
        return None
    unit = ((q[0]-p[0]) / span, (q[1]-p[1]) / span)

    def at(a, b, distance):
        length = math.dist(a[:2], b[:2])
        if length <= 0:
            return a[2]
        offset = (distance*unit[0] + p[0] - a[0], distance*unit[1] + p[1] - a[1])
        fraction = (offset[0]*(b[0]-a[0]) + offset[1]*(b[1]-a[1])) / length**2
        return a[2] + (b[2]-a[2]) * min(1., max(0., fraction))

    best = None
    for i in range(3):
        a, b = target[i], target[(i + 1) % 3]
        bounds = []
        for point in (a, b):
            offset = (point[0]-p[0], point[1]-p[1])
            along = offset[0]*unit[0] + offset[1]*unit[1]
            away = abs(offset[0]*unit[1] - offset[1]*unit[0])
            if away > tolerance:
                break
            bounds.append(along)
        else:
            low, high = max(0., min(bounds)), min(span, max(bounds))
            if high - low <= minimum_portal:
                continue
            middle = (low + high) / 2
            drop = abs(at(p, q, middle) - at(a, b, middle))
            if drop > vertical_tolerance:
                continue
            if best is None or (high-low, -drop) > (best[1]-best[0], -best[2]):
                best = (low, high, drop)
    if best is None:
        return None
    return tuple(tuple((p[0] + unit[0]*side, p[1] + unit[1]*side, at(p, q, side)))
                 for side in best[:2])


def funnel(start, gates, end):
    points, apex = [start], start
    left = right = start
    apex_at = left_at = right_at = 0
    gates = [*gates, (end, end)]
    index, guard = 0, 0
    while index < len(gates):
        guard += 1
        if guard > 8 * len(gates) + 16:
            raise ValueError('funnel failed to converge')
        gate_left, gate_right = gates[index]
        if right == apex or turn(apex, right, gate_right) >= 0:
            if left == apex or turn(apex, left, gate_right) <= 0:
                right, right_at = gate_right, index
            else:
                if points[-1] != left:
                    points.append(left)
                apex, apex_at = left, left_at
                left = right = apex
                left_at = right_at = apex_at
                index = apex_at + 1
                continue
        if left == apex or turn(apex, left, gate_left) <= 0:
            if right == apex or turn(apex, right, gate_left) >= 0:
                left, left_at = gate_left, index
            else:
                if points[-1] != right:
                    points.append(right)
                apex, apex_at = right, right_at
                left = right = apex
                left_at = right_at = apex_at
                index = apex_at + 1
                continue
        index += 1
    if points[-1] != end:
        points.append(end)
    return points


class Mesh:
    def __init__(self, triangles, links):
        self.triangles, self.links = triangles, links
        self.centres = {k: tuple(sum(p[i] for p in t) / 3 for i in range(3))
                        for k, t in triangles.items()}
        self.index = {}
        for key, triangle in triangles.items():
            for cell in self._cells(triangle):
                self.index.setdefault(cell, []).append(key)
        self._components = None
        self._component_index = None

    @staticmethod
    def _cells(triangle):
        low = [min(p[i] for p in triangle) for i in range(2)]
        high = [max(p[i] for p in triangle) for i in range(2)]
        for x in range(math.floor(low[0]/INDEX_CELL), math.floor(high[0]/INDEX_CELL)+1):
            for y in range(math.floor(low[1]/INDEX_CELL), math.floor(high[1]/INDEX_CELL)+1):
                yield x, y

    def locate(self, point, vertical_tolerance=2.0):
        x, y, z = point
        cell = (math.floor(x/INDEX_CELL), math.floor(y/INDEX_CELL))
        best = None
        for key in self.index.get(cell, ()):
            surface = height(self.triangles[key], x, y)
            if surface is None:
                continue
            distance = abs(z - surface)
            if distance <= vertical_tolerance and (best is None or distance < best[0]):
                best = (distance, key, (x, y, surface))
        return None if best is None else best[1:]

    def neighbours(self, key):
        return self.links.get(key, ())

    def components(self):
        if self._components is not None:
            return self._components
        undirected = {}
        for key, edges in self.links.items():
            for target, _ in edges:
                undirected.setdefault(key, set()).add(target)
                undirected.setdefault(target, set()).add(key)
        seen, groups = set(), []
        for key in self.triangles:
            if key in seen:
                continue
            group, stack = set(), [key]
            seen.add(key)
            while stack:
                current = stack.pop()
                group.add(current)
                for target in undirected.get(current, ()):
                    if target not in seen:
                        seen.add(target)
                        stack.append(target)
            groups.append(group)
        groups.sort(key=len, reverse=True)
        self._components = groups
        return groups

    def component_index(self):
        if self._component_index is None:
            self._component_index = {key: number
                                     for number, group in enumerate(self.components())
                                     for key in group}
        return self._component_index

    def corridor(self, first, last, limit=200000):
        if first == last:
            return [(first, None)]
        goal = self.centres[last]
        queue = [(0., first)]
        costs, parents, expanded = {first: 0.}, {}, 0
        while queue:
            _, current = heapq.heappop(queue)
            if current == last:
                chain = []
                while current != first:
                    previous, portal = parents[current]
                    chain.append((current, portal))
                    current = previous
                chain.append((first, None))
                return list(reversed(chain))
            expanded += 1
            if expanded > limit:
                return None
            for target, portal in self.links.get(current, ()):
                cost = costs[current] + math.dist(self.centres[current], self.centres[target])
                if cost >= costs.get(target, float('inf')):
                    continue
                costs[target], parents[target] = cost, (current, portal)
                heapq.heappush(queue, (cost + math.dist(self.centres[target], goal), target))
        return None

    def route(self, start, end):
        source, destination = self.locate(start), self.locate(end)
        if source is None or destination is None:
            return None
        chain = self.corridor(source[0], destination[0])
        if chain is None:
            return None
        midpoints = [tuple((a[i]+b[i])/2 for i in range(3))
                     for _, portal in chain if portal for a, b in (portal,)]
        return [source[1], *midpoints, destination[1]]

    def gates(self, chain):
        result = []
        for index in range(1, len(chain)):
            portal = chain[index][1]
            if portal is None:
                continue
            a, b = portal
            source, target = self.centres[chain[index-1][0]], self.centres[chain[index][0]]
            forward = (target[0]-source[0], target[1]-source[1])
            middle = tuple((a[i]+b[i])/2 for i in range(3))
            offset = (a[0]-middle[0], a[1]-middle[1])
            side = forward[0]*offset[1] - forward[1]*offset[0]
            result.append((a, b) if side > 0 else (b, a))
        return result

    def path(self, start, end, step=1.0):
        source, destination = self.locate(start), self.locate(end)
        if source is None or destination is None:
            return None
        chain = self.corridor(source[0], destination[0])
        if chain is None:
            return None
        points = funnel(source[1], self.gates(chain), destination[1])
        if not step or step <= 0:
            return points
        keys = [key for key, _ in chain]
        result, cursor = [points[0]], 0
        for a, b in zip(points, points[1:]):
            distance = math.dist(a[:2], b[:2])
            for part in range(1, int(distance / step) + 1):
                fraction = part * step / distance
                x, y = a[0] + (b[0]-a[0])*fraction, a[1] + (b[1]-a[1])*fraction
                surface, cursor = self._surface(keys, cursor, x, y)
                result.append((x, y, a[2] + (b[2]-a[2])*fraction
                               if surface is None else surface))
            result.append(b)
        return result

    def _surface(self, keys, cursor, x, y):
        for offset in range(len(keys)):
            index = (cursor + offset) % len(keys)
            surface = height(self.triangles[keys[index]], x, y)
            if surface is not None:
                return surface, index
        return None, cursor

    def on_mesh(self, point, vertical_tolerance=0.05):
        return self.locate(point, vertical_tolerance) is not None
