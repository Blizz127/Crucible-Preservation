# Hunter navigation and combat work

After Alpha Hunters entry and three visible Marine hunters were confirmed,
the user requested movement/combat next, followed by character selection and
missing abilities. Those later issues are still open: startup forces Marine,
and the visible HUD has empty ability slots.

## Navigation-backed patrol candidate

`server/navigation.py` reads the installed April2018 `mnmnavmission0.bai`
from `NvGameSdk/levels/april2018/level.pak` without modifying any retail file.
The format matches MNM version 7/configuration 6. It contains 43,130 triangles
and 99,256 links for MediumSizedCharacters. All input bytes are consumed and
declared mesh/agent sizes agree. Vertex indices, link ranges, and destinations
are validated. Off-mesh traversal is excluded.

Format references:

- [NavigationSystem::ReadFromFile](https://github.com/aws/lumberyard/blob/master/dev/Gems/CryLegacy/Code/Source/CryAISystem/Navigation/NavigationSystem/NavigationSystem.cpp)
- [Tile layouts](https://github.com/aws/lumberyard/blob/master/dev/Gems/CryLegacy/Code/Source/CryAISystem/Navigation/MNM/Tile.h)
- [Tile neighbour offsets](https://github.com/aws/lumberyard/blob/master/dev/Gems/CryLegacy/Code/Source/CryAISystem/Navigation/MNM/MeshGrid.cpp)

The initial patrols connect the three authored drop points, following triangle
links and edge midpoints. Route lengths are 8.90, 19.05 and 10.91 metres.
779 sampled route positions all lie on the installed mesh within 0.03m
vertical tolerance. The general route helper still needs further handling
of partially overlapping tile-border edges before use for arbitrary paths;
the three installed test routes are the verified bounded scope.

`CRUCIBLE_SOLO_PATROL=1` enables these routes for profiles with hunters.
Initial hunter positions use their nav-surface projection (changes in Z
of less than 0.51m). At 20Hz the server sends native Marine Location deltas
only to simulation entities 01000003..05; position advances at a conservative
1.5m/s test speed, reversing at route ends. Rotation faces travel. This is
authoritative movement of server-owned bots, not a player teleport or client
memory write. The local player and Practice population retain their paths.

Seventy server tests and hub protocol checks pass. The opt-in shard drop-in
`hunter-patrol.conf` was enabled with Crucible absent, then the shard restarted.
Fresh launch needed: verify the three hunters move along the path, remain on
the ground, face travel, and do not disconnect the client. Walking animations
are unverified; targeting, bot fire, validated damage, death, respawn, and
ability/loadout restoration are not implemented by this increment.

Disable the patrol override and restart only after the user closes Crucible
to return to the visually accepted stationary population. Preserve the Alpha
Hunters profile and original Training Arena baseline.
