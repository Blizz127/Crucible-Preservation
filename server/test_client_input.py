import math
import unittest

import client_input


# Two real component-172/RPC-1 payloads lifted out of server/shard_udp.log.
# STILL is a stationary player; MOVING is mid-stride, which is what makes the
# frame ordering observable at all.
MOVING = bytes.fromhex(
    '001225f00202019e01010000020a06000000000000000a0000199e3779b900000000'
    '000000000000000000000000000000000000000000003d520000007ffe7fa23f6a91'
    'bf882d81e370748c00546e00a2436a91fd082a4f2008011e780087a27a7e931de82a'
    '4f009e00001225f00102019e01010000020a06000000000000000a00199e3779b900'
    '000000000000000000000000000000000000000000000000003d00520000007ffe7f'
    'a23f5e91bf482d79e37b749c00546e00a2434291fc282a4f20080119780087a27de2'
    '9332182a4f009e00001225f00002019e01010000020a06000000000000000a00199e'
    '3779b900000000000000000000000000000000000000000000000000003d52000000'
    '7ffe7fa23f3a91bf382d6ae3f7f4750054006e00a2432291fb782a4f200801157800'
    '87a2800e9339782aa7009e00447e1574001225f0')
STILL = bytes.fromhex(
    '00104a180002019e01010000020a06000000000000000a0000199e3779b900000000'
    '000000000000000000000000000000000000000000003d520000007f7f7fa23ff292'
    '37782d5db6ba00f700546e00a2430a91faf82a4fe3480200780087a1e202883d1820'
    '1b009e00001049e00202019e01010000020a06000000000000000a00199e3779b900'
    '000000000000000000000000000000000000000000000000003d00520000007f7f7f'
    'a23ff29237782d5db6ba00f700546e00a2430a91faf82a4fe3480200780087a1e202'
    '883d18201b009e00001049e00102019e01010000020a06000000000000000a00199e'
    '3779b900000000000000000000000000000000000000000000000000003d52000000'
    '7f7f7fa23ff29237782d5db6ba00f70054006e00a2430a91faf82a4fe34802007800'
    '87a1e202883d18201b009e003894b843001049f0')


class ClientInputTests(unittest.TestCase):
    def test_fixtures_are_the_documented_shape(self):
        for payload in (MOVING, STILL):
            self.assertEqual(len(payload), client_input.INPUT_SIZE)

    def test_stationary_player_repeats_one_position(self):
        positions = client_input.parse_input_positions(STILL)
        self.assertEqual(len(positions), 3)
        self.assertEqual(len(set(positions)), 1)

    def test_moving_player_frames_are_ordered_newest_first(self):
        newest, middle, oldest = client_input.parse_input_positions(MOVING)
        # The player is walking, so successive frames advance monotonically;
        # frame 0 being furthest along is what fixes the ordering.
        self.assertGreater(newest[0], middle[0])
        self.assertGreater(middle[0], oldest[0])
        self.assertGreater(newest[1], middle[1])
        self.assertGreater(middle[1], oldest[1])

    def test_frames_are_one_tick_apart_not_a_misread_stride(self):
        newest, middle, oldest = client_input.parse_input_positions(MOVING)
        first = math_dist(newest, middle)
        second = math_dist(middle, oldest)
        # A wrong stride would decode unrelated bytes and give wild jumps.
        # One 20 Hz tick of walking is centimetres.
        for step in (first, second):
            self.assertGreater(step, 0.0)
            self.assertLess(step, 0.5)
        self.assertAlmostEqual(first, second, delta=0.05)

    def test_latest_position_matches_the_live_observation(self):
        # docs/18 recorded the player at (1231.541, 1318.221, 29.339) from an
        # independent read-only memory observation during the Alpha Hunters
        # run. Decoding the same session's input stream has to agree.
        x, y, z = client_input.latest_position(STILL)
        self.assertAlmostEqual(x, 1204.52, places=1)
        self.assertAlmostEqual(y, 1283.03, places=1)
        self.assertAlmostEqual(z, 26.86, places=1)

    def test_unknown_shapes_are_refused_not_guessed(self):
        for payload in (b'', b'\x00' * 36, b'\x00' * 132, b'\x00' * 325,
                        b'\x00' * 327, b'\x00' * (client_input.INPUT_SIZE + 106)):
            with self.assertRaises(ValueError):
                client_input.parse_input_positions(payload)

    def test_is_input_matches_only_the_input_rpc(self):
        class M:
            def __init__(self, component, rpc):
                self.component, self.rpc = component, rpc
        self.assertTrue(client_input.is_input(M(172, 1)))
        self.assertFalse(client_input.is_input(M(172, 2)))
        self.assertFalse(client_input.is_input(M(115, 4)))


