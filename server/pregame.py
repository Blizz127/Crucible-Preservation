"""Schedule caller-defined stages and locate elapsed time within them."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    name: str
    phase: int
    seconds: float
    status: int = None


def schedule(phases, wanted):
    """Build a schedule from ordered (name, phase, seconds) entries."""
    wanted = tuple(wanted)
    stages = [Stage(name, value, float(seconds))
              for name, value, seconds in phases if name in wanted]
    if [s.name for s in stages] != list(wanted):
        return []
    return stages


def _locate(stages, elapsed):
    """(index, seconds into it) of the stage `elapsed` falls in; len(stages)
    is the hand-over."""
    start = 0.
    for index, stage in enumerate(stages):
        if elapsed < start + stage.seconds:
            return index, elapsed - start
        start += stage.seconds
    return len(stages), elapsed - start
