# Objective slices against the live Marine component table (25 October)

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
  covered network types, the shared ones among them, and every component UUID
  the table does not cover. Run it read-only with
  `.venv/bin/python -I snapshots/guide-objectives-20261025/coverage.py`.
- `stage5-rules.md`: HOTH_GUIDE's objective and score rules, decoded script by
  script with node citations, and marked read, inferred or unknown. docs/37's
  "Stage 5 findings" summarises it.
- `uncovered-uuids.json`: the 28 distinct uncovered UUIDs. Each has the
  number of entities using it and any exe `{UUID}\0\0Name` label. Such a label
  is a lead, not proof; on the Marine, 4 of 30 were wrong.

Result: no objective entity is fully covered. Components of unknown type per
entity (some are probably not network components at all, such as
AudioEnvironmentComponent, MeshComponent and SimpleMotionComponent):

| Entity | Uncovered |
|---|---|
| Hive_Standard, CruciHeart, VictoryObjective | 9 each |
| Harvester_A to _E | 13 each |
| Telegraphs | 2 to 4 |

Eleven of the 28 have no exe label, and three more carry junk ones (two EBus
signatures and "Effect Scale"). The most common unlabelled UUIDs:
- `65a2dd2b` in 46 entities;
- `6e413692` in 22, also on every MatchDataStore and MatchPhaseController;
- `f970cf86` in 19;
- `c8ad3419` in 17.

The slices are addressable: april2018's (and every other shipped level's)
`commondata/stringhashlibrary.xml` in levels.pak names the hive, cruciheart,
harvester, victory-objective and telegraph slices. (It names no technomancer
character slice, as `hero_loadout` says.) So a hive could be spawned in
`hoth-guide-april2018` once its component set is known.

Resolving these 28 network types would make the objective sets derivable,
still subject to the position caveat. The name-free route is untested: from
the UUID literal, find the code using it, then its vtable's type getter.
