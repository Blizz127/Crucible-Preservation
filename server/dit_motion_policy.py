import json
import math
import pathlib


MIN_PAUSE_SECONDS = 0.25
MAX_PAUSE_SECONDS = 20.0


def approved_patrol_motion_ranges(path, *, source, game, tendency_keys):
    if not path:
        return None, None
    path = pathlib.Path(path).expanduser()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(document, dict):
        return None, None
    if document.get("schemaVersion") != 1 or document.get("source") != source:
        return None, None
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
        behavior = next((name for name, key in tendency_keys.items()
                         if item.get("key") == key), None)
        if (behavior is None or item.get("approval") != "active"
                or tendency.get("provisional") is not False
                or not isinstance(evidence_games, list)
                or game not in evidence_games):
            continue
        distribution = tendency.get("distribution", {})
        if not isinstance(distribution, dict):
            continue
        low, median, high = (distribution.get(name) for name in ("p25", "median", "p75"))
        if all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in (low, median, high)):
            if (MIN_PAUSE_SECONDS <= low <= median <= high <= MAX_PAUSE_SECONDS):
                found[behavior] = (float(low), float(high))
    return found.get("pause"), found.get("move")
