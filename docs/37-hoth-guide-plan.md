# HOTH_GUIDE: the retail bot match, and how to get there

Status: stage 2 and stage 1's match timer are implemented and tested
offline (25 October), but not played. The other stages are plan only.

The Alpha Hunters bot match (docs/30, docs/35, docs/36) is local: retail
Alpha Hunters had no bots, and its three Marine hunters are our own
construction from the unwired `marine_guide_dummy.ai`. The only bot mode
retail shipped is **HOTH_GUIDE**, the single-player Heart of the Hives on
**HotH_02**, with four guide dummy heroes (docs/23;
`snapshots/accuracy-20260924/bot-authored-params.md` §1). A match there
is the true "retail-accurate bot match". This plan stages the work so that
each step can be played on its own.

## What the assets say (already read)

- **Mode:** `design/gamemodes/hoth_guide.gamemodeconfig`. MinimumPlayers 1;
  UI override HEART_OF_THE_HIVES; target score 3, match point 2. Flags
  include GuideHeartOfTheHives, SequentialHives, HivesSpawnHearts and
  RespawnPointShields. Respawn uses `guide_respawnparams`
  (UseRespawnPointsAsDropPoints). Phases are PerkPreview 1, TeamInfo 5,
  PerkSelect 20, DropPreview 12, DropSelect 8, then GamePlay.
- **Win:** `cdl/gamemodes/guide_hoth/hoth_guide_win_condition.cdl`. On each
  MatchScoreEvent it compares team scores with the target, then raises
  MatchOverEvent(WinningTeamId) (match-flow track finding 04). Thirty more
  `guide_hoth/*.cdl` scripts handle hearts, harvesters, hives, respawn,
  level-up and match-over voice lines. The main script is
  `guide_criticalpath.cdl` (1.8 MB).
- **Bots:** `slices/planetaryevents/guide_hoth.dynamicslice` BotData maps
  DuelistDummy, HeroDummy, InventorDummy and SaboteurDummy to
  `<hero>_character_dummy.dynamicslice`. Each names its
  `<hero>_guide(_dummy).ai`. `guide_criticalpath.cdl` holds the eight
  SpawnGuideBot nodes. HotH_02 authors twelve `SpawnNode_Guide_EnemyBot`
  nodes (entries 33–44, docs/23).
- **Creatures:** none. `hoth_guide_no_overlord.aioverlordspawndata` spawns
  no creatures. HotH_02's camps come from planetary events (creatures track).

### Guide AI core differences

Read from `snapshots/accuracy-20260924/guide-bots-ai-full.json`. Each value
is the first element whose path ends in the field name, and is
**unverified**. The same matching mis-hit `Duration` and `Angle`, and
Saboteur's 1.2 s may come from a second attack element. Re-extract by full
path before building on any of it.

| | duelist | hero | inventor | saboteur | (marine) |
|---|---|---|---|---|---|
| AggroRadiusNear / Far, m | 30 / 40 | 30 / 40 | 30 / 40 | 30 / 60 | 5 / 40 |
| DeaggroRadius, m | 50 | 30 | 40 | 60 | 50 |
| AggroAngleFar | 180° | 180° | 180° | 180° | 120° |
| ForcedAggroDurationOnDamage, s | 6 | 6 | 6 | 10 | 6 |
| AttackDelayForNewTargets, s | 0.25 | 0 | 0 | 0.25 | 0.25 |
| MinTimeBetweenAttacks, s | 0 | 0 | 0 | 1.2 | 0 |
| SpeedChase, m/s | 7.5 | 6.5 | 5.5 | 6.5 | 8.0 |
| Strafe full-influence band, m | ≤20, to 30 | ≤8, to 10 | ≤8, to 15 | ≤20, to 30 | ≤20, to 30 |
| StopDesiredDistance, m | 1.8 | 1.5 | 1.5 | 1.8 | 1.8 |
| CanPatrol | no | yes | yes | no | yes |

All four check line of sight, start awake, and never flee from damage.
Their weapons, health and capsules come from their character slices
(player-damage track table: duelist 800, hero 1200, inventor 600,
saboteur 800).

## Stages

Each stage ends in a live check. Every live entry must be able to end with
GameOver, never by leaving mid-match: leaving arms the rejoin trap
(docs/30).

