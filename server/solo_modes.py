"""Explicit solo map + mode profiles.

A map is not a mode. This module keeps them apart:

* `MapProfile`  - one installed level: its name, its level-name hash, its
  baked Match* entity array positions, its baked-network-ID bias and its
  authored spawn transforms.
* `ModeProfile` - one entry of the shipped `design/gamemodes/
  gamemodeconfiglist`: its configuration index and the values read out of
  its `.gamemodeconfig`.
* `SoloMode`    - the composed, shard-facing view. Its field names and
  values are unchanged from the previous revision so that `shard_stub.py`
  and `match_stub.py` need no edit, and so that the two visually accepted
  profiles (Practice and Alpha Hunters) serialise byte-for-byte as before.

Evidence for every number is cited in `docs/23-maps-and-modes.md`. The three
evidence classes are kept apart in the `*_source` fields:

  'shipped'   read directly out of an installed asset
  'computed'  produced by a formula derived from shipped assets
  'live'      confirmed by an observed client run
  'unmeasured' a placeholder that has never been observed or derived

Only `practice` (practicearena) and `alpha-hunters` (april2018) have
confirmed live map entry. Everything else here is a candidate.
"""
from dataclasses import dataclass, field
import os


# ---------------------------------------------------------------------------
# MatchPhaseType
# ---------------------------------------------------------------------------
# Retail enum-name pointer table at .rdata 0x65bc9c0 (names in
# GameCommon\Crucible\Services\BakedNames\MatchPhaseType.cpp, string block
# 0x6be7840..0x6be7950). Index 8 = GamePlay agrees with the live-accepted
# `00 08` CurrentPhase wire value in shard_stub.solo_phase_reply.
MATCH_PHASE = {
    'WaitForPlayers': 0,
    'CharacterPreview': 1,
    'CharacterSelect': 2,
    'PerkPreview': 3,
    'TeamInfo': 4,
    'PerkSelect': 5,
    'DropPreview': 6,
    'DropSelect': 7,
    'GamePlay': 8,
    'GameOver': 9,
    'Shutdown': 10,
}
SOLO_PHASE = MATCH_PHASE['GamePlay']


# ---------------------------------------------------------------------------
# Level-name hashes
# ---------------------------------------------------------------------------
def level_name_hash(path):
    """Jenkins one-at-a-time over the lowercased asset path.

    Recovered from `levels/*/commondata/stringhashlibrary.xml`: this function
    reproduces 906 of 906 non-empty Key/Value pairs in practicearena's
    library, including the two hashes already in live use
    (levels/practicearena/commondata/level -> 1289380704 and
    levels/april2018/commondata/level -> 161882647).
    """
    mask = 0xFFFFFFFF
    value = 0
    for byte in path.lower().encode():
        value = (value + byte) & mask
        value = (value + (value << 10)) & mask
        value ^= value >> 6
    value = (value + (value << 3)) & mask
    value ^= value >> 11
    return (value + (value << 15)) & mask


def level_hash(level):
    return level_name_hash('levels/%s/commondata/level' % level)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MapProfile:
    """One installed level. Entry numbers are direct Entities-array positions.

    Array position == the `SliceEntryId` the client validates in
    CreateReplicatedIndexedEntity (retail 0x4bf5120) because every installed
    level slice has an empty slice-reference list (field CRC 0xafa37bc9), so
    AZ::SliceComponent::GetEntities prepends no nested-instance entities.
    Reproduce with `tools/inspect_map_entities.py`.
    """
    level: str
    level_hash: int
    level_hash_source: str
    entity_array_length: int
    phase_entry: int
    mode_entry: int
    store_entry: int
    match_components_entry: int
    baked_counter_bias: int
    baked_counter_bias_source: str
    player_position: tuple
    player_drop_entry: int
    player_drop_name: str
    bot_positions: tuple = ()
    bot_drop_entries: tuple = ()
    hive_spawns: tuple = ()          # (name, entry, (x, y, z))
    creature_spawns: tuple = ()      # (name, entry, (x, y, z))
    notes: str = ''


