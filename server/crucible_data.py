"""Seed data for the local Crucible revival backend.

All challenge IDs are REAL retail IDs harvested from the client's own
locale bundle (ui/dist/ui.loc2.js), so names/descriptions render exactly
as retail. Goal counts (Count) are server-supplied and were never shipped
in the client; values below are sensible retail-plausible picks.
"""
import copy

SEASON = {
    "StartDate": "2026-08-03T00:00:00.000Z",
    "EndDate": "2027-02-01T00:00:00.000Z",
    "Currency": "currency_battlepassxp",
    "Entitlement": "battlepass_s1",
    "SeasonId": "crucible-season1",
    "Name": "Season 1",
}

SEASON_ID = SEASON["SeasonId"]

# Local-profile seeding (documented approximation: a fresh retail account
# would start at 0; we seed modest progress so screens show life).
SEED_STARS = 23          # currency_battlepassxp amount -> battlepass tier 3
SEED_REROLLS = 3
SEED_CREDITS = 2500
SEED_KEYS = 3            # currency_battlepasskeys (challenge reroll keys)


def prog(challenge_id):
    return "prog_" + challenge_id


def daily(challenge_id, count, stars=1):
    return {
        "ChallengeId": challenge_id,
        "ProgressionId": prog(challenge_id),
        "Count": count,
        "Permanence": "Daily",
        "Visibility": "Visible",
        "RelativeStartToSeason": 0,
        "StarAmount": stars,
    }


def weekly(challenge_id, week_idx, count, stars=2):
    return {
        "ChallengeId": challenge_id,
        "ProgressionId": prog(challenge_id),
        "Count": count,
        "Permanence": "Weekly",
        "Visibility": "Visible",
        "RelativeStartToSeason": week_idx,
        "StarAmount": stars,
    }


def seasonal(challenge_id, count, stars=4):
    return {
        "ChallengeId": challenge_id,
        "ProgressionId": prog(challenge_id),
        "Count": count,
        "Permanence": "Seasonal",
        "Visibility": "Visible",
        "RelativeStartToSeason": 0,
        "StarAmount": stars,
    }


# Today's daily set: 3 retail dailies + goal counts.
DAILIES = [
    daily("daily_play_matches", 2),
    daily("daily_deal_damage_to_players", 2500),
    daily("daily_earn_essence", 1500),
]

# Spare dailies the reroll endpoint can swap in.
DAILY_POOL = [
    daily("daily_defeat_players", 6),
    daily("daily_gain_levels", 8),
    daily("daily_win_matches", 1),
    daily("daily_capture_harvesters", 3),
    daily("daily_kill_hives", 2),
    daily("daily_use_medkit", 3),
    daily("daily_heal_health", 1500),
    daily("daily_farm_anything", 25),
]

# Weekly challenges, weeks 1..20 (retail s1_w<N>_* set). Three per week;
# RelativeStartToSeason is the 0-based week index the client sorts on.
WEEKLIES = []
_WEEKLY_KINDS = [
    ("collect_essence", 3000),
    ("gain_levels", 12),
    ("win_games", 3),
]
for _w in range(1, 21):
    for _kind, _count in _WEEKLY_KINDS:
        WEEKLIES.append(weekly("s1_w%d_%s" % (_w, _kind), _w - 1, _count))
# A few combat-flavored weeklies mixed into early weeks (retail IDs).
WEEKLIES[1] = weekly("s1_w1_deal_damage", 0, 5000)
WEEKLIES[4] = weekly("s1_w2_defeat_enemy_hunters", 1, 10)
WEEKLIES[7] = weekly("s1_w3_gain_essence", 2, 4000)
WEEKLIES[10] = weekly("s1_w4_deal_damage", 3, 7500)

