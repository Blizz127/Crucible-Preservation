import struct
import unittest

import combat
import shard_stub as shard
from practice_population import DUMMY_SHARED_TYPES, SPITTER_SHARED_TYPES

MARINE_SHARED_TYPES = SPITTER_SHARED_TYPES  # only Health's position matters here


class FalloffTests(unittest.TestCase):
    """Port of CalculateFalloff, RVA 0x3220510."""

    def test_ratio_is_one_inside_begin_and_zero_beyond_end(self):
        w = combat.MARINE_PRIMARY
        self.assertEqual(combat.falloff_ratio(w, 0.0), 1.0)
        self.assertEqual(combat.falloff_ratio(w, w.falloff_begin_s), 1.0)
        self.assertEqual(combat.falloff_ratio(w, w.falloff_end_s), 0.0)
        self.assertEqual(combat.falloff_ratio(w, 10.0), 0.0)
        self.assertAlmostEqual(combat.falloff_ratio(w, 0.080), 0.5)

    def test_asset_falloff_seconds_are_forty_and_sixty_units(self):
        w = combat.MARINE_PRIMARY
        self.assertEqual((w.falloff_begin_units, w.falloff_end_units), (40.0, 60.0))

    def test_unconfigured_and_inverted_falloff_return_one(self):
        flat = combat.Weapon('flat', 5.0, 5.0, 0.0, 0.0, 100.0, 2.0, 1.0)
        self.assertEqual(combat.falloff_ratio(flat, 3.0), 1.0)
        inverted = combat.Weapon('inv', 5.0, 1.0, 0.5, 0.2, 100.0, 2.0, 1.0)
        self.assertEqual(combat.falloff_ratio(inverted, 0.4), 1.0)


class DamageTests(unittest.TestCase):
    def test_marine_primary_endpoints_and_interpolation(self):
        w = combat.MARINE_PRIMARY
        self.assertEqual(combat.immediate_damage(w, distance=0.0), 20.0)
        self.assertEqual(combat.immediate_damage(w, distance=40.0), 20.0)
        self.assertEqual(combat.immediate_damage(w, distance=50.0), 15.0)
        self.assertEqual(combat.immediate_damage(w, distance=60.0), 10.0)
        self.assertEqual(combat.immediate_damage(w, distance=400.0), 10.0)

    def test_unknown_range_uses_the_close_maximum(self):
        self.assertEqual(combat.immediate_damage(combat.MARINE_PRIMARY), 20.0)

    def test_critical_multiplies_after_falloff(self):
        w = combat.MARINE_PRIMARY
        self.assertEqual(combat.immediate_damage(w, distance=50.0, critical=True), 30.0)


class HealthEncodingTests(unittest.TestCase):
    def test_traversal_matches_the_captured_health_graph(self):
        bits = combat.health_traversal_bits(DUMMY_SHARED_TYPES)
        self.assertEqual(len(bits), len(DUMMY_SHARED_TYPES) + 9)
        start = DUMMY_SHARED_TYPES.index(79)
        self.assertEqual(tuple(bits[start:start + 10]), combat.HEALTH_VALUE_BITS)
        self.assertFalse(any(bits[:start]))
        self.assertFalse(any(bits[start + 10:]))

    def test_entity_without_health_is_rejected(self):
        with self.assertRaises(ValueError):
            combat.health_traversal_bits((13, 24, 44))

    def test_packet_is_byte_identical_to_the_existing_probe(self):
        for entity, types, maximum, local in (
                (0xfe00004b, DUMMY_SHARED_TYPES, 600.0, True),
                (0xfe000052, DUMMY_SHARED_TYPES, 600.0, True),
                (0x1000003, SPITTER_SHARED_TYPES, 400.0, False)):
            for value, previous in ((maximum, 0), (1.0, 5), (123.0, 3)):
                self.assertEqual(
                    combat.build_health_update(entity, types, maximum=maximum,
                                               value=value, local=local,
                                               previous_id=previous),
                    shard.build_practice_health_update(entity, value,
                                                       previous_id=previous))

    def test_zero_health_encodes_and_the_old_probe_refuses_it(self):
        message = combat.build_health_update(0xfe00004b, DUMMY_SHARED_TYPES,
                                             maximum=600.0, value=0.0, local=True)
        self.assertEqual(message.payload[-12:], struct.pack('>fff', 0, 600.0, 0.0))
        with self.assertRaises(ValueError):
            shard.build_practice_health_update(0xfe00004b, 0)

    def test_out_of_range_health_is_rejected(self):
        with self.assertRaises(ValueError):
            combat.build_health_update(0xfe00004b, DUMMY_SHARED_TYPES,
                                       maximum=600.0, value=601.0, local=True)
        with self.assertRaises(ValueError):
            combat.build_health_update(0xfe00004b, DUMMY_SHARED_TYPES,
                                       maximum=600.0, value=-1.0, local=True)


