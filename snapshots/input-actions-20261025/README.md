# The client's held actions in its input frames (25 October)

These scripts are the evidence for docs/25's "The rest of the frame, and the
held actions" section. All of them are read-only: they read the client exe
and the shard's own UDP capture, and write nothing in the repo.

- `ier.py`, `lib_emul.py`: the static side.
  - `lib_emul.py` runs ActionsLibrary's populate routine (exe 0x36fe370)
    straight-line and records the bit index each AddAction call passes. That
    gives the action-to-bit table.
  - `ier.py` is the PE and disassembly helper it uses (the bot-weapon
    track's inspector code).
  - The exe path comes from `CRUCIBLE_EXE`, by default the Steam install.
- `extract.py`, `corr2.py`, `validate.py`, `validate2.py`: the empirical side.
  - The source is the 5 October capture, `server/shard_udp-20261005T0245Z.log.gz`
    (42 MB, not committed, the shard's own datagram log).
  - `extract.py` parses it into `packets.pkl`.
  - `corr2.py` and the validators check the bit table against it, for
    example that PrimaryFire leads each accepted hit report (115/4).

To rerun, with the project venv (lz4, pefile, capstone):

    export INPUT_ACTIONS_WORK=$(mktemp -d)
    zcat server/shard_udp-20261005T0245Z.log.gz > $INPUT_ACTIONS_WORK/shard_udp_big.log
    cd $INPUT_ACTIONS_WORK
    "$REPO/.venv/bin/python" "$REPO/snapshots/input-actions-20261025/extract.py"
    "$REPO/.venv/bin/python" "$REPO/snapshots/input-actions-20261025/corr2.py"
    "$REPO/.venv/bin/python" "$REPO/snapshots/input-actions-20261025/lib_emul.py"

Results as of 25 October:
- 95% of 663 hit reports have PrimaryFire (byte 0, 0x20) set within the
  preceding second, against 3% for a random frame.
- The alliance bits toggle in their default key order.
- Interact (byte 0, 0x80) is seen live once, a 20-message burst from a
  standing player. Its bit rests on the static table.
