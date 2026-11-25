# Stage 4.5 native acceptance plan

**Native PASS at `6d618afd6bd33328ef9ecda0a99e023a86afaa8b`.
Formal owner acceptance is pending.**
[Independent evidence checkpoint](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/21)
· [Native job](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45681467288/job/139442818846)

[OS-bound dispatch contract](tasks/stage4/4.5-os-bound-dispatch-contract.md)
· [Stage 4.4 native acceptance plan](stage44-native-acceptance.md)

## Preconditions and command

Apply the campaign preflight to the reviewed, CI-tested and merged
`main` revision. Require the authenticated owner, first attempt, read-only
repository contents, hosted Ubuntu 24.04, five-minute job limit and
180-second offline phase. Validate protected staging, extension loading
under the isolated Control Plane UID and expected service identities.
Keep all 21 prior markers in the selected scope.

```bash
gh workflow run stage4-2-native-acceptance.yml --repo Iliya-Tsvibel/adaptive-agentops --ref main -f include_stage43=true -f include_stage44=true -f include_stage45=true
```

## Expected additional markers

- `PASS 4.5 authenticated target rejected wrong Controller UID before reading`
- `PASS 4.5 wrong target received zero dispatch bytes and created no reservation`
- `PASS 4.5 durable fixture reservation and correlated no-effect acknowledgement`
- `PASS 4.5 consumed fixture approval cannot authorize a second application send`

Require all 25 markers once, successful job completion, and independent
verification of the exact code and bounded logs. Record every dispatch
against the campaign limit.

The distinct AGENT UID obtains only controlled filesystem reachability to
the target socket. The real target fails kernel peer authentication before
reading request bytes. For the reciprocal check, a wrong-UID listener is
moved into the expected path and given the expected socket metadata. The
Controller connects, rejects the wrong peer, sends no request data, and
creates no reservation. The test uses a pinned socket inode throughout.

For the positive case, the Controller uses a single explicitly synthetic
authorization fixture in private SQLite. The target over the authenticated
connection returns a correlated no-effect acknowledgment. The trusted
fixture inspects the committed approval, reservation and audit under the
Controller UID after the exchange. A fresh target observer accepts at
most one retry connection and confirms that the repeated coordinator
emits zero application bytes. The original reservation and consumed
approval remain intact.

The fixture tests OS-bound authorization and one Controller-mediated
application send. It supplies no evidence of authenticated live human
approval, durable target claims, target replay protection, simulation
effects, real infrastructure, or production readiness.
