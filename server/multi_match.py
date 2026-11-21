import dataclasses
from dataclasses import dataclass

@dataclass
class Player:
    slot: int
    user_id: int
    state: dict
    hero: object
    team: int
    character_id: int
    connection_id: int
    position: tuple
    left: bool = False

    @property
    def name(self):
        return self.state.get('local_player_name', b'')

@dataclass
class Snapshot:
    steps: int
    positions: dict
    facings: dict = dataclasses.field(default_factory=dict)
    velocities: dict = dataclasses.field(default_factory=dict)
    transforms: dict = dataclasses.field(default_factory=dict)
    movement: dict = dataclasses.field(default_factory=dict)
    at: float = None

def viewer_messages(match, viewer, messages, *, message_id):
    multi = viewer.state['multi']
    own = {viewer.connection_id, viewer.character_id}
    foreign = (match.connection_ids() | match.character_ids()) - own
    closed, kept = (set(), [])
    for message in messages:
        entity, is_delete = message_id(message)
        if entity in multi['deleted'] or entity in closed:
            continue
        if entity in foreign and entity not in multi['known']:
            continue
        if is_delete:
            closed.add(entity)
        kept.append(message)
    multi['deleted'] |= closed
    multi['known'] -= closed
    return kept
