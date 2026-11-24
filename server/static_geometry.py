"""Intersection queries for caller-supplied triangles, instances and layer masks."""
import math


CELL = 8.0


LEAF = 4


class MeshTree:

    def __init__(self, vertices, triangles):
        self.triangles = [(vertices[a], vertices[b], vertices[c]) for a, b, c in triangles]

        self.nodes = []
        order = list(range(len(self.triangles)))
        if order:
            self._build(order, 0, len(order))
        self.order = order

    def _bounds(self, order, start, end):
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for index in order[start:end]:
            for point in self.triangles[index]:
                for axis in range(3):
                    if point[axis] < lo[axis]:
                        lo[axis] = point[axis]
                    if point[axis] > hi[axis]:
                        hi[axis] = point[axis]
        return lo, hi

    def _build(self, order, start, end):
        lo, hi = self._bounds(order, start, end)
        node = len(self.nodes)
        self.nodes.append([lo, hi, -1, -1, start, end - start])
        if end - start <= LEAF:
            return node
        axis = max(range(3), key=lambda a: hi[a] - lo[a])
        order[start:end] = sorted(order[start:end],
                                  key=lambda i: sum(p[axis] for p in self.triangles[i]))
        middle = (start + end) // 2
        left = self._build(order, start, middle)
        right = self._build(order, middle, end)
        self.nodes[node][2], self.nodes[node][3] = left, right
        return node

    def blocks(self, origin, direction):
        return self.first_hit(origin, direction, any_hit=True) is not None

    def first_hit(self, origin, direction, *, any_hit=False):
        if not self.nodes:
            return None
        inverse = tuple(1. / d if d else math.inf for d in direction)
        closest = None
        stack = [0]
        while stack:
            lo, hi, left, right, first, count = self.nodes[stack.pop()]
            if not _slab(origin, inverse, lo, hi):
                continue
            if left < 0:
                for index in self.order[first:first + count]:
                    hit = _triangle_fraction(origin, direction, *self.triangles[index])
                    if hit is not None:
                        if any_hit:
                            return hit
                        if closest is None or hit < closest:
                            closest = hit
            else:
                stack.append(left)
                stack.append(right)
        return closest

    def swept_triangles(self, start, end, padding):
        low = tuple(min(a, b) - r for a, b, r in zip(start, end, padding))
        high = tuple(max(a, b) + r for a, b, r in zip(start, end, padding))
        stack = [0]
        while stack:
            lo, hi, left, right, first, count = self.nodes[stack.pop()]
            if not _overlap(low, high, lo, hi):
                continue
            if left < 0:
                for index in self.order[first:first + count]:
                    yield self.triangles[index]
            else:
                stack.extend((left, right))


def _overlap(lo, hi, other_lo, other_hi):
    return (lo[0] <= other_hi[0] and other_lo[0] <= hi[0]
            and lo[1] <= other_hi[1] and other_lo[1] <= hi[1]
            and lo[2] <= other_hi[2] and other_lo[2] <= hi[2])


def _slab(origin, inverse, lo, hi):
    near, far = 0., 1.
    for axis in range(3):
        o, inv = origin[axis], inverse[axis]
        if inv == math.inf:
            if o < lo[axis] or o > hi[axis]:
                return False
            continue
        t1, t2 = (lo[axis] - o) * inv, (hi[axis] - o) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > near:
            near = t1
        if t2 < far:
            far = t2
        if near > far:
            return False
    return True


def _triangle_fraction(origin, direction, a, b, c, epsilon=1e-12):
    e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    e2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    p = (direction[1] * e2[2] - direction[2] * e2[1],
         direction[2] * e2[0] - direction[0] * e2[2],
         direction[0] * e2[1] - direction[1] * e2[0])
    det = e1[0] * p[0] + e1[1] * p[1] + e1[2] * p[2]
    if det < epsilon:
        return None
    inv = 1. / det
    s = (origin[0] - a[0], origin[1] - a[1], origin[2] - a[2])
    u = (s[0] * p[0] + s[1] * p[1] + s[2] * p[2]) * inv
    if u < 0. or u > 1.:
        return None
    q = (s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2], s[0] * e1[1] - s[1] * e1[0])
    v = (direction[0] * q[0] + direction[1] * q[1] + direction[2] * q[2]) * inv
    if v < 0. or u + v > 1.:
        return None
    t = (e2[0] * q[0] + e2[1] * q[1] + e2[2] * q[2]) * inv
    return t if 0. <= t <= 1. else None


