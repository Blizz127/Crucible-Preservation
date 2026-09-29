# Marine locomotion: replicated movement state and client-derived velocity

`docs/21-hunter-ai.md` concluded from static symbol absence that "locomotion
animation is driven client-side from the character's own observed motion, so
sending Location deltas at 20 Hz should be sufficient". In the 2026-09-21 live
run, three bots on a **constant** replicated position (simulation speed 0.00,
0.034 m drift over six seconds) nevertheless played a **run cycle**.

The 22 September trace resolves the two separate inputs. Native client code
does derive blend velocity from interpolated Location changes. The replicated
`time_since_movement_input` property independently selects locomotion versus
idle, and its native zero default selects locomotion even at zero velocity.
The earlier draft's claim that no client velocity mechanism exists was wrong.
The authored graph and native producer traces below must be read together.

> **Status key.** Every claim below is tagged. **PROVEN** = read directly out of
> an installed asset or the executable, with the path/offset/RVA given.
> **INFERRED** = a specific deduction from proven facts, with the gap named.
> **SPECULATIVE** = a hypothesis with a stated way to test it.

## The asset nobody had opened: `marine.animgraph` is XML

**PROVEN.** `animations/characters/player/marine/emfx/marine.animgraph` lives in
`zero2.pak`, is 2,331,213 bytes, sha256
`7a491d06665129b18d9d6b407e9382705e33394a099bc9d787d06e09dcfed083`, and its
first bytes are:

```
<ObjectStream version="3">
	<Class name="AnimGraph" version="1" type="{BD543125-CFEE-426C-B0AC-129F2A4C6BC8}">
```

So it is an **XML** ObjectStream, not the EMotionFX native binary chunk format
and not the CRC32-keyed binary ObjectStream that `tools/inspect_creature_ai.py`
exists to decode. Field names, node names, parameter names, transition
conditions, blend-space coordinates and motion ids are all present as literal
text. No reverse engineering is required to read the entire authored animation
logic of the game.

The reference chain is **PROVEN** too: `slices/player/marine/marine_character.dynamicslice`
(misc.pak) names `animations/characters/player/marine/emfx/marine.animgraph` at
offset 0x5706, `…/marine.motionset` at 0x578f,
`animations/characters/player/marine/character/marine_actor.actor` at 0x51d5 and
`assets/player/marine/marine.animconfig` at 0x24fc.

New read-only tool: **`tools/inspect_anim_graph.py`** — flattens the ObjectStream
tree and prints `--params`, `--nodes`, `--grep`, `--node NAME`, `--blendspace`
and `--param-users NAME`. It resolves node ids to names so connections and
transitions read as a graph. It never writes under the Steam install and never
launches the game.

## The 35 animation-graph parameters, with their authored defaults

**PROVEN**, `--params` on the asset above. These are the *complete* set of
inputs to the Marine's animation:

