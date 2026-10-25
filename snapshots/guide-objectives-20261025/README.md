# Objective slices: their network component sets, derived offline (25 October)

Can a HotH objective entity's shared component set be derived offline? Read
docs/37, stage 5, first. Nothing here is game data: only UUIDs, network type
numbers and slice names.

- `marine-uuid-types.json`: the Marine player character's 81 components, each
  with its network type. The type comes from the component's live type getter,
  decoded in the exe. Of the 81, 71 are network components, and their
  direction-0 set equals `hero_loadout.SHARED_TYPES` exactly. The pairing with
  the slice's components is by position. Four exe name labels contradict it.
- `coverage.py`, `coverage.txt`: every slice in misc.pak whose path names a
  hive, heart, harvester or objective, entity by entity. There are 45,
  including test mocks, wasps and tutorial waypoints. Each entity lists its
  network component count and its shared types, from `uuid-type-table.json`,
  and any component it cannot classify (none). Run it read-only with
  `.venv/bin/python -I snapshots/guide-objectives-20261025/coverage.py`.
- `uuid-type-table.json`, `uuid-route/`: the exe-derived UUID-to-type table
  and the scripts that build it (below).
- `objective-shared-sets.json`: the main objective entities' shared,
  autonomous and network type lists.
- `stage5-rules.md`: HOTH_GUIDE's objective and score rules, decoded script by
  script with node citations, and marked read, inferred or unknown. docs/37's
  "Stage 5 findings" summarises it.
- `uncovered-uuids.json`: the 28 UUIDs the Marine table did not cover. Each
  has the number of entities using it, any exe `{UUID}\0\0Name` label (a
  lead, not proof: on the Marine, 4 of 30 were wrong) and its resolved class
  and type from the exe route (18 network, 10 not).

Result, first pass (the Marine table alone): no objective entity was fully
covered, and 28 distinct UUIDs were missing.

Result, second pass: the exe route resolves them all. `uuid-route/` holds it:
- the braced UUID literal in `.rdata`;
- the `Uuid()` function that builds it (the referencing `lea` lands a byte or
  two past the literal, inside an inlined hex-parse loop);
- the class vtable whose slot 0 thunks to that function. Slot 1 returns the
  class name, and slot 14 is the network type getter (`mov al,N; ret`).

`uuid-type-table.json` maps 211 UUIDs to their type: every one of the 180
schema types, through `::Client` and `::ClientBase` classes. Validation:
- the Marine's 81 components, type and live vtable, all exact;
- MatchPhaseController, GameMode and MatchDataStore, 23 of 23;
- three live sets the route never used, all exact: the spitter's 18 shared
  types, the RoboDummy's 15 and the connection entity's 8, from the type
  getters in `snapshots/gamemode-20260919/live-upright-npcs.json` and
  `live-npc-placement.json`.

The four contradicting name labels were label errors, not pairing errors.
Every component of the 45 slices is now classified (`coverage.txt`), and
`objective-shared-sets.json` gives the main entities' shared sets:

| Entity | Shared types |
|---|---|
| Hive_Standard | 22 |
| CruciHeart | 9 |
| Harvester_A (entry 1 of 4) | 11 |
| VictoryObjective | 10 |
| HiveTelegraph | 8 |
| Wasp | 14 |

Regenerate the table read-only from `uuid-route/` with the project venv,
which has pefile and capstone:
`../../../.venv/bin/python forward.py && ../../../.venv/bin/python full_table2.py`.
It writes `forward_map.json` and `uuid_type_table.json` in the working
directory.