@dataclass(frozen=True)
class ModeProfile:
    """One entry of the shipped gamemodeconfiglist, plus its config values."""
    name: str
    configuration: int
    config_asset: str
    minimum_players: int
    target_score: int
    match_point_threshold: int
    phase_sequence: tuple            # ((phase name, enum value, seconds), ...)
    alliances_enabled: bool
    static_alliance_mode: bool
    enable_respawn: bool
    ui_mode_override: str
    roster_size: int
    local_team: int
    bot_team: int
    authored_maps: tuple
    flags: tuple = ()
    notes: str = ''

    @property
    def auto_skip_phase_to_gameplay(self):
        return 'AutoSkipPhaseToGameplay' in self.flags

    @property
    def waits_for_human_players(self):
        """True when the shipped phase order would stall a solo peer.

        Solo startup must never honour this: shard_stub sends the GamePlay
        phase directly. Modes carrying AutoSkipPhaseToGameplay do not need
        that bypass at all.
        """
        return (self.minimum_players > 1
                and not self.auto_skip_phase_to_gameplay)


@dataclass(frozen=True)
class SoloMode:
    """Shard-facing composed profile. Field names are frozen by shard_stub."""
    level: str
    level_hash: int
    configuration: int
    mode_entry: int
    phase_entry: int
    store_entry: int
    player_position: tuple
    bot_positions: tuple = ()
    baked_counter_bias: int = 2
    # Everything below is additive; it does not change the wire bytes.
    map_profile: MapProfile = None
    mode_profile: ModeProfile = None
    solo_phase: int = SOLO_PHASE

    @property
    def roster_size(self):
        return self.mode_profile.roster_size if self.mode_profile else 1

    @property
    def local_team(self):
        return self.mode_profile.local_team if self.mode_profile else 1

    @property
    def bot_team(self):
        return self.mode_profile.bot_team if self.mode_profile else 2

    @property
    def baked_counter_bias_is_measured(self):
        return bool(self.map_profile
                    and self.map_profile.baked_counter_bias_source == 'live')


def compose(map_profile, mode_profile):
    """Build the shard-facing profile. The map supplies baked identity, the
    mode supplies the configuration index and the solo roster shape."""
    if mode_profile.authored_maps and map_profile.level not in mode_profile.authored_maps:
        # Not an error: the April2018 Alpha Hunters candidate is exactly this
        # case and it is the only accepted non-Practice entry so far.
        pass
    bots = map_profile.bot_positions[:max(mode_profile.roster_size - 1, 0)]
    return SoloMode(
        map_profile.level, map_profile.level_hash, mode_profile.configuration,
        map_profile.mode_entry, map_profile.phase_entry, map_profile.store_entry,
        map_profile.player_position, bots, map_profile.baked_counter_bias,
        map_profile=map_profile, mode_profile=mode_profile)


# ---------------------------------------------------------------------------
# Maps
# ---------------------------------------------------------------------------
# practicearena: 1198 direct entities. MatchPhaseController 123, GameMode 127,
# MatchDataStore 133, MatchComponents 125 (docs/16). Baked counter = entry + 2
# was confirmed live for entries 73..80 (FE00004B..FE000052), 123, 127 and 133.
PRACTICE_ARENA = MapProfile(
    'practicearena', 1289380704, 'shipped', 1198,
    123, 127, 133, 125, 2, 'live',
    (501.09075927734375, 564.5346069335938, 45.27315902709961),
    695, 'PlayerStartSpawn',
    creature_spawns=(('RoboDummy x8', 73, None),),
    notes='Accepted Training Arena baseline. bot_positions stays empty so '
          'shard_stub keeps using build_practice_npc_probes().')

# april2018: 19529 direct entities. Phase/mode/store 1656/1660/1666; the
# component UUID lists match their practicearena counterparts exactly.
# Baked counter = entry + 787 was established by the first HotH launch
# (hives-first-replicators.json) and re-confirmed by the accepted Alpha
# Hunters run (live-alpha-entry.json).
# Authored drop cluster 17: player 17d (173), hunters 17c (181), 17a (246),
# 17b (268). The map has NO hive spawn nodes at all.
APRIL2018 = MapProfile(
    'april2018', 161882647, 'shipped', 19529,
    1656, 1660, 1666, 1658, 787, 'live',
    (1312.711181640625, 1271.156005859375, 23.670000076293945),
    173, 'Player_DropPoint_17d',
    bot_positions=(
        (1316.273193359375, 1257.5782470703125, 22.709447860717773),
        (1313.4710693359375, 1264.975341796875, 23.624998092651367),
        (1318.4801025390625, 1248.326904296875, 20.875)),
    bot_drop_entries=(181, 246, 268),
    notes='Only confirmed non-Practice entry (Alpha Hunters). No '
          'SpawnNode_Hive_AIOverlord_* and no hive_spawn_* entities exist on '
          'this map, so Heart of the Hives objectives cannot be authored here.')

