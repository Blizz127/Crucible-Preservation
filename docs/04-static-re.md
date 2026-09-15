# Static RE — Crucible.exe build 5685572

All addresses are RVAs (image base `0x140000000`). No execution involved:
PE parsing via `pefile`, disassembly via `capstone`, function bounds from
`.pdata` unwind data (464,648 functions). Reproduce anything here with
`./tools/xref.py` (e.g. `./tools/xref.py --func 0x1f6a290`).

## Binary facts

- Monolithic MSVC release build, `.text` 103 MB, `.rdata` 18 MB; entropy
  6.5 → **not packed**. ASLR + DEP on, GUI subsystem.
- Link timestamp **2020-10-14** — final weeks before the Nov 2020 shutdown.
- PDB ref is the bare name `Crucible.pdb` (build path stripped).
- Source paths baked into log calls:
  `d:\workspace\crucible_hotfix\...NovaGameClient\GameDll\...`,
  `E:\crucible_mainline\3rdParty\AwsGameSparks\...`

## Import inventory (revival-relevant)

- `WS2_32` (34): full client **and** server sockets
  (`socket/connect/send/recv` + `bind/listen/accept`, `getaddrinfo`,
  `WSAPoll`) — the client can listen, supporting a listen-server theory.
- `WINHTTP` (12): full HTTP client — presumably the CloudGem REST path.
- `WININET` (12): second HTTP stack (crash upload or legacy path).
- `steam_api64` (11): `SteamAPI_Init`, `RestartAppIfNecessary`,
  `RunCallbacks`, callback/callresult registration. Auth tickets go through
  interface vtables (no direct ticket import), so ticket calls need vtable
  analysis, not import xrefs.
- `CRYPT32` + `bcrypt` + `WINTRUST!WinVerifyTrust`: cert validation and
  signature verification.
- `vivoxsdk` (38), PhysX, CoherentUIGT, `AVIFIL32` (intro videos),
  `D3DCOMPILER_47`.
- **EAC is not in the static imports** — loaded dynamically, a patch seam.

## Console: the revival control surface

Lumberyard console interface pattern: global → `+0xa0` → vtable;
`+0x18`/`+0x20` register cvars, `+0x110` registers commands.

Cvar/command registration cluster at `0x1f6a3b0`:

| Name | Kind | Notes |
|---|---|---|
| `gamesparks_creds` | command → `0x1f6a290` | **"Set credentials required to initialize GameSparks."** Usage: `gamesparks_creds <ApiKey> <ApiAccess> <ApiSecret> <Region>` (argc ≥ 4, reads args 1–4 into `GameSparksConfig` via `0x141f6b050`; short of args prints "Login into GameSparks requires GS API key, GS API access, GS API secret, and GS region" under `CrucibleLiveAuth`) |
| `PlayerAuthorizerEndpoint` | command → `0x1f6a350` | **"Set endpoint url for the PlayerAuthorizer service"** — exactly 1 arg, stored via `0x141f6ae00`. Only live-service endpoint with a console override |
| `crucibleLive_lwa` | command → `0x1f6a1e0` | "Log in with a LWA token." (Login With Amazon) |
| `crucibleLive_authChannel` | string cvar (default `""`) | "AGS Live Auth channel to log in to." |
| `crucibleLive_sessionRefreshWindowMinutes` | int cvar (default 30) | "Period of time before expiry that AGS Live credentials should be refreshed." |

Cvar registration at `0xb7a030`:

- `crucible_UsePreviewGameSparks` (default 0): preview vs live GameSparks.
- `crucible_SteamTokenOverride`: **"If Steam authentication is enabled, use
  this Steam Session Ticket to authenticate"** — a canned-ticket injector.

Other names found in the console filter list (`0xe39be0`, ~150 entries):
`connect`, `disconnect`, `connect_lobby`, `crucible_UsePersonaAuth`,
`lwa_clientId`, `sv_EnableLAN`, `sv_LanHost`, `cl_LANTeamID`, `g_skipIntro`,
`rcon_*`, backfill overrides. `0xe39be0` returns 1 when its input matches a
listed name (case-insensitive, reached only via indirect call, so caller
semantics are unconfirmed). Working hypothesis: **release-console
whitelist** — the list contains benign cvars (`cl_sensitivity`,
`r_Fullscreen`, `quit`) that no blocklist would include, so
`gamesparks_creds` / `PlayerAuthorizerEndpoint` / `connect` should be
typeable. Confirm on host by typing `gamesparks_creds` with no args (expect
the usage error, which proves reachability).

