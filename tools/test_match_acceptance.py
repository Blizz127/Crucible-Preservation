import datetime
import unittest

from match_acceptance import client_facts, journal_time, score, shard_facts

J = '2026-10-05T18:%02d:00-05:00 bazzite python3[9]: [2026-10-05T18:%02d:00.000]       '
SHARD = [
    J % (0, 0) + 'listening on 127.0.0.1:18878 (UDP, log: shard_udp.log)',
    J % (2, 2) + 'SOLO POPULATION PROBE: three Marine hunters',
    J % (2, 2) + 'CLIENT INPUT decoded: player at (1.00, 2.00, 3.00)',
    J % (2, 2) + 'HUNTER AI 01000003 -> pursue',
    J % (3, 3) + 'HUNTER AI 01000003 fired at 01000002 (range 14.1 m)',
    J % (3, 3) + 'SOLO COMBAT report refused shooter=01000004 (no live enemy, count 4): x',
    J % (4, 4) + 'SOLO COMBAT hunter 01000003 hit target=01000002 damage=25 health=775',
    J % (9, 9) + 'SOLO COMBAT player hit target=01000003 damage=40 health=0 KILLED',
    J % (12, 12) + 'ALPHA HUNTERS match over: winner team 1',
    J % (12, 12) + 'ALPHA HUNTERS rank 1 published (PlayerStats.Rank)',
    J % (15, 15) + 'SOLO POPULATION PROBE: three Marine hunters',
]
C = '[10-05-2026:23:%02d:00.000000-GMT] '
CLIENT = [
    C % 2 + 'GameState(): Client is transitioning state from ConnectingToServerShard to Connected',
    C % 3 + 'ConsoleAPI: {"match":{"phases":{"playing":"shooting"}}}',
    C % 12 + 'ConsoleAPI: {"match":{"phases":{"gameover":"results"}}}',
    C % 13 + 'GameState(): Client is transitioning state from Connected to Disconnecting',
]


def statuses(shard, client):
    return {row[0].split()[0]: row[1] for row in score(shard_facts(shard), client_facts(client))}


class MatchAcceptanceTests(unittest.TestCase):
    def test_journal_times_are_converted_to_utc(self):
        self.assertEqual(journal_time('2026-10-03T17:01:36-05:00 bazzite x'),
                         datetime.datetime(2026, 10, 3, 22, 1, 36))
        self.assertIsNone(journal_time('[2026-10-03T17:01:36.598] listening'))

    def test_complete_match_proves_logs_and_leaves_visuals_to_the_player(self):
        self.assertEqual(statuses(SHARD, CLIENT), {
            'A1': 'PASS', 'A2': 'LOGS-OK', 'A3': 'LOGS-OK', 'A4': 'PASS',
            'A5': 'LOGS-OK', 'A6': 'LOGS-OK', 'A7': 'PASS'})
        a6 = score(shard_facts(SHARD), client_facts(CLIENT))[5][2]
        self.assertIn('re-entered 1x after GameOver', a6)
        self.assertIn('winner team 1, rank 1 sent', a6)

    def test_refused_hunter_reports_explain_missing_damage(self):
        shard = [line for line in SHARD if 'hunter 01000003 hit' not in line]
        rows = score(shard_facts(shard), client_facts(CLIENT))
        self.assertEqual(rows[3][1], 'FAIL')
        self.assertIn('01000004 no live enemy >=4', rows[3][2])

    def test_client_refused_activations_fail_bot_fire(self):
        client = CLIENT + [C % 4 + 'HybridProjectileSystemClient::ValidateNetData: Entity id '
                           '(16777219), name (x) -> Network data validation failure. ...']
        rows = score(shard_facts(SHARD), client_facts(client))
        self.assertEqual(rows[2][1], 'FAIL')
        self.assertIn('client refused 1 activations', rows[2][2])

    def test_shard_start_inside_the_connected_window_fails_stability(self):
        shard = SHARD + [J % (5, 5) + 'listening on 127.0.0.1:18878 (UDP, log: x)']
        rows = score(shard_facts(shard), client_facts(CLIENT))
        self.assertEqual(rows[6][1], 'FAIL')
        self.assertIn('shard started mid-match at 23:05:00Z', rows[6][2])


if __name__ == '__main__':
    unittest.main()
