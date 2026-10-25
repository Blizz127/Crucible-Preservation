"""Playable hunter roster, hero selection and authored ability loadouts.

Every number here is read out of the installed game or out of
`bin/Crucible.exe`; nothing is guessed. Evidence is cited inline and in
`docs/24-hero-and-abilities.md`. This module holds metadata only -- it
copies no asset bytes and it never writes to the Steam install.

Three separate identities have to agree before the client shows the right
hunter, and they come from three different places:

  * the **character slice** the shard spawns
        `slices/player/<hero>/<hero>_character`, keyed by the level's
        `stringhashlibrary.xml` name hash.
  * the **CharacterClass** in the replicated `DataStoreMatch` PlayerData
        a native enum; its name table is at .rdata RVA 0x6c1de08
        (accessor 0x432a380). Index 8 = "Marine" agrees with the value
        the accepted Practice bootstrap already sends.
  * the **LoadoutSetup.Character** value on the connection entity
        the same enum, written as a big-endian uint16 (the accepted
        bootstrap sends `00 08`).

The HUD's ability slots are NOT server data: `ui/dist/abilities.js` is a
static per-hero ability->slot table compiled into the client, keyed by the
lower-cased hero name. `ui/dist/hud.js` derives that key from the local
player's replicated CharacterClass. `ABILITY_SLOTS` below is a verbatim
transcription of that shipped table, so the server can tell exactly which
slots a given selection will light up.
"""
from dataclasses import dataclass
import os


# ---------------------------------------------------------------------------
# CharacterClass
# ---------------------------------------------------------------------------
# Retail enum-name pointer table, .rdata RVA 0x6c1de08, 38 entries, read by
# the accessor at 0x432a380 (`lea rax, [rip+0x28f3a81]; ret`). Only the
# entries this project can spawn are listed; the table continues with
# Juggernaut 14 .. MapBoundsMarker 37.
CHARACTER_CLASS = {
    'Unknown': 0,
    'None': 1,
    'robotanist': 2,
    'commando': 3,
    'duelist': 4,
    'fighter': 5,
    'hero': 6,
    'inventor': 7,
    'marine': 8,
    'minigunner': 9,
    'pyro': 10,
    'rocketeer': 11,
    'saboteur': 12,
    'technomancer': 13,
    'Spectator': 22,
}


# ---------------------------------------------------------------------------
# Replicated component layout of a hunter character entity
# ---------------------------------------------------------------------------
# The 33 shared roots and 25 autonomous roots below were established live for
# Marine (docs/16, 19:18 GMT capture) and are reproduced here so the shard can
# spawn any hunter with the same record. They are valid for every hunter in
# `ROSTER` because the only component-set differences between the installed
# `*_character.dynamicslice` assets are network-irrelevant:
#
#   duelist / fighter / saboteur add {ac9b8fa0-a6da-4377-8219-25ba7e4a22e8}
#       = "cloth" (exe string pair at raw offset 0x67a9009). No component of
#       that name exists in the client's 180-entry network descriptor table
#       (snapshots/gamemode-20260919/component-replication-schema.json), so it
#       has no network type and contributes no record bit.
#   hero adds {2aa86eeb-4b0c-9b17-15d1-f41791b3f79c}
#       = AudioJointComponent::Client. It IS network type 16, but its four
#       expanded direction counts are [0, 0, 0, 0], so it contributes no bit
#       in either direction.
#
# `rocketeer` is deliberately excluded: its slice drops
# {4f80779f-9085-fc7a-d978-bf32338e0c35} = DamageFloaterComponent::Client,
# network type 36 with 36 autonomous bits, so its autonomous record would have
# 24 roots, not 25. That label is suspect: the live Marine capture puts
# CrucibleLOD (no record bits) at 4f80779f (docs/24). It is also absent from
# the shipped character roster, which alone excludes it.
# `technomancer` is excluded because no shipped level hash library names its
# character slice.
SHARED_TYPES = (0, 10, 13, 18, 21, 24, 27, 44, 46, 48, 69, 79, 80,
                98, 99, 101, 102, 107, 110, 115, 120, 125, 128, 132,
                133, 143, 145, 146, 156, 158, 163, 165, 166)
AUTONOMOUS_ROOTS = 25


