# Stage 5 findings (draft for docs/37): HOTH_GUIDE objectives and score

Read on 25 October 2026 from the devbox reference game copy, read-only.
Nothing was written under Steam, and no pak bytes were copied into the repo.
The raw decodes are deliberately not committed: they are decompiled game
scripts. Regenerate any of them read-only with the bot-weapon track's decoder,
`.venv/bin/python .worktrees/bot-weapon/tools/inspect_cdl_logic.py
~/.local/share/Steam/steamapps/common/Crucible/misc.pak <asset path>` (section 6
has the full commands). Paths below under `$SP/` were the session's scratch
folder and no longer exist.

Labels: **Read** is what a decode prints, with the node id `[n]` (or the
asset field). **Inferred** is reasoning on top of that. **Unknown** is
neither. Conventions, all from the bot-weapon notes unless marked:
`<op N>` 0 `==`, 1 `!=`, 2 `<`, 3 `<=`, 4 `>`, 5 `>=`; combiner 0 AND, 1 OR;
Condition out0 true, out1 false. New here, from consistent use across the
files and from the connection lists: ForEach out0 is the loop body and out1
is "done"; WaitForEvent out2 is "event received" (out0 and out1 are never
wired); HasEntityType and HasGameModeFlag out0 is yes (flag set), out1 is no;
GetContainerValue and GetTeamId have a failure output at out1. The nodes
GetClosestEntity, ExternalGet, QueryEntities, SphereShapeEntityQuery,
GetContainerSize, SpawnGuideBot and SpawnNodes_AIOverlord have **one** output
slot, so they continue whether or not they found anything.

## 0. Short version

1. **The guide match ends at the first heart, 12 s later, provided the
   capturer's team id is 0 or 1** (the win script reads only those two ids, and
   the player's id is not known: item 9). Capturing the heart runs
   `AdjustTeamScore(+1)` for the capturer's team (`cruciheart_interaction.cdl`
   `[41]`). The win script then tests `score >= 1`, not 3, and raises
   MatchOverEvent **12.0 s** later (`hoth_guide_win_condition.cdl` `[45]`
   `[41]` `[16]` `[34]`). TargetScore 3 and MatchPointThreshold 2 in the mode
   config are not used by that script.
2. **Only seven `guide_hoth` scripts load, and one of those is inert.** The
   scripts list names criticalpath, onspawn_hivetelegraph, gameinput_detectmode,
   ondeath_clearcreaturecamps, ondeath_playerrespawn and ondamage_hive; the win
   script loads through the mode config. `guide_onspawn_hivetelegraph.cdl` is
   loaded but wired to nothing after its first check. Nine of the thirteen
   requested scripts are never loaded (section 1.0).
3. **The critical path never touches the score.** It raises no
   MatchScoreEvent or MatchOverEvent and writes no team score. It spawns one
   hive (through the AI overlord, 45 s telegraph) and the three allies and the
   Saboteur. Its `WaitForEvent MatchScoreEvent` `[421]` is wired to nothing.
4. **Hive order.** The guide has exactly one scripted hive. Later hives come
   from the heart-capture script, which (flag `SequentialHives`, capturer's
   score still below 3) asks the overlord for the next `Spawner_Hive` with a
   **240 s** telegraph (`cruciheart_interaction.cdl` `[235]` `[175]` `[204]`).
   In the guide that call happens about 0.3 s after the first capture and the
   match is over 12 s later, so hive 2 never becomes real.
5. **The hive:** 5,000 HP (`hive.dynamicslice`), types Hive, Monster,
   Objective, flag CombatTarget. Anything of type Player can damage it,
   including the guide dummies. It heals 100 HP/s after about 20 s without
   being attacked. On death a heart spawns on its exact position (`ForceCenterSpawn`).
6. **The heart:** an Interactable of type HiveHeart, 10.0 s interaction,
   2.9 m radius, one interactor at a time, no team or ownership check. A
   completed capture sets the heart non-interactable, scores +1 for the
   capturer's team, writes status `HeartClaimed`, and the heart despawns
   about 1 s later (inferred). A failed capture re-enables the heart. Melee very
   likely interrupts a capture (inferred): every hero's quick-melee hit applies a
   `Knockback` velocity override, the type the interrupt achievements count; the
   native interrupt rule itself was not read (2.5).
7. **Harvesters** (five on HotH_02, types A to E) give essence, not score:
   10 s capture, 5 m radius, then 150 essence to the owner every 10 s
   (global heartbeat). Team score from harvesters exists only for CONQUEST.
8. **Every way the match can end** (section 2.7): heart score (capturer on team
   0 or 1), then +12 s MatchOverEvent; the 60-minute `MaxGameDuration`, then +10 s rank 2 and
   `IsMatchOver` without a MatchOverEvent; surrender (generic).
9. **Biggest unknowns:** the player's team id (the win script reads only
   teams 0 and 1); whether the 90 s first-event delay in
   `hoth_guide.sequentialgmdata` applies to the guide entity; whether
   `hive.spawntelegraphdata`'s `SuppliedSpawnOverrideInSeconds 30` overrides
   the 45 s and 240 s the scripts pass; which of the six hive nodes the
   overlord picks; what raises MatchScoreEvent (no CDL does).

## 1. Per script

### 1.0 What loads and what does not

Coverage of the "never loaded" proof. For each of the nine scripts below
there is no hit by **asset GUID** in any of the 4,610 readable entries of
`misc.pak` (the asset catalog gives the GUIDs; one text entry would not
decompress), no hit by **path** (only the asset catalog itself names them) in
`misc.pak`, `levels.pak`, `config.pak`, `gamedata.pak` or HotH_02's `level.pak`,
and no entry in HotH_02's
`resourcelist.txt` (7,699 lines; the loaded scripts are all in it). The
`objects`, `vfx`, `architecture` and `animations` paks were not searched.
Say "not referenced by any asset checked", not "proven unused".

| Script | Status | Basis |
|---|---|---|
| `hoth_guide_win_condition.cdl` | loaded, live | `EndConditionScript` of `hoth_guide.gamemodeconfig`; GUID found there |
| `guide_criticalpath.cdl` | loaded, live (172 of 230 nodes reachable) | `ScriptAssets` of `guide_hoth_scripts.cdlscriptslist` |
| `guide_ondamage_hive.cdl` | loaded, live | same list (path spelled `cdl/GameModes/Guide_HOTH/...` there; the pak name is lowercase) |
| `guide_onspawn_hivetelegraph.cdl` | loaded, **inert** | same list; 2 of 21 nodes reachable (see its entry in 1.1) |
| `guide_onspawn_hive.cdl` | never loaded | no reference |
| `guide_onspawn_hiveheart.cdl` | never loaded | no reference |
| `guide_ondeath_hive.cdl` | never loaded | no reference |
| `guide_interaction_captureheart.cdl` | never loaded | no reference |
| `guide_interaction_captureharvester.cdl` | never loaded, and broken | no reference; 3 of 13 nodes reachable |
| `guide_entitydetected_captureharvester.cdl` | never loaded | no reference |
| `guide_melee_interruptheartcap.cdl` | never loaded | no reference |
| `guide_interaction_interruptheartcap-usemelee.cdl` | never loaded | no reference |
| `guide_onmatchover.cdl` | never loaded | no reference |

The scripts list also loads `guide_gameinput_detectmode.cdl`,
`guide_ondeath_clearcreaturecamps.cdl` and `guide_ondeath_playerrespawn.cdl`
(section 1.2), and two shared blackboards: `cdl/sharedblackboards/guide_progress.sbb`
and `designer_events.sbb`. The list is carried by the entity `Guide_HOTH` in
`slices/planetaryevents/guide_hoth.dynamicslice` (`ScriptAssetList` field), which
also holds the four BotData rows and an `EventData` entry for
`assets/planetaryevents/events/event_guide_hoth.gmeventdata`. That event asset
is type `CapShieldAmplifiers`, severity Major, phases Early, Middle and Late,
tag Amplifier, and its script `cdl/planetaryevents/event_guide_hoth.cdl` only
prints "Guide event started" on spawn.

How that entity is created: `hoth_guide.sequentialgmdata` has one
`ScenarioEvents` entry, type `Guide_HOTH`, slice `guide_hoth.dynamicslice`,
and `MinDelayForFirstEvent` = `MaxDelayForFirstEvent` = **90.0** s,
`MinTimeBetweenEvents` = `MaxTimeBetweenEvents` = 10.0 s, no `GMEvents`.
**Open, two readings.** (a) Stage 4 inferred the 90 s cannot apply, or the
script would miss the drop-pod event; the entity is live from the start of
play. (b) The compact file `heart_of_the_hives_compact.sequentialgmdata` has
only `GMEvents` and still carries delays (120 s first, 10 s between), so the
delay fields govern GMEvents-only data too; that weakens (a) without
overturning it. If (b) holds, the entity appears 90 s after the event clock
starts (which clock is not decoded), the first
`CharacterFinishedExitingDropPod` may already be past, and because of the
`[677]` guard (`dropSequence == 0`) only a later drop-pod exit (a respawn)
would start the tutorial.

### 1.1 The thirteen requested scripts

Sizes, hashes and node counts for all of them are in section 6.

#### `hoth_guide_win_condition.cdl`

- **Status.** Loaded and live. 15 nodes, all reachable (13 from the enter
  trigger, 2 through `[21] OnExit`).
- **Trigger.** `[1]` MatchScoreEvent on any endpoint. The event has no
  fields, so the script reads the scores itself.
- **Flow (read).** `[1]` -> `[7]` LockProgressionSubmission -> `[13]`
  GetTeamScore (`TeamId = $User_Team0`, from
  `DataStoreMatchComponent.TeamScores`) -> `[45]` "Team 0 win check"
  `Score0 >= 1`.
  - True: `[24]` WinningTeamId = 0 -> `[30]` LosingTeamID = 1 -> `[10]`
    MakeMatchOverEvent(WinningTeamId = `$User_WinningTeamId`) -> `[16]`
    **Delay 12.0 s** -> `[34]` RaiseEvent MatchOverEvent ("Match is over").
  - False: `[37]` GetTeamScore(`$User_Team1`) -> `[41]` "Team 1 win check"
    `Score1 >= 1`. True: `[27]` WinningTeamId = 1 -> `[19]` LosingTeamID = 0
    -> `[10]` (same tail). False: no edge, the script just ends.
- **Exit (read).** `[21]` OnExit -> `[4]` UnlockProgressionSubmission. It runs
  when the script ends, after the raise or at once when nobody has a point.
- **Literals.** Both compare-to literals are u32 `1` (the hidden-literal
  blackboard has two `Condition::CompareTo` entries, both `AZ::u32 1`). The
  delay literal is float 12.0.
- **User variables (read).** `User_WinningTeamId` u32 255, `User_Team0` u8 0,
  `User_Team1` u8 1, `User_LosingTeamID` u32 0, `User_ConsolationPrize` s32 0,
  `User_ConsolationPrizePerHeart` u32 20. The last two are bound to no node.
