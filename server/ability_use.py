"""Track and describe ability attribution using caller-supplied records."""

import dataclasses

import hero_abilities


@dataclasses.dataclass
class Edge:
    action: str
    time: float
    game_time_ms: object
    camera: object
    forward: object
    target: object
    abilities: tuple
    released_at: object = None


@dataclasses.dataclass
class Attribution:
    status: str
    ability: object = None
    edge: object = None
    detail: str = ''
    weapon: object = None
    request_index: object = None


def describe(abilities):
    if not abilities:
        return 'no ability'
    if len(abilities) == 1:
        return '#%d %s' % (abilities[0].index, abilities[0].name)
    return '%d abilities (%s)' % (len(abilities), ', '.join(
        '#%d %s' % (a.index, a.name) for a in abilities))


def projectile_rows(hero, ability):
    data = hero_abilities.ROSTER[hero]
    rows = []
    for entry in ability.creates + ability.text_only:
        kind, _, name = entry.partition(':')
        if kind == 'projectile':
            row = next((p for p in data.projectiles if p.asset == name), None)
            if row is not None and row not in rows:
                rows.append(row)
    return rows


def lifetime(hero, ability):
    rows = projectile_rows(hero, ability)
    return max(row.lifetime_s for row in rows) if rows else None


def numbers(weapon):
    return dataclasses.replace(weapon, name='')


def place(point):
    return 'none' if point is None else '(%s)' % ', '.join('%.1f' % v for v in point)
