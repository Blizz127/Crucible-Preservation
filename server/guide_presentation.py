from collections import namedtuple


Beat = namedtuple('Beat', 'offset kind name')
Step = namedtuple('Step', 'key beats poll_gap')


def director(state, arena, now, *, steps, triggered, beat_message):
    """Return messages for due beats from a caller-supplied sequence."""
    memory = state.setdefault('guide_voice', {'reached': [], 'queue': [], 'tail': None,
                                              'count': 0, 'sent': 0})
    for step in steps:
        if step.key in memory['reached']:
            continue
        if not triggered(step.key, state, arena):
            break
        memory['reached'].append(step.key)
        base = now
        if memory['tail'] is not None:
            base = max(now, memory['tail'] + step.poll_gap - step.beats[0].offset)
        for beat in step.beats:
            memory['count'] += 1
            memory['queue'].append((base + beat.offset, memory['count'], step.key, beat))
        memory['tail'] = base + step.beats[-1].offset
    due_now = sorted(item for item in memory['queue'] if item[0] <= now + 1e-9)
    memory['queue'] = [item for item in memory['queue'] if item[0] > now + 1e-9]
    memory['sent'] += len(due_now)
    return [beat_message(beat) for _, _, _, beat in due_now]
