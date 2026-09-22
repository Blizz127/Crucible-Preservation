"""Track combatants, health changes, and scheduled respawns."""


from dataclasses import dataclass, field
import time


@dataclass
class CombatEntity:
    entity_id: int
    shared_types: tuple
    maximum: float
    team: int
    local: bool = False
    health: float = None
    respawn_seconds: float = None
    telegraph_seconds: float = 0.0
    respawn_at: float = None
    dirty: bool = False

    def __post_init__(self):
        if self.health is None:
            self.health = self.maximum

    @property
    def alive(self):
        return self.health > 0.0


@dataclass(frozen=True)
class HitResult:
    target: int
    damage: float
    health: float
    killed: bool


@dataclass
class Combat:
    entities: dict = field(default_factory=dict)

    def register(self, entity_id, shared_types, *, maximum, team, local=False,
                 health=None, respawn_seconds=None, telegraph_seconds=0.0):
        entity = CombatEntity(entity_id, tuple(shared_types), float(maximum),
                              team, local, None if health is None else float(health),
                              respawn_seconds, telegraph_seconds)
        self.entities[entity_id] = entity
        return entity

    def health(self, entity_id):
        return self.entities[entity_id].health

    def alive(self, entity_id):
        return self.entities[entity_id].alive

    def _die(self, entity, now):
        if entity.respawn_seconds is not None:
            entity.respawn_at = (now + entity.respawn_seconds
                                 + entity.telegraph_seconds)

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        for entity in self.entities.values():
            if entity.respawn_at is not None and now >= entity.respawn_at:
                entity.respawn_at = None
                entity.health = entity.maximum
                entity.dirty = True
        return [entity for entity in sorted(self.entities.values(),
                                            key=lambda e: e.entity_id)
                if entity.dirty]
