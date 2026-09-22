# Every map, every mode

Static survey of the five playable installed levels and all twelve shipped
game-mode configurations, plus the restructuring of `server/solo_modes.py`
into separate map and mode profiles. Nothing here is live-verified except
where it repeats an earlier accepted run; the user has to launch one map at
a time. Read `docs/18-heart-of-the-hives-solo.md` first for the accepted
Practice and Alpha Hunters baselines.

Evidence classes are kept apart everywhere below and in the code:
**shipped** (read out of an installed asset), **computed** (produced by a
formula derived from shipped assets), **live** (observed in a client run),
**unmeasured** (a placeholder that is neither).

## The level-name hash function is Jenkins one-at-a-time

`levels/<name>/commondata/stringhashlibrary.xml` is an `AssetHashMap` of
`Key` (u32) to `Value` (a lowercase asset path without extension). The
function is **Jenkins one-at-a-time over the lowercased path**:

```
h = 0
for c in path.lower(): h += c; h += h << 10; h ^= h >> 6
h += h << 3; h ^= h >> 11; h += h << 15          (all mod 2^32)
```

It reproduces **906 of 906** non-empty pairs in
`levels/practicearena/commondata/stringhashlibrary.xml`, including both
hashes already in live use: `levels/practicearena/commondata/level` ->
1289380704 and `levels/april2018/commondata/level` -> 161882647. CRC32,
CRC32C, FNV-1/1a, djb2, sdbm, ELF, murmur2 and Adler-32 all score zero.
`server/solo_modes.level_name_hash` implements it and
`server/test_solo_modes.py` pins it against four shipped pairs. A wider
refit over all six shipped libraries pooled — 942 distinct pairs — also
misses none; `tools/inspect_map_hashes.py --verify` re-runs it. That sweep
additionally rules out MurmurHash3, xxHash32, the leading four bytes of
MD5/SHA-1 and of an `AZ::Uuid::CreateName`-style name UUID, and CRC32 across
both bit orders, four polynomials, three initial values, both final XORs and
with/without a trailing NUL.

### What actually pins the lowercasing

The 906-pair fit above cannot see the `.lower()`: every path-like Value in
practicearena's library is *already* lowercase, and for lowercase input
"lowercase then hash" and "hash verbatim" are the same function. So that fit
alone does not decide the one case where the two disagree — **HotH_02**, the
only installed level whose directory name has capitals.

Twelve of the 942 pooled entries settle it. They are the only Values shipped
in mixed case, and they live in the tutorial_02/tutorial_03 libraries:
`GameModeEntity` (1308711974), `DialogueMatchEntity`,
`ParticlesCoordinatorEntity`, `TutorialObjectives`, `R_wrist_JNT`, `Speed`
and six `StandaloneAutoStruct.*` names. **All twelve match when lowercased
and all twelve miss when hashed verbatim** — e.g. `GameModeEntity` is
shipped as 1308711974, hashes to 1308711974 lowercased and to 981657145
verbatim. Hashing verbatim therefore scores 930/942, lowercasing 942/942,
and the discriminating subset is unanimous.

Consequently HotH_02's key is the hash of `levels/hoth_02/commondata/level`
= **1770556851**, not of `levels/HotH_02/...` = 4142557249. The second value
is recorded only so that a failed HotH_02 launch has one cheap retry
(`CRUCIBLE_LEVEL_HASH=4142557249`) before falling back to April2018's
shipped hash; the twelve entries say it is wrong. No shipped library
contains a single path-like Value with a capital letter, which is consistent
with the asset pipeline lowercasing paths before hashing them.

Computed level hashes (all **computed**, none shipped):

| level | `levels/<l>/commondata/level` hash | in any shipped library? |
| --- | --- | --- |
| practicearena | 1289380704 | yes (shipped) |
| april2018 | 161882647 | yes (shipped) |
| mainmenu | 3193911061 | yes (shipped) |
| HotH_02 | **1770556851** | **no** |
| tutorial_02 | **3368922599** | **no** |
| tutorial_03 | **3298824014** | **no** |

