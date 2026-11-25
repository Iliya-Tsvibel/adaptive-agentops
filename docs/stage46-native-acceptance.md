# Stage 4.6 native acceptance plan

[Durable target replay contract](tasks/stage4/4.6-durable-target-replay-contract.md)
· [Stage 4.5 native evidence](stage45-native-acceptance.md)
· [Campaign contract](campaigns/stage4-autonomous-completion.md)

Run the cumulative workflow only against the reviewed, CI-tested merge.
Confirm authenticated owner, first attempt, read-only token, hosted Ubuntu
24.04, five-minute job limit, 180-second offline phase, protected staging,
and remaining included campaign capacity. Select Stage 4.3 through 4.6.

Require the prior 25 markers and these three additional markers, each once:

- `PASS 4.6 one correlated durable SQLite simulated effect`
- `PASS 4.6 restarted target denied replay and authenticated lookup retained the receipt`
- `PASS 4.6 abrupt exit after claim retained unknown evidence and no second effect`

The disposable fixture provisions distinct Controller and target databases
under their service UIDs. A synthetic approved action exercises one mediated
send. Inspect the target receipt and the Controller's committed outcome.
Restart the target with the same protected database and a fresh socket path.
Exercise a fixture-only duplicate and
an authenticated read-only receipt lookup. A second synthetic action causes
the target process to exit after the replay claim commits. Require the
Controller to retain an unknown outcome, the replay claim to survive, and
the target counter to remain unchanged after a duplicate is denied.
The trusted test client provides direct duplicates to check target-side
deduplication. The application Controller never retries a mutation.

Record the exact merge SHA, run and job IDs, selected inputs, owner and
triggering actor, attempt, runner, permissions, and all 28 markers. Require
independent evidence verification. Formal owner acceptance is separate.