def _sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def _dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _at(start, move, t):
    return (start[0]+move[0]*t, start[1]+move[1]*t, start[2]+move[2]*t)


def _sphere_entry(offset, move, radius):
    outside = _dot(offset, offset) - radius*radius
    if outside <= 0:
        return 0.0
    approach = _dot(offset, move)
    if approach >= 0:
        return None
    discriminant = approach*approach - _dot(move, move)*outside
    if discriminant < 0:
        return None
    t = outside / (-approach + math.sqrt(discriminant))
    return t if t <= 1 else None


def sphere_triangle_contact(start, move, radius, a, b, c):
    normal = _cross(_sub(b, a), _sub(c, a))
    normal_length = math.sqrt(_dot(normal, normal))
    if not normal_length:
        return None
    normal = tuple(n/normal_length for n in normal)
    height = _dot(_sub(start, a), normal)
    rise = _dot(move, normal)
    end_height = height + rise
    if max(height, end_height) < 0 or min(height, end_height) > radius:
        return None


    if height > radius:
        t = (radius - height) / rise
    elif height < -radius:
        t = (-radius - height) / rise
    else:
        t = 0.0
    centre = _at(start, move, t)
    edges = ((a, b), (b, c), (c, a))
    if all(_dot(_cross(_sub(q, p), _sub(centre, p)), normal) >= 0 for p, q in edges):
        return t if height + t * rise >= 0 else None
    first = None
    for p, q in edges:
        edge, offset = _sub(q, p), _sub(start, p)
        edge2 = _dot(edge, edge)
        along = _dot(offset, edge)/edge2
        speed = _dot(move, edge)/edge2

        side = _sphere_entry(_at(offset, edge, -along), _at(move, edge, -speed), radius)
        if side is not None and not 0 <= along + side*speed <= 1:
            side = None
        for contact in (side, _sphere_entry(offset, move, radius)):
            if contact is not None and (first is None or contact < first):
                first = contact

    return first if first is not None and height + first * rise >= 0 else None


def _invert(matrix):
    c0, c1, c2, t = matrix[0:3], matrix[3:6], matrix[6:9], matrix[9:12]
    m = ((c0[0], c1[0], c2[0]), (c0[1], c1[1], c2[1]), (c0[2], c1[2], c2[2]))
    det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
           - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
           + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    if abs(det) < 1e-12:
        return None
    inv = [[0.] * 3 for _ in range(3)]
    for row in range(3):
        for col in range(3):
            r = [i for i in range(3) if i != col]
            c = [i for i in range(3) if i != row]
            minor = m[r[0]][c[0]] * m[r[1]][c[1]] - m[r[0]][c[1]] * m[r[1]][c[0]]
            inv[row][col] = (-1) ** (row + col) * minor / det
    shift = tuple(-(inv[i][0] * t[0] + inv[i][1] * t[1] + inv[i][2] * t[2]) for i in range(3))
    return tuple(inv[i][j] for j in range(3) for i in range(3)) + shift


def _apply(matrix, point):
    x, y, z = point
    return tuple(x * matrix[i] + y * matrix[3 + i] + z * matrix[6 + i] + matrix[9 + i]
                 for i in range(3))