| idx | name | class | default | range |
| --- | --- | --- | --- | --- |
| 0 | `aim_target` | Vector3Gizmo | (0, 5, 1.6) | ±100000 |
| 1 | **`velocity`** | **Vector2** | **(0, 0)** | −10 .. 10 |
| 2 | `turn_speed` | FloatSpinner | 0.0 | −10 .. 10 |
| 3 | **`time_since_movement_input`** | **FloatSpinner** | **1.0** | 0 .. 100 |
| 4 | `is_crouching` | Bool | false | |
| 5 | `is_aiming` | Bool | false | |
| 6 | `is_sprinting` | Bool | false | |
| 7 | `is_jumping` | Bool | false | |
| 8 | `is_falling` | Bool | false | |
| 9 | `is_landing` | Bool | false | |
| 10 | `is_dead` | Bool | false | |
| 11 | `using_reload` | Bool | false | |
| 12 | `using_primary1` | Bool | false | |
| 13 | `using_primary2` | Bool | false | |
| 14 | `using_ability1` | Bool | false | |
| 15 | `using_ability2` | Bool | false | |
| 16 | `using_ability3` | Bool | false | |
| 17 | `using_aim_ik` | Bool | false | |
| 18 | `created_projectile` | FloatSpinner | 0.0 | 0 .. 3 |
| 19 | `dummy` | FloatSlider | 1.0 | 0 .. 1 |
| 20 | `turn_angle` | FloatSpinner | 0.0 | −3.15 .. 3.15 |
| 21 | `is_interacting` | Bool | false | |
| 22 | `using_medpack` | Bool | false | |
| 23 | `using_quick_melee` | Bool | false | |
| 24 | `lod` | FloatSpinner | 0.0 | 0 .. 6 |
| 25 | `stance` | FloatSpinner | 0.0 | 0 .. 2 |
| 26 | `hit_type` | FloatSpinner | 0.0 | 0 .. 1 | *(group `hit_reaction`)* |
| 27 | `hit_yaw` | FloatSlider | 0.0 | −180 .. 180 | *(group `hit_reaction`)* |
| 28 | `is_stunned` | Bool | false | *(group `hit_reaction`)* |
| 29 | `is_complete` | Bool | false | *(group `graph_controlled`)* |
| 30 | `hand_ik_offset` | Vector3Gizmo | (−0.058, 0.274, −0.02) | *(group `graph_controlled`)* |
| 31 | `hand_ik_enabled` | Bool | true | *(group `graph_controlled`)* |
| 32 | `face_pose` | FloatSpinner | 0.0 | −1 .. 9 | *(group `graph_controlled`)* |
| 33 | `emote` | FloatSpinner | 0.0 | 0 .. ∞ |
| 34 | `vertical_speed` | FloatSpinner | 0.0 | −100 .. 100 |

The two relevant controls are **`velocity`** and
**`time_since_movement_input`**. The graph consumes these parameters; native
client systems supply them. Absence of a transform-observer node in the asset
does not establish absence of a C++ producer. The producer is traced below.

## Why a static transform produces a run cycle

Two independent facts stack. Both are **PROVEN** from the asset.

### 1. The idle-vs-locomotion choice is made by `time_since_movement_input` alone

The `fullbody` state machine (`Root/base/fullbody`) has states `idle`,
`idle_turn`, `airborne`, `breakers_sm`, `move_sm`, `fullbody_abilities`,
`interact_sm`, `emote_ref`, `stun_ref`, `drop_pod_exit_m`. Its `entryStateId`
resolves to `idle`. The two wildcard transitions that decide locomotion are:

| transition | priority | blend | condition |
| --- | --- | --- | --- |
| `* -> move_sm` | 0 | 0.30 s | `AnimGraphParameterCondition` on `time_since_movement_input`, function **5**, testValue 0.0 |
| `* -> idle` | 0 | 0.15 s | `AnimGraphParameterCondition` on `time_since_movement_input`, function **0**, testValue 0.1 |

The EMotionFX `AnimGraphParameterCondition::EFunction` enum is
`0 GREATER, 1 GREATEREQUAL, 2 LESS, 3 LESSEQUAL, 4 NOTEQUAL, 5 EQUAL,
6 INRANGE, 7 NOTINRANGE`. That mapping is **PROVEN from this same asset by
internal consistency**, not assumed: the `* -> idle_turn` transition tests
`turn_speed` with function **4** against 0.0 (start a turn when turn speed is
*not* zero), and the `* -> idle` transition tests `turn_speed` with function
**6**, testValue −0.1, rangeValue 0.1, timeRequirement 0.2 (stop turning when
turn speed has been *in the range* −0.1..0.1 for 0.2 s). Only
4 = NOTEQUAL and 6 = INRANGE make those two authored transitions sensible, and
every boolean condition in the graph uses function 0 for "true" (`> 0`) and
function 5 for "false" (`== 0`), which is consistent with the same table.

So, in plain terms:

```
time_since_movement_input == 0.0   ->  move_sm   (locomotion)
time_since_movement_input >  0.1   ->  idle
```

