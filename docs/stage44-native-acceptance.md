# Stage 4.4 native acceptance plan

**Native evidence and formal owner acceptance are pending.**
[Protected Controller state contract](tasks/stage4/4.4-protected-controller-state-contract.md)
· [Stage 4.3 native evidence plan](stage43-native-acceptance.md)

## Preconditions and command

Apply the campaign preflight to the exact reviewed, CI-tested and merged
`main` SHA. Require the actual owner, disposable hosted Ubuntu 24.04, the
first-attempt check, read-only repository contents, the five-minute job
limit and 180-second offline phase. Verify the previous 17 markers remain in
the selected scope. After preflight, use one fresh campaign dispatch.

```bash
gh workflow run stage4-2-native-acceptance.yml --repo Iliya-Tsvibel/adaptive-agentops --ref main -f include_stage43=true -f include_stage44=true
```

## Expected additional markers

- `PASS 4.4 private Controller state and overlapping service denied`
- `PASS 4.4 durable grant issue and revoke with matching decision evidence`
- `PASS 4.4 fresh Controller session invalidated an active grant after process exit`
- `PASS 4.4 restarted Controller denied stale grant and retained prior evidence`

Require all 21 markers exactly once, a successful job, and independent
verification of the bounded log and reviewed code. Record every dispatch,
including a failed or cancelled one, against the campaign limit.

The disposable fixture prepares separate protected socket directories and a
Controller-owned mode-`0700` data directory. All SQLite operations run under
the Control Plane UID. The agent and operator run under distinct UIDs and
attempt to open the protected directory and database, which must fail. A
competing Controller with independent socket paths must fail on the same
state-directory lease before either new socket appears.

One Controller issues and revokes through authenticated IPC. A second sends
a live positive verdict and is terminated, leaving durable active evidence.
The third opens the same state, denies the old grant through both links,
allocates a fresh grant, and retains all prior evidence. A trusted fixture
reads the database after the processes exit to check the exact grantor,
agent, scope, session, run, terminal state, and matching decision events.

The process-exit test demonstrates restart invalidation and preservation
of committed evidence on the runner. It does not simulate hardware power
loss or a hostile administrator. Ambiguous commits, schema corruption and
expiry boundaries have separate deterministic tests. This fixture does
not authorize or execute a target mutation.
