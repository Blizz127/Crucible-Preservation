"""Deterministic creature AI state machine built from the retail authored data.

Every constant in this module is read out of an installed asset: the creature
behaviour parameters come from `ai/<creature>.ai` and the attack timeline comes
from the creature's attack script `cdl/creatures/<creature>/<creature>_attack.cdl`.
`tools/inspect_creature_ai.py` and `tools/inspect_creature_cdl.py` reproduce
both, with the sha256 of each asset recorded next to the values below.

Nothing here reads a wall clock, touches the network or decides damage: `step`
is driven by an injected time and returns plain events. Damage, health, death
and respawn belong to `server/combat.py` (a different workstream); this module
only reports that a creature attacked an entity at a time, plus the authored
projectile that the retail script would have spawned.

What is authored and what is assumed is stated per field. Nothing in this file
has been observed in a running client: it is asset-faithful, not verified.
"""
from dataclasses import dataclass, field, replace
import math

# ---------------------------------------------------------------------------
# Authored parameter blocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttackProfile:
    """One entry of the .ai `Attacks` container (spitter.ai 0x12b7..0x1402)."""
    angle_degrees: float
    range_min: float
    range_max: float
    range_vertical_max: float
    duration: float
    rotation_disallowed_min: float
    rotation_disallowed_max: float
    abandon_if_target_out_of_max_range: bool = False
    time_without_los_until_abandon: float = 0.0


@dataclass(frozen=True)
class AttackScript:
    """The attack .cdl timeline, measured from the AIAttackEvent.

    For the spitter the graph (cdl/creatures/spitter/spitter_attack.cdl,
    sha256 fd82fba9...) runs, in connection order:
      AIAttackEvent -> Condition -> ExternalGet CrucibleHealthComponent.IsDead
      -> Condition "If the target is NOT dead..." -> EndEnterTriggerChecks
      -> HasBuff/RemoveBuff Cloak -> Set AnimationParameterComponent
      .using_primary1 = True -> Delay 1.0 -> VectorMath2 "Offset the projectile
      source pos" (+Z 2.75) -> ExternalGet target position -> Delay 0.1
      -> Set using_primary1 = False -> CreateProjectile
      (assets/creature/spitter/spitter_projectile.projectile, manual targeting
      on AIComponent.AimPosition) -> Delay 1.25 -> SphereShapeEntityQuery
      radius 10 for Player with line of sight -> ForEach/GetContainerSize
      -> Condition -> MakeAIAlertedEvent.
    """
    source: str
    sha256: str
    kind: str                      # 'projectile' (CreateProjectile) or 'melee'
    windup_seconds: float          # Set using_primary1 True -> Delay
    aim_to_fire_seconds: float     # second Delay, ends at the attack node
    attack_asset: str
    muzzle_offset_z: float = 0.0   # VectorMath2 offset applied to the origin
    target_offset_z: float = 0.0   # VectorMath2 offset applied to the target
    radius: float = 0.0            # area query radius for a melee attack
    alert_after_fire_seconds: float = None  # Delay before a nearby-player query
    alert_radius: float = 0.0
    alert_check_line_of_sight: bool = False

    @property
    def fire_seconds(self):
        """Seconds from attack start to the CreateProjectile/MeleeAttack node."""
        return self.windup_seconds + self.aim_to_fire_seconds

    @property
    def alert_seconds(self):
        """None when the attack script raises no AIAlertedEvent of its own."""
        if self.alert_after_fire_seconds is None:
            return None
        return self.fire_seconds + self.alert_after_fire_seconds


