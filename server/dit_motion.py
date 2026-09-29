"""Local, aggregate-only motion observations for DIT's file transport.

This adapter records no coordinates, combat state, or identity. Positions are
kept only in memory long enough to estimate speed and are discarded on flush.
"""
from collections import deque
import json
import math
import os
import pathlib
import statistics
import tempfile
import time
from datetime import datetime, timezone


SAMPLE_PERIOD = 0.25
MOVING_SPEED = 0.1  # DIT Crucible world-model threshold, metres per second.
STATE_HOLD = 0.5
FLUSH_PERIOD = 60.0
MAX_BOUTS = 400
MAX_SPEEDS = 400


class MotionTransport:
    def __init__(self, root):
        self.root = pathlib.Path(root).expanduser()
        self.tracks = {}

    def observe(self, actor, position, now=None):
        """Sample a position tuple; persist only movement aggregates."""
        if not isinstance(position, (tuple, list)) or len(position) != 3:
            return
        try:
            point = tuple(float(value) for value in position)
        except (TypeError, ValueError):
            return
        if not all(math.isfinite(value) for value in point):
            return
        now = time.monotonic() if now is None else float(now)
        track = self.tracks.get(actor)
        if track is None:
            track = self._load(actor, now)
            self.tracks[actor] = track
        previous = track.get("position")  # last accepted sample, memory only
        if previous is None:
            track["position"] = point
            track["sample_at"] = now
            track["flush_at"] = now
            return
        elapsed = now - track["sample_at"]
        if elapsed <= 0:
            return
        speed = math.dist(previous, point) / elapsed
        if elapsed > 5.0 or speed > 100.0:  # discard reconnect gaps and teleports
            track["position"] = point
            track["sample_at"] = now
            track["pending_state"] = None
            track["pending_since"] = now
            return
        if now - track["sample_at"] < SAMPLE_PERIOD:
            return
        track["position"] = point
        track["sample_at"] = now
        moving = speed >= MOVING_SPEED
        track["samples"] += 1
        track["moving_samples"] += int(moving)
        track["seconds_observed"] += SAMPLE_PERIOD
        if moving:
            track["speeds"].append(speed)
        self._transition(track, moving, now)
        if now - track["flush_at"] >= FLUSH_PERIOD:
            try:
                self._flush(actor, track, now)
            except OSError:
                pass  # An unavailable optional transport cannot interrupt gameplay.

    def flush_all(self, now=None):
        now = time.monotonic() if now is None else float(now)
        for actor, track in self.tracks.items():
            try:
                self._flush(actor, track, now)
            except OSError:
                pass

    def _transition(self, track, moving, now):
        current = track["moving"]
        if moving == current:
            track["pending_state"] = None
            return
        if track["pending_state"] != moving:
            track["pending_state"] = moving
            track["pending_since"] = now
            return
        if now - track["pending_since"] < STATE_HOLD:
            return
        duration = max(0.0, now - track["bout_since"])
        bouts = track["move_bouts"] if current else track["pause_bouts"]
        bouts.append(round(duration, 3))
        del bouts[:-MAX_BOUTS]
        track["moving"] = moving
        track["bout_since"] = now
        track["pending_state"] = None

    def _load(self, actor, now):
        path = self._path(actor)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        def numbers(name):
            values = doc.get(name, [])
            return deque((float(value) for value in values[-MAX_BOUTS:]
                          if isinstance(value, (int, float)) and math.isfinite(value)),
                         maxlen=MAX_BOUTS)
        return {
            "position": None, "sample_at": now, "flush_at": now,
            "samples": max(0, int(doc.get("samples", 0) or 0)),
            "moving_samples": max(0, int(doc.get("movingSamples", 0) or 0)),
            "seconds_observed": max(0.0, float(doc.get("secondsObserved", 0) or 0)),
            "pause_bouts": numbers("pauseBoutSamples"),
            "move_bouts": numbers("moveBoutSamples"),
            "speeds": numbers("_recentSpeeds"),
            "moving": False, "pending_state": None,
            "pending_since": now, "bout_since": now,
        }

    def _path(self, actor):
        kind, name = actor
        directory = self.root / "knowledge" / "observed"
        if kind == "bot":
            directory /= "bots"
        safe = "".join(char.lower() if char.isalnum() or char in "-_" else "-"
                       for char in name).strip("-")
        return directory / ((safe or "actor") + ".json")

    def _flush(self, actor, track, now):
        path = self._path(actor)
        path.parent.mkdir(parents=True, exist_ok=True)
        kind, name = actor
        speed_median = statistics.median(track["speeds"]) if track["speeds"] else None
        doc = {
            "schemaVersion": 1,
            "player": name,
            "isBot": kind == "bot",
            "updatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "samples": track["samples"],
            "movingSamples": track["moving_samples"],
            "secondsObserved": round(track["seconds_observed"], 3),
            "movingShare": round(track["moving_samples"] / track["samples"], 4) if track["samples"] else 0.0,
            "pauseBoutSamples": list(track["pause_bouts"]),
            "moveBoutSamples": list(track["move_bouts"]),
            "_recentSpeeds": list(track["speeds"]),
        }
        if speed_median is not None:
            doc["runSpeedMedian"] = round(speed_median, 3)
        fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(doc, stream, separators=(",", ":"), allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            track["flush_at"] = now
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def transport_for_state(state):
    """Return an opt-in transport for this match, if configured."""
    root = os.environ.get("CRUCIBLE_DIT_TRANSPORT_ROOT", "").strip()
    if not root:
        return None
    transport = state.get("dit_motion")
    if transport is None:
        transport = state["dit_motion"] = MotionTransport(root)
    return transport
