# Network surface (from live DNS capture + static RE)

## Bootstrap endpoint (CONFIRMED via pcap)

- Host: **`x8evjomsd1.execute-api.us-west-2.amazonaws.com`** — API Gateway
  REST API in us-west-2, queried exactly 3x at boot (one per
  `PlayerAuthorizerCall` attempt). Currently **NXDOMAIN** (API deleted),
  so hosts-file redirect is conflict-free.
- Full URL shape (reversed from `0x26f21e0`): `<PlayerAuthorizerEndpoint
  value>` + `"/authorizePlayer"` — no stage in the path, so any stage is
  part of the endpoint value itself.
- The API ID string exists in NO binary, pak, or config (ASCII, UTF-16,
  raw grep all negative) while `us-west-2` does appear (next to the
  `WSSL\UriBuilder.cpp` SigV4 signer strings) — the default endpoint is
  obfuscated/assembled at runtime. Irrelevant for revival: `user.cfg`
  overrides it before auth fires.
- EAC is alive as a service: boot also queries `download.eac-cdn.com`,
  `download-alt.easyanticheat.net`, `gossip.easyanticheat.net`.

## REST inventory (string literals in exe)

- Authorizer: `/authorizePlayer`
- Identity: `/players/externalid`, `/players/externalids`,
  `/players/getdisplayname`, `/players/setdisplayname`,
  `/players/getplayericon`, `/players/setplayericon`, `/players/sessiondata`
- PlayerData: `/playerdata/activeloadout`, `/playerdata/getclientversion`,
  `/playerdata/setclientversion`, `/playerdata/getmatchrecordsforplayers`,
  `/playerdata/incrementplayermatchrecord`, `/playerdata/getplayermatchhistory`,
  `/playerdata/setplayermatchhistory`, `/playerdata/getplayermatchstate`,
  `/playerdata/setplayersmatchstate`, `/playerdata/getplayermatchstateforplayers`,
  `/playerdata/getrankpointsforplayers`, `/playerdata/setrankpoints`,
  `/playerdata/inputconfiglist`, `/playerdata/skillratings`
- Custom games: `/customgames/pingplayers`, `/customgames/setplayerposition`,
  `/customgames/swapplayerpositions`
- Infra: `/CloudCanvas_AwsApiJob`

## Per-service endpoint pattern (CONFIRMED via config files)

`misc.pak:metagame/remoteapicatalog.json` (AZ ObjectStream, loaded by the
exe via the `@assets@/MetaGame/RemoteApiCatalog.json` alias) maps services
to full HTTPS URLs — each live service gets its OWN API Gateway ID + stage:

- Entitlements: `https://2xizkmky98.execute-api.us-west-2.amazonaws.com/dev/entitlements/{list,invoketransaction,getblueprintcatalog}`
- Analytics: `https://kinesis.us-west-2.amazonaws.com`

No other catalog/URL lives in any pak script or config (424 files
surveyed) — the remaining endpoints (authorizer `x8evjomsd1…/prod`, etc.)
are obfuscated/assembled in code (absent from ASCII, UTF-16, and raw
byte search). Irrelevant for revival: hosts-file redirect covers them.

## JSON key candidates (exact-case literals)

`entitlementId`, `EntitlementId`, `gamesparksId`, `playerId`, `PlayerId`,
`region`, `sessionTicket`, `SessionTicket`, `Token` (both casings present;
request/response sides TBD from stub logs).

## Response iteration (server/responses.json, hot-reloaded)

- v1: 4 oracle-confirmed EndpointConfig keys + auth candidates (both
  casings). Never served (no privileged listener available in this
  environment).
- v2 (current, 47 keys): + identity cluster (`playerSessionId`,
  `gameSparksPlayerId/UserId`, `steamToken`, `accessToken`, …), AWS
  triple (`accessKeyId`, `secretAccessKey`, `sessionToken` — matches the
  `AwsSessionCredentials` RTTI), login verdict (`loginAllowed: true`,
  `loginFailedReason: ""`), inits (`hasEntitlementsParams`,
  `initServiceCredentials`, `initAwsCredentialsWithSteam`), game config
  pointers. All key names are exact-case binary literals. Next iteration
  is driven by the client's missing-key oracle once v2 is served.

## Redirect plan

- `user.cfg` override is INEFFECTIVE (proven 2026-09-14 23:29 UTC): with
  `PlayerAuthorizerEndpoint http://localhost:8443` set, the client still
  queried the default `x8evjomsd1…` hostname 3x and sent nothing to the
  stub (plain HTTP and TLS both ruled out by connection-level logging).
  Hypothesis: the service client snapshots the endpoint at construction,
  before `user.cfg` executes; the console setter only updates the config
  component. `//` comment truncation and IP-literal rejection were both
  ruled out by the same runs.
- Current path: hosts-file redirect of the default hostname +
  `server/tls_stub.py` (self-signed cert for the API hostname, stdlib +
  openssl, verified with a live TLS probe). Pending: whether Wine/WinHTTP
  accepts the self-signed cert; if not, install the test CA into the
  Proton prefix trust store.
