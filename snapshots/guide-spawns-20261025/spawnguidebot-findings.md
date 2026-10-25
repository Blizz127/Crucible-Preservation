## Stage 4 findings: the critical path's SpawnGuideBot calls

Read on 25 October from `cdl/gamemodes/guide_hoth/guide_criticalpath.cdl` in
the devbox reference `misc.pak` (1,825,791 bytes, SHA-256
`3ed12869df61fc2dd898a629901c5bc8ac28f9c2ae71f75ceb68b8a105014ee3`, the value
both tools print). Read-only: nothing was written under the Steam directory or
the repo. **Read** marks what a command prints, with the node id `[n]` (the
tools' instance id) or a file offset. **Inferred** marks reasoning on top of
it. Commands are listed at the end.

### Short version

1. **There are four SpawnGuideBot nodes, not eight.** The earlier "8" counted
   the string `SpawnGuideBot` in the file: each node carries its name twice
   (`m_nodeName` and `m_MetaData/m_nodeName`), which is 8 hits for 4 nodes.
   The other 48 hits are the hidden-literal names (4 nodes x 6 inputs x 2
   names). There are four more bot spawners in the file, but they are
   `LocalDistributedSpawning` nodes that nothing reaches (see "The four dead
   nodes").
2. **The loaded scripts spawn four bots per match, once.** Three allies
   (Duelist, Hero, Inventor) appear 2.5 to 3.1 s after the player first gets
   near the hive, on the player's team. One Saboteur appears when that hive
   dies, after the allies are despawned. The script calls it the enemy bot
   (its comment box is "Defeat the Hive: Spawn the enemy bot", the next voice
   line says to "fight off any enemies", it gets no team and no blacklist, and
   its AI targets only Player), so it is read as hostile; only its team number
   is unknown. Nothing in the loaded scripts respawns, replaces or adds bots
   for the second and third hives. The script ends after the first heart.
   Mode-level code could do more (open question 4).
3. **No live call uses a `SpawnNode_Guide_EnemyBot`.** Each call takes a
   Transform, a radius and the `Bipeds` nav layer. The Transform is the hive's
   (allies) or the hive heart's (Saboteur). The twelve map nodes are not
   referenced by any script that runs (the server's own code is not in the
   install, so server-side use cannot be ruled out).
4. **The bots appear minutes into the match, not at the drop.** The first spawn
   waits for detect mode, a creature camp, a harvester capture, level 2 and
   the player walking to a hive. The current placeholder (hunters standing on
   guide nodes 44, 36 and 39 from the start) is wrong in time as well as in
   place.
5. **The hive order is not in the script.** It calls the overlord once, with
   no hive named.

### The four live nodes

All four sit in script portion 1, are reachable from the enter trigger, and lie
on no cycle (each can run at most once per script run). Order is the order they
run in. Ring is `Radius / InnerExclusionRadius / RadiusOfSpawnedItem`, in
metres.

| # | Node | BotID | Location input (read) | Ring | Team and targeting (read) | Trigger and delay (read) |
|---|---|---|---|---|---|---|
| 1 | `[252]` | DuelistDummy | `User_HiveTransform` | 25 / 20 / 2 | Player's team via AddToTeam `[38]`; blacklisted from targeting the player and the other bots. AI TargetTypes: Player, Hive | GoToHive objective completes `[541]`, then Delay `[314]` 1.0 s, Delay `[268]` 0.5 s, voice `hiveFight.3`, Delay `[79]` 1.0 s: **2.5 s** later |
| 2 | `[580]` | HeroDummy | `User_HiveTransform` | 25 / 20 / 2 | as `[252]` | Delay `[485]` **0.3 s** after the Duelist |
| 3 | `[7]` | InventorDummy | `User_HiveTransform` | 25 / 20 / 2 | as `[252]` | Delay `[583]` **0.3 s** after the Hero (0.6 s after the Duelist). Delay `[488]` 0.1 s follows, commented "Needed to give time to the bot to spawn", then the team pass |
| 4 | `[133]` | SaboteurDummy | Transform of the first HiveHeart: `ExternalGet_11.OutputValue` from `[562]` <- `[602]` <- `[461]` | 25 / 5 / 1 | Script sets no team and no blacklist; its comment box calls it "the enemy bot". AI TargetTypes: Player only | Any Hive dies (`[595]` OnEntityKilledEvent, victim type Hive `[262]`); every BotControlled entity is despawned `[189]`; Delay `[255]` **0.25 s** |

Common to all four (read): `NavMesh` = `Bipeds`; `OwnerId` = the invalid
EntityId `0x00000000FFFFFFFF` (no owner); `EntityFlags` = `BotControlled`;
BlueNoise `RadiusPercentage` 1.0, `CandidateCount` 5, `CandidateScalar` 0,
`ForceCenterSpawn` false. Each node has one input slot, one output slot and
`m_nodeClass` 1 (instant). There is no failure output, so the script cannot
tell whether a spawn happened. **Inferred:** the action only queues a request
(the component has a `SpawnBotRequestContainer`, file offset 0x6a22cf0), which
fits the instant node class and the 0.1 s comment on `[488]`.

Binding check, from raw variable ids and not from either tool
(`$SCRIPTS/verify_bindings.py`): `[7]`, `[252]` and `[580]` all bind
`Transform` to user variable `User_HiveTransform` (id `ba6cdb25...`); `[133]`
binds it to generated variable `ExternalGet_11.OutputValue`.
`inspect_creature_cdl.py` prints `Transform` as a literal zero for all four.
That is the slot default, not the binding. Use only `inspect_cdl_logic.py` for
bindings.

BotID to node was cross-checked the same way. The four BotID literals sit at
file offsets 0x14723b (Inventor), 0x159230 (Saboteur), 0x16bb47 (Duelist) and
0x1a899b (Hero).

### The four dead nodes

| Node | Spawns (slice) | Transform / radius / inner / margin / owner | Layout |
|---|---|---|---|
| `[352]` | `duelist_character_dummy` | `User_HiveTransform` / 25 / 20 / 2 / none | same editor column as `[252]` |
| `[427]` | `hero_character_dummy` | `User_HiveTransform` / 25 / 20 / 2 / none | same column as `[580]` |
| `[41]` | `inventor_character_dummy` | `User_HiveTransform` / 25 / 20 / 2 / none | same column as `[7]` |
| `[440]` | `saboteur_character_dummy` | zero transform, never wired / 40 / 5 / 1 / `@Self` | inside the "Defeat the Hive: Spawn the enemy bot" box with `[133]` |

`[424]` is a `SpawnSlice` of `major_damageamplifiers` (not a bot) and is dead
for the same reason.

**Read, the proof that they do not run.** The script has 230 nodes (the enter
trigger included) and 230 connections. Following the connections from the
enter trigger reaches 172 nodes. The other 58 are unreachable: no connection
from any reachable node lands on any of them. The four
`LocalDistributedSpawning` nodes and `[424]` have no incoming connection at
all. The only entry points without an incoming connection that could plausibly
start by themselves are the two `WaitForEvent` nodes `[322]` and `[421]`;
`[322]` reaches one dead-end condition and `[421]` reaches nothing, so neither
leads to a spawn node (`$SCRIPTS/graph_checks.py`, `$SCRIPTS/flow.py ... summary`).

**Read, supporting only.** The 58 nodes are exactly the script's key-0 portion
(58 nodes, 45 connections), which has no start node. This is not a proof:
across all 1,545 `.cdl` files in `misc.pak`, no key-0 portion has a start node
and every key-1 portion is fully reachable from the enter trigger, but in 240
scripts key-0 nodes are reached from the enter trigger through other portions
(`$SCRIPTS/portion_survey.py`). The connection analysis above is the argument.

**Inferred, why they exist.** They are the older version of the same calls,
replaced by `SpawnGuideBot`. The three ally nodes have the same Transform
variable, radius and noise settings as their replacements and sit in the same
editor column 800 to 900 units away. Their `SpawnedEntityIds` outputs are never
read. The designer comment "Wait for Hive: Moved this to
guide_onspawn_hivetelegraph.cdl" sits over other abandoned nodes in the same
portion. SpawnGuideBot is the instant, BotID-keyed form (the four BotID rows
of `guide_hoth.dynamicslice` BotData); `LocalDistributedSpawning` is latent
(`m_nodeClass` 3) and names the slice directly.

### Trigger chain

Each step is **read** from the connection list (`$SCRIPTS/flow.py
$S/critpath-logic.json path 252 133` prints it node by node). Condition outputs: out0 true, out1 false, operators
0 `==`, 1 `!=`, 2 `<`, 3 `<=`, 4 `>`, 5 `>=` (bot-weapon track's findings
notes, proven from the exe). ForEach out0 is the loop body and out1 is "done";
WaitForEvent out2 is "event fired". Those last two are **inferred** from
consistent use across the file.

1. Enter `[1]` `CharacterFinishedExitingDropPod`. Guard `[677]`:
   `Shared_hoth_startState_dropSequence == 0` (its default in
   `guide_progress.sbb` is 0). `[32]` sets it to 1 and `[361]` stores the
   event's character as `Shared_hoth_player_EntityId`. A second drop event, for
   example a respawn, finds the guard closed and does nothing. The script never
   reads the event's `IsRespawn`.
2. Detect mode: objective `Guide_HotH_Detect`, then waits for designer event
   `Shared_guide_endTask_UseDetectMode` `[517]`/`[669]`.
3. Creature camp: `Guide_HotH_ClearCreatureCamp`, waits for designer event
   `endTask_ClearCreatureCamp` `[292]`/`[725]` (comment: raised in
   `camp_iconmanager_basic.cdl`).
4. Harvester: `Guide_HotH_CaptureHarvester`, waits for a successful
   InteractionCompletionEvent whose target is a Harvester `[213]`/`[681]`/`[349]`.
5. Essence: `Guide_HotH_CollectEssence`, passes when the player's level is
   2 or more `[733]`/`[689]` (at once if already there).
6. `[64]` `SpawnNodes_AIOverlord`: Category `Spawner_Hive`, Count 1,
   TelegraphTime 45, SingleUse true, OccupySpawn true. Its comment box reads
   "Spawn the hive telegraph". 1.0 s later voice `hiveFight.1`; 3.0 s later
   objective `Guide_HotH_GoToHive`.
7. GoToHive poll, one pass per second (`[391]`). `[186]` reads the player's
   position. `[452]`/`[713]` ask whether the hive was damaged within the last
   0.6 s: if so the search radius is 999999 m, otherwise 40 m (`[44]`/`[355]`).
   `[240]` sphere-queries HiveTelegraph entities around the player; exactly one
   found (`[741]`, `== 1`) gives its transform. Otherwise `[231]` queries Hive
   entities; exactly one (`[693]`) gives its transform. Otherwise it waits and
   polls again. The transform is the entity's `LocationComponent.InternalTransform`,
   saved into `User_HiveTransform` by `[409]` or `[16]` (the only two writers).
8. `[541]` completes GoToHive. The ally calls follow with the delays in the
   table. Before `[252]`, `[26]` `GetTeamId` reads the player's team.
9. After `[488]`: `[538]` queries every BotControlled entity; for each,
   `[38]` AddToTeam (the player's team id) then `[19]` blacklists the player;
   an inner loop `[302]`/`[673]`/`[331]` blacklists every other bot (reasons
   Sensing, Alerted, Damaged; duration 1e9 s).
10. 1.0 s later `[29]` queries Hive entities. If one exists, the objective
    becomes `Guide_HotH_DefeatHive`. If not, `Guide_HotH_WaitForHive` is
    shown until a Hive's OnSpawnEvent (`[607]`/`[177]`). Then `[595]` waits for
    OnEntityKilledEvent, looping until the victim has type Hive.
11. Hive dies: `[192]` queries BotControlled, `[632]` despawns each (`[189]`),
    Delay `[255]` 0.25 s, `[461]` queries HiveHeart, `[602]` takes the first,
    `[133]` spawns the Saboteur, `[494]` completes DefeatHive. Voice
    `hiveFight.5` follows ("The hive is down, but you'll need to fight off any
    enemies before you can safely secure its heart"), then the heart objective.
12. `[622]` voice `end.3` is the last node. It has no outgoing edge.

Designer comment boxes, attributed by node position (`$SCRIPTS/comments.py`),
agree: `[252]`, `[580]`, `[7]` and `[26]` lie in "Spawn bot allies"; `[139]`
(`hiveFight.3`) lies in the box "Hives are dangerous. Since you don't have a
team, we're sending in help."; `[38]`, `[19]` and `[331]` lie in "so bots don't
attack the player or those on their team"; `[189]` lies in "Defeat the Hive:
Despawn ally bots"; `[133]` lies in "Defeat the Hive: Spawn the enemy bot".

**Timing.** `QueueVoiceEvent`, `PlayAudioEvent`, `GetTeamId` and `Set` are
instant (class 1). `Delay`, `ForEach`, `WaitForEvent` and `WaitUntilTimePoint`
are latent (class 3). So the 2.5 s, 0.3 s and 0.3 s above are the Delay nodes
alone. From the enter trigger to the overlord call `[64]` there are 36.5 s of
fixed delays plus the player's detect-mode, camp, harvester and level-2 waits.
The fastest possible Duelist spawn is the overlord call plus 4.0 s (to the
GoToHive objective), plus the `[391]` 1.0 s poll, plus 2.5 s: 7.5 s, if the
player is already within 40 m of the telegraph.

### Team, targeting, the player and the loaded scripts

- **Read.** Allies: AddToTeam with the player's team id, read once by `[26]`
  just before the first spawn, plus the blacklists in step 9. Saboteur: no
  AddToTeam, no blacklist anywhere downstream of `[133]`.
- **Read.** The AI assets: Duelist, Hero and Inventor have TargetTypes Player
  and Hive; Saboteur has Player only (`guide-bots-ai-full.json`, field
  `(root)/TargetTypes`). So the allies are the hive attackers and the Saboteur
  is the only bot that hunts the player.
- **Unknown.** The Saboteur's team. The script leaves it to the engine's
  default for a bot spawned with no owner. The win condition script compares
  team 0 and team 1 scores, so the player is on one of those, but nothing says
  which team the Saboteur gets.
- **Player drop and respawn (read).** The bots depend on the drop only through
  the enter trigger and the stored player entity id. No bot is placed relative
  to the player's drop position. `guide_ondeath_playerrespawn.cdl` runs on the
  player's first death only (`Shared_hoth_respawn == False`): it waits 3 s,
  sets `Shared_hoth_respawn` and queues voice `voice.guide23`. It does not
  touch bots, so a player death does not despawn or respawn any.
- **Not run (inferred from no reference).** A second intro,
  `guide_onplayerlanded.cdl`, uses the same `dropSequence` guard but nothing
  references it: it is not in the scripts list or the level's resource list.
- **Which scripts run.** `guide_hoth_scripts.cdlscriptslist` loads six scripts:
  criticalpath, onspawn_hivetelegraph, gameinput_detectmode,
  ondeath_clearcreaturecamps, ondeath_playerrespawn, ondamage_hive. HotH_02's
  `resourcelist.txt` agrees. Only `guide_criticalpath.cdl` and the test script
  `cdl/test/guide/test_spawnbot.cdl` call SpawnGuideBot in `misc.pak`. The
  Guide_HOTH entity that carries the scripts and the BotData is the single
  ScenarioEvents entry of `hoth_guide.sequentialgmdata`. That file also says
  `MinDelayForFirstEvent` 90 s, but its GMEvents list is empty. **Inferred:**
  the 90 s cannot apply to this entry, or the script would miss the drop-pod
  event; it presumably governs only timed GMEvents, of which this file has
  none. The Guide_HOTH entity is therefore live from the start of play.

### Despawn, respawn and limits

- **Read.** One despawn: `[189]`, applied to every BotControlled entity when a
  Hive dies. No script respawns or replaces a bot. A bot that dies earlier is
  not replaced.
- **Read.** The Saboteur is spawned after the despawn loop, so no more than
  three bots are alive together (the three allies), then one. No count limit is
  authored in the script.
- **Unknown.** Whether the mode's own code (`SequentialHives`,
  `HivesSpawnHearts`) or `GuideBotSpawnerComponent` adds or limits bots. The
  server implementation is not in the install: the client's `SpawnGuideBot::Execute`
  (xref `--func 0x3a01100`) loads the string "SpawnGuideBotActionCommon: This
  action does not have any effect on client side. It was designed to be used on
  a server only script" and then the "Aborting SpawnGuideBot. Reason: %s" format.

### Where the bots stand

**Read.** There is no entity, tag, node-name or hive input. The location is a
Transform plus `Radius` 25.0, nav layer `Bipeds` and BlueNoiseSettings. The
client exe carries editor tooltips for the settings (file offsets
0x6a98900 to 0x6a98ca0):

- Inner Exclusion Radius: "Blocks spawning of points inside this distance
  from the center."
- Radius Percentage: "The largest percentage of the 'global radius' to
  allow candidates to be selected from."
- Radius of spawned item: "Spawn locations will be chosen to ensure they are
  at least this much away from the calculated DF Edge or InnerExclusionRadius"
- Candidate Count: "How many candidates we will generate to find the next
  random location."
- Candidate Scalar: raises the candidate count as more points are selected;
  0 turns that off.
- Force Center Spawn: "Forces the first point selected to be the center of the
  'global circle'."

**Inferred.** The spawn point is a `Bipeds` navmesh point between
`InnerExclusionRadius` and `Radius x RadiusPercentage` from the transform's
position, kept `RadiusOfSpawnedItem` clear of the inner boundary and of the DF
edge. That puts the allies about **22 to 25 m** from the hive transform and the
Saboteur about **6 to 25 m** from the heart. I read "DF" as death fog, because
the overlord spawn data next to these fields has `DeathFogClearTime` and
`AdditionalDeathFogRadiusReduction`; if it means a distance field that also trims
the outer edge, the allies' ring would be 22 to 23 m. Unknown: 2D or 3D
distance, how the best of the five candidates is chosen, whether earlier bots
influence later picks, and what an abort does. The sampler is server code. I
also assumed the HiveTelegraph, Hive and HiveHeart entities sit at the chosen
`Spawner_Hive` node's transform, and that the layer name `Bipeds` (used by
other scripts too, for example the essence drop on player death) is the one
agent the baked mesh holds: HotH_02's `mnmnavmission0.bai` has a single agent,
`MediumSizedCharacters`, which is what `navigation.installed_mesh` reads. I did
not check either.

### The twelve `SpawnNode_Guide_EnemyBot` nodes

- **Read.** The type `GuideEnemyBotSpawner` appears in `misc.pak` in three
  places only: the enum `entitytypevalue.names`, the slice
  `slices/spawn_nodes/spawnnode_guide_enemybot.dynamicslice` (entity type and
  nothing else: no team, tag or hive field), and one node of the critical path.
  That node is `[246]`, in the dead portion: a sphere query of that type with a
  literal origin of (0, 0, 0) and radius 50 m. Its result `[587]` -> `[446]`
  (`ExternalGet_10.OutputValue`) is read by no node. HotH_02's nodes are 750 to
  1300 m from the origin, so even wired it would find nothing. The type also
  appears in `levels/HotH_02/commondata/level.dynamicslice` (12 entities) and
  nowhere else in `levels.pak`, `config.pak`, `gamedata.pak`, HotH_02's
  `level.pak` or the exe (neither the name nor its CRC-32).
- **Read.** The twelve entries 33..44 differ from each other only in entity id
  and transform. Nothing ties a node to a hive; "two beside each hive" is
  proximity alone. By 2D distance to the nearest hive:

  | Hive | Nodes (distance, m) |
  |---|---|
  | SWAMP | 36 (25.4), 42 (27.2) |
  | RESEARCH | 44 (23.7), 39 (25.8) |
  | SPACEPORT | 41 (25.9), 34 (27.8) |
  | GROTTO | 43 (29.7), 33 (33.4) |
  | LOG | 37 (31.0), 40 (32.0) |
  | FALLS | 38 (35.4), 35 (36.0) |

- **Inferred.** They are the older, node-based placement. The dead Saboteur
  chain queried them within 50 m, and an unused earlier hive-heart script
  (`guide_onspawn_hiveheart.cdl`, referenced by nothing) already used the
  hive-relative pattern: a robodummy slice at the heart's transform, radius 25,
  inner exclusion 10. Three hives' nodes (SWAMP, RESEARCH, SPACEPORT) lie within
  3 m of the allies' 25 m outer edge; the other three hives' nodes (LOG, GROTTO,
  FALLS) lie 5 to 11 m beyond it. So they approximate the ring, but they are
  not the retail rule.
- We cannot rule out some server-only use of the nodes, since the server binary
  is not in the install.

### Hive order

Not in the script, and not in the data I read (the six node records,
`hoth_guide.sequentialgmdata`, `hoth_guide_no_overlord.aioverlordspawndata`).
The critical path calls `SpawnNodes_AIOverlord` once, with no hive named. The
six hive nodes (entries 1605..1610, entity type `Spawner_Hive`) differ only in
name and transform; their order in the entity array is SWAMP, LOG, RESEARCH, GROTTO,
FALLS, SPACEPORT, and their entity ids sort as GROTTO, FALLS, SWAMP, LOG,
SPACEPORT, RESEARCH. Neither is shown to be the selection rule. The mode's
`SequentialHives` flag and the overlord's pick are server logic. The script
also ends after the first heart, so it says nothing about the second and
third hives. If the mode spawns a second hive while the critical path is still
polling, the `== 1` checks at `[741]` and `[693]` never succeed and the guide
stalls.

### What this means for the server

All **inferred** from the above.

1. Do not spawn any bot at the drop or at connection. The faithful earliest is
   the player's first approach to a hive.
2. Replace nodes 44, 36 and 39 with hive-relative positions: three allies on
   the player's team on the nav mesh about 22 to 25 m from the first hive,
   spawned Duelist, Hero, Inventor, 0.3 s apart. Spawn one hostile Saboteur
   about 6 to 25 m from that hive's heart when the hive dies, after removing
   the allies. Where mesh sampling fails, the two guide nodes beside that hive
   are the nearest authored stand-ins (but 33, 34, 41 and 42 are off the mesh,
   per the stage 2 notes above).
3. Roster and teams are asymmetric: the three allies friendly, with AI that
   targets Player and Hive; one Saboteur that the script treats as the enemy.
   Until hives exist in the server (stage 5), the allies have nothing to
   target.
4. "Add respawn per `guide_respawnparams`" (docs/37 stage 4) concerns the
   player, not the bots. Retail bots do not respawn.
5. Which hive is first is a free choice to make explicit in the server until
   it is measured live.

### Open questions

1. **Which `Spawner_Hive` node the overlord picks at `[64]`.** Every ally and
   Saboteur position derives from it. Neither the script nor the six node
   records say. Needs the server code (not installed) or a live capture of the
   telegraph's position.
2. The Saboteur's default team.
3. The sampler details above (distance metric, candidate selection, effect of
   earlier bots, abort behaviour).
4. Whether mode-level code adds, limits or respawns bots outside the scripts.

### Corrections to earlier notes (made on 25 October)

- `snapshots/accuracy-20260924/bot-authored-params.md` section 1, item 6: "8
  `SpawnGuideBot` action nodes" should read 4 (the BotID list there is right).
- `docs/37-hoth-guide-plan.md` lines 32 to 33 ("the eight SpawnGuideBot nodes")
  and the stage 4 item ("eight SpawnGuideBot calls"): four. The "Placeholder
  spawns" bullet under "Per-map navigation" and the `solo_modes.py` comment
  before `HOTH_02_BOT_NODES` say stage 4 replaces the placeholder with "the
  critical path's SpawnGuideBot choices"; those are hive-relative rings, not
  guide nodes.
- `docs/23-maps-and-modes.md` ("These are the HOTH_GUIDE bot spawns", after the
  twelve coordinates) and the `HOTH_02_GUIDE_BOT_SPAWNS` comment in
  `server/solo_modes.py`: the nodes are authored but no running script uses them.

### Commands

```
# from the repo root
PY="$PWD/.venv/bin/python"
STEAM=~/.local/share/Steam/steamapps/common/Crucible
PAK=$STEAM/misc.pak
S=$(mktemp -d)        # any scratch directory: the dumps are large
mkdir -p "$S/hoth02"
SCRIPTS=snapshots/guide-spawns-20261025/scripts

# script bytes (sha256 as above)
"$PY" -I tools/pak.py cat "$PAK" cdl/gamemodes/guide_hoth/guide_criticalpath.cdl --out "$S/guide_criticalpath.cdl.bin"

# node list, comments, edges. Its Transform values are placeholders.
"$PY" -I tools/inspect_creature_cdl.py "$PAK" cdl/gamemodes/guide_hoth/guide_criticalpath.cdl \
  --json "$S/critpath-creature.json" > "$S/critpath-creature.txt"

# bindings, conditions, edge ordinals. Exists only in the bot-weapon worktree
# (tools/inspect_cdl_logic.py, SHA-256 cdc8b9bf7226f229a5826d3470847ab72b5051dd458372cf74d7400b97737286); run it from that tools dir.
(cd .worktrees/bot-weapon/tools && "$PY" -I inspect_cdl_logic.py "$PAK" \
  cdl/gamemodes/guide_hoth/guide_criticalpath.cdl --json "$S/critpath-logic.json" > "$S/critpath-logic.txt")

# the other guide_hoth scripts and event_guide_hoth.cdl, same tool
"$PY" -I tools/pak.py list "$PAK" > "$S/misc-pak-list.txt"
mkdir -p "$S/guide_hoth_logic"
for a in $(grep -E '^cdl/gamemodes/guide_hoth/[^/]+\.cdl$|^cdl/planetaryevents/event_guide_hoth\.cdl$' "$S/misc-pak-list.txt"); do
  (cd .worktrees/bot-weapon/tools && "$PY" -I inspect_cdl_logic.py "$PAK" "$a" > "$S/guide_hoth_logic/$(basename "$a" .cdl).logic.txt")
done

# which scripts the mode loads, and the mode's event data
"$PY" -I tools/pak.py cat "$PAK" cdl/gamemodes/guide_hoth/guide_hoth_scripts.cdlscriptslist --out "$S/guide_hoth_scripts.cdlscriptslist"
grep -a -o 'cdl/[A-Za-z0-9_/.-]*' "$S/guide_hoth_scripts.cdlscriptslist"
"$PY" -I tools/pak.py cat "$STEAM/NvGameSdk/levels/HotH_02/level.pak" resourcelist.txt | grep -i guide_hoth
"$PY" -I tools/pak.py cat "$PAK" assets/planetaryevents/hoth_guide.sequentialgmdata --out "$S/hoth_guide.sequentialgmdata"

# the exe's BlueNoiseSpawnSettings tooltips (file offsets, not RVAs)
"$PY" -I -c "d=open('$STEAM/bin/Crucible.exe','rb').read(); print(d[0x6a98900:0x6a98ca0].replace(b'\0',b'\n').decode('latin1'))"

# HotH_02 nodes: twelve guide nodes (33..44), six hive nodes (1605..1610)
"$PY" -I tools/pak.py cat $STEAM/levels.pak levels/HotH_02/commondata/level.dynamicslice --out "$S/hoth02/level.dynamicslice"
"$PY" -I tools/inspect_map_entities.py "$S/hoth02/level.dynamicslice" --range 33:45 --positions
"$PY" -I tools/inspect_map_entities.py "$S/hoth02/level.dynamicslice" --range 1605:1611 --positions

# who mentions the type or calls SpawnGuideBot, in every pak (grep_all_assets.py prints per-asset counts)
"$PY" -I tools/pak.py grep "$PAK" 'GuideEnemyBotSpawner|EnemyBotSpawn|Guide_EnemyBot'
for p in "$PAK" $STEAM/levels.pak $STEAM/config.pak $STEAM/gamedata.pak $STEAM/NvGameSdk/levels/HotH_02/level.pak; do
  "$PY" -I "$SCRIPTS/grep_all_assets.py" "$p" "SpawnGuideBot,GuideEnemyBotSpawner,Guide_EnemyBot,GuideBotSpawner,BotControlled"
done

# the exe: client-side stub and its xrefs
"$PY" -I tools/xref.py --strings "Aborting SpawnGuideBot,unsigned char __cdecl Crucible::SpawnGuideBot::Execute"
"$PY" -I tools/xref.py --func 0x3a01100
```

The scripts (read-only; each reads the file it is given, and the exe for
field-name lookup) are in `scripts/` beside this file:
`$SCRIPTS/raw_walk.py <cdl.bin>` (the 56 string hits and where they sit),
`portions.py <cdl.bin> <out.json>` (portions and node membership),
`flow.py <critpath-logic.json> summary | labels | path <ids>` (reachability and paths),
`graph_checks.py $S` (node classes, entry points, cycles, delay sums),
`comments.py <cdl.bin> <out.json> spawn | boxes` (comment boxes by position),
`verify_bindings.py <cdl.bin>` (raw variable-id check),
`portion_survey.py $PAK x <out.json>` (all 1,545 scripts),
`hoth02_nodes.py <level.dynamicslice> <entries> cmp | dump` (node data and diffs),
`who_references.py $PAK <targets.txt>` (which assets mention each guide script).
The commands above recreate the dumps in `$S`: `critpath-logic.txt`,
`critpath-creature.txt`, `node-comment-boxes.json`, `portions.json`,
`portion-survey.json` and `guide_hoth_logic/*.logic.txt`.
