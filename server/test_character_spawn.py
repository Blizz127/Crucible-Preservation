"""Retail spawn wire checks; these do not establish in-game possession."""
import unittest
import math
from unittest.mock import patch
import shard_stub as shard


class CharacterSpawnTests(unittest.TestCase):
    def test_spawn_transform_roundtrips_to_authored_start(self):
        wire = shard.pack_practice_spawn_transform()
        packed = int.from_bytes(wire[:8], 'big')
        actual = ((packed >> 42) * 9000 / ((1 << 22)-1) - 4500,
                  ((packed >> 20) & 0x3fffff) * 9000 / ((1 << 22)-1) - 4500,
                  (packed & 0xfffff) * 2600 / ((1 << 20)-1) - 1300)
        for got, expected in zip(actual, (501.09075927734375, 564.5346069335938, 45.27315902709961)):
            self.assertAlmostEqual(got, expected, delta=0.003)
        # Retail smallest-three quaternion: largest component w (index 0), each zero
        # component quantized to round(511.5) = 512. Reconstructed w ~ 1.
        quat = int.from_bytes(wire[8:], 'big')
        self.assertEqual(quat >> 30, 0)
        xyz = [(((quat >> shift) & 1023)-511.5)/723.3699951171875
               for shift in (20, 10, 0)]
        self.assertGreater(math.sqrt(1-sum(x*x for x in xyz)), 0.99999)

    def test_marine_spawn_has_own_autonomous_entity_and_parent(self):
        message = shard.build_practice_character_probe()
        self.assertEqual(message.header[:3], bytes.fromhex('081200'))
        self.assertEqual(int.from_bytes(message.header[2:6], 'big'), 9256072)
        self.assertEqual(message.header[6], 0)
        # 33 shared roots plus Location's 5 fields and Hierarchy's 2;
        # 25 unchanged autonomous roots. Values follow both records.
        self.assertEqual(message.payload[0], 40)
        self.assertEqual(message.payload[6:11], bytes.fromhex('1900000000'))
        self.assertEqual(message.payload[-4:], bytes.fromhex('01000001'))
        self.assertEqual(message.payload[11:-4], shard.pack_practice_spawn_transform())

    def test_spawn_requires_solo_phase_and_roster_once_in_opt_in_mode(self):
        state = {'seq': 5, 'received_seq': 4, 'received_window': 1}
        with patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character'}):
            for flag in ('bootstrap_sent', 'local_player_activation_received', 'match_store_sent', 'solo_phase_sent'):
                self.assertIsNone(shard.character_spawn_reply(state))
                state[flag] = True
            self.assertIsNotNone(shard.character_spawn_reply(state))
            self.assertIsNone(shard.character_spawn_reply(state))
            self.assertNotIn('gameplay_ready', state)
        with patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-roster'}):
            self.assertIsNone(shard.character_spawn_reply(dict(state, character_spawn_sent=False)))

    def test_links_wait_for_spawn_packet_ack_and_remain_once(self):
        from test_bootstrap import client_packet
        state = {'seq': 16, 'character_spawn_sent': True, 'character_spawn_seq': 16,
                 'received_seq': 12, 'received_window': 1}
        with patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character'}):
            self.assertIsNone(shard.character_links_reply(client_packet(2, 13, 15, 1), state))
            self.assertIsNone(shard.character_links_reply(client_packet(2, 14, 17, 1), state))
            self.assertIsNotNone(shard.character_links_reply(client_packet(2, 15, 17, 3), state))
            self.assertIsNone(shard.character_links_reply(client_packet(2, 16, 17, 3), state))
            self.assertNotIn('gameplay_ready', state)
        with patch.dict('os.environ', {'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-roster'}):
            self.assertIsNone(shard.character_links_reply(client_packet(2, 17, 16, 1),
                                                        dict(state, character_links_sent=False)))

    def test_spawned_roster_names_real_marine_and_character_id(self):
        message = shard.build_single_player_roster_probe(b'Hunter', spawned_character=True)
        self.assertEqual(message.payload[0], 33)
        self.assertTrue(message.payload.endswith(bytes.fromhex('0000080101017fff030000000101000002')))
        # No repeated slice-creation flag for an existing match store.
        self.assertEqual(message.header[2], shard.ENTITY_ROLE_CLIENT_SIMULATION)

    def test_connection_links_keep_component_value_order(self):
        message = shard.build_character_connection_links()
        # shared: 8 roots + parent/children/size/entry; autonomous also
        # selects the two Hierarchy control target fields.
        self.assertEqual(message.payload[0], 12)
        self.assertEqual(message.payload[3:6], bytes.fromhex('1006e0'))
        self.assertEqual(message.payload[6:], bytes.fromhex('000801010000020100000201000002'))