1. **HotH_02 entry.** Its level hash is computed but its baked counter bias
   is unmeasured (docs/23). The timer exists:
   `CRUCIBLE_SOLO_MATCH_MAX_SECONDS=<s>` ends the match with GameOver after
   that many seconds of play. The count starts at the connection's first
   simulation tick, as in the match-flow track's `match_director.py`, which
   reads the same variable. A timeout is a draw: no team wins, so a
   published rank is 2.
   - **The timer only helps if GameOver reaches the client.** GameOver goes
     to `phase_entry + baked_counter_bias`. With a wrong bias the client
     keeps inert replicas and logs "GameModeComponent not ready" (docs/23),
     so no GameOver lands.
   - **Run order** (docs/23's launch order):
     1. `hoth-guide-april2018`: configuration 9 on a live bias (787).
     2. `tutorial`, to measure a computed map's bias.
     3. `hoth-guide`: measure the bias with `tools/inspect_map_baked_bias.py`,
        then relaunch with `CRUCIBLE_BAKED_BIAS`.
   - **Open question for the owner before `hoth-guide`:** how does the run
     exit if the bias proves wrong?
   - **Gate:** GamePlay, then the timer ends the match cleanly.
2. **Per-map navigation.** Done offline; see "Per-map navigation" below.
   The hunters walk the selected map's own `mnmnavmission0.bai`, from
   `NvGameSdk/levels/<level>/level.pak` (the directory is `HotH_02`; case
   matters on Linux). On HotH_02 they start on `SpawnNode_Guide_EnemyBot`
   nodes, not drop points.
3. **One guide dummy.** Generalise `hunter_ai.Tuning` to every
   `*_guide*.ai` and spawn one hero dummy slice. That slice's shared
   component set must be captured live first, as was done for the Marine.
   Each hero also needs its weapon from `<hero>_projectile_request`, with
   the server-resolved hit model (docs/35) and its accuracy asset. Its
   attack selector is `cdl/player/<hero>/guide/<hero>_guide_attack_selector.cdl`
   (the bot-weapon track's decoder handles the Marine's).
4. **The roster and teams.** Start with the critical path's eight
   SpawnGuideBot calls: which BotID, when and on which team. Then add
   respawn per `guide_respawnparams`. RespawnComponent 120's records are
   named (death-respawn track) but their encoding is not traced.
5. **Objectives and score.** Sequential hives, hearts and harvesters are
   spawned objective entities. Their replication is NOT traced: the
   DataStoreMatch ReplicatedHiveData, ReplicatedHarvesterData and
   TeamScores records. Until they are, score server-side only. GameOver
   with the rank already works (docs/30) and ends the match at 3 hearts.
6. **Guide presentation.** CrucibleVoiceScript (144) PlayDialogueLine and
   TutorialObjectives (140) RPCs (docs/26) carry the guide's voice and
   prompts.

Stages 1–2 are small and unblock everything else. Stages 3–5 are each
larger than the whole Alpha Hunters bot work so far.

## Per-map navigation (stage 2)

`navigation.installed_mesh(level)` reads the named level's pak, and caches
one mesh per pak. `hunter_patrol.routes`, `circuits` and `squad` take the
level, and all spellings of one level share a cache entry
(`hunter_patrol.level_key`). The shard passes `solo_modes.selected().level`
in three places: the prewarm, the patrol probes and the squad. The squad
build and the patrol are guarded: a map whose bot points do not route
keeps its hunters standing where they spawned, and logs a simulation
error. It does not fail the population reply mid-handshake.

What the shipped files say (read on the devbox reference copy):
- **HotH_02's navmesh is April2018's.** Their `mnmnavmission0.bai` entries
  are byte-identical (1,397,671 bytes, SHA-256 `f0cfcc3d2079280e…`), so
  retail's guide bots walked April2018's mesh. The old hard-wiring
  (accuracy audit item 12, D3) therefore had the right mesh for HotH_02,
  but the wrong points.
- **The terrains are not the same.** On a 10 m grid over x 600–1500,
  y 600–1400, 8% of HotH_02's samples differ from April2018's by more
  than 0.5 m (p99 33 m, max 54 m). The navmesh fits both equally well
  overall (64% of sampled triangle centres within 0.75 m of either). Where
  HotH_02 was re-sculpted, bots on the stale mesh float or sink. Retail's
  bots would have done the same.
- **practicearena, tutorial_02 and tutorial_03 share one mesh**: 49,893
  bytes, 1,190 triangles. The tutorials' bot points are creature and dummy
  nodes, and most of them are off that mesh. With AI on, their hunters
  stand still (`test_every_selection_with_hunters_routes_unless_listed`).
- **Guide nodes.** Four of the twelve (33, 34, 41, 42) are off the mesh:
  34, 41 and 42 sit 6–8 m above it, and 33 is 21 m from any triangle,
  where HotH_02's terrain was lowered by about 8 m. The 09b–09d drop pods
  that the HotH_02 hunters used before are off the mesh too.
- **Placeholder spawns.** The HotH_02 hunters start on guide nodes 44, 36
  and 39. Those are the three nearest the player's drop by navigation path
  (147, 180 and 228 m; `HOTH_02_BOT_NODES`). Stage 4 replaces this with
  the critical path's SpawnGuideBot choices. Their circuit,
  44 → 36 → 39, is 784 m. Only 10 of its 813 one-metre samples lie on
  re-sculpted terrain, all by 0.6 m or less.
