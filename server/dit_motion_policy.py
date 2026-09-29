"""Read only DIT-approved Crucible movement tendencies for solo hunters."""
import json
import math
import pathlib


TENDENCY_KEY = "motion/pause-bout-seconds/field"
MIN_PAUSE_SECONDS = 0.25
MAX_PAUSE_SECONDS = 20.0


def approved_patrol_pause_range(root):
    """Return a reviewed human pause interquartile range that includes Crucible.

    DIT candidates, provisional evidence, malformed files, and out-of-range
    values leave the authored hunter behavior unchanged.
    """
    if not root:
        return None
    path = pathlib.Path(root).expanduser() / "knowledge" / "shared" / "human-tendencies.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    if document.get("schemaVersion") != 1 or document.get("source") != "dit-neuronetwork":
        return None
    items = document.get("tendencies")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        tendency = item.get("tendency", {})
        if not isinstance(tendency, dict):
            continue
        evidence_games = tendency.get("evidenceGames")
        if (item.get("key") != TENDENCY_KEY or item.get("approval") != "active"
                or tendency.get("provisional") is not False
                or not isinstance(evidence_games, list)
                or "crucible" not in evidence_games):
            continue
        distribution = tendency.get("distribution", {})
        low, median, high = (distribution.get(name) for name in ("p25", "median", "p75"))
        if all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in (low, median, high)):
            if (MIN_PAUSE_SECONDS <= low <= median <= high <= MAX_PAUSE_SECONDS):
                return float(low), float(high)
    return None
