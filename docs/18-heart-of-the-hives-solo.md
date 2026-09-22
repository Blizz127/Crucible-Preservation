# Heart of the Hives solo restoration

User requested this mode with hunter bots after accepting upright Training
Arena population. Preserve the accepted arena baseline while preparing the
mode. See the latest dated observations below for installed mode selection.

## Installed asset findings

`levels/HotH_02/commondata/level.dynamicslice` exists in levels.pak. Its
largest direct Entities array contains 21510 entries. Candidate baked
component entities: MatchPhaseController 617, GameMode 621, MatchDataStore
627. These are asset indices, not yet verified live network IDs.

`design/gamemodes/heart_of_the_hives.gamemodeconfig` has MinimumPlayers 8
and TargetScore 3. Its phase sequence is WaitForPlayers, PerkPreview,
TeamInfo, PerkSelect, DropPreview, DropSelect, GamePlay, GameOver, Shutdown.
Solo startup must count populated bot slots appropriately and must never
wait for seven other human connections. Phase setup needs its own trace;
reusing Practice's baked entity IDs would target unrelated HotH entities.

The map includes Guide_EnemyBotSpawns (11940), twelve guide enemy spawn
nodes (33..44), and hive AI-overlord spawn nodes (1605..1610) for Swamp,
Log, Research, Grotto, Falls and Spaceport. Installed guide AI definitions
exist for Marine, Inventor, Duelist, Saboteur and generic hero dummies.
These assets are useful references, not proof of functioning hunter bots
or objective-capable teammates.

## Implementation order and acceptance

1. Per-mode level/configuration selection and correct baked component IDs.
2. Solo roster, team allocation and authored player/bot spawn setup.
3. Shared combat processing with hit validation, health, death and respawn.
4. Creature targeting/attacks and hunter movement/combat decisions.
5. Hive encounters, heart capture/scoring, match completion and replay.

Acceptance requires a user-played solo match: no human waiting gate,
controllable hunter, functioning allied/opposing bots, creatures that
react and attack, capturable hearts, a real win/loss, and another match
without restarting the client. None of these HotH runtime gates is passed.

## Explicit goal milestone and first candidate

The user subsequently set a narrower active goal: Heart of the Hives
loadable solo with some hunter bots inside. Visible hunter presence and
actual map entry are this milestone; the full gameplay acceptance above
remains later work. The goal is not complete until observed in-game.

`CRUCIBLE_SOLO_MODE=heart-of-the-hives` now selects configuration index 8
and the shipped April2018 map. HotH_02's hash library has no matching
HotH_02 level entry; April2018 has an explicit level hash 161882647.
This is the selected test map, not proof of the original service's map
selection policy. April2018 has 19529 entities. Its phase/mode/store
entries are 1656/1660/1666; component UUID lists exactly match their
Practice counterparts (see `hives-component-census.json`). The entry+2
baked network-ID mapping still needs live confirmation on this map.

The player uses authored DropPoint_17d (entry 173); three Marine simulation
entities, IDs 01000003..05, use nearby authored 17c/17a/17b positions
(181/246/268). These are hunter shells for presence testing; navigation,
combat AI, team/roster bot integration and objectives are not implemented.
The bootstrap sends the solo GamePlay phase, bypassing waiting for other
human connections. Existing player controls and monotonic clock are reused.

All 60 server tests and the hub protocol checks pass. The hub and shard
were restarted only after confirming no Crucible process. Their new
`solo-mode.conf` drop-ins select this candidate; the nonlethal damage
experiment remains disabled. Setting both mode drop-ins to `practice`
restores the accepted arena profile. First user launch is pending.

Before the first launch, the spawned roster was extended to four entries:
local player slot 1 and Hunter 1/2/3 in slots 2..4. CharacterIds maps to
01000002..05; local team is 1 and hunter roster team is 2. This is roster
bookkeeping only, not completed team-aware AI or combat. The initial
pre-character roster stays one entry. Practice serialization remains
unchanged and all 61 tests pass. Shard was restarted after another absence
check. Live activation and map visibility remain pending user launch.

## First launch: map loaded, baked-ID mismatch localized

