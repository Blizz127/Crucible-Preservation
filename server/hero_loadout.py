"""Select a character from a caller-populated registry."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Hero:
    name: str
    display: str
    roster_index: int


ROSTER = {}
DEFAULT_HERO = None
ENV = 'CRUCIBLE_SOLO_HERO'


def names():
    return tuple(sorted(ROSTER, key=lambda n: ROSTER[n].roster_index))


def get(name):
    key = (name or '').strip().lower()
    if key not in ROSTER:
        raise ValueError('unknown hero %r; choose one of %s'
                         % (name, ', '.join(names())))
    return ROSTER[key]


def selected():
    return get(os.environ.get(ENV) or DEFAULT_HERO)
