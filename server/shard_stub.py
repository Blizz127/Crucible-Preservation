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
