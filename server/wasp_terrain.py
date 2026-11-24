"""Sphere contact on caller-provided geometry."""

import math


class Terrain:
    """Sweep spheres against triangles supplied by a regular horizontal grid."""

    def __init__(self, rows, columns, row_scale, column_scale, triangles):
        self.rows, self.columns = rows, columns
        self.row_scale, self.column_scale = row_scale, column_scale
        self._triangles = triangles

    def first_contact(self, start, end, radius):
        """Earliest fraction along the segment at which the sphere hits solid ground from above."""
        if radius < 0 or not math.isfinite(radius):
            raise ValueError('invalid terrain contact radius')
        move = _sub(end, start)
        x0 = max(0, math.ceil((min(start[0], end[0])-radius)/self.column_scale)-1)
        x1 = min(self.columns-2, math.floor((max(start[0], end[0])+radius)/self.column_scale))
        y0 = max(0, math.ceil((min(start[1], end[1])-radius)/self.row_scale)-1)
        y1 = min(self.rows-2, math.floor((max(start[1], end[1])+radius)/self.row_scale))
        low, high = min(start[2], end[2])-radius, max(start[2], end[2])+radius
        first = None
        for row in range(y0, y1+1):
            for col in range(x0, x1+1):
                for a, b, c in self._triangles(row, col):
                    if low > max(a[2], b[2], c[2]) or high < min(a[2], b[2], c[2]):
                        continue
                    contact = _triangle_contact(start, move, radius, a, b, c)
                    if contact == 0:
                        return 0.0
                    if contact is not None and (first is None or contact < first):
                        first = contact
        return first


def _sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def _dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _at(start, move, t):
    return (start[0]+move[0]*t, start[1]+move[1]*t, start[2]+move[2]*t)


def _sphere_entry(offset, move, radius):
    """First root of |offset + t*move| = radius in this segment."""
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


def _triangle_contact(start, move, radius, a, b, c):
    """Sweep against the upper face, the three bounded edges and their vertices."""
    normal = _cross(_sub(b, a), _sub(c, a))
    normal_length = math.sqrt(_dot(normal, normal))
    normal = tuple(n/normal_length for n in normal)
    height = _dot(_sub(start, a), normal)
    rise = _dot(move, normal)
    end_height = height + rise
    if max(height, end_height) < 0 or min(height, end_height) > radius:
        return None
    # Enter the slab about the infinite plane first. A projectile may cross
    # the plane outside the finite triangle, then graze an edge from above.
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
        # Against the edge cylinder use motion perpendicular to its axis.
        side = _sphere_entry(_at(offset, edge, -along), _at(move, edge, -speed), radius)
        if side is not None and not 0 <= along + side*speed <= 1:
            side = None
        for contact in (side, _sphere_entry(offset, move, radius)):
            if contact is not None and (first is None or contact < first):
                first = contact
    # An underside entry is a cave ceiling or a flight exiting solid terrain.
    return first if first is not None and height + first * rise >= 0 else None
