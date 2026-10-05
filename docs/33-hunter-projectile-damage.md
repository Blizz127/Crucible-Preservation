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

## Refused reports are logged (5 October)

Before this change the shard logged accepted hits only. If a hunter hit never
appeared, a live run could not show whether the client had sent no report or
whether a gate had refused the report. Each refusal is now logged as
`SOLO COMBAT report refused shooter=<id> (<gate>, count N): <detail>`. A line
is written at counts 1, 2, 4, 8, ... for each shooter and gate, so the final
count is a lower bound with a 2x tolerance. The gates are:

- `shooter not live`;
- `projectile id not forward`;
- `no live enemy`;
- `several live enemies`;
- `invalid vector`;
- `unregistered shooter`, logged only with `CRUCIBLE_SOLO_BOT_HIT_REPORTS=1`,
  so hunter results sent under an unexpected entity id remain visible.

The detail field shows the projectile id, the previous accepted id and every
reported entity id. `tools/match_acceptance.py` turns these lines into the A4
and A5 evidence. Suppose a live run shows hunter fire decisions but no
accepted hunter hit and no refused hunter report. Then the retail client is
not reporting results for remote projectiles, and hunter damage needs a
server-resolved path instead.
