#!/usr/bin/env python3
"""Score one live solo Alpha Hunters match against acceptance criteria A1-A7.

The input is the shard's own log lines, from the managed unit's journal or
`server/shard_udp.log`, plus the client's NovaGameClient log if available.
For each criterion the script prints one of:
- PASS: the logs prove it;
- LOGS-OK: the server side happened, but the player still has to confirm
  what was drawn;
- FAIL;
- NO-DATA.
Logs record the server's decisions and the client's state changes. They
cannot show that a model, a projectile or a death animation appeared on
screen, so those criteria stop at LOGS-OK.

  A1 entry      READY reaches GamePlay; population spawned, input decoded
  A2 bots move  hunter AI changes state (visible/animated/grounded: eyes)
  A3 bots fire  hunter AI fires (shot animation and projectile: eyes)
  A4 damaged    a hunter hit on the player is accepted (HUD drop: eyes)
  A5 kills      a player hit kills a hunter (visible death: eyes)
  A6 match end  team elimination reaches GameOver; next READY re-enters
  A7 stable     >= 10 min connected, no shard restart, no traceback

The script only reads files and uses only the standard library, so it can
run on the game host over SSH, reading that host's journal and newest
client log:

  ssh HOST 'python3 - --journal-since "2026-10-05 18:00" --client latest' \\
      < tools/match_acceptance.py

or locally on saved copies:

  python3 tools/match_acceptance.py --shard shard.txt --client client.log
"""
import argparse
import datetime
import glob
import os
import re
import subprocess
import sys

CLIENT_LOGS = os.path.expanduser(
    '~/.local/share/Steam/steamapps/compatdata/1057240/pfx/drive_c/users/'
    'steamuser/AppData/Local/AGS/Crucible/NovaNetLogs')
PLAYER = '01000002'
MIN_CONNECTED_SECONDS = 600

POPULATION = re.compile(r'SOLO POPULATION PROBE: (.*)')
INPUT = re.compile(r'CLIENT INPUT decoded: player at')
STATE = re.compile(r'HUNTER AI ([0-9a-f]{8}) -> (\w+)')
FIRE = re.compile(r'HUNTER AI ([0-9a-f]{8}) fired at ([0-9a-f]{8})')
HIT = re.compile(r'SOLO COMBAT (player|hunter ([0-9a-f]{8})) hit target=([0-9a-f]{8})'
                 r' damage=(\d+) health=(\d+)( KILLED)?')
REFUSED = re.compile(r'SOLO COMBAT report refused shooter=([0-9a-f]{8})'
                     r' \(([^,]+), count (\d+)\)')
OVER = re.compile(r'ALPHA HUNTERS match over: winner team (\w+)')
RANK = re.compile(r'ALPHA HUNTERS rank (\d+) published')
SHARD_START = re.compile(r'Started crucible-revival-shard|listening on \S+:18878')
TRACEBACK = re.compile(r'Traceback \(most recent call last\)')
# docs/36: feedback RPCs the shard sent (logged at counts 1, 2, 4, ...) and
# Marine reloads.
FEEDBACK = re.compile(r'SOLO FEEDBACK sent rpc (\d+)/(\d+) to [0-9a-f]{8} \(count (\d+)\)')
RELOAD = re.compile(r'HUNTER AI ([0-9a-f]{8}) reloading')
JOURNAL_TIME = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)([+-]\d\d):?(\d\d)')

CLIENT_TIME = re.compile(r'^\[(\d\d)-(\d\d)-(\d{4}):(\d\d):(\d\d):(\d\d)\.(\d+)-GMT\]')
UI_PHASES = re.compile(r'"match":\{"phases":(\{[^}]*\})')
ASSERT = re.compile(r'NV_\w*ASSERT')
MULTI_HIT = "There wasn't only one fixup hit"
# ValidateRpcMessage 0x4bfcee0: a refused server RPC, and the variant that
# also drops the connection (docs/36).
DROPPED_RPC = 'Dropping RPC'
# HybridProjectileSystemClient drops a bad activation with a log line, e.g.
# ValidateNetData "Network data validation failure" (docs/29 5b).
ACTIVATION_REFUSED = re.compile(r'HybridProjectileSystemClient::\w+: .*'
                                r'(validation failure|missing|[Cc]ould not)')