# ---------------------------------------------------------------------------
# HUD ability slots
# ---------------------------------------------------------------------------
# Verbatim from the shipped `ui/dist/abilities.js` (gamedata.pak). The HUD
# renders one slot per entry; `ui/dist/hud.js` builds each icon as
# `url(shared/img/<hero>/abilities/<ability id>.svg)`. An entry whose `id` is
# a tuple is a slot the client cycles between.
ABILITY_SLOTS = {
    'commando': (('commando_primary', 'PrimaryFire'),
                 ('commando_dash', 'Ability3'),
                 ('commando_inertiagunswap', 'Ability1'),
                 ('commando_shotgunswap', 'Ability2'),
                 ('commando_knifeswap', 'Ability4')),
    'duelist': (('duelist_pistol_fire', 'PrimaryFire'),
                ('duelist_weapon_swap', 'Ability3'),
                ('duelist_bubble', 'Ability1'),
                ('duelist_deflect', 'Ability2'),
                (('duelist_disarming_strike', 'duelist_ads'), 'Ability4')),
    'fighter': (('fighter_primary', 'PrimaryFire'),
                ('fighter_rush', 'Ability3'),
                ('fighter_roar', 'Ability1'),
                ('fighter_resonate', 'Ability2'),
                ('fighter_quiver_strike', 'Ability4')),
    'hero': (('hero_primary', 'PrimaryFire'),
             ('hero_send_bot', 'Ability3'),
             ('hero_shield_self', 'Ability1'),
             ('hero_send_shield', 'Ability2'),
             ('hero_force_punch', 'Ability4')),
    'inventor': (('inventor_primary', 'PrimaryFire'),
                 ('inventor_blink', 'Ability3'),
                 ('inventor_smokescreen', 'Ability1'),
                 ('inventor_passive', 'Ability2'),
                 ('inventor_grenade', 'Ability4')),
    'marine': (('marine_primary', 'PrimaryFire'),
               ('marine_sprint', 'Ability3'),
               ('marine_bunker', 'Ability1'),
               ('marine_grenade', 'Ability2'),
               ('marine_ads', 'Ability4')),
    'minigunner': (('minigunner_primary', 'PrimaryFire'),
                   ('minigunner_afterburner', 'Ability3'),
                   ('minigunner_drink', 'Ability1'),
                   ('minigunner_airhorn', 'Ability2'),
                   ('minigunner_upshift', 'Ability4')),
    'pyro': (('pyro_primary', 'PrimaryFire'),
             ('pyro_jumpjets', 'Ability3'),
             ('pyro_eruption', 'Ability1'),
             ('pyro_air_blast', 'Ability2'),
             ('pyro_fireball', 'Ability4')),
    'robotanist': (('robotanist_primary', 'PrimaryFire'),
                   ('robotanist_thrusters', 'Ability3'),
                   ('robotanist_shield_burst', 'Ability1'),
                   ('robotanist_crop_dust', 'Ability2'),
                   ('robotanist_seed_pod', 'Ability4')),
    'saboteur': (('saboteur_primary', 'PrimaryFire'),
                 ('saboteur_grapple', 'Ability3'),
                 ('saboteur_jammer', 'Ability1'),
                 ('saboteur_mine', 'Ability2'),
                 ('saboteur_ads', 'Ability4')),
}


@dataclass(frozen=True)
class Hero:
    """One playable entry of `assets/gamesystems/characterrosters/
    commoncharacterroster.roster` that this project can actually spawn."""
    name: str                 # lower-cased key: slice folder and HUD key
    display: str              # the roster's authored CharacterClass name
    roster_index: int         # position in the shipped roster asset
    character_class: int      # native CharacterClass enum value
    slice_hash: int           # stringhashlibrary key of the character slice
    slice_entry: int          # entry index inside that slice
    abilities: tuple          # authored `.ability` assets in loadout order
    loadout_asset: str
    perk_asset: str
    skin_asset: str

    @property
    def slice_path(self):
        return 'slices/player/%s/%s_character' % (self.name, self.name)


def _hero(name, display, roster_index, slice_hash, abilities,
          loadout_asset=None, perk_asset=None, skin_asset=None):
    return Hero(
        name=name, display=display, roster_index=roster_index,
        character_class=CHARACTER_CLASS[name],
        slice_hash=slice_hash, slice_entry=0,
        abilities=tuple(abilities),
        loadout_asset=loadout_asset or
        'assets/player/%s/%s_abilities.loadout' % (name, name),
        perk_asset=perk_asset or 'assets/player/%s/%s_perks.perkdata' % (name, name),
        skin_asset=skin_asset or
        'assets/player/%s/%s_skins.characterskinlist' % (name, name))


