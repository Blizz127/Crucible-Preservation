"""Server-resolved Marine hunter hits (docs/35, owner's option a).

The retail client never reports hits from projectiles it does not own, so a
hunter's rounds can only hurt the player if the server resolves them, as
retail's server did. Each Shot from hunter_ai draws a deviation inside the
authored Marine spread cone. A deviation that falls within the target
capsule lands after the projectile's flight time, with the authored falloff.

Everything is opt-in (CRUCIBLE_SOLO_BOT_DAMAGE=server) and seeded, so a
logged match is reproducible. ACCURACY_SCALE is a declared tuning value, not
an authored one. It stands in for AI aim error that is not yet decoded.
"""
from dataclasses import dataclass
import math
import random

from combat import MARINE_PRIMARY, immediate_damage

# assets/player/marine/marine_primary.accuracy, build 5685572.
SPREAD_MIN_DEGREES = 1.5     # MinMaxDegrees.x
SPREAD_MAX_DEGREES = 3.5     # MinMaxDegrees.y
# BloomRatioPerShot control points (bloom -> added bloom). The asset's Bezier
# tangents are nearly linear between keys, so this interpolates linearly.
BLOOM_PER_SHOT = ((0.0, 0.05), (0.480234, 0.139486), (1.0, 0.217935))
# CooldownDuration's first key: 0.3 s at bloom 0. Only that key has been
# read. It is taken as the pause after which bloom resets; firing every
# 96 ms never pauses that long, so a sustained burst holds its bloom.
BLOOM_RECOVERY_SECONDS = 0.3
# Target capsule radius. The hero character slice has not been read yet, so
# this is a declared stand-in (docs/35, known gaps).
TARGET_RADIUS = 0.4
# Default hit-probability scale. Measured by seeded simulation of
# continuous fire, mean over 20 seeds, time to drain 800 health:
#   1.0  (raw cone): 3 Marines at 15 m 5 s, 1 Marine at 15 m 18 s, 3 at 8 m 2 s
#   0.25 (default):  3 Marines at 15 m 25 s, 1 Marine at 15 m 80 s, 3 at 8 m 7 s
# so the player can fight back but closing on three Marines is still deadly.
ACCURACY_SCALE = 0.25
MAX_REACH = MARINE_PRIMARY.speed * MARINE_PRIMARY.lifetime_s


def bloom_increment(bloom):
    for (x0, y0), (x1, y1) in zip(BLOOM_PER_SHOT, BLOOM_PER_SHOT[1:]):
        if bloom <= x1:
            return y0 + (y1 - y0) * (max(bloom, x0) - x0) / (x1 - x0)
    return BLOOM_PER_SHOT[-1][1]


def spread_degrees(bloom):
    return SPREAD_MIN_DEGREES + (SPREAD_MAX_DEGREES - SPREAD_MIN_DEGREES) * bloom


@dataclass(frozen=True)
class Landing:
    shooter: int
    target: int
    time: float
    damage: float


class HunterGunnery:
    """Per-hunter bloom and a seeded hit roll for every round fired."""

    def __init__(self, *, seed=0, accuracy_scale=ACCURACY_SCALE,
                 target_radius=TARGET_RADIUS):
        if not 0.0 <= accuracy_scale <= 1.0:
            raise ValueError('accuracy scale must be within 0..1')
        self.rng = random.Random(seed)
        self.accuracy_scale = accuracy_scale
        self.target_radius = target_radius
        self.bloom = {}
        self.last_shot = {}

    def resolve(self, shot):
        """Return a Landing for a round that connects, else None."""
        bloom = self.bloom.get(shot.shooter, 0.0)
        last = self.last_shot.get(shot.shooter)
        if last is not None and shot.time - last >= BLOOM_RECOVERY_SECONDS:
            bloom = 0.0
        cone = math.tan(math.radians(spread_degrees(bloom)))
        self.bloom[shot.shooter] = min(1.0, bloom + bloom_increment(bloom))
        self.last_shot[shot.shooter] = shot.time
        # Uniform over the cone's disk: radius scales with sqrt(u).
        deviation = cone * math.sqrt(self.rng.random())
        aimed = self.rng.random() < self.accuracy_scale
        if (not aimed or shot.range <= 0 or shot.range > MAX_REACH
                or deviation > self.target_radius / shot.range):
            return None
        return Landing(shot.shooter, shot.target,
                       shot.time + shot.range / MARINE_PRIMARY.speed,
                       immediate_damage(MARINE_PRIMARY, distance=shot.range))