Six further transitions in the same state machine gate on the identical
parameter — `fullbody_abilities -> move_sm`, `fullbody_abilities -> idle`,
`airborne -> move_sm`, `stun_ref -> move_sm`, `interact_sm -> move_sm`, and the
(disabled) `idle -> breakers_sm` — always with the same "== 0 means moving"
sense. It is the single authored "is this character moving" switch for the whole
fullbody layer.

`time_since_movement_input` is **registered as a replicated property of
`AnimationParameterComponent`** — it is 16th in the registration order that
`docs/22` recovered at `7e2bd0..7e3599`. The baseline shard never sent it; the
new opt-in implementation below remains disabled in the running service.

### 2. The locomotion blend space is indexed by `velocity` in **m/s**, and an in-place run clip sits at the origin

Inside `move_sm/move_bt`, the connection chain is **PROVEN** by node ids:

```
BlendTreeParameterNode "Parameters00"   (output port 1 = "velocity")
  -> BlendTreeVector2DecomposeNode "Vector2Decompose00"
      -> BlendTreeSmoothingNode "Smoothing01"  (interpolationSpeed 0.8)  -> BlendSpace2D input port 0  (X)
      -> BlendTreeSmoothingNode "Smoothing51"                            -> BlendSpace2D input port 1  (Y)
```

`Parameters00` declares all 35 parameter names in the table above, in that
order, so its output port 1 is `velocity` — the Vector2 whose authored default
is **(0, 0)**.

The blend space `active_run_hips_f_blend0` (`BlendSpace2DNode`,
`calculationMethodX = 0`, `calculationMethodY = 0`, i.e. *automatic*) carries
these authored motion coordinates:

| motionId | coordinates | typeFlags |
| --- | --- | --- |
| `active_run_r_anim` | (0.0000, 0.0000) | 4 |
| `active_run_fr_anim` | (3.8891, 3.8891) | 13 |
| `active_run_l_anim` | (0.0000, 0.0000) | 4 |
| `active_run_bl_anim` | (0.0000, 0.0000) | 4 |
| `active_run_f_anim` | (0.0000, **5.5000**) | 12 |
| `active_run_b_anim` | (0.0000, 0.0000) | 4 |
| `active_run_fl_anim` | (0.0000, 0.0000) | 4 |
| `active_run_br_anim` | (2.4749, −2.4749) | 13 |
| **`active_run_ip_f_anim`** | **(0.0000, 0.0000)** | 4 |

Read the numbers. `active_run_f_anim` sits at Y = **5.5**, which is exactly the
Marine's authored forward movement speed from
`assets/player/marine/marine_default.movementconfig` (`docs/21`, offset 0x54:
5.5 / 4.5 / 3.5). `active_run_fr_anim` sits at (3.8891, 3.8891), and
5.5 / √2 = 3.8891. `active_run_br_anim` sits at (2.4749, −2.4749), and
3.5 / √2 = 2.4749 — the *backward* speed, on the negative Y half.

**The blend space axes are the character's planar velocity in metres per
second.** That is a proven correspondence between two independently authored
assets, to four decimal places, and it is what makes the `velocity` parameter's
units unambiguous.

The clip named `active_run_ip_f_anim` — *run **in place** forward* — is listed
at coordinate **(0, 0)**. Several entries share that coordinate. This supplies
a candidate explanation for the observed run cycle; exact runtime clip
selection and weights have not been captured.

### The two facts together

The native initializer now confirms the missing movement-state value starts
at **0.0**, independently of the asset's default **1.0**. That keeps the
graph's moving transition eligible. Native client code independently computes
velocity from the Location interpolation path, so a stationary bot can have
zero blend velocity while its graph remains in locomotion.

**INFERRED:** this explains why sending Location alone did not establish idle
behavior. It does not prove the exact clip sampled in the previous live run,
nor prove that the new timer updates repair the visible behavior; those need
a fresh visual observation with the probe enabled.

