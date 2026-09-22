"""Captured projectile results are client claims, not authoritative damage."""
import unittest
import shard_stub as shard


class ProjectileResultTests(unittest.TestCase):
    def test_captured_target_and_shooter_list(self):
        self.assertEqual(shard.parse_projectile_result(bytes.fromhex(
            '3002fe00004b01000002')), (48, (0xfe00004b, 0x01000002)))
        self.assertEqual(shard.parse_projectile_result(bytes.fromhex(
            '2a0101000002')), (42, (0x01000002,)))

    def test_bounds_and_exact_length(self):
        valid = bytes.fromhex('3002fe00004b01000002')
        for data in [valid[:i] for i in range(len(valid))] + [valid+b'\0', b'\0\x09'+b'\0'*36]:
            with self.subTest(data=data.hex()), self.assertRaises(ValueError):
                shard.parse_projectile_result(data)
        self.assertEqual(shard.parse_projectile_result(b'\xff\0'), (255, ()))