- **Against the generic script.** `crucihearts_win_condition.cdl` (the PvP
  mode's) compares `>= 3`, raises MatchOverEvent at once, and then awards
  XP (`AwardBonusExperience` victory, heart captures, perfect game, using
  `GetGameConfigValue` keys `hoth_prestige_on_win`, `hoth_consolation_prestige`,
  `hoth_perfect_game`). The guide copy dropped the XP block and added the 12 s
  delay. **Inferred:** the two unused user variables are leftovers of that
  block.
- **Ties.** Team 0 is tested first, so if both teams had a point team 0 would
  win. Nothing cancels the 12 s delay. What Lock/UnlockProgressionSubmission
  do is unknown (probably hold back the progression/XP submission while the
  result is pending).

#### `guide_onspawn_hive.cdl` (never loaded)

- **Trigger.** `[1]` OnSpawnEvent on any endpoint -> `[14]` HasEntityType
  (spawned entity is `Hive`) -> `[27]` `Shared_hoth_taskState_defeatHive != 2`.
- **Actions.** `[17]` Delay 6.0 s -> `[23]` IncrementInteger
  (`Shared_hoth_active_items`) -> `[10]` Set it -> `[7]` NewTutorialObjectives
  `Guide_HotH_DefeatHive` -> `[4]` audio `play_ui_checklist_trans_in` -> `[20]`
  Set `defeatHive` = 1 (s32).
- **Meaning.** Older, event-driven way of showing "Defeat the Hive". The live
  critical path does this itself at `[512]`. Nothing raises or reads
  `defeatHive` in the loaded scripts.

#### `guide_onspawn_hiveheart.cdl` (never loaded)

- **Trigger.** `[1]` OnSpawnEvent -> `[32]` HasEntityType `HiveHeart` -> `[36]`
  `captureHeart == 0` (true) or `[40]` `captureHeart == 2` (false branch).
- **Actions, `captureHeart == 0`.** `[28]` Delay 1.0 s -> `[25]` voice
  `voice.guide.hiveFight.5` -> `[10]` audio -> `[16]` NewTutorialObjectives
  `Guide_HotH_TeamCaptureHeart` -> `[13]` IncrementInteger `active_items` ->
  `[22]` Set -> `[19]` Set `captureHeart` = 1 -> `[4]` ExternalGet the heart's
  `LocationComponent.InternalTransform` -> `[7]` LocalDistributedSpawning of
  **`slices/practicearena/robodummy.dynamicslice`**, Count 1, NavMesh
  `Bipeds`, `Closest`, Radius 25.0, TelegraphTime 0, owner `@Self`, BlueNoise
  inner exclusion 10, item radius 2, 5 candidates.
- **Meaning.** The earlier design spawned a practice-arena RoboDummy near the
  heart. The live design spawns the Saboteur there instead (`[133]`).

#### `guide_onspawn_hivetelegraph.cdl` (loaded, inert)

- **Trigger.** `[1]` OnSpawnEvent on any endpoint -> `[38]` HasEntityType
  `HiveTelegraph`.
- **Raw evidence that nothing follows.** The file serialises 17 connections.
  `[38]` has output slots 36 (yes) and 37 (no); neither is the source of any
  connection. `[65]` (the body's first condition) has input slot 62, which is
  the target of none. 19 of 21 nodes are unreachable from `[1]`.
- **What the dead body would do.** `[65]` `reachHive == 0` -> `[41]` Set
  `reachHive` = 1 -> `[25]` GetTimePoint +60,000 ms (`0xea60`) -> `[10]` Set
  `Shared_hoth_hiveTaskStart` -> `[44]` NewTutorialObjectives `WaitForHive` ->
  `[50]` audio -> `[34]` GetTimePoint -> `[61]` now `<` `hiveTaskStart`: wait
  (`[47]`) then complete WaitForHive (`[4]`), else complete at once. `[31]`
  MakeDesignerEvent `Shared_guide_startTask_GoToHive` (100) -> `[57]`
  RaiseEvent has no incoming connection either.
- **Effect on the live chain: none.** Its only consumers are the critical
  path's `[322]`/`[701]` (wait for event 100) and `[243]`/`[705]`
  (`hiveTaskStart`), and both sit in the critical path's dead portion.
  `Shared_hoth_hiveTaskStart` and `Shared_hoth_taskState_reachHive` are never
  written by anything that runs. The critical path's own GoToHive poll (`[391]`
  loop) replaced this script. Stage 4 recorded the designer comment "Wait for
  Hive: Moved this to guide_onspawn_hivetelegraph.cdl" over other dead nodes.
- **Not to be confused with** `cdl/gameplay/interaction/hivetelegraph.cdl` and
  `cdl/telegraphobjects/onspawn_hivetelegraph.cdl`, which run on the telegraph
  entity and do matter (section 1.3).

#### `guide_ondeath_hive.cdl` (never loaded)

- **Trigger.** `[1]` OnDeathEvent on any endpoint -> `[24]` HasEntityType
  `Hive` (victim) -> `[28]` `defeatHive == 1`.
- **Actions.** `[4]` Set `defeatHive` = 2 -> `[17]` audio
  `play_ui_checklist_set_complete` -> `[20]` CompleteTutorialObjectives
  `Guide_HotH_DefeatHive` -> `[14]` `active_items -= User_One` (s32 1) ->
  `[7]` Delay 1.0 s -> `[10]` RemoveTutorialObjectives `Guide_HotH_DefeatHive`.
- **Meaning.** Superseded by critical path `[494]`/`[85]`.

#### `guide_ondamage_hive.cdl` (loaded, live)

- **Trigger.** `[1]` OnDamageOrDotAppliedReceivedEvent on any endpoint.
- **Condition.** `[11]` HasEntityType on `targetEntityId` = `Hive`.
- **Action.** `[7]` GetTimePoint(+0 ms) -> `[4]` Set
  `Shared_hoth_lastHiveDamageTime` (u32 ms) = now.
- **Use.** Read only by the critical path `[452]`/`[713]`: if the last hive
  damage is newer than now minus 600 ms, the GoToHive search radius becomes
  999,999 m instead of 40 m (1.2 and 2.2).
- **Note.** It fires for damage to any entity of type Hive from any source, and
  for damage over time as well as hits.

#### `guide_interaction_captureheart.cdl` (never loaded)

- **Trigger.** `[1]` InteractionCompletionEvent on any endpoint -> `[27]`
  interactable has type `HiveHeart` -> `[37]` `succeeded == True` AND
  `Shared_hoth_taskState_captureHeart == 1`.
- **Actions.** `[20]` Delay 1.0 s -> `[10]` voice `voice.guide.hiveFight.7` ->
  `[7]` Set `captureHeart` = 2 -> `[23]` audio `item_complete` -> `[30]`
  CompleteTutorialObjectives `Guide_HotH_TeamCaptureHeart` -> `[17]`
  `active_items -= 1` -> `[13]` Delay 1.0 s -> `[4]` RemoveTutorialObjectives
  -> `[33]` voice `voice.guide.end.1`.
- **Meaning.** The live critical path does the same at `[697]`/`[413]`/`[628]`
  and ends with `voice.guide.end.3`.

#### `guide_interaction_captureharvester.cdl` (never loaded, and broken)

- **Trigger.** `[1]` InteractionCompletionEvent -> `[5]` interactable type
  `Harvester` -> `[37]` `succeeded == True`. **No connection leaves `[37]`.**
  The body (`[41]` `captureHarvester == 0` -> `[12]` Set `captureHarvester` = 2,
  and the complete/remove/voice `voice.guide.harvester.4` nodes) has no way in:
  10 of 13 nodes unreachable. It would have done nothing even if loaded.

#### `guide_entitydetected_captureharvester.cdl` (never loaded)

- **Trigger.** `[1]` OnSpawnEvent -> `[23]` entity type `Player` -> `[13]` Set
  `User_Player` -> a poll once per second (`[26]` Delay 1.0 s) while
  `captureHarvester == 0` (`[53]`).
- **Poll.** `[4]` player position -> `[19]` SphereShapeEntityQuery type
  `Harvester`, **radius 15.0, CheckLineOfSight True** -> `[41]`
  `ContainerSize >= 1` (`[45]`) AND `active_items < max_items` (`[49]`).
- **Action.** `[38]`/`[32]` `active_items += 1`, `[16]` NewTutorialObjectives
  `Guide_HotH_CaptureHarvester`, `[7]` audio, `[10]` Print "Guide: You're close
  to a harvester, go capture it", `[29]` Set `captureHarvester` = 1, `[35]`
  voice `voice.guide.harvester.1`.
- **Meaning.** An older proximity prompt. The live path shows the objective as
  soon as the camp step ends (`[458]`), wherever the harvester is.

#### `guide_melee_interruptheartcap.cdl` (never loaded)

- **Trigger.** `[1]` OnNonDoTDamageDealtEvent on any endpoint -> `[14]` source
  type `Player` -> `[34]` `IndicatorType == 'Melee'` -> `[38]`
  `Shared_hoth_taskState_useMelee == 1`.
- **Actions.** True: `[20]` voice `voice.guide18` (listener = the source) ->
  `[7]` Set `useMelee` = 2 -> `[23]` CompleteTutorialObjectives
  `Guide_HotH_InterruptCaptureHeart` (on the source entity) -> `[17]` audio ->
  `[30]` `active_items -= 1` -> `[4]` Delay 1.0 s -> `[10]` RemoveTutorialObjectives.
  False (`useMelee != 1`): `[26]` Set `useMelee` = 2 only.
- **Meaning.** It rewards any melee damage by any Player. It does not check that
  the target is a heart capturer. The objective is the "interrupt a capture with
  melee" lesson; see 2.5 for what interrupts a capture.

#### `guide_interaction_interruptheartcap-usemelee.cdl` (never loaded)

- **Trigger.** `[1]` InteractionStartEvent on any endpoint -> `[23]`
  interactable type `HiveHeart` -> `[34]` `interactorId != Shared_hoth_player_EntityId`
  (somebody else starts capturing) -> `[30]` `useMelee != 1`.
- **Actions.** `[16]` Delay 0.25 s -> `[38]` `active_items < max_items` ->
  `[7]`/`[4]` `active_items += 1` -> `[10]` voice `voice.guide17` -> `[19]`
  audio -> `[13]` NewTutorialObjectives `Guide_HotH_InterruptCaptureHeart` ->
  `[26]` Set `useMelee` = 1.
- **Meaning.** Pairs with the previous script; neither is loaded, so the guide
  never teaches or tests interrupts.

#### `guide_onmatchover.cdl` (never loaded)

- **Trigger.** `[1]` MatchOverEvent on any endpoint -> `[13]` Delay 5.0 s ->
  `[17]` Condition, AND over twelve `Shared_hoth_taskState_* == 2` (detect,
  clearCreatureCamp, collectXEssence, reachHive, captureHeart,
  interruptCaptureHeart, defendCaptureHeart, captureMajorObjective, ping,
  captureHarvester, useMelee, healMedStation).
- **Actions.** True: `[10]` Set `Shared_hoth_goodbye` = True -> `[7]` voice
  `voice.guide.end.2`. False: `[4]` voice `voice.guide.end.3`.
- **Meaning.** Since the live chain sets only the `taskState_critical_*`
  variables, the twelve would never all be 2. The live voice line
  `voice.guide.end.3` is played by the critical path at `[622]` instead.

#### `guide_criticalpath.cdl` (loaded, live)

- **Size.** 1,825,791 bytes, 230 nodes, 230 connections, one enter trigger,
  no exit trigger. 172 nodes are reachable; 58 are dead (portion key 0 with no
  start, as stage 4 found).
- **Trigger.** `[1]` `CharacterFinishedExitingDropPod` on any endpoint.
- **Guard.** `[677]` `Shared_hoth_startState_dropSequence == 0` (s32 default 0
  in `guide_progress.sbb`). False: nothing, so a later pod exit (respawn) does
  nothing. `[32]` sets it to 1 and `[361]` stores the event's
  `CharacterEntityId` as `Shared_hoth_player_EntityId`.
- **Steps, with the decoded literals.** Delays are the Delay nodes only.

| Step | Nodes | Waits for | Fixed delays |
|---|---|---|---|
| Intro | `[364]` SetActiveTutorialObjectives (`assets/tutorial/guideobjectives.tutorialobjectiveslist`); voice `detectMode.1` `[416]`; objective `Guide_HotH_Detect` `[544]`; `[180]` `critical_detect = 'Active'` | DesignerEvent `EventId == Shared_guide_endTask_UseDetectMode` (102) `[517]`/`[669]`, raised by `guide_gameinput_detectmode.cdl` | 5.0 `[641]`, 3.0 `[650]` |
| Detect done | voice `detectMode.3`; `critical_detect = 'Completed'` `[337]`; complete and remove Detect | none | 1.0, 1.0, then 6.0 `[170]` |
| Camp | voice `creatureCamp.1`; `GetClosestEntity` SnurtleCamp `[127]` and SpitterCamp `[145]`, `HeightenObjectiveMessaging` (duration -1); objective `ClearCreatureCamp`; `[111]` `critical_clearCamp = 'Active'` | DesignerEvent `EventId == Shared_guide_endTask_ClearCreatureCamp` (101) `[292]`/`[725]` | none |
| Camp done | `[173]`/`[520]` cancel the heightening on all camps; `critical_clearCamp = 'Completed'` `[102]`; complete and remove | none | 1.0, 1.0 |
| Harvester | voice `harvester.1`; closest `Harvester` `[222]` heightened; objective `CaptureHarvester` | InteractionCompletionEvent with `succeeded == True` `[681]` and interactable type `Harvester` `[349]`, by anyone | none |
| Harvester done | cancel heightening; complete; remove; voice `harvester.4` `[298]`; voice `essence.1` `[568]`; voice `essence.4` `[547]`; objective `CollectEssence` `[665]` | player `LevelComponent.Level >= 2` `[523]`/`[733]`, else OnPlayerLevelupEvent `level >= 2` `[345]`/`[689]` | 1.0, 0.5, 10.0 `[99]`, 5.0 `[550]` |
| Level 2 | voice `essence.6`; complete and remove `CollectEssence` | none | 1.0, 1.0 |
| Hive telegraph | **`[64]` SpawnNodes_AIOverlord**: Category `Spawner_Hive`, OccupySpawn True, SingleUse True, Count 1, **TelegraphTime 45**, DeathFogClearTime 0.0, AdditionalDeathFogRadiusReduction 0.0 | none | 1.0 `[610]`, voice `hiveFight.1`, 3.0 `[526]` |
| GoToHive | `[70]` closest HiveTelegraph, heightened 5.0 s; objective `GoToHive` `[136]`; poll loop (below) | hive or telegraph in range | 1.0 poll `[391]` |

  The fixed delays from the enter trigger to `[64]` add to 8.0 + 8.0 + 2.0 +
  16.5 + 2.0 = **36.5 s**, plus every wait. Stage 4 got the same figure.
  A slip in the camp step: `[388]` heightens `GetClosestEntity_1` (the
  Snurtle camp) a second time, so the Spitter result `[145]` -> `GetClosestEntity_2`
  is never used.
- **GoToHive poll (read).** Each pass: `[186]` player position; `[452]`
  GetTimePoint with offset `0xfffffda8` (signed -600 ms); `[713]`
  `Shared_hoth_lastHiveDamageTime > now - 600 ms`: true `[44]` radius =
  999,999.0; false `[355]` radius = 40.0 (the default of `User_HiveSearchRadius`
  is also 40.0). `[240]` SphereShapeEntityQuery of `HiveTelegraph` at the player,
  `CheckLineOfSight False`; `[741]` `ContainerSize == 1` (exactly one): `[284]`
  take index 0, `[73]` its transform, `[409]` Set `User_HiveTransform`, then
  `[541]` complete GoToHive. If not exactly one telegraph, `[231]` the same query
  for `Hive`; `[693]` `== 1`: `[160]`/`[202]`/`[16]` Set `User_HiveTransform`,
  `[541]`. Otherwise back to `[391]`. The `== 1` tests mean two telegraphs or two
  hives in range stall the poll.
- **Allies (read, matches stage 4).** `[541]` -> `[479]` audio -> `[314]` Delay
  1.0 -> `[325]` remove GoToHive -> `[268]` Delay 0.5 -> `[139]` voice
  `hiveFight.3` -> `[79]` Delay 1.0 -> `[26]` GetTeamId(player) -> `[252]`
  SpawnGuideBot DuelistDummy -> `[485]` Delay 0.3 -> `[580]` HeroDummy ->
  `[583]` Delay 0.3 -> `[7]` InventorDummy -> `[488]` Delay 0.1 -> `[538]`
  QueryEntities flag `BotControlled`. For each bot `[38]` AddToTeam (player's
  team) and `[19]` AddEntityToAITargetBlackList (the player; reasons Sensing,
  Alerted, Damaged; duration 1e9 s), and an inner loop `[302]`/`[673]`/`[331]`
  blacklists every other bot. All three calls: Transform `User_HiveTransform`,
  Radius 25.0, inner exclusion 20, item radius 2, 5 candidates,
  RadiusPercentage 1.0, ForceCenterSpawn False, OwnerId the invalid entity id,
  EntityFlags `BotControlled`, NavMesh `Bipeds`. Confirmed. The ring is centred
  on `User_HiveTransform`, which `[409]` sets from the single HiveTelegraph in
  range or `[16]` from the single Hive; the hive is expected to spawn where its
  telegraph stood (I).
- **Then (read).** `[76]` Delay 1.0 -> `[29]` QueryEntities `Hive` -> `[737]`
  count `>= 1`: true: `[124]` heighten each hive (-1), `[512]` objective
  `DefeatHive`, `[595]` wait for OnEntityKilledEvent. False: `[265]` objective
  `WaitForHive`, `[607]` wait for an OnSpawnEvent of type Hive, then
  `[379]`/`[183]`/`[277]` and back to `[512]`.
- **Hive killed (read).** `[595]`/`[262]` victim type `Hive`: `[192]` query
  every `BotControlled` entity -> `[632]` ForEach `[189]` **Despawn** (every
  bot, not only the allies; DespawnNetworkedChildren False) -> `[255]` Delay
  0.25 -> `[461]` QueryEntities `HiveHeart` -> `[602]` GetContainerValue index 0
  -> `[562]` ExternalGet its `LocationComponent.InternalTransform` -> `[133]`
  **SpawnGuideBot SaboteurDummy**, Transform = that, Radius 25.0, inner
  exclusion 5, item radius 1, 5 candidates, OwnerId invalid, `BotControlled`,
  `Bipeds`. Then `[494]` complete DefeatHive, `[613]` audio, `[216]` Delay 1.0,
  `[85]` remove, `[156]` Delay 1.0, `[271]` voice `hiveFight.5`, `[553]` objective
  `TeamCaptureHeart`, `[130]` audio, `[150]` wait for an InteractionCompletionEvent.
- **Heart captured (read).** `[697]` `succeeded == True` -> `[413]` interactable
  type `HiveHeart` -> `[628]` Delay 0.5 -> `[509]` complete TeamCaptureHeart ->
  `[311]` audio -> `[464]` voice `hiveFight.7` -> `[574]` Delay 1.0 -> `[559]`
  remove -> `[82]` Delay 5.0 -> `[622]` voice `end.3`. **`[622]` has no
  outgoing connection: the script ends.** So its last act is 6.5 s after the
  heart's completion event.
- **Dead ends the script cannot recover from (read from the raw connection
  lists, not the outline).**
  - `[602]` GetContainerValue has output slots 0 and 1; only out0 is wired. If
    no `HiveHeart` entity exists 0.25 s after the hive's kill event, the chain
    stops: no Saboteur, no DefeatHive completion, no TeamCaptureHeart wait, no
    end voice. **The match still ends:** heart capture and scoring run in
    `cruciheart_interaction.cdl`, not here.
  - `[26]` GetTeamId has a wired out0 only: a player with no team would stop the
    chain before the first ally.
  - `[160]` and `[284]` (poll) are guarded by the `== 1` checks.
- **Dead portion (58 nodes, never run).** Four `LocalDistributedSpawning`
  nodes (`[352]` duelist, `[427]` hero, `[41]` inventor, `[440]` saboteur
  dummy slices), `[424]` SpawnSlice of `major_damageamplifiers.dynamicslice`
  (TelegraphTime 30), `[246]` a GuideEnemyBotSpawner sphere query at the
  origin, `[421]` WaitForEvent MatchScoreEvent (no connection in or out),
  `[322]`/`[701]` the GoToHive event wait, the CaptureAmp objectives, and the
  `Shared_PlayersWhoDamagedMe` checks. Everything stage 4 listed is confirmed.
- **User variables (read).** `User_HiveSearchRadius` float 40.0,
  `User_HiveTransform` identity at the origin, `User_RoboOffset` (1,0,0) and
  `User_RoboTransform` identity (both unbound).
- **Events.** The script has no RaiseEvent node: it only waits (DesignerEvent,
  InteractionCompletionEvent, OnEntityKilledEvent, OnSpawnEvent,
  OnPlayerLevelupEvent). It also has no score node.
- **Correct or confirm for docs/37:** the four SpawnGuideBot calls, their rings,
  teams and triggers in the table in the stage 4 findings are all confirmed:
  three allies (Duelist, Hero, Inventor) on the player's team, ring 25 / 20 / 2
  around the hive, spawned 2.5 s, 2.8 s and 3.1 s after GoToHive completes; one
  Saboteur ring 25 / 5 / 1 around the first HiveHeart, 0.25 s after a Hive's
  kill event. That the Saboteur is hostile is **inferred** (no AddToTeam and no
  blacklist after `[133]`, AI TargetTypes `Player` only, the designers' "enemy
  bot" comment); its team number is unknown. Additions: the despawn covers all
  `BotControlled` entities; `[602]` is unguarded; the script never writes a score
  or raises a score or match event.

### 1.2 Other loaded guide scripts

- **`guide_gameinput_detectmode.cdl`** (live branch). `[1]`
  `GameInputAction_DetectMode` on any endpoint -> `[59]` `critical_detect ==
  'Active'` -> `[52]` voice `voice.guide.detectmode.2` -> `[22]` Delay 3.0 ->
  `[12]` MakeDesignerEvent(`Shared_guide_endTask_UseDetectMode`, 102) ->
  `[19]` RaiseEvent. The rest of the file is an exit-trigger check of input
  states (jump, crouch, fire, interact, medpack).
- **`guide_ondeath_clearcreaturecamps.cdl`** (live branch). `[1]` OnDeathEvent
  on any endpoint -> `[83]` `clearCreatureCamp == 1` OR `critical_clearCamp ==
  'Active'` -> `[65]` killer is `Player` -> `[45]` victim is `Monster` -> `[41]`
  victim position -> `[8]` SphereShapeEntityQuery `Monster` radius 20 -> `[91]`
  count `> 0` -> `[79]` `critical_clearCamp == 'Active'` -> `[5]` RaiseEvent
  DesignerEvent id `0x357607c5` (896,927,685). **No loaded guide script listens
  for that id** (it appears only in this file, the never-loaded
  `guide_onessencegained_collectxessence.cdl` and two tutorial_03 scripts), so
  killing a camp's monsters does not end the camp step. The step ends on event
  101, raised by `cdl/creatures/camp_iconmanager_basic.cdl` (1.3). The other 18
  nodes (objective bookkeeping) are unreachable.
- **`guide_ondeath_playerrespawn.cdl`** (live). `[1]` OnDeathEvent on any
  endpoint -> `[14]` victim is `Player` -> `[18]` `Shared_hoth_respawn == False`
  -> `[7]` Delay 3.0 -> `[4]` Set `respawn` = True -> `[10]` voice
  `voice.guide23` to `@Self`. Voice only; touches no bot.

### 1.3 Generic scripts that carry the real rules

These are not in `guide_hoth/`. They run on the hive, telegraph, heart and
harvester entities and on the `MatchDataStore` entity, because those slices
list them, and they apply in the guide through the mode flags and values
(table in 2.1). Each is in HotH_02's resource list. Node ids are from the
decoder. `HasGameModeFlag` out0 is "flag set".

**Hive telegraph entity** (`slices/spawntelegraphobjects/hivetelegraph.dynamicslice`,
types `Objective`, `HiveTelegraph`, flag `HiveTelegraph`; scripts
`hivetelegraph.cdl`, `onspawn_hivetelegraph.cdl`,
`crucihearts_seteventascritical.cdl`; `TelegraphData`
`assets/spawntelegraphs/hive.spawntelegraphdata`).
- `hive.spawntelegraphdata` (read): `SliceToSpawn`
  `slices/creatures/hive/hive.dynamicslice`, `SpawnTimeOffset` 0.0,
  `DespawnTimeOffset` 0.0, **`CDLTriggerOffset` -30.0**,
  **`SuppliedSpawnOverrideInSeconds` 30.0**, `TransferOwnership` True, no
  script, no VFX. Whether the 30 is a default for callers that supply no time
  or an override of the 45 s and 240 s that the scripts pass is **unknown**.
- `onspawn_hivetelegraph.cdl` (read): `[1]` OnSpawnEvent (entity-local) ->
  `[5]` flag `HivesSpawnHearts` -> `[11]` ChangeObjectivePriority `Critical` ->
  `[33]` GetDataStoreMatchComponentEntityId -> `[30]` ExternalGet
  `Shared_NumHivesSpawned` from it -> **`[8]` SetReplicatedHiveStatus
  (HiveEntityId = the invalid id, HiveId = NumHivesSpawned, Status
  `Spawning`, OwningHeartTeamId 255)** -> `[24]`/`[27]`/`[21]` now +
  `User_TWOMIN_TWENTYSIXSEC_in_ms` (u32 146,000) -> `[17]` WaitUntilTimePoint
  -> `[14]` LocalDistributedSpawning of
  `hivetelegraph_blackoutonly.dynamicslice` (Count 1, radius 2.0). With a 45 s
  guide telegraph the 146 s wait cannot finish before the hive appears;
  `hive_onspawnactive.cdl` despawns any `Blackout` entity within 10 m.
- `hivetelegraph.cdl` (read): `[1]` TelegraphOffsetEvent -> `[19]`
  `HivesSpawnHearts` -> `[4]` ChangeObjectivePriority `Critical` -> for every
  valid team id `[25]`/`[15]`: `[28]` GetAllMembersOfTeam -> `[11]` first
  member -> `[22]` QueueVoiceEvent `voice.auto.hiveReminder` (listener the
  invalid id). The event fires at the `CDLTriggerOffset`.

**Hive** (`slices/creatures/hive/hive.dynamicslice`, entity name `Hive_Standard`).
- Data (read): types `Hive`, `Monster`, `Objective`; flags `AffectedByDeathFog`,
  `CombatTarget`; **Health max 5000.0, start 5000.0, ChangePerSecond 0.0**;
  capsule radius 4.95, height 14.95; `IncomingDamageMultiplier` 1.0;
  `EntityResourcesDataAssets` `hive.resourcesdata` and
  `common_creature.resourcesdata`; blackboards `baseplayer.sbb`, `hive.sbb`,
  `creature.sbb`; scripts `hive_ondeathspawnessence`, `hive_onspawnactive`,
  `hive_deathdespawn`, `deathfog_creature_despawn`, `ondeath_remove_collision`,
  `hive_ondamage`, `crucihearts_seteventascritical`, `hive_onspawneffects`,
  `hive_damage_accumulation`, `evolution_onspawn`, `evolution_onlevelup`. A
  `dataPerGameMode` table has `CRUCIHEARTS` and `HEART_OF_THE_HIVES` rows
  (radius 250.0) and no `HOTH_GUIDE` row. No team and no ownership.
- `hive_onspawnactive.cdl`: `[49]` Delay 0.05 -> `[58]` mesh visible -> `[7]`
  Delay 0.5 -> `[64]` SpawnSlice `hive_aoe_damagefield.dynamicslice` ->
  `[46]` `HivesSpawnHearts`: `[61]` query `Blackout` within 10 m, `[23]`
  despawn the first -> `[73]`/`[67]` read `Shared_NumHivesSpawned` from the
  MatchDataStore -> `[4]` `Shared_MyHiveIndex` = it -> **`[52]`
  SetReplicatedHiveStatus(HiveEntityId `@Self`, HiveId MyHiveIndex, Status
  `Spawned`, OwningHeartTeamId 255)** -> `[14]`/`[32]` write
  `NumHivesSpawned` = index + 1 -> `[20]` priority `Critical` -> `[39]`..`[10]`
  an ActiveWindow with 1.0 s delay and critical-point particles -> `[55]`
  `is_active` = True. So hive 1 has index 0, and the status write happens
  about 0.55 s after the hive spawns.
- `hive_damage_accumulation.cdl`: on OnNonDoTDamageReceivedThisFrameEvent, if
  `Shared_IsAttacking == False`, for each damage source: type `Player` or
  `PlayerPet` is added to `Shared_InstigatorTargets` if new (`[5]`/`[24]`) and
  then raises DesignerEvent **808 (`0x328`)** (`[28]`); a source of any other
  type except `Monster` raises 808 without being added; a `Monster` source does
  nothing. The guide's four dummies are typed `Player` (their slices list
  `Base`, the hero type and `Player`), so they trigger the hive's defence.
- `hive_ondamage.cdl` (event 808): `[64]` set resource `HiveOutOfCombat` = 0,
  `is_aiming` True, 1.0 s, if Health > 0: spawn `hive_aoe_damagefield`
  (TelegraphTime 2, radius 0.1). Wasps by Health: **`<= 1500` 14 wasps 0.2 s
  apart, `<= 2800` 10 wasps 0.33 s, otherwise 7 wasps 0.5 s**
  (`User_*Wasps`, `User_LowHealthThreshold_Float` 1500.0,
  `User_MedHealthThreshold_Float` 2800.0). Then a 30 m query for Players,
  `HiveAttack(MaxWasps, WaspSpawnHeightOffset 5.0, 12 attachment points
  HiveWaspSpawner01..)`, crouch, 7.667 s, clear the target list,
  `IsAttacking` False, 2.0 s, repeat while new instigators exist.
- `hive_outofcombatregen.cdl`: the resource `HiveOutOfCombat` (0..20, +1.0/s,
  notification band 19.5 to 20.0) runs it: heal particles, then
  **DoHealing 100 (min = max) every 1.0 s** until Health equals its maximum. Its
  exit trigger stops it on any hit with total damage `>= 1.0`.
- `hive_ondeathspawnessence.cdl`: `[1]` OnDeathEvent -> `[22]` priority `None`
  -> `[60]` `HivesSpawnHealingZonesOnDeath` (not set) -> `[56]`
  `HivesSpawnHearts` -> **`[11]` LocalDistributedSpawning
  `slices/interactive/cruciheart.dynamicslice`**, Count 1, `Closest`, Radius
  1.0, TelegraphTime 0, owner `@Self`, 20 candidates, inner exclusion 0, item
  radius 0, **ForceCenterSpawn True** -> `[15]` ForEach spawned heart: **`[37]`
  SetReplicatedHiveStatus(`@Self`, MyHiveIndex, `Heart`, 255)**, `[77]`
  ExternalSet the heart's `Shared_MyHiveIndex` = this hive's -> done: `[74]`
  `HiveNoEssence`. The mode sets it, so the essence-eruption block (`out1`:
  `Shared_HiveEssence` split into pools fired as
  `essenceprojectile_large.projectile`) does not run in the guide.
- `hive_deathdespawn.cdl`: `[1]` OnDeathEvent with `entityId == @Self` ->
  `is_dead`, audio -> 0.5 s -> despawn `Shared_MyDamageField` -> KillCharacter
  -> 0.5 s -> dissolve particles -> 4.5 s -> **Despawn self** (5.5 s after
  death).

**Heart** (`slices/interactive/cruciheart.dynamicslice`: types `Interactable`,
`HiveHeart`, `Objective`; flag `AffectedByDeathFog`; scripts
`cruciheart_onspawn`, `crucihearts_seteventascritical`,
`cruciheart_interaction_hoth_voiceover`, `cruciheart_capturedelaydespawn`;
`InteractableData` `assets/gamesystems/interaction/cruciheart.interactabledata`).
No health component was found in the slice.
- `cruciheart.interactabledata` (read): `InteractableType` `CruciHeart`,
  `allowMultipleInteractions` False, **`interactionRadius` 2.9**,
  **`interactionDuration` 10.0**, `AllowsLoopingInteractions` False, validation
  `cruciheart_interaction_validation.cdl`, interaction
  `cruciheart_interaction.cdl`.
- `cruciheart_interaction_validation.cdl`: `[1]` InteractionEvalutationEvent
  (sic) -> `[14]` `interactorEntityId != 0x00000000FFFFFFFF` -> `[10]`
  ReportInteractionResult `PickUp`. No team, ownership, state or distance test.
- `cruciheart_onspawn.cdl`: `[13]` Delay 0.05 -> `[10]` priority `Critical` ->
  `[29]` audio `music_hive_active` -> `[17]` flag `HeartHasVulnField` (not set
  in the guide). When set it plays an aura and, every second, applies
  `IncomingDamageMultiplier` 1.25 for 1.0 s to Players with `CombatTarget`
  within 4 m.
- `cruciheart_interaction.cdl` (630,121 bytes, entity-local
  InteractionStartEvent):
  - Start: audio and capturing VFX, voice `voice.auto.Hivecapturing`,
    `User_InteractionTime` = (`interactionEndTimePoint` - now) / 1000 (default
    10.0). If it is `> 9.0` (`[223]`) the normal capture VFX plays; otherwise the
    "faster" VFX plays and `Shared_MusicTriggerDelay_Heart` = time - 7.0
    (`User_ShortMusicApexTime`). So the capture time can be shortened below 10 s
    by something outside the script. Then `User_InteractorId` and
    `Shared_Heart_InteractorID` = the interactor, `Shared_IsCapturing` = True,
    `[208]` StartScript `cruciheart_interaction_hoth_voiceover.cdl` (event id
    1928, `0x788`; music only), and `[9]` WaitForEvent InteractionCompletionEvent.
  - On completion by the same interactor (`[231]`): `[215]` `succeeded == True`:
    1. `[47]` `InteractableComponent.AllowsInteractions` = False.
    2. `[20]` GetTeamId(interactor) -> **`[41]` AdjustTeamScore(team,
       ScoreChange +1)** ("Increment team score for collector").
    3. **`[112]` SetReplicatedHiveStatus(HiveEntityId the invalid id, HiveId
       `Shared_MyHiveIndex`, Status `HeartClaimed`, OwningHeartTeamId the
       capturer's team).**
    4. `[53]`/`[164]` read that team's score; **`[235]` `score < 3`** (u32
       literal 3). Only when true does the rest run. When false (score 3 or
       more) nothing follows.
    5. Voice: `[194]` `voice.yourteam.heartcaptured` to the capturer's first team
       member, `[167]` `voice.enemyteam.heartcaptured` to the first member of
       the other team (`User_NonCapturingTeamId`, u8, default 0, set to 1 only
       when the capturer is team 0).
    6. `[65]` query entities of type `GMEvent` with flag `Evolution`; if one
       exists, increment its `Shared_EvolutionCurrentLevel` (`[197]`/`[171]`/`[92]`).
       Its absence (`[143]` out1) goes straight on. The guide's
       `hoth_guide.sequentialgmdata` has no Evolution event.
    7. `[136]` Delay 0.3 s -> `[188]` flag `RespawnCampsBasedOnHive` (not set in
       the guide; when set, `SpawnNodes_MatchStart` and designer event
       `0x29b92709` = 700,000,009).
    8. **`[175]` flag `SequentialHives`** (set in the guide) -> **`[204]`
       SpawnNodes_AIOverlord, Category `Spawner_Hive`, OccupySpawn True,
       SingleUse True, Count 1, TelegraphTime 240**, DeathFogClearTime 0.0,
       AdditionalDeathFogRadiusReduction 0.0 ("CAMPS spawn hive") -> `[178]`/`[99]`
       RaiseEvent designer id `0x020441a7` (33,833,383, "SENDING EVOLVE EVENT").
  - On failure (`succeeded == False`): the loop ends and the exit path runs.
  - Exit trigger (`[38]` OnExit, unwired in the outline): audio stop, particles
    stop, `Shared_IsCapturing` = False; `[227]` `User_IsSuccessful`: true plays
    `music_hive_captured`, the success VFX, prints "Interaction was successful"
    and **raises DesignerEvent `0x004dfc90` (5,110,928)**; false plays the fail
    audio and `music_hive_reset`, prints "Interaction was interrupted" and
    **sets `AllowsInteractions` = True again**, so the heart can be recaptured.
- `cruciheart_capturedelaydespawn.cdl`: on a DesignerEvent, audio
  `play_interact_dissolve_3p`, 1.0 s, stop audio, stop the capturing particles,
  **Despawn `@Self`**. Its event id filter is not visible in the decode; the
  only other place that carries `0x004dfc90` is the raise above, so this is
  **inferred** to be its trigger. The heart therefore despawns about 1 s after a
  successful capture.

**Harvester** (`slices/interactive/harvester_a..e.dynamicslice`: types
`Harvester`, `Interactable`, `Objective` plus a letter A to E; flag
`AffectedByDeathFog`; scripts `harvester_deathfog`, `harvester_heartbeat`,
`harvester_capture_anim_driver`, `harvester_area_trigger(_exit)`,
`harvester_onspawn`, `harvester_onownershipchanged`,
`conquest_setharvestericon`, `interactable_icon_onspawn`,
`medstation_spawn_in_mode`; `InteractableData` `harvesterinteractabledata`).
- `harvesterinteractabledata` (read): type `Harvester`, **radius 5.0, duration
  10.0**, no multiple interactions, no looping, validation
  `harvester_interaction_validation.cdl`, interaction
  `harvester_interaction.cdl`. (The mini harvester is 3.5 m and 4.0 s; not used
  by the guide.)
- `harvester_onspawn.cdl`: flag `NoHarvesters` -> despawn; entity type `C`: flag
  `RemoveCentralHarvester` -> despawn; otherwise (and for non-C, unless
  `OnlyCentralHarvester`) **`[12]` SetReplicatedHarvesterStatus(`@Self`,
  HarvesterId `HarvesterIdComponent.Id`, Status `InPlay`, OwningTeamId 255,
  CapturerEntityId invalid)**, `is_active` True, 5 s later the beam particles.
  None of those flags is set in the guide, so all five stay.
- `harvester_interaction_validation.cdl`: `Shared_Locked` (`harvester.sbb`,
  default false) true -> `AlreadyOwned`; else owner (OwnableComponent)
  differs from the interactor: allied owner -> `OwnedByAlly`, else `Capture`;
  owner is the interactor -> `AlreadyOwned`; no owner -> `Capture`.
- `harvester_interaction.cdl`: on start, optional megadrone damage (flag
  `HarvestersAlertMegadrones`, not set). On a successful completion by the same
  interactor: **`[23]` SetOwner(`OwnableComponent.BecomeOwnedRequests`,
  interactor)**. With `HarvestersAreCamps` (not set): 2000 essence, 120 s lock.
- `harvester_onownershipchanged.cdl`: on a new, valid owner: `[35]` GetTeamId ->
  `[31]` **SetReplicatedHarvesterStatus(`@Self`, id, `InPlay`, OwningTeamId
  `$User_NewOwnerTeamId`, CapturerEntityId = the new owner)**. **Quirk (read):**
  `User_NewOwnerTeamId` (u8, default 255) has one writer, `[49]`, whose Set
  target is `$GetTeamId_1.TeamId` and whose value is `$User_NewOwnerTeamId`,
  i.e. the opposite of the intended direction. If the Set node assigns
  target := value as it does everywhere else in these files, the team id
  written to the replicated record stays 255 and only `CapturerEntityId` carries
  the owner. **Inferred** until a live client shows it.
- `harvester_heartbeat.cdl`: `GameplayHeartbeatEvent` of type `Harvester` while
  `Shared_alive` -> **GrantEssence to the owner**: `GetGameModeValue
  HarvesterEssenceOverrideAmount` if the mode defines it (the guide does not),
  else `Shared_HarvesterEssencePerTick` (`harvester.sbb`, u32 **150**). The
  `GameplayHeartbeat` asset `harvesterheartbeat.gameplayheartbeat` is type
  `Harvester`, **`SecondsBetweenEvents` 10.0, `UseGlobalTimer` True**, and the
  MatchDataStore slice starts it (`InitialHeartbeats`). **Team score only in
  the old CONQUEST paradigm** (`GetGameMode` `CONQUEST`, `CONQUEST_8V8` or
  `CONQUEST_8V8_COLLAPSE`, and `HarvesterDifferentialScoring` not set; with that
  flag set the scoring moves to `harvestercontroldifferentialscoring.cdl`).
  Neither applies in the guide.
- `harvester_deathfog.cdl`: on DeathFogUpdateEvent with no active interaction
  and `Shared_alive`: raises HarvesterDisabledByDeathFogEvent, clears
  `AffectedByDeathFog`, stops the heartbeat, `Shared_alive` False,
  **SetReplicatedHarvesterStatus `InDeathFog`** (owner cleared), and grants
  `Shared_DeathFogged_Essence_GrantAmount` (2000) to the owner outside CONQUEST.
  Death fog timeline: not read.

**MatchDataStore entity** (`slices/crucible/match/matchdatastore.dynamicslice`,
type `MatchDataStore`; blackboards `gamemodes.sbb`, `gmevents.sbb`; scripts
`matchdatastore_onspawn`, `harvestercontroldifferentialscoring`,
`matchover_set_teamranks_and_endmatch_flag`, `endgame_maxdurationreached`,
`objectivespitterguards`, `crucihearts_autovoice`, `conquest_autovoice`,
`surrender`).
- `matchover_set_teamranks_and_endmatch_flag.cdl`: `[1]` MatchOverEvent ->
  `[66]` `Shared_MatchEnded == False` -> `[35]` set it True -> for each valid
  team and each member: `WinningTeamId` match: **`SetPlayerRank(1)`**, voice
  `player.victory.team`; otherwise **`SetPlayerRank(2)`**, voice `player.lose`;
  both get the `Invulnerable` buff (999,999 s) and lose the `CombatTarget` flag
  -> `[29]` **`DataStoreMatchComponent.IsMatchOver` = True** -> every
  `Monster`: cloak particles, 3.0 s, Despawn.
- `endgame_maxdurationreached.cdl`: OnSpawnEvent (the MatchDataStore's) ->
  `GetGameModeValue MaxGameDuration` x 60 (the script's comment: "converting
  from minutes to seconds"; 7,200 s when the value is absent) -> Delay ->
  voice `voice.gamemaxduration` -> 10.0 s -> every Player `SetPlayerRank(2)`
  (`NumPlayersInMatch` in FreeForAll) -> `IsMatchOver` = True. **No
  MatchOverEvent.** The guide's value is 60.
- `surrender.cdl`: `[1]` SurrenderedEvent -> the surrendering entity's team is
  `User_Team0`: winner 1, loser 0 (else the reverse) -> flags
  `SurrenderComponent.HasEnemySurrendered`/`IsSuccessful` on the members ->
  `[49]` Delay 3.0 -> MakeMatchOverEvent -> RaiseEvent MatchOverEvent.
- `matchdatastore_onspawn.cdl`: on the first Player spawn
  (`Shared_IsReady == False`) loops team ids 0 to 7 with SetTeamScore =
  `GetGameModeValue InitialScore` + 0, then 0.5 s and `Shared_IsReady` True.
  The guide's `GameModeValues` has no `InitialScore`, and `[29]` has no wired
  failure output, so the loop probably never runs and the scores keep their
  component default. **Inferred.**

**Camp icon manager** (`cdl/creatures/camp_iconmanager_basic.cdl`, raises the
event the critical path waits for in the camp step): on the GamePlay phase,
after a 10.0 s blackout window, every 4,000 ms (`User_LoopDelay_millisec`) it
queries `Monster` entities within 30 m of the camp: **none** -> raise
DesignerEvent `Shared_guide_endTask_ClearCreatureCamp` (101). The guide's
`CampRespawnTime` is 300 and `CampRespawnTelegraph` 60, so the camp internal
timers are used and the camp respawns. **Inferred:** because each camp raises 101
whenever its monsters are gone, the camp step can end without the player killing
anything if some camp is already empty when the step becomes active.

## 2. Consolidated rules

Each rule ends with its status: R for Read, I for Inferred, U for Unknown (or
a mix). Node ids point to section 1.

### 2.1 Mode flags and values that gate the scripts

`hoth_guide.gamemodeconfig` lists 17 flags: `HiveNoEssence`,
`AddRespawnTimeByStage`, `RetainOwnershipOfOwnableInteractablesOnPlayerDeath`,
`NoPlayersLeftCount`, `KeepMedpacksUponRespawn`, `FlatKillEssence`,
`ScenariosAndEvents`, `GuideHeartOfTheHives`, `HivesSpawnHearts`,
`DistributedEssenceEruption`, `MinimalModeCamps`, `SequentialHives`,
`CampIconsAlwaysOn`, `AmpsOnlyCenter`, `ForceCrossMapDropSpawns`,
`ForceSplitEssence`, `RespawnPointShields`. Against what the scripts test:

| Flag tested | Where | In the guide | Effect |
|---|---|---|---|
| `HivesSpawnHearts` | `hive_onspawnactive` `[46]`, `hive_ondeathspawnessence` `[56]`, `onspawn_hivetelegraph` `[5]`, `hivetelegraph` `[19]` | set | hive index and status writes; heart spawns on hive death |
| `SequentialHives` | `cruciheart_interaction` `[175]` | set | next hive after a capture while score < 3 |
| `HiveNoEssence` | `hive_ondeathspawnessence` `[74]` | set | no essence eruption on hive death |
| `GuideHeartOfTheHives` | only the never-loaded `hive_telegraph_guide_prompt.cdl` | set | no CDL effect found; any native use unknown |
| `HivesSpawnHealingZonesOnDeath` | `hive_ondeathspawnessence` `[60]` | not set | no healing zone |
| `HeartHasVulnField` | `cruciheart_onspawn` `[17]` | not set | no vulnerability field around the heart |
| `RespawnCampsBasedOnHive` | `cruciheart_interaction` `[188]` | not set | no camp respawn on capture |
| `NoHarvesters`, `RemoveCentralHarvester`, `OnlyCentralHarvester` | `harvester_onspawn` | not set | all five harvesters stay |
| `HarvestersAreCamps`, `HarvestersAlertMegadrones`, `FreeForAll` | harvester scripts | not set | ownership as normal; no 2000-essence camp mode |
| `HarvesterDifferentialScoring` | `harvester_heartbeat` `[52]` | not set | no harvester score |

`GameModeValues` in the guide config (13): `PlayerDeathEssencePercent` 50,
`SolarFlareDeltaShields` -3, `SwarmDeltaBubbles` 5, **`MaxGameDuration` 60**,
`SpittersDeltaPatches` 2, `PortableSiphonEssenceOverrideAmount` 75,
`AlwaysNAmps` 1, `AmpRequiredBuffer` 110, `DamageAmpPercentBuffOverrideAmount` 8,
`HealthAmpPercentBuffOverrideAmount` 12, `AmpTelegraphOverride` 30,
**`CampRespawnTime` 300**, **`CampRespawnTelegraph` 60**. Absent, and relevant:
`InitialScore`, `HarvesterEssenceOverrideAmount`, `PointsForKill`. The PvP
config differs in a few values (for example `MaxGameDuration` 59,
`AmpTelegraphOverride` 150) and in `EndConditionScript` (generic win script),
`AIOverlordData` (`heartofthehives_no_overlord`), `SequentialGMData`,
`GuideEntityDetectionRules` (none), `MinimumPlayers` 8. Both configs carry
`MatchScoreData` TargetScore 3 and MatchPointThreshold 2. (R)

### 2.2 Hive order and timing

1. **The guide data schedules no hive.** `hoth_guide_no_overlord.aioverlordspawndata`
   has only `Spawner_Healthpack` (10 each, telegraph 0) and
   `Spawner_HealthPlant` (12 each) sets at `WhenToSpawn` 180, 445, 715, 985,
   1225, 1525, 1795 and 2065 s. The PvP `heartofthehives_no_overlord` file has
   a hive set at **40 s**: Category `Spawner_Hive`, slice
   `hivetelegraph.dynamicslice`, Count 1, TelegraphTime 240,
   AdditionalDeathFogRadiusReduction 20, OccupySpawn and SingleUse True. (R)
2. **Hive 1 is raised by the critical path** at `[64]`, after the player has
   used detect mode (event 102), a camp is clear (event 101), a harvester has
   been captured (a successful InteractionCompletionEvent on a `Harvester`) and
   the player is level 2 or more. Category `Spawner_Hive`, OccupySpawn True,
   SingleUse True, Count 1, TelegraphTime 45. The fixed delays before it total
   36.5 s. (R)
3. **Which node.** HotH_02 has six hive overlord nodes (docs/23, entries
   1605..1610): SWAMP (1229.125, 821.033, 18.625), LOG (989.551, 1238.868,
   52.205), RESEARCH (1280.411, 1046.880, 22.875), GROTTO (787.559, 1054.326,
   50.000), FALLS (905.220, 720.125, 10.125), SPACEPORT (1196.556, 1230.998,
   20.500). The call names none, and the overlord's choice is server code. (U)
4. **Telegraph, then hive.** The telegraph entity appears at the node; its
   script writes status `Spawning` with HiveId = `Shared_NumHivesSpawned` (0 for
   the first). The hive slice spawns when the telegraph ends. The script passes
   45 s, but `hive.spawntelegraphdata` also has `SuppliedSpawnOverrideInSeconds`
   30.0 and a `CDLTriggerOffset` of -30.0 (the reminder voice fires 30 s before
   the spawn). **Do not write "hive at +45 s" as decoded.** If the 45 stands, the
   reminder fires 15 s after the call and the hive appears at +45 s; if the 30
   overrides, the hive appears at +30 s and the reminder at the call. (R: the
   status write, the 45 and the data fields; I: the hive spawning from
   `SliceToSpawn` when the telegraph ends; U: which duration applies)
5. **Hive appears:** `Shared_MyHiveIndex` = `Shared_NumHivesSpawned` (0), status
   `Spawned` about 0.55 s after the hive spawns, `NumHivesSpawned` = 1,
   `is_active` about 1.6 s after spawn. (R)
6. **The guide's pacing around the hive** (allies at +2.5 s, +0.3 s, +0.3 s; the
   earliest ally spawn 7.5 s after `[64]` if the player is within 40 m of the
   telegraph) is in the stage 4 findings and matches this decode. (R)
7. **What advances the order.** Only a completed heart capture. If the
   capturer's team score is below 3 and `SequentialHives` is set, the heart
   script asks the overlord for the next `Spawner_Hive` with **TelegraphTime
   240** about 0.3 s after the capture (`[136]` Delay 0.3, then `[204]`). That
   hive gets index `NumHivesSpawned` (1). In the guide the match ends 12 s after
   the capture, so hive 2's telegraph exists but its hive never spawns (240 s
   against 12 s). (R; that the overlord finds a free node is I)
8. **The stage 4 stall concern does not arise from this path.** The `== 1`
   telegraph and hive tests at `[741]`/`[693]` only run before the allies spawn;
   hive 2 is requested after a capture, when the critical path has left that
   loop. (I)

### 2.3 How a hive dies

- **Health 5,000**, no regeneration by itself, `IncomingDamageMultiplier` 1.0.
  Types `Hive`, `Monster`, `Objective`; flag `CombatTarget`. No team and no
  ownership, so no team check. (R)
- **Who can damage it.** No script restricts the source: the hive has no
  team or ownership, and `hive_damage_accumulation` and `guide_ondamage_hive`
  take any source. The damage rules themselves are native, so "anything that
  can hurt a `CombatTarget`" is I. The guide's allies attack it: Duelist, Hero
  and Inventor have TargetTypes `Player` and `Hive` (docs/37 table); the
  Saboteur targets `Player` only. The dummies are typed `Player`, so their hits
  count as player damage for the hive's defence. (R, except where marked)
- **Defence.** A hit by a Player-typed source raises event 808; the hive fires
  7, 10 or 14 wasps (Health above 2800, up to 2800, up to 1500) at 0.5, 0.33 or
  0.2 s spacing from 12 spawner attachments, in salvos of about 7.7 s plus 2.0 s,
  and spawns an AoE damage field (`hive_aoe_damagefield`). (R)
- **Regeneration.** About 19.5 s after the last attack trigger the
  `HiveOutOfCombat` resource calls the regen script: +100 Health per second until
  full; any hit of 1.0 or more stops it. (R)
- **Death sequence.** `hive_ondeathspawnessence` (heart, status `Heart`) and
  `hive_deathdespawn` (is_dead, 0.5 s, kill the damage field, 0.5 s, dissolve,
  4.5 s, despawn: gone 5.5 s after death). The critical path listens for
  OnEntityKilledEvent with a `Hive` victim (`[595]`/`[262]`): despawn every
  `BotControlled` entity, +0.25 s, spawn the Saboteur by the heart (`[133]`). (R)
- **Order of the two reactions (U).** Whether the heart exists when the critical
  path queries for it (0.25 s after the kill event) is not shown. `[602]` has no
  failure branch (1.1).

### 2.4 Heart spawn

- Raised by the hive's death script when `HivesSpawnHearts` is set: slice
  `slices/interactive/cruciheart.dynamicslice` at the hive's transform with
  `ForceCenterSpawn` True, so on the hive's exact position, TelegraphTime 0. It
  copies the hive's index (`Shared_MyHiveIndex`), and status `Heart` is written
  with OwningHeartTeamId 255. (R)
- One heart per hive; it is a HiveHeart Interactable with no health component.
  `cruciheart_onspawn` sets objective priority `Critical` after 0.05 s and plays
  `music_hive_active`. (R; "cannot be damaged" is I, from the absent component)
- The Saboteur: SpawnGuideBot at the first HiveHeart found, ring 25 m, inner
  exclusion 5, item radius 1, 0.25 s after the kill event, no team set, AI
  TargetTypes `Player`. Its team number is unknown. (R; team U)

### 2.5 Heart capture

- **Duration and range.** `interactionDuration` **10.0 s**, `interactionRadius`
  **2.9 m**, one interactor at a time (`allowMultipleInteractions` False), no
  looping. The script handles a shorter time (`> 9.0` check), so a perk or
  buff outside the script can shorten it. (R; the shortening source U)
- **Who can capture.** The validation script rejects only the invalid entity
  id (`0x00000000FFFFFFFF`) and reports `PickUp`. There is no team, ownership,
  distance or state test in it. Any entity that the interaction system lets
  start can capture, whichever team it is on. Whether the guide bots have an
  interactor and ever use it is not shown. (R; bots U)
- **Interrupts.** The heart script handles the failure: it plays the reset music
  and VFX, prints "Interaction was interrupted" and sets `AllowsInteractions`
  True again, so the heart can be recaptured at once. What decides success or
  failure is the native interaction system, which no script reads. Evidence for
  one cause, from two achievement scripts: a `Knockback` velocity override from a
  Marine on a Player whose `InteractorComponent.ActiveInteractionTarget` is a
  HiveHeart (and teams differ) advances `ACHIEVEMENT_MARINE_INTERRUPT_HEART_CAPTURE_WITH_BUNKER`;
  a `MinigunnerAirhorn` override near such a Player advances
  `ACHIEVEMENT_EARL_PROTECT_HEART_CAPTURER`. So knockback-type overrides
  interrupt. **Melee very likely does**: every hero's quick-melee on-hit script
  (`marine_quickmelee_onhit.cdl` `[17]`, `hero_quickmelee_onhit.cdl` `[10]`, and
  the same in the commando, duelist, fighter, inventor, minigunner, pyro,
  robotanist, saboteur and technomancer copies) does `DoDamage` and then
  `StartVelocityOverrideWithEntityTarget` on each hit entity with
  `assets/player/_common/common_quickmelee_target.velocityoverride`:
  `overrideType` `Knockback`, Duration 0.032 s. That is the override type the
  bunker achievement counts, and the guide's melee lesson is built on it. The two
  guide melee scripts are never loaded, and `guide_melee_interruptheartcap.cdl`
  would reward any melee damage by any Player. The interrupt rule itself is
  native and unread; death, leaving the radius, stun and jumping are not read
  anywhere. (I; the cause list U)
- **On success** (all in the capturer's team context): heart set non-interactable
  `[47]`; `AdjustTeamScore +1` `[41]`; status `HeartClaimed` with the capturer's
  team `[112]`; voice lines if the new score is below 3; the next-hive request
  `[204]` (2.2, item 7); the heart's despawn about 1 s later (I, via event
  `0x004dfc90`). The critical path's own reaction (objective complete, voice
  `hiveFight.7`, `end.3` 6.5 s later) is a separate listener on the same
  InteractionCompletionEvent. (R)

