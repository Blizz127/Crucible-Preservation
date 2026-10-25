"""Standalone scheduling and batching helpers."""

import logging


def encode_varint(value):
    if value < 0:
        raise ValueError('varint is unsigned; zigzag first')
    out = bytearray()
    while True:
        b = value & 127
        value >>= 7
        if value:
            out.append(b | 128)
        else:
            out.append(b)
            return bytes(out)


def guarded(state, where, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception:
        counts = state.setdefault('simulation_errors', {})
        count = counts[where] = counts.get(where, 0) + 1
        if (count & (count - 1)) == 0:
            logging.getLogger(__name__).exception('Simulation error in %s (count %d)', where, count)
        return None


def group_unique(items, key):
    """Distribute items so each group has distinct keys."""
    groups = []
    for item in items:
        identity = key(item)
        for group, seen in groups:
            if identity not in seen:
                break
        else:
            group, seen = [], set()
            groups.append((group, seen))
        group.append(item)
        seen.add(identity)
    return [group for group, _ in groups]