def shard_facts(lines):
    facts = {'population': [], 'input': 0, 'states': {}, 'fire': 0, 'hits': [],
             'refused': {}, 'over': [], 'rank': None, 'starts': [], 'tracebacks': 0,
             'population_after_over': 0, 'feedback': {}, 'reloads': 0}
    for line in lines:
        if FEEDBACK.search(line):
            component, rpc, count = FEEDBACK.search(line).groups()
            key = '%s/%s' % (component, rpc)
            facts['feedback'][key] = max(facts['feedback'].get(key, 0), int(count))
        elif RELOAD.search(line):
            facts['reloads'] += 1
        elif POPULATION.search(line):
            facts['population'].append(POPULATION.search(line).group(1))
            if facts['over']:
                facts['population_after_over'] += 1
        elif INPUT.search(line):
            facts['input'] += 1
        elif FIRE.search(line):
            facts['fire'] += 1
        elif STATE.search(line):
            entity, state = STATE.search(line).groups()
            facts['states'].setdefault(entity, set()).add(state)
        elif HIT.search(line):
            m = HIT.search(line)
            facts['hits'].append({'shooter': m.group(2) or PLAYER, 'target': m.group(3),
                                  'health': int(m.group(5)), 'killed': bool(m.group(6))})
        elif REFUSED.search(line):
            shooter, reason, count = REFUSED.search(line).groups()
            key = (shooter, reason)
            # Logged at counts 1, 2, 4, ...: the last one is a lower bound.
            facts['refused'][key] = max(facts['refused'].get(key, 0), int(count))
        elif OVER.search(line):
            facts['over'].append(OVER.search(line).group(1))
        elif RANK.search(line):
            facts['rank'] = int(RANK.search(line).group(1))
        if SHARD_START.search(line):
            facts['starts'].append(journal_time(line))
        if TRACEBACK.search(line):
            facts['tracebacks'] += 1
    return facts


def journal_time(line):
    """UTC time of a `journalctl -o short-iso` line, else None."""
    m = JOURNAL_TIME.match(line)
    if not m:
        return None
    stamp, hours, minutes = m.groups()
    offset = datetime.timedelta(hours=int(hours), minutes=int(hours[0] + minutes))
    return datetime.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%S') - offset


def client_time(line):
    m = CLIENT_TIME.match(line)
    if not m:
        return None
    month, day, year, hour, minute, second, fraction = m.groups()
    return datetime.datetime(int(year), int(month), int(day), int(hour), int(minute),
                             int(second), int(fraction[:6].ljust(6, '0')))


def client_facts(lines):
    facts = {'connected': None, 'disconnecting': None, 'match_exit': False,
             'phases': [], 'asserts': 0, 'multi_hit': 0, 'refused_activations': 0,
             'dropped_rpcs': 0, 'last': None}
    for line in lines:
        when = client_time(line)
        if when is not None:
            facts['last'] = when
        if 'from ConnectingToServerShard to Connected' in line and not facts['connected']:
            facts['connected'] = when
        elif 'from Connected to Disconnecting' in line and not facts['disconnecting']:
            facts['disconnecting'] = when
        elif 'Client disconnected on purpose from MatchExit' in line:
            facts['match_exit'] = True
        m = UI_PHASES.search(line)
        if m and (not facts['phases'] or facts['phases'][-1] != m.group(1)):
            facts['phases'].append(m.group(1))
        if ASSERT.search(line):
            facts['asserts'] += 1
        if MULTI_HIT in line:
            facts['multi_hit'] += 1
        if ACTIVATION_REFUSED.search(line):
            facts['refused_activations'] += 1
        if DROPPED_RPC in line:
            facts['dropped_rpcs'] += 1
    return facts


def refused_summary(refused, shooters):
    parts = ['%s %s >=%d' % (shooter, reason, count)
             for (shooter, reason), count in sorted(refused.items())
             if shooter in shooters]
    return '; '.join(parts) if parts else 'none'


