"""Read-only MNM v7 navigation: triangle corridors and funnelled paths.

Format authority: aws/lumberyard, CryAISystem/Navigation, specifically
NavigationSystem::ReadFromFile, MNM/Tile.h and MNM/MeshGrid.cpp.
Off-mesh jumps are deliberately excluded until their traversal is implemented.

Links no longer carry a single edge midpoint. A tile-border link may join two
triangles whose edges only partially overlap, so the midpoint of the source
edge can sit outside the destination triangle. Every link now stores the
clipped overlap segment, and paths are string-pulled through those segments
with the simple funnel algorithm instead of hopping midpoint to midpoint.
"""
import functools
import heapq
import math
from pathlib import Path
import struct
import sys

LEVELS = (Path.home() / '.local/share/Steam/steamapps/common/Crucible'
          / 'NvGameSdk/levels')
LEVEL_PAK = LEVELS / 'april2018/level.pak'
NAVIGATION_ENTRY = b'mnmnavmission0.bai'

NEIGHBOURS = ((1,0,0),(1,0,1),(1,0,-1),(0,1,0),(0,1,1),(0,1,-1),
              (0,0,1),(-1,0,0),(-1,0,-1),(-1,0,1),(0,-1,0),
              (0,-1,-1),(0,-1,1),(0,0,-1))

# Tile vertices are quantised to 1/2048 m, so collinear border edges agree far
# inside this bound; it only has to absorb quantisation, not geometry.
COLLINEAR_TOLERANCE = 0.02
# Two triangles sharing a plan edge but this far apart in height are stacked
# floors, not neighbours.
VERTICAL_TOLERANCE = 0.5
# Portals shorter than this are corner touches, not walkable openings.
MINIMUM_PORTAL = 1e-3
INDEX_CELL = 8.0


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError('truncated MNM navigation')
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def unpack(self, fmt):
        values = struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))
        return values[0] if len(values) == 1 else values

    def string(self):
        size = self.unpack('I')
        if size > 1024:
            raise ValueError('invalid MNM name length')
        return self.take(size).decode('ascii')


def turn(a, b, c):
    """Twice the signed plan-view area of a->b->c; positive when c is left."""
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def height(triangle, x, y):
    """Interpolated surface height at (x, y), or None when outside in plan."""
    a, b, c = triangle
    denominator = (b[1]-c[1])*(a[0]-c[0]) + (c[0]-b[0])*(a[1]-c[1])
    if abs(denominator) < 1e-10:
        return None
    u = ((b[1]-c[1])*(x-c[0]) + (c[0]-b[0])*(y-c[1])) / denominator
    v = ((c[1]-a[1])*(x-c[0]) + (a[0]-c[0])*(y-c[1])) / denominator
    if min(u, v, 1-u-v) < -1e-6:
        return None
    return u*a[2] + v*b[2] + (1-u-v)*c[2]


def shared_edge(source, edge, target, tolerance=COLLINEAR_TOLERANCE,
                vertical_tolerance=VERTICAL_TOLERANCE):
    """Overlap of one source edge with whichever target edge is collinear.

    Returns the clipped segment as two points on the source edge, or None when
    the triangles only touch. Tile-border links routinely pair an edge with a
    longer or shorter neighbour edge, which is why the overlap must be clipped
    rather than assumed identical.

    Collinearity is judged in plan view. Adjacent MNM triangles share a plan
    edge but may disagree slightly in height along it, and sides 6 and 13 are
    vertical tile neighbours whose triangles share plan edges while sitting on
    different floors, so a separate height gate keeps stacked surfaces apart.
    """
    p, q = source[edge], source[(edge + 1) % 3]
    span = math.dist(p[:2], q[:2])
    if span <= MINIMUM_PORTAL:
        return None
    unit = ((q[0]-p[0]) / span, (q[1]-p[1]) / span)

    def at(a, b, distance):
        """Height on segment a->b at `distance` along the source edge."""
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
            if high - low <= MINIMUM_PORTAL:
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
    """Simple funnel string-pull across oriented (left, right) portal pairs."""
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
        """Nearest triangle under/over `point`, with the point pulled to it."""
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
        """Connected triangle sets over walkable links, largest first."""
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
        """{triangle key: its component's index in components()}, cached.

        Two points in different components have no path between them, and a
        search that proves it explores the whole component first.
        """
        if self._component_index is None:
            self._component_index = {key: number
                                     for number, group in enumerate(self.components())
                                     for key in group}
        return self._component_index

    def corridor(self, first, last, limit=200000):
        """A* triangle corridor as [(key, entry portal or None), ...]."""
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
        """Conservative corridor route through portal midpoints."""
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
        """Orient each corridor portal into (left, right) for the funnel."""
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
        """String-pulled path from start to end, resampled onto the surface."""
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

    def wander_point(self, origin, low, high, rng, attempts=5,
                     anchor=None, anchor_limit=None, vertical_tolerance=2.0):
        """A reachable mesh point `low`..`high` metres from `origin`.

        Mirrors the authored PatrolParams search: try a bounded number of
        random offsets and give up rather than force a destination. Returns
        (point, path) or None. `rng` is injected so patrols are reproducible.
        """
        if not 0 <= low <= high or attempts < 1:
            raise ValueError('invalid wander bounds')
        for _ in range(attempts):
            angle = rng.uniform(-math.pi, math.pi)
            radius = low + (high - low) * rng.random()
            candidate = (origin[0] + math.cos(angle)*radius,
                         origin[1] + math.sin(angle)*radius, origin[2])
            found = self.locate(candidate, vertical_tolerance)
            if found is None:
                continue
            point = found[1]
            if anchor is not None and anchor_limit is not None \
                    and math.dist(point, anchor) > anchor_limit:
                continue
            path = self.path(origin, point)
            if path is not None and len(path) >= 2:
                return point, path
        return None


