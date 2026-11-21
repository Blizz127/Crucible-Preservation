"""Inventory and time-based healing arithmetic."""

from dataclasses import dataclass, field


@dataclass
class Medpack:
    count: float
    maximum: float
    heal_rate: float
    heals: list = field(default_factory=list)

    @property
    def heal_left(self):
        return sum(left for left, _ in self.heals)

    def heal(self, now):
        total = 0.0
        for running in self.heals:
            left, paid_to = running
            if now <= paid_to:
                continue
            amount = min(left, self.heal_rate * (now - paid_to))
            running[0], running[1] = left - amount, now
            total += amount
        self.heals[:] = [running for running in self.heals if running[0] > 1e-9]
        return total

    def add(self, amount=1.0):
        if self.count >= self.maximum:
            return False
        self.count = min(self.maximum, self.count + amount)
        return True