### 2.6 Harvesters

- **What they are.** Five `Harvester` Interactable Objectives on HotH_02, types
  A to E (level entries 597 C, 601 E, 605 B, 609 A, 613 D, in layer
  `SLICE_LAYER_HotH_HARVESTERS`): C (1152.746, 915.821, 19.082), E (936.114,
  1168.715, 57.375), B (1258.810, 1086.970, 22.500), A (1115.108, 1241.943,
  34.361), D (892.810, 988.600, 39.875). Level object names suggest the
  `HarvesterIdComponent.Id` values (Swamp, Mining, Research, Falls, ...) but the
  ids themselves were not read. (R; ids U)
- **Capture.** Radius 5.0 m, duration 10.0 s, one interactor at a time. A
  completed capture makes the interactor the owner; ownership change writes the
  replicated record (2.8). An already-owned harvester cannot be captured by its
  owner (`AlreadyOwned`) or by an ally of its owner (`OwnedByAlly`); it can by an
  enemy. `Shared_Locked` blocks capture (only set in `HarvestersAreCamps`). (R)
- **Reward.** Essence only: **150 per tick to the owner, one tick every 10.0 s**,
  from the global `Harvester` heartbeat that the MatchDataStore starts; 2000
  essence to the owner if death fog disables it (outside CONQUEST). **No team
  score** in the guide. (R)
