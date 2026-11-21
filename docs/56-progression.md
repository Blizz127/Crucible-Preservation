# Recovering persistent progress

The project's persistence design records match awards in an append-only journal. Each award has a stable match ID. Once that ID has been credited to an account, replaying the award uses the same account and skips a credit already present there. This covers retries after an interrupted write and changes to the active account.

Serialize account updates across writers. Write a replacement document to a temporary file in the same directory, flush it to disk, then atomically rename it into place. Preserve unreadable account documents for inspection before reconstructing match awards from the journal. An incomplete final journal entry can wait until the writer finishes it.

Recovery tests can submit the same award twice, restart between updates, and change the active account before replay. In each case the original account should show a single credit.
