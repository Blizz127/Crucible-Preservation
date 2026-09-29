"""Server-authoritative combat core: damage, health replication, death, respawn.

Every constant here is traced to an installed asset or a disassembly RVA and
cited at its definition. Nothing in this module has been verified in a live
run; it is implemented and unit-tested only. See docs/20-combat-core.md for
the evidence trail and the open questions.

This module never imports shard_stub at import time: shard_stub is the
consumer, so the helpers that need its encoders import it lazily.
"""
from dataclasses import dataclass, field
import struct
import time


# --- Damage model -----------------------------------------------------------
#
# assets/player/marine/marine_projectile_primary.projectile (misc.pak,
# sha256 56722f39...0e46adb91, ObjectStream v3). Field names are crc32 of the
# lowercased name; resolved against the executable's reflection strings:
#
#   SpeedUnitsPerSecond        0x054  625.0
#   LifetimeSeconds  (Vec3)    0x0bc  (0.4, 0.064, 0.096)
#   HealthChangeImmediateOnHit 0x392  (20.0, 10.0)      <- Vec2
#   HealthChangeOverTimeOnHit  0x3b1  (0.0, 0.0, 0.0)   <- Vec3, unused here
#   CriticalMultiplier         0x3d4  2.0
#   preciseCriticalHits        0x3ee  1
#   TargetFilter               0x323  "Enemies"
#   CreateProjectileType       0xaf8  "Hybrid"
#
# LifetimeSeconds is not one number. The edit-context descriptions sit at
# file offsets 0x6dee2b0 "Lifetime in seconds", 0x6dee2a0 "falloff begin",
# 0x6dee290 "falloff end", i.e. Vec3(lifetime, falloffBegin, falloffEnd), and
# Crucible::ProjectileUtils::CalculateFalloff (RVA 0x3220510) reads exactly
# those slots off the projectile data asset:
#
#   0x3220532  mov rax,[r9+0x150]          ; projectile data asset
#   0x3220539  movups xmm4,[rax+0x60]      ; the Vec3
#   0x322053d  movss  xmm5,[rax+0x64]      ; y = falloff begin
#   0x3220542  shufps xmm4,xmm4,0xaa       ; z = falloff end
#   0x3220546  ucomiss xmm4,0 / 0x322054b ucomiss xmm5,0
#   0x322054e  je 0x3220736                ; both zero -> return 1.0
#   ...        xmm0 = [r9+0x3c] (flight time) minus a sub-tick correction
#   0x32205c6  subss xmm4,xmm5             ; span = end - begin
#   0x32205ce  comiss xmm4,0 / ja 0x3220722
#   0x3220722  subss xmm0,xmm5             ; t - begin
#   0x3220726  maxss xmm0,0
#   0x322072a  minss xmm0,xmm4
#   0x322072e  divss xmm0,xmm4
#   0x3220732  subss xmm7,xmm0             ; xmm7 was 1.0 (const 0x6e00f40)
#   0x3220753  ret                         ; returns 1 - clamp01((t-b)/(e-b))
#
# span <= 0 takes the assert at 0x32205e8 ("... falloffstart (%f) that is
# greater that its falloffend (%f)") and returns 1.0 unchanged.
#
# So the ratio is 1 at or inside falloff begin and 0 at or beyond falloff end.
# The ratio then travels in DamageData.m_FalloffRatio alongside
# m_HealthChangeImmediateOnHitMax / Min, m_CriticalHit and
# m_CriticalHitMultiplier (reflected member list at file offset 0x6a738c3).


@dataclass(frozen=True)
class Weapon:
    """One projectile definition's damage-relevant fields."""
    name: str
    damage_max: float          # HealthChangeImmediateOnHit.x, at/inside begin
    damage_min: float          # HealthChangeImmediateOnHit.y, at/beyond end
    falloff_begin_s: float     # LifetimeSeconds.y
    falloff_end_s: float       # LifetimeSeconds.z
    speed: float               # SpeedUnitsPerSecond
    critical_multiplier: float  # CriticalMultiplier
    lifetime_s: float          # LifetimeSeconds.x

    @property
    def falloff_begin_units(self):
        """Bullet behaviour has zero acceleration/gravity/drag, so distance is
        speed * flight time; the asset's Acceleration/Gravity/Drag are all 0."""
        return self.falloff_begin_s * self.speed

    @property
    def falloff_end_units(self):
        return self.falloff_end_s * self.speed


