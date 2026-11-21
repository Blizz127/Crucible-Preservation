"""Select a character from a caller-populated registry."""

import json
import os
import tempfile
from dataclasses import dataclass


@dataclass(frozen=True)
class Hero:
    name: str
    display: str
    roster_index: int


ROSTER = {}
DEFAULT_HERO = None
ENV = 'CRUCIBLE_SOLO_HERO'
MENU = 'menu'
DECLARED_ENV = 'CRUCIBLE_ACTIVE_HERO_FILE'
_declared = {}
_declared_file = {}


def names():
    return tuple(sorted(ROSTER, key=lambda n: ROSTER[n].roster_index))


def get(name):
    key = (name or '').strip().lower()
    if key not in ROSTER:
        raise ValueError('unknown hero %r; choose one of %s'
                         % (name, ', '.join(names())))
    return ROSTER[key]


def declare(name):
    hero = get(name)
    path = os.environ.get(DECLARED_ENV)
    if path:
        directory = os.path.dirname(path) or '.'
        os.makedirs(directory, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix='active-hero-', suffix='.tmp', dir=directory)
        try:
            with os.fdopen(handle, 'w') as stream:
                json.dump({'character': hero.name}, stream)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    _declared['name'] = hero.name
    return hero


def declared():
    path = os.environ.get(DECLARED_ENV)
    if not path:
        return _declared.get('name')
    try:
        status = os.stat(path)
    except OSError:
        return None


    stamp = (path, status.st_ino, status.st_mtime_ns, status.st_size)
    if _declared_file.get('stamp') != stamp:
        try:
            with open(path) as stream:
                name = get(json.load(stream)['character']).name
        except (OSError, ValueError, KeyError, TypeError):
            name = None
        _declared_file.update(stamp=stamp, name=name)
    return _declared_file['name']


def selected():
    value = os.environ.get(ENV)
    if (value or '').strip().lower() == MENU:
        return get(declared() or DEFAULT_HERO)
    return get(value or DEFAULT_HERO)