## What this means for the evaluators in `docs/21`

**PROVEN, and it corrects `docs/21` directly.** `docs/21` wrote:

> "Evaluator" is the operative word: these derive the blend parameters.

They do not. `BlendSpaceMoveSpeedParamEvaluator` (rva 0x6d7b440) and
`BlendSpaceTravelDirectionParamEvaluator` (rva 0x6d7b4e0) are referenced from
0x4edcb71 and 0x4edd011, and both of those addresses lie inside a
UUID-parsing/type-registration function — the unwind fragment
[0x4edca00, 0x4edcb5f] is a `{XXXXXXXX-…}` hex-digit parser writing a 16-byte
value, i.e. `AzTypeInfo<T>::Uuid()`. The `lea` is the class *name* for
reflection, nothing more.

What those classes actually do is visible in the asset: the blend space's
`evaluatorTypeX` / `evaluatorTypeY` fields name them by UUID, and
`calculationMethod{X,Y} = 0` (automatic) means EMotionFX runs the evaluator
**once per motion at load time** to place each clip's coordinate in the grid.
That is why the coordinates are baked into the asset as literal numbers
(5.5, 3.8891, 2.4749 above): they are per-clip properties derived from each
clip's own root motion, not per-frame observations of the character.

The runtime blend-space *position* comes from the node's two input ports, and
those ports are wired to the `velocity` parameter, as traced above.

So the evaluators are irrelevant to the bug. They were a red herring, and the
absence-of-evidence reasoning in `docs/21` reached the wrong conclusion because
it never opened the animation graph.

## Open traces

1. **Visible idle/move transition acceptance**, including whether the bot-speed
   proxy matches native movement-input intent when a path is blocked.
2. **AnimationSyncInfo payload semantics**, beyond the proven container layout.
3. **Which of `move_bt`'s four BlendSpace2D nodes is actually output**, exact clip
   weights, and the other characters' graph behavior.

*(sections below added incrementally — see git history of this file)*

## Live animation schema captured, 22 September 2026

Read-only captures at **17:46:27 UTC** and **17:47:32 UTC** came from the same
client PID `1562288`, executable SHA-256
`e28643747df4a4eccf6a0f5490a3a1c4ab0a6a75b2cee62d3c62f2b191d18203`.
The local connection selects character class **12**, controls `0x01000002`,
and that entity has Saboteur/Ajonah slice hash **2391706089**. Hunters
`0x01000003`–`0x01000005` have Marine slice hash **9256072**.
All four are active (`active_state=4`, an entity lifecycle state, not a
network-role value). The server clock advanced by **64,875 ms** between
captures.

Evidence:

- `snapshots/systems-20260922/live-animation-20260922T174627Z.json`
- `snapshots/systems-20260922/live-animation-20260922T174732Z.json`
- `snapshots/systems-20260922/animation-capture-manifest.json`

Each of the four characters has the same component-13 graph and property
descriptor list, unchanged between captures. There are **34 stored nodes**,
including the sentinel; the component's expanded shared count remains 33.
Its **26 property record indices** are:

```
1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
17, 18, 19, 20, 21, 28, 29, 30, 31, 32
```

Record **21** owns six child records **22–27**. Other property roots have no
children. These record indices must not be confused with storage row indices:
the graph has a sentinel and component root before the property nodes.

This closes the missing live-graph capture gate. No animation record was sent,
no live memory was changed, and the services were not restarted for these
captures. Visible idle/run behavior remains a separate acceptance gate.

## Shared serializer map and offline encoder

The captured vtable `0x66caf20`, slot `+0x80`, reaches `0x7e35a0`, which calls
the generated shared serializer `0x7e3a10`. Its descriptor-pointer accesses
establish the order below independently of the animation-parameter registration
block. `AnimationSyncInfo` is the extra root absent from that registration list.