MARINE_PRIMARY = Weapon('marine_projectile_primary', 20.0, 10.0,
                        0.064, 0.096, 625.0, 2.0, 0.4)
# 0.064 * 625 = 40 units, 0.096 * 625 = 60 units: 20 damage inside 40,
# falling linearly to 10 at 60 and staying there.


# --- Authored respawn timings -----------------------------------------------
# Practice respawning is authored in .cdl node graphs, not in the respawner
# slices: slices/practicearena/practicearena_spitterrespawner.dynamicslice and
# robodummyrespawner.dynamicslice only reference the scripts below. Read with
# tools/inspect_creature_cdl.py; values are the Delay nodes' Duration and the
# SpawnSlice nodes' TelegraphTime.
#
# cdl/practicearena/practicearena_targetdummydeathwatcher.cdl
#   (sha256 933d81b6...) OnDeathEvent -> Condition -> Delay 4.0
#   -> SpawnSlice(slices/practicearena/robodummy.dynamicslice, TelegraphTime 2)
PRACTICE_TARGET_RESPAWN_SECONDS = 4.0
PRACTICE_TARGET_TELEGRAPH_SECONDS = 2.0
# cdl/practicearena/practicearena_spittertracker.cdl (sha256 5c502d26...)
#   OnSpawnEvent -> SphereShapeEntityQuery(radius 2, type "Spitter")
#   -> GetContainerSize -> Condition; the absent branch waits Delay 5.0 then
#   SpawnSlice(slices/creatures/spitter/spitter_noaggro.dynamicslice,
#   TelegraphTime 0), the present branch re-polls after Delay 1.0.
# So the spitter is respawned by ABSENCE POLLING, not by a death event, and
# the slice it respawns is the *_noaggro variant.
PRACTICE_SPITTER_RESPAWN_SECONDS = 5.0
PRACTICE_SPITTER_TELEGRAPH_SECONDS = 0.0
PRACTICE_SPITTER_POLL_SECONDS = 1.0


def falloff_ratio(weapon, flight_seconds):
    """Port of CalculateFalloff (RVA 0x3220510); 1.0 near, 0.0 far."""
    span = weapon.falloff_end_s - weapon.falloff_begin_s
    if weapon.falloff_begin_s == 0.0 and weapon.falloff_end_s == 0.0:
        return 1.0
    if span <= 0.0:
        return 1.0  # the client asserts and leaves the ratio at 1.0
    return 1.0 - min(max(flight_seconds - weapon.falloff_begin_s, 0.0), span) / span


def falloff_ratio_for_distance(weapon, distance):
    """Distance form of the same curve; see Weapon.falloff_begin_units."""
    return falloff_ratio(weapon, distance / weapon.speed)


def immediate_damage(weapon, *, distance=None, critical=False):
    """DamageData's immediate health change for one hit.

    UNPROVEN: the exact arithmetic that combines Min/Max/FalloffRatio inside
    the client was not located, so the linear interpolation below is the
    reading the field names and the ratio's own 1-near/0-far orientation
    imply, not a decoded instruction sequence. The endpoints are exact: at
    ratio 1 it is HealthChangeImmediateOnHitMax and at ratio 0 it is Min.

    distance=None means "range unknown" and yields the close-range maximum;
    the shard cannot measure range from a ClientProjectileResult (see
    docs/20-combat-core.md, Open questions).
    """
    ratio = 1.0 if distance is None else falloff_ratio_for_distance(weapon, distance)
    damage = weapon.damage_min + (weapon.damage_max - weapon.damage_min) * ratio
    if critical:
        damage *= weapon.critical_multiplier
    return damage


# --- Health replication -----------------------------------------------------
#
# Health is native network component type 79. Its live replication graph
# (snapshots/gamemode-20260919/live-combat-layout.json) has property record
# indices [1,2,3,4,5,23] with a nested resource subtree at 23..26, and the
# default-resource serializer emits five regeneration/timing floats then
# minimum, maximum and value (9355f0 -> 9359a0 -> 93d870 -> 8c3780 ->
# 34cbea0 -> 35dc910). With net_CompressHeaders=1 the wire carries traversal
# decisions, not the whole schema (0x4be8240), so selecting the value triplet
# alone is ten decisions:
HEALTH_NETWORK_TYPE = 79
HEALTH_VALUE_BITS = (True,      # component root
                     False, False, False, False, False,   # records 1..5
                     True,      # 23 Resource
                     True,      # 24 DefaultResource
                     False,     # 25 regeneration/timing group
                     True)      # 26 minimum/maximum/value