# HotH_02: 21510 direct entities; phase/mode/store 617/621/627, components 619.
# This is the real Heart of the Hives map: it carries all six hive overlord
# spawn nodes (1605..1610) and the twelve SpawnNode_Guide_EnemyBot nodes
# (33..44) that the HOTH_GUIDE configuration is authored around.
# Its tags.txt, level.cfg and stringhashlibrary.xml are byte-identical to
# april2018's and it even ships april2018.dds minimaps, so its string hash
# library was never regenerated and contains no hoth_02 level entry. Its
# navigation mesh (mnmnavmission0.bai) is byte-identical to april2018's too,
# though 8% of its terrain was re-sculpted (docs/37).
HOTH_02_DROP_CLUSTER = (
    (1316.2420654296875, 921.8919677734375, 19.25),     # 586 Player_DropPoint_09a
    (1329.0159912109375, 929.6680297851562, 20.0),      # 572 Player_DropPoint_09b
    (1328.4779663085938, 921.9210205078125, 20.0),      # 593 Player_DropPoint_09c
    (1331.4970703125, 935.1290283203125, 20.0),         # 592 Player_DropPoint_09d
)
# The twelve SpawnNode_Guide_EnemyBot nodes, entries 33..44 (docs/23), two
# near each hive. Authored for HOTH_GUIDE, but no running script uses them:
# the critical path spawns its bots on rings around the hive and its heart
# (docs/37, stage 4 findings). Exact float32 translations.
HOTH_02_GUIDE_BOT_SPAWNS = (
    (33, (754.2969970703125, 1051.093505859375, 56.45686721801758)),
    (34, (1200.2071533203125, 1203.4083251953125, 25.895103454589844)),
    (35, (905.9044189453125, 756.08154296875, 16.393978118896484)),
    (36, (1208.5262451171875, 806.21826171875, 23.87455177307129)),
    (37, (978.9752807617188, 1209.764892578125, 58.96003341674805)),
    (38, (940.55078125, 721.4732666015625, 13.03976821899414)),
    (39, (1259.547119140625, 1062.1329345703125, 30.893030166625977)),
    (40, (997.5733642578125, 1269.808349609375, 59.713417053222656)),
    (41, (1194.12548828125, 1256.833740234375, 27.506921768188477)),
    (42, (1223.384765625, 847.5761108398438, 26.346139907836914)),
    (43, (817.1101684570312, 1051.6953125, 50.3217658996582)),
    (44, (1300.6107177734375, 1034.3909912109375, 30.026182174682617)),
)
# A presence placeholder, not the retail spawns: the three guide nodes
# nearest the player's drop by navigation path (147, 180 and 228 m). Retail
# spawns no bot at the start; its guide bots come minutes in, around the
# first hive (docs/37, stage 4 findings). The 09b-09d drop pods are off the
# navigation mesh, so bots could not patrol from them.
HOTH_02_BOT_NODES = (44, 36, 39)
HOTH_02 = MapProfile(
    'HotH_02', level_hash('HotH_02'), 'computed', 21510,
    617, 621, 627, 619, 2, 'unmeasured',
    HOTH_02_DROP_CLUSTER[0], 586, 'Player_DropPoint_09a',
    bot_positions=tuple(dict(HOTH_02_GUIDE_BOT_SPAWNS)[entry]
                        for entry in HOTH_02_BOT_NODES),
    bot_drop_entries=HOTH_02_BOT_NODES,
    hive_spawns=(
        ('SWAMP', 1605, (1229.125, 821.0330200195312, 18.625)),
        ('LOG', 1606, (989.5509643554688, 1238.8679809570312, 52.20500183105469)),
        ('RESEARCH', 1607, (1280.4110107421875, 1046.8800048828125, 22.875)),
        ('GROTTO', 1608, (787.5590209960938, 1054.3260498046875, 50.0)),
        ('FALLS', 1609, (905.2199707031250, 720.125, 10.125)),
        ('SPACEPORT', 1610, (1196.5560302734375, 1230.9979248046875, 20.5))),
    creature_spawns=(('SpawnNode_Guide_EnemyBot x12', 33, None),),
    notes='Level hash is COMPUTED, not shipped: no installed '
          'stringhashlibrary.xml contains levels/hoth_02/commondata/level. '
          'The baked bias has never been observed; measure it once with '
          'tools/inspect_map_baked_bias.py.')

