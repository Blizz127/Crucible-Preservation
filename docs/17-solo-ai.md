# Solo creatures and hunter bots — in progress

Requested scope: restore creatures first, then AI-controlled hunter opponents,
and extend solo play to the game's other areas. Training Arena entry, camera,
walking and jumping are already accepted; preserve that working baseline.

## Current findings

The local shard currently replicates startup and a monotonic clock. It does
not implement NPC decisions, authoritative combat, navigation or respawning.
Visible population is a prerequisite, not proof that those systems work.

Installed retail `misc.pak` contains `ai/spitter.ai`, `ai/megadrone.ai`,
creature slices, attack/death scripts and Practice tracker scripts. The
spitter slice references its AI definition and attack/alert/spawn scripts.
AIComponent type 2 has two shared record bits and no autonomous direction;
no AIComponent::Authority/Autonomous/ Predictable implementation was located
in the client by the initial symbol-string check. That check is not proof
that every relevant common behavior is absent. AI execution needs tracing.

Practice's direct baked entity array has 1198 entries. Entries 138/139 are
SpitterRespawners referencing practicearena_spittertracker.cdl; entries
73..80 are RoboDummy entities. Tracker metadata includes OnSpawnEvent,
Duration, SpawnSlice, Transform, and TelegraphTime. Entry 124 is
Dummy_AI_Overlord referencing gameplay/spawning/spawn_matchstart.cdl.

The installed level pack contains April2018, HotH_02, PracticeArena,
Tutorial_02 and Tutorial_03 gameplay level slices, plus MainMenu. Each area
needs its own mode, spawn and encounter audit before solo acceptance.

## First population probe

New opt-in `CRUCIBLE_PRACTICE_NPCS=1`, alongside `practice-character`, sends
2 spitter slices (hash 1141747700, entry 0, network counters 3/4) at the two
authored respawner positions, then activates the eight baked RoboDummies
(entries 73..80 / FE00004B..FE000052). All are simulation role 1. Location
uses the verified packed transform; spitters retain the authored identity
rotation and targets use their authored 180-degree Z rotation. Other
properties retain asset defaults. No client files or memory are patched.

Metadata-only positions and schema IDs are in server/practice_population.py.
50 server tests pass. The probe is bounded, per-session and exactly once
following character links, before advancing clock packets. It is disabled
by default. NPC visibility, live AI state, shooting/damage, decisions and
respawn still require runtime observation. Hunter bots and other areas are
not implemented. This document will retain those distinctions as work proceeds.

## Orientation correction prepared

The user observed visible targets upside down near the lights. Read-only
`snapshots/gamemode-20260919/live-npc-placement.json` shows all ten NPCs
active and their positions matching the authored coordinates. The first
probe incorrectly treated the quaternion as XYZW. Native CryQuat memory
order is WXYZ: connection identity and the moving player's rotation have
their scalar component first. The dummy packet selected component 2,
producing a 180-degree Y rotation, instead of component 3 for authored Z.

The correction selects component 3 for dummies and component 0 for identity
on spitters and initial player spawn. Positions are unchanged. A regression
test decodes the packet and checks upright and forward axes against the
authored transforms; it failed before the fix and passes afterward. The
correction still needs a fresh launch and visible acceptance. Desktop
capture attempts showed Codex, so no game screenshot has been obtained.

## Upright population accepted; combat input observed

The user confirmed the corrected models look good, remain stationary, and
do not take visible damage. `live-upright-npcs.json` verifies both spitters
and all eight targets active, with corrected WXYZ rotations. Native asset
health defaults are 400/400 for spitters and 600/600 for targets.

A requested aimed burst produced twelve unique component 115 / RPC 4
messages on character 01000002, projectile IDs 48..59. All contain target
FE00004B and the shooter 01000002. Evidence is in
`projectile-result-observation.json`. Retail 9d9120 names this message
ClientProjectileResult: u8 projectileId then prevHitEntities; 883530 ->
889840 bounds the vector to eight full network IDs. The added strict
decoder rejects truncation, trailing bytes and oversized vectors. All 52
server tests pass. This is client-reported contact history, not validated
damage or a list of recipients to damage.

The current RPC observer ignores the character entity; it only tracks the
connection entity. Server-side projectile validation, weapon damage and
health replication remain missing. No damage has been fabricated from
the hit list and the running service has not been restarted for this
diagnostic-only decoder. Next trace: ProjectileRequest autonomous-to-
authority properties and the weapon definition, then validated target
damage and its health/death updates.

AIComponent's two shared record bits are root plus InVelocityOverride,
seen in 785790 -> 785b30. That property is a bool (786620); it is not an
AI-enable switch or a navigation target. Creature behavior still requires
the missing authoritative decision/simulation path.

## Retail combat configuration and health wire trace

`combat-asset-fields.json` records source hashes, offsets and scalar values
from the installed assets. Spitter MovementEnabled and CanPatrol are both
false; stationary placement is expected for these creatures. Aggro radius
is 5..20, deaggro radius 25, initial target attack delay 1 second, and attack
range 0.1..40. Restoring this creature requires target/attack behavior, not
inventing a roaming path.

Marine primary uses Hybrid projectiles with speed 625, an Enemies filter,
HealthChangeImmediateOnHit float pair (20,10), and CriticalMultiplier 2.
Damage interpolation and critical classification still need tracing before
claiming a faithful damage calculation.

Health state serializer is 9355f0 -> 9359a0 -> 93d870 -> 8c3780 ->
34cbea0. The default resource serialization includes five regeneration/time
fields and then minimum, maximum and value (35dc910). Vtable +78 is the RPC
handler, not the state serializer; the inspector now labels +78/+80
correctly. It is prepared to capture Health, AI and ProjectileRequest
replication graphs and resource timing values read-only on the next launch.
No combat packet is enabled yet; no services restarted during this trace.

The subsequent user-launched run supplied `live-combat-layout.json`.
Health's property record indices are [1,2,3,4,5,23], including a nested
resource subtree at 23..26; all observed NPC regeneration/timing values
are zero. This resolves the missing layout capture, but packet encoding
and actual damage delivery still need implementation and verification.
The user requested Heart of the Hives with hunter bots next; the mode
audit and acceptance gates are in `18-heart-of-the-hives-solo.md`.

## Nonlethal reported-hit test prepared

`CRUCIBLE_PRACTICE_DAMAGE=1` enables a deliberately limited combat test
after the NPC population is sent. Only reliable delivery-3 component 115
RPC 4 from the local character 01000002 is accepted. Exactly one known NPC
must appear in the report; shooter/unknown IDs cannot receive damage.
Projectile IDs use forward modulo-256 ordering to reject duplicates and
older results. Transport duplicate suppression also remains in place.

Each accepted result subtracts 20 HP, clamped to 1. This is a fixed body-hit
test, not range/critical/physics-validated combat. It does not implement
death, hit animations, hit indicators, respawn or AI decisions. The clamp
keeps this probe separate from unimplemented death processing. Dropped UDP
updates are covered by repeating absolute health on subsequent clock ticks.

Health compressed traversal selects component root, Resource at record 23,
DefaultResource at 24 and value triplet at 26; timing group 25 stays clear.
Min/max/current are three big-endian floats. NPC slice and placement are
unchanged. All 57 server tests pass, including malformed result rejection,
sender/target gates, replay suppression, nonlethal bounds and repeated
health packets. Native deserialization and visible health change are still
unverified; test remains disabled until a guarded service restart.