def health_traversal_bits(shared_types):
    """Compressed shared traversal selecting only Health's value triplet.

    shared_types is the entity's network component type ids in ascending
    order (NetSystemComponent orders by vtable+0x70, 0x4a950d0..0x4a953a6).
    """
    bits = []
    for component in shared_types:
        bits.extend(HEALTH_VALUE_BITS if component == HEALTH_NETWORK_TYPE
                    else (False,))
    if HEALTH_NETWORK_TYPE not in shared_types:
        raise ValueError('entity has no Health component to update')
    return bits


def build_health_payload(shared_types, *, minimum, maximum, value):
    """Replication record then three big-endian floats (35dc910)."""
    import shard_stub
    return (shard_stub.build_replication_record(health_traversal_bits(shared_types))
            + struct.pack('>fff', minimum, maximum, value))


def build_health_update(entity_id, shared_types, *, maximum, value,
                        minimum=0.0, local=False, previous_id=0):
    """One EntityMessage carrying an absolute Health value for any entity.

    Byte-identical to shard_stub.build_practice_health_update for the two NPC
    families that function supports; unlike it, this accepts zero (death) and
    is not restricted to the Practice population.
    """
    import shard_stub
    if not minimum <= value <= maximum:
        raise ValueError('health outside [minimum, maximum]')
    return shard_stub.build_entity_message(
        entity_id & 0xffffff,
        shard_stub.pack_type_and_flags(shard_stub.ENTITY_ROLE_CLIENT_SIMULATION),
        payload=build_health_payload(shared_types, minimum=minimum,
                                     maximum=maximum, value=value),
        previous_id=previous_id, local=local)


# --- Entity state -----------------------------------------------------------


@dataclass
class CombatEntity:
    """Registered combatant. team is used for the projectile's Enemies filter."""
    entity_id: int
    shared_types: tuple
    maximum: float
    team: int
    local: bool = False                 # baked-level id kind for RelativeId
    health: float = None
    respawn_seconds: float = None       # None: never respawns
    telegraph_seconds: float = 0.0
    respawn_at: float = None            # monotonic deadline while dead
    dirty: bool = False                 # replicate on the next tick
    killer_id: int = None

    def __post_init__(self):
        if self.health is None:
            self.health = self.maximum

    @property
    def alive(self):
        return self.health > 0.0


@dataclass(frozen=True)
class HitResult:
    """Accepted reported hit, after every gate."""
    target: int
    damage: float
    health: float
    killed: bool