# tutorial_02 / tutorial_03: 2303 / 2400 direct entities. Both share the one
# authored SpawnNode_Player_DropPoint transform. Neither has an authored bot
# cluster within visual range of it, so bot_positions uses the nearest
# authored character/creature spawn nodes and hunter visibility on entry is
# not expected. Their level hashes are also absent from every shipped
# stringhashlibrary.xml.
TUTORIAL_DROP = (501.59399414062500, 516.8480224609375, 41.572000000000000)
TUTORIAL_02 = MapProfile(
    'tutorial_02', level_hash('tutorial_02'), 'computed', 2303,
    57, 61, 67, 59, 2, 'unmeasured',
    TUTORIAL_DROP, 2084, 'SpawnNode_Player_DropPoint',
    bot_positions=(
        (494.1150207519531, 627.7559814453125, 37.5),   # 1 Snurtle AIOverlord
        (508.6740112304688, 631.4739990234375, 37.5),   # 2 Snurtle AIOverlord
        (528.3939819335938, 580.9370117187500, 37.5)),  # 3 Snurtle AIOverlord
    bot_drop_entries=(1, 2, 3),
    creature_spawns=(('SpawnNode_Spitter_AIOverlord x9', 40, None),
                     ('SpawnNode_Snurtle_AIOverlord x3', 1, None)),
    notes='Authored bot nodes are 60-115 m from the drop; presence will not '
          'be visible at spawn.')
TUTORIAL_03 = MapProfile(
    'tutorial_03', level_hash('tutorial_03'), 'computed', 2400,
    57, 60, 66, 58, 2, 'unmeasured',
    TUTORIAL_DROP, 1024, 'SpawnNode_Player_DropPoint',
    bot_positions=(
        (502.5750122070312, 562.0059814453125, 42.109001159667969),  # 1090
        (492.2489929199219, 806.0650024414062, 38.736999511718750),  # 82
        (492.1989746093750, 810.6719970703125, 38.736999511718750)),  # 83
    bot_drop_entries=(1090, 82, 83),
    creature_spawns=(('SpitterSpawnLocation', 1018, (480.578, 703.149, 37.410)),
                     ('TargetDummyRespawner x2', 48, None)),
    notes='1090 is the authored InitialTrainingDummySpawnPoint (45 m from the '
          'drop); 82/83 are the authored RoboDummy transforms (290 m away).')

MAPS = {profile.level.lower(): profile for profile in
        (PRACTICE_ARENA, APRIL2018, HOTH_02, TUTORIAL_02, TUTORIAL_03)}


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
# Every entry of design/gamemodes/gamemodes.gamemodeconfiglist, in its shipped
# array order; the array index IS the ConfigurationIndex the shard puts on the
# wire. The list's own default entry is SOLO_CASUAL.
HOTH_PHASES = (
    ('WaitForPlayers', 0, 0), ('PerkPreview', 3, 240), ('TeamInfo', 4, 10),
    ('PerkSelect', 5, 5), ('DropPreview', 6, 5), ('DropSelect', 7, 5),
    ('GamePlay', 8, None), ('GameOver', 9, 300), ('Shutdown', 10, None))
CASUAL_PHASES = (
    ('WaitForPlayers', 0, 0), ('PerkPreview', 3, 30), ('PerkSelect', 5, 20),
    ('DropPreview', 6, 5), ('DropSelect', 7, 30), ('GamePlay', 8, None),
    ('GameOver', 9, 300), ('Shutdown', 10, None))
DOUBLES_PHASES = (
    ('WaitForPlayers', 0, 0), ('PerkPreview', 3, 240), ('PerkSelect', 5, 20),
    ('DropPreview', 6, 5), ('DropSelect', 7, 30), ('GamePlay', 8, None),
    ('GameOver', 9, 300), ('Shutdown', 10, None))
TEAM_PHASES = (
    ('WaitForPlayers', 0, 0), ('PerkPreview', 3, 240), ('TeamInfo', 4, 10),
    ('PerkSelect', 5, 20), ('DropPreview', 6, 5), ('DropSelect', 7, 20),
    ('GamePlay', 8, None), ('GameOver', 9, 300), ('Shutdown', 10, None))
