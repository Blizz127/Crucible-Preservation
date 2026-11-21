"""State and bookkeeping for a caller-supplied collection of harvesters."""


class Harvester:
    __slots__ = ('letter', 'entry', 'position')

    def __init__(self, letter, entry, position):
        self.letter, self.entry, self.position = letter, entry, position

    def __repr__(self):
        return 'Harvester(%r, %d, %r)' % (self.letter, self.entry, self.position)


def counter(profile, harvester):
    return harvester.entry + profile.baked_counter_bias


def new_state(stations, now, *, heartbeat_seconds):
    if heartbeat_seconds <= 0:
        raise ValueError('heartbeat_seconds must be positive')
    return {
        'origin': now,
        'next_beat': now + heartbeat_seconds,
        'sent': False,
        'units': {
            station.letter: {
                'status': 'neutral', 'capture_started': None, 'near_since': None,
                'owner': None, 'owned_at': None, 'interrupts': 0, 'sends': 0,
                'next_progress': 0.0,
            }
            for station in stations
        },
    }


def _heartbeat_step(now, hv, *, heartbeat_seconds, pay):
    """Call pay(letter, beat) for stations held when a scheduled beat falls due."""
    if heartbeat_seconds <= 0:
        raise ValueError('heartbeat_seconds must be positive')
    while now >= hv['next_beat'] - 1e-9:
        beat = hv['next_beat']
        hv['next_beat'] = beat + heartbeat_seconds
        for letter, unit in hv['units'].items():
            if unit['status'] == 'owned' and unit['owned_at'] <= beat + 1e-9:
                pay(letter, beat)