| Record | Property | Network value |
|---|---|---|
| 1, 2 | `melee_swipe`, `emote` | float32 each |
| 3–8 | `is_crouching`, `is_dead`, `using_medpack`, `using_quick_melee`, `using_primary2`, `using_primary1` | bool each |
| 9 | `stance` | float32 |
| 10–14 | `using_ability3`, `using_ability2`, `using_ability1`, `is_active`, `is_interacting` | bool each |
| 15 | `ability_target1` | three float32 values |
| 16 | `time_since_movement_input` | float32 |
| 17 | `is_aiming` | bool |
| 18, 19 | `ammo_ratio`, `spawn_ratio` | float32 each |
| 20 | `using_aim_ik` | bool |
| 21 | `AnimationSyncInfo` | container: Size record 22, five uint32 elements at 23–27 |
| 28, 29 | `turn_angle`, `resource1_ratio` | float32 each |
| 30, 31 | `is_falling`, `using_reload` | bool each |
| 32 | `AimDirection` | packed quaternion uint32 |

Floats are raw IEEE754 binary32, big endian: wrapper `0x7f32c0` selects slot
`+0x28`; input reader `0x4f56730` calls `ntohl` at `0x4f56770`. Boolean wrapper
`0x786620` selects slot `+0x78`. Reader `0x4f565d0` retains an LSB-first bit
cursor across intervening scalar fields: subsequent bools reuse the same byte
until eight bits have been consumed. They are not one byte per field.

`server/animation_events.py` implements the **23 scalar roots** with explicit
values and strict validation. It skips the entire unselected SyncInfo subtree.
It excludes Vec3, packed quaternion, and SyncInfo payloads; SyncInfo semantics
remain unresolved. There is **no shared `velocity` property** in this channel.
The helper assigns no gameplay defaults or firing/death timing policy. The
shard's opt-in movement policy below calls it for record 16 only.

`tools/inspect_animation_wire.py` checks the executable hash, actual name LEAs,
helper calls, descriptor slots and captured tree before producing
`snapshots/systems-20260922/animation-wire-map.json`. Thirteen encoder tests
cover both captures on all four characters, literal bytes, mixed scalar/bool
packing, the ninth bool, and rejected unknown/invalid inputs.

## Native movement producers and opt-in timer policy

The component constructor clears `xmm1` at `0x7dfa45`, addresses member
`+0x1330` at `0x7dfa48`, and calls float-property constructor `0x7ec0b0` at
`0x7dfa4f`. Registration at `0x7e31cb`–`0x7e31f4` independently names that
member `time_since_movement_input`: **the native value starts at zero**.

`PlayerMovementSystemGameplayCommon` tests four native input floats through
`0x31c9fe0`. With input present it resets its quiet timer; without input it
accumulates frame time. At `0x3292744`–`0x329277b` it writes **0.0 or 1.0**
depending on whether the quiet interval has reached the configured delay.
Initializer `0x3941b0` gives CVar `0x7bbecd0` the float **0.1 seconds** from
`0x6e00ac8`. Despite the parameter's name, the emitted value is a thresholded
flag, not elapsed seconds.

The remote velocity path is separate:

1. `PositionInterpolationSystem::CalculateBlendedVelocity` computes
   `(new interpolated position - old position) / dt` at
   `0xdcd78f`–`0xdcd7ad` and stores Location client `+0x1460` at `0xdcd8bb`.
2. `AnimationParameterSyncSystemClient::UpdateEntity` (`0xd84940`–`0xd85294`)
   uses the blended transform at Location `+0x1440` when flag `+0x1430` is set,
   otherwise the Location getter `0x4b04700`.
3. It rotates the `+0x1460` velocity into character-local coordinates at
   `0xd84dde`–`0xd84eec`, then writes the animation Vec2 wrapper `+0x8a0`
   through `0xd852a0` at `0xd84f28`.