@dataclass(frozen=True)
class CreatureProfile:
    """One installed `ai/*.ai` asset, plus its slice health and attack script."""
    name: str
    ai_asset: str
    ai_sha256: str
    start_dormant: bool
    wake_up_seconds: float
    sensing_radius: float
    movement_enabled: bool
    can_patrol: bool
    aggro_radius_near: float
    aggro_radius_far: float
    aggro_angle_far_degrees: float
    aggro_check_line_of_sight: bool
    aggro_origin_offset_z: float
    aggro_target_offset_vertical: float
    deaggro_radius: float
    forced_aggro_seconds_on_damage: float
    attack_delay_for_new_targets: float
    min_time_between_attacks: float
    time_without_los_to_forget_target: float
    alert_radius_near: float
    alert_radius_far: float
    alert_delay_min: float
    alert_delay_max: float
    alert_repeat_moratorium: float
    target_types: tuple
    attack: AttackProfile
    script: AttackScript = None
    health_max: float = 0.0


SPITTER_ATTACK_SCRIPT = AttackScript(
    source='cdl/creatures/spitter/spitter_attack.cdl',
    sha256='fd82fba9b3c252ecca2f0aaedda86cce819576c417a7ab77b21b23c888f370e4',
    kind='projectile',
    windup_seconds=1.0, aim_to_fire_seconds=0.1, alert_after_fire_seconds=1.25,
    alert_radius=10.0, alert_check_line_of_sight=True,
    attack_asset='assets/creature/spitter/spitter_projectile.projectile',
    muzzle_offset_z=2.75)

# cdl/creatures/megadrone_v2/megadrone_v2_attack0.cdl (sha256 7c8eec7c...,
# bound by slices/creatures/megadrone/megadrone_v2.dynamicslice):
#   AIAttackEvent -> Condition -> EndEnterTriggerChecks -> HasBuff/RemoveBuff
#   Cloak -> Set using_primary1 = True -> Delay 0.65 -> ExternalGet the
#   target position -> VectorMath2 "Offset the target by 1m on the z axis"
#   -> CreateProjectile megadrone_projectile.projectile (UseManualTargeting
#   False) -> two PlayAudioEvents -> Delay 1.6 -> Set using_primary1 = False.
# The attack raises no alert of its own; megadrone_onattackalertothers.cdl is
# a separate AIRaiseAlertEvent script (Delay 3.0, 25 metre Megadrone query).
MEGADRONE_ATTACK_SCRIPT = AttackScript(
    source='cdl/creatures/megadrone_v2/megadrone_v2_attack0.cdl',
    sha256='7c8eec7ca39d9a69fefb26ac06f9db8943b43e71787665056314c4c996f1681d',
    kind='projectile',
    windup_seconds=0.6499999761581421, aim_to_fire_seconds=0.0,
    attack_asset='assets/creature/megadrone/megadrone_projectile.projectile',
    target_offset_z=1.0)

# cdl/creatures/snurtle/snurtle_attack0.cdl (sha256 1658e63d...):
#   AIAttackEvent -> ExternalGet own Health -> Condition "Snurtle attack #0
#   and the snurtle's health is not 0" -> ExternalGet target IsDead ->
#   Condition -> EndEnterTriggerChecks -> HasBuff/RemoveBuff Cloak ->
#   Set using_primary1 = True -> Delay 0.3 -> Set using_primary1 = False ->
#   Delay 0.8 -> PlayParticles -> CircleShapeEntityQuery radius 8, Player,
#   CheckLineOfSight False -> ForEach -> PlayCameraShakeAction + MeleeAttack
#   (snurtle_primary_attack.melee) -> Delay 1.25 -> Set using_primary1 = False.
SNURTLE_ATTACK_SCRIPT = AttackScript(
    source='cdl/creatures/snurtle/snurtle_attack0.cdl',
    sha256='1658e63d229eef0de02fba9064d1ae3843ae2735e56673b3e52df5523a94b834',
    kind='melee',
    windup_seconds=0.30000001192092896, aim_to_fire_seconds=0.800000011920929,
    attack_asset='assets/creature/snurtle/snurtle_primary_attack.melee',
    radius=8.0)