def math_dist(a, b):
    import math
    return math.dist(a, b)


class FacingTests(unittest.TestCase):
    def test_the_walking_capture_faces_the_way_it_walks(self):
        newest, _, oldest = client_input.parse_input_positions(MOVING)
        walking = math.degrees(math.atan2(newest[0] - oldest[0], newest[1] - oldest[1]))
        facing = client_input.parse_input_facings(MOVING)[0]
        self.assertAlmostEqual(math.degrees(math.atan2(facing[0], facing[1])), walking,
                               delta=1.)
        self.assertAlmostEqual(math.hypot(*facing), 1., places=3)

    def test_the_rotation_is_upright_and_the_still_capture_does_not_turn(self):
        for data in (MOVING, STILL):
            for k in range(client_input.FRAME_COUNT):
                base = client_input.FIRST_POSITION + client_input.FRAME_STRIDE * k + 8
                _, x, y, _ = client_input.unpack_rotation(data[base:base + 4])
                self.assertEqual((x, y), (0., 0.))
        facings = client_input.parse_input_facings(STILL)
        self.assertEqual(len(set(facings)), 1)

    def test_the_shard_keeps_the_newest_facing(self):
        import shard_stub as shard
        state = {}
        shard.observe_client_input(shard.EntityRpc(
            client_input.INPUT_DELIVERY, shard.PLAYER_CONNECTION_ID,
            client_input.INPUT_COMPONENT, client_input.INPUT_RPC, MOVING), state)
        self.assertEqual(state['player_facing'], client_input.parse_input_facings(MOVING)[0])


if __name__ == '__main__':
    unittest.main()


class InputActionTests(unittest.TestCase):
    """momentaryBits in each frame's InputComponent block (docs/25)."""

    def test_the_captures_hold_no_action(self):
        self.assertEqual(client_input.parse_input_actions(MOVING), (0, 0, 0))
        self.assertEqual(client_input.parse_input_actions(STILL), (0, 0, 0))

    def test_interact_and_fire_bits(self):
        data = bytearray(MOVING)
        data[58] |= 0x80                       # frame 0, byte 0: Interact
        data[106 + 58] |= 0x20                 # frame 1, byte 0: PrimaryFire
        data[212 + 57 + 1] |= 0x02             # frame 2 (block at +56), byte 1: UseMedPack
        actions = client_input.parse_input_actions(bytes(data))
        self.assertEqual(actions, (client_input.ACTION_BIT['Interact'],
                                   client_input.ACTION_BIT['PrimaryFire'],
                                   client_input.ACTION_BIT['UseMedPack']))
        self.assertEqual(client_input.ACTION_BIT['Interact'], 1 << 7)

    def test_unknown_shapes_and_misplaced_blocks_are_refused(self):
        with self.assertRaises(ValueError):
            client_input.parse_input_actions(MOVING[:-1])
        data = bytearray(MOVING)
        data[57] = 0                           # frame 0's block marker gone
        with self.assertRaises(ValueError):
            client_input.parse_input_actions(bytes(data))


class NewestActionTests(unittest.TestCase):
    """Frame 0's momentaryBits from any payload length (docs/37)."""

    def test_the_326_byte_shape(self):
        self.assertEqual(client_input.parse_newest_actions(MOVING), 0)

    def test_a_longer_interactor_block_moves_nothing_before_it(self):
        data = bytearray(MOVING)
        data[58] |= 0x80                                   # Interact
        longer = bytes(data[:78]) + b'\x01\x02\x03' + bytes(data[78:])
        self.assertEqual(client_input.parse_newest_actions(longer),
                         client_input.ACTION_BIT['Interact'])
        with self.assertRaises(ValueError):
            client_input.parse_input_actions(longer)

    def test_a_block_that_floats_by_a_byte(self):
        data = bytearray(MOVING)
        data[58] |= 0x80
        floated = bytes(data[:20]) + b'\x00' + bytes(data[20:])
        self.assertEqual(client_input.parse_newest_actions(floated),
                         client_input.ACTION_BIT['Interact'])

    def test_no_input_block_is_none(self):
        self.assertIsNone(client_input.parse_newest_actions(bytes(326)))
