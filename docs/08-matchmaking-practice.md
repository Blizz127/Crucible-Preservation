# Matchmaking, practice, and harness findings (Phase 3 progress)

## READY flow, end to end (all verified in-game with screenshots)

`3` (READY) → `SET_READY` → lan `ready` → `localconnection.loadout.set`
→ `data.set lanloadout` → `connecting` → `matchserver.connect` →
outcome modal. Each stage was unblocked in turn:

- **M2** (`tools/patch_menu.py`): `localconnection.loadout.set` is a native
  bridge promise with no offline provider; the invoke src now resolves
  immediately. Before: instant `FAILED_TO_SET_LOADOUT` modal.
- **M3**: extended the offline `localconnection` object with
  `ipAddress:"127.0.0.1", port:18877` (previously undefined), giving
  `matchserver.connect` a concrete target.
- **L1** (`tools/patch_locale.py`): added missing en-US strings for
  `matchmaking.error.failed_to_set_loadout.*` and
  `matchmaking.error.success.*` (retail locale lacks them; raw keys
  rendered otherwise). Locale file is a JSON blob inside
  `ui/dist/ui.loc2.js` (`JSON.parse('...')`); the patcher validates by
  emulating JS single-quote unescaping + `json.loads`.

## Key discovery: match server speaks TLS

With M3, `matchserver.connect` opens **TCP to the LAN endpoint and sends a
TLS 1.2/1.3 ClientHello** (no SNI). Evidence: `/tmp/probe-ms.log`
(`TCP ACCEPT ... TCP 237 bytes: b'\x16\x03\x01...'`).
A second probe completing TLS with a self-signed cert got
`TLSV1_ALERT_INTERNAL_ERROR` — the client aborts on untrusted certs.

Implication: a community game server is conceivable (TLS + unknown app
protocol) but far beyond menu revival: cert trust (Wine root store) +
full protocol reverse engineering. Current behavior with no listener is
the retail-accurate `MATCHMAKING FAILURE / Timeout` modal
(real locale strings).

## Practice arena: not reachable via launch args

- Level assets exist (`levels.pak` practice content, CDL watchers,
  `practice_button_ui`, `practice-background_ui`).
- `steam -applaunch 1057240 +map practicearena`: early crash (367-line
  log, dies before UI init, `+map` never mentioned in log).
- `steam -applaunch 1057240` (no args): boots to menu fine.
- Direct Proton run (no Steam arg mediation): early crash regardless.
- Conclusion: `+map` is toxic in this build and/or applaunch skips needed
  launch options. Practice via CLI is dead; the in-menu practice mode
  button (needs tab clicks to reach) is the remaining path.

## Asset-cache incident (resolved)

SIGTERM-killing the game mid-run corrupted the 13 MB asset cache
(`.../AppData/Local/AGS/Crucible/cache`), after which EVERY launch died
~10 s in at level unload (identical 367-line logs). Fix: delete the cache
dir (regenerable); boots recovered. Lesson: quit via the in-game EXIT
path where possible; if a SIGTERM is needed, be ready to clear the cache.

## Sporadic "quitting normally" exits (open)

Three runs exited via the orderly quit path (`SET NOT READY` + Jn +
`client.quit`, no error) at irregular intervals (38 s – 9 min), while
other runs survived 5–9 min until killed by the operator. No OOM, no
signals in the journal, backend traffic does not correlate, markers
proved nothing (quit happened with and without backend 200s).
Leading theory: the game window steals focus on boot over the user's
active RustDesk session and gets closed by hand. Mitigation: launch only
on request / minimize at boot; ask the user to confirm.
