# hit-reactions track: running notes (saved as I go)

## 0. Starting context (from docs, 2026-09-24)
- Component 80 HitReactionComponent: shared graph root 0, atomic records 1 (NetworkedAdditiveHitIndex, uint8, 0x79fcd0) and 2 (NetworkedAdditiveHitYaw, float32, 0x7f32c0). Serializer 0x943c00 -> 0x943fa0 (docs/27 s9).
- No inbound RPC on 80 (stub 0x943f40, docs/26).
- marine.animgraph params (docs/28): 26 hit_type FloatSpinner 0..1, 27 hit_yaw FloatSlider -180..180, 28 is_stunned, group `hit_reaction`.

## 1. Client consumer: Crucible::HitReactionClientSystem (PROVEN, static)
Scratch helper: $TMPDIR/hr/rx.py (capstone + numpy rel32 xref scan; read-only).
- SystemEntry 0xd37530 (profiler name LEA 0xd375c8 -> str 0x6776cb0). Per entity it fetches four
  components via getters 0xd714e0 (arg6), 0xc41730 (arg5), 0xd70680 (r9), 0xd71420 (r8 = HitReaction
  client comp), then calls worker 0xdae770(xmm0 = dt).
- Worker 0xdae770 (fragments 0xdae770..0xdaea0d), comp = r8:
  * [comp+0x100] float timer -= dt every frame.
  * cl = byte [comp+0x150], al = byte [comp+0xe0], r14b = byte [comp+0xe1].
  * if cl > al (UNSIGNED, jbe at 0xdae7c7): [comp+0xe1] = cl; call play 0xdae6f0(anim=r9, comp,
    idx=old e1, yaw=float [comp+0x154]).
  * elif e1 != e0: local branch: yaw computed from Location (0x4b04700 on arg5) and vec [comp+0xf0]:
    acos (0x615f204) * +/-57.2957 (sign from cross product); default 180.0 (0x6e018e0). Then play().
  * else: if timer < 0.064 (0x6e00a60): write 0.0 to [anim+0x600].
  * then: elapsed = (t_now - t[idx7]) * 0.001 (0x827800(arg6,7), 0x4b64100 game time) ;
    bool [anim+0x750] = elapsed > float [comp+0x158] (0x7e8020).
- play 0xdae6f0: [comp+0xe0] = idx; if timer [comp+0x100] <= 0: timer = [[anim+0x4000]+0xa0] (0 if null),
  write yaw -> [anim+0x4b0], write 1.0 -> [anim+0x600] (setter 0x7eb1e0). If timer > 0: NO replay
  (cooldown = remaining timer), but e0 still advances.

## 2. Field identities (PROVEN)
- Serializer 0x943fa0: record 1 NetworkedAdditiveHitIndex -> r9 = [rec+0x150] (0x944123, call 0x79fcd0 uint8);
  record 2 NetworkedAdditiveHitYaw -> [rec+0x154] (0x9441b2, call 0x7f32c0 float). Component byte 0x50 = 80.
  These are exactly the bytes the worker reads (+0x150 cl, +0x154 yaw).
- Anim-parameter object ctor 0x34714e0 builds float vars with ctor 0x7ec0b0:
  +0x4b0 "AnimationParameterComponent.hit_yaw" (0x3471b10), +0x600 "...hit_type" (0x3471c55),
  +0x750 "...is_stunned" (0x3471c7d, bool). These are client-local graph variables, NOT component-13
  replicated records (no bare "hit_yaw" name string in the image).
- [anim+0x4000] = animconfig data. Reflection 0x56fd627: aimDistanceMax at +0x74 (used 0xd84c8d),
  flinchDelay at +0xa0 (r8d=0xa0 at 0x56fd845). So play() cooldown = animconfig.flinchDelay.
- assets/player/marine/marine.animconfig (misc.pak, 251 B, sha256 af4ee8af69458db1eaacbb59fce332ec1e28f1b36615b10d0edabd4b604fc878):
  aimDistanceMin 15, aimDistanceMax 15, rootBoneName marine:c_hips_BND, flinchDelay 0.2.

## 3. Semantics (PROVEN unless marked)
- Index = monotonically increasing uint8 hit COUNTER, not a list index. New reaction iff networked > last
  played (unsigned). Wrap 255->0 does NOT trigger (0 > 255 false) -> server must not wrap blindly (see policy).