class World:

    def __init__(self, level, meshes, instances):
        self.level = level
        self.trees = [MeshTree(v, t) for v, t in meshes]
        self.instances = []
        self.sweep_transforms = []
        self.grid = {}
        for mesh, layer, matrix in instances:
            inverse = _invert(matrix)
            tree = self.trees[mesh]
            if inverse is None or not tree.nodes:
                continue
            lo, hi = tree.nodes[0][0], tree.nodes[0][1]
            corners = [_apply(matrix, (x, y, z)) for x in (lo[0], hi[0])
                       for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
            box = (tuple(min(c[i] for c in corners) for i in range(3)),
                   tuple(max(c[i] for c in corners) for i in range(3)))
            number = len(self.instances)
            self.instances.append((tree, layer, inverse, box))


            row_norms = tuple(math.hypot(inverse[i], inverse[i+3], inverse[i+6]) for i in range(3))
            mirrored = _dot(_cross(matrix[:3], matrix[3:6]), matrix[6:9]) < 0
            self.sweep_transforms.append((matrix, row_norms, mirrored))
            for gx in range(int(box[0][0] // CELL), int(box[1][0] // CELL) + 1):
                for gy in range(int(box[0][1] // CELL), int(box[1][1] // CELL) + 1):
                    self.grid.setdefault((gx, gy), []).append(number)

    def _candidates(self, start, end):
        seen = set()
        x0, y0, x1, y1 = start[0] / CELL, start[1] / CELL, end[0] / CELL, end[1] / CELL
        gx, gy = int(math.floor(x0)), int(math.floor(y0))
        last = (int(math.floor(x1)), int(math.floor(y1)))
        dx, dy = x1 - x0, y1 - y0
        step_x, step_y = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        t_dx = abs(1. / dx) if dx else math.inf
        t_dy = abs(1. / dy) if dy else math.inf
        t_x = ((gx + (dx > 0) - x0) / dx) if dx else math.inf
        t_y = ((gy + (dy > 0) - y0) / dy) if dy else math.inf
        for _ in range(4096):
            for number in self.grid.get((gx, gy), ()):
                if number not in seen:
                    seen.add(number)
                    yield number
            if (gx, gy) == last or min(t_x, t_y) > 1.:
                return
            if t_x < t_y:
                t_x += t_dx
                gx += step_x
            else:
                t_y += t_dy
                gy += step_y

    def blocked(self, start, end, mask):
        return self.first_hit(start, end, mask, any_hit=True) is not None

    def first_hit(self, start, end, mask, *, any_hit=False):
        closest = None
        direction = tuple(end[i] - start[i] for i in range(3))
        inverse_direction = tuple(1. / d if d else math.inf for d in direction)
        for number in self._candidates(start, end):
            tree, layer, inverse, (lo, hi) = self.instances[number]
            if not (mask >> layer) & 1 or not _slab(start, inverse_direction, lo, hi):
                continue
            origin = _apply(inverse, start)
            local_end = _apply(inverse, end)
            local_direction = tuple(local_end[i] - origin[i] for i in range(3))
            hit = tree.first_hit(origin, local_direction, any_hit=any_hit)
            if hit is not None:
                if any_hit:
                    return hit
                if closest is None or hit < closest:
                    closest = hit
        return closest

    def first_sphere_hit(self, start, end, radius, mask):
        if not math.isfinite(radius) or radius < 0:
            raise ValueError('invalid projectile radius')
        if radius == 0:
            return self.first_hit(start, end, mask)
        move = _sub(end, start)
        low = tuple(min(a, b) - radius for a, b in zip(start, end))
        high = tuple(max(a, b) + radius for a, b in zip(start, end))


        candidates = set()
        for gx in range(math.floor(low[0]/CELL), math.floor(high[0]/CELL) + 1):
            for gy in range(math.floor(low[1]/CELL), math.floor(high[1]/CELL) + 1):
                candidates.update(self.grid.get((gx, gy), ()))
        closest = None
        for number in candidates:
            tree, layer, inverse, (lo, hi) = self.instances[number]
            if not (mask >> layer) & 1 or not _overlap(low, high, lo, hi):
                continue
            matrix, row_norms, mirrored = self.sweep_transforms[number]
            padding = tuple(radius * norm for norm in row_norms)
            for a, b, c in tree.swept_triangles(_apply(inverse, start), _apply(inverse, end), padding):


                if mirrored:
                    b, c = c, b
                hit = sphere_triangle_contact(start, move, radius, _apply(matrix, a),
                                              _apply(matrix, b), _apply(matrix, c))
                if hit == 0:
                    return 0.0
                if hit is not None and (closest is None or hit < closest):
                    closest = hit
        return closest
