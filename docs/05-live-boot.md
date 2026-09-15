# Live boot (host, GE-Proton) — soft-locked at login

Evidence: `snapshots/host-boot-20260914-geproton/` (client log + prefs).

## Timeline (CDT)

- 22:37 Steam first-time setup (vcredist x86/x64) into prefix
  `compatdata/1057240`.
- 22:38:08 client starts (`executableVersion: 5.3.1515.1132017`), loads
  `system_windows_pc.cfg`, builds D3D11 shader cache under
  `AppData/Local/AGS/Crucible/`.
- 22:38:26 UI up (Coherent GT 2.9.4.0, 1680x1050 windowed), Vivox
  initialized, analytics warns "Could not connect to Live Services!".
- 22:38:28 `PlayerAuthorizerCall` attempts 1–3/3, each failing in ~15–35 ms
  at `[AWS] WinHttpSyncHttpClient - Send request failed`, final:
  "Error authenticating player with PlayerAuthorizer."
- 22:38:28–38 UI boot events: `locale → environment(lan/booted) →
  intros(warning, logos) → {"servicesBootup":"login"}`.
- 22:38:38 log freezes; no file activity anywhere (shaders included) for
  6+ minutes. Screen: static logo loading screen, no error, no login form.

## Diagnosis

Soft-lock in the login state, not a crash (CrashDB `metadata` empty,
`reports/420.txt` is a bare report-ID stub). UI is an XState chart
(`menu.js`: `booted:{initial:"checking"...}`, targets `everyboot`,
`servicesBootup`, `welcome`); the chart reached `servicesBootup.login`,
which presumably awaits an auth promise that the failed PlayerAuthorizer
calls never resolve — so the login form never renders and no retry fires.

## 00:07 screenshot

Second capture (`snapshots/host-boot-20260914-geproton/waiting-for-services-0007.png`):
identical WAITING FOR SERVICES screen, spinner mid-animation (UI thread
alive), client log frozen — the hung instance persists across runs. The
screen IS the `servicesBootup` gate; it labels itself only once the login
sub-state is reached.

## Open questions for the host (in priority order)

1. ~~Does the Lumberyard console open?~~ NO — user tested, no console
   key works on the loading screen. Fall back to non-interactive command
   injection: `user.cfg` autoexec (profile dir and/or game root) whose
   output lands in the client log, then launch options (`-devmode`,
   `+map practicearena`). If yes it had opened: `PlayerAuthorizerEndpoint`
   (bare, echoes URL), `gamesparks_creds` (bare, proves reachability).
2. Is `Crucible.exe` spinning or sleeping (CPU % in System Monitor)?
3. If no console: `user.cfg` experiment — put echo commands in
   `.../AGS/Crucible/user.cfg` and/or the game root, relaunch, read the
   client log for printed values.
4. Launch-option experiment: `+map practicearena` (may bypass menu/auth UI
   if the client honors `+commands`); `-devmode` may unlock the console.
5. DNS capture during a fresh boot (`sudo tcpdump -i any -n port 53`):
   the 3 auth attempts each trigger resolution — the queried hostname is
   the PlayerAuthorizer endpoint.
