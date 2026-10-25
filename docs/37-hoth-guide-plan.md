# HOTH_GUIDE: the retail bot match, and how to get there

Status: **plan only**. None of it is implemented or played.

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
   is unmeasured (docs/23). Use the existing `hoth-guide` profile with no
   bots, plus a timer GameOver (`CRUCIBLE_SOLO_MATCH_MAX_SECONDS`, from the
   match-flow track's `match_director.py`). Measure the bias from the client
   log, as for April2018. Gate: GamePlay, then the timer ends the match
   cleanly.
2. **Per-map navigation.** `navigation.LEVEL_PAK` and
   `hunter_patrol.routes()` are hard-wired to April2018 (accuracy audit
   item 12, disagreement D3). Load `levels/hotH_02/level.pak`
   `mnmnavmission0.bai` for the selected map, and spawn bots on
   `SpawnNode_Guide_EnemyBot` nodes rather than drop points.
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
