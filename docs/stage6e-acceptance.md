# Stage 6.E technical assessment

**TECHNICALLY COMPLETE — PENDING OWNER ACCEPTANCE. Production NO-GO.**
The owner retains formal Stage 6.E acceptance.

## Scope and architecture

The dedicated Controller maintenance launch holds the existing execution
state lease across the authenticated inspection and disclosure. A separate
Linux UID and socket group, mutual kernel peer checks, and a frozen
dispatch UUID protect the inspection method. The Controller reads the
accepted Controller schema through a read-only SQLite connection in one
local observation and makes at most one existing authenticated Target
receipt lookup. It commits the exact redacted result to a separate
protected audit store before returning it. The inspection path provides
no grant, approval, reservation, dispatch or retry mechanism.

A distinct UID and maintenance socket isolate inspection from the accepted
requester and approver roles. Controller mediation keeps authenticated
Target access and scope binding inside the trusted service. Each store is
observed independently. The bridge uses a typed live projection and keeps
the synthetic Stage 5 fixtures, capture labels and order outside trusted
inputs. These choices require execution to pause during maintenance and
support only one explicitly configured dispatch per launch.

[ADR-0002](decisions/ADR-0002-stage6-evidence-bridge.md) ·
[Inspection contract](tasks/stage6e/inspection-contract.md) ·
[Operator workflow](tasks/stage6e/operator-demo.md)

## Repository and review evidence

Starting main was `7ba7569a35f2be94dd10d8ab3a5f3a5144c31334`.

| Package | Reviewed head | Shared reviewed, CI-tested and merged tree | Merged SHA | Ordinary CI |
| --- | --- | --- | --- | --- |
| [6.E.0 architecture](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/38) | `87e30905faade1a7e75874dcb34e1c4be3d89d4e` | `bc74743ca308459c18059edb24c877dcf0f84232` | `6a8062a081e2cd560e67a29764e74d1b2d25ef59` | [1,865 passed](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46002807782) |
| [6.E.1–3 inspection and acquisition](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/39) | `ae4d4602a87400c3fe13b8ed92675e8d3ac7b1d9` | `07b229df352ae99cc83674934fd3b2edef162b63` | `66cfe2c502915e68efa06df535f8b06305802af7` | [1,895 passed](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46003867103) |
| [6.E.4 demo and native harness](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/40) | `b018e5b00b2a84c897a4675b9f5562a45dbf74f9` | `d1ff2e8a3f7e8dd6bc5d0fb9ad88aa331431da8b` | `3ed5f3c9c567481fd5dd1a0ce8715ab088a1bc0c` | [1,895 passed](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46004622836) |
| [Native probe repair](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/41) | `bf8a72162f89e5a38f3410122e9f5213b4fdafcc` | `faeb68fc4041a8ed798cc774186e331c50f7e919` | `1066bfde6f3af6e7d2356e2c1a88dd2a15a9aa74` | [1,895 passed](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46005580486) |

The attributed review revisions and diff fingerprints are in the linked PRs.
Separate agent review was read-only within the shared sandbox. Three Medium
inspection findings were repaired and independently cleared. Native harness
review identified and cleared stale fixture socket handling and an explicit
SHA gate before elevation. No unresolved Critical, High or contract-blocking
Medium findings remained after those reviews.

Ordinary Stage 6.E tests cover peer/method/scope denial, correlated receipt
keys, historical approval binding, complete shared IPC deadline, Target
unavailability, audit commit and modeled ambiguous commit. Supplemental
close-out checks model capture interruption and audit capacity, and exercise
a real invalid SQLite input file. Corrupt and negative paths emit no raw
source content. These are deterministic development checks.

The final evidence package added three failure regressions and stabilized
the historical fixture clock without touching runtime. Builder focused
verification passed 184 tests, including Stage 5 and native harness unit
tests. Full ordinary local pytest passed 1,898 with one existing Starlette
warning. The 33 regression fixtures and 16 frozen evaluations retained
their original hashes and labels.

## Native Linux acceptance

| Run | Reviewed main revision | Outcome |
| --- | --- | --- |
| [1 · 46004919686](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46004919686/job/140525572155) | `3ed5f3c9c567481fd5dd1a0ce8715ab088a1bc0c` | Setup succeeded. 32 Stage 4 markers, zero Stage 6.E markers. Probe exited 79. The truncated inner trace and source indicate the OSError-based test seam was translated by the existing Target state lease. PR #41 repaired the fixture only. |
| [2 · 46005768880](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46005768880/job/140528417433) | `1066bfde6f3af6e7d2356e2c1a88dd2a15a9aa74` | Passed 7/7 Stage 6.E and 32/32 cumulative Stage 4 markers. Each marker appeared exactly once. |

Run 2 was a disposable Ubuntu native workflow, attempt 1, with
`Iliya-Tsvibel` as both actor and triggering actor. The checkout and
privileged staging were bound to the reviewed revision. The markers cover:

1. A real SQLite effect with a deliberately suppressed acknowledgement,
   later read through authenticated Target IPC while Controller UNKNOWN
   remains in the protected history.
2. Abrupt Target exit after a real claim commit, restart and conservative
   claim-only and unavailable results.
3. Reachable wrong-UID agent, requester and approver denial, and exact
   dispatch scope.
4. Protected database access, exclusive Controller lease and mutation-frame
   denial.
5. Durable audit gating, a modeled ambiguous commit with no disclosure,
   and unchanged Controller and Target database bytes.
6. Unsafe Target socket metadata suppressing positive attribution.
7. A wrong-UID server receiving no inspector request bytes even when the
   socket entry matches the expected metadata.

The suite exercised real OS and SQLite boundaries. Lost acknowledgement
and ambiguous audit were deliberate seam injections. They do not establish
production crash recovery or tamper-resistant persistence. Campaign use
was **2/8** authorized fresh native dispatches and **$0 additional spend**.
The native runtime and harness are identical in the assessment package.

## Scope limits

The two stores have no synchronized snapshot or completeness guarantee.
Target UNKNOWN intentionally covers a nonmatching or absent claim and a
claim without a committed receipt. Historical Controller UNKNOWN remains
unchanged after a later committed Target receipt. The receipt describes
one local simulated SQLite effect; an inspection result cannot authorize
another attempt.

Residual limitations include the trusted host kernel, administrator and
service UIDs, bounded audit capacity with no automatic retention policy,
the exclusive maintenance lease, no guarantee of reply delivery after audit,
and no proof of physical human presence or tamper-resistant storage. Fault
injection and the disposable demo supply no production crash recovery
guarantee. Multi-tenancy, remote identity, real external effects, live
deployment, container portability, Kubernetes and GitOps remain gated.

[Campaign ledger](campaigns/stage6e-progress.md) ·
[Customer objections and evidence](portfolio/customer-objections.md)
