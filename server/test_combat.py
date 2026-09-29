import unittest

import combat


class ServerResolvedDamageTests(unittest.TestCase):
    def arena(self):
        arena = combat.Combat()
        arena.register(1, (), maximum=12.0, team=1)
        arena.register(2, (), maximum=12.0, team=2)
        arena.register(3, (), maximum=12.0, team=2, respawn_seconds=5.0)
        return arena

    def test_damage_kills_and_records_killer(self):
        arena = self.arena()
        hit = arena.apply_damage(2, 1, 7.0, now=1.0)
        self.assertEqual((hit.damage, hit.health, hit.killed), (7.0, 5.0, False))
        hit = arena.apply_damage(2, 1, 7.0, now=2.0)
        self.assertEqual((hit.health, hit.killed), (0.0, True))
        self.assertEqual(arena.entities[1].killer_id, 2)
        self.assertIsNone(arena.apply_damage(2, 1, 1.0, now=3.0))

    def test_same_team_dead_or_unknown_attackers_are_rejected(self):
        arena = self.arena()
        self.assertIsNone(arena.apply_damage(2, 3, 1.0))
        self.assertIsNone(arena.apply_damage(4, 1, 1.0))
        arena.entities[2].health = 0.0
        self.assertIsNone(arena.apply_damage(2, 1, 1.0))
        with self.assertRaises(ValueError):
            arena.apply_damage(3, 1, 0.0)

    def test_respawn_deadline_uses_supplied_time(self):
        arena = self.arena()
        arena.apply_damage(1, 3, 12.0, now=10.0)
        self.assertEqual(arena.entities[3].respawn_at, 15.0)


if __name__ == "__main__":
    unittest.main()