`docs/18` recorded that HotH_02 "has no matching entry in the string hash
library". The reason is now concrete: **`levels/HotH_02/commondata/
stringhashlibrary.xml` is byte-identical (md5 66521c4e…) to
`levels/april2018/commondata/stringhashlibrary.xml`.** So are its
`tags.txt` and `level.cfg`, and the HotH_02 directory even ships
`april2018.dds`(.1–.8) minimaps. HotH_02 was derived from April2018 and its
build artefacts were never regenerated. `tutorial_02` and `tutorial_03`
share a library too (md5 98237fc4…) which lists `levels/tutorial/...`, a
level that is not installed. No level ships a `temporary/commondata/level`
slice, and every level's `leveldata/instances.xml` is `<Instances />`.

### Does anything validate the hash?

Partly, and it is the one thing that could refuse a HotH_02 or tutorial
launch outright. The wire `SliceEntryId`/slice-hash pair is consumed by
`NovaNet::EntitySliceLibrary::CreateSliceData` (retail 0x4bea820, source
path `dev\Code\NovaNet\Game\NovaGameCommon\Network\EntitySliceLibrary.cpp`).
It does three things in order:

1. `0x4bf1480` scans the StringAsset's **path string** at `+0xc` and, when
   it is empty or has no path separator, emits
   `"StringAsset %s is an invalid asset path!"` (line 0xe6).
2. `0x4be8b10` looks the slice up in a bucket map keyed **only on the u32
   hash** at StringAsset `+8`.
3. An empty result emits `"Slice '%s' was not found."`.

Step 1 needs the hash to resolve to a path, which is what the AssetHashMap
is for, so a hash absent from the loaded library will at minimum log that
warning. Step 2 does not consult the library at all, so if the loaded level
slice self-registers under the hash of its own path the lookup can still
succeed. **Whether step 1 is fatal is unproven.** The client's own log will
say: if HotH_02 fails, `NovaGameClient-*.log` will contain
`StringAsset ... is an invalid asset path!` or `Slice '...' was not found.`
The global `assetcatalog.xml` in `misc.pak` *does* contain
`levels/hoth_02/commondata/level.dynamicslice`, so the asset itself
resolves; only the hash-to-name map is stale.

Mitigation: `CRUCIBLE_LEVEL_HASH` overrides the profile's hash, so the
retry is one environment variable, not a code change. Plan B for HotH_02 is
`CRUCIBLE_LEVEL_HASH=161882647` (April2018's shipped hash) while HotH_02 is
the loaded level.

## The complete shipped mode list

`design/gamemodes/gamemodes.gamemodeconfiglist` (misc.pak, 2376 bytes) has
exactly **twelve** entries; the array position is the `ConfigurationIndex`
the shard puts on the wire, and the list's own default entry is
`SOLO_CASUAL`. All values below are **shipped**.

| idx | name | `.gamemodeconfig` | MinPlayers | targetScore / matchPoint | phases | UIModeOverride |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | SOLO_CASUAL | singlesunranked | 8 | 0 / 0 | 8 | Invalid |
| 1 | DOUBLES_CASUAL | doublescasual | 16 | 0 / 0 | 8 | Invalid |
| 2 | SOLO_BOTS | singlesunranked | 8 | 0 / 0 | 8 | Invalid |
| 3 | CRUCIHEARTS | crucihearts | 8 | 3 / 2 | 9 | Invalid |
| 4 | CONQUEST_8V8 | conquest_8v8 | 16 | 0 / 0 | 9 | CONQUEST |
| 5 | TUTORIAL | tutorial | **1** | 0 / 0 | 4 | Invalid |
| 6 | PRACTICE | practice | **1** | 0 / 0 | 4 | Invalid |
| 7 | 1v1 | 1v1 | 3 | 10 / 9 | 9 | Invalid |
| 8 | HEART_OF_THE_HIVES | heart_of_the_hives | 8 | 3 / 2 | 9 | HEART_OF_THE_HIVES |
| 9 | **HOTH_GUIDE** | hoth_guide | **1** | 3 / 2 | 9 | **HEART_OF_THE_HIVES** |
| 10 | HOTH_MINIMAL | hoth_minimal | 8 | 3 / 2 | 9 | HEART_OF_THE_HIVES |
| 11 | TUTORIAL2 | tutorial2 | **1** | 0 / 0 | 4 | Invalid |

Note that index 0 and index 2 reference the **same** config asset
(`singlesunranked.gamemodeconfig`), so SOLO_CASUAL and SOLO_BOTS differ on
the wire only by the index.

### MatchPhaseType is now a known enum

Retail name-pointer table at `.rdata` 0x65bc9c0, strings in the block
0x6be7840..0x6be7950, source
`GameCommon\Crucible\Services\BakedNames\MatchPhaseType.cpp`:

| 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| WaitForPlayers | CharacterPreview | CharacterSelect | PerkPreview | TeamInfo | PerkSelect | DropPreview | DropSelect | **GamePlay** | GameOver | Shutdown |

Index 8 = GamePlay agrees exactly with the `00 08` CurrentPhase value that
`shard_stub.solo_phase_reply` already sends and that the client accepted on
both the Practice and the Alpha Hunters runs. This is the first time the
whole enum has been enumerated rather than inferred from one value.

### Phase orders, with each phase's shipped duration in seconds

`inf` is 0xFFFFFFFF. The `time` field is a big-endian u32, not a float.

| mode | phase order |
| --- | --- |
| PRACTICE | WaitForPlayers=inf, GamePlay=inf, GameOver=1, Shutdown=inf |
| TUTORIAL / TUTORIAL2 | WaitForPlayers=1, GamePlay=inf, GameOver=300, Shutdown=inf |
| SOLO_CASUAL / SOLO_BOTS | WaitForPlayers=0, PerkPreview=30, PerkSelect=20, DropPreview=5, DropSelect=30, GamePlay=inf, GameOver=300, Shutdown=inf |
| DOUBLES_CASUAL | as above but PerkPreview=240 |
| HEART_OF_THE_HIVES / HOTH_MINIMAL | WaitForPlayers=0, PerkPreview=240, TeamInfo=10, PerkSelect=5, DropPreview=5, DropSelect=5, GamePlay=inf, GameOver=300, Shutdown=inf |
| HOTH_GUIDE | WaitForPlayers=0, PerkPreview=1, TeamInfo=5, PerkSelect=20, DropPreview=12, DropSelect=8, GamePlay=inf, GameOver=300, Shutdown=inf |
| CRUCIHEARTS / CONQUEST_8V8 | WaitForPlayers=0, PerkPreview=240, TeamInfo=10, PerkSelect=20, DropPreview=5, DropSelect=20, GamePlay=inf, GameOver=300, Shutdown=inf |
| 1v1 | WaitForPlayers=0, PerkPreview=20, TeamInfo=5, PerkSelect=15, DropPreview=1, DropSelect=10, GamePlay=inf, GameOver=300, Shutdown=inf |

`docs/18`'s recorded HotH sequence (WaitForPlayers, PerkPreview, TeamInfo,
PerkSelect, DropPreview, DropSelect, GamePlay, GameOver, Shutdown) is
confirmed exactly.

Practice, TUTORIAL and TUTORIAL2 carry the `AutoSkipPhaseToGameplay` and
`DoNotSpawnDropPod` flags and `MinimumPlayers 1`, so they never need the
solo phase bypass. Every other mode does.

### HOTH_GUIDE is the shipped single-player Heart of the Hives

This is the most consequential finding for the current goal.
`design/gamemodes/hoth_guide.gamemodeconfig` has **MinimumPlayers 1**, a
`UIModeOverride` of `HEART_OF_THE_HIVES`, targetScore 3 / matchPoint 2, a
`GuideHeartOfTheHives` flag, `SequentialHives`, `HivesSpawnHearts`,
`RespawnPointShields`, its own
`assets/respawnparams/guide_respawnparams.respawnparams`,
`assets/planetaryevents/hoth_guide.sequentialgmdata`,
`assets/gamesystems/aioverlord/hoth_guide_no_overlord.aioverlordspawndata`
and a real win condition,
`cdl/gamemodes/guide_hoth/hoth_guide_win_condition.cdl`. Thirty further
`cdl/gamemodes/guide_hoth/*.cdl` scripts cover heart capture, harvester
capture, hive spawn/death, player respawn, level-up and match-over, and
`assets/bakednames/guidebotidentifier.names` exists alongside
`gamemodeflag.names`/`gamemodetype.names`. It is the only Heart of the
Hives configuration that does not require eight players.

It is authored for **HotH_02**, whose entity array is the only one
containing hive spawn nodes (below), and its twelve
`SpawnNode_Guide_EnemyBot` nodes. `CRUCIBLE_SOLO_MODE=hoth-guide` selects
this pair. Nothing about it is live-verified.

## Per-map baked entity array

Read with the new `tools/inspect_map_entities.py` from
`levels/<name>/commondata/level.dynamicslice` (extract with
`tools/pak.py cat`). Every installed level slice has an **empty**
slice-reference list (field CRC 0xafa37bc9), so
`AZ::SliceComponent::GetEntities` (0x530a110) prepends nothing and the
direct Entities-array position *is* the `SliceEntryId` that
`CreateReplicatedIndexedEntity` (0x4bf5120) bounds-checks against the
vector built by 0x4bf4d60. The builder at 0x4bf4dd1 stores, per
`AZ::EntityId`, a `{u32 index; bool hasNetworkComponent}` pair — the index
is the plain array position.

All **shipped**:

| level | entities | MatchPhaseController | MatchComponents | GameMode | MatchDataStore |
| --- | --- | --- | --- | --- | --- |
| practicearena | 1198 | 123 | 125 | 127 | 133 |
| april2018 | 19529 | 1656 | 1658 | 1660 | 1666 |
| HotH_02 | 21510 | 617 | 619 | 621 | 627 |
| tutorial_02 | 2303 | 57 | 59 | 61 | 67 |
| tutorial_03 | 2400 | 57 | 58 | 60 | 66 |

Each name occurs exactly once per map, so there is no ambiguity. HotH_02's
617/621/627 confirm the candidates `docs/18` recorded. Reproduce any row:

```
python3 tools/pak.py cat ~/.local/share/Steam/steamapps/common/Crucible/levels.pak \
    levels/HotH_02/commondata/level.dynamicslice --out /tmp/hoth.dynamicslice
python3 tools/inspect_map_entities.py /tmp/hoth.dynamicslice \
    --name '^(MatchPhaseController|MatchComponents|GameMode|MatchDataStore)$'
```

## The baked network-ID bias: what it is not

`practicearena` maps baked entry -> network counter as `entry + 2`
(**live**: entries 73..80 -> FE00004B..FE000052, 123 -> FE00007D,
127 -> FE000081, 133 -> FE000087). `april2018` maps it as `entry + 787`
(**live**: 1656/1660/1666 -> FE00098B/FE00098F/FE000995,
`hives-first-replicators.json`, re-confirmed by `live-alpha-entry.json`).
Within each map the offset is constant across a 60-entry window, so the
client's counter advances exactly once per array entry; the per-map part is
a single additive constant.

**I could not derive that constant from the level asset.** The following
hypotheses were each tested and each fails:

1. *A fixed engine offset.* Would give 2 on both maps.
2. *Root entity + slice metadata entity.* Also 2 on both maps.
3. *Nested slice instances prepended by `GetEntities`.* Field 0xafa37bc9 is
   empty in every installed level slice, and a whole-tree walk finds
   exactly `entities + 2` `AZ::Entity` nodes per file (1200 and 19531).
4. *A different entity ordering.* Ranking the array by the 64-bit asset
   `EntityId`, by its high dword, by its low dword or by the swapped pair
   yields 7 to 11 distinct offsets across the ten known practicearena
   points, not one.
5. *The network ID stored in the asset.* The 4-byte big-endian patterns
   FE00007D/81/87/4B/52 and FE00098B/8F/95 each occur at most once per
   file, always inside unrelated blob data; `fe 00 00` occurs 579 times
   scattered through practicearena alone, so those single hits are noise.
6. *The slice metadata maps.* The metadata entity's two components carry
   maps of 1200 (practicearena) and 20601 (april2018) elements, one leaf
   `EntityId` pair each, all keys distinct. The excesses over the entity
   counts are 2 and **1072**, not 2 and 787.
7. *A per-map population of some component.* Sweeping every component-type
   UUID in both files for one whose per-map count is (2, 787) finds none;
   nor does the count of entities by component arity.
8. *Distinct asset-EntityId salts.* 77 and 625, not 2 and 787.
9. *A separately instantiated `temporary` level slice, or
   `leveldata/instances.xml`.* Neither exists: no level ships a
   `temporary/commondata/level`, and every `instances.xml` is
   `<Instances />`.

So the bias for HotH_02, tutorial_02 and tutorial_03 is **unmeasured**, and
the profiles say so (`baked_counter_bias_source='unmeasured'`, placeholder
value 2). It costs one launch per map to measure, and
`tools/inspect_map_baked_bias.py` does it read-only: it walks the client's
replication tree, prints every 0xFE-category node with its slice hash,
`slice_entry` and `network_counter - slice_entry`, and reports whether that
difference is a single constant. Run it with the client sitting in the
target level, then set `CRUCIBLE_BAKED_BIAS=<value>` (or put the value into
the `MapProfile` and change its source to `'live'`).

If the wrong bias is sent the failure mode is already known from the first
HotH launch: the client creates inert state-2 replicas and the log
repeatedly reports `GameModeComponent not ready`.

## Authored spawn transforms

`AzFramework::TransformComponent` is UUID
22b10178-39b6-4c12-bb37-77db45fdd3b6. Its `Transform` field (CRC
0x77b281ad) is **twelve big-endian floats**, row-major 3x4, translation
last; `LocalTransform` is 0xcae6ee55 and `Parent` is 0x3d8e604f. Decoding
practicearena entry 695 `PlayerStartSpawn` gives exactly
(501.09075927734375, 564.5346069335938, 45.27315902709961), the authored
start already recorded in `docs/16-entity-bootstrap.md` — that is the
calibration for every coordinate below. All **shipped**.

| level | player drop | hunter/bot cluster |
| --- | --- | --- |
| practicearena | 695 `PlayerStartSpawn` (501.091, 564.535, 45.273) | none — eight baked `RoboDummy` entries 73..80 are the authored population |
| april2018 | 173 `Player_DropPoint_17d` (1312.711, 1271.156, 23.670) | 181 `_17c` (1316.273, 1257.578, 22.709), 246 `_17a` (1313.471, 1264.975, 23.625), 268 `_17b` (1318.480, 1248.327, 20.875) |
| HotH_02 | 586 `Player_DropPoint_09a` (1316.242, 921.892, 19.250) | 572 `_09b` (1329.016, 929.668, 20.000), 593 `_09c` (1328.478, 921.921, 20.000), 592 `_09d` (1331.497, 935.129, 20.000) |
| tutorial_02 | 2084 `SpawnNode_Player_DropPoint` (501.594, 516.848, 41.572) | 1/2/3 `SpawnNode_Snurtle_AIOverlord` (494.115, 627.756, 37.500), (508.674, 631.474, 37.500), (528.394, 580.937, 37.500) |
| tutorial_03 | 1024 `SpawnNode_Player_DropPoint` (501.594, 516.848, 41.572) | 1090 `InitialTrainingDummySpawnPoint` (502.575, 562.006, 42.109), 82/83 `RoboDummy` (492.249, 806.065, 38.737), (492.199, 810.672, 38.737) |

The april2018 row reproduces `docs/18`'s cluster exactly, including the
entry numbers 173/181/246/268 — independent confirmation that the reader is
correct. The HotH_02 cluster is one of six four-point drop groups; it sits
next to `SpawnNode_Player_RespawnPoint_06a` (533, at 1335.358, 926.614,
20.500), so it is an authored playable area. Its maximum spread is 16 m,
comparable to the april2018 cluster's 23 m, which is the spread the user
photographed three visible hunters across.

**The tutorials have no authored bot cluster near the player drop.** The
nearest authored spawn nodes are 45 m (tutorial_03's training dummy point)
to 115 m away, so hunter presence will not be visible at spawn on those
maps. Those entries are in the profile only so that the roster path stays
active; nothing has been invented.

### HotH_02's objective and AI geography (shipped)

Only HotH_02 has hives. `april2018` contains **no**
`SpawnNode_Hive_AIOverlord_*` and **no** `hive_spawn_*` entity at all —
its single hive-named entity is `HIVE_SHOT_01a` (14228), a prop. This means
the accepted Alpha Hunters map cannot host Heart of the Hives objectives
even if the mode index is accepted, which is consistent with `docs/18`
reaching PerkPreview on april2018 and nothing more.

HotH_02's six hive overlord spawn nodes, entries 1605..1610:
SWAMP (1229.125, 821.033, 18.625), LOG (989.551, 1238.868, 52.205),
RESEARCH (1280.411, 1046.880, 22.875), GROTTO (787.559, 1054.326, 50.000),
FALLS (905.220, 720.125, 10.125), SPACEPORT (1196.556, 1230.998, 20.500).
Each has a matching `hive_spawn_base_001` / `hive_spawn_root_*` /
`hive_spawn_flower_branch_001` prop group and a `HIVE_<name>` container.

The twelve `SpawnNode_Guide_EnemyBot` nodes, entries 33..44:
(754.297, 1051.094, 56.457), (1200.207, 1203.408, 25.895),
(905.904, 756.082, 16.394), (1208.526, 806.218, 23.875),
(978.975, 1209.765, 58.960), (940.551, 721.473, 13.040),
(1259.547, 1062.133, 30.893), (997.573, 1269.808, 59.713),
(1194.125, 1256.834, 27.507), (1223.385, 847.576, 26.346),
(817.110, 1051.695, 50.322), (1300.611, 1034.391, 30.026).
These are the HOTH_GUIDE bot spawns. `docs/18` recorded a
`Guide_EnemyBotSpawns` container at 11940 and the nodes at 33..44; the
coordinates are new. They are all 100 m or more from the 09a drop cluster,
so they are match-scale spawns, not presence-test positions.

## Restructured `server/solo_modes.py`

Three dataclasses replace the single conflated one:

* `MapProfile` — level name, level hash + its source, entity array length,
  the four baked Match* entries, the baked bias + its source, authored
  player drop (position, entry, name), the bot cluster (positions and
  entries), hive spawns and creature spawns.
* `ModeProfile` — one gamemodeconfiglist entry: configuration index, config
  asset path, MinimumPlayers, targetScore/matchPointThreshold, the full
  ordered phase sequence as `(name, enum value, seconds)`, alliance and
  respawn flags, UIModeOverride, plus the solo roster shape
  (`roster_size`, `local_team`, `bot_team`) and `authored_maps`.
  `waits_for_human_players` is true exactly for the modes whose shipped
  phase order would stall one peer; Practice, TUTORIAL and TUTORIAL2 are
  false because they carry `AutoSkipPhaseToGameplay`.
* `SoloMode` — the composed shard-facing view. **Its field names, order and
  values are unchanged**, so `shard_stub.py`, `match_stub.py`,
  `hunter_patrol.py` and `navigation.py` need no edit, and
  `solo_modes.PRACTICE` / `HIVES` / `ALPHA_HUNTERS` / `SOLO_CASUAL` still
  exist as `SoloMode` instances with the same contents.

Preservation is asserted, not assumed: `test_solo_modes.py` pins the exact
nine-field wire tuple of both accepted profiles, re-checks Practice's
gamemode/store counters (129/135), its `80 06` configuration payload, its
phase counter 125 and its one-entry roster, and asserts that
`practicearena` never gains bot positions (which is what keeps
`shard_stub` on the `build_practice_npc_probes()` path).

`CRUCIBLE_SOLO_MODE` keeps `practice`, `heart-of-the-hives`,
`alpha-hunters` and `solo-casual` and adds `solo-bots`, `one-v-one`,
`conquest`, `hoth-guide`, `hoth-hives`, `hoth-minimal`, `crucihearts`,
`tutorial`, `tutorial2`, plus the hash-safe fallbacks
`hoth-guide-april2018` and `hoth-minimal-april2018`. It also accepts an
explicit `<level>:<mode>` pair, e.g. `HotH_02:hoth-guide` or
`tutorial_03:TUTORIAL2`, so every installed map can be paired with every
shipped mode without a code change. Unknown levels and unknown modes both
raise. Two overrides exist for the unproven values:
`CRUCIBLE_BAKED_BIAS` and `CRUCIBLE_LEVEL_HASH`.

## Readiness

| map + mode | level hash | baked bias | baked entries | spawns | entry proven? |
| --- | --- | --- | --- | --- | --- |
| practicearena + PRACTICE | shipped | live | shipped | shipped | **yes, accepted** |
| april2018 + DOUBLES_CASUAL (alpha-hunters) | shipped | live | shipped | shipped | **yes, accepted, three hunters visible** |
| april2018 + SOLO_CASUAL / SOLO_BOTS / 1v1 / CONQUEST_8V8 | shipped | live | shipped | shipped | no; identity is sound, only the index differs from the accepted run |
| april2018 + HEART_OF_THE_HIVES | shipped | live | shipped | shipped | partial: reached PerkPreview; map has no hives |
| april2018 + HOTH_GUIDE (`hoth-guide-april2018`) | shipped | live | shipped | shipped | no; MinimumPlayers 1 but the map has no hives |
| tutorial_02 + TUTORIAL | computed | **unmeasured** | shipped | drop shipped, bots distant | no |
| tutorial_03 + TUTORIAL2 | computed | **unmeasured** | shipped | drop shipped, bots distant | no |
| HotH_02 + HOTH_GUIDE (`hoth-guide`) | computed | **unmeasured** | shipped | shipped | no — but this is the only map+mode pair the shipped assets actually author for solo Heart of the Hives |
| HotH_02 + HEART_OF_THE_HIVES / HOTH_MINIMAL / CRUCIHEARTS | computed | **unmeasured** | shipped | shipped | no; MinimumPlayers 8 |

## Recommended launch order

Each step is one launch, and each isolates exactly one unknown. Set the
same `CRUCIBLE_SOLO_MODE` in **both** the hub and the shard drop-ins; they
must agree on the profile.

1. `CRUCIBLE_SOLO_MODE=practice` — confirm the accepted baseline still
   enters after this restructuring. Nothing else is worth testing until
   this is green.
2. `CRUCIBLE_SOLO_MODE=alpha-hunters` — confirm the second accepted
   baseline still enters with three visible hunters.
3. `CRUCIBLE_SOLO_MODE=tutorial` — the cheapest test of a **computed**
   level hash, because tutorial_02's mode has MinimumPlayers 1 and
   `AutoSkipPhaseToGameplay`, so the only new variables are the hash and
   the bias. Run `tools/inspect_map_baked_bias.py` while the level is up,
   before judging anything, and check the client log for
   `StringAsset ... is an invalid asset path!` /
   `Slice '...' was not found.`. If the bias is not 2, re-run with
   `CRUCIBLE_BAKED_BIAS=<measured>`.
4. `CRUCIBLE_SOLO_MODE=hoth-guide-april2018` — HOTH_GUIDE's configuration
   index on a map with a **shipped** hash and a **live** bias. This
   isolates "does the client accept configuration 9" from "does HotH_02
   load". Expect no hives: april2018 has none.
5. `CRUCIBLE_SOLO_MODE=hoth-guide` — the real target. Measure the bias with
   `tools/inspect_map_baked_bias.py` first, then relaunch with
   `CRUCIBLE_BAKED_BIAS=<measured>`. If the log shows the invalid-asset-path
   warning, retry once with `CRUCIBLE_LEVEL_HASH=161882647`.
6. `CRUCIBLE_SOLO_MODE=hoth-hives` — only after step 5 enters, to compare
   configuration 8 against configuration 9 on the same map.

Do not restart the hub or the shard while a Crucible process exists.

## What is still unproven

* No launch has happened for any profile added here.
* The baked bias for HotH_02, tutorial_02 and tutorial_03 is a placeholder.
* Whether a level hash missing from the loaded `stringhashlibrary.xml` is
  fatal or merely logged.
* Whether the hub's level string and the shard's slice hash have to agree,
  and whether the hub validates the level name at all.
* HOTH_GUIDE's `GuideHeartOfTheHives` behaviour, the guide bot spawns, hive
  encounters, heart capture, scoring and win/loss. None of it is
  implemented; only the shipped configuration and geography are catalogued.
* Whether the tutorials' `MatchDataStore` replication schema matches
  Practice's; only the entity names and array positions were compared, not
  the component UUID lists.

## Integration notes

`shard_stub.py` and `match_stub.py` were deliberately **not** edited. The
new fields are additive and every existing field keeps its name and value,
so the current code already works with the new profiles. The following
changes are the ones worth making, in order of risk.

1. **`shard_stub.py`, line 816** (the population branch):
   `(build_solo_hunter_probes() if solo_modes.selected().bot_positions
   else build_practice_npc_probes())`. `build_practice_npc_probes()`
   activates practicearena's baked entries 73..80 and the authored spitter
   positions; on any other level those entries are unrelated entities.
   Gate it on the level, not on `bot_positions`:
   `if solo_modes.selected().level == 'practicearena'`. The same applies to
   the `practice_npcs_sent` guards at lines 1021 and 1025. This is a real latent
   bug for the tutorials, which is why their profiles ship non-empty
   `bot_positions` as a stopgap.
2. **`shard_stub.py`, line 972**: `solo_modes.selected() != solo_modes.PRACTICE`
   still works (dataclass equality, and the composed PRACTICE is identical)
   but it is fragile. Prefer
   `solo_modes.selected().mode_profile is not solo_modes.MODE_PRACTICE`, or
   `solo_modes.selected().level != 'practicearena'`, depending on which the
   guard actually means.
3. **`shard_stub.solo_phase_reply`**: replace the literal `b'\0\x08'` with
   `solo_modes.selected().solo_phase.to_bytes(2, 'big')` and gate the whole
   bypass on `solo_modes.selected().mode_profile.waits_for_human_players`.
   PRACTICE, TUTORIAL and TUTORIAL2 carry `AutoSkipPhaseToGameplay` and do
   not need it; sending it anyway is what the accepted Practice run already
   does, so changing this is an improvement, not a fix.
4. **`shard_stub.build_single_player_roster_probe`**: it derives the entry
   count from `spawned_character` plus `bot_positions`. Drive it from
   `solo_modes.selected().roster_size` instead, and take the team bytes
   from `local_team` / `bot_team` rather than the hardcoded 1 and 2. This
   must leave Practice at one entry (`roster_size` is 1) and Alpha Hunters
   at four, which `test_solo_modes.py` now asserts.
5. **`shard_stub.build_practice_gamemode_probe`**: the six-decision
   replication record and the `configuration + 32768` int16 are unchanged
   and correct for every index 0..11, since `ConfigurationIndex` is a
   full-range signed int16. Nothing to do; only the docstring is now stale
   (Practice is entry 6 of a twelve-entry list, not an unspecified one).
6. **`match_stub.py`, line ~316**: already sends
   `os.environ.get("CRUCIBLE_HUB_LEVEL", solo_modes.selected().level)`. For
   `HotH_02` the directory name is mixed case; the hub string should keep
   the installed casing (`HotH_02`) while the hash is computed over the
   lowercased path. `solo_modes` does this already — `level` is
   `'HotH_02'`, `level_hash` uses `hoth_02`. Do not lowercase the hub
   string "to be consistent".
7. **Both stubs must select the same profile.** `CRUCIBLE_SOLO_MODE` is
   read independently in each process, so the two `solo-mode.conf`
   drop-ins have to carry the same value, and so must `CRUCIBLE_BAKED_BIAS`
   and `CRUCIBLE_LEVEL_HASH` if either is set. A mismatch will silently
   produce a hub that loads one level and a shard that names another.

## New read-only tools

* `tools/inspect_map_entities.py` — enumerate a level's Entities array:
  names, asset EntityIds, component UUIDs, authored world translations,
  with `--name REGEX`, `--component UUID`, `--range LO:HI`,
  `--near X,Y,Z --radius R` and `--json`. It reports the array length and
  whether the slice has nested instances, so the `SliceEntryId ==
  array position` assumption is re-checked on every run.
* `tools/inspect_map_hashes.py` — the level-hash calculator and the refit
  harness for the hash function itself: `--levels` prints every installed
  level's key, `--hash STRING` hashes anything, `--verify LIB...` re-runs the
  fit over the shipped libraries and reports every miss, and `--lookup KEY
  --library LIB` resolves a key back to its path. `--verify` is what backs
  the 942/942 claim and the twelve mixed-case discriminators above, so it is
  the check to re-run if the hash is ever doubted.
* `tools/inspect_map_baked_bias.py` — one-launch, read-only measurement of
  a level's baked network-ID bias from the running client's replication
  tree, with a histogram so a non-constant bias is visible rather than
  silently averaged.

Neither tool writes anything, launches the game or touches the Steam
install.

## Second reading of the bias question, from the executable

The section "The baked network-ID bias: what it is not" above rules the bias
out of static derivation and calls for a one-launch measurement. A separate
static pass over the client reaches a weaker requirement, which is worth
recording because it changes what a failed launch means. Evidence class
**computed** (disassembly only; no launch).

`NovaNet::SliceIndexing::CreateReplicatedIndexedEntity` (retail 0x4bf5120,
asserts naming
`dev/Code/NovaNet/Game/NovaGameCommon/Network/EntitySliceIndexing.cpp`) does
this and nothing else with the incoming replica: it reads the SliceEntryId from
the replica at `+0x90`, rejects `-1`, bounds-checks it against the entity
vector at `+0xc8..+0xd0` (`(end-begin)>>3` elements), and loads
`[begin + id*8]`. The vector is the one built by the loop at 0x4bf4e30, one
pass over `GetEntities` in array order. **No instruction in either function
recomputes, derives or compares the 0xFE-prefixed replica ID.** A scan of the
whole `.text` finds `0xFE000000` as an immediate only in one unrelated block
(0x5df2720..0x5df4153, all `and` masks), so the client does not build these IDs
from a constant prefix anywhere on this path either.

On that reading the wire requirement is only that the SliceEntryId equals the
array position and that the replica ID is unique and used consistently by the
server for the same entity, and the bias is free. It does not explain why the
first april2018 run failed, so it does not overturn the measured 787 — the two
readings can be told apart cheaply: if `CRUCIBLE_BAKED_BIAS=2` also enters on
april2018, the bias is free and no per-map measurement is ever needed; if it
does not, the measurement procedure above is required. That test costs one
launch on an already-accepted map and should be run before spending launches
measuring HotH_02.

`0xFE000000` is `127 << 25`, so the counter field is 25 bits wide — no shipped
level's entity count (21510 at most) can overflow or collide with it.

## HotH_02's six drop clusters

The "Authored spawn transforms" table uses the RESEARCH cluster. All six
authored `Player_DropPoint_09{a,b,c,d}` groups are below, each paired with the
`SpawnNode_Player_Respawn_<region>` anchor it sits next to, so an alternative
can be chosen without re-reading the slice. Entries 572..595 and 532..557;
all **shipped**, from `snapshots/maps-20260921/HotH_02-profile.json`.

| region | anchor | anchor position | 09a | 09b | 09c | 09d |
| --- | --- | --- | --- | --- | --- | --- |
| RESEARCH | 532 | (1340.830, 920.110, 28.000) | 586 | 572 | 593 | 592 |
| RIVER | 537 | (1349.244, 1191.413, 18.500) | 585 | 591 | 582 | 589 |
| CRATER | 542 | (842.201, 1202.723, 60.545) | 574 | 595 | 578 | 577 |
| SWAMP | 547 | (1079.751, 757.567, 20.500) | 584 | 594 | 580 | 575 |
| SPACEPORT | 552 | (1129.095, 1292.188, 29.750) | 587 | 579 | 573 | 588 |
| SW_EXPANSION | 557 | (824.745, 911.202, 41.500) | 590 | 581 | 576 | 583 |

RIVER is the tightest group: 585 (1349.742, 1187.142, 18.000), 591
(1346.516, 1196.918, 18.750), 582 (1347.228, 1191.921, 18.750), 589
(1344.747, 1201.129, 18.750) — about 14 m across, against RESEARCH's 16 m.
Either is a valid presence-test cluster.

## Reproducing the mode list and the map profiles

`tools/inspect_map_modes.py` reads
`design/gamemodes/gamemodes.gamemodeconfiglist` and every
`.gamemodeconfig` it references, and prints or dumps the index, mode name,
config path, MinimumPlayers, TargetScore/MatchPointThreshold, the ordered
phase list with each phase's shipped time, the GameModeFlags, the named
GameModeValues, and the alliance/respawn/UI fields. It regenerates the whole
mode table above from the install with no launch:

```
python3 tools/inspect_map_modes.py
python3 tools/inspect_map_modes.py --json snapshots/maps-20260921/gamemodes.json
```

Stored reports, all read-only and reproducible:
`snapshots/maps-20260921/gamemodes.json` and
`snapshots/maps-20260921/<level>-profile.json` for all six levels (entity
array length, nested-instance flag, the three controller entries found by
component signature, and every spawn-named entity with its authored
translation).
