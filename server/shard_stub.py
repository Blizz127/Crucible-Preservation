"""Standalone scheduling and batching helpers."""

import logging
import os
import time


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


def play_seconds(state, now):
    return now - state.setdefault('play_started_at', now)


def guarded(state, where, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception:
        counts = state.setdefault('simulation_errors', {})
        count = counts[where] = counts.get(where, 0) + 1
        if (count & (count - 1)) == 0:
            logging.getLogger(__name__).exception('Simulation error in %s (count %d)', where, count)
        return None


def match_max_seconds():
    try:
        value = float(os.environ.get('CRUCIBLE_SOLO_MATCH_MAX_SECONDS') or 0)
    except ValueError:
        return None
    return value if 0 < value < float('inf') else None


def session_seconds(state):
    now = time.monotonic()
    if 'clock_epoch' in state:
        now = int((now - state['clock_epoch']) * 1000) / 1000.0
    return now


def group_unique(items, key, *, preserve_order=False):
    """Distribute items so each group has distinct keys."""
    groups = []
    for item in items:
        identity = key(item)
        for group, seen in groups[-1:] if preserve_order else groups:
            if identity not in seen:
                break
        else:
            group, seen = [], set()
            groups.append((group, seen))
        group.append(item)
        seen.add(identity)
    return [group for group, _ in groups]


def respawn_step(state, alive, now, *, delay, fall_time, resend_interval, sends):
    """Advance the local timer and return the transition for the caller to dispatch."""
    stage = state.get("stage")
    if stage in (None, "landed") and not alive:
        state.update(stage="dead", died_at=now)
        return "dead"
    if stage == "dead" and now >= state["died_at"] + delay:
        state.update(stage="dropping", sent_at=now)
        return "dropping"
    if stage == "dropping" and now >= state["sent_at"] + fall_time:
        state.update(stage="landed", landed_at=now, sends=1)
        state["count"] = state.get("count", 0) + 1
        return "landed"
    if stage == "landed":
        if state["sends"] >= sends:
            state["stage"] = None
        elif now >= state["landed_at"] + state["sends"] * resend_interval:
            state["sends"] += 1
            return "landed"
    return None
