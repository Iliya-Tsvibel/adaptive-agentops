# Stage 4.3 native acceptance plan

**Native execution and owner acceptance are pending.** Ordinary CI and local
socket tests exercise the implementation, deterministic fault cases, and
both serial revoke/admit orderings with modeled service identities. They
supply no distinct-UID or protected-runner acceptance evidence.
[Stage 4.3 contract](tasks/stage4/4.3-operator-delegation-contract.md)
· [Stage 4.2 acceptance](stage42-native-acceptance.md)

## Prerequisites and command

Use a separately authorized fresh dispatch for the exact reviewed and merged
`main` SHA, after verifying the registered workflow, repository, owner
authentication, included Actions capacity, blocked paid overage, and no
competing privileged run. Check GitHub-hosted x86-64 Ubuntu 24.04, first
attempt, owner-only workflow guard, read-only contents, native Linux outside
WSL and containers, and the existing five-minute job and 180-second offline
phase limits. Root runs only the audited setup in the isolated runner. It
validates the protected executable code mount and socket parent, verifies
the existing hash-pinned wheels, checks dependency loading under the Control
Plane UID, sets up the separate directories and dedicated temporary UIDs/GIDs,
and conducts all security probes inside a loopback-only network namespace.

```bash
gh workflow run stage4-2-native-acceptance.yml --repo Iliya-Tsvibel/adaptive-agentops --ref main -f include_stage43=true
```

The explicit input adds Stage 4.3 after the existing Stage 4.1 and Stage 4.2
probes in the same offline job. Default input preserves the baseline two
slices. Confirm executed SHA, both actors, first attempt, exact job success,
and every original marker plus the six additional markers exactly once.

- `PASS 4.3 operator filesystem isolation and kernel UID rejection`
- `PASS 4.3 authenticated requester issuance and own grant inspection`
- `PASS 4.3 approver-only and forged operator authority denied`
- `PASS 4.3 live managed grant and revoked proposal denial`
- `PASS 4.3 bounded operator connections and both sockets cleaned up`
- `PASS 4.3 operator rejected wrong server UID before sending data`

## Evidence boundaries

The native Control Plane checks Unix ownership, modes, ACLs, and connected
peer credentials using the inherited boundary code. An actual agent is
denied entry by its group configuration. A second attempt lets the same
agent reach the socket in a controlled test to verify the UID decision.
An unmapped target UID and an approver-only UID supply independent role
and peer negatives. A wrong server socket presents correct filesystem
ownership while the process retains the wrong kernel UID. It must receive
zero request bytes.

The requester obtains a real response, and the agent receives a real
positive proposal verdict before revocation and a denial after revocation.
The controller's bounded decision records are compared with each response
and session correlation. The server effects guard verifies the reviewed
fixture only. Socket timeouts, journal failures, clock faults, stale
sessions, and opposite race orderings also have deterministic tests. This
plan does not claim durable audit, MFA, dispatch, target execution, or
production readiness.

Save the run URL, job ID, exact SHA, runner, actors, permissions, attempt,
and bounded marker/failure logs. Have the independent reviewer trace the
evidence through the assertions. Stop after an unsuccessful or ambiguous
run. Any additional privileged invocation requires separate authorization.
