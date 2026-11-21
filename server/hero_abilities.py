"""Index caller-supplied ability and projectile records."""

from dataclasses import dataclass


@dataclass(frozen=True)
class HeroData:
    loadout: str
    request: str
    request_slots: tuple
    abilities: tuple
    projectiles: tuple
    explosions: tuple

    def projectile(self, asset):
        return next(p for p in self.projectiles if p.asset == asset)

    def entered_by(self, action):
        return tuple(a for a in self.abilities if action in a.enter)

    def projectile_at(self, request_index):
        return next((p for p in self.projectiles if p.request_index == request_index), None)


ROSTER = {}