@dataclass
class Combat:
    """Authoritative health for NPCs, hunter bots and the player alike.

    Construction registers nothing; the shard registers what it spawned.
    tick() is the only thing that produces packets, so a caller that drops a
    datagram recovers on the next tick: every tracked entity repeats its
    absolute health, exactly as the nonlethal probe did.
    """
    entities: dict = field(default_factory=dict)
    last_projectile: dict = field(default_factory=dict)
    # Explicit probe metadata only. Asset table indices alone do not establish
    # wire enum ids or a neutral default source/ability pair (docs/27).
    death_metadata: tuple = None

    def __post_init__(self):
        if self.death_metadata is not None:
            if (len(self.death_metadata) != 2 or
                    any(type(v) is not int or not 0 <= v <= 65535
                        for v in self.death_metadata)):
                raise ValueError('death metadata must contain two uint16 ids')

    # -- registration
    def register(self, entity_id, shared_types, *, maximum, team, local=False,
                 health=None, respawn_seconds=None, telegraph_seconds=0.0):
        entity = CombatEntity(entity_id, tuple(shared_types), float(maximum),
                              team, local, None if health is None else float(health),
                              respawn_seconds, telegraph_seconds)
        health_traversal_bits(entity.shared_types)  # reject unreplicatable entities
        self.entities[entity_id] = entity
        return entity

    def health(self, entity_id):
        return self.entities[entity_id].health

    def alive(self, entity_id):
        return self.entities[entity_id].alive

    def terminal_winner(self):
        """Return the sole surviving team, 0 for a draw, or None in progress.

        A team with any scheduled respawn is still in play. The caller decides
        whether this match profile actually uses team elimination.
        """
        teams = {entity.team for entity in self.entities.values()}
        alive = {entity.team for entity in self.entities.values() if entity.alive}
        if any(entity.respawn_at is not None for entity in self.entities.values()):
            return None
        if len(alive) == 1:
            return next(iter(alive))
        if not alive and teams:
            return 0
        if len(alive) > 1:
            return None
        return None

    # -- damage
    def apply_reported_hit(self, shooter, projectile_id, reported, *,
                           weapon=MARINE_PRIMARY, distance=None,
                           critical=False, now=None):
        """Apply one ClientProjectileResult (component 115 / RPC 4).

        The report is contact history, not a damage command, so every gate
        from the nonlethal probe stays: the shooter must be registered, the
        projectile id must advance in forward modulo-256 order for that
        shooter, and exactly one registered enemy of the shooter must appear
        in the list. Marine primary despawns on collision (DynamicHitSetting
        "Despawn"), so an ambiguous multi-target report cannot name a target.

        Returns a HitResult, or None when a gate rejects the report.
        """
        now = time.monotonic() if now is None else now
        attacker = self.entities.get(shooter)
        if attacker is None or not attacker.alive:
            return None
        previous = self.last_projectile.get(shooter)
        if previous is not None and not 0 < (projectile_id - previous) % 256 < 128:
            return None
        # TargetFilter is "Enemies" (asset offset 0x323): same-team and self
        # hits are not damage.
        targets = {e for e in reported
                   if e in self.entities and self.entities[e].team != attacker.team
                   and self.entities[e].alive}
        if len(targets) != 1:
            return None
        self.last_projectile[shooter] = projectile_id
        entity = self.entities[targets.pop()]
        damage = immediate_damage(weapon, distance=distance, critical=critical)
        entity.health = max(0.0, entity.health - damage)
        entity.dirty = True
        killed = not entity.alive
        if killed:
            entity.killer_id = shooter
            self._die(entity, now)
        return HitResult(entity.entity_id, damage, entity.health, killed)

    def apply_damage(self, attacker, target, damage, *, now=None):
        """Apply server-resolved damage (NPC shots and attacks).

        Unlike apply_reported_hit there is no client report to gate: the
        caller has already resolved the hit. Both entities must be registered
        and alive and on different teams. Returns a HitResult or None.
        """
        now = time.monotonic() if now is None else now
        source = self.entities.get(attacker)
        entity = self.entities.get(target)
        if (source is None or entity is None or not source.alive
                or not entity.alive or source.team == entity.team):
            return None
        if type(damage) not in (int, float) or not 0 < damage < float('inf'):
            raise ValueError('damage must be a positive finite number')
        entity.health = max(0.0, entity.health - damage)
        entity.dirty = True
        killed = not entity.alive
        if killed:
            entity.killer_id = attacker
            self._die(entity, now)
        return HitResult(entity.entity_id, float(damage), entity.health, killed)

    def _die(self, entity, now):
        """Schedule the simulation's respawn after health reaches zero.

        The default still publishes health only. Explicit death metadata
        additionally selects records 2/3 (docs/27); the client's death event
        and respawn presentation remain unverified. The respawn deadline
        includes the authored telegraph, which precedes the spawn itself:
        the dummy death watcher waits Delay 4.0 and then spawns with
        TelegraphTime 2, so the target is back 6.0 s after it died.
        """
        if entity.respawn_seconds is not None:
            entity.respawn_at = (now + entity.respawn_seconds
                                 + entity.telegraph_seconds)

    # -- tick
    def tick(self, now=None):
        """Advance respawns and return the entity updates to replicate.

        Every entity that has been touched repeats its absolute health, so a
        dropped UDP update cannot lose a hit or a respawn.
        """
        now = time.monotonic() if now is None else now
        for entity in self.entities.values():
            if entity.respawn_at is not None and now >= entity.respawn_at:
                entity.respawn_at = None
                entity.health = entity.maximum
                entity.killer_id = None
                entity.dirty = True
        return [entity for entity in sorted(self.entities.values(),
                                            key=lambda e: e.entity_id)
                if entity.dirty]

    def build_messages(self, entities, *, previous_id=0):
        """EntityMessages for tick()'s result, chained for RelativeId."""
        messages = []
        for entity in entities:
            builder, metadata = build_health_update, {}
            if (self.death_metadata is not None and not entity.alive
                    and entity.killer_id is not None):
                from death_events import build_death_update
                builder = build_death_update
                metadata = dict(source_name=self.death_metadata[0],
                                ability_name=self.death_metadata[1],
                                killer_id=entity.killer_id)
            messages.append(builder(
                entity.entity_id, entity.shared_types, maximum=entity.maximum,
                value=entity.health, local=entity.local, previous_id=previous_id,
                **metadata))
            previous_id = entity.entity_id & 0xffffff
        return messages
