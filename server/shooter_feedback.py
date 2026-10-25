def hit_scale(damage, weapon):
    """Marker size for one hit: its damage over the weapon's close-range maximum."""
    return max(0.0, min(1.0, damage / weapon.damage_max))
