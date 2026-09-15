# Recon — Crucible client (partial download, 2026-09-14)

Source of truth: Steam download staging dir
`~/.local/share/Steam/steamapps/downloading/1057240/` (~2.1 GB of ~20 GB at
time of inspection) plus `appmanifest_1057240.acf` (TargetBuildID `5685572`).
Install dir `steamapps/common/Crucible` is still empty/0 bytes — Steam has not
promoted the staged files yet.

## Engine

- **Amazon Lumberyard 1.17.0.0** (copyright 2018) — see `engine.json`.
- `bootstrap.cfg`: `sys_game_folder=NvGameSDK`, `assets=pc`.
- `NvGameSdk/config/game.xml` + `NvGameSdk/Gem/gem.json` present; levels ship
  as `NvGameSdk/levels/*/level.pak` (`april2018`, `practicearena`,
  `tutorial_02`, `tutorial_03`, `mainmenu`, `HotH_02`).
- Paks are ZIPs with a CryEngine quirk: central-directory names use `/`,
  local headers use `\`. Standard `unzip` warns but reads them; Python's
  `zipfile` raises `BadZipFile` on `read()`; **`7z` handles them cleanly**.

## Binaries (`bin/`)

- `Crucible.exe` (~130 MB), `aws-cpp-sdk-gamelift.dll`, `steam_api64.dll`,
  `steamdatagram_ticketgen.dll`, `vivoxsdk.dll` (voice), `eac_server64.dll` +
  `EasyAntiCheat/` tree, CoherentGT UI DLLs, PhysX, `WTF.dll`.
- `Launch_Crucible.exe` (~1.2 MB) — thin launcher, no embedded URLs of note.

## Backend stack (all server-side, all dead)

Evidence from `config.pak` gem list + `Crucible.exe` strings:

1. **GameSparks** (game backend-as-a-service, itself sunset Sept 2022):
   `GameSparksSDKWrapper`, `GameSparksConfig`, `GSRequest`, cvars/commands
   `crucible_UsePreviewGameSparks` ("If non-zero, passes 'preview' flag to
   GameSparks init. Otherwise passes 'live'"), `Crucible.ReinitGameSparks`,
   `gamesparks_creds`, states `WAITING_FOR_GAMESPARKS_PARAMS` /
   `BEGIN_GAMESPARKS_RECONNECT`, error "Auth Tokens request timed out!
   Cannot authenticate to GameSparks." and "Login into GameSparks requires
   GS API key, GS API access, GS API secret, and GS region".
2. **CloudGem Framework live services** (AWS API Gateway/Lambda style):
   `CrucibleEndpointConfiguration` plus `CrucibleLive{Auth, Challenge,
   CharacterLevel, Entitlement, GameConfig, Matchmaking, Offer, Party,
   PlayerAuthorizer, PlayerData, PlayerIdentity}Service`. Auth flow touches
   Steam: `PlayerAuthorizerAppComponent::BeginAuthWithSteam`,
   `ConfirmSteamPurchase` / `ConfirmDlcOwnership`.
3. **GameLift** (dedicated servers): full AWS GameLift + Cognito client
   symbols in the exe; `aws-cpp-sdk-gamelift.dll` ships alongside.
4. **Netcode**: `NovaNetClient` / `NovaNetHub` / `NovaNetServer` gems;
   connect log format: `Attempting to connect to %s:%d with player info:
   [GameSparksId = %s, NovaNetUserId = (%u : 0x%08x), PlayerSessionId = %s]`.
   `server.xml` (in `gamedata.pak`) loads `Gem.NovaNetHub.Server` and
   `Gem.NovaNetServer.Server` — but **no server exe ships in the client
   depot's `bin/`**, so server binaries were GameLift-fleet-only.
5. **Voice**: Vivox (`vivoxsdk.dll`, `CruciComms` gem). **Anti-cheat**: EAC.
6. AWS regions referenced in the exe: `eu-west-1`, `eu-west-2`, `us-east-1`,
   `us-east-2`.

## UI

`gamedata.pak` → `ui/dist/*.js` (164 files) is the CoherentGT/LyShine HUD +
menus. Grep of all UI JS finds no backend endpoints — only support links,
a Qualtrics survey URL, `store.steampowered.com/api/appdetails?`, and a
Kinesis URL template. Backend config is compiled into `Crucible.exe`, not in
UI or pak filenames.

## What this means

There is no "point the exe at a server" config file. The client authenticates
(Steam → PlayerAuthorizer → GameSparks/Cognito), fetches live config, then
matchmakes into GameLift-hosted NovaNet servers. Every one of those services
is gone, so revival = emulate or bypass each layer. See `02-roadmap.md`.