# ai/spitter.ai sha256 e8433ab3..., field offsets from
# tools/inspect_creature_ai.py misc.pak ai/spitter.ai --flat:
#   0x0f98 StartDormant False        0x0173 WakeUpParams.Duration 1.0
#   0x0702 MovementEnabled False     0x0205 CanPatrol False
#   0x1003 AggroRadiusNear 5.0       0x101d AggroRadiusFar 20.0
#   0x1037 AggroAngleFar 360.0       0x1051 DeaggroRadius 25.0
#   0x0fec AggroCheckLineOfSight True 0x1085 ForcedAggroDurationOnDamage 4.0
#   0x123e AttackDelayForNewTargets 1.0  0x1258 MinTimeBetweenAttacks 0.0
#   0x1272 TimeWithoutLoSToForgetCurrentTarget 8.0
#   0x12b7 Attacks[0].Angle 15.0     0x12d1/0x12eb Range 0.1..40.0
#   0x131f Attacks[0].Duration 3.0   0x136d/0x1387 RotationDisallowed 0.5..1.0
# Health 400/400 is the spitter slice's authored CrucibleHealthComponent
# resource (slices/creatures/spitter/spitter.dynamicslice sha256 60efed22...,
# offsets 0x4c1c max and 0x4c36 value); that slice's AiAsset field at 0x1dcf
# is the string "ai/spitter.ai", so Practice's creatures use the aggressive
# profile below, not the _noaggro variant the other levels reference.
SPITTER = CreatureProfile(
    name='spitter', ai_asset='ai/spitter.ai',
    ai_sha256='e8433ab347fea20170abe5821e9e68a0f25d924d1f27ea9d18e64739283fcb7d',
    start_dormant=False, wake_up_seconds=1.0, sensing_radius=20.0,
    movement_enabled=False, can_patrol=False,
    aggro_radius_near=5.0, aggro_radius_far=20.0, aggro_angle_far_degrees=360.0,
    aggro_check_line_of_sight=True, aggro_origin_offset_z=1.0,
    aggro_target_offset_vertical=1.0,
    deaggro_radius=25.0, forced_aggro_seconds_on_damage=4.0,
    attack_delay_for_new_targets=1.0, min_time_between_attacks=0.0,
    time_without_los_to_forget_target=8.0,
    alert_radius_near=4.0, alert_radius_far=20.0,
    alert_delay_min=0.25, alert_delay_max=1.0, alert_repeat_moratorium=4.0,
    target_types=('Player',),
    attack=AttackProfile(angle_degrees=15.0, range_min=0.1, range_max=40.0,
                         range_vertical_max=0.0, duration=3.0,
                         rotation_disallowed_min=0.5, rotation_disallowed_max=1.0),
    script=SPITTER_ATTACK_SCRIPT, health_max=400.0)

# ai/spitter_noaggro.ai sha256 differs only in the fields below; it is the
# variant every other shipped level references for scripted encounters.
SPITTER_NOAGGRO = replace(
    SPITTER, name='spitter_noaggro', ai_asset='ai/spitter_noaggro.ai',
    ai_sha256='504652d38bf6cb15a3f29545dcd1980e77ab80232cdc00636ea6062489aa3eb6',
    wake_up_seconds=0.0, aggro_radius_near=0.0, aggro_radius_far=0.0,
    deaggro_radius=60.0, forced_aggro_seconds_on_damage=5.0,
    alert_radius_near=1.0,
    attack=replace(SPITTER.attack, range_max=60.0))