NovaGameClient-20260919T230712Z.log confirms the hub selected April2018 and
the native level loader loaded it. Read-only observation shows the player
and all three Marine hunter entities active (state 4), at their intended
drop coordinates. The local controlling/following links both target the
player and the monotonic server clock advances. This does not establish
visible map entry: the user still sees Connecting.

The log repeatedly reports GameModeComponent not ready. The replicator
tree identifies the cause: baked mode/phase/store entries 1660/1656/1666
are keyed by FE00098F/FE00098B/FE000995, and remain inactive state 2.
Practice's entry+2 formula does not hold on this map; the observed bias
is 787. Evidence: `hives-first-replicators.json`. The profile now carries
that explicit bias for all bootstrap and relative-ID links; Practice keeps
2. All 61 tests pass. A restart/fresh user launch is required to verify
stable IDs and actual activation. No restart performed with the game open.

## Second launch and Alpha Hunters request (23:16–23:21 GMT)

NovaGameClient-20260919T231447Z.log confirms the corrected baked IDs activate
the match store and phase controller. The client recognizes HEART_OF_THE_HIVES
and advances Connecting to PerkPreview, then disconnects with StreamError:
DataStoreMatch consumed 73 of 160 bytes in the spawned four-player roster.
Visible playable entry remains unverified.

The exact defect is boolean packing. Retail reader 4f565d0 retains a bool-byte
offset and bit index across intervening reads; each PlayerData DropMaster
consumes one bit of that shared byte. The previous encoder wrote a separate
byte for each bot. After the first bot's DropMaster, its extra byte shifted
RespawnWaveIndex and CharacterStatus. The encoder now supplies one byte per
eight roster bools. A decoder regression failed on the old second-entry
respawn/status bytes and passes after correction. The one-player Practice
packet remains byte-identical. All 63 server tests and hub checks pass.

The user also requested Alpha Hunters/the default mode. Added explicit
`alpha-hunters` (DOUBLES_CASUAL, configuration 1) and `solo-casual` (configuration
0, the shipped list's default) profiles. The Alpha Hunters mapping is supported
by the menu's mode_alphaHunterDoubles_unlock entitlement. Both reuse April2018
and the three Marine presence-test entities. These are unverified entry
candidates, not completed hunter AI, alliances, elimination or victory logic.
The next selected test is Alpha Hunters; Hives remains selectable and its
entry goal is still incomplete. The hub and shard must use the same profile.

## Alpha Hunters entry accepted (23:23 GMT)

User reports "I'm in!" on the Alpha Hunters candidate. Read-only observation
(`live-alpha-entry.json`) confirms Connected state 8/status 4, GamePlay phase 8,
all three baked controllers active, four accepted roster entries, and three
Marine simulation hunters active with 800 health at the authored drop points.
The player has moved away from the initial spawn; control/follow links target
01000002 and server/input clocks agree at 68581 ms with zero input offset.
Hunter visibility still needs user confirmation; navigation and combat AI
remain unfinished. This verifies Alpha Hunters map entry, not a completed
Heart of the Hives entry test or either mode's complete gameplay.

## Hunter visibility remains unresolved

The user reports no visible hunters. Do not treat active-state 4 as visual
acceptance. The player observation at (1231.541,1318.221,29.339) is about
100 metres from the authored hunter cluster. Live Location renderer transform
(+1440), populated by native c406b0, matches the hunter spawn coordinates;
the earlier zero values at +c0/+d0 are not sufficient evidence of incorrect
render placement. The player and first hunter both have skin selection 0.
A desktop capture shows the player model and Remaining 4, with the camera
pointed upward; it does not show the hunter cluster. A ground-level visual
check at the cluster is pending. No speculative visibility or transform
changes were made, and services were left running.

## Alpha Hunters visual population confirmed

The user found a hunter and requested a close capture.
`alpha-three-hunters-visible.png` shows all three upright Marine hunters
along the shaded rock path, plus the controllable local player. Paired
read-only state confirms hunter distances 3.58, 11.52 and 21.11 metres.
Alpha Hunters solo map entry and visible three-hunter population now have
human and screenshot confirmation. No visibility fix was needed. They
remain stationary presence-test entities; combat and navigation AI are
unfinished. Heart of the Hives still requires its own post-roster-fix launch.