def read_mnm(data, agent_name='MediumSizedCharacters'):
    r = Reader(data)
    if r.unpack('H') != 7:
        raise ValueError('unsupported MNM version')
    r.unpack('I')  # Navigation.xml configuration version
    for _ in range(r.unpack('I')):
        r.string()
        r.unpack('I')
    triangles, raw_links = {}, {}
    for _ in range(r.unpack('I')):
        agent = r.string()
        size = r.unpack('I')
        agent_end = r.pos + size
        for _ in range(r.unpack('I')):
            mesh_id = r.unpack('I')
            r.string()
            r.unpack('I')  # island count
            size = r.unpack('I')
            mesh_end = r.pos + size
            r.unpack('If')  # boundary id/height
            r.take(r.unpack('I') * 12)
            r.take(r.unpack('I') * 4)  # exclusion volume ids
            count = r.unpack('I')
            params = r.unpack('3f3i3fI')
            origin, tile_size = params[:3], params[3:6]
            if not all(math.isfinite(v) for v in origin) or min(tile_size) <= 0:
                raise ValueError('invalid MNM grid')
            for _ in range(count):
                xyz = r.unpack('3H')
                r.unpack('I')  # tile hash
                ts = [r.unpack('4HI') for _ in range(r.unpack('H'))]
                vs = [r.unpack('3H') for _ in range(r.unpack('H'))]
                ls = [r.unpack('H') for _ in range(r.unpack('H'))]
                r.take(r.unpack('H') * 14)  # optional BV nodes
                if agent != agent_name:
                    continue
                vertices = [tuple(origin[i] + xyz[i]*tile_size[i] + v[i]/2048.
                                  for i in range(3)) for v in vs]
                for index, t in enumerate(ts):
                    if max(t[:3]) >= len(vs):
                        raise ValueError('invalid MNM vertex index')
                    key = (mesh_id, *xyz, index)
                    if key in triangles:
                        raise ValueError('duplicate MNM triangle')
                    triangles[key] = tuple(vertices[i] for i in t[:3])
                    first, length = t[3] >> 4, t[3] & 15
                    if first + length > len(ls):
                        raise ValueError('invalid MNM link range')
                    raw_links[key] = ls[first:first+length]
            if r.pos != mesh_end:
                raise ValueError('MNM mesh size mismatch')
        if r.pos != agent_end:
            raise ValueError('MNM agent size mismatch')
    if r.pos != len(data):
        raise ValueError('trailing MNM bytes')
    links, counts = {}, dict.fromkeys(
        ('raw', 'off_mesh', 'internal', 'border', 'clipped', 'dropped'), 0)
    for key, values in raw_links.items():
        for value in values:
            counts['raw'] += 1
            side, edge, index = value & 15, (value >> 4) & 3, value >> 6
            if side == 14:  # off-mesh traversal requires separate support
                counts['off_mesh'] += 1
                continue
            if edge > 2:
                raise ValueError('invalid MNM edge')
            counts['internal' if side == 15 else 'border'] += 1
            delta = (0, 0, 0) if side == 15 else NEIGHBOURS[side]
            target = (key[0], *(key[i+1] + delta[i] for i in range(3)), index)
            if target not in triangles:
                raise ValueError('missing MNM linked triangle')
            source = triangles[key]
            portal = shared_edge(source, edge, triangles[target])
            if portal is None:  # corner touch, not a walkable opening
                counts['dropped'] += 1
                continue
            if math.dist(*portal) < math.dist(source[edge], source[(edge+1) % 3]) - 1e-6:
                counts['clipped'] += 1
            links.setdefault(key, []).append((target, portal))
    mesh = Mesh(triangles, links)
    mesh.counts = counts
    return mesh


def level_pak(level=None):
    """A level's installed pak: April2018 by default, a level directory name
    such as 'HotH_02' or 'tutorial_02', or an explicit pak path."""
    if level is None:
        return LEVEL_PAK
    text = str(level)
    if text.endswith('.pak') or '/' in text:
        return Path(text)
    return LEVELS / text / 'level.pak'


def installed_mesh(level=None):
    """Parse the shipped level's navigation mesh, once per level pak.

    Nothing installed is written. Each level pak ships its own
    mnmnavmission0.bai (HotH_02's is a byte copy of April2018's), and the
    bots walk the selected map's (docs/37).
    """
    return _installed_mesh(str(level_pak(level)))


@functools.lru_cache(maxsize=4)
def _installed_mesh(path):
    # The read-only PAK reader copes with CryEngine's local-header separators.
    tools = str(Path(__file__).resolve().parents[1] / 'tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import pak
    entries = pak.load(Path(path))
    data = next(pak.entry_bytes(e) for e in entries if e['name'] == NAVIGATION_ENTRY)
    return read_mnm(data)
