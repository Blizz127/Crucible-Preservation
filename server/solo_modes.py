"""Solo profile records for caller-supplied maps and modes."""
from dataclasses import dataclass


@dataclass(frozen=True)
class MapProfile:
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
    hive_spawns: tuple = ()
    creature_spawns: tuple = ()
    notes: str = ''


@dataclass(frozen=True)
class ModeProfile:
    name: str
    configuration: int
    config_asset: str
    minimum_players: int
    target_score: int
    match_point_threshold: int
    phase_sequence: tuple
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


@dataclass(frozen=True)
class SoloMode:
    level: str
    level_hash: int
    configuration: int
    mode_entry: int
    phase_entry: int
    store_entry: int
    player_position: tuple
    bot_positions: tuple = ()
    baked_counter_bias: int | None = None
    map_profile: MapProfile = None
    mode_profile: ModeProfile = None
    solo_phase: int | None = None


def compose(map_profile, mode_profile):
    bots = map_profile.bot_positions[:max(mode_profile.roster_size - 1, 0)]
    return SoloMode(
        map_profile.level, map_profile.level_hash, mode_profile.configuration,
        map_profile.mode_entry, map_profile.phase_entry, map_profile.store_entry,
        map_profile.player_position, bots, map_profile.baked_counter_bias,
        map_profile=map_profile, mode_profile=mode_profile)
