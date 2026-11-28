# Stage 6 technical assessment

**TECHNICALLY COMPLETE FOR NARROWED SCOPE — PENDING OWNER ACCEPTANCE.
Production NO-GO.**

Recommend owner acceptance of the tested local nonproduction Docker Agent
profile and the explicit Kubernetes and GitOps deferrals. The original
Docker/Kubernetes/GitOps roadmap remains broader than the demonstrated
deployment. The owner decides whether the narrowed scope closes Stage 6.

## Scope and architecture

| Track | Status | Evidence |
| --- | --- | --- |
| Stage 4 native Linux Control Plane | ACCEPTED historical | [Owner acceptance and 32 native markers](stage4-acceptance.md) at executable `34c38d9087b5c34b97fa3d4b68cafc3dc8219e0a` |
| Stage 5 synthetic offline reconciliation | ACCEPTED historical | [Owner acceptance](stage5-acceptance.md) at `1002b9c838fb42041b45f6b73104bc87cc20490f` |
| Stage 6.E Trusted Evidence Bridge | ACCEPTED historical | [Owner decision](campaigns/stage6e-progress.md) at runtime `1066bfde6f3af6e7d2356e2c1a88dd2a15a9aa74` |
| Docker Agent with native trusted peers | TESTED, owner acceptance pending | [ADR-0003](decisions/ADR-0003-stage6d-docker-portability.md) and [exact-revision assessment](stage6d-technical-assessment.md) |
| Direct Agent Pod/native lift and cross-node UDS | REJECTED under current protocol | [ADR-0004](decisions/ADR-0004-stage6-kubernetes-topology.md) |
| Single-node co-located Kubernetes workload | DEFERRED, candidate only | [Feasibility and revisit gates](decisions/ADR-0004-stage6-kubernetes-topology.md) |
| Runtime GitOps | DEFERRED | [Authority and convergence decision](decisions/stage6-gitops-disposition.md) |

The Docker Agent reaches the native Controller's proposal socket. The
native requester issues scoped grants and the separate native approver
authorizes the exact action. The Controller reserves dispatch atomically
with approval consumption and contacts the native Target over a separate
authenticated socket. The Target and Controller own separate protected
stores. The native inspector uses the Controller's separate maintenance
socket under an exclusive lease. The Controller commits disclosure audit
before returning inspection evidence. The principals share one private
PID namespace and retain their configured UIDs. The Agent has its own
mount and network namespaces. The rootful Docker daemon, host administrator,
kernel and privileged provisioning remain trusted.

The independent read-only architecture reviewer supported retaining this
profile without application or launcher changes. Positive peer PID and
UID checks continue to use kernel identity. Docker's default separate PID
namespace hides the native Controller PID. Host PID sharing was rejected
for this profile. Rootless and remapped deployments would require a
separately validated credential and storage mapping. Image digest and
wheel hash pins support reproducibility. Publisher attestation is untested.

## Security evidence and gaps

| Concern | Evidence or disposition |
| --- | --- |
| Wrong Agent UID and wrong server with matching metadata | TESTED in Docker. The reachable wrong UID received no verdict and the wrong server received zero proposal bytes. |
| Direct Target, operator and inspector reachability and store access | TESTED as Docker path or store denials, with a live operator socket. Live Target and inspector role checks were exercised separately in the native harness. |
| Forged input, requester requesting approval, inspector requesting execution | Native and deterministic evidence in the accepted contracts. No additional Docker-specific role-frame coverage is claimed. |
| UID map, capabilities, mounts, daemon socket and PID namespace | The supported launch checked its effective configuration at runtime. Deliberately incompatible maps, excess capabilities, exposed daemon socket, accidental shared mount, and holder death were NOT TESTED as separate experiments. |
| Reservation failure and uncertain dispatch | Accepted deterministic fault and native fixture evidence preserve atomic reservation and fail-closed UNKNOWN. A lost acknowledgement and a retained claim without a receipt were exercised in native Linux. Automatic retry remains prohibited. |
| Unavailable stores and audit ambiguity | Existing failure evidence preserves uncertainty or suppresses disclosure. The native inspection test exercised audit-before-disclosure and a modeled ambiguous audit commit. Physical recovery and tamper-resistant storage remain unproven. |
| Volume rollback, Kubernetes replacement and ServiceAccount misconfiguration, GitOps security-boundary edits | NOT TESTED. Kubernetes and GitOps are deferred. A structurally valid old database could lack a later replay claim, so workload/storage identity alone is insufficient. |

