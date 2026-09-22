# Routing across a triangle mesh

The project uses A* to find a corridor of adjacent triangles. Two neighboring tiles can split their common boundary at different points. To join them, use the overlapping part of the boundary as a gate. Requiring both endpoints to match can leave a gap in the graph even where the surface is continuous.

Order each gate's endpoints relative to the direction of travel, then use the funnel algorithm to shorten the path through the corridor. Sample the resulting segments and project the samples onto the corridor surface. Without that last step, a line between two waypoints can pass underneath a convex slope.

A small test mesh can exercise these cases directly. Split one side of a tile boundary into two edges and check that the route crosses the seam. Add a ridge and check that the sampled heights follow the surface.
