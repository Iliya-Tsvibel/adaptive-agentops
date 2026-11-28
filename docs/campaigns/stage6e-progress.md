# Stage 6.E progress

**Owner decision, 2026-11-28 — Stage 6.E ACCEPTED and CLOSED for the
documented native Linux, local, nonproduction scope. Production NO-GO.**

The owner communicated the acceptance directly outside GitHub. The accepted
runtime is `1066bfde6f3af6e7d2356e2c1a88dd2a15a9aa74`. The final assessed
main is `0d80247b82174cd2b8ae45a83390fd5def152eb0`. The decision cites
7/7 Stage 6.E native markers, 32/32 cumulative Stage 4 markers, 1,898 passing
final CI tests, independent agent review records, and verified source-tree
correspondence. See the [technical assessment](../stage6e-acceptance.md).

Acceptance covers authenticated, scoped, read-only inspection of one
historical simulated dispatch, provenance-aware conservative attribution,
preserved UNKNOWN and replay semantics, and durable audit-before-disclosure.
Production readiness, external exactly-once effects, multi-tenancy, remote
identity, tamper-resistant audit, container portability, and real recovery
guarantees remain outside the accepted scope.

The native campaign closed at **2/8** runs. All six unused authorizations
expired and confer no execution authority. **Stage 6.D Docker Security
Portability is PROPOSED / NOT AUTHORIZED.** Its architecture decision,
implementation, and any new privileged campaign require separate owner
authorization.

## Campaign history

Starting main verified through GitHub at
`7ba7569a35f2be94dd10d8ab3a5f3a5144c31334`.

| Package | Status |
| --- | --- |
| 6.E.0 | [Architecture proposal](../decisions/ADR-0002-stage6-evidence-bridge.md). Merged with independent architecture review. |
| 6.E.1–3 | Merged after exact-diff repair review. |
| 6.E.4 | Operator demo and native harness. |
| Native and technical assessment | 7/7 Stage 6.E markers and 32/32 cumulative Stage 4 markers. 2/8 native runs used. See the owner decision above. |

## 6.E.0 verification

[PR #38](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/38) merged as
`6a8062a081e2cd560e67a29764e74d1b2d25ef59` with independently reviewed and
CI-tested tree `bc74743ca308459c18059edb24c877dcf0f84232`. The exact
review is preserved in the PR. [CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46002807782)
passed 1,865 tests. The review cleared security implementation within the
ADR scope.

## 6.E.1–3 verification

[PR #39](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/39) merged as
`66cfe2c502915e68efa06df535f8b06305802af7`. Exact reviewed and CI-tested tree
`07b229df352ae99cc83674934fd3b2edef162b63`. The PR preserves two independent
review revisions and the disposition of three Medium contract findings.
Builder focused tests passed 30. Full local pytest and
[ordinary CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46003867103)
each passed 1,895. The CI checked out synthetic PR merge
`de4826d550416eb5da4751f17edf71511a9fe6b4` with the same tree.

## 6.E.4 and native preparation

The [operator demo](../tasks/stage6e/operator-demo.md) and
[customer objections](../portfolio/customer-objections.md) record the
workflow and limits. Native acceptance requires its own exact-revision
dispatch, job and marker evidence. Native use was 0/8 at this checkpoint.

[PR #40](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/40) merged as
`3ed5f3c9c567481fd5dd1a0ce8715ab088a1bc0c`, tree
`d1ff2e8a3f7e8dd6bc5d0fb9ad88aa331431da8b`. [Ordinary CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46004622836)
passed 1,895. The exact independent review and repair are in the PR.

Native run 1 of 8, [46004919686](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46004919686/job/140525572155),
ran on that exact revision with owner actor and attempt 1. Setup and all 32
cumulative Stage 4 markers passed. The Stage 6.E lost-acknowledgement fixture
stopped before any new marker. Its injected OSError crossed a state lease
that wraps OSError, so the probe did not catch the modeled acknowledgment
loss. The run failed with exit 79. Count it as a run and no Stage 6.E
acceptance evidence. The repair is confined to the disposable probe.

[PR #41](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/41) merged
the independently reviewed fixture repair as
`1066bfde6f3af6e7d2356e2c1a88dd2a15a9aa74`, shared reviewed and CI-tested
tree `faeb68fc4041a8ed798cc774186e331c50f7e919`.
[Ordinary CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46005580486)
passed 1,895 tests.

[Native run 2](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46005768880/job/140528417433)
on that exact revision passed **7/7 Stage 6.E** and **32/32 cumulative Stage 4**
markers, all distinct. Usage **2/8**. See the [technical assessment](../stage6e-acceptance.md)
for observed properties, limitations and both native attempts. No additional
paid spending. This run supplied the native evidence for the owner decision above.
