"""Sphere contact on caller-provided geometry."""

import math


class Terrain:
    """Sweep spheres against triangles supplied by a regular horizontal grid."""

    def __init__(self, rows, columns, row_scale, column_scale, triangles, contact=None):
        self.rows, self.columns = rows, columns
        self.row_scale, self.column_scale = row_scale, column_scale
        self._triangles = triangles
        if contact is None:
            from static_geometry import sphere_triangle_contact
            contact = sphere_triangle_contact
        self._contact = contact

    def first_contact(self, start, end, radius):
        """Earliest fraction along the segment at which the sphere hits solid ground from above."""
        if radius < 0 or not math.isfinite(radius):
            raise ValueError('invalid terrain contact radius')
        move = tuple(b - a for a, b in zip(start, end))
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
                    contact = self._contact(start, move, radius, a, b, c)
                    if contact == 0:
                        return 0.0
                    if contact is not None and (first is None or contact < first):
                        first = contact
        return first
