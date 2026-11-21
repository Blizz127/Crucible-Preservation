"""Timed pickup scheduling and collection for caller-supplied match data."""

import math
import random
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class PickupRules:
    interaction_radius: float
    interaction_seconds: float
    despawn_seconds: float
    id_reuse_seconds: float
    refusal_interval: float
    max_spawns_per_tick: int
    max_deletes_per_tick: int


@dataclass
class Pack:
    entity: int
    node: int
    position: tuple
    source: str
    state: str = 'queued'
    live_at: float = None
    started_at: float = None
    delete_at: float = None


class World:
    """Schedule pickups and return a sequence of events for each tick."""

    def __init__(self, initial_nodes, scheduled_nodes, *, rules, spawn_sets,
                 entity_ids, seed=None):
        self.initial_nodes = tuple(initial_nodes)
        self.scheduled_nodes = tuple(scheduled_nodes)
        self.rules = rules
        self.spawn_sets = tuple(spawn_sets)
        self.rng = random.Random(seed)
        self.ids = deque((entity, 0.0) for entity in entity_ids)
        self.packs = {}
        self.queue = []
        self.sets_done = 0
        self.started = False
        self.collecting = None
        self.refused_at = None
        self.counts = {'refused': 0, 'cancelled': 0, 'picked': 0}

    def _queue(self, node, position, source):
        pack = Pack(0, node, position, source)
        self.packs[node] = pack
        self.queue.append(pack)

    def _schedule(self, now, events):
        if not self.started:
            self.started = True
            for node, position in self.initial_nodes:
                self._queue(node, position, 'match start')
            events.append(('match start', len(self.initial_nodes)))
        while (self.sets_done < len(self.spawn_sets)
               and now >= self.spawn_sets[self.sets_done][0]):
            when, wanted = self.spawn_sets[self.sets_done]
            self.sets_done += 1
            free = [(node, position) for node, position in self.scheduled_nodes
                    if node not in self.packs]
            chosen = self.rng.sample(free, min(wanted, len(free)))
            for node, position in chosen:
                self._queue(node, position, 'set %d' % self.sets_done)
            events.append(('set', (self.sets_done, when, len(chosen), len(free))))

    def _nearest_live(self, player):
        best = None
        for pack in self.packs.values():
            if pack.state != 'live':
                continue
            distance = math.dist(player, pack.position)
            if distance <= self.rules.interaction_radius and (best is None or distance < best[0]):
                best = (distance, pack)
        return None if best is None else best[1]

    def _pickup(self, now, player, alive, pressed, at_maximum, events):
        pack = self.collecting
        if pack is not None:
            in_range = (alive and player is not None
                        and math.dist(player, pack.position) <= self.rules.interaction_radius)
            if not in_range:
                pack.state, pack.started_at = 'live', None
                self.collecting = None
                self.counts['cancelled'] += 1
                events.append(('cancelled', pack))
            elif now - pack.started_at >= self.rules.interaction_seconds - 1e-9:
                if at_maximum:
                    pack.state, pack.started_at = 'live', None
                    self.collecting = None
                    self.counts['refused'] += 1
                    events.append(('refused', pack))
                    return
                pack.state = 'taken'
                pack.delete_at = round(now + self.rules.despawn_seconds, 3)
                self.collecting = None
                self.counts['picked'] += 1
                events.append(('picked', pack))
            return
        if not (alive and player is not None and pressed):
            return
        near = self._nearest_live(player)
        if near is None:
            return
        if at_maximum:
            if self.refused_at is None or now - self.refused_at > self.rules.refusal_interval:
                self.counts['refused'] += 1
                events.append(('refused', near))
            self.refused_at = now
            return
        near.state, near.started_at = 'collecting', now
        self.collecting = near
        events.append(('started', near))

    def tick(self, now, *, player, alive, pressed, at_maximum, play=None):
        """Use play time for scheduling and now for pickup and ID lifetimes."""
        events = []
        self._schedule(now if play is None else play, events)
        self._pickup(now, player, alive, pressed, at_maximum, events)
        deleting = [pack for pack in self.packs.values()
                    if pack.state == 'taken' and now >= pack.delete_at][:self.rules.max_deletes_per_tick]
        for pack in deleting:
            del self.packs[pack.node]
            self.ids.append((pack.entity, round(now + self.rules.id_reuse_seconds, 3)))
            events.append(('delete', pack))
        spawned = 0
        while self.queue and spawned < self.rules.max_spawns_per_tick:
            if not self.ids or self.ids[0][1] > now:
                break
            pack = self.queue.pop(0)
            pack.entity = self.ids.popleft()[0]
            pack.state, pack.live_at = 'live', now
            spawned += 1
            events.append(('spawn', pack))
        return events
