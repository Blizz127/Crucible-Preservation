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


class FeedbackRowTests(unittest.TestCase):
    def test_feedback_and_reload_rows_appear_only_when_used(self):
        self.assertNotIn('F1', statuses(SHARD, CLIENT))
        shard = SHARD + [
            J % (9, 9) + 'SOLO FEEDBACK sent rpc 115/2 to 01000002 (count 1)',
            J % (9, 9) + 'SOLO FEEDBACK sent rpc 115/2 to 01000002 (count 16)',
            J % (9, 9) + 'SOLO FEEDBACK sent rpc 115/3 to 01000002 (count 1)',
            J % (5, 5) + 'HUNTER AI 01000003 reloading',
            J % (6, 6) + 'HUNTER AI 01000004 reloading']
        rows = {row[0].split()[0]: row for row in score(shard_facts(shard), client_facts(CLIENT))}
        self.assertEqual(rows['F1'][1], 'LOGS-OK')
        self.assertIn('115/2 >=16, 115/3 >=1', rows['F1'][2])
        self.assertIn('client dropped RPCs: 0', rows['F1'][2])
        self.assertEqual(rows['F2'][1:], ('LOGS-OK', rows['F2'][2]))
        self.assertIn('2 Marine reloads', rows['F2'][2])

    def test_a_client_dropped_rpc_fails_the_markers(self):
        client = CLIENT + [C % 9 + '[NET_Rpc] Dropping RPC EntityId=16777218 LocalRole=1']
        shard = SHARD + [J % (9, 9) + 'SOLO FEEDBACK sent rpc 115/2 to 01000002 (count 1)']
        self.assertEqual(statuses(shard, client)['F1'], 'FAIL')



class RingRowTests(unittest.TestCase):
    def test_ring_counts_and_the_edge_asset_crash_marker(self):
        shard = SHARD + [J % (9, 9) + 'SOLO FEEDBACK damage numbers index 4 (count 4)',
                         J % (9, 9) + 'SOLO FEEDBACK hit arcs index 1 (count 1)']
        rows = {row[0].split()[0]: row for row in score(shard_facts(shard), client_facts(CLIENT))}
        self.assertEqual(rows['F3'][1], 'LOGS-OK')
        self.assertIn('damage numbers >=4, hit arcs >=1', rows['F3'][2])
        client = CLIENT + [C % 9 + 'Edge effects asset is null or could not be loaded!']
        self.assertEqual(statuses(shard, client)['F3'], 'FAIL')



class RefusedUpdateRowTests(unittest.TestCase):
    OLD = ('[NET_RepUpdate] EntityReplicationManager: Received old PropertyChangeMessage '
           'message for entity id 16777219, sequence %d latest sequence %d from remote manager id 0')

    def test_refused_updates_are_counted_and_a_wrap_fails(self):
        self.assertNotIn('S1', statuses(SHARD, CLIENT))
        client = CLIENT + [C % 5 + self.OLD % (21, 21), C % 6 + self.OLD % (2369, 2369)]
        rows = {row[0].split()[0]: row for row in score(shard_facts(SHARD), client_facts(client))}
        self.assertEqual(rows['S1'][1], 'INFO')
        self.assertIn('2 entity updates refused as old (sequence 21..2369)', rows['S1'][2])
        wrapped = CLIENT + [C % 7 + self.OLD % (12, 65530)]
        self.assertEqual(statuses(SHARD, wrapped)['S1'], 'FAIL')



class ValveRowTests(unittest.TestCase):
    def test_a_valve_ending_is_named_in_a6(self):
        shard = [line for line in SHARD if 'match over' not in line and 'rank 1' not in line]
        shard += [J % (12, 12) + 'SIMULATION FAILED on 9 of 40 ticks: ending the match with GameOver']
        rows = {row[0].split()[0]: row for row in score(shard_facts(shard), client_facts(CLIENT))}
        self.assertEqual(rows['A6'][1], 'FAIL')
        self.assertIn('forced by a valve', rows['A6'][2])
        self.assertIn('SIMULATION FAILED on 9 of 40 ticks', rows['A6'][2])

    def test_the_match_timer_is_an_accepted_ending(self):
        shard = [line for line in SHARD if 'match over' not in line and 'rank 1' not in line]
        shard.insert(-1, J % (12, 12) + 'MATCH TIMER: 180.0 s of play; ending the match '
                     'with GameOver')
        rows = {row[0].split()[0]: row for row in score(shard_facts(shard), client_facts(CLIENT))}
        self.assertEqual(rows['A6'][1], 'LOGS-OK')
        self.assertIn('match timer after 180 s of play', rows['A6'][2])
        self.assertIn('re-entered 1x after GameOver', rows['A6'][2])