SHORT_PHASES = (
    ('WaitForPlayers', 0, 1), ('GamePlay', 8, None), ('GameOver', 9, 300),
    ('Shutdown', 10, None))
PRACTICE_PHASES = (
    ('WaitForPlayers', 0, None), ('GamePlay', 8, None), ('GameOver', 9, 1),
    ('Shutdown', 10, None))

MODES = {}


def _mode(alias, *args, **kwargs):
    profile = ModeProfile(*args, **kwargs)
    MODES[alias] = profile
    return profile


MODE_SOLO_CASUAL = _mode(
    'solo-casual', 'SOLO_CASUAL', 0, 'design/gamemodes/singlesunranked.gamemodeconfig',
    8, 0, 0, CASUAL_PHASES, True, False, False, 'Invalid',
    roster_size=4, local_team=1, bot_team=2, authored_maps=('april2018',),
    flags=('Music_SpectatorEnabled', 'FreeForAll', 'UsesPassivePlayPrevention'),
    notes='The config list default. FreeForAll, so team allocation is cosmetic.')

MODE_DOUBLES_CASUAL = _mode(
    'alpha-hunters', 'DOUBLES_CASUAL', 1, 'design/gamemodes/doublescasual.gamemodeconfig',
    16, 0, 0, DOUBLES_PHASES, True, True, False, 'Invalid',
    roster_size=4, local_team=1, bot_team=2, authored_maps=('april2018',),
    flags=('CanFormDynamicAlliancesAfterPartnerDies',
           'TransferOwnershipOfOwnableInteractablesOnPlayerDeath',
           'Music_SpectatorEnabled', 'FreeForAll', 'UsesPassivePlayPrevention',
           'KillsAwardPrestige'),
    notes='Alpha Hunters. Menu gate is mode_alphaHunterDoubles_unlock. '
          'Entry and three visible hunters accepted by the user on april2018.')

MODE_SOLO_BOTS = _mode(
    'solo-bots', 'SOLO_BOTS', 2, 'design/gamemodes/singlesunranked.gamemodeconfig',
    8, 0, 0, CASUAL_PHASES, True, False, False, 'Invalid',
    roster_size=4, local_team=1, bot_team=2, authored_maps=('april2018',),
    flags=('Music_SpectatorEnabled', 'FreeForAll', 'UsesPassivePlayPrevention'),
    notes='List index 2. Shares singlesunranked.gamemodeconfig with '
          'SOLO_CASUAL byte-for-byte, so only the index differs on the wire.')

MODE_CRUCIHEARTS = _mode(
    'crucihearts', 'CRUCIHEARTS', 3, 'design/gamemodes/crucihearts.gamemodeconfig',
    8, 3, 2, TEAM_PHASES, False, True, True, 'Invalid',
    roster_size=4, local_team=1, bot_team=2, authored_maps=('HotH_02',),
    flags=('HiveNoEssence', 'HivesSpawnHearts', 'ScenariosAndEvents'),
    notes='Hive/heart mode; needs the hive spawn nodes only HotH_02 has.')

MODE_CONQUEST_8V8 = _mode(
    'conquest', 'CONQUEST_8V8', 4, 'design/gamemodes/conquest_8v8.gamemodeconfig',
    16, 0, 0, TEAM_PHASES, False, True, True, 'CONQUEST',
    roster_size=4, local_team=1, bot_team=2, authored_maps=('april2018',),
    flags=('HarvesterDifferentialScoring', 'TeamModeEventConfiguration'),
    notes='TargetScore 100 lives in GameModeValues, not MatchScoreData.')

MODE_TUTORIAL = _mode(
    'tutorial', 'TUTORIAL', 5, 'design/gamemodes/tutorial.gamemodeconfig',
    1, 0, 0, SHORT_PHASES, False, False, True, 'Invalid',
    roster_size=4, local_team=1, bot_team=2, authored_maps=('tutorial_02',),
    flags=('RespawnInPlace', 'KeepMedpacksUponRespawn',
           'AutoSkipPhaseToGameplay', 'DoNotSpawnDropPod'),
    notes='MinimumPlayers 1 and AutoSkipPhaseToGameplay, like Practice.')