# The slice hashes are the `Key=` values of
# `levels/<level>/commondata/stringhashlibrary.xml`; all five shipped levels
# (practicearena, april2018, HotH_02, tutorial_02, tutorial_03, mainmenu)
# publish the identical eleven hunter entries, so the hash does not depend on
# the selected map. `roster_index` is the order of the entries in the shipped
# `commoncharacterroster.roster`; it is NOT the CharacterClass value.
# `abilities` is the ordered asset list inside each `*_abilities.loadout`.
ROSTER = {h.name: h for h in (
    _hero('marine', 'Marine', 0, 9256072, (
        'marine_sprint', 'marine_grenade', 'marine_primary', 'marine_reload',
        'marine_aimdownsights', '_common/usemedpack', 'marine_quickmelee',
        'character/interact', 'marine_bunker', 'marine_bunker_place',
        '_common/common_emote', '_common/common_emoji', '_common/common_voiceline')),
    _hero('pyro', 'Pyro', 1, 1913935216, (
        'pyro_flamethrower', 'pyro_eruption', 'pyro_air_blast', 'pyro_jumpjets',
        'pyro_fireball', '_common/usemedpack', 'character/interact',
        'pyro_quickmelee', 'pyro_vent_heat', 'pyro_overheat_melee',
        'pyro_ability_overheat', 'pyro_jumpjet_pounce',
        '_common/common_emote', '_common/common_emoji', '_common/common_voiceline')),
    _hero('minigunner', 'Minigunner', 2, 4288064808, (
        'minigunner_ability_primary', 'minigunner_ability_reload',
        'minigunner_ability_afterburner', 'minigunner_ability_drink',
        'minigunner_ability_airhorn', 'minigunner_ability_upshift',
        '_common/usemedpack', 'character/interact', 'minigunner_quickmelee',
        '_common/common_emote', '_common/common_voiceline', '_common/common_emoji')),
    _hero('inventor', 'Inventor', 3, 3739945788, (
        'inventor_primaryfire', 'inventor_reload', 'inventor_blink_test',
        'inventor_grenade', '_common/usemedpack', 'character/interact',
        'inventor_smokescreen', 'inventor_quickmelee', 'inventor_passive',
        '_common/common_emote', '_common/common_emoji', '_common/common_voiceline')),
    # The shipped duelist loadout file name really is misspelled.
    _hero('duelist', 'Duelist', 4, 1641511682, (
        '_common/usemedpack', 'duelist_quickmelee', 'character/interact',
        'abilities/duelist_primary_fire', 'abilities/duelist_ads',
        'abilities/duelist_swap', 'abilities/duelist_primary_swing',
        'abilities/duelist_disarming_strike', 'abilities/duelist_bubble',
        'abilities/duelist_deflect', '_common/common_emoji',
        '_common/common_emote', '_common/common_voiceline'),
          loadout_asset='assets/player/duelist/deulist_abilities.loadout'),
    _hero('robotanist', 'Robotanist', 5, 1443315109, (
        '_common/usemedpack', 'robotanist_spray', 'character/interact',
        'robotanist_thrusters', 'robotanist_seed_pod', 'robotanist_crop_dust',
        'robotanist_shield_burst', 'robotanist_reload', 'robotanist_quickmelee',
        '_common/common_emote', '_common/common_voiceline', '_common/common_emoji')),
    _hero('fighter', 'Fighter', 6, 2220298799, (
        'fighter_rush', 'fighter_resonate', 'fighter_resonate_primary',
        'fighter_resonate_secondary', 'fighter_roar', 'fighter_roar_primary',
        'fighter_roar_secondary', '_common/usemedpack', 'character/interact',
        'fighter_quickmelee', 'fighter_air_dash', 'fighter_primary_attack',
        'fighter_quiver_strike', '_common/common_emote', '_common/common_emoji',
        '_common/common_voiceline')),
    _hero('hero', 'Hero', 7, 501823405, (
        'hero_primary_fire', '_common/usemedpack', 'character/interact',
        'hero_quickmelee', 'hero_force_punch', 'hero_send_bot',
        'hero_bot_teleport', 'hero_shield_self', 'hero_send_shield',
        'hero_reload', 'hero_super_jump', '_common/common_voiceline',
        '_common/common_emoji', '_common/stance_crouch')),
    _hero('saboteur', 'Saboteur', 8, 2391706089, (
        'saboteur_primary_fire', 'saboteur_reload', '_common/usemedpack',
        'character/interact', 'saboteur_quickmelee', 'saboteur_aimdownsights',
        'saboteur_jammer', 'saboteur_mine', 'saboteur_grapple',
        '_common/common_emote', '_common/common_emoji', '_common/common_voiceline')),
    _hero('commando', 'Commando', 9, 3623436538, (
        'commando_inertiagunswap', 'commando_shotgunswap', 'commando_knifeswap',
        '_common/usemedpack', 'character/interact', 'commando_quickmelee',
        'comando_dash', 'commando_shotgun_fire', 'commando_pulse_fire',
        'commando_knife_fire', 'commando_bandage', '_common/common_emote',
        '_common/common_voiceline', '_common/common_emoji')),
)}