`CRUCIBLE_SOLO_MOVEMENT_ANIMATION=1` enables an **experimental server policy**:
publish record 16 as zero while a bot's reported speed is positive, then one
after 100 ms without movement. The simulation's pre-separation speed is an
explicit approximation for native input intent; separation corrections alone
do not reset the timer. Millisecond session time makes the threshold stable.
Dead/absent bot state is discarded, and revival starts a fresh timer. Values
repeat each tick so a dropped update does not permanently lose the state.

The annotated native source evidence is saved in
`snapshots/systems-20260922/movement-animation-source.json`.

This flag defaults off and was not enabled in the running shard. Seven offline
integration tests verify the threshold, reset, per-bot isolation, lifecycle,
combined fire/health RelativeId chaining, and unchanged output when disabled. No firing/death
animation policy is inferred from this movement-only integration.

## Combat-state wiring and opt-in policy, 22 September 2026

**PROVEN**, from `marine.animgraph` (sha256 above) with the transition dumps
of `Root`, `primary_fire_sm0`, `ads_scope_sm`, `reload_sm` and `breakers_sm`:

| Parameter | Record | Authored consumer |
|---|---|---|
| `is_dead` | 4 | `Root`: `base -> death` (0.3 s blend) when true; `death -> base` and `dead_bt -> base` with **zero** blend when false; entry selects `dead_bt` when already dead. |
| `using_primary1` | 8 | `primary_fire_sm0`: `nothing -> Motion90` when true; `Motion90 <-> Motion900` while it stays true, each gated by a 0.1 s time condition; `* -> nothing` after 0.3 s false. Both motion nodes play non-looping `recoil_active_single_anim`. Also feeds `shoot_twist_m` and `breakers_sm` exit. |
| `is_aiming` | 17 | `ads_scope_sm` `sight_out_m <-> sight_in_m`; selects `reload_sm`'s `not_reloading_m0` vs `not_reloading_m`; exits `breakers_sm`. |

Holding `using_primary1` therefore re-triggers recoil every 0.1 s: a held
trigger. The Marine bots fire one simulated shot per 2 s `fire_interval`.

**Native writers: NOT FOUND.** Component members are `is_dead +0x1b20`,
`using_primary1 +0x1dc0`, `is_aiming +0x1880` (registration LEAs at `0x7e2d6b`,
`0x7e2ee4`, `0x7e3218`). A `.text` scan for those displacements found only the
component's own constructor/registration/serializer and accessors near
`0x7dc840`–`0x7e45fa`. The remaining matches are in functions identified by their
strings as unrelated (UI settings, particle descriptors, healing/spawn action
layouts) or GUID-only reflection tables. This is a bounded search, not proof of
absence. Unlike `velocity`, these are replicated records, and the creature
script graphs set `using_primary1` server-side (`docs/22`). **INFERRED:** the
server authors them for remote characters.

`CRUCIBLE_SOLO_COMBAT_ANIMATION=1` (default off) adds these three bools to each
Marine bot's animation update, merged with record 16 when the movement flag is
also on (one update per bot per tick):

- `is_dead`: true for every registered hunter the combat arena reports dead,
  including one still present in the AI steps. It is repeated each tick and
  cleared on respawn. Without combat, no bot is dead.
- `using_primary1`: follows `step.firing`, so it stays true throughout the
  authored 2.0 s attack and clears between attacks or when dead. The graph
  re-triggers recoil every 0.1 s while held. This matches the authored
  `marine_primary_onfire.cdl` trigger lifetime; the one-activation-per-packet
  fire queue can publish projectiles on later ticks.
- `using_aim_ik`: follows the same held-fire state. `is_aiming` remains false:
  aiming down sights is a separate ability and no native bot producer is known.

The player's own character is untouched. The policy keeps its settings opt-in
in source; the managed local Alpha Hunters service enables it. Seven tests in
`server/test_hunter_combat_animation.py` cover the disabled path, the single
pulse, per-state aiming, dead repetition and respawn clearing, dead-but-stepped
bots, no-combat operation, and the merged literal bool/float packing. Visible
death, recoil and sight-in behavior are **NOT_OBSERVED**.