# ai/megadrone.ai --flat: 0x1109 StartDormant True, 0x0173 WakeUp 0.7,
# 0x06e8 MovementEnabled True, 0x01eb CanPatrol True, 0x1174/0x118e aggro
# 7..25, 0x11a8 AggroAngleFar 115, 0x11c2 Deaggro 10, 0x13b1 AttackDelay 0.25,
# 0x142a Angle 65, 0x1444/0x145e range 0..15, 0x1492 Duration 2.7.
MEGADRONE = CreatureProfile(
    name='megadrone', ai_asset='ai/megadrone.ai',
    ai_sha256='1e868c88ed486d2e710012fe5d5a6a69031bb5c2449d0bc493c54695eaf45a55',
    start_dormant=True, wake_up_seconds=0.699999988079071, sensing_radius=20.0,
    movement_enabled=True, can_patrol=True,
    aggro_radius_near=7.0, aggro_radius_far=25.0, aggro_angle_far_degrees=115.0,
    aggro_check_line_of_sight=True, aggro_origin_offset_z=1.0,
    aggro_target_offset_vertical=1.0,
    deaggro_radius=10.0, forced_aggro_seconds_on_damage=4.0,
    attack_delay_for_new_targets=0.25, min_time_between_attacks=0.0,
    time_without_los_to_forget_target=8.0,
    alert_radius_near=2.0, alert_radius_far=15.0,
    alert_delay_min=0.25, alert_delay_max=0.75, alert_repeat_moratorium=4.0,
    target_types=('Player',),
    attack=AttackProfile(angle_degrees=65.0, range_min=0.0, range_max=15.0,
                         range_vertical_max=0.0, duration=2.700000047683716,
                         rotation_disallowed_min=0.0, rotation_disallowed_max=0.0),
    script=MEGADRONE_ATTACK_SCRIPT, health_max=0.0)

# ai/snurtle.ai --flat: aggro 5..15 angle 120, deaggro 30, forced aggro 6,
# AttackDelayForNewTargets 0.0, attack angle 90 range 0..5.5 duration 3.15.
SNURTLE = CreatureProfile(
    name='snurtle', ai_asset='ai/snurtle.ai',
    ai_sha256='3bc9e10b6b909d9cecf004230520085c1795a4bc88049217315dda607136205e',
    start_dormant=False, wake_up_seconds=1.0, sensing_radius=20.0,
    movement_enabled=True, can_patrol=True,
    aggro_radius_near=5.0, aggro_radius_far=15.0, aggro_angle_far_degrees=120.0,
    aggro_check_line_of_sight=True, aggro_origin_offset_z=1.0,
    aggro_target_offset_vertical=1.0,
    deaggro_radius=30.0, forced_aggro_seconds_on_damage=6.0,
    attack_delay_for_new_targets=0.0, min_time_between_attacks=0.0,
    time_without_los_to_forget_target=8.0,
    alert_radius_near=7.0, alert_radius_far=15.0,
    alert_delay_min=1.0, alert_delay_max=2.0, alert_repeat_moratorium=4.0,
    target_types=('Player',),
    attack=AttackProfile(angle_degrees=90.0, range_min=0.0, range_max=5.5,
                         range_vertical_max=0.0, duration=3.1500000953674316,
                         rotation_disallowed_min=0.5, rotation_disallowed_max=2.5),
    script=SNURTLE_ATTACK_SCRIPT, health_max=0.0)

PROFILES = {profile.name: profile for profile in
            (SPITTER, SPITTER_NOAGGRO, MEGADRONE, SNURTLE)}


# ---------------------------------------------------------------------------
# Events handed to other subsystems
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreatureEvent:
    kind: str
    time: float
    creature_id: int
    target_id: int = 0


@dataclass(frozen=True)
class CreatureAttack:
    """"Creature N attacked entity X at time T" - the combat handoff.

    `kind`, `attack_asset` and `origin` describe what the retail attack script
    would have spawned: a projectile from `origin` towards `target_position`,
    or a melee area query of `radius` around the creature. This module never
    computes damage: consuming code (server/combat.py) owns health, death and
    respawn.
    """
    time: float
    creature_id: int
    target_id: int
    attack_asset: str
    origin: tuple
    target_position: tuple
    kind: str = 'projectile'
    radius: float = 0.0


def lowest(low, high):
    """Deterministic default for the authored min/max ranges.

    Retail picks uniformly inside [min,max] for alert delays and the attack's
    rotation-lock period. A server that must be reproducible in tests cannot
    roll dice by default, so the low bound is used unless the caller injects
    another policy. This is a deliberate deviation, not an authored value.
    """
    return low


