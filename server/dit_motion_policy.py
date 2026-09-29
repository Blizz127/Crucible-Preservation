import json
import math
import pathlib


MIN_PAUSE_SECONDS = 0.25
MAX_PAUSE_SECONDS = 20.0


def approved_patrol_pause_range(path, *, source, game, tendency_key):
    if not path:
        return None
    path = pathlib.Path(path).expanduser()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    if document.get("schemaVersion") != 1 or document.get("source") != source:
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
        if (item.get("key") != tendency_key or item.get("approval") != "active"
                or tendency.get("provisional") is not False
                or not isinstance(evidence_games, list)
                or game not in evidence_games):
            continue
        distribution = tendency.get("distribution", {})
        low, median, high = (distribution.get(name) for name in ("p25", "median", "p75"))
        if all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in (low, median, high)):
            if (MIN_PAUSE_SECONDS <= low <= median <= high <= MAX_PAUSE_SECONDS):
                return float(low), float(high)
    return None
