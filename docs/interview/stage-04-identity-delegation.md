# Stage 4 — OS Identity, Delegation, Approval, Dispatch and Durable Replay

**Owner status: ACCEPTED — LOCAL SCOPE**, 26 Nov 2026. **Production: NO-GO.**

- Tested executable SHA: `34c38d9087b5c34b97fa3d4b68cafc3dc8219e0a`.
- Report publication SHA: `135eecc57abcca4c39f6e4d3342576d5ce3eda1c` (not the tested binary revision).
- Accepted ADR: D01–D10.
- Final [native run 45794902062](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45794902062): **32/32 distinct PASS markers**, ordinary CI **1,755 tests**.
- [Formal owner sign-off](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/24#issuecomment-7188763211).

## Why Stage 4 was necessary

Earlier Python `RunContext` labels, an in-process policy gate and a separate operator CLI were useful but insufficient against hostile local processes. They did not authenticate Linux workloads or stop direct requests to the legacy unauthenticated mock HTTP Target. The design aim was to harden **a single, constrained local authority path**, without building a generic production IAM service.

## The first architecture branch: decisions and trade-offs

We explored signed artifacts/JWT and more complex identity approaches. For one native Linux host, we chose distinct service UIDs, protected Unix Domain Sockets (UDS) and *mutual* `SO_PEERCRED` verification. A server-owned principal mapping ties the kernel-authenticated UID to the actual service role. The Controller stores bounded grants rather than trusting an agent-carried bearer credential.

**WHY this choice?** Fewer cryptographic secrets, signing keys, issuer and revocation services for a local demo. **Trade-off:** Linux-host specificity, trusted kernel/admin and launch configuration, plus no isolation between processes with the same UID. OIDC, SPIFFE and cloud workload identity may become relevant later, not in this accepted local architecture.

## Architectural glossary

| Object | Question answered | What it does **not** prove |
| --- | --- | --- |
| Linux UID / `SO_PEERCRED` | Which OS principal is on the socket? | Physical person, MFA, remote workload identity |
| Scoped grant | Which agent may propose what, for which resource, until when? | Right to execute a risky mutation |
| PDP decision | Is this proposal allowed/denied/approval-gated now? | Approval or confirmed Target effect |
| Pending action | Which exact immutable action is proposed? | Human authorization |
| Approval | Did a configured approver agree to this action ID/fingerprint? | That the action has executed |
| Reservation | Was the right to make **one Controller attempt** consumed? | That bytes reached Target |
| Target replay claim | Has this exact request already claimed its effect opportunity? | That an effect definitely occurred |
| Target receipt | What correlated simulated effect was committed? | Exactly-once arbitrary remote effects |
| Audit | Which state transitions/decisions are recorded? | Tamper-proof third-party attestation |

## Stage 4 work packages: full history

### Stage 4.1 — Authenticated UDS IPC (5/5 native)

Separate Agent, Controller, Target Linux UIDs; protect socket paths (ownership, mode, ACL, no dangerous existing entries), authenticate *both directions* using kernel `SO_PEERCRED`; deny direct Agent→Target. Reject wrong-UID peers and listeners before sending privileged data. **No delegation or mutation yet.** Native Ubuntu rather than WSL2 was required to prove this specific boundary.

### Stage 4.2 — Scoped proposal admission (11/11 cumulative)

A trusted fixture preissued a session-only, narrowly scoped grant for `mock.get_health(demo-api, local-mock)`. Controller checked authenticated agent UID, grant, scope, expiry, PDP and journal *before* sending a positive verdict. Input frames could not supply a trusted role, run or grant. The verdict itself was **not** a Target operation or a dispatch permit.

### Stage 4.3 — Operator IPC / delegation lifecycle (17/17)

Distinct operator socket; configured requester can issue/inspect/revoke its own bounded grant; configured approver-only principal does not gain requester powers. One serialized Controller protected the ordering of revoke and proposal decisions. A revoked grant blocks *new* authority, but already-sent reply bytes cannot be recalled. Initial native execution failed when loading a `pydantic_core` shared library staged on `/run`: `failed to map segment from shared object`. The verified harness correction kept sockets under `/run`, placed executable dependencies under protected `/var/lib` and added an unprivileged loading preflight. This was **setup failure**, not demonstrated authorization bypass.

### Stage 4.4 — Protected Controller state (21/21)

Dedicated Controller-owned SQLite directory/file (`0700`/`0600`), ancestor/path/type/symlink/ACL checks, exclusive directory `flock`, expected schema and integrity on startup. Grant issuance, revoke and audit transitions were durably committed. Controller restarts invalidate old live authority and create fresh sessions; historical evidence is retained. Storage errors or ambiguous commits cannot turn into positive grants. This SQLite model trusts the host administrator, underlying filesystem and same-UID processes.

### Stage 4.5 — OS-bound dispatch (25/25)

Controller authenticates Target **before** reservation. It rechecks session, role, grant, action fingerprint, approval, PDP, endpoint and deadline. In one transaction, approval consumption + unique reservation + pre-dispatch audit are confirmed. An in-memory one-use permit bound to the live authenticated connection is consumed before **one send**. Stored reservation rows never reconstruct a send right. The Stage 4.5 test Target only acknowledged the correctly bound envelope; **it had no effect yet**.

### Stage 4.6 — Durable Target replay and SQLite-only effect (28/28)

Target independently verifies Controller UID and canonical envelope, then first *durably claims* a unique request bound to dispatch/action IDs and digest. After a confirmed claim, the live caller may enter a second transaction that rechecks expiry and atomically increments a **simulated SQLite counter** and stores correlated receipt. A lost receipt leaves Controller outcome unknown; duplicate mutation messages or restarted Target cannot consume that effect opportunity twice. A claim committed before a crash may have *no effect* but remains spent—**safety over availability**.

### Stage 4.7 — End-to-end live OS-principal approval (32/32)

The test replaced previous synthetic approval fixture with an authenticated requester→agent→approver→Controller→Target path. The configured approver inspected the exact pending action and fingerprint, explicitly approved and separately requested dispatch. Controller consumed approval/reserved/sent under correct state; Target committed a single SQLite effect. In an interrupted path Target retained a claim, Controller kept `UNKNOWN`, and a second dispatch was denied. This proves a **mapped OS principal**, not a physically present human or MFA.

The independent reviewer found a **Medium** expiry/acknowledgement edge case; the implementation was repaired and re-reviewed. The final native runner and regression suite validated the reviewed exact SHA. Campaign used **5 of 8** authorized fresh native runs; leftover authorization is not available to Stage 5.

## One complete request — explain on a whiteboard

1. A verified requester UID issues a bounded scoped grant (not an approval).
2. Agent UID sends strict `mock.restart_service` proposal for exactly `demo-api`/`local-mock`.
3. Controller checks kernel-bound agent identity, frozen principal/run, active grant, scope and current PDP `APPROVAL_REQUIRED`.
4. Controller commits immutable pending action, fingerprint and decision evidence. No Target effect.
5. Distinct approver UID inspects scope, approves exact action ID/fingerprint and separately requests dispatch.
6. Controller authenticates Target first. It rechecks current session, scope, approval, grant, expiration, policy and endpoint.
7. Atomic SQLite `BEGIN IMMEDIATE`: consume exact approval, create unique reservation and append required audit. Ambiguous COMMIT yields **no send permit**.
8. One process-local permit is burned for one authenticated Target send, not reconstructed later.
9. Target authenticates Controller, checks canonical request/time/digest and records durable replay claim.
10. Target atomically commits SQLite-only simulated counter increment and receipt, or retains unresolved claim if interrupted.
11. Controller records correlated receipt or `UNKNOWN`. Read-only receipt lookup can inspect evidence but cannot authorize another mutation.

## FAILURE MODE table

| Failure | Safe behavior | Why |
| --- | --- | --- |
| Wrong UID or forged JSON role | DENY, zero privileged bytes to wrong server | Kernel identity overrides caller claims |
| Grant expired or revoked | Deny new proposal/dispatch authority | Previously sent bytes can't be retracted |
| Different action fingerprint | DENY | Approval is action-specific |
| PDP or required audit unavailable | Fail closed before send | No implicit authority on component failure |
| Ambiguous approval/reservation COMMIT | No positive permit | COMMIT error does not prove rollback |
| Crash after reservation before send | Lose attempt rather than retry | No way to prove zero send |
| Target claim commit then crash | Retain claim, don't resume | Potentially consumed effect opportunity |
| Effect commits, reply disappears | `UNKNOWN`, read-only investigation | Timeout isn't proof of non-effect |
| Replay after Target restart | Durable claim denies second effect | Target-side replay prevention |
| State corrupted or capacity full | Fail closed | No silent evidence loss |

## Security boundaries / production gaps

**Accepted:** local, protected Linux host and launch profile with distinct service UIDs; simulated SQLite-only effects; reviewed code and native runner. **Not accepted:** hostile host administrator, processes sharing an authorized UID, physical human identity, MFA, OIDC/SPIFFE remote federation, tamper-proof audit, distributed transaction, real Kubernetes restart, multi-host availability, general external exactly-once, complete Incident Investigator PRD, arbitrary production deployment.

## WHY / HOW / TRADE-OFF answers

**Why UDS and `SO_PEERCRED` instead of JWT?** Local kernel-authenticated peer identity removes bearer token custody from this slice. It is not a general replacement for distributed workload identity.

**Why authenticate both ends?** A client could connect to a fake Control Plane; a server could receive forged identity payloads. Mutually binding UIDs prevents accepting those peers under the trusted local threat model.

**Why grants and approvals separately?** A grant permits *proposal admission* in a scoped session; an approval permits *one immutable action*, under an independent configured approver.

**Why two Target transactions?** The claim spends the operation's replay opportunity first. Then simulated effect and receipt commit together. A crash between them may lose availability but prevents unsafe automatic duplication.

**What does at-most-one mean exactly?** The Controller makes at most one supported application dispatch attempt per action. It does not imply one target execution in every network/distributed architecture. Target-side SQLite claim adds deduplication within its local, simulated transaction model.

**Why Stage 5 starts with reconciliation?** It is safer to inspect durable unknown evidence read-only than introduce retries, deletion, or broader execution rights.

## 60-second English pitch

> I designed and accepted a local Secure AI Agent Control Plane where agent output is untrusted intent, never authority. Linux service UIDs and mutually authenticated Unix sockets establish local workload identity. The Controller binds narrowly scoped delegation, a deterministic PDP, a distinct OS-authenticated approver and an atomically consumed dispatch reservation. The Target independently verifies the Controller and stores durable replay claims and SQLite-only effect receipts. We tested wrong principals, expiry, crashes and replay on a native Linux runner with 32 cumulative PASS markers and 1,755 passing regression tests. It is deliberately not a production or exactly-once external-effects claim.

## Sources

[ADR-0001](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/decisions/ADR-0001-stage4-identity-boundaries.md), [Stage 4 acceptance report](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/stage4-acceptance.md), [Stage 4 campaign ledger](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/campaigns/stage4-progress.md), [native run](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45794902062), [target-state tests](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/tests/test_stage46_target_state.py), [end-to-end tests](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/tests/test_stage47_hardened.py).