def practice_combat():
    c = combat.Combat()
    c.register(0x1000002, MARINE_SHARED_TYPES, maximum=800.0, team=1)
    for entity in range(0xfe00004b, 0xfe000053):
        c.register(entity, DUMMY_SHARED_TYPES, maximum=600.0, team=2, local=True,
                   respawn_seconds=combat.PRACTICE_TARGET_RESPAWN_SECONDS,
                   telegraph_seconds=combat.PRACTICE_TARGET_TELEGRAPH_SECONDS)
    for entity in (0x1000003, 0x1000004):
        c.register(entity, SPITTER_SHARED_TYPES, maximum=400.0, team=2,
                   respawn_seconds=combat.PRACTICE_SPITTER_RESPAWN_SECONDS,
                   telegraph_seconds=combat.PRACTICE_SPITTER_TELEGRAPH_SECONDS)
    return c


class HitGateTests(unittest.TestCase):
    def test_accepted_hit_damages_exactly_one_enemy(self):
        c = practice_combat()
        hit = c.apply_reported_hit(0x1000002, 0x30, (0xfe00004b, 0x1000002), now=0.0)
        self.assertEqual((hit.target, hit.damage, hit.health, hit.killed),
                         (0xfe00004b, 20.0, 580.0, False))

    def test_replayed_and_stale_projectile_ids_are_ignored(self):
        c = practice_combat()
        c.apply_reported_hit(0x1000002, 0x30, (0xfe00004b,), now=0.0)
        self.assertIsNone(c.apply_reported_hit(0x1000002, 0x30, (0xfe00004b,), now=0.0))
        self.assertIsNone(c.apply_reported_hit(0x1000002, 0x2f, (0xfe00004b,), now=0.0))
        self.assertEqual(c.health(0xfe00004b), 580.0)

    def test_projectile_ids_wrap_forward_past_255(self):
        c = practice_combat()
        c.apply_reported_hit(0x1000002, 0xf0, (0xfe00004b,), now=0.0)
        self.assertIsNotNone(c.apply_reported_hit(0x1000002, 0x02, (0xfe00004b,), now=0.0))
        self.assertEqual(c.health(0xfe00004b), 560.0)

    def test_unknown_shooter_and_unknown_target_are_ignored(self):
        c = practice_combat()
        self.assertIsNone(c.apply_reported_hit(0x1000009, 1, (0xfe00004b,), now=0.0))
        self.assertIsNone(c.apply_reported_hit(0x1000002, 1, (0xfe00007d,), now=0.0))
        self.assertEqual(c.health(0xfe00004b), 600.0)

    def test_same_team_and_self_hits_are_not_damage(self):
        c = practice_combat()
        c.register(0x1000005, MARINE_SHARED_TYPES, maximum=800.0, team=1)
        self.assertIsNone(c.apply_reported_hit(0x1000002, 1, (0x1000002,), now=0.0))
        self.assertIsNone(c.apply_reported_hit(0x1000002, 1, (0x1000005,), now=0.0))
        self.assertEqual(c.health(0x1000005), 800.0)

    def test_ambiguous_multi_target_report_is_refused(self):
        c = practice_combat()
        self.assertIsNone(c.apply_reported_hit(
            0x1000002, 1, (0xfe00004b, 0xfe00004c), now=0.0))
        self.assertEqual(c.health(0xfe00004b), 600.0)

    def test_a_dead_target_cannot_be_hit_again(self):
        c = practice_combat()
        projectile = 1
        while c.alive(0xfe00004b):
            c.apply_reported_hit(0x1000002, projectile, (0xfe00004b,), now=0.0)
            projectile += 1
        self.assertIsNone(c.apply_reported_hit(0x1000002, projectile,
                                               (0xfe00004b,), now=0.0))


