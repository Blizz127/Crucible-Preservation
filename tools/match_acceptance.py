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
  A6 match end  team elimination (or the opt-in match timer) reaches
                GameOver; next READY re-enters
  A7 stable     >= 10 min connected, no shard restart, no traceback

Rows that appear only when their docs/36 switch is used:
  S1 refused    entity updates the client refused as old (a wrap fails)
  F1-F3         hit and kill markers, Marine reloads, damage-number rings
  F4 flinch     the Marines' networked flinch; on without the split fails
  B0 switches   the bot switches and sight mode the shard ran with
  B1 sight      which line of sight loaded; a static request that fell back
                to the terrain fails
  B2 blocked    hunter rounds stopped by line of sight
  B3 rests      the Marines' idle rests between patrol hops
  B4 flee       Marines that fled a losing fight
  B5 hive       HOTH_GUIDE's hive: spawned, damaged, destroyed or regenerating

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
HUNTERS = ('01000003', '01000004', '01000005')   # shard_stub.HUNTER_BOT_IDS
HIVE = '01000006'                                  # shard_stub.HIVE_ENTITY_ID
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
# docs/36 valves that end a match with a no-winner GameOver.
VALVE = re.compile(r'(SIMULATION FAILED on \d+ of \d+ ticks|SEQUENCE LIMIT: LocalSequence \d+ reached \d+)')
RINGS = re.compile(r'SOLO FEEDBACK (damage numbers|hit arcs) index \d+ \(count (\d+)\)')
# docs/37: CRUCIBLE_SOLO_MATCH_MAX_SECONDS, an intended ending (a draw).
TIMER = re.compile(r'MATCH TIMER: ([\d.]+) s of play; ending the match with GameOver')
# docs/36: which line of sight loaded, a static request that fell back to the
# terrain, rounds the line of sight stopped, and the Marines' idle rests
# (the last two logged at counts 1, 2, 4, ...).
SIGHT = re.compile(r'prewarmed line of sight \((terrain|static)\)')
# Logged at each match start when any bot switch or sight mode is on.
SWITCHES = re.compile(r'HUNTER AI switches: (.*)$')
SIGHT_FALLBACK = 'LINE OF SIGHT: no static geometry cache'
# Logged at prewarm and at every match start when the sight test is lost.
SIGHT_BROKEN = re.compile(r'LINE OF SIGHT (unavailable on|static geometry unreadable on) (\S+)')
BLOCKED = re.compile(r'HUNTER AI rounds blocked by line of sight \(count (\d+)\)')
RESTS = re.compile(r'HUNTER AI ([0-9a-f]{8}) resting between patrol hops \(count (\d+)\)')
FLEES = re.compile(r'HUNTER AI ([0-9a-f]{8}) fleeing a losing fight \(count (\d+)\)')
HIVE_SPAWNED = re.compile(r'GUIDE HIVE spawned ([0-9a-f]{8}) at (\([^)]*\))')
HIVE_DESTROYED = re.compile(r'GUIDE HIVE destroyed at ([\d.]+) s')
HIVE_REGEN = re.compile(r'GUIDE HIVE regenerating \(count (\d+)')
# docs/36: the Marines' networked flinch (component 80), logged at indexes
# 1, 2, 4, ..., and the switch left without the split it needs.
FLINCH = re.compile(r'HIT REACTION ([0-9a-f]{8}) index (\d+)')
FLINCH_SPLIT_OFF = 'HIT REACTION disabled'
FLINCH_ON = 'HIT REACTION on:'
JOURNAL_TIME = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)([+-]\d\d):?(\d\d)')

CLIENT_TIME = re.compile(r'^\[(\d\d)-(\d\d)-(\d{4}):(\d\d):(\d\d):(\d\d)\.(\d+)-GMT\]')
UI_PHASES = re.compile(r'"match":\{"phases":(\{[^}]*\})')
ASSERT = re.compile(r'NV_\w*ASSERT')
MULTI_HIT = "There wasn't only one fixup hit"
# ValidateRpcMessage 0x4bfcee0: a refused server RPC, and the variant that
# also drops the connection (docs/36).
DROPPED_RPC = 'Dropping RPC'
# PlayEdgeOfScreenEffect 0xdacd30 logs this and then dereferences the null
# asset, so it marks an imminent crash on the first incoming-hit arc.
EDGE_ASSET_NULL = 'Edge effects asset is null'
# ValidateUpdate 0x4bb5144: a property message refused because its packet
# sequence is not above the entity's latest (docs/36). Without the split each
# Marine draws about 28 a second; with it, about none. A refusal far below the
# latest marks a wrapped u16 sequence.
OLD_UPDATE = re.compile(r'Received old PropertyChangeMessage message for entity id \d+, '
                        r'sequence (\d+) latest sequence (\d+)')
# HybridProjectileSystemClient drops a bad activation with a log line, e.g.
# ValidateNetData "Network data validation failure" (docs/29 5b).
ACTIVATION_REFUSED = re.compile(r'HybridProjectileSystemClient::\w+: .*'
                                r'(validation failure|missing|[Cc]ould not)')