- Yaw = degrees (local branch: acos * 57.2957, sign by cross product, default 180). Written straight into
  graph var hit_yaw (graph range -180..180). Frame: character-relative (local branch uses the entity's
  Location rotation vs stored direction [comp+0xf0]); exact axis/sign convention: see animgraph section.
- hit_type = 1.0 on play, back to 0.0 once timer < 0.064 s with no new index (so ~0.136 s pulse for Marine).
- Retrigger: a new index while timer > 0 advances played index but does not replay (swallowed).
  Effective max rate: one flinch per flinchDelay (0.2 s Marine).

## 4. Local (client-predicted) path (PROVEN writer, gating NOT fully traced)
- 0xdd6070 `inc byte [comp+0xe1]` inside the Hybrid projectile client hit processing (same function family
  as CreateHitMaterialVfx 0xdd5039 / PlayProjectileImpactSound 0xdd5395 / "ProjectileImpact"); target comp via
  getter 0xc82a50 (type global 0x7c9b0b8). Then stores a direction vec at [comp+0xf0] (0xdd6100) computed
  from the shooter rotation (rbx+0x260) rotated reference axis. Second copy 0xdd65fe.
- So the client can PREDICT a flinch on the victim of its own Hybrid projectile; the worker's local branch
  (e1 != e0) turns that into hit_yaw from [comp+0xf0] vs victim facing.
- Consequence for server policy: networked counter must count the same hits; a networked value <= local
  e0 does not double-play. Server must NOT reset the counter on (our fake) respawn: the client component
  persists (no despawn), so e0 persists; a reset would silence all later reactions.
- No HitReactionComponent::Server symbols in the client image: retail server increment policy NOT_OBSERVED.

## 5. The reaction graph (PROVEN, asset)
- marine.animgraph `base` contains AnimGraphReferenceNode Reference0 -> animations/characters/additive_hit_react.animgraph
  (animations.pak, sha256 74c506b73863b8f6e20c218c38534706fc739cc25b820297358b944f9e9906b0), masked param hit_yaw;
  Parameters15 in base feeds hit_type.
- additive_hit_react params: hit_type FloatSpinner 0..2 default 0; hit_yaw FloatSlider -180..180 default 0.
- Root SM (entry b): wildcard -> f | l | r | b (blend 0) when hit_type > 0 AND hit_yaw in:
  f [-45,45], r [45,135], l [-135,-45], b [135,180] or [-180,-135]; wildcard -> Hub0 when hit_type == 0.
  (EMotionFX INRANGE accepts reversed bounds.) So the hit_type 1->0 pulse re-arms the SM for the next hit.
- Clips: hit_flinch_{f,b,l,r}_anim per character (marine: animations/characters/player/marine/animations/).
- Dump script: $TMPDIR/hr/transitions.py (copied into snapshot folder as transitions.py).

## 6. Yaw frame (PROVEN math; one INFERRED identity)
- 0x7719240 = AxisY (0,1,0): static init 0x149930 copies .rdata 0x6e02510 = (0,1,0,0).
  0x7726d00 = AxisZ: static init 0x203680 copies 0x6e02930 = (0,0,1,0). (Both are .bss at load.)
- Worker local branch 0xdae7f6..0xdae947: f = victim rotation (Location 0x4b04700 +0x10 quat) applied to +Y;
  c = f x Z (shufps 0xc9/0xd2 cross at 0xdae86d..0xdae8ae) = victim RIGHT; d = [comp+0xf0];
  yaw = acosf(dot(d,f)) * (dot(d,c) >= 0 ? +57.2957764 : -57.2957764)  [0x615f204 -> IAT acosf];
  degenerate c -> 180.0.
- Projectile path 0xdd6076..0xdd6100 stores d = rotation(rbx+0x260) applied to +Y (INFERRED: projectile
  orientation = travel direction of the hitting shot).
- => yaw = signed angle of the SHOT TRAVEL DIRECTION in the victim frame, degrees, 0 = travelling the same
  way the victim faces (shot from behind), +90 = travelling toward victim's right, +-180 = shot from front.
  Graph: 0 -> hit_flinch_f, +90 -> hit_flinch_r, -90 -> hit_flinch_l, 180 -> hit_flinch_b (i.e. clip = push dir).
- Server-side networked yaw: use the same formula with d = normalize(victim_pos - shooter_pos) and the
  victim facing that we replicate (Location quaternion from pack_upright_rotation).