def score(shard, client):
    """[(criterion, status, evidence)] for one match's facts."""
    rows = []
    entered = bool(shard['population'] and shard['input'])
    if client is not None and not client['connected']:
        rows.append(('A1 entry', 'FAIL', 'client never reached Connected'))
    elif entered:
        rows.append(('A1 entry', 'PASS', 'population: %s; input decoded %dx%s' % (
            shard['population'][0], shard['input'],
            '; client Connected %sZ' % client['connected'].time() if client else '')))
    else:
        rows.append(('A1 entry', 'NO-DATA' if not shard['population'] else 'FAIL',
                     'population %d, input %d' % (len(shard['population']), shard['input'])))

    states = sorted({s for per in shard['states'].values() for s in per})
    rows.append(('A2 bots move', 'LOGS-OK' if states else 'FAIL',
                 'states %s for %d hunters; eyes: visible, animated, grounded, apart'
                 % ('/'.join(states) or 'none', len(shard['states']))))
    refused_fire = client['refused_activations'] if client is not None else 0
    fire_ok = shard['fire'] and not refused_fire
    rows.append(('A3 bots fire', 'LOGS-OK' if fire_ok else 'FAIL',
                 '%d fire decisions%s; eyes: shot animation and projectile' % (
                     shard['fire'], '; client refused %d activations' % refused_fire
                     if refused_fire else '')))

    on_player = [h for h in shard['hits'] if h['shooter'] != PLAYER and h['target'] == PLAYER]
    if on_player:
        rows.append(('A4 damaged', 'PASS', '%d accepted hunter hits, lowest health %d;'
                     ' eyes: HUD drop' % (len(on_player), min(h['health'] for h in on_player))))
    else:
        # Every non-player shooter, including ids the shard did not register.
        refused = refused_summary(shard['refused'],
                                  {s for s, _ in shard['refused'] if s != PLAYER})
        rows.append(('A4 damaged', 'FAIL', 'no accepted hunter hit; refused hunter reports: %s%s'
                     % (refused, '' if refused != 'none'
                        else ' (client may not report remote projectiles)')))

    kills = [h for h in shard['hits'] if h['shooter'] == PLAYER and h['killed']]
    player_hits = [h for h in shard['hits'] if h['shooter'] == PLAYER]
    if kills:
        rows.append(('A5 kills', 'LOGS-OK', 'killed %s (%d player hits); eyes: visible death'
                     % (' '.join(sorted({h['target'] for h in kills})), len(player_hits))))
    else:
        rows.append(('A5 kills', 'FAIL', '%d player hits, no kill; refused player reports: %s'
                     % (len(player_hits), refused_summary(shard['refused'], {PLAYER}))))

    if shard['over']:
        evidence = 'winner team %s' % shard['over'][0]
        if shard['rank'] is not None:
            evidence += ', rank %d sent' % shard['rank']
        if client is not None:
            evidence += '; client UI phases %s' % ' > '.join(client['phases'][-3:])
        evidence += ('; re-entered %dx after GameOver' % shard['population_after_over']
                     if shard['population_after_over'] else '; next READY not seen')
        rows.append(('A6 match end', 'LOGS-OK', evidence + '; eyes: end screen, home'))
    else:
        rows.append(('A6 match end', 'FAIL', 'no GameOver%s' % (
            '; client left via MatchExit' if client and client['match_exit'] else '')))

    problems = []
    if shard['tracebacks']:
        problems.append('%d shard tracebacks' % shard['tracebacks'])
    seconds = None
    if client is not None and client['connected']:
        end = client['disconnecting'] or client['last']
        seconds = (end - client['connected']).total_seconds()
        # Shard starts between matches are routine; inside one they are not.
        restarts = [t for t in shard['starts'] if t and client['connected'] <= t <= end]
        if restarts:
            problems.append('shard started mid-match at %sZ' % restarts[0].time())
        if seconds < MIN_CONNECTED_SECONDS and not shard['over']:
            problems.append('connected only %.0f s' % seconds)
        if client['disconnecting'] is None:
            problems.append('client log ends while Connected')
    notes = []
    if client is not None:
        notes.append('%d network asserts' % client['asserts'])
        if client['multi_hit']:
            notes.append('%d multi-target projectile warnings' % client['multi_hit'])
    evidence = '; '.join(problems + notes + (
        ['connected %.0f s' % seconds] if seconds is not None else []))
    if client is None and not problems:
        rows.append(('A7 stable', 'NO-DATA', 'needs the client log'))
    else:
        rows.append(('A7 stable', 'FAIL' if problems else 'PASS', evidence))

    # Opt-in docs/36 features: reported only when the shard used them.
    if shard['feedback'] or (client is not None and client['dropped_rpcs']):
        dropped = client['dropped_rpcs'] if client is not None else None
        sent = ', '.join('%s >=%d' % item for item in sorted(shard['feedback'].items()))
        rows.append(('F1 markers', 'FAIL' if dropped else 'LOGS-OK',
                     'sent %s; client dropped RPCs: %s; eyes: hit and kill markers'
                     % (sent or 'none', 'unknown' if dropped is None else dropped)))
    if shard['reloads']:
        rows.append(('F2 reloads', 'LOGS-OK', '%d Marine reloads; eyes: reload animation, '
                     'about 2 s without fire' % shard['reloads']))
    return rows


def read_lines(path):
    if path == '-':
        return sys.stdin.read().splitlines()
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read().splitlines()


def latest_client_log(directory=CLIENT_LOGS):
    logs = glob.glob(os.path.join(directory, 'NovaGameClient-*.log'))
    if not logs:
        raise SystemExit('no NovaGameClient log in %s' % directory)
    return max(logs, key=os.path.getmtime)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--shard', help="shard log lines: a file, or '-' for stdin")
    source.add_argument('--journal-since', metavar='TIME',
                        help='read the managed shard unit journal since TIME')
    parser.add_argument('--unit', default='crucible-revival-shard')
    parser.add_argument('--client', help="NovaGameClient log, a log directory, or 'latest'")
    args = parser.parse_args()

    if args.journal_since:
        lines = subprocess.run(
            ['journalctl', '--user', '-u', args.unit, '--since', args.journal_since,
             '-o', 'short-iso', '--no-pager'], check=True, capture_output=True,
            text=True).stdout.splitlines()
    else:
        lines = read_lines(args.shard)
    client = None
    if args.client:
        path = args.client
        if path == 'latest':
            path = latest_client_log()
        elif os.path.isdir(path):
            path = latest_client_log(path)
        print('client log: %s' % path)
        client = client_facts(read_lines(path))
    print('shard lines: %d' % len(lines))
    for criterion, status, evidence in score(shard_facts(lines), client):
        print('%-13s %-8s %s' % (criterion, status, evidence))


if __name__ == '__main__':
    main()