# ---------------------------------------------------------------------------
# Geometry and the verified packed transform
# ---------------------------------------------------------------------------


def horizontal_distance(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def distance(a, b):
    return math.dist(a[:3], b[:3])


def facing_yaw(origin, target):
    """Yaw that points a creature's +Y forward axis at `target`.

    CryEngine's character forward axis is +Y; `shard_stub.build_hunter_patrol_updates`
    uses the same atan2(-dx, dy) convention for the walking hunters.
    """
    return math.atan2(-(target[0] - origin[0]), target[1] - origin[1])


def angle_between(yaw, origin, target):
    """Absolute degrees between the creature's facing and the target."""
    delta = facing_yaw(origin, target) - yaw
    return abs(math.degrees(math.atan2(math.sin(delta), math.cos(delta))))


def pack_position(position):
    """8-byte packed Location position (4f47e10: X/Y 22 bits, Z 20 bits)."""
    x, y = (int((value + 4500) * 2097151.5 / 4500 + 0.5) for value in position[:2])
    z = int((position[2] + 1300) * 524287.5 / 1300 + 0.5)
    return ((x << 42) | (y << 20) | z).to_bytes(8, 'big')


def pack_rotation(yaw):
    """4-byte packed CryQuat for a yaw-only rotation, WXYZ memory order.

    The WXYZ order and the 723.37 scale are the correction recorded in
    docs/17-solo-ai.md and used by shard_stub.build_hunter_patrol_updates.
    """
    quaternion = (math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2))
    largest = max(range(4), key=lambda i: abs(quaternion[i]))
    sign = 1 if quaternion[largest] >= 0 else -1
    packed = largest << 30
    for shift, index in zip((20, 10, 0),
                            (i for i in range(4) if i != largest)):
        component = quaternion[index] * sign * 723.3699951171875 + 512
        packed |= max(0, min(1023, int(component))) << shift
    return packed.to_bytes(4, 'big')


def pack_transform(position, yaw):
    """The 12-byte atomic LocationComponent.InternalTransform payload."""
    return pack_position(position) + pack_rotation(yaw)


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------

DORMANT = 'dormant'
WAKING = 'waking'
IDLE = 'idle'
AGGRO = 'aggro'
ATTACKING = 'attacking'