Docker replacement reuses the configured Agent identity under a fresh
grant. It restores no previous execution authority. The retained native
receipt still represented one simulated SQLite effect. Controller and
Target persistence was native throughout. No Target container replacement,
distributed storage recovery, or external exactly-once effect was
demonstrated. See the [Docker job](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46133235506/job/140942822779).

## Reliability investigation

The preceding Stage 6.D campaign recorded three 1,897-pass/one-failure
local runs and successful isolated reruns. This campaign used the
unchanged source and existing unprivileged Python 3.12 environment under
WSL2. All three exact cases passed in ten fresh pytest processes in the
original order and three more in reverse order, for 39 passing case
invocations. The ordinary full suite with loopback available then yielded
1,898 passes and one existing Starlette warning. There was no recurrence
or dependency on the two tested case orders.

| Case | Prior failure and code inspection |
| --- | --- |
| `test_stage45_dispatch_state.py::test_wrong_persisted_bindings_never_authorize_a_send[cross-action]` | The prior trace stopped in the production clock guard before the state assertion. The guard checks regressions and wall/elapsed divergence. The underlying workstation clock condition was not established. |
| `test_stage47_hardened.py::test_grant_expiring_after_confirmed_approval_commit_suppresses_reply` | The prior trace rejected approval before the intended expiry seam. The test snapshots a simulated session wall clock while approval uses a separate real UTC source. A disagreement is plausible, but the original timing was not captured. |
| `test_dispatch_control.py::test_interruption_with_cleanup_failure[True-CancelledError]` | The prior test did not receive the injected exception. Reservation validates clock-sensitive prerequisites before reaching the injection point. The reason that historical run missed the injection remains unresolved. |

Reproduce with the existing Python 3.12 virtual environment and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=. python -m pytest -q --tb=short`.
For the focused loop, pass the three exact node IDs above and start a
fresh pytest process per iteration. This investigation establishes no
root cause for the workstation events. Port binding, filesystem timing,
cleanup, and temp state were not identified as causes. Retain the local
flake as a nonblocking risk. No assertions, sleeps or runtime clocks
were changed.

## GitHub and spending

Starting main was `4474ff0758e412d15075fae13b8d32aec181bba5`.
The historical implementation [PR #44](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/44)
and assessment [PR #45](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/45)
are merged. The [Stage 6.D assessment](stage6d-technical-assessment.md)
preserves the reviewed/tested/merged implementation tree and CI references.
Historical Docker evidence at implementation
`af410f24e353b466657006fb5d14a92125ee7382` is 3/3 Docker,
32/32 distinct Stage 4 and 7/7 Stage 6.E markers. This completion campaign
added Markdown only. It used **0/8 new privileged runs**, added no
infrastructure or dependencies, and incurred **$0 additional spend**.
There is no Kubernetes or GitOps runtime evidence. The closeout PR records
its independent exact-diff review, ordinary CI and merge verification.

## Proposed MasterBrief delta

Record Stage 6 as technically complete for the tested local nonproduction
Docker Agent hybrid, pending the owner's final narrowed-scope decision.
Kubernetes and runtime GitOps are deferred. Retain Stage 4/5 and Stage 6.E
history, the exact Stage 6.D runtime and evidence, and Production NO-GO.

Prioritize a separate storage-generation and recovery design before another
orchestration experiment. It should make rollback, lost state, and
operator-led recovery explicit while preserving spent authority and
UNKNOWN. This proposal starts no new stage or execution campaign.
