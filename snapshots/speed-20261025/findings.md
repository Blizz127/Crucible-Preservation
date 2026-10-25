# How SpoofedPlayerInputs turns speed into inputs

Static trace of `bin/Crucible.exe` (client build 5685572) and `misc.pak`,
25 October 2026, by a helper agent; reviewed and kept here because docs/21
and docs/36 rest on it. Nothing was run, written to the Steam install, or
changed in the repo. `rev.py` beside this file is the numpy rel32 xref scan
it used (in the style of `snapshots/hit-reactions-20260923/rx_scratch.py`).

## Short answer

The conversion from an AI's `SpeedWander` / `SpeedChase` to spoofed inputs is
**not in the client executable**. It belongs to the server's AI, and no
server component is linked into `Crucible.exe`. The client does contain the
consumer of a spoofed move (the player movement pipeline) and the sprint
ability. Those bound the answer:
- **Forward speed.** Assuming the movement code works the way its input
  accessors suggest, a spoofed Marine moves at the active movement config's
  speed: 5.5 m/s forward on `Default`, or 8.5 on `Sprint`.
- **No sustained 8.0.** 8.0 is not a value the pipeline can hold.

| question | answer | status |
| --- | --- | --- |
| 1. Is desired speed turned into an input magnitude (desired / max, clamped)? | Not readable. The consumer never reads magnitude, so a ratio would change nothing. | consumer READ, conclusion INFERRED |
| 2. Is sprint set when desired speed exceeds the walk cap, or on "chase"? | Not readable. Sprint is a held ability (Ability3), not an axis. The AI asset has no sprint field, and the retail Marine scripts press Ability3 themselves. | READ + INFERRED |
| 3. Patrol and chase speed | Patrol about 5.5 m/s. Chase about 5.5 m/s, or about 8.5 while Ability3 is held. | INFERRED; moderate for patrol, lower for chase |

## READ from the client executable

Commands run from the repo root as `.venv/bin/python -I tools/xref.py ...`
(each takes 30 to 100 s), and `rev.py` for rel32 xrefs.

1. **The mode is only a stored enum.**
   - `AIControlMode` has two names, `Normal` and `SpoofedPlayerInputs`. They
     sit in a qword table at RVA `0x65cdcc0`/`0x65cdcc8`; the string is at
     `0x6c34d70`.
   - `ControlMode` is 2 bytes at `AIDataAsset+0x40` (`0x570beca`, `0x570bede`;
     `--range 0x570be50:0x570bef0`).
   - Of 19 `cmp word [reg+0x40], imm` sites, the four that compare with 1 are
     projectile code (`0x321a705`, `0x3220c38`, `0x3229297` FireProjectile) and
     post-process (`0xe08ebe`). The other 15 compare with 0, 2 or 3 and were not
     inspected. Other load forms were not scanned.
2. **Field offsets.**
   - `MovementPararams` is at `AIDataAsset+0x150`, size `0x1d0` (`0x570da8e`,
     `0x570da96`; `--range 0x570da00:0x570daa0`).
   - Inside it: `SpeedWander` +0xc, `SpeedChase` +0x10, `FleeingSpeed` +0x14,
     `StartStopAcceleration` +0x18, `CruiseAcceleration` +0x1c,
     `MaxSpeedMultiplier` +0x20 (`--range 0x571ed00:0x571f100`). That makes
     asset offsets `0x15c..0x170`.
3. **No server component code in the exe.**
   - `grep -a -o -E 'AIComponent::[A-Za-z]+' Crucible.exe | sort | uniq -c`
     gives only `Client`, `ClientRecord`, `Common`, `CommonBase` and
     `CrucibleCommonBase`.
   - `grep -a -o -E '[A-Za-z]+Component::(Server|Authority)'` finds one hit,
     inside an error message (`ProjectileComponent::Authority::PerformRewindChecks`,
     `0x6d34120`).
   - `...SystemGameplayServer` appears once, also in a message (`0x679ba50`).
     None of the 119 ECS system names contains "Server".