@dataclass
class Creature:
    """One authored creature instance, driven entirely by an injected clock."""
    creature_id: int
    profile: CreatureProfile
    position: tuple
    yaw: float = 0.0
    state: str = field(init=False)
    target: int = 0
    dead: bool = False

    def __post_init__(self):
        self.state = DORMANT if self.profile.start_dormant else IDLE
        self._now = None
        self._state_since = 0.0
        self._target_since = 0.0
        self._target_seen_at = 0.0
        self._attack_started = None
        self._attack_fired = False
        self._attack_alerted = False
        self._last_attack_end = None
        self._forced_aggro_until = 0.0
        self._rotation_dirty = False

    # -- external stimuli ---------------------------------------------------

    def note_damage(self, now, attacker_id):
        """ForcedAggroDurationOnDamage (spitter.ai 0x1085 = 4.0 seconds).

        Retail also drives this path from the AIAlertedEvent script, which
        applies 1 damage to nearby players to force them into combat; that
        side effect is combat.py's decision, not this module's.
        """
        if self.dead:
            return None
        self._forced_aggro_until = now + self.profile.forced_aggro_seconds_on_damage
        if attacker_id and self.target != attacker_id and self.state != ATTACKING:
            self._acquire(now, attacker_id)
            return CreatureEvent('aggro', now, self.creature_id, attacker_id)
        return None

    def note_alert(self, now, aggressor_id):
        """AIAlertedEvent from another creature's attack script."""
        return self.note_damage(now, aggressor_id)

    def kill(self, now):
        self.dead = True
        self.target = 0
        self.state = IDLE
        self._attack_started = None
        return CreatureEvent('died', now, self.creature_id)

    # -- internals ----------------------------------------------------------

    def _acquire(self, now, target_id):
        self.target = target_id
        self._target_since = now
        self._target_seen_at = now
        self._state_since = now
        self.state = AGGRO

    def _eligible(self, position, visible):
        """Aggro test from AggroRadiusNear/Far, AggroAngleFar and line of sight."""
        profile = self.profile
        if profile.aggro_check_line_of_sight and not visible:
            return False
        origin = (self.position[0], self.position[1],
                  self.position[2] + profile.aggro_origin_offset_z)
        aim = (position[0], position[1],
               position[2] + profile.aggro_target_offset_vertical)
        span = distance(origin, aim)
        if span <= profile.aggro_radius_near:
            return True
        if span > profile.aggro_radius_far:
            return False
        return angle_between(self.yaw, self.position, position) <= \
            profile.aggro_angle_far_degrees / 2.0

    def _in_attack_envelope(self, position):
        attack = self.profile.attack
        span = distance(self.position, position)
        if not attack.range_min <= span <= attack.range_max:
            return False
        if attack.range_vertical_max and \
                abs(position[2] - self.position[2]) > attack.range_vertical_max:
            return False
        return angle_between(self.yaw, self.position, position) <= \
            attack.angle_degrees / 2.0

    def _face(self, position, locked):
        if locked:
            return False
        yaw = facing_yaw(self.position, position)
        if abs(yaw - self.yaw) < 1e-4:
            return False
        self.yaw = yaw
        return True

    # -- the tick -----------------------------------------------------------

    def step(self, now, targets, *, visible=None, pick=lowest):
        """Advance to `now`. `targets` maps entity id -> world position.

        `visible(creature, target_id)` supplies line of sight; with no
        navigation mesh or collision on the server the default is True, which
        is a simplification the retail AggroCheckLineOfSight flag does not make.
        Returns a list of CreatureEvent/CreatureAttack, oldest first.
        """
        if self._now is not None and now < self._now:
            raise ValueError('creature clock moved backwards')
        self._now = now
        if self.dead:
            return []
        see = (lambda entity: True) if visible is None else \
            (lambda entity: bool(visible(self, entity)))
        profile = self.profile
        events = []

        if self.state == DORMANT:
            sensed = [entity for entity, position in sorted(targets.items())
                      if distance(self.position, position) <= profile.sensing_radius]
            if sensed:
                self.state = WAKING
                self._state_since = now
                events.append(CreatureEvent('waking', now, self.creature_id))
            return events
        if self.state == WAKING:
            if now - self._state_since < profile.wake_up_seconds:
                return events
            self.state = IDLE
            self._state_since = now
            events.append(CreatureEvent('awake', now, self.creature_id))

        # Keep an existing target's last-seen stamp fresh for the LoS timeout.
        if self.target and self.target in targets and see(self.target):
            self._target_seen_at = now

        if self.state == IDLE:
            for entity, position in sorted(targets.items()):
                if self._eligible(position, see(entity)):
                    self._acquire(now, entity)
                    events.append(CreatureEvent('aggro', now, self.creature_id, entity))
                    break
            if self.state != AGGRO:
                return events

        target_position = targets.get(self.target)
        forced = now < self._forced_aggro_until

        if self.state == ATTACKING:
            events.extend(self._step_attack(now, target_position, pick))
            return events

        # AGGRO: deaggro tests first, then face, then decide to attack.
        if target_position is None or (
                not forced and (
                    distance(self.position, target_position) > profile.deaggro_radius
                    or now - self._target_seen_at
                    >= profile.time_without_los_to_forget_target)):
            lost = self.target
            self.target = 0
            self.state = IDLE
            self._state_since = now
            events.append(CreatureEvent('deaggro', now, self.creature_id, lost))
            return events

        if self._face(target_position, locked=False):
            events.append(CreatureEvent('rotated', now, self.creature_id, self.target))

        ready = now - self._target_since >= profile.attack_delay_for_new_targets
        cooled = (self._last_attack_end is None or
                  now - self._last_attack_end >= profile.min_time_between_attacks)
        if ready and cooled and self._in_attack_envelope(target_position):
            self.state = ATTACKING
            self._state_since = now
            self._attack_started = now
            self._attack_fired = False
            self._attack_alerted = False
            events.append(CreatureEvent('attack_started', now, self.creature_id,
                                        self.target))
            events.extend(self._step_attack(now, target_position, pick))
        return events

    def _step_attack(self, now, target_position, pick):
        """Replay the attack .cdl timeline against the injected clock."""
        profile = self.profile
        attack = profile.attack
        script = profile.script
        elapsed = now - self._attack_started
        events = []

        locked = elapsed < pick(attack.rotation_disallowed_min,
                                attack.rotation_disallowed_max)
        if target_position is not None and self._face(target_position, locked):
            events.append(CreatureEvent('rotated', now, self.creature_id, self.target))

        if script and not self._attack_fired and elapsed >= script.fire_seconds:
            self._attack_fired = True
            if target_position is not None:
                origin = (self.position[0], self.position[1],
                          self.position[2] + script.muzzle_offset_z)
                aim = (target_position[0], target_position[1],
                       target_position[2] + script.target_offset_z)
                events.append(CreatureAttack(now, self.creature_id, self.target,
                                             script.attack_asset, origin, aim,
                                             script.kind, script.radius))
        if (script and script.alert_seconds is not None
                and not self._attack_alerted and elapsed >= script.alert_seconds):
            self._attack_alerted = True
            events.append(CreatureEvent('alert_nearby', now, self.creature_id,
                                        self.target))
        if elapsed >= attack.duration:
            self.state = AGGRO
            self._state_since = now
            self._last_attack_end = now
            self._attack_started = None
            events.append(CreatureEvent('attack_ended', now, self.creature_id,
                                        self.target))
        elif (target_position is None or (
                attack.abandon_if_target_out_of_max_range
                and distance(self.position, target_position) > attack.range_max)):
            self.state = AGGRO
            self._state_since = now
            self._last_attack_end = now
            self._attack_started = None
            events.append(CreatureEvent('attack_terminated', now, self.creature_id,
                                        self.target))
        return events


