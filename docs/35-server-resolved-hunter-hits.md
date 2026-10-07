# Server-resolved hunter hits (design, option a)

Status: **design only**. Nothing is implemented or tested. The owner chooses
between this option (a), fidelity-first (b) and harmless bots (c). This file
records what (a) would do and where every number comes from, so the choice
can be made on facts.

## Why it is needed

The 5 October live run settled one thing: the retail client never reports
hits from a hunter's projectiles (docs/30, "Alienware live run, 5 October").
The hunters made 10,146 fire decisions. The client sent zero
`ClientProjectileResult`s for them, and the shard refused none. Client→server
RPCs come from components the client owns, and it does not own a hunter. So
`CRUCIBLE_SOLO_BOT_HIT_REPORTS` (docs/33) can never fire for the Marines.
Retail ran bot projectiles on the server, and so must we.

## Authored inputs (build 5685572)

Every value here was read from installed assets with
`tools/inspect_combat_projectile.py` or is already cited in the source.

| Quantity | Value | Source |
|---|---|---|
| Spread cone | `MinMaxDegrees` 1.5° → 3.5° | `marine_primary.accuracy` |
| Bloom per shot | curve 0.05 (bloom 0) → 0.139 (0.48) → 0.218 (1.0), Bezier | `marine_primary.accuracy` `BloomRatioPerShot` |
| Bloom recovery | `CooldownDuration` curve, first key 0.3 s at bloom 0 (remaining keys not yet read) | `marine_primary.accuracy` |
| Accuracy weight | 0.2 (meaning not yet traced) | `marine_primary.accuracy` |
| Round interval | 96 ms while the trigger is held for `Attacks[0].Duration` 2.0 s | `hunter_ai.MARINE`, accuracy audit item 1 |
| Shot origin / aim point | bot position + (0,0,1) m → target position + 1.0 m | `AggroOriginOffsetLocal`, `AggroTargetOffsetVertical` (`hunter_ai.py`) |
| Projectile speed | 625 u/s, lifetime 0.4 s, so 250 u maximum reach | `marine_projectile_primary.projectile` |
| Damage | 20 at ≤ 40 u, linear to 10 at 60 u, then 10 | `combat.MARINE_PRIMARY`, `falloff_ratio` |
| Critical | ×2.0, `preciseCriticalHits` | same asset |
| Attack range | 0–30 m, vertical ≤ 20 m | `Attacks[0]` in `marine_guide_dummy.ai` |

## Proposed rule

For each `Shot` that `hunter_ai` already emits:

1. **Spread angle.** `θ_max = lerp(1.5°, 3.5°, bloom)`. Each shot adds
   `BloomRatioPerShot(bloom)` to the hunter's bloom, capped at 1. While the
   trigger is released, bloom decays at the `CooldownDuration` rate.
2. **Deviation.** Draw an angle uniformly over the cone's disk from a
   per-match seeded RNG, so the result is reproducible from the log.
3. **Hit test.** The ray hits if the deviation does not exceed
   `atan(r / d)`, where `d` is the distance from origin to aim point and `r`
   is the target capsule radius. Its source is still open (see below).
4. **Flight.** At `t_shot + d / 625` apply `combat.apply_damage` with
   `immediate_damage(MARINE_PRIMARY, distance=d)`, but only if the target is
   still alive and `d ≤ 250`. Log each applied hit as
   `SOLO COMBAT hunter … hit`, the line the extractor already reads for A4.
5. **Gates.** Off by default, behind `CRUCIBLE_SOLO_BOT_DAMAGE=server`. It
   is mutually exclusive with client bot-hit reports, so a hit is never
   counted twice.

## Lethality knob

Three Marines each fire about 10 rounds/s at 20 damage. At 15 m with early
bloom, the cone (≈1.5°, ≈0.39 m) is about the size of the body, so most
early rounds would hit. The player would lose 800 health in about 2 s. Retail
bots surely missed more than that: their AI aim error, reaction time and
target-switch delays are not yet modelled. So that the match is playable
before those are decoded, `CRUCIBLE_SOLO_BOT_ACCURACY` (default 1.0) scales
the hit probability. It is a declared, logged tuning value, not an authored
one, and it should be removed once AI aim error is traced.

## Known gaps

- **No line of sight.** Hunters already engage through walls (accuracy audit
  item 6), so hits would pass through cover too. Retail used PhysX raycasts
  in an `AwarenessLoS` group (`snapshots/accuracy-20260924/line-of-sight-geometry.md`).
- **Approximate spread.** The client's own deterministic wedge, sway and
  kick pattern (`WedgeStep`, `CurrentWedgeNum`, `SwayRatio`, `KickRatio`,
  docs/29) is not decoded, so the uniform cone stands in for it.
- **Capsule radius unread.** It must come from the hero's character slice
  (Ajonah for the managed profile). The fallback is a declared constant.
- **No critical hits.** Head-hit classification needs the hit-bone lookup;
  every hit is treated as non-critical.
- **No hit feedback.** Component 80 hit reactions and the incoming-hit
  indicator (component 81) remain unwired (accuracy audit item 9).

## Acceptance

With the flag on and the knob at its default, a live run should show the
following, read by `tools/match_acceptance.py`:
- A4 PASS: accepted hunter hits on `01000002`, the HUD dropping below full,
  and the player able to die.
- A6 LOGS-OK with winner team 2 when the player loses.
- The owner's judgement on whether the knob value feels fair. That decides
  the default before the flag is enabled in the managed profile.