def shard_facts(lines):
    facts = {'population': [], 'input': 0, 'states': {}, 'fire': 0, 'hits': [],
             'refused': {}, 'over': [], 'rank': None, 'starts': [], 'tracebacks': 0,
             'population_after_over': 0, 'feedback': {}, 'reloads': 0, 'rings': {},
             'valves': [], 'timer': [], 'sight': None, 'sight_fallback': 0,
             'sight_broken': [], 'blocked': 0, 'rests': {}, 'flinches': {},
             'flinch_split_off': 0, 'flinch_on': 0, 'switches': None, 'flees': {},
             'hive': None, 'hive_destroyed': None, 'hive_regens': 0}
    for line in lines:
        if VALVE.search(line):
            facts['valves'].append(VALVE.search(line).group(1))
        elif SIGHT.search(line):
            facts['sight'] = SIGHT.search(line).group(1)
        elif SWITCHES.search(line):
            facts['switches'] = SWITCHES.search(line).group(1).strip()
        elif SIGHT_FALLBACK in line:
            facts['sight_fallback'] += 1
        elif SIGHT_BROKEN.search(line):
            facts['sight_broken'].append(' '.join(SIGHT_BROKEN.search(line).groups()))
        elif BLOCKED.search(line):
            facts['blocked'] = max(facts['blocked'], int(BLOCKED.search(line).group(1)))
        elif RESTS.search(line):
            entity, count = RESTS.search(line).groups()
            facts['rests'][entity] = max(facts['rests'].get(entity, 0), int(count))
        elif FLEES.search(line):
            entity, count = FLEES.search(line).groups()
            facts['flees'][entity] = max(facts['flees'].get(entity, 0), int(count))
        elif HIVE_SPAWNED.search(line):
            facts['hive'] = HIVE_SPAWNED.search(line).groups()
        elif HIVE_DESTROYED.search(line):
            facts['hive_destroyed'] = HIVE_DESTROYED.search(line).group(1)
        elif HIVE_REGEN.search(line):
            facts['hive_regens'] = max(facts['hive_regens'], int(HIVE_REGEN.search(line).group(1)))
        elif FLINCH.search(line):
            entity, index = FLINCH.search(line).groups()
            facts['flinches'][entity] = max(facts['flinches'].get(entity, 0), int(index))
        elif FLINCH_SPLIT_OFF in line:
            facts['flinch_split_off'] += 1
        elif FLINCH_ON in line:
            facts['flinch_on'] += 1
        elif TIMER.search(line):
            facts['timer'].append(float(TIMER.search(line).group(1)))
        elif RINGS.search(line):
            kind, count = RINGS.search(line).groups()
            facts['rings'][kind] = max(facts['rings'].get(kind, 0), int(count))
        elif FEEDBACK.search(line):
            component, rpc, count = FEEDBACK.search(line).groups()
            key = '%s/%s' % (component, rpc)
            facts['feedback'][key] = max(facts['feedback'].get(key, 0), int(count))
        elif RELOAD.search(line):
            facts['reloads'] += 1
        elif POPULATION.search(line):
            facts['population'].append(POPULATION.search(line).group(1))
            if facts['over'] or facts['timer']:
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
             'dropped_rpcs': 0, 'edge_asset_null': 0, 'old_updates': 0,
             'old_range': None, 'wrapped_updates': 0, 'last': None}
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
        if EDGE_ASSET_NULL in line:
            facts['edge_asset_null'] += 1
        m = OLD_UPDATE.search(line)
        if m:
            sequence, latest = int(m.group(1)), int(m.group(2))
            facts['old_updates'] += 1
            low, high = facts['old_range'] or (sequence, sequence)
            facts['old_range'] = (min(low, sequence), max(high, sequence))
            if latest - sequence > 30000:
                facts['wrapped_updates'] += 1
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

    kills = [h for h in shard['hits'] if h['shooter'] == PLAYER and h['killed']
             and h['target'] != HIVE]
    player_hits = [h for h in shard['hits'] if h['shooter'] == PLAYER and h['target'] != HIVE]
    if kills:
        rows.append(('A5 kills', 'LOGS-OK', 'killed %s (%d player hits); eyes: visible death'
                     % (' '.join(sorted({h['target'] for h in kills})), len(player_hits))))
    else:
        rows.append(('A5 kills', 'FAIL', '%d player hits, no kill; refused player reports: %s'
                     % (len(player_hits), refused_summary(shard['refused'], {PLAYER}))))

    if shard['over'] or shard['timer']:
        evidence = ('winner team %s' % shard['over'][0] if shard['over']
                    else 'match timer after %.0f s of play (a draw)' % shard['timer'][0])
        if shard['rank'] is not None:
            evidence += ', rank %d sent' % shard['rank']
        if client is not None:
            evidence += '; client UI phases %s' % ' > '.join(client['phases'][-3:])
        evidence += ('; re-entered %dx after GameOver' % shard['population_after_over']
                     if shard['population_after_over'] else '; next READY not seen')
        rows.append(('A6 match end', 'LOGS-OK', evidence + '; eyes: end screen, home'))
    elif shard['valves']:
        rows.append(('A6 match end', 'FAIL', 'GameOver forced by a valve, not by the match: %s'
                     % shard['valves'][0]))
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

    if client is not None and (client['old_updates'] or client['wrapped_updates']):
        low, high = client['old_range']
        rows.append(('S1 refused', 'FAIL' if client['wrapped_updates'] else 'INFO',
                     '%d entity updates refused as old (sequence %d..%d)%s; expected about '
                     '28/s per Marine without CRUCIBLE_SHARD_SPLIT_UPDATES and about 0 with it'
                     % (client['old_updates'], low, high,
                        '; %d after a sequence wrap' % client['wrapped_updates']
                        if client['wrapped_updates'] else '')))

    # Opt-in docs/36 features: reported only when the shard used them.
    if shard['feedback'] or (client is not None and client['dropped_rpcs']):
        dropped = client['dropped_rpcs'] if client is not None else None
        sent = ', '.join('%s >=%d' % item for item in sorted(shard['feedback'].items()))
        rows.append(('F1 markers', 'FAIL' if dropped else 'LOGS-OK',
                     'sent %s; client dropped RPCs: %s; eyes: hit and kill markers'
                     % (sent or 'none', 'unknown' if dropped is None else dropped)))
    if shard['rings'] or (client is not None and client['edge_asset_null']):
        broken = client['edge_asset_null'] if client is not None else 0
        counts = ', '.join('%s >=%d' % item for item in sorted(shard['rings'].items()))
        rows.append(('F3 rings', 'FAIL' if broken else 'LOGS-OK',
                     'wrote %s%s; a hang shows in A7 as a client log that ends while '
                     'Connected; eyes: numbers over Marines, arcs toward the shooter'
                     % (counts or 'none', '; client edge-effects asset null %dx' % broken
                        if broken else '')))
    if shard['reloads']:
        rows.append(('F2 reloads', 'LOGS-OK', '%d Marine reloads; eyes: reload animation, '
                     'about 2 s without fire' % shard['reloads']))
    if shard['flinch_split_off']:
        rows.append(('F4 flinch', 'FAIL', 'hit reactions are on without '
                     'CRUCIBLE_SHARD_SPLIT_UPDATES=1, so none were sent'))
    elif shard['flinches']:
        rows.append(('F4 flinch', 'LOGS-OK', 'flinch index sent %s; eyes: a Marine flinches '
                     'away from the shot' % ', '.join(
                         '%s >=%d' % item for item in sorted(shard['flinches'].items()))))
    elif shard['flinch_on']:
        hunter_hits = [h for h in shard['hits'] if h['shooter'] == PLAYER
                       and h['target'] in HUNTERS]
        if hunter_hits:
            rows.append(('F4 flinch', 'FAIL', 'hit reactions on and %d player hits on hunters '
                         'accepted, but no flinch was sent (no decoded player position?)'
                         % len(hunter_hits)))
    if shard['switches']:
        rows.append(('B0 switches', 'INFO', shard['switches']))
    if shard['sight_broken']:
        rows.append(('B1 sight', 'FAIL', 'line of sight requested but lost: %s'
                     % shard['sight_broken'][0]))
    elif shard['sight_fallback']:
        rows.append(('B1 sight', 'FAIL', 'static sight requested, but there is no cache: '
                     'terrain only (%dx); build it with tools/build_static_geometry.py'
                     % shard['sight_fallback']))
    elif shard['sight']:
        rows.append(('B1 sight', 'LOGS-OK', '%s line of sight loaded at prewarm' % shard['sight']))
    if shard['blocked']:
        rows.append(('B2 blocked', 'LOGS-OK', '>=%d hunter rounds stopped by line of sight; '
                     'eyes: no damage through cover' % shard['blocked']))
    if shard['hive']:
        entity, where = shard['hive']
        hits = [h for h in shard['hits'] if h['target'] == entity]
        state = ('destroyed at %s s' % shard['hive_destroyed'] if shard['hive_destroyed']
                 else 'lowest health %d' % min(h['health'] for h in hits) if hits
                 else 'never hit')
        rows.append(('B5 hive', 'LOGS-OK', 'spawned at %s; %d player hits, %s; %d regenerations; '
                     'eyes: a hive about 30 m from the drop, which may look dormant (no '
                     'marker, wasps, heart or death animation)'
                     % (where, len(hits), state, shard['hive_regens'])))
    if shard['flees']:
        rows.append(('B4 flee', 'LOGS-OK', 'flights %s; eyes: a losing Marine finishes its '
                     'burst (up to 2 s), runs about 4 s, then comes back' % ', '.join(
                         '%s >=%d' % item for item in sorted(shard['flees'].items()))))
    if shard['rests']:
        rows.append(('B3 rests', 'LOGS-OK', 'idle rests %s; eyes: an idle animation between '
                     'patrol hops' % ', '.join('%s >=%d' % item
                                               for item in sorted(shard['rests'].items()))))
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
