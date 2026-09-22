"""Authored population replication checks, not AI/combat acceptance."""
import unittest
from unittest.mock import patch
import lz4.block
import shard_stub as shard


class PracticeNpcTests(unittest.TestCase):
    def test_authored_population_has_two_creatures_and_eight_baked_targets(self):
        messages = shard.build_practice_npc_probes()
        self.assertEqual(len(messages), 10)
        # Two ordinary spitter entities, then eight existing baked RoboDummies.
        self.assertEqual(messages[0].header[1], 0x11)
        self.assertEqual(int.from_bytes(messages[0].header[2:6], 'big'), 1141747700)
        self.assertEqual(messages[0].payload[0], 23)  # 18 roots + Location fields
        self.assertEqual(messages[2].payload[0], 20)  # 15 roots + Location fields
        for message in messages:
            self.assertLess(len(message.payload), 32)
        # Different authored spawns; no NPC copied onto the player's position.
        self.assertNotEqual(messages[0].payload[-12:], messages[1].payload[-12:])
        self.assertNotEqual(messages[0].payload[-12:], shard.pack_practice_spawn_transform())

    def test_population_is_opt_in_and_sent_once_after_character_links(self):
        state = {'seq': 10, 'received_seq': 5, 'received_window': 1}
        with patch.dict('os.environ', {'CRUCIBLE_PRACTICE_NPCS':'1',
                                      'CRUCIBLE_SHARD_BOOTSTRAP':'practice-character'}):
            self.assertIsNone(shard.practice_npcs_reply(state))
            state['character_links_sent'] = True
            reply = shard.practice_npcs_reply(state)
            raw = lz4.block.decompress(reply[1:], uncompressed_size=4096)
            self.assertEqual(int.from_bytes(raw[15:17], 'big'), 10)
            self.assertIsNone(shard.practice_npcs_reply(state))
            self.assertNotIn('ai_working', state)
        with patch.dict('os.environ', {'CRUCIBLE_PRACTICE_NPCS':'0'}):
            self.assertIsNone(shard.practice_npcs_reply(dict(state, practice_npcs_sent=False)))

    def test_authored_rotations_preserve_upright_world_axis(self):
        import math
        for index, message in enumerate(shard.build_practice_npc_probes()):
            packed = int.from_bytes(message.payload[-4:], 'big')
            largest = packed >> 30
            components = [(float((packed >> shift) & 1023)-511.5)/723.3699951171875
                          for shift in (20, 10, 0)]
            components.insert(largest, math.sqrt(1-sum(x*x for x in components)))
            # NovaNet uses CryQuat memory order w,x,y,z. Test the rotated
            # world axes against the authored matrices, not just packed bits.
            w, x, y, z = components
            up = (2*(x*z+w*y), 2*(y*z-w*x), 1-2*(x*x+y*y))
            self.assertAlmostEqual(up[2], 1.0, delta=0.005)
            forward_y = 1-2*(x*x+z*z)
            self.assertAlmostEqual(forward_y, 1 if index < 2 else -1, delta=0.005)
