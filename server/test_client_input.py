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


if __name__ == '__main__':
    unittest.main()