SEASONALS = [
    seasonal("s1_capture_amplifiers", 15),
    seasonal("s1_capture_harvesters_or_amplifiers", 40),
    seasonal("s1_kill_megadrones", 10),
    seasonal("s1_kill_stompers_spitters_megadrones", 30),
    seasonal("s1_deal_damage_to_hives", 10000),
    seasonal("s1_do_healing", 8000),
    seasonal("s1_proc_any_plant", 25),
    seasonal("s1_reach_level_6", 1, stars=3),
    seasonal("s1_gain_levels_after_5", 20),
    seasonal("s1_seasonal_win_games", 10, stars=5),
    seasonal("s1_seasonal_essence", 25000, stars=5),
    seasonal("s1_win_with_three_kills", 3, stars=5),
]

# Seasonal challenge -> account-icon reward (real icon asset IDs from pak).
SEASONAL_ICONS = {
    "s1_capture_amplifiers": "icon_0014_world_flagbannersilhouette",
    "s1_capture_harvesters_or_amplifiers": "icon_0019_world_hivesilhouette",
    "s1_kill_megadrones": "icon_0012_world_enemykilllogo",
    "s1_kill_stompers_spitters_megadrones": "icon_0021_world_meleepunchlogo",
    "s1_deal_damage_to_hives": "icon_0019_world_hivesilhouette",
    "s1_do_healing": "icon_0016_world_healthpack",
    "s1_proc_any_plant": "icon_0005_world_cosmicstorm",
    "s1_reach_level_6": "icon_0038_world_unitysymbol",
    "s1_gain_levels_after_5": "icon_0039_world_unitysymbolalternate",
    "s1_seasonal_win_games": "icon_0000_world_cruciblelogo",
    "s1_seasonal_essence": "icon_0011_world_edgeofplanet",
    "s1_win_with_three_kills": "icon_0015_world_headshotlogo",
}

# --- Gamemode unlocks -------------------------------------------------------
# The mode-select screen (ui/dist/perks.js) builds its list from
# /sysmsgs/gamemodes.json and gates every entry on an entitlement in the
# "Gamemode" category. The derived store is, verbatim:
#
#   G = derived([sysmsgs, gamemodeStore], ([e, a]) =>
#         !e || !a.size ? [] :
#         p.TUTORIAL.entitlementId.find(id => a.has(id) && a.get(id).amount)
#           ? Object.entries(e.modes).reduce(...)   // -> mode buttons
#           : [])
#
# So TWO things are required or the list renders empty and the practice
# button never appears at all:
#   1. /CrucibleLiveSystemMessages/gamemodes.json must return
#      {"modes": {<GAMETYPE>: <bool>}} keyed exactly like the catalog in
#      ui/dist/preloader.js. A payload without a "modes" key makes
#      Object.entries(undefined) throw inside the store.
#   2. the entitlement catalog (/gameConfigData/entitlements) must define the
#      mode_*_unlock ids under category "Gamemode", and /entitlements/list
#      must grant them amount > 0. The client forces catalog amounts to 0 and
#      re-fills them from the quantities list, so BOTH sides are needed.
#
# Master gate: TUTORIAL's entitlementId is ["mode_initial_unlock",
# "mode_tutorial_unlock"] -- owning either one is what makes the mode list
# appear at all. PRACTICE is then unlocked by either "mode_standard_unlock"
# or "mode_practiceArena_unlock". `enabled` is used as a real boolean by the
# mode card (`disabled: !enabled`), so the values below are booleans.
GAMEMODES = {
    "DOUBLES_CASUAL": True,
    "CRUCIHEARTS_CASUAL": True,
    "CONQUEST_CASUAL": True,
    "PRACTICE": True,
    "TUTORIAL": True,
    "HOTH_GUIDE": True,
    "CUSTOM_CRUCIHEARTS": True,
    "SOLO_BOTS": True,
    "SOLO_CASUAL": True,
}

MODE_UNLOCK_CATEGORY = "Gamemode"

