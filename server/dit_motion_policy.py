"""Read only DIT-approved Crucible movement tendencies for solo hunters."""
import json
import math
import pathlib


TENDENCY_KEYS = {
    "pause": "motion/pause-bout-seconds/field",
    "move": "motion/move-bout-seconds/field",
}
MIN_PAUSE_SECONDS = 0.25
MAX_PAUSE_SECONDS = 20.0


def approved_patrol_motion_ranges(root):
    """Return reviewed human pause and move interquartile ranges.

    DIT candidates, provisional evidence, malformed files, and out-of-range
    values leave the corresponding authored behavior unchanged.
    """
    if not root:
        return None, None
    path = pathlib.Path(root).expanduser() / "knowledge" / "shared" / "human-tendencies.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(document, dict):
        return None, None
    if document.get("schemaVersion") != 1 or document.get("source") != "dit-neuronetwork":
        return None
    items = document.get("tendencies")
    if not isinstance(items, list):
        return None, None
    found = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        tendency = item.get("tendency", {})
        if not isinstance(tendency, dict):
            continue
        evidence_games = tendency.get("evidenceGames")
        behavior = next((name for name, key in TENDENCY_KEYS.items()
                         if item.get("key") == key), None)
        if (behavior is None or item.get("approval") != "active"
                or tendency.get("provisional") is not False
                or not isinstance(evidence_games, list)
                or "crucible" not in evidence_games):
            continue
        distribution = tendency.get("distribution", {})
        low, median, high = (distribution.get(name) for name in ("p25", "median", "p75"))
        if all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in (low, median, high)):
            if (MIN_PAUSE_SECONDS <= low <= median <= high <= MAX_PAUSE_SECONDS):
                found[behavior] = (float(low), float(high))
    return found.get("pause"), found.get("move")
