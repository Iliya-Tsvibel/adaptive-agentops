# Stage 6.E progress

Owner-authorized local nonproduction campaign. **Production NO-GO.**
Starting main verified through GitHub at
`7ba7569a35f2be94dd10d8ab3a5f3a5144c31334`.

| Package | Status |
| --- | --- |
| 6.E.0 | [Architecture proposal](../decisions/ADR-0002-stage6-evidence-bridge.md). Merged with independent architecture review. |
| 6.E.1–3 | Merged after exact-diff repair review. |
| 6.E.4 | Operator demo and native harness. |
| Native and technical assessment | Pending reviewed implementation. 0/8 authorized native runs used. |

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
dispatch, job and marker evidence. Native use remains 0/8 at this checkpoint.

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
