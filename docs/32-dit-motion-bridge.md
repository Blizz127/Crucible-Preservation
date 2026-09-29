# DIT motion bridge

The local shard can publish aggregate movement observations to DIT's existing
filesystem transport. `server/dit_motion.py` observes the decoded local-player
input position and the Marine hunters' corrected positions after navmesh
grounding and separation. It samples both at 4 Hz and writes the established
`knowledge/observed/*.json` and `knowledge/observed/bots/*.json` schema. DIT's
`ModuleSeedImporter` imports these documents as adapter-private Crucible
observations for motion scorecards.

The observer keeps the last accepted position in memory to estimate speed. It
does not persist coordinates, combat events, or account identifiers. It drops
teleports and gaps over five seconds, waits 0.5 seconds before recording a
move/pause transition, caps the retained bout samples at 400, and atomically
replaces each document at most once per minute. Files accumulate aggregate
sample counts and bout durations across shard restarts. The only human subject
label is the local constant `local-player`; bot labels are stable Marine slot
names.

The shard transport is opt-in through `CRUCIBLE_DIT_TRANSPORT_ROOT`. The
managed local service installer sets it to this checkout's `dit-transport`
directory. DIT's host defaults register that directory as Crucible's `solo`
transport, with no sync-down target. The hub must be restarted to load this
host-default change; the local services installer must be rerun to apply the
shard environment change. Neither was restarted by this implementation task.

This is a DIT observation feed, not DIT-controlled bot behavior. Hunter
decisions remain in `server/hunter_ai.py`; this feed gives DIT comparative
human and actor motion evidence for later behavior work. It does not record
combat choices or prove that the bots behave like humans. Live ingestion and
in-game presentation have not been verified because no Crucible client process
is available in this environment.
