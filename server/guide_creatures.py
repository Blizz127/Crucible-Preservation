from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Camp:
    name: str
    nodes: tuple
    entity_ids: tuple

    @property
    def centre(self):
        count = len(self.nodes)
        return tuple(sum(node[1][axis] for node in self.nodes) / count
                     for axis in range(3))


def camps_for(level, nodes, keep_clear=(), *, radius, first_entity_id, last_entity_id):
    """Group supplied nodes by single linkage and allocate each group contiguous entity IDs."""
    nodes = tuple(node for node in nodes
                  if all(math.dist(node[1], spot) > radius for spot in keep_clear))
    groups, seen = [], set()
    for start in range(len(nodes)):
        if start in seen:
            continue
        seen.add(start)
        group, queue = [start], [start]
        while queue:
            current = queue.pop()
            for other in range(len(nodes)):
                if other not in seen and math.dist(nodes[current][1],
                                                   nodes[other][1]) <= radius:
                    seen.add(other)
                    group.append(other)
                    queue.append(other)
        groups.append(sorted(group))
    camps, next_id = [], first_entity_id
    for number, group in enumerate(groups, 1):
        ids = tuple(range(next_id, next_id + len(group)))
        next_id += len(group)
        camps.append(Camp('%s-camp-%d' % (level.lower(), number),
                          tuple(nodes[index] for index in group), ids))
    if next_id - 1 > last_entity_id:
        raise ValueError('guide creatures overflow their entity id block')
    return tuple(camps)
