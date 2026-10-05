#!/usr/bin/env python3
"""Install login-started local Crucible services; never launches the game."""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIT_TRANSPORT_ROOT = ROOT / 'dit-transport'
SERVICES = {
    'backend': ('crucible_backend.py', ['--port', '18876'], {}),
    'hub': ('match_stub.py', [], {
        'CRUCIBLE_REPLY': 'hubrpc', 'CRUCIBLE_HUB_SHARDADDR': 'reply',
        'CRUCIBLE_HUB_SHARD_HOST': '127.0.0.1', 'CRUCIBLE_HUB_SHARD_PORT': '18878',
        # Keep the hub's selected map/mode in lockstep with the shard so its
        # baked entity and gamemode indices describe the same bot match.
        'CRUCIBLE_SOLO_MODE': 'alpha-hunters',
    }),
    'shard': ('shard_stub.py', [], {'CRUCIBLE_SHARD_REPLY': 'accept',
                                  'CRUCIBLE_SHARD_SPAWN': '0',
                                  'CRUCIBLE_SOLO_MODE': 'alpha-hunters',
                                  'CRUCIBLE_SOLO_AI': '1',
                                  'CRUCIBLE_SOLO_COMBAT': '1',
                                  'CRUCIBLE_SOLO_FIRE': '1',
                                  # Damage from native client collision reports
                                  # for the three local hunter projectiles.
                                  'CRUCIBLE_SOLO_BOT_HIT_REPORTS': '1',
                                  'CRUCIBLE_SOLO_MOVEMENT_ANIMATION': '1',
                                  'CRUCIBLE_SOLO_COMBAT_ANIMATION': '1',
                                  # Source-backed death metadata probe: the
                                  # baked tables identify 0 as Unknown and
                                  # 51 as marine_primary (docs/27). This may
                                  # trigger the native death callback; live
                                  # visual acceptance is still required.
                                  'CRUCIBLE_SOLO_DEATH_EVENTS': '1',
                                  'CRUCIBLE_SOLO_DEATH_SOURCE_ID': '0',
                                  'CRUCIBLE_SOLO_DEATH_ABILITY_ID': '51',
                                  # PlayerStats.Rank after a result, so a win
                                  # can show victory instead of the 5 s
                                  # defeat fallback (match-flow 01/03).
                                  'CRUCIBLE_SOLO_MATCH_RANK': '1',
                                  # The local DIT transport stores aggregate
                                  # movement only, never positions.
                                  'CRUCIBLE_DIT_TRANSPORT_ROOT': str(DIT_TRANSPORT_ROOT)}),
}


def quote(value):
    # systemd quoted values, including literal percent specifiers.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def main():
    # The units run this interpreter. The shard imports lz4 at startup (see
    # server/requirements.txt), and without it the unit restarts every few
    # seconds forever, as the devbox shard did 51k times from 3 to 5 October.
    try:
        import lz4.block  # noqa: F401
    except ImportError:
        raise SystemExit(f'{sys.executable} cannot import lz4; run this installer '
                         'with an interpreter that has server/requirements.txt')
    # DIT sync-down only writes to configured roots that already exist. Create
    # the root before starting the shard so a hub sync does not depend on the
    # first 60-second motion-observation flush.
    DIT_TRANSPORT_ROOT.mkdir(parents=True, exist_ok=True)
    directory = pathlib.Path.home() / '.config/systemd/user'
    directory.mkdir(parents=True, exist_ok=True)
    names = []
    for name, (script, args, env) in SERVICES.items():
        unit = f'crucible-revival-{name}.service'
        names.append(unit)
        command = ' '.join(quote(v) for v in [sys.executable, ROOT / 'server' / script, *args])
        settings = '\n'.join('Environment=' + quote(k + '=' + v) for k, v in env.items())
        contents = f'''# Managed by Crucible Revival tools/install_local_services.py
[Unit]
Description=Crucible Revival local {name}

[Service]
Type=simple
WorkingDirectory={str(ROOT).replace('%', '%%')}
ExecStart={command}
{settings}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
'''
        destination = directory / unit
        if destination.exists() and not destination.read_text().startswith('# Managed by Crucible Revival'):
            raise SystemExit(f'Refusing to replace unmanaged unit: {destination}')
        destination.write_text(contents)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', *names], check=True)
    print('Enabled local services at login. The game must be launched manually.')


if __name__ == '__main__':
    main()
