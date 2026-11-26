# Stage 5 local acceptance assessment

**Recommendation — ACCEPT LOCAL STAGE 5 for the synthetic offline scope.**
Formal acceptance is reserved to the owner. **Production remains NO-GO.**

Stage 5 delivers a bounded synthetic evidence projector, an opt-in
redacted diagnostic formatter, a frozen deterministic evaluation, and
in-process reliability tests. The supported question is what the
supplied synthetic evidence establishes about one simulated dispatch.
[Owner authorization](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/30#issuecomment-7202319666)
· [Stage 5.0 architecture](tasks/stage5/5.0-architecture-security-plan.md)
· [Campaign ledger](campaigns/stage5-progress.md)

## Revision and regression evidence

| Package | Merged revision | Reviewed tree | Ordinary CI |
| --- | --- | --- | --- |
| [5.0 architecture, PR #27](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/27) | `fd15fde5569df7a25e2bf848ff3b95605b681199` | `852fbd7fb2ea2d12013c70718721ae24ed118108` | [45833154701](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45833154701), 1,755 passed |
| [5.1 reconciliation, PR #28](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/28) | `3ed9aed1b3cba9237eb67a9dc2e4ceb697412c26` | `bfaffe1a0c345501639040a09633b1c204181e9c` | [45834294258](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45834294258), 1,796 passed |
| [5.2 diagnostics, PR #29](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/29) | `998f5b9659161855d45a0579326bba36bf8c5eb5` | `914c3711a80f7604a4b7a1862dfbb19ebda44089` | [45887675612](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45887675612), 1,839 passed |
| [5.3 evaluation, PR #30](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/30) | `ffdd6ddeede2cbc522936f1316fa8b23806daaa2` | `b41840ce1f3cc330b10121ac06a186462b91c6bb` | [45889456110](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45889456110), 1,840 passed |
| [5.4 reliability, PR #31](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/31) | `1002b9c838fb42041b45f6b73104bc87cc20490f` | `bd4cffd5a0feb84478a3115bd97d9648f9d2c840` | [45890024145](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45890024145), 1,846 passed |

The implementation revision for the owner decision is
`1002b9c838fb42041b45f6b73104bc87cc20490f`. The last ordinary-user
local regression run passed 1,846 tests on Python 3.12.3 with one existing
Starlette deprecation warning. The 5.3 and 5.4 local full runs matched their
respective CI test counts. Report publication carries separate ordinary CI.

## Independent review record

| Package | Reviewer and verdict | Exact reviewed diff SHA-256 |
| --- | --- | --- |
| 5.0 | `/root/stage5_review`, PASS for publication with a nonblocking finding about coverage and no-send evidence. Stage 5.1 defined those rules. | `3d8c74eab96b5e717b959f526b0caaf30b16f11ad526c71e776bb5378f72648f` |
| 5.1 | `/root/stage51_verifier`, PASS after approval, event and capture-order binding corrections. | `15c943227dc3930a673150e57e872ad4d6743469768b394a04e5cc4904d3a2fc` |
| 5.2 | `/root/stage52_review`, PASS after bounded type-check verification and a scoped test-harness repair. | `6c9f1e2d77cf5c2f80453821b281aa08621364ba1cfe2ea3b3bc1dbffca477b0` |
| 5.3 | `/root/campaign_review`, independent pre-score oracle PASS, exact implementation PASS and CI/tree verification. | `164c44bf9febc6dc22fcaaa65119b6a46af92665ad9509a220fc1b271be27914` |
| 5.4 | `/root/campaign_review`, exact pre-implementation contract PASS and implementation PASS. | `63e16f6ef89b40ce100eb5288c6ce5e4563caf106793e7141ef37e8d2631d38a` |

The 5.4 reviewed contract file SHA-256 is
`a47c0ccabaa3c62444c21923999320af937a09eae395bb63c071a939ffd583cf`.
The Stage 5.5 publication records the independent whole-scope review,
the report diff, its CI and its verified tree.

## Reliability results

All six checks passed independently and in the full regression suite.
Each check used fresh synthetic inputs and preserved the source evidence.

| Scenario | Observed result |
| --- | --- |
| R1 injected parser recursion fault | One parser call. Invalid fixture result and no positive diagnostic attribution. |
| R2 injected parse interruption | One parser call. Interruption propagated without a result or retry. A separate clean invocation matched the control. |
| R3 injected serializer interruption | One serialization call. Interruption propagated without a diagnostic or retry. The original result was preserved. |
| R4 modeled unavailable, partial-empty and complete-empty Target | The reservation stayed unresolved. Coverage and missing-row distinctions were preserved across observations. |
| R5 modeled invalid Target and equal-order contradiction | Invalid or inconsistent result. Both suppressed positive effect attribution. |
| R6 byte and row limits | Valid input at 64 KiB matched control. Sixteen duplicate rows yielded inconsistency. Inputs above the size or row cap yielded invalid results. |

R1–R3 actually inject Python exceptions at test seams. R4–R6 cover
modeled source evidence and parser bounds. No storage engine, OS signal
delivery or restart recovery was exercised. The test suite checks the
offline handling of the failure and preservation of supplied evidence.
[Stage 5.4 contract](tasks/stage5/5.4-synthetic-reliability-contract.md)
· [Stage 5.4 scenarios and evidence](campaigns/stage5-progress.md)


## Deterministic evaluation

The v5 regression corpus contains 33 fixtures and retains SHA-256
`3674b2b94b828084d6a47bc7136e1d8272bd9caef058cdebb2fc168b48c193ab`.
It contributed zero cases to the new denominator. The independent
reviewer froze the v1 evaluation oracle before scoring at SHA-256
`7c7341a596ca034ea8f60cfbd932f7108e8c37b8b2af07579211d9cc44d0c755`.
A pre-score correction separated the earlier complete pre-send case
from the partial observation. The scored labels stayed frozen.
Two independent constructions per case on Python 3.12.3 returned
16/16 passes, 13/13 classifications, 13/13 provenance checks and zero
critical failures. The four families returned 4/4 each. The three
formatter-only cases are outside the classification and provenance
denominators. Cases cover equal-order conflicts, corroboration,
unavailability, malformed inputs and diagnostic disclosure.
[Stage 5.3 contract](tasks/stage5/5.3-deterministic-evaluation-contract.md)
· [Independent freeze and scoring record](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/30)

## Security boundary and residual risk

- Approved Stage 4 implementation and workflow files remain at their
  accepted content. Stage 5 adds a separate synthetic Python module
  and verification. Reconciliation imports no live authorization,
  dispatch, storage or IPC code.
- Positive synthetic effect evidence requires corroborating bindings.
  A durable no-send conclusion requires the matching outcome and event.
  Missing, partial or conflicting evidence can never confer execution
  or retry authority. Local diagnostics omit identifiers, exception
  text and private evidence fields and are capped at 256 ASCII bytes.
- Tests verify determinism over the frozen corpus and exercise explicit
  fault seams. They cannot establish completeness across arbitrary
  inputs, live capture correctness, crash atomicity or distributed
  finality. Cross-store provenance and real storage recovery remain
  separate design decisions.
- Stage 5 used **0/4** native dispatches. Local WSL2 and ordinary CI
  provide development evidence. The existing Stage 4 native evidence
  covers its own executable SHA and local scope. Host administrator,
  kernel and the reviewed launch environment remain trusted. Stage 5
  adds no live operator access or identity guarantees.
  [Security rules](../SECURITY.md) · [Stage 4 acceptance](stage4-acceptance.md)

## Proposed MasterBrief update

> Stage 5 synthetic offline work is implemented and independently
> assessed. The deterministic evaluation passed all 16 new frozen
> cases with zero critical failures and the six reliability scenarios
> passed. Stage 5 formal local acceptance is pending. Live evidence
> capture, authenticated inspection and production remain outside scope.

## Owner decision

Accept the completed Stage 5 synthetic offline scope at the verified
implementation revision above. Production remains NO-GO. Any live
evidence capture, authenticated inspection or privileged security
validation requires separately approved scope.