class DeathAndRespawnTests(unittest.TestCase):
    def test_health_reaches_zero_and_is_not_clamped_to_one(self):
        c = practice_combat()
        for projectile in range(1, 31):     # 30 * 20 = 600
            hit = c.apply_reported_hit(0x1000002, projectile, (0xfe00004b,), now=0.0)
        self.assertEqual(c.health(0xfe00004b), 0.0)
        self.assertTrue(hit.killed)
        self.assertFalse(c.alive(0xfe00004b))

    def test_death_replicates_health_zero(self):
        c = practice_combat()
        for projectile in range(1, 31):
            c.apply_reported_hit(0x1000002, projectile, (0xfe00004b,), now=0.0)
        message, = c.build_messages(c.tick(now=0.0))
        self.assertEqual(message.payload[-12:], struct.pack('>fff', 0, 600.0, 0.0))

    def test_respawn_restores_full_health_on_the_authored_duration(self):
        c = practice_combat()
        for projectile in range(1, 31):
            c.apply_reported_hit(0x1000002, projectile, (0xfe00004b,), now=100.0)
        deadline = (100.0 + combat.PRACTICE_TARGET_RESPAWN_SECONDS
                    + combat.PRACTICE_TARGET_TELEGRAPH_SECONDS)
        c.tick(now=deadline - 0.01)
        self.assertEqual(c.health(0xfe00004b), 0.0)
        c.tick(now=deadline)
        self.assertEqual(c.health(0xfe00004b), 600.0)
        self.assertTrue(c.alive(0xfe00004b))

    def test_an_entity_without_a_respawn_duration_stays_dead(self):
        c = combat.Combat()
        c.register(0x1000002, MARINE_SHARED_TYPES, maximum=20.0, team=1)
        c.register(0xfe00004b, DUMMY_SHARED_TYPES, maximum=20.0, team=2, local=True)
        c.apply_reported_hit(0x1000002, 1, (0xfe00004b,), now=0.0)
        c.tick(now=1e6)
        self.assertEqual(c.health(0xfe00004b), 0.0)


class TickTests(unittest.TestCase):
    def test_untouched_entities_produce_no_updates(self):
        self.assertEqual(practice_combat().tick(now=0.0), [])

    def test_touched_entities_repeat_absolute_health_every_tick(self):
        c = practice_combat()
        c.apply_reported_hit(0x1000002, 1, (0xfe00004b,), now=0.0)
        c.apply_reported_hit(0x1000002, 2, (0x1000003,), now=0.0)
        for _ in range(3):
            updates = c.tick(now=0.0)
            self.assertEqual([e.entity_id for e in updates],
                             [0x1000003, 0xfe00004b])
            messages = c.build_messages(updates)
            self.assertEqual(messages[0].payload[-12:],
                             struct.pack('>fff', 0, 400.0, 380.0))
            self.assertEqual(messages[1].payload[-12:],
                             struct.pack('>fff', 0, 600.0, 580.0))

    def test_messages_chain_relative_ids(self):
        c = practice_combat()
        c.apply_reported_hit(0x1000002, 1, (0xfe00004b,), now=0.0)
        c.apply_reported_hit(0x1000002, 2, (0x1000003,), now=0.0)
        messages = c.build_messages(c.tick(now=0.0), previous_id=7)
        self.assertEqual(messages[0].header,
                         shard.build_practice_health_update(0x1000003, 380.0,
                                                            previous_id=7).header)
        self.assertEqual(messages[1].header,
                         shard.build_practice_health_update(0xfe00004b, 580.0,
                                                            previous_id=3).header)

    def test_the_whole_packet_still_fits_the_entity_updates_envelope(self):
        c = practice_combat()
        for index, entity in enumerate(range(0xfe00004b, 0xfe000053)):
            c.apply_reported_hit(0x1000002, index + 1, (entity,), now=0.0)
        packet = shard.build_entity_updates(b'\x00', 1, 1,
                                            c.build_messages(c.tick(now=0.0)),
                                            game_time_ms=2000)
        self.assertLess(len(packet), 1200)


if __name__ == '__main__':
    unittest.main()


class ServerResolvedDamageTests(unittest.TestCase):
    def arena(self):
        arena = combat.Combat()
        arena.register(0x1000002, (13, 79), maximum=100., team=1)
        arena.register(0x1000003, (13, 79), maximum=100., team=2)
        arena.register(0x1000004, (13, 79), maximum=100., team=2,
                       respawn_seconds=5.)
        return arena

    def test_damage_kills_and_records_killer(self):
        arena = self.arena()
        hit = arena.apply_damage(0x1000003, 0x1000002, 60., now=1.)
        self.assertEqual((hit.damage, hit.health, hit.killed), (60., 40., False))
        hit = arena.apply_damage(0x1000003, 0x1000002, 60., now=2.)
        self.assertEqual((hit.health, hit.killed), (0., True))
        self.assertEqual(arena.entities[0x1000002].killer_id, 0x1000003)
        self.assertIsNone(arena.apply_damage(0x1000003, 0x1000002, 1., now=3.))

    def test_same_team_dead_or_unknown_attackers_are_rejected(self):
        arena = self.arena()
        self.assertIsNone(arena.apply_damage(0x1000003, 0x1000004, 10.))
        self.assertIsNone(arena.apply_damage(0x1000009, 0x1000002, 10.))
        arena.entities[0x1000003].health = 0.
        self.assertIsNone(arena.apply_damage(0x1000003, 0x1000002, 10.))
        with self.assertRaises(ValueError):
            arena.apply_damage(0x1000004, 0x1000002, 0.)

    def test_respawn_deadline_uses_supplied_time(self):
        arena = self.arena()
        arena.apply_damage(0x1000002, 0x1000004, 100., now=10.)
        self.assertEqual(arena.entities[0x1000004].respawn_at, 15.)