# Every mode_*_unlock id referenced by the client's own gametype catalog.
MODE_UNLOCKS = [
    "mode_initial_unlock",            # master gate (TUTORIAL / HOTH_GUIDE)
    "mode_tutorial_unlock",
    "mode_standard_unlock",           # all listed casual modes require this
    "mode_practiceArena_unlock",      # PRACTICE
    "mode_alphaHunterDoubles_unlock",  # DOUBLES_CASUAL
    "mode_heartOfTheHives_unlock",    # CRUCIHEARTS_CASUAL
    "mode_harvesterCommand_unlock",   # CONQUEST_CASUAL
]


def gamemodes_payload():
    """Body for /CrucibleLiveSystemMessages/gamemodes.json."""
    return {"modes": dict(GAMEMODES)}


def entitlement_catalog():
    """Body for gameconfig /gameConfigData/entitlements (entitlement dict).

    Defines which entitlements exist and their category; the client zeroes
    every amount here and re-fills them from /entitlements/list. Only the
    "Gamemode" category is needed for the mode list to populate -- other
    categories are created on demand by the UI, so this stays additive.
    """
    return [{"entitlementId": eid, "category": MODE_UNLOCK_CATEGORY,
             "amount": 0, "maxAmount": 1}
            for eid in MODE_UNLOCKS]


# Seeded per-challenge progress (runningTotal vs Count goal).
SEED_PROGRESS = {
    prog("daily_play_matches"): 1,
    prog("daily_deal_damage_to_players"): 1130,
    prog("daily_earn_essence"): 640,
    prog("s1_w1_deal_damage"): 2350,
    prog("s1_capture_amplifiers"): 4,
    prog("s1_proc_any_plant"): 9,
}


def all_challenge_defs():
    return copy.deepcopy(DAILIES + DAILY_POOL + WEEKLIES + SEASONALS)


def daily_stars_table():
    return {d["ChallengeId"]: copy.deepcopy(d) for d in DAILIES + DAILY_POOL}


def season_stars_table():
    return {SEASON_ID: copy.deepcopy(WEEKLIES + SEASONALS)}


def entitlement_to_achievement():
    """ui-entitlement-to-achievement: challengeId -> reward linkage."""
    table = {}
    for d in SEASONALS:
        cid = d["ChallengeId"]
        table[cid] = {
            "ChallengeId": cid,
            "ProgressionId": d["ProgressionId"],
            "Count": d["Count"],
            "EntitlementId": SEASONAL_ICONS[cid],
        }
    return table


# Battlepass tiers. ChallengeId MUST contain uppercase "FREE" for free-track
# classification (client checks ChallengeId.includes("FREE")). Count is in
# star units, 10 per tier (client divisor L=10). Reward entitlementIds are
# real asset IDs harvested from the pak so icons resolve.
_FREE_REWARDS = [
    ("accounticon", "icon_0000_world_cruciblelogo"),
    ("currency", "currency_battlepasskey"),
    ("accounticon", "icon_0005_world_cosmicstorm"),
    ("emoji", "commando_emoji_001_laugh"),
    ("currency", "currency_credits"),
    ("accounticon", "icon_0012_world_enemykilllogo"),
    ("decal", "poddecal_001_commando1"),
    ("currency", "currency_battlepasskey"),
    ("accounticon", "icon_0016_world_healthpack"),
    ("emoji", "duelist_emoji_001_laugh"),
    ("pod", "droppod_generic_01_var01"),
    ("currency", "currency_credits"),
    ("accounticon", "icon_0038_world_unitysymbol"),
    ("decal", "poddecal_005_duelist1"),
    ("currency", "currency_battlepasskey"),
]
_PAID_REWARDS = [
    ("skin", "commando_t1_default_02"),
    ("currency", "currency_battlepasskey"),
    ("skin", "duelist_t1_default_02"),
    ("emoji", "commando_emoji_004_joy"),
    ("skin", "fighter_t1_default_02"),
    ("currency", "currency_credits"),
    ("skin", "hero_t1_default_02"),
    ("pod", "droppod_duelist_01_var01"),
    ("skin", "inventor_t1_default_02"),
    ("currency", "currency_battlepasskey"),
    ("skin", "marine_t1_default_02"),
    ("emoji", "marine_emoji_001_laugh"),
    ("skin", "minigunner_t1_default_02"),
    ("currency", "currency_credits"),
    ("skin", "pyro_t1_default_02"),
]
N_TIERS = 30


