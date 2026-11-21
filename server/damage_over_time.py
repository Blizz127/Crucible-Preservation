"""Accrue per-target damage from caller-supplied effects."""

from dataclasses import dataclass


TICK_SECONDS = 1.0
STACKING = True


@dataclass
class Stack:
    attacker: int
    target: int
    rate: float
    ends: float
    last: float


@dataclass
class Book:
    stacks: list

    def add(self, attacker, target, explosion, now, distance=None):
        total = explosion.dot_total(distance)
        if total <= 0.0 or explosion.dot_seconds <= 0.0:
            return None
        stack = Stack(attacker, target, total / explosion.dot_seconds,
                      now + explosion.dot_seconds, now)
        if not STACKING:
            self.stacks = [s for s in self.stacks
                           if (s.attacker, s.target) != (attacker, target)]
        self.stacks.append(stack)
        return stack

    def drop_target(self, target):
        self.stacks = [s for s in self.stacks if s.target != target]

    def due(self, arena, now):
        keep, owed = [], {}
        for stack in self.stacks:
            attacker = arena.entities.get(stack.attacker)
            target = arena.entities.get(stack.target)
            if (attacker is None or target is None or not attacker.alive
                    or not target.alive):
                continue
            until = min(now, stack.ends)
            finished = now >= stack.ends
            if finished or now - stack.last >= TICK_SECONDS:
                owed[stack.attacker, stack.target] = (
                    owed.get((stack.attacker, stack.target), 0.0)
                    + stack.rate * (until - stack.last))
                stack.last = until
            if not finished:
                keep.append(stack)
        self.stacks = keep
        hits = []
        for (attacker, target), damage in owed.items():
            if damage <= 0.0:
                continue
            hit = arena.apply_damage(attacker, target, damage, now=now)
            if hit is None:
                continue
            hits.append(hit)
            if hit.killed:
                self.drop_target(target)
        return hits


def book(state):
    return state.setdefault('dot', Book([]))


def note_hit(state, arena, shooter, hit, explosion, now):
    if hit.killed:
        return None
    return book(state).add(shooter, hit.target, explosion, now)
