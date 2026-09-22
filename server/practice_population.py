"""Caller-populated registry of entity placements by level."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Placement:
    """One server-side placement, supplied by the caller."""
    entity_id: int
    profile: str
    position: tuple
    yaw: float = 0.0
    slice_hash: int = 0
    slice_entry: int = 0
    health_max: float = 0.0
    shared_types: tuple = ()
    local_id: bool = False


POPULATION = {}


def population(level):
    """Return registered placements for a level."""
    return POPULATION.get(level.lower(), ())


def creatures(level):
    """Return only placements with an AI profile."""
    return tuple(place for place in population(level) if place.profile)
