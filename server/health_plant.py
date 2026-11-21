"""Scheduling, overlap, and healing calculations with caller-supplied inputs."""

import math


def spawn_times(schedule, *, first_spawn=None):
    times = tuple(item.when + item.telegraph for item in schedule)
    if first_spawn is None or not times:
        return times
    offset = first_spawn - times[0]
    return tuple(time + offset for time in times)


def in_field(player, field, *, radius, height, player_radius, player_height):
    if math.dist(player[:2], field[:2]) > radius + player_radius:
        return False
    return (player[2] < field[2] + height / 2
            and player[2] + player_height > field[2] - height / 2)


def heal_owed(heals, now, *, rate):
    total = 0.0
    for running in heals:
        left, paid_to = running
        if now <= paid_to:
            continue
        amount = min(left, rate * (now - paid_to))
        running[0], running[1] = left - amount, now
        total += amount
    heals[:] = [running for running in heals if running[0] > 1e-9]
    return total


def _field_id(plants, now, *, first_id, count, rest_seconds):
    for offset in range(count):
        candidate = first_id + offset
        if candidate in plants['fields']:
            continue
        if now - plants['rested'].get(candidate, -rest_seconds) >= rest_seconds:
            return candidate
    return None