## Auth flow (reversed)

- `ReinitializeGameSparks` at `0xb7a530` (self-identifying log strings):
  takes the component lock, calls validator `[vtable+0x60]`; if creds are
  complete → logs "Resetting GameSparks!" and tears down/re-inits; else
  logs "GameSparks API Key, Access Key, Secret Key, Region, Auth Token, and
  entitlementId is required to reinitialize GameSparks". So the console-set
  key/access/secret/region plus token + entitlementId (arriving via the
  auth flow) gate init.
- `crucible_UsePersonaAuth` (+ description "If true, Crucible will use the
  Persona based PlayerAuthorizer flow instead of Gamesparks...") selects a
  parallel `PersonaNotificationListenerAppComponent` stack with its own
  TLS+websocket client (`PersonaUIWebsocket`: `TlsClient`, `TlsContext`,
  `WebSocketClient`, `PhysicalConnection`). Xrefs at `0xe3a743`,
  `0x4f9992a`.
- No other `Set endpoint url` commands exist: the remaining live-service
  endpoints come from `CrucibleEndpointConfiguration` / post-auth messages
  ("Received new connection url from gamesparks backend"), i.e. redirect
  the GameSparks stub and the rest follows.

## GameSparks wire format (for the stub server)

- SDK: GameSparks C++ 1.1.14, transport = **easywsclient** (open source)
  over **mbedtls**, JSON messages with `ScriptMessage`/ExtCode/ShortCode
  validation (`GameSparksSDKMessageParser`).
- URL builder at `0x5fa2f90` assembles:
  `wss://<stage>.service.gamesparks.<region>.amazonaws.com/ws/game/<key>/stage/<preview|live>/type/device`
  (literals: `"wss://"`, `".service.gamesparks."`, `".amazonaws.com"`,
  `"/ws/game/"`, `"/stage/"`, `"/type/device"`).
- Message model visible in strings: `GameFound` (session ID, ip, port,
  player session ID), `P2P`, `PartyStatus`. A stub must answer auth +
  `GameFound` pointing at a loopback NovaNet listener.

## Server connect path

- Entry: `Crucible::ConnectToServerWithPlayerSession(...)` at `0xd76b70`
  (`NovaGameClient\GameDll\Crucible\Services\...`). Logs
  `Attempting to connect to %s:%d with player info: [GameSparksId = %s,
  NovaNetUserId = (%u : 0x%08x), PlayerSessionId = %s ...]`, hashes the
  hostname (case-insensitive CRC32 → id), builds a ≤0x7f FixedSizeString
  node and dispatches the async connect job. Identity tuple for the server:
  **GameSparksId + NovaNetUserId + PlayerSessionId + host + port**.

## UI layer (no backend secrets)

`gamedata.pak:ui/dist/*.js` is compiled Svelte. Matchmaking is a client
state machine (`matchmaking.js`):
`IDLE → READY → SEARCHING → CANCELLING → CONNECTING → MATCHMAKING_COMPLETE`.
Key screens: `connecting.js` (connecting/reconnecting),
`disconnected.js` (quit modal), `booterror.js` (support-link error),
`reconnecting.js`, `party-errors.js`. UI reaches native code through
LyShine UI endpoints (the dozens of `*Endpoint` names in the binary are
UI bindings, not backend URLs). No credentials or hosts in JS.

## Next actions

1. Host: `./tools/host_boot.sh` — first real boot log + DNS capture.
2. Host console: `gamesparks_creds` (no args) to prove command reachability;
   then `crucible_UsePreviewGameSparks 1` / `crucible_UsePersonaAuth ?`.
3. Build the GameSparks wss stub (auth + GameFound → 127.0.0.1) and point
   the client at it via `gamesparks_creds` + hosts-file redirect of the
   assembled `*.service.gamesparks.*.amazonaws.com` name.
4. Deeper: vtable analysis of `SteamInternal_FindOrCreateUserInterface`
   consumers (auth-ticket flow), `ConnectToServerWithPlayerSession`
   callees (NovaNet handshake bytes).
