# Stage 4 campaign progress ledger

Checkpoint date: 2026-11-25 (Asia/Jerusalem).
Verified starting `main`: `989b98424c99ae3be947bae5a34661cce86c28a5`.
Latest verified `main`: `6d618afd6bd33328ef9ecda0a99e023a86afaa8b`.
[Campaign contract](stage4-autonomous-completion.md) ·
[ADR-0001](../decisions/ADR-0001-stage4-identity-boundaries.md)

## Verified work and pending gates

| Package | State | Exact evidence and next action |
| --- | --- | --- |
| Stage 4.1 native IPC | Accepted | Owner accepted the isolated native IPC scope at `de5d9dbfb8db9ccb7a546892f4046010dfc3b9da`. [Acceptance record](../stage4-native-acceptance.md) · [Native run 45403991684](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45403991684) |
| Stage 4.2 scoped delegation | Verified, owner acceptance pending | Native run `45609096357` at `044c112a8e8e684cb516d1f4c0146da0c748d97b` passed 11/11 markers. [Native run and job evidence](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45609096357/job/139187627419) · [Stage 4.2 contract](../tasks/stage4/4.2-scoped-delegation-contract.md) |
| Stage 4.3 operator and lifecycle | Verified, owner acceptance pending | PR #17 reviewed head `dd4242a5beab0543fd291e90f4b3f4209bb0e9c2` merged as `989b98424c99ae3be947bae5a34661cce86c28a5`. CI passed 1,614 tests. Native run `45614418616` stopped during Stage 4.2 dependency loading after 5/17 markers. The failure did not demonstrate an authorization defect. Campaign run `45623922695` succeeded on the corrected tree with 17/17 markers, each once, and independently verified PASS. [PR #17](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/17) · [CI 45612233695](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45612233695) · [Failed native job](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45614418616/job/139206017985) |
| Stage 4.3 harness repair | Merged, native evidence verified | PR #18 reviewed head `28a578d9a861465bba3d8ffc7cc0adf91e331d74` merged as `b2fd3a4eb013c40b5238798a78c0a31c66000d00` with unchanged reviewed tree `9ac3ca8f257222d76d1194516f89fc95097e8d6e`. Independent review PASS, no open findings, reviewed diff SHA-256 `9c87cfc0708f63987adbbbdbf3e3ca4d1cf3f4eb5fb19492202b3f95c51bbd49`. CI passed 1,623 tests. Native dispatch with `include_stage43=true` succeeded against the exact reviewed merge. Independent evidence review PASS. [PR #18](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/18) · [CI 45615365922](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45615365922) · [17-marker plan](../stage43-native-acceptance.md) |
| Stage 4.4 protected Controller state | Verified, owner acceptance pending | Reviewed head `6c53474b31ccb32fd061a1bd84877af7e645b070` merged as `e5ea24b7a4954d76e2de3a2c57b6eac8a6b0e943` with exact tested tree `9957c411e7efc2b97dc07e862089c8c4f7698cb6`. Independent review PASS, 311 focused and 1,656 full local tests; ordinary CI 1,656 passed. [PR #20](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/20) · [CI 45626541124](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45626541124) · [Native run 45626930749](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45626930749) · [Work-package contract](../tasks/stage4/4.4-protected-controller-state-contract.md) · [Native plan](../stage44-native-acceptance.md) |
| Stage 4.5 OS-bound dispatch | Verified, owner acceptance pending | PR #21 merged as `6d618afd6bd33328ef9ecda0a99e023a86afaa8b`. Ordinary CI and independent review passed. Native dispatch 3 succeeded with 25/25 markers and independent evidence PASS. [PR #21](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/21) · [CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45681017591) · [Native job](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45681467288/job/139442818846) · [Work-package contract](../tasks/stage4/4.5-os-bound-dispatch-contract.md) · [Native plan](../stage45-native-acceptance.md) |
| Stage 4.6 durable replay and effects | In progress | The package starts at the verified Stage 4.5 merge. [Work-package contract](../tasks/stage4/4.6-durable-target-replay-contract.md) · [Native plan](../stage46-native-acceptance.md) |
| Stage 4.7 hardened local acceptance | Planned | Obtain end-to-end evidence and an independent D01–D10 assessment. |

## Resume from this checkpoint

- Campaign native dispatch 1 ran on the exact independently reviewed PR #18
  merge tree at `b2fd3a4eb013c40b5238798a78c0a31c66000d00`, attempt 1 with owner
  `Iliya-Tsvibel` as both actors, `include_stage43=true`, hosted Ubuntu 24.04.5
  and read-only token. Job `139239013398` succeeded in 58 seconds. The log
  contains 5/5 Stage 4.1, 6/6 Stage 4.2 and 6/6 Stage 4.3 markers, each once
  with zero unexpected or duplicate markers. Independent evidence review returned PASS.
  [Native run 45623922695](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45623922695)
  · [Job evidence](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45623922695/job/139239013398)
- Governance PR #19 merged as `d711a67f3b6bd095528f2bcd90d3e0aec2191c04` after
  independent review and ordinary CI passed 1,627 tests. The merged tree
  matched the reviewed and tested candidate. The isolated Stage 4.4 branch
  `feat/stage44-protected-controller-state` starts at that exact base.
  [PR #19](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/19)
  · [Passing CI 45624560662](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45624560662)
- Campaign native dispatch 2 ran on exact PR #20 merge
  `e5ea24b7a4954d76e2de3a2c57b6eac8a6b0e943`, attempt 1, owner
  `Iliya-Tsvibel` as both actors, hosted Ubuntu 24.04.5, read-only token, and
  both `include_stage43=true` and `include_stage44=true`. Job `139249491611`
  succeeded in 69 seconds. Logs contain 21/21 markers exactly once without
  duplicates or unexpected marks. Independent evidence review returned PASS.
  [Native job](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45626930749/job/139249491611)
- Campaign native dispatch 3 ran on exact PR #21 merge
  `6d618afd6bd33328ef9ecda0a99e023a86afaa8b`, attempt 1, owner
  `Iliya-Tsvibel` as actor and triggering actor, on hosted Ubuntu 24.04.5
  with a read-only token and Stage 4.3 through 4.5 selected. Job
  `139442818846` succeeded in 84 seconds with 25/25 markers exactly once.
  Independent evidence review returned PASS. The slice used synthetic
  approval and acknowledged dispatch without a target effect.
  [PR checkpoint](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/21)
  · [Native run](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45681467288)
- The prior Stage 4.3 failure was a shared-object mapping failure on `/run`
  before the Stage 4.2 listener was ready. A noexec-like restriction fits
  the error. The failed run did not capture flags needed to confirm it.
- Before dispatch, verify the actual owner identity, native workflow version
  and constraints, included compute under the established zero-spend policy,
  and the remote run list. Count each fresh dispatch conservatively and never
  rerun the unchanged failing revision.
- Governance CI run `45623579277` failed because the docs doctor's
  copied-document fixture omitted the new campaign link targets. The first
  repair registers those documents and their transitive Stage 4.3 sources
  and adds regressions for missing documents and required links. Renewed
  independent review and repaired CI passed. [Initial governance CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45623579277)
- Campaign usage at this checkpoint is **3/8 native dispatches**. At least **4 ordinary
  CI runs** are recorded, with **1 governance repair cycle**, and **0 escalations**.
  Independent checks completed include PR #18 merge-gate reverification,
  both governance reviews, the Stage 4.3 native evidence review and the
  Stage 4.4 exact-diff security and native evidence reviews.
  PR #18's original implementation, CI and initial correction review are
  pre-campaign evidence.
- Reconcile remote refs, PRs, reviewed diffs, CI and native jobs before taking
  action. Persist the next checkpoint after each merge and native run. Record a
  context or usage interruption as such and resume from the last verified gate.

Production remains NO-GO. Formal owner acceptance remains outstanding for
Stage 4.2 and later milestones.