MODE_PRACTICE = _mode(
    'practice', 'PRACTICE', 6, 'design/gamemodes/practice.gamemodeconfig',
    1, 0, 0, PRACTICE_PHASES, False, False, True, 'Invalid',
    roster_size=1, local_team=1, bot_team=2, authored_maps=('practicearena',),
    flags=('RespawnInPlace', 'KeepMedpacksUponRespawn',
           'AutoSkipPhaseToGameplay', 'DoNotSpawnDropPod',
           'MinimalModeCamps', 'CampIconsAlwaysOn'),
    notes='The accepted baseline. roster_size 1 keeps the one-entry roster.')

MODE_ONE_V_ONE = _mode(
    'one-v-one', '1v1', 7, 'design/gamemodes/1v1.gamemodeconfig',
    3, 10, 9, (('WaitForPlayers', 0, 0), ('PerkPreview', 3, 20),
               ('TeamInfo', 4, 5), ('PerkSelect', 5, 15),
               ('DropPreview', 6, 1), ('DropSelect', 7, 10),
               ('GamePlay', 8, None), ('GameOver', 9, 300),
               ('Shutdown', 10, None)),
    False, True, True, 'Invalid',
    roster_size=2, local_team=1, bot_team=2, authored_maps=('april2018',),
    flags=('Always3Amps', 'NoPlayersLeftCount'),
    notes='Lowest MinimumPlayers of the team modes (3).')

MODE_HEART_OF_THE_HIVES = _mode(
    'heart-of-the-hives', 'HEART_OF_THE_HIVES', 8,
    'design/gamemodes/heart_of_the_hives.gamemodeconfig',
    8, 3, 2, HOTH_PHASES, False, True, True, 'HEART_OF_THE_HIVES',
    roster_size=4, local_team=1, bot_team=2, authored_maps=('HotH_02',),
    flags=('HiveNoEssence', 'SequentialHives', 'HivesSpawnHearts',
           'ForceCrossMapDropSpawns', 'RespawnPointShields',
           'ScenariosAndEvents', 'DistributedEssenceEruption'),
    notes='MinimumPlayers 8. The client reached PerkPreview with this index '
          'on april2018, but april2018 has no hives.')

MODE_HOTH_GUIDE = _mode(
    'hoth-guide', 'HOTH_GUIDE', 9, 'design/gamemodes/hoth_guide.gamemodeconfig',
    1, 3, 2, (('WaitForPlayers', 0, 0), ('PerkPreview', 3, 1),
              ('TeamInfo', 4, 5), ('PerkSelect', 5, 20),
              ('DropPreview', 6, 12), ('DropSelect', 7, 8),
              ('GamePlay', 8, None), ('GameOver', 9, 300),
              ('Shutdown', 10, None)),
    False, True, True, 'HEART_OF_THE_HIVES',
    roster_size=4, local_team=1, bot_team=2, authored_maps=('HotH_02',),
    flags=('GuideHeartOfTheHives', 'HiveNoEssence', 'SequentialHives',
           'HivesSpawnHearts', 'ForceSplitEssence', 'RespawnPointShields'),
    notes='The only Heart of the Hives configuration with MinimumPlayers 1. '
          'UIModeOverride is HEART_OF_THE_HIVES, its win condition is '
          'cdl/gamemodes/guide_hoth/hoth_guide_win_condition.cdl and it is '
          'the configuration the HotH_02 SpawnNode_Guide_EnemyBot nodes and '
          'the cdl/gamemodes/guide_hoth scripts are authored for. This is '
          'the best shipped candidate for solo Heart of the Hives.')

MODE_HOTH_MINIMAL = _mode(
    'hoth-minimal', 'HOTH_MINIMAL', 10, 'design/gamemodes/hoth_minimal.gamemodeconfig',
    8, 3, 2, HOTH_PHASES, False, True, True, 'HEART_OF_THE_HIVES',
    roster_size=4, local_team=1, bot_team=2, authored_maps=('HotH_02',),
    flags=('NoHarvesters', 'RespawnCampsBasedOnHive', 'SequentialHives',
           'HivesSpawnHearts', 'MinimalModeCamps', 'CreaturesScanWhenAwoken'),
    notes='NoHarvesters variant of HotH; still MinimumPlayers 8.')