def battlepass_tiers():
    tiers = []
    for i in range(N_TIERS):
        count = i * 10
        fcat, fid = _FREE_REWARDS[i % len(_FREE_REWARDS)]
        tiers.append({
            "ChallengeId": "BATTLEPASS_S1_FREE_TIER_%02d" % (i + 1),
            "Count": count,
            "Entitlements": [{"entitlementId": fid, "category": fcat}],
        })
        pcat, pid = _PAID_REWARDS[i % len(_PAID_REWARDS)]
        tiers.append({
            "ChallengeId": "BATTLEPASS_S1_PAID_TIER_%02d" % (i + 1),
            "Count": count,
            "Entitlements": [{"entitlementId": pid, "category": pcat}],
        })
    return tiers


def battlepass_tier_entitlements():
    tiers = battlepass_tiers()
    return {
        "tiers": tiers,
        "entitlementIds": [e["Entitlements"][0]["entitlementId"] for e in tiers],
    }


def base_entitlements():
    """PlayerData entitlements for the local profile."""
    import copy as _copy
    ents = [
        {"entitlementId": "currency_battlepassxp", "amount": SEED_STARS,
         "category": "Currency", "sourceTransactionId": "DEFAULT"},
        {"entitlementId": "currency_battlepasskeys", "amount": SEED_KEYS,
         "category": "Currency", "sourceTransactionId": "DEFAULT"},
        {"entitlementId": "currency_credits", "amount": SEED_CREDITS,
         "category": "Currency", "sourceTransactionId": "DEFAULT"},
        {"entitlementId": "icon_0000_world_cruciblelogo", "amount": 1,
         "maxAmount": 1, "category": "AccountIcon",
         "sourceTransactionId": "DEFAULT"},
    ]
    for _cid, _eid in SEASONAL_ICONS.items():
        if _eid == "icon_0000_world_cruciblelogo":
            continue
        ents.append({"entitlementId": _eid, "amount": 0, "maxAmount": 1,
                     "category": "AccountIcon",
                     "sourceTransactionId": "DEFAULT"})
    # Mode unlocks: amount must be > 0 or the mode-select list gates every
    # entry out (see the Gamemode block above).
    for _eid in MODE_UNLOCKS:
        ents.append({"entitlementId": _eid, "amount": 1, "maxAmount": 1,
                     "category": MODE_UNLOCK_CATEGORY,
                     "sourceTransactionId": "DEFAULT"})
    return _copy.deepcopy(ents)


def offers_list():
    return {"offers": [
        {"offerId": "credits_pack_500", "type": "CURRENCY",
         "providerName": "STEAM",
         "entitlements": [{"entitlementId": "currency_credits",
                           "amount": 500}],
         "metadata": ["credits", "pack_small"],
         "price": {"amount": 499, "currency": "USD"}},
        {"offerId": "credits_pack_1000", "type": "CURRENCY",
         "providerName": "STEAM",
         "entitlements": [{"entitlementId": "currency_credits",
                           "amount": 1000}],
         "metadata": ["credits", "pack_medium"],
         "price": {"amount": 999, "currency": "USD"}},
        {"offerId": "credits_pack_2500", "type": "CURRENCY",
         "providerName": "STEAM",
         "entitlements": [{"entitlementId": "currency_credits",
                           "amount": 2500}],
         "metadata": ["credits", "pack_large"],
         "price": {"amount": 1999, "currency": "USD"}},
        {"offerId": "battlepass_s1_pass", "type": "BATTLEPASS",
         "providerName": "STEAM",
         "entitlements": [{"entitlementId": "battlepass_s1",
                           "amount": 1}],
         "metadata": ["battlepass", "season1"],
         "price": {"amount": 1000, "currency": "credits"}},
    ]}

