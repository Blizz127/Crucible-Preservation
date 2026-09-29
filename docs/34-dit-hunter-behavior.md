# DIT movement behavior input

The shard can consume DIT's shared human-tendency file at match startup. DIT's
Crucible transport is registered as both an import root and a sync-down target.
The hub writes `knowledge/shared/human-tendencies.json` there on its scheduled
sync-down run.

`server/dit_motion_policy.py` looks for the field `pause-bout-seconds` and
`move-bout-seconds` tendencies. It samples uniformly within their measured
P25–P75 ranges only when both have
document is schema version 1, its approval is `active`, its evidence is
non-provisional, and `evidenceGames` includes `crucible`. Both ranges must be
ordered within 0.25 and 20 seconds. Candidates, provisional samples,
malformed documents and out-of-range values do not change bot behavior. The file is read
once when a solo match starts, so the hub and shard do not need a live control
connection.

When active, a hunter samples its moving and paused bout lengths independently
from the approved ranges using its seeded per-bot random generator. Its patrol
pauses when a movement bout expires, and resumes when a pause bout expires.
Pursuit, attack timing, target selection, damage and retail movement-speed limits remain on
the current authored simulation. The observation source pools field samples
across games; requiring an active non-provisional tendency with Crucible in
its evidence prevents unrelated or unreviewed evidence from changing the
Crucible bots.

The path is gated until a person approves suitable DIT evidence. There is no
active Crucible tendency on this machine yet, and neither the hub sync-down nor
the in-game effect was run during implementation. The hub must reload its host
configuration, the shard service must receive its transport environment, and a
human movement sample must pass DIT import, scorecard and approval before this
changes bot behavior. Target selection and combat remain outside DIT control.
