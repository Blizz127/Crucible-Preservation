"""Metadata-only census of the retail creature population, per map.

Derived from the installed level.dynamicslice and creature slices, not copies
of assets. Network descriptor counts are captured in the schema snapshot. This
describes spawning and which authored AI profile each entity runs; it does not
itself implement creature decisions (see server/creature_ai.py).

The Practice constants below are the accepted, live-observed placement and are
kept byte-for-byte: shard_stub.build_practice_npc_probes still imports them.
Everything added on top is a generalisation over maps, and every other map's
population is declared empty because no baked network IDs for its creature
spawners have been observed on the wire yet.
"""
from dataclasses import dataclass

SPITTER_SLICE_HASH = 1141747700
PRACTICE_LEVEL_HASH = 1289380704
SPITTER_SHARED_TYPES = (2, 13, 18, 24, 44, 46, 64, 79, 80, 101, 107, 115,
                       143, 145, 156, 158, 165, 166)
DUMMY_SHARED_TYPES = (13, 24, 44, 46, 79, 80, 101, 107, 132, 145, 146,
                     156, 158, 165, 166)
# World transforms on baked SpitterRespawner entries 138 and 139.
SPITTER_POSITIONS = ((508.07464599609375, 640.0597534179688, 39.54024887084961),
                     (498.94964599609375, 640.1847534179688, 40.54024887084961))
# Existing baked RoboDummy entries 73..80, authored rotation 180 degrees Z.
DUMMY_POSITIONS = ((511.6386413574219, 605.4163818359375, 37.990962982177734),
                   (503.3886413574219, 609.4163818359375, 37.990962982177734),
                   (515.5000610351562, 611.9999389648438, 45.999996185302734),
                   (520.8886108398438, 617.6663818359375, 37.990962982177734),
                   (514.3886108398438, 628.1663818359375, 39.740962982177734),
                   (491.8886413574219, 615.1663818359375, 37.990962982177734),
                   (502.00006103515625, 618.4999389648438, 48.249996185302734),
                   (507.6386413574219, 622.4163818359375, 37.990962982177734))
# Network IDs the accepted probe assigns: spitters counter 3 and 4 in the
# connection's own space, RoboDummies the baked FE0000xx of entry+2.
SPITTER_ENTITY_IDS = (0x01000003, 0x01000004)
DUMMY_ENTITY_IDS = tuple(0xfe00004b + index for index in range(8))


@dataclass(frozen=True)
class Placement:
    """One replicated creature, and which creature_ai profile it runs.

    `profile` is None for inert props such as the RoboDummy targets, which
    have no AiAsset in their slice and therefore no behaviour to restore.
    """
    entity_id: int
    profile: str
    position: tuple
    yaw: float = 0.0
    slice_hash: int = 0
    slice_entry: int = 0
    health_max: float = 0.0
    shared_types: tuple = ()
    local_id: bool = False


PRACTICE_CREATURES = tuple(
    Placement(entity_id=entity_id, profile='spitter', position=position,
              slice_hash=SPITTER_SLICE_HASH, slice_entry=0, health_max=400.0,
              shared_types=SPITTER_SHARED_TYPES)
    for entity_id, position in zip(SPITTER_ENTITY_IDS, SPITTER_POSITIONS))

# RoboDummy entries 73..80: authored 180-degree Z rotation, 600/600 health,
# no AI asset. They are damage targets, not creatures with behaviour.
PRACTICE_TARGETS = tuple(
    Placement(entity_id=entity_id, profile=None, position=position,
              yaw=3.141592653589793, slice_hash=PRACTICE_LEVEL_HASH,
              slice_entry=73 + index, health_max=600.0,
              shared_types=DUMMY_SHARED_TYPES, local_id=True)
    for index, (entity_id, position) in
    enumerate(zip(DUMMY_ENTITY_IDS, DUMMY_POSITIONS)))

# Per-map creature population. Only Practice has observed network IDs.
#
# String census of the installed levels (regex over the decompressed
# level.dynamicslice for the engine's AZStd::string encoding; a name that
# occurs once is the spawner archetype itself, so single counts are not
# instances):
#   april2018   89 Spawner_Spitter, 97 Spawner_MegaDrone, 62 Spawner_Snurtle,
#               47 Spawner_Hive, 115 SpawnNodeMatchStart
#   HotH_02     97 Spawner_MegaDrone, 7 SpitterCamp, 7 SpitterColony,
#               8 SnurtleCamp, 8 SnurtleHerd, 10 MegaDroneCampAnchor,
#               7 Spawner_Hive, 12 GuideEnemyBotSpawner
#   tutorial_02 10 Spawner_Spitter, 4 Spawner_Snurtle
#   tutorial_03 3 Snurtle, 1 Spitter, 1 TutorialSpitterSpawn (plus the only
#               level referencing ai/sentry_laser.ai and ai/snurtle_noaggro.ai)
#   practicearena  the two SpitterRespawners and eight RoboDummies above
# Populating those maps needs each spawner's baked entity index and a live
# ID mapping the way Practice's entry+2 and April2018's entry+787 were found.
POPULATION = {
    'practicearena': PRACTICE_CREATURES + PRACTICE_TARGETS,
    'april2018': (),
    'hoth_02': (),
    'tutorial_02': (),
    'tutorial_03': (),
}


def population(level):
    """Placements for a solo_modes.SoloMode.level name; empty when unaudited."""
    return POPULATION.get(level.lower(), ())


def creatures(level):
    """Only the placements that have an authored AI profile to run."""
    return tuple(place for place in population(level) if place.profile)
