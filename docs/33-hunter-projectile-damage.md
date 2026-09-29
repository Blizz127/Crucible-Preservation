# Hunter projectile damage

The shard already emits Marine primary-fire activations to the local client
(`docs/29`). This change accepts the client's decoded
`ClientProjectileResult` (component 115, RPC 4) for the three registered
Alpha Hunters and passes the report through the same combat checks as player
fire: live registered shooter, forward projectile-ID sequence, exactly one
live enemy target, and the authored Marine primary damage curve. The target's
health, death metadata, terminal team check, and `GameOver` phase then use the
existing server-side combat path.

Hunter reports are limited to IDs `01000003` through `01000005` and are gated
by `CRUCIBLE_SOLO_BOT_HIT_REPORTS`. The local services installer enables that
gate for the 127.0.0.1 shard. It does not accept hunter reports in other modes
or from arbitrary entity IDs. The report is still client-observed collision
history, as with player projectiles; this does not independently verify a
raycast or cover collision on the server.

This completes a damage path for local solo play if the retail client sends
`ClientProjectileResult` for the replicated hunter projectiles. That behavior,
the first visible hit, death presentation, and a loss through hunter damage
still require an in-game capture. The client and DIT services were not running
during implementation, so the feature remains unverified in play.
