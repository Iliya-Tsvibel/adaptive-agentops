# Stage 4 local acceptance report

**ACCEPT LOCAL STAGE 4** is the recommendation. Formal acceptance belongs to the
repository owner. Production remains **NO-GO**.

The executable candidate is `34c38d9087b5c34b97fa3d4b68cafc3dc8219e0a`, the reviewed and CI-tested
Stage 4.7 squash merge. The native workflow exercised this exact revision.
The report publication adds the evidence record after that run.

## Merged work and evidence

| Scope | Merged PRs | Verification |
| --- | --- | --- |
| 4.1 and engineering baseline | [#9](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/9), [#10](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/10), [#11](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/11) | Native IPC at `de5d9db`, owner accepted. [Run 45403991684](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45403991684) |
| 4.2 scoped admission and staging | [#12](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/12), [#13](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/13), [#14](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/14), [#15](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/15), [#16](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/16) | Native 11/11 at `044c112`. [Run 45609096357](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45609096357) |
| 4.3 operator IPC and recovery | [#17](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/17), [#18](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/18) | 1,623 CI tests after staging repair. Campaign native 17/17 at `b2fd3a4`. [CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45615365922) · [Native](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45623922695) |
| Campaign governance | [#19](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/19) | 1,627 CI tests after correcting documentation fixture coverage. [CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45624560662) |
| 4.4 Controller state | [#20](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/20) | 1,656 CI tests. Campaign native 21/21 at `e5ea24b`. [CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45626541124) · [Native](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45626930749) |
| 4.5 authenticated dispatch | [#21](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/21) | 1,724 CI tests. Campaign native 25/25 at `6d618af`. [CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45681017591) · [Native](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45681467288) |
| 4.6 target replay and effects | [#22](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/22) | 1,738 CI tests. Campaign native 28/28 at `bb4fd26`. [CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45691861904) · [Native](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45692237741) |
| 4.7 hardened acceptance | [PR #23](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/23) | 1,755 CI tests on the reviewed tree. Campaign native 32/32 at the executable candidate SHA above. [CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45794474657) · [Native job](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45794902062/job/139822944001) |

## ADR-0001 compliance

| Decision | Implemented local guarantee | Evidence |
| --- | --- | --- |
| D01 | Dedicated Linux UIDs for agent, Controller and target, with distinct requester and approver UIDs. | Native cross-UID boundary, requester and approver role checks, and unmapped-agent denial with socket reachability. |
| D02 | Protected Unix sockets with filesystem validation and mutual peer authentication. | Native ownership, mode, ACL, wrong peer/client and zero-byte-to-wrong-server markers across proposal, operator and dispatch boundaries. |
| D03 | Frozen UID-to-principal mapping and server-owned run, session and scope. | Strict typed proposal and authorization payload tests. Native decision evidence correlates mapped UID, run, grant and action. |
| D04 | Durable bounded grant, scoped to one operation, agent, resource and current session. | Native issue, revoke, expiry and restart checks. Focused tests enforce scope, expiration and grant rollback. |
| D05 | Requester-only grant management and approver-only action inspection, explicit decision and dispatch request. | Native denies wrong role and agent peer. Focused tests deny wrong ID/fingerprint, forged fields and rejected or reused decisions. Stage 2 records preserve committed action scope and immutable decisions. |
| D06 | OS-bound target authorization, exact action and approval binding, fresh PDP, atomic reservation and one send. | Native correlates approval consumption, reservation, digest and receipt. Tests demonstrate revised-policy denial, audit rollback and no send after ambiguous reservation commit. |
| D07 | Protected target SQLite claim and effect receipt, idempotent evidence lookup, replay denial. | Native restart, duplicate and interrupted claim markers. Deterministic target tests cover transaction failure and concurrency. |
| D08 | Separate legacy and hardened launches and database profiles. | Native legacy loopback closed and leak checks. Focused tests reject profile interchange. |
| D09 | Frozen Controller policy and identity configuration with fresh sessions. | Native restart invalidates prior authority. Runtime rechecks the session map, target endpoint, grant, approval and audited approving UID. |
| D10 | Retained unresolved reservations and target claims, capped state, and fail-closed evidence. | Native abrupt exit after claim leaves no second effect. Focused tests cover audit/capacity failure, ambiguous commits, stale authority and loss of receipt. |

[ADR-0001](decisions/ADR-0001-stage4-identity-boundaries.md)
· [Stage 4.7 contract](tasks/stage4/4.7-hardened-local-acceptance-contract.md)

## Demonstrated chain

The authenticated requester issued the bounded grant. The Controller used
the authenticated agent UID and server-owned run to admit the typed
`mock.restart_service` proposal for `demo-api`. The PDP required approval,
and the Controller durably recorded the pending action and decision
evidence. The mapped approver inspected the action, sent an explicit
approval with its ID and fingerprint, then separately requested dispatch.

On the authenticated target connection, the Controller rechecked policy and
scope, committed the Stage 2 approval consumption with one reservation and
audit event, and recorded the exact target request digest. The target
recorded one SQLite counter increment and a receipt bound to the action,
dispatch and digest. On the interrupted path the target committed a replay
claim and exited before incrementing the counter. The Controller retained
an `UNKNOWN` outcome and rejected a second dispatch. The final target
counter was one across both paths. The native suite retained the Stage 4.6
replay denial and authenticated receipt lookup markers.

The approved local operator boundary authenticates the OS UID. A
disposable test process exercised that identity in the owner-triggered
workflow. Same-UID processes share that principal. Physical-human presence,
MFA and workload attestation are outside this evidence.

## Verification and independent review

The initial exact-diff review identified a Medium issue with grant expiry
and acknowledgement during proposal and approval recording. The repair
checks grant clocks and the request deadline inside the decision
transaction and suppresses the reply if expiry overtakes an otherwise
confirmed commit. Reservation also checks the persisted approval evidence
for the mapped approver UID. Independent re-review passed on full-index
diff SHA-256 `3037397e015cc57dc30a8b919b45d468cf00eace437c110d42dd51da01eeb49c`.
The independent merge gate matched the reviewed, CI checkout and squash
tree `45b7cfddc6207794795a816109da4b330ef58d02`.
The independent native evidence review and consolidated D01–D10 security
assessment passed. No blocking findings remain. [Review and verification record](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/23)

The final Stage 4.7 ordinary-user suite passed 1,755 tests, with one
existing Starlette deprecation warning. The focused suite passed 445
tests. The documentation doctor and workflow Bash syntax checks passed.
The restricted sandbox blocked Unix peer-credential tests. Those checks
passed when rerun as the ordinary user with the required socket access.
The development machine runs WSL2. Security acceptance comes from the
separate native Linux VM.

The focused tests assert rejection of a missing or revoked grant, expiry
before or during decisions, an unmapped UID, a forged requester field, a
wrong action ID or fingerprint, PDP denial, explicit rejection, and
consumed approval. They also assert rollback when required proposal,
approval or reservation evidence cannot be written, no send after an
ambiguous approval or reservation commit, and no retry after loss of the
target receipt. Earlier tests cover isolated target replay, concurrent
claims, restart, uncertain outcomes and capacity. [Hardened tests](../tests/test_stage47_hardened.py)
· [Dispatch tests](../tests/test_stage45_dispatch_state.py)
· [Target tests](../tests/test_stage46_target_state.py)

Native run `45794902062`, job `139822944001`, attempt 1, selected Stage 4.3 through
4.7 on `main` under the existing read-only workflow. Both the actor and
triggering actor were `Iliya-Tsvibel`. The hosted runner was Ubuntu 24.04.5.
The marker inventory was 32/32, each exactly once. [Cumulative marker plan](stage47-native-acceptance.md)

## Limits and residual risks

- The kernel, host administrator, reviewed Controller and target, and
  protected launch configuration are trusted. The evidence is local and protected by those
  boundaries. It does not establish tamper-proof audit or availability.
- The profile supports one bounded restart grant and pending action per
  Controller session. A restart invalidates authority, and disposition of
  ambiguous operations requires an operator to inspect durable evidence.
  Capacity fails closed. A reserved operation can be lost before send.
  Once sent, a request cannot be recalled.
- The single mutation is a SQLite counter. The target evidence proves a
  simulated transaction under this deployment. Generic external effects,
  distributed delivery, and production-grade identity and recovery require
  separate design and review.
- The hardened profile currently derives part of its dispatch schema from
  shared DDL text. The independent reviewer recorded a low maintainability
  observation. Schema and integration tests cover the current definitions.

## Campaign usage

The campaign used **5 of 8 fresh native dispatches** and six ordinary
execution CI runs. One execution CI run failed during the governance
documentation fixture change and passed after repair. The report PR
records its documentation publication CI separately. The historical
Stage 4.2/4.3 setup failures and the initial Stage 4.3 CI are outside the
campaign count. Stage 4.7 required one local security review repair and
one native dispatch.

No new dependencies, installations, paid services or production resources
were used. Ordinary CI and native acceptance used the existing GitHub
Actions allowance. The owner issued the campaign and resume instructions.
The preauthorized review, publication and native gates governed subsequent
work. [Campaign ledger](campaigns/stage4-progress.md)

## Proposed MasterBrief update

> Stage 4 local hardened acceptance evidence is complete at `34c38d9087b5c34b97fa3d4b68cafc3dc8219e0a`.
> The owner-authenticated native run passed 32/32 markers including the
> live OS-authenticated requester, agent, approver and target path and
> durable SQLite-only effect. Stage 4 owner sign-off is pending. Production
> remains NO-GO.

## Recommended Stage 5 starting point

After owner acceptance, draft a Stage 5 architecture and security contract
for an operator-facing local reconciliation workflow. Start with read-only
inspection and disposition of unresolved dispatch and target records.
Specify operator identity, retention, capacity, backup and recovery, and
the evidence needed to verify those guarantees before adding new effects
or production scope.