4. **Nothing reads the AI asset's movement block.**
   - `AzTypeInfo<AIDataAsset>::Uuid` (`0x7842d0`) has 24 call sites
     (`rev.py x 0x7842d0`). All are RTTI, asset registration or reflection:
     `0x330b7xx`, `0x332bxxx`, the "AI Data" registration in `0x3314c60`, the
     reflection blocks `0x570a61f`, `0x5710000`, `0x57101b4`, `0x5711220`,
     `0x5711c9f`, and a `0x78xxxx` group.
   - All six `0x78xxxx` functions (`0x786080`, `0x786720`, `0x7868f0`,
     `0x786e70`, `0x787795`, `0x787f40`) are `Asset<AIDataAsset>` template
     members. No function loads an `AIDataAsset` field.
   - `AIClientSystem::SystemEntry` (`0xd28a10`) shows no `AIDataAsset` access.
     It reads `AIComponent::Client+0x1218`, whose origin was not traced.
   - There are no patrol, wander, chase, strafe or flee log or assert strings,
     only struct names (`StrafeParameters` `0x69df2d8`,
     `PatrollingStateParameters` `0x69df510`, ...).
5. **The consumer of a spoofed move.**
   `PlayerMovementComponent::Predictable::UpdateInputs` (`0x31c98b5`, string
   `0x69a9b90`; `--range 0x31c98b5:0x31c9d3e`) reads two events:
   - **`GameInputAction_Move`** (listener at component+0x10, via
     `0x31b8300`) stores a Vec2 at +0x150/+0x154. An assert near `0x31c9a0e`
     says "local movement event had magnitude > 1".
   - **`GameInputAction_MoveInWorldDirection`** (listener +0x60, via
     `0x31b8460`) carries `MovementDirectionWorld` and
     `FacingDirectionWorld`.
     - The move Vec2 goes to +0x158/+0x15c, with an assert near `0x31c9bca`
       ("world movement event had magnitude > 1").
     - The facing Vec2 goes to +0x160/+0x164, with an assert near
       `0x31c9cbe` ("turn event was not normalized or zero").
   - **The readers.** The four move floats have exactly three
     (`--range 0x31c9fe0:0x31ca230`):
     - `0x31c9fe0`: true if any of the four floats is non-zero;
     - `0x31ca040`: the movement direction, from the local vector (through
       two helpers that take the caller's orientation) or else the world
       vector. It normalises it (`rsqrtss` plus one Newton step at
       `0x31ca173`; zero length gives (0, 1)). **Magnitude is discarded
       here.**
     - `0x31ca200`: the facing vector.
   - **No other reader.** A displacement scan of +0x150..+0x15c over
     `0x31c0000..0x31d0000` and `0x3285000..0x3295000` finds none.
   - **Callers.** The accessors are called from `0x328c740` and
     `PlayerMovementSystemGameplayCommon::UpdateEntity` (`0x328fdf7`).
6. **Speed comes from the active movement config.**
   - `GetActiveConfigAsset` (`0x31ca230`) has one caller, `0x328fd09` in
     `UpdateEntity`.
   - The `MovementConfigDataAsset` Field() block
     (`--range 0x33ed100:0x33ed260`) gives the layout:
     - Movement Speeds at +0x50. Tooltip: "Speed to move in each direction:
       (F)orward, (S)trafe, (B)ackward".
     - Acceleration at +0x60. Tooltip: "Seconds to accelerate from Min Speed
       to Max Speed in each direction".
     - Overspeed Deceleration at +0xb4. Tooltip: "m/s to reduce your speed if
       you are moving too fast".
7. **Scripts can inject inputs, but not movement events.**
   - `PlayerInputSpoofComponent` holds `InputOverrideRequests` and is fed by
     the CDL action `StartInputOverride`. Overrides use `ClientInputAction`
     names: `Jump`, `Ability1..4`, `PrimaryFire`, `MoveAxisX/Y/Z`,
     `MoveForward/Backward/Left/Right`.
   - None of the 76 `MakeGameInputAction_*` CDL actions produces a Move or
     MoveInWorldDirection event. Whatever raises `MoveInWorldDirection` is
     native code that is not in this exe.

## READ from the retail assets

- **Sprint is an ability.** `cdl/player/marine/ability/marine_sprint.cdl`
  (sha256 `b11a066c...`):
  - Its enter trigger is `GameInputAction_Ability3`.
  - Node [39], "Sprint speed happens here", sets
    `PlayerMovementComponent.ActiveConfig = Sprint`.
  - It drains a `Sprint` resource at 8.0 per second, and cancels "if movement
    stops or non-sprinting ability begins". Its exit restores `Default`.
- **Movement configs** (misc.pak, forward / strafe / back, then the ramp):

  | config | speeds, m/s | Acceleration, s | Brake Friction | Overspeed Deceleration Rate |
  | --- | --- | --- | --- | --- |
  | `marine_default` | 5.5 / 4.5 / 3.5 | 0.15 each | 100 | 12 |
  | `marine_sprint` | 8.5 / 4.5 / 3.5 | 0.15 each | 100 | 12 |
  | `marine_sprint_charge` | 9.0 / 4.5 / 3.5 | 0.15 each | 100 | 12 |
- **The Marine guide presses Ability3 itself.**
  - `marine_guide_on_hit.cdl` (sha256 `4de60d8c...`): on damage it calls
    `RequestAIStateChange(Flee, 4.0 s)`, `StartInputOverride Ability3` for
    4.0 s, `MoveAxisY` for 4.0 s and `UseMedPack` for 6.0 s. The gating
    conditions were not decoded.
  - `marine_guide_attack_selector.cdl` (sha256 `8cc44216...`) has ten
    overrides, including `Ability3` 3.0 s and `MoveBackward` 3.0 s.
  - Both carry leftover debug prints.
- **The Marine dummy is a player entity.**
  `slices/player/marine/marine_character_dummy.dynamicslice` carries
  `PlayerMovement`, `PlayerInputSpoof`, `Input` and `AIComponent`, and no
  `NpcMovement`, `NpcBlocker` or `AIWander` component.
- **Authored AI speed against the player caps** (forward / lateral / back):

  | AI asset | wander = chase | strafe | back-up | player config |
  | --- | --- | --- | --- | --- |
  | hero_guide_dummy | 6.5 | 4.5 | 1.0 | hero_default 6.5 / 4.5 / 3.5 |
  | saboteur_guide | 6.5 | 4.5 | 3.5 | saboteur_default 6.5 / 4.5 / 3.5 |
  | marine_guide_dummy | 8.0 | 4.5 | 1.0 | default 5.5 / 4.5 / 3.5; sprint 8.5 |
  | inventor_guide_dummy | 5.5 | 4.5 | 1.0 | inventor_default 4.5 / 4.0 / 3.5 |
  | duelist_guide | 7.5 | 4.5 | 3.5 | pistol 5.5 / 4.5 / 3.5; sword 8.25 / 6.5 / 5.5 |

## INFERRED

- **Magnitude does not scale speed.** The move floats have no reader but
  "any non-zero" and "unit direction", and their magnitude is only asserted to
  be at most 1. So any non-zero input gives full config speed.
  - Gap: the instruction that multiplies direction by the speed triple was not
    found. This is the weakest link.
- **The AI producer is server-side**, by READ items 3 and 4 and the dummy's
  component set: an argument from absence, but a strong one.
- **Speeds.**
  - Patrol is about 5.5 m/s.
  - Chase is about 5.5 m/s, or about 8.5 while Ability3 is held and the
    Sprint resource lasts (its capacity is unresolved).
  - Firing probably cancels sprint.
  - A sustained 8.0 chase would need a config the Marine does not have.
  - Counter-hypothesis: the server AI presses Ability3 whenever desired speed
    exceeds the cap.
- **Acceleration.** `Acceleration` = 0.15 s from min to max speed, so a
  spoofed Marine reaches 5.5 m/s in about 0.15 s. The AI's
  `StartStopAcceleration` of 10 m/s² would take 0.55 s, and applies only if
  the server shapes the input's magnitude and the client honours it.
- **MaxSpeedMultiplier (1.1) and CruiseAcceleration (10)** read as
  navigation-mover parameters. 8.0 × 1.1 = 8.8 is above even the sprint
  config.

## Not established, and what would settle it

- **What is missing:** the server AI code that turns the mover's output into
  inputs. It is in no file we hold.
- **Static follow-up:** find where the speed triple is multiplied. Start from
  `0x33f1c80` and `0x33f2900`, callees of `UpdateEntity` that take
  config-derived floats from `config+0x2e8..0x2f8`.
- **Live check:** on the local character, hold a gamepad stick about halfway
  for 2 s and read the speed from the replicated input positions (docs/25).
  - Ajonah's default 6.5 m/s means magnitude is discarded.
  - About 3.25 m/s means it scales.
  - Keyboard input is all or nothing, so it cannot decide this.