- **In the guide's chain.** A successful capture of any harvester by anyone
  completes `CaptureHarvester` (`[681]`/`[349]`), and essence then has to bring
  the player to level 2. `standard.levelupdata` (the guide's `LevelupTable`):
  `EssenceToNextLevel` 2700 at level 1, 3800 at 2, 4900 at 3, 6000 at 4,
  `maxLevel` 50. I read 2700 as the essence needed to reach level 2, but the
  table does not say whether it is cumulative. (R; reading I)
- **Not decoded.** The area-trigger scripts (`harvester_area_trigger(_exit)`),
  the capture animation driver, and the quirk in 1.3 about the team id.

### 2.7 Score per team, target, and how the match ends

- **Team score.** `DataStoreMatchComponent.TeamScores`, one u32 per team id. In
  the guide the only writer that can run is the heart: `AdjustTeamScore(team of
  the interactor, +1)`. The other writers in the pak are gated off here: kill
  points (`PointsForKill`, not defined), CONQUEST harvester scoring, the
  differential-scoring flag, the ordo, refinery and victory-beacon scripts of
  other modes. `matchdatastore_onspawn` would init team ids 0 to 7 to
  `InitialScore`, which the guide does not define. (R; the init loop never
  running is I)
- **Target.** The guide's win script tests `>= 1` for team 0 then team 1. No
  CDL reads the config's TargetScore 3 or MatchPointThreshold 2 at all: the
  numbers 3 and 2 appear only as hard-coded literals in the PvP win script, the
  PvP auto-voice (gated on the mode names `CRUCIHEARTS` and `HEART_OF_THE_HIVES`)
  and, as `< 3`, in the heart script (the only one of the three that runs in
  the guide). So the config values are probably display and UI numbers here.
  (R; "probably display" is I)
- **Who raises MatchScoreEvent.** No CDL does (searched all `.cdl` in
  `misc.pak`: only listeners). The event must come from the native score change
  behind `AdjustTeamScore`. (I)
- **Ways the match ends:**
  1. **Heart.** Capture -> score 1 -> MatchScoreEvent -> win script -> 12.0 s ->
     MatchOverEvent(WinningTeamId) -> `matchover_set_teamranks_and_endmatch_flag`:
     rank 1 for the winner's members, rank 2 for the others, victory and loss
     voice, invulnerability, `CombatTarget` cleared, `IsMatchOver` True,
     creatures cloaked and despawned after 3 s. Then the GameOver phase, 300 s
     in the phase list. (R)
  2. **Time.** `MaxGameDuration` 60 minutes from the MatchDataStore's spawn
     (unit proven by the script's comment), then voice, 10 s, every Player
     rank 2, `IsMatchOver` True. **No MatchOverEvent.** This agrees with
     docs/37's "a timeout is a draw: no team wins, so a published rank is 2". (R)
  3. **Surrender.** The generic `surrender.cdl`: 3 s, then MatchOverEvent for the
     other team. Whether the guide exposes it is not shown. (R; reachability U)
- **Which team the player is on is unknown, and it blocks a replication rule.**
  The win script reads only team ids 0 and 1 (`User_Team0` u8 0, `User_Team1` u8
  1); the heart scores for the interactor's team id. The solo roster in
  `server/solo_modes.py` uses `local_team=1`, `bot_team=2` in every mode; that
  convention is the server's (docs/18 calls it "roster bookkeeping only"), and
  whether retail's guide used 0, 1 or another id for the player is not shown.
  A player on team id 2 or higher would never end the match through this
  script. (U)

### 2.8 Replicated state these scripts write

For the later replication work (docs/37 stage 5). The parameters below are
read from the CDL nodes (R). The nodes do not name their destination;
docs/37 calls the records `ReplicatedHiveData` and `ReplicatedHarvesterData` in
the match data store, and that destination is taken from there (I).

| Record | Written by | Values |
|---|---|---|
| Hive status | `onspawn_hivetelegraph` `[8]` | HiveEntityId invalid, HiveId `NumHivesSpawned`, `Spawning`, OwningHeartTeamId 255 |
| Hive status | `hive_onspawnactive` `[52]` | HiveEntityId = the hive, HiveId = its index, `Spawned`, 255 |
| Hive status | `hive_ondeathspawnessence` `[37]` | the hive, its index, `Heart`, 255 |
| Hive status | `cruciheart_interaction` `[112]` | invalid entity id, the index, `HeartClaimed`, the capturer's team id |
| Harvester status | `harvester_onspawn` `[12]` | the harvester, `HarvesterIdComponent.Id`, `InPlay`, OwningTeamId 255, CapturerEntityId invalid |
| Harvester status | `harvester_onownershipchanged` `[31]` | `InPlay`, OwningTeamId `User_NewOwnerTeamId` (255 unless the quirk in 1.3 is wrong), CapturerEntityId the new owner |
| Harvester status | `harvester_deathfog` `[51]` | `InDeathFog`, 255, invalid |
| Team score | `cruciheart_interaction` `[41]` | `AdjustTeamScore(team, +1)` |
| Match flags | `matchover_set_teamranks...` `[29]`, `endgame_maxdurationreached` | `DataStoreMatchComponent.IsMatchOver` = True |
| Counter | `hive_onspawnactive` `[32]` | `Shared_NumHivesSpawned` on the MatchDataStore (a CDL shared variable; whether it replicates is not shown) |

## 3. Decoded, inferred, unknown

### 3.1 Decoded (read from an asset, node or field cited above)

- The win script's `>= 1` literals, the 12.0 s delay, both team ids (0 and 1),
  and that nothing else in it can end the match.
- Heart: 10.0 s, 2.9 m, one interactor, `PickUp` for any valid id; success
  sequence (`AllowsInteractions` False, `AdjustTeamScore` +1, `HeartClaimed`,
  `score < 3` gate, `SequentialHives` -> overlord `Spawner_Hive` telegraph 240).
- Hive: 5,000 HP, types and flags, wasp counts and thresholds, regen 100 HP/s,
  death sequence and 5.5 s despawn, heart on the exact hive position.
- Harvester: 5.0 m, 10.0 s, essence 150 per 10 s tick to the owner, status
  strings `InPlay` and `InDeathFog`.
- The critical path's order, delays, four SpawnGuideBot calls and what they
  take, and that it ends after `voice.guide.end.3`.
- Which scripts are loaded (scripts list, mode config, entity script lists),
  and that nine requested scripts have no reference in the assets checked.
- `guide_onspawn_hivetelegraph.cdl` has no connection from its first check.
- `MaxGameDuration` 60 and its unit (the script's comment).

### 3.2 Inferred

| Claim | Basis |
|---|---|
| MatchScoreEvent is raised natively by the score change | no CDL raises it; the score writer is the only thing that could |
| The heart despawns about 1 s after a success | `cruciheart_capturedelaydespawn` runs on a DesignerEvent and the only `0x004dfc90` raise is in the heart script |
| The heart cannot be damaged | no health component in `cruciheart.dynamicslice`; absence only |
| `GetGameMode` returns `HOTH_GUIDE` for the guide, so the `CRUCIHEARTS` / `HEART_OF_THE_HIVES` gates in `crucihearts_autovoice` and `crucihearts_seteventascritical` fail | the mode name in the config list (docs/23 table) and the `dataPerGameMode` rows use the same strings; the UI override is `HEART_OF_THE_HIVES`, so this is unverified. It only affects voice lines and objective priority; hive, heart and telegraph set priority themselves |
| TargetScore 3 and MatchPointThreshold 2 are display-only in the guide | no script that runs reads them; a native reader cannot be ruled out |
| Knockback-type velocity overrides, including a quick-melee hit, interrupt a heart capture | two achievement scripts count `Knockback` overrides on a capturer; every hero's quick-melee on-hit script applies a `Knockback` override to what it hits; no capture script reads the cause |
| `User_NewOwnerTeamId` stays 255, so the replicated harvester record carries no team | `[49]` Set direction in `harvester_onownershipchanged` |
| The overlord spawns a `HiveTelegraph` entity for the guide's call | `[70]`/`[240]` look for one and the poll expects exactly one |
| The camp step can end with no kill if a camp is already empty | `camp_iconmanager_basic` raises 101 whenever no Monster is within 30 m of any camp |
| The 12 s delay is never cancelled and a second MatchScoreEvent within it could start another run | no cancelling node; re-entrancy of CDL scripts not read |
| The `InitialScore` init loop never runs in the guide | the value is not in `GameModeValues` and the node has no wired failure output |
| Level 2 needs 2,700 essence | `EssenceToNextLevel` 2700 at level 1; cumulative or per level not stated |

### 3.3 Unknown

| Unknown | What would settle it |
|---|---|
| The player's team id (0, 1 or other) and so which win check fires | a live capture of the roster or `GetTeamId` in a running guide; the exe's team assignment for solo |
| The Saboteur's team; whether bots can interact with hearts or harvesters | a live run; the `InteractorComponent` on the dummy slices |
| Which of the six `Spawner_Hive` nodes the overlord picks | the server overlord code, not in the install |
| Whether `SuppliedSpawnOverrideInSeconds` 30 overrides the 45 and 240 s | the telegraph spawner code or a timed live run |
| Whether the 90 s `MinDelayForFirstEvent` governs the `Guide_HOTH` entity, and the clock it counts from | the SequentialGMData reader in the exe, or the timestamp of the first tutorial voice line in a live run |
| What the heart's `AllowsInteractions` default is at spawn | the heart slice's `InteractableComponent` field; not printed by the flat dump |
| Whether the heart exists 0.25 s after the hive's kill event | order of the two OnDeath/OnEntityKilled handlers |
| What Lock/UnlockProgressionSubmission do | the exe |
| The death-fog timeline in the guide and its effect on hive, heart and harvesters | `deathfog_heart_of_the_hives.deathfogdata` (read only to its first transitions: PreGameBuffer 20 s, Early 240 s, Hive1 60 s, Middle 210 s, ...) |
| The `HarvesterIdComponent.Id` values | the level entities' component data; not read |
| What the wire form of a status or score change is | stage 5's own replication trace; this file gives only the CDL side |

### 3.4 Defaults that matter but do not show in the decodes

| Variable | Where | Default |
|---|---|---|
| `User_Team0`, `User_Team1`, `User_WinningTeamId`, `User_LosingTeamID` | win script | u8 0, u8 1, u32 255, u32 0 |
| compare-to literals, delay | win script `[45]` `[41]`, `[16]` | u32 1 and u32 1, float 12.0 |
| `User_HiveSearchRadius` | critical path | float 40.0 |
| `User_HiveTransform` | critical path | identity at the origin (set by `[409]`/`[16]` before use) |
| `Shared_hoth_startState_dropSequence` | `guide_progress.sbb` | s32 0 |
| `Shared_hoth_taskState_critical_*` | `guide_progress.sbb` | enum `NotActive` (values seen: `Active`, `Completed`) |
| `Shared_hoth_taskState_*` (fourteen s32) and `max_items` | `guide_progress.sbb` | 0, and 3 (used only by never-loaded scripts) |
| `Shared_hoth_lastHiveDamageTime`, `Shared_hoth_hiveTaskStart` | `guide_progress.sbb` | u32 0 (the first is the poll's "recent damage" input; the second is never written by anything that runs) |
| `Shared_guide_startTask_GoToHive`, `endTask_ClearCreatureCamp`, `endTask_UseDetectMode` | `designer_events.sbb` | s32 100, 101, 102 (and 103, 104 for two tutorial events) |
| `Shared_NumHivesSpawned`, `Shared_MatchEnded`, `Shared_IsReady` | `gamemodes.sbb` | u32 0, false, false |
| `Shared_MyHiveIndex`, `Shared_IsCapturing`, `Shared_Heart_InteractorID`, `Shared_IsAttacking` | `hive.sbb` | u32 0, false, invalid id, false |
| `User_NonCapturingTeamId`, `User_InteractionTime`, `User_ShortMusicApexTime` | heart script | u8 0, float 10.0, float 7.0 |
| `Shared_HarvesterEssencePerTick`, `Shared_DeathFogged_Essence_GrantAmount`, `Shared_MiniHarvesterEssencePerTick` | `harvester.sbb` | u32 150, 2000, 75 |
| `Shared_alive`, `Shared_Locked` | `harvester.sbb` | true, false |
| `User_NewOwnerTeamId` | `harvester_onownershipchanged` | u8 255 |
| `User_*HealthWasps`, thresholds, delays | `hive_ondamage` | 7 / 10 / 14 wasps; 2800.0 and 1500.0; 0.5 / 0.33 / 0.2 s |
| `User_TWOMIN_TWENTYSIXSEC_in_ms` | `onspawn_hivetelegraph` | u32 146,000 |
| `User_LoopDelay_millisec`, `User_EarlyGame_CampUpdateBlackoutWindow` | `camp_iconmanager_basic` | u32 4000, float 10.0 |

## 4. What the scripts touch

For the replication work. Anything under "write" needs a server-side
equivalent or a deliberate omission.

### 4.1 Entity types and flags

- **Types queried or tested:** `Player`, `PlayerPet`, `Monster`, `Hive`,
  `HiveHeart`, `HiveTelegraph`, `Harvester` (letters `A` to `E`; `C` is the
  central one in `harvester_onspawn`), `Interactable`, `Objective`,
  `SnurtleCamp`, `SpitterCamp`, `MegaDroneCamp`, `Megadrone`, `GMEvent`,
  `Blackout`, `MatchDataStore`; in dead code `Amplifier`,
  `GuideEnemyBotSpawner`. Hive overlord nodes are `Spawner_Hive`.
- **Flags:** `BotControlled` (every bot the guide spawns; the despawn and team
  loops select by it), `CombatTarget` (hive; players), `AffectedByDeathFog`
  (hive, heart, harvesters, players), `Evolution` (on a GMEvent), `Cloaked`
  (dead query).

### 4.2 Components and data, by group

- **Match data store** (`DataStoreMatchComponent`): `TeamScores` (read
  `GetTeamScore`, write `AdjustTeamScore`/`SetTeamScore`); `IsMatchOver`
  (write); `NumPlayersInMatch`, `NumPlayersKilled` (read); the hive and
  harvester status records (write, through the two Set nodes; their component
  field names are not in the CDL); the CDL shared variable
  `Shared_NumHivesSpawned` on the same entity.
- **Interaction:** `InteractableComponent.AllowsInteractions` (write: heart),
  `.ActiveInteractionCount` (read: harvester death fog),
  `InteractorComponent.ActiveInteractionTarget` (read: achievements),
  `OwnableComponent.OwningControllerId`, `.BecomeOwnedRequests`,
  `.TransferOwnerShipToLivingTeamMateRequests`, `HarvesterIdComponent.Id`,
  `GameplayHeartbeatComponent.Heartbeats`, `EssenceComponent.EssenceGrantRequests`.
- **Player and world state:** `LevelComponent.Level`,
  `PlayerMovementComponent.Position`, `LocationComponent.InternalTransform`
  (and `.Position`), `EntityFlagsComponent.EntityTypes` and `.FlagRequests`,
  `CrucibleHealthComponent.Health`, `.IsDead`, `.Requests`,
  `EntityResourceCollectionComponent.ResourceAdjustmentRequests` (the hive's
  `HiveOutOfCombat`), `StatsComponent.StatsChangeRequests`,
  `BuffComponent.BuffChangeRequests` (match over), `InputComponent.*` (only the
  detect-mode exit path).
- **Teams and AI:** `AllianceComponent.AllianceStaticSetupRequests` (AddToTeam
  for the allies), `.AlliedController`, `.BreakingAllianceController`,
  `AIComponent.TargetBlackListRequests` (the allies' blacklist),
  `SurrenderComponent`.
- **Guide presentation (docs/26 RPCs):** `TutorialObjectivesComponent`
  (`NewObjectivesListRequestContainer`, `RemoveObjectivesListRequestContainer`,
  `CompleteObjectiveRequestContainer`, `SetActiveObjectiveListRequestContainer`,
  all addressed to `Shared_hoth_player_EntityId`), `CrucibleVoiceScriptComponent.VoiceQueueRequests`,
  `AudioScriptComponent.TriggerRequests`, `ObjectiveComponent.ChangePriorityRequests`
  and `.SetVisibilityRequests`, the `HeightenObjectiveMessaging` and
  `CancelHeightenedMessaging` nodes, `ParticlesRequestComponent.PredictableRequests`,
  `CameraGameplayDataComponent.PlayCameraShakeRequests`,
  `AnimationParameterComponent` (`is_active`, `is_aiming`, `is_crouching`,
  `is_dead`), `ActorPropertySyncComponent.SetMeshVisibleRequests`.
- **Spawning and scripting:** `ScriptComponent.ScriptStartRequests` (heart's
  StartScript), `ProjectileRequestComponent.ProjectileRequests` (wasps, essence);
  native nodes `SpawnGuideBot`, `SpawnNodes_AIOverlord`, `SpawnNodes_MatchStart`,
  `LocalDistributedSpawning`, `SpawnSlice`, `Despawn`, `HiveAttack`, `DoDamage`,
  `DoHealing`, `KillCharacter`, `SetPlayerRank`, `MakeMatchOverEvent`.

### 4.3 Events

| Event | Payload fields used | Seen as |
|---|---|---|
| `CharacterFinishedExitingDropPod` | `CharacterEntityId` | critical path entry |
| `GameInputAction_DetectMode` | none | detect-mode entry |
| `DesignerEvent` | `EventId` (s32) | wait, raise, enter |
| `InteractionStartEvent` | `interactorId`, `interactableId`, `interactionEndTimePoint` | heart entry; harvester re-raise |
| `InteractionCompletionEvent` | `interactorId`, `interactableId`, `succeeded` | critical path waits; heart and harvester waits |
| `OnEntityKilledEvent` | `victimEntityId` | critical path wait |
| `OnDeathEvent` | `entityId`, `killerEntityId` | hive scripts; guide camp and respawn scripts |
| `OnSpawnEvent` | `entityid` | many entries; critical path wait for a Hive |
| `OnPlayerLevelupEvent` | `level` | critical path wait |
| `OnDamageOrDotAppliedReceivedEvent` | `targetEntityId` | `guide_ondamage_hive` |
| `OnNonDoTDamageReceivedThisFrameEvent` | `damageSources` (with `sourceEntityId`) | hive defence |
| `MatchScoreEvent` | none | win script; PvP voice; never raised by CDL |
| `MatchOverEvent` | `WinningTeamId` | raised by win script and `surrender`; read by the match-over script |
| `OwnershipChanged` | `OldOwnerId`, `NewOwnerId` | harvester |
| `GameplayHeartbeatEvent` | `Type` | harvester |
| `TelegraphOffsetEvent`, `DeathFogUpdateEvent`, `OnMatchPhaseChangedEvent` (`currentPhase`), `SurrenderedEvent` | | telegraph, harvester, camps, match |

Designer event ids: 100 `Shared_guide_startTask_GoToHive` (raised only in dead
code; waited for only in dead code), 101 `endTask_ClearCreatureCamp` (raised by
the camp icon manager `[30]`/`[5]`, waited `[292]`/`[725]`), 102
`endTask_UseDetectMode` (raised `[12]`/`[19]` in the detect script, waited
`[517]`/`[669]`), 808 `0x328` (hive attack, `hive_damage_accumulation` `[28]`
to `hive_ondamage` `[145]`), 1928 `0x788` (heart's voiceover start),
5,110,928 `0x004dfc90` (heart success), 700,000,009 `0x29b92709` (camps, set
`RespawnCampsBasedOnHive`), 33,833,383 `0x020441a7` (evolve),
896,927,685 `0x357607c5` (raised by the guide's camp-kill script; no loaded
listener).

### 4.4 Assets named by the scripts

- **Slices spawned:** `slices/spawntelegraphobjects/hivetelegraph.dynamicslice`
  (overlord), `slices/creatures/hive/hive.dynamicslice` (telegraph data),
  `slices/interactive/cruciheart.dynamicslice` (hive death),
  `slices/creatures/hive/hive_aoe_damagefield.dynamicslice`,
  `slices/creatures/hive/wasp.dynamicslice` (via `assets/creature/hive/wasp.projectile`),
  `slices/spawntelegraphobjects/hivetelegraph_blackoutonly.dynamicslice`; the
  guide bots from BotData in `guide_hoth.dynamicslice`:
  `slices/player/{duelist,hero,inventor,saboteur}/<hero>_character_dummy.dynamicslice`
  (types `Base`, hero type, `Player`; flags `AffectedByDeathFog`, `CombatTarget`,
  `AbilityTargetable`; AI `ai/guide_dummies/*`). Dead code names
  `slices/practicearena/robodummy.dynamicslice` and
  `slices/planetaryevents/major_damageamplifiers.dynamicslice`.
- **Data:** `assets/gamesystems/interaction/cruciheart.interactabledata`,
  `harvesterinteractabledata.interactabledata`,
  `harvesterheartbeat.gameplayheartbeat`, `assets/spawntelegraphs/hive.spawntelegraphdata`,
  `assets/creature/hive/hive.resourcesdata`, `design/gamemodes/leveluptables/standard.levelupdata`,
  `assets/tutorial/guideobjectives.tutorialobjectiveslist` (ListId
  `GuideObjectives`), `assets/planetaryevents/hoth_guide.sequentialgmdata`,
  `assets/gamesystems/aioverlord/hoth_guide_no_overlord.aioverlordspawndata`.
- **Objective ids the live chain uses:** `Guide_HotH_Detect`,
  `Guide_HotH_ClearCreatureCamp`, `Guide_HotH_CaptureHarvester`,
  `Guide_HotH_CollectEssence`, `Guide_HotH_GoToHive`, `Guide_HotH_WaitForHive`,
  `Guide_HotH_DefeatHive`, `Guide_HotH_TeamCaptureHeart`.
- **Voice events the live chain uses:** `voice.guide.detectMode.1`, `.detectMode.3`,
  `voice.guide.detectmode.2` (detect script, lowercase m), `voice.guide.creatureCamp.1`,
  `voice.guide.harvester.1`, `.harvester.4`, `voice.guide.essence.1`, `.essence.4`,
  `.essence.6`, `voice.guide.hiveFight.1`, `.hiveFight.3`, `.hiveFight.5`,
  `.hiveFight.7`, `voice.guide.end.3`, `voice.guide23`. Generic ones:
  `voice.auto.Hivecapturing`, `voice.yourteam.heartcaptured`,
  `voice.enemyteam.heartcaptured`, `voice.auto.hiveReminder`,
  `player.victory.team`, `player.lose`, `voice.gamemaxduration`.

## 5. Contradictions with docs/37 and the stage 4 findings

1. **"The match ends at 3 hearts"** (docs/37 stage 5) describes the server's
   current design, not retail. Retail's guide win script tests `>= 1` and
   waits **12 s** before MatchOverEvent. docs/37's "What the assets say" is
   right that the config says target 3 and match point 2; the script does not
   use them.
2. **"Thirty more `guide_hoth/*.cdl` scripts handle hearts, harvesters, hives,
   respawn, level-up and match-over voice lines"** (docs/37) overstates what
   runs. 40 `.cdl` files sit in the directory (8 in a subdirectory); seven
   load (six from the list plus the win script); one of those seven is inert
   after its first check. The heart, harvester, hive-spawn, hive-death,
   interrupt and match-over scripts have no reference by GUID or path in the
   assets checked (1.0); the level-up, ping, plant, med-station and map scripts
   are not in the scripts list or in HotH_02's resource list (not searched
   further).
3. **The 90 s first-event delay.** Stage 4 inferred the guide entity is live from
   the start of play because 90 s would miss the drop-pod event. This file's
   evidence weakens that (the delay also governs a GMEvents-only data file) but
   does not overturn it. Both readings are in 1.0; docs/37 should not state
   either as fact.
4. **"Only then does `SpawnNodes_AIOverlord` raise the hive's 45 s telegraph"**
   (docs/37 stage 4) is right about the call, but `hive.spawntelegraphdata`'s
   `SuppliedSpawnOverrideInSeconds` 30.0 leaves open whether 45 s is the real
   duration (2.2, item 4).
5. **"If the mode spawns a second hive while the critical path is still polling,
   the guide stalls"** (stage 4): the only second-hive call found is in the heart
   script, after a capture, when the poll has long ended (2.2, item 8).
6. **"The script ends after the first heart"** (stage 4) is confirmed, with two
   additions: the script ends silently if no `HiveHeart` exists 0.25 s after the
   hive's kill event (`[602]`), and the match still ends because the heart
   script scores independently.
7. **Stage 4's open question 4** ("whether the mode's own code adds or limits
   bots") is unchanged. The generic scripts read here spawn no bots and do not
   limit them.

## 6. Evidence and reproduction

Every file read here is in `misc.pak` (65,804,798 bytes; found with
`tools/pak.py`'s `index()` and `read()`), except HotH_02's level slice
(`levels.pak`) and its `resourcelist.txt` (the level's own `level.pak`).

Scripts in the requested set (and the other loaded guide scripts). Bytes and
hash are the decoder's values (SHA-256, first 12 hex digits). "Reach" is nodes
reachable from the enter trigger or an exit trigger.

| Script | Bytes | SHA-256 | Nodes | Edges | Reach | Dead |
|---|---|---|---|---|---|---|
| `hoth_guide_win_condition.cdl` | 70197 | 5c497c453fb8 | 15 | 14 | 15 | 0 |
| `guide_onspawn_hive.cdl` | 57027 | 72ad7bfff2cc | 9 | 8 | 9 | 0 |
| `guide_onspawn_hiveheart.cdl` | 106692 | 1e04e4c700ed | 13 | 13 | 13 | 0 |
| `guide_onspawn_hivetelegraph.cdl` | 149249 | ce2201d2dd8b | 21 | 17 | 2 | 19 |
| `guide_ondeath_hive.cdl` | 72582 | 27dc7b007de1 | 9 | 8 | 9 | 0 |
| `guide_ondamage_hive.cdl` | 29874 | b6cdb795e7cd | 4 | 3 | 4 | 0 |
| `guide_interaction_captureheart.cdl` | 98696 | 6c0023da1212 | 12 | 11 | 12 | 0 |
| `guide_interaction_captureharvester.cdl` | 97513 | 79f2dfeff428 | 13 | 9 | 3 | 10 |
| `guide_entitydetected_captureharvester.cdl` | 116062 | d02b286a4137 | 17 | 18 | 17 | 0 |
| `guide_melee_interruptheartcap.cdl` | 98334 | 7976cd6f24be | 12 | 11 | 12 | 0 |
| `guide_interaction_interruptheartcap-usemelee.cdl` | 78702 | 4991d08780fd | 12 | 11 | 12 | 0 |
| `guide_onmatchover.cdl` | 77136 | 955828b5a4fe | 6 | 5 | 6 | 0 |
| `guide_criticalpath.cdl` | 1825791 | 3ed12869df61 | 230 | 230 | 172 | 58 |
| `guide_gameinput_detectmode.cdl` | 171731 | 864468d12587 | 24 | 22 | 23 | 1 |
| `guide_ondeath_clearcreaturecamps.cdl` | 215419 | e916666c701c | 28 | 24 | 10 | 18 |
| `guide_ondeath_playerrespawn.cdl` | 39676 | 2194f287ede0 | 6 | 5 | 6 | 0 |

(For `guide_gameinput_detectmode` the one dead node is an `ExternalGet` of the
shared player id; its enter chain is 6 nodes and the rest is the exit-trigger
check. For `guide_ondeath_clearcreaturecamps` the 10 reachable nodes are the
enter chain only; the other 18 are dead.)

Generic scripts decoded (bytes, SHA-256 prefix): `cruciheart_interaction` 630121
2831c8e17e27; `cruciheart_interaction_validation` 24118 6035007ed016;
`cruciheart_onspawn` 95463 05722665e40b; `cruciheart_capturedelaydespawn` 55552
6b3fd2a045ea; `cruciheart_interaction_hoth_voiceover` 227411 3f12bd053b2f;
`hive_onspawnactive` 165217 df8e60a352c2; `hive_ondeathspawnessence` 249058
6a962522e4c9; `hive_deathdespawn` 110384 713b688e6aec; `hive_ondamage` 311105
64f6c3792b5a; `hive_damage_accumulation` 54363 6b3d21c2b783;
`hive_outofcombatregen` 86885 7f0f707ebf23; `onspawn_hivetelegraph` 87483
a1d0beb2a8b7; `hivetelegraph` 50891 8fb114c858e6; `harvester_interaction` 385714
f6640b5481f9; `harvester_interaction_validation` 86277 acf772acc7cb;
`harvester_onspawn` 69132 aa71f7da9a8c; `harvester_onownershipchanged` 101723
c902058f8a6f; `harvester_heartbeat` 134505 053305f512ab; `harvester_deathfog`
178661 7b92cea5991a; `camp_iconmanager_basic` 258261 2ace27cefde5;
`matchdatastore_onspawn` 68601 c9f875894a11;
`matchover_set_teamranks_and_endmatch_flag` 126182 ae00e67fa218;
`endgame_maxdurationreached` 94021 35a0fce2d110; `surrender` 155844
e111230d1250; `crucihearts_win_condition` 164570 d8890d856a87. All under
`cdl/` in `misc.pak`; the paths are in section 1.3.

Commands (read-only; the Python is the repo's `.venv`):

```
PY="/home/blizz/dev/Crucible Revival/.venv/bin/python"
DEC="/home/blizz/dev/Crucible Revival/.worktrees/bot-weapon/tools/inspect_cdl_logic.py"
PAK=~/.local/share/Steam/steamapps/common/Crucible/misc.pak
"$PY" "$DEC" "$PAK" cdl/gamemodes/guide_hoth/<script>.cdl [--json out.json]
```

The mode config, scripts list, sequential data, interactable data, slices and
data assets were read with `.worktrees/bot-weapon/tools/inspect_creature_ai.py --flat` (asset paths
decoded from the trailing ASCII of the asset references). The `.sbb` shared
blackboards are **JSON** ObjectStreams, not binary; they were parsed with a
small scratch script. The worktree's `tools/pak.py` has no `index()`; the main
repo's does (`pak.index()` then `pak.read()`, which seeks). All of these are in
the session's scratch folder (`dumpvars.py` for user and hidden-literal variable
defaults, `flowview.py` for a flow-ordered outline from the decoder's JSON,
`sbbdump.py`, `flat.sh`); none is in the repo, and none survives the session. The outline tool drops the output ordinal for
single-output nodes, so every failure-branch claim above was re-read from the raw
connection lists (`[602]`, `[26]`, the `Has*` and `GetContainerValue` nodes).

Reference locations: pak holding all the scripts, configs and slices read here:
`misc.pak` (`design/gamemodes/hoth_guide.gamemodeconfig`, `cdl/gamemodes/guide_hoth/`,
`slices/planetaryevents/guide_hoth.dynamicslice`, `assets/planetaryevents/`, the
generic `cdl/gameplay/interaction/`, `cdl/creatures/hive/`, `cdl/gamemodes/`).
HotH_02's level slice (the harvester positions) is `levels/HotH_02/commondata/level.dynamicslice`
in `levels.pak`, read with `tools/inspect_map_entities.py`; its
`resourcelist.txt` is in `NvGameSdk/levels/HotH_02/level.pak`.
