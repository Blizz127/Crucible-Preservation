import unittest
from unittest.mock import patch
import shard_stub as shard


class SoloDamageTests(unittest.TestCase):
    @patch.object(shard, 'log')
    @patch.dict('os.environ', {'CRUCIBLE_PRACTICE_DAMAGE': '1'})
    def test_hit_is_nonlethal_and_not_replayed_or_applied_to_shooter(self, _log):
        state = {'practice_npcs_sent': True}
        hit = shard.EntityRpc(3, 0x1000002, 115, 4, bytes.fromhex('3002fe00004b01000002'))
        shard.observe_practice_damage(hit, state)
        self.assertEqual(state['npc_health'], {0xfe00004b: 580.0})
        shard.observe_practice_damage(hit, state)
        self.assertEqual(state['npc_health'][0xfe00004b], 580)
        for i in range(49, 90):
            shard.observe_practice_damage(shard.EntityRpc(3, 0x1000002, 115, 4,
                bytes([i, 2])+bytes.fromhex('fe00004b01000002')), state)
        self.assertEqual(state['npc_health'][0xfe00004b], 1)

    @patch.dict('os.environ', {'CRUCIBLE_PRACTICE_DAMAGE': '1'})
    def test_rejects_unspawned_wrong_sender_and_unknown_targets(self):
        for state, sender, data in [({}, 0x1000002, '0101fe00004b'),
                ({'practice_npcs_sent': True}, 0x1000003, '0101fe00004b'),
                ({'practice_npcs_sent': True}, 0x1000002, '0101fe00007d')]:
            shard.observe_practice_damage(shard.EntityRpc(3,sender,115,4,bytes.fromhex(data)),state)
            self.assertNotIn('npc_health',state)

    def test_health_delta_selects_only_resource_value(self):
        import struct
        from practice_population import DUMMY_SHARED_TYPES
        message = shard.build_practice_health_update(0xfe00004b, 580)
        bits=[]
        for c in DUMMY_SHARED_TYPES:
            bits.extend([True,False,False,False,False,False,True,True,False,True]
                        if c==79 else [False])
        self.assertEqual(message.payload, shard.build_replication_record(bits)+struct.pack('>fff',0,600,580))
        self.assertEqual(message.header[:3], bytes.fromhex('ae0201'))  # baked 75; no new slice
        with self.assertRaises(ValueError):
            shard.build_practice_health_update(0x1000002, 780)

    @patch.dict('os.environ', {'CRUCIBLE_PRACTICE_DAMAGE': '0'})
    def test_disabled_by_default(self):
        state = {'practice_npcs_sent': True}
        shard.observe_practice_damage(shard.EntityRpc(3,0x1000002,115,4,
            bytes.fromhex('3001fe00004b')),state)
        self.assertNotIn('npc_health',state)

    @patch.dict('os.environ', {'CRUCIBLE_PRACTICE_DAMAGE': '1',
                             'CRUCIBLE_SHARD_BOOTSTRAP': 'practice-character'})
    def test_clock_repeats_absolute_health_for_packet_loss(self):
        import lz4.block
        state={'character_links_sent':True, 'clock_epoch':100,
               'clock_last_sent':100, 'seq':1, 'received_seq':1,
               'received_window':1, 'practice_npcs_sent':True,
               'npc_health':{0xfe00004b:580.0}}
        for now in (101,102):
            packet=shard.game_clock_reply(state,now=now)
            raw=lz4.block.decompress(packet[1:],uncompressed_size=4096)
            self.assertEqual(int.from_bytes(raw[15:17],'big'),1)
            self.assertEqual(raw[-16:-4],bytes.fromhex('000000004416000044110000'))