class BotSightRowTests(unittest.TestCase):
    def rows(self, extra):
        shard = SHARD[:1] + [J % (1, 1) + line for line in extra] + SHARD[1:]
        return {row[0].split()[0]: row for row in score(shard_facts(shard), client_facts(CLIENT))}

    def test_the_rows_appear_only_when_used(self):
        rows = self.rows([])
        self.assertFalse({'B0', 'B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8', 'F4'} & set(rows))

    def test_the_hive_row(self):
        rows = self.rows([
            'GUIDE HIVE spawned 01000006 at (1250.0, 1300.0, 30.0)',
            'SOLO COMBAT player hit target=01000006 damage=40 health=4960',
            'GUIDE HIVE regenerating (count 2, health 5000)',
        ])
        self.assertEqual(rows['B5'][1], 'LOGS-OK')
        self.assertIn('(1250.0, 1300.0, 30.0)', rows['B5'][2])
        self.assertIn('1 player hits, lowest health 4960; 2 regenerations', rows['B5'][2])
        rows = self.rows(['GUIDE HIVE spawned 01000006 at (1.0, 2.0, 3.0)',
                          'SOLO COMBAT player hit target=01000006 damage=40 health=0 KILLED',
                          'GUIDE HIVE destroyed at 91.5 s'])
        self.assertIn('destroyed at 91.5 s', rows['B5'][2])
        # The hive is not a hunter: its kill is not A5's.
        self.assertIn('killed 01000003 (1 player hits)', rows['A5'][2])

    def test_the_heart_row_and_its_match_end(self):
        shard = [SHARD[0]] + [J % (1, 1) + line for line in (
            'SOLO POPULATION PROBE: three Marine hunters',
            'GUIDE HEART spawned 01000007 at (1.0, 2.0, 3.0)',
            'GUIDE HEART capture started at 60.0 s',
            'GUIDE HEART captured at 70.0 s; team 1 score 1',
            'GUIDE HEART match over: winner team 1')]
        rows = {row[0].split()[0]: row for row in score(shard_facts(shard), client_facts(CLIENT))}
        self.assertEqual(rows['B6'][1], 'LOGS-OK')
        self.assertIn('captured at 70.0 s; team 1 score 1', rows['B6'][2])
        self.assertIn('ended by the heart', rows['B6'][2])
        self.assertEqual(rows['A6'][1], 'LOGS-OK')
        self.assertIn('winner team 1', rows['A6'][2])

    def test_what_started_the_capture(self):
        base = ['GUIDE HEART spawned 01000007 at (1.0, 2.0, 3.0)']
        done = ['GUIDE HEART captured at 70.0 s; team 1 score 1']
        for start, status in (('(interact)', 'LOGS-OK'), ('(proximity fallback)', 'FAIL'),
                              ('(proximity)', 'INFO')):
            with self.subTest(start=start):
                rows = self.rows(base + ['CLIENT INPUT action Jump rose (count 2)',
                                         'GUIDE HEART capture started at 60.0 s %s' % start]
                                 + done)
                self.assertEqual(rows['B7'][1], status)
                if status == 'FAIL':
                    self.assertIn('Jump >=2', rows['B7'][2])
        rows = self.rows(base + ['GUIDE HEART capture started at 60.0 s (interact)'])
        self.assertNotIn('B7', rows)                       # not captured: no verdict

    def respawn_rows(self, extra):
        # Only the listening line before them: the shared fixture's later
        # player hit would otherwise count as a hit after the revival.
        shard = [SHARD[0]] + [J % (1, 1) + line for line in extra]
        return {row[0].split()[0]: row for row in score(shard_facts(shard), client_facts(CLIENT))}

    def test_the_respawn_row(self):
        died = ['GUIDE RESPAWN player died at 30.0 s; respawn in 10 s',
                'GUIDE RESPAWN wave expired sent at 40.0 s',
                'GUIDE RESPAWN pod landed sent and health restored at 41.0 s (count 1)']
        # Revived, and nothing shows control came back: INFO.
        rows = self.respawn_rows(died + ['GUIDE RESPAWN moved 0.0 m in the 10 s after revival 1'])
        self.assertEqual(rows['B8'][1], 'INFO')
        # The player moved after it: LOGS-OK.
        rows = self.respawn_rows(died + ['GUIDE RESPAWN moved 12.5 m in the 10 s after revival 1'])
        self.assertEqual(rows['B8'][1], 'LOGS-OK')
        self.assertIn('deaths 1, waves sent 1, revivals sent 1', rows['B8'][2])
        # A hit before the revival does not count; one after it does.
        rows = self.respawn_rows(['SOLO COMBAT player hit target=01000004 damage=40 health=760']
                                 + died)
        self.assertEqual(rows['B8'][1], 'INFO')
        rows = self.respawn_rows(['SOLO COMBAT player hit target=01000004 damage=40 health=760']
                                 + died
                                 + ['SOLO COMBAT player hit target=01000004 damage=40 health=720'])
        self.assertEqual(rows['B8'][1], 'LOGS-OK')
        self.assertIn('after a revival: 1 player hits', rows['B8'][2])

    def test_hive_hits_do_not_fail_the_flinch_row(self):
        rows = self.rows(['HIT REACTION on: hunters the player hits flinch (component 80)',
                          'SOLO COMBAT player hit target=01000006 damage=40 health=4960'])
        hunter_hits = [l for l in SHARD if 'player hit target=0100000' in l and '01000006' not in l]
        if not hunter_hits:
            self.assertNotIn('F4', rows)
        else:
            self.assertIn('%d player hits on hunters' % len(hunter_hits), rows['F4'][2])

    def test_flights_are_counted(self):
        rows = self.rows(['HUNTER AI 01000005 fleeing a losing fight (count 2)'])
        self.assertEqual(rows['B4'][1], 'LOGS-OK')
        self.assertIn('01000005 >=2', rows['B4'][2])

    def test_the_switches_line_is_echoed(self):
        rows = self.rows(['HUNTER AI switches: reload strafe idle accel; sight static'])
        self.assertEqual(rows['B0'][1:], ('INFO', 'reload strafe idle accel; sight static'))

    def test_static_sight_blocked_rounds_and_rests(self):
        rows = self.rows([
            'prewarmed line of sight (static) in 3.10s',
            'HUNTER AI rounds blocked by line of sight (count 1)',
            'HUNTER AI rounds blocked by line of sight (count 4)',
            'HUNTER AI 01000003 resting between patrol hops (count 2)',
            'HUNTER AI 01000004 resting between patrol hops (count 1)'])
        self.assertEqual(rows['B1'][1:], ('LOGS-OK', 'static line of sight loaded at prewarm'))
        self.assertIn('>=4 hunter rounds', rows['B2'][2])
        self.assertIn('01000003 >=2, 01000004 >=1', rows['B3'][2])

    def test_flinches_sent_or_switched_on_without_the_split(self):
        rows = self.rows(['HIT REACTION 01000003 index 1 (yaw 0)',
                          'HIT REACTION 01000003 index 4 (yaw -90)'])
        self.assertEqual(rows['F4'][1], 'LOGS-OK')
        self.assertIn('01000003 >=4', rows['F4'][2])
        rows = self.rows(['HIT REACTION disabled: it needs CRUCIBLE_SHARD_SPLIT_UPDATES=1'])
        self.assertEqual(rows['F4'][1], 'FAIL')

    def test_flinches_on_with_hunter_hits_but_none_sent_fails(self):
        # SHARD's player hit kills 01000003, so a run with the switch on and
        # no index sent was a silent no-op.
        rows = self.rows(['HIT REACTION on: hunters the player hits flinch (component 80)'])
        self.assertEqual(rows['F4'][1], 'FAIL')
        self.assertIn('1 player hits on hunters', rows['F4'][2])

    def test_a_lost_sight_test_fails_rather_than_vanishing(self):
        # No prewarm line follows, so without this the row would just be absent.
        rows = self.rows(["LINE OF SIGHT unavailable on april2018: OSError('x')"])
        self.assertEqual(rows['B1'][1], 'FAIL')
        self.assertIn('unavailable on april2018', rows['B1'][2])

    def test_a_static_request_without_a_cache_fails(self):
        rows = self.rows([
            'LINE OF SIGHT: no static geometry cache for april2018 (x); terrain only',
            'prewarmed line of sight (static) in 0.31s'])
        self.assertEqual(rows['B1'][1], 'FAIL')
        self.assertIn('terrain only', rows['B1'][2])


if __name__ == '__main__':
    unittest.main()