DEFAULT_HERO = 'marine'
ENV = 'CRUCIBLE_SOLO_HERO'


def names():
    """Selectable hero keys, in shipped roster order."""
    return tuple(sorted(ROSTER, key=lambda n: ROSTER[n].roster_index))


def get(name):
    key = (name or '').strip().lower()
    if key not in ROSTER:
        raise ValueError('unknown hero %r; choose one of %s'
                         % (name, ', '.join(names())))
    return ROSTER[key]


def selected():
    """The hero this shard spawns. Defaults to the accepted Marine baseline.

    An unset or empty `CRUCIBLE_SOLO_HERO` keeps every wire byte identical to
    the visually accepted Practice / Alpha Hunters profiles.
    """
    return get(os.environ.get(ENV) or DEFAULT_HERO)


def character_class_bytes(hero=None):
    """PlayerData.CharacterClass -- a full-range big-endian uint16 enum.

    Matches the accepted `00 08` the roster probe already sends for Marine.
    """
    hero = hero or selected()
    return hero.character_class.to_bytes(2, 'big')


def loadout_character_bytes(hero=None):
    """LoadoutSetup.Character on the connection entity, same enum and width."""
    return character_class_bytes(hero)


def hud_ability_slots(hero=None):
    """The slots `ui/dist/abilities.js` will render for this hero."""
    hero = hero or selected()
    return ABILITY_SLOTS[hero.name]


# ---------------------------------------------------------------------------
# CrucibleLivePlayerDataService payloads
# ---------------------------------------------------------------------------
# The client asks for these three routes and the local backend answers none of
# them (no "CrucibleLivePlayerDataService" entry in crucible_backend.ROUTERS),
# so every request 404s -- see the unanswered SVCSREQ 17/19..29/31/32 in
# snapshots/gamemode-20260919/NovaGameClient-20260919T173321Z.log.
#
# Shapes are taken from the client's own consumers in gamedata.pak:
#   ui/dist/menu.js   `t.loadout.character.toLowerCase()`
#   ui/dist/menu.js   `const {loadouts: n = []} = await gn(e), [o = !1] = n;`
#                     reading o.skin / o.dropPodSkin / o.dropPodDecals /
#                     o.wheel1 / o.wheel2 / o.wheel3
#   ui/dist/perks.js  `.reduce((e, {perkSet: a}) => { const {character, perks} = a; ...`
#
# These are PROPOSED payloads. They have never been served to the client.
def playerdata_activeloadout(hero=None):
    hero = hero or selected()
    return {'loadout': {'character': hero.display, 'slot': 0}}


def playerdata_loadouts(character, hero=None):
    """Cosmetic loadout for one character; every field has a client default."""
    del hero
    name = (character or '').strip()
    return {'loadouts': [{
        'character': name,
        'slot': 0,
        'skin': '%s_t1_default_01' % name.lower(),
        'dropPodSkin': 'droppod_default_01_var01',
        'dropPodDecals': [],
        'wheel1': [],
        'wheel2': [],
        'wheel3': [],
    }]}


def playerdata_perkset(character):
    """Empty authored perk selection; the client falls back to tier defaults.

    `ui/dist/perks.js` fills any tier with no selected perk by marking its
    first entry selected, so an empty list is a valid 'nothing chosen yet'.
    """
    return {'perkSet': {'character': (character or '').strip(), 'perks': []}}
