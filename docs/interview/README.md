# Adaptive AgentOps — Interview Lab (Stages 0–4)

**Purpose:** study the engineering choices, failure paths, security guarantees and evidence of the local project. These are *learning notes*, not new normative architecture decisions or claims of production readiness.

**Study order:**
1. [Stage 0 — Environment and engineering foundations](stage-00-environment.md)
2. [Stage 1 — PRD, threat model and permission contract](stage-01-requirements-threat-model.md)
3. [Stage 2 — Deterministic authorization and transactions](stage-02-policy-approval-sqlite.md)
4. [Stage 3 — Local LLM, tool boundaries and evaluation](stage-03-llm-evals.md)
5. [Stage 4 — Linux identity, delegation, dispatch and replay](stage-04-identity-delegation.md)
6. [Cross-stage interview stories and the autonomous development loop](engineering-stories.md)

## Reading protocol

For each stage, answer aloud:
- **Problem:** What risk or missing capability motivated the stage?
- **WHY:** Why this design rather than simpler or more fashionable alternatives?
- **HOW:** Trace one real request through the code and data model.
- **FAILURE MODE:** What happens when a process crashes, a commit is ambiguous, a policy check fails or the result is missing?
- **SECURITY:** What trust boundary is actually enforced, and what remains trusted?
- **TRADE-OFF:** Which useful functionality or availability did we intentionally sacrifice?
- **EVIDENCE:** Which accepted tests, commit and run demonstrate the claim?
- **MY ROLE:** Distinguish owner-driven architecture and acceptance from AI-assisted implementation.

Do not memorize an impressive story you cannot reproduce. Open the relevant code and tests and explain them in your own words. The final multi-stage interview cheat sheet is deferred until Stage 9 / comprehensive Work review.

## Current claims and provenance

- These notes cover Stages 0–4. The Stage 4 owner decision covers the local, nonproduction scope. Production remains **NO-GO**.
- Stage 4 executable acceptance SHA: `34c38d9087b5c34b97fa3d4b68cafc3dc8219e0a`; owner acceptance: [PR #24 comment](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/24#issuecomment-7188763211).
- Stage 4 cumulative native evidence: [32/32 markers](../stage4-acceptance.md) on a disposable native Ubuntu runner.
- Earlier acceptance and limitations: [Stage 2 security](../stage2-security.md), [Stage 3 acceptance](../stage3-acceptance.md).
- Normative identity decisions: [ADR-0001](../decisions/ADR-0001-stage4-identity-boundaries.md).
- Stage 1 historical specification source: **Corrected Baseline v0.1.1** in the ChatGPT IT Projects Project Sources (PRD, Architecture, Threat Model, Permission Matrix, Decision Record). It predates implementation.
- Stage 0 source: completed Environment Setup & Audit Project Source, not a comprehensive inventory of installed tool versions.

### Words that must not be conflated

`proposal ≠ authority`; `authentication ≠ authorization`; `delegation ≠ approval`; `reservation ≠ effect`; `audit event ≠ proof of delivery`; `UNKNOWN ≠ failure`; `at-most-one dispatch attempt ≠ exactly-once external side effect`; `synthetic or OS-UID approver ≠ MFA-verified physical human`.

## How to rehearse

1. Explain every stage in 60 seconds without looking.
2. Explain the concrete request path in 5 minutes, with a whiteboard.
3. Reproduce one positive and one negative verification from a disposable environment.
4. Explain an unresolved failure and the exact evidence needed to investigate it.
5. Say explicitly what the system does *not* prove.

These files are versioned learning materials. Update a claim only after checking the exact accepted artifact and source, and cite the change in a reviewed PR.