MODE_TUTORIAL2 = _mode(
    'tutorial2', 'TUTORIAL2', 11, 'design/gamemodes/tutorial2.gamemodeconfig',
    1, 0, 0, SHORT_PHASES, False, False, True, 'Invalid',
    roster_size=4, local_team=1, bot_team=2, authored_maps=('tutorial_03',),
    flags=('RespawnInPlace', 'KeepMedpacksUponRespawn',
           'AutoSkipPhaseToGameplay', 'DoNotSpawnDropPod'),
    notes='MinimumPlayers 1; own levelup table design/gamemodes/'
          'leveluptables/tutorial2.levelupdata.')


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
# Named selections. The first four are the values that already existed and
# they must keep producing byte-identical bootstraps.
SELECTIONS = {
    'practice': (PRACTICE_ARENA, MODE_PRACTICE),
    'heart-of-the-hives': (APRIL2018, MODE_HEART_OF_THE_HIVES),
    'alpha-hunters': (APRIL2018, MODE_DOUBLES_CASUAL),
    'solo-casual': (APRIL2018, MODE_SOLO_CASUAL),
    # New: every remaining shipped mode on the map it is authored for.
    'solo-bots': (APRIL2018, MODE_SOLO_BOTS),
    'one-v-one': (APRIL2018, MODE_ONE_V_ONE),
    'conquest': (APRIL2018, MODE_CONQUEST_8V8),
    'hoth-guide': (HOTH_02, MODE_HOTH_GUIDE),
    'hoth-hives': (HOTH_02, MODE_HEART_OF_THE_HIVES),
    'hoth-minimal': (HOTH_02, MODE_HOTH_MINIMAL),
    'crucihearts': (HOTH_02, MODE_CRUCIHEARTS),
    'tutorial': (TUTORIAL_02, MODE_TUTORIAL),
    'tutorial2': (TUTORIAL_03, MODE_TUTORIAL2),
    # Hash-safe fallbacks: HotH_02's level hash is computed, not shipped, so
    # these run the same configuration on april2018, whose hash is shipped.
    'hoth-guide-april2018': (APRIL2018, MODE_HOTH_GUIDE),
    'hoth-minimal-april2018': (APRIL2018, MODE_HOTH_MINIMAL),
}


# Backwards-compatible composed constants. `shard_stub.py` compares the
# selected profile against PRACTICE, and navigation/hunter_patrol read HIVES'
# authored positions, so these names and values must not move.
PRACTICE = compose(PRACTICE_ARENA, MODE_PRACTICE)
HIVES = compose(APRIL2018, MODE_HEART_OF_THE_HIVES)
ALPHA_HUNTERS = compose(APRIL2018, MODE_DOUBLES_CASUAL)
SOLO_CASUAL = compose(APRIL2018, MODE_SOLO_CASUAL)


def profile(level, mode):
    """Compose any installed map with any shipped mode."""
    map_profile = MAPS.get(level.lower())
    if map_profile is None:
        raise ValueError('unknown level: %s (have %s)'
                         % (level, ', '.join(sorted(MAPS))))
    mode_profile = MODES.get(mode)
    if mode_profile is None:
        mode_profile = next((m for m in MODES.values() if m.name == mode), None)
    if mode_profile is None:
        raise ValueError('unknown mode: %s (have %s)'
                         % (mode, ', '.join(sorted(MODES))))
    return compose(map_profile, mode_profile)


def selected():
    """Resolve CRUCIBLE_SOLO_MODE.

    Accepts a named selection ('practice', 'alpha-hunters', 'hoth-guide', ...)
    or an explicit '<level>:<mode>' pair so that every installed map can be
    paired with every shipped mode without a code change.

    CRUCIBLE_BAKED_BIAS overrides the map's baked-network-ID bias; the bias is
    only live-confirmed on practicearena (2) and april2018 (787), and
    tools/inspect_map_baked_bias.py measures it in one launch.
    CRUCIBLE_LEVEL_HASH overrides the level-name hash, for maps whose hash is
    computed rather than shipped.
    """
    name = os.environ.get('CRUCIBLE_SOLO_MODE', 'practice')
    if ':' in name:
        level, _, mode = name.partition(':')
        result = profile(level, mode)
    else:
        pair = SELECTIONS.get(name)
        if pair is None:
            raise ValueError('unsupported CRUCIBLE_SOLO_MODE: ' + name)
        result = compose(*pair)
    bias = os.environ.get('CRUCIBLE_BAKED_BIAS')
    if bias:
        result = SoloMode(**{**vars(result), 'baked_counter_bias': int(bias)})
    override = os.environ.get('CRUCIBLE_LEVEL_HASH')
    if override:
        result = SoloMode(**{**vars(result), 'level_hash': int(override)})
    return result