class CreatureDirector:
    """Drives a whole map's creature population off one injected clock."""

    def __init__(self, placements, *, visible=None, pick=lowest):
        self.creatures = {}
        for placement in placements:
            profile = PROFILES[placement.profile]
            self.creatures[placement.entity_id] = Creature(
                placement.entity_id, profile, placement.position,
                yaw=placement.yaw)
        self._visible = visible
        self._pick = pick

    def step(self, now, targets):
        events = []
        for entity_id in sorted(self.creatures):
            events.extend(self.creatures[entity_id].step(
                now, targets, visible=self._visible, pick=self._pick))
        return events

    def note_damage(self, now, creature_id, attacker_id):
        creature = self.creatures.get(creature_id)
        if creature is None:
            return []
        event = creature.note_damage(now, attacker_id)
        alerted = [event] if event else []
        # The attack script's MakeAIAlertedEvent is the creature-to-creature
        # path; damage taken only forces this one creature's aggro.
        return alerted

    def alert_near(self, now, origin, radius, aggressor_id):
        """AIAlertedEvent fan-out used by a creature's attack script."""
        events = []
        for entity_id in sorted(self.creatures):
            creature = self.creatures[entity_id]
            if creature.creature_id == aggressor_id or creature.dead:
                continue
            if distance(creature.position, origin) <= radius:
                event = creature.note_alert(now, aggressor_id)
                if event:
                    events.append(event)
        return events

    def rotations(self):
        """entity id -> 12-byte packed transform for creatures facing a target."""
        return {entity_id: pack_transform(creature.position, creature.yaw)
                for entity_id, creature in sorted(self.creatures.items())
                if creature.target}
