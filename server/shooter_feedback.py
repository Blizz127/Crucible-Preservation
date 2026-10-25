"""Hit and kill confirmation for the local player's own shots (docs/36).

Both are ProjectileRequestComponent (115) RPCs with RpcDeliveryType 2
(Authority -> Autonomous), so they reach only the client that controls the
entity they are sent on: the player's own character. Retail build 5685572:

- Dispatch 0x9dd650 calls the game-side interface at [component+0x1298]:
  rpc 2 -> vtable+0x218 = 0xbc53d0, rpc 3 -> +0x220 = 0xbc5400 (vtable
  0x674c5c8).
- 0xbc53d0 forwards ConfirmHitIndicator's five fields to 0xbc4390. That sets
  the component's HitConfirmScaleValueToShow (+0x19a8), IsCrit (+0x19ac) and
  DirectDamage (+0x19ad) and increments HitIndicatorCounter (+0x19b0). For
  damage type 1 it also loads the hit-confirm sounds of AllowedAssets
  [projectileTypeIndex] from the shooter's projectile request (index 0..15,
  else no sound).
- 0xbc5400 stores killedEntity.m_Id in LastKilledPlayer (+0x19c0) and
  increments PlayerKillCounter (+0x19b4).
- The HUD's combat.outgoing.updates (ui/dist/reticle-container.js) reads
  {scale, isCrit, killCounter, directDamage, indirectDamage}. It draws the
  hit marker at max(scale, 0.2) * (crit ? 45 : 35) percent, and shows the
  kill marker for one second whenever killCounter rises.

Payload layouts (serializers 0x9d8e20 and 0x9d9070; RPC reader vtable
0x6d875b0, whose bool cursor starts empty and shares one byte low bit first):

  ConfirmHitIndicator  f32 BE scale | u8 bools (bit0 isCrit, bit1 directDamage)
                       | u8 damageType (range 0..4) | u32 BE index - INT32_MIN
  ConfirmKillIndicator i8 killedEntity.m_Id (handler default -1)

Opt-in: CRUCIBLE_SOLO_HIT_CONFIRM=1 and CRUCIBLE_SOLO_KILL_CONFIRM=1. The
retail server's choice of scale is not traced; the shard sends the hit's
damage as a fraction of the weapon's maximum (DERIVED).
"""
import math
import os
import struct

PROJECTILE_REQUEST_COMPONENT = 115
CONFIRM_HIT_INDICATOR = 2
CONFIRM_KILL_INDICATOR = 3
AUTHORITY_TO_AUTONOMOUS = 2          # RpcDeliveryType accepted by 0x9dd66c

# NovaNet::EHitConfirmationDamageTypes, registered at 0x4ae3891..0x4ae38df.
DAMAGE_NONE = 0
DAMAGE_PROJECTILE_TYPE_INDEXED = 1
DAMAGE_EXPLOSION = 2
DAMAGE_QUICK_MELEE = 3
DAMAGE_TYPE_MAX = 4                  # range descriptor 0x6e02670 = (0, 4)

NO_KILLED_ENTITY = -1                # 0x9dd6b0 pre-initialises m_Id to 0xff


def hit_confirm_enabled():
    return os.environ.get('CRUCIBLE_SOLO_HIT_CONFIRM') == '1'


def kill_confirm_enabled():
    return os.environ.get('CRUCIBLE_SOLO_KILL_CONFIRM') == '1'


def build_confirm_hit_indicator(*, scale, projectile_type_index, is_crit=False,
                                damage_type=DAMAGE_PROJECTILE_TYPE_INDEXED,
                                direct_damage=True):
    """The 10-byte ConfirmHitIndicatorRpcStruct payload."""
    if type(scale) not in (int, float) or not math.isfinite(scale) or scale < 0:
        raise ValueError('scale must be a finite non-negative number')
    if type(is_crit) is not bool or type(direct_damage) is not bool:
        raise ValueError('isCrit and directDamage must be bools')
    if type(damage_type) is not int or not 0 <= damage_type <= DAMAGE_TYPE_MAX:
        raise ValueError('damage type outside EHitConfirmationDamageTypes')
    if (type(projectile_type_index) is not int
            or not -2**31 <= projectile_type_index < 2**31):
        raise ValueError('projectile type index must be an int32')
    flags = int(is_crit) | int(direct_damage) << 1
    return struct.pack('>fBBI', scale, flags, damage_type,
                       (projectile_type_index + 2**31) & 0xffffffff)


def build_confirm_kill_indicator(killed_id=NO_KILLED_ENTITY):
    """The one-byte ConfirmKillIndicatorRpcStruct payload."""
    if type(killed_id) is not int or not -128 <= killed_id <= 127:
        raise ValueError('killedEntity id must be an int8')
    return struct.pack('>b', killed_id)


def hit_scale(damage, weapon):
    """Marker size for one hit: its damage over the weapon's close-range maximum."""
    return max(0.0, min(1.0, damage / weapon.damage_max))
