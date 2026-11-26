# Stage 5 bounded autonomous engineering campaign — v0.1

**Owner authorization supplied in the Stage 5 kickoff, 2026-11-26.** **Architecture proposal NOT YET APPROVED.** This document records a narrow local, nonproduction execution allowance; it cannot grant actual GitHub/OS permissions and does not adopt the proposed Stage 5.1 interface. Production remains **NO-GO**.

## Scope and exclusions

The owner authorized preparation and review of Stage 4 documentation closure, Stage 5.0 architecture/security proposal and campaign ledger, then bounded Stage 5.1–5.5 implementation *after* the architecture gate. Stage 5 extends the accepted secure local Controller/Target. Stage 4 D01–D10 remains in force. Original Stage 0–9 sequence stays intact. Do not resume rejected JWT/bearer authority or introduce new agent execution permissions.

In scope: isolated worktrees, in-scope local source/docs/tests, reviewed commits/pushes/PRs, normal CI, squash merges only after an exact-diff independent review and full release gate, and no more than **four new, justified, authorized Stage 5 native Linux acceptance dispatches**.

Out of scope: paid spend, production, cloud, persistent privileged host changes, new credentials or repository privileges, new mutation powers, retrying uncertain effects, evidence deletion, real infrastructure effects, bypassing existing security controls or enabling destructive recovery.

Stage 4's native budget was 5/8 consumed; its unused three attempts **do not transfer**. Stage 5 starts from **0/4**. A failed or cancelled Stage 5 dispatch counts as consumed. No rerun merely to force a pass.

## Mandatory security and quality invariants

- Strict OS-authenticated local roles and protected IPC; no direct operator/agent access to protected Controller/Target databases. No new authority derived from prompts, roles in request JSON or telemetry.
- Default deny, policy/approval/reservation binding, immutable scope, one Controller send opportunity and durable unresolved evidence. No automatic mutation retry, no authority rehydration from an old reservation or grant.
- Read-only reconciliation never changes action, approval, reservation, outcome, target claim/receipt or effect state. Append-only required security access audit is allowed, bounded and fail-closed.
- Provenance and uncertainty are explicit; a matching target `COMMITTED` proves only a simulated SQLite effect. `UNKNOWN` never implies rollback.
- No secret/raw-prompt telemetry. No generic exactly-once, MFA, production availability or hostile-administrator guarantee.

## Workflow and stop gates

**Orchestrator → Builder → Independent Read-Only Reviewer → CI → exact tree/diff verification → authorized merge → justified native acceptance → next package.**

1. **Stage 5.0 architecture gate:** verify exact current code, schemas and local threat assumptions, produce proposal and acceptance cases, obtain independent architecture/security review and record owner technical approval before any new operator read authority is implemented. No historical Stage 4 work package authorization transfers.
2. **Per package:** record base SHA, permitted files, data/identity boundaries, negative scenarios, tests and target evidence before implementation. Builder and reviewer do not share write ownership.
3. **Publication and merge:** block for unresolved Critical/High findings or any violation of required invariants, missing tests, stale base, unreviewed changes or mismatch between reviewed/tested/merged tree. Do not weaken assertions to get CI green. Squash merge only through actual GitHub permissions.
4. **Native gate:** only if security behavior requires a native UID/UDS test beyond deterministic CI; verify actual owner and triggering actor, exact merged SHA and expected markers, disposable approved GitHub-hosted Ubuntu runner, read-only token, bounded runtime, no new privileges and available Stage 5 budget. Preserve run ID, SHA, logs and marker inventory. Count every dispatched attempt. Never reinterpret WSL2 as native acceptance.
5. **Continuity:** update [progress ledger](stage5-progress.md) at each accepted checkpoint with actual commits/PRs/reviews/CI/native usage; distinguish observed from proposed. Repair routine in-scope defects without breaking authorization. Stop for material architecture conflicts, unknown authority, access lock, permission expansion, real-world consequences, spending risk or exhausted limits.
6. **Final:** independent full Stage 5 local assessment, exact SHAs/tests/native evidence and residual risks; owner alone decides ACCEPT LOCAL STAGE 5 / PARTIAL ACCEPTANCE / NO-GO. Production stays NO-GO.

References: [Stage 5 draft architecture](../architecture/stage5-proposal-v0.1.md), [security rules](../../SECURITY.md), [ADR-0001](../decisions/ADR-0001-stage4-identity-boundaries.md), [Stage 4 accepted report](../stage4-acceptance.md).
