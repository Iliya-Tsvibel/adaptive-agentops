# Adaptive AgentOps Stage 4 autonomous completion campaign

The repository owner authorized this campaign on 2026-11-25 for
`Iliya-Tsvibel/adaptive-agentops`. The scope is local, nonproduction
implementation and verification of accepted ADR-0001 decisions D01–D10.
The owner can revoke or supersede this contract. Production remains NO-GO.
[ADR-0001](../decisions/ADR-0001-stage4-identity-boundaries.md) ·
[Stage 4 architecture](../architecture/stage4-local.md) ·
[Progress ledger](stage4-progress.md)

## Authority and invariants

The campaign authorizes in-scope development, reviewed publication, ordinary
GitHub CI, squash merges, and bounded privileged GitHub-hosted native acceptance.
It supplies the owner authorization for those operations when every applicable
gate below passes. The actual authenticated owner identity and GitHub branch
protections, sandbox and approvals still apply. The Independent Verifier
evaluates gates within this contract. Only the owner can enlarge its scope.

Development authorization, Control Plane runtime human approval, and formal
milestone acceptance are separate decisions. Runtime mutations require human
approval at the Control Plane, atomic approval consumption and dispatch
reservation, at most one controller-mediated attempt, and fail-closed handling
of ambiguous persistence or outcomes. Agent and model inputs confer no
authority. Require strict typed proposals, immutable action fingerprints,
binding and expiry, authenticated principals, default deny, complete mediation,
and the existing Stage 2/3 and accepted Stage 4 guarantees. Formal milestone
acceptance remains with the owner. [Security rules](../../SECURITY.md)

Use existing dependencies, local resources and included GitHub Actions compute
at zero added cost. Keep ordinary CI automatic where practical. Preserve the
owner's billing policy without recurring approval while there is no evidence
of changed spending risk. Production, real infrastructure effects, cloud
deployment, persistent host changes, permission or credential expansion, and
paid overage are outside scope. Local effects are confined to the simulated
`demo-api` target. Local privileged workflow execution, unsupported native
runners, and bypass of any approval or security control are prohibited.

## Work packages and evidence gates

Before implementation of each package, record its base SHA, ADR decisions,
file scope, exclusions, invariants, validation, and exact evidence targets.
Refine the package within the accepted architecture after reading current
implementation. The initial [ledger](stage4-progress.md) separates older
evidence from this campaign's execution budget.

| Package | In scope | Evidence required before progression |
| --- | --- | --- |
| Stage 4.3 recovery | Reverify, integrate and validate PR #18. Keep the reviewed operator/delegation boundary and workflow guards. If needed, diagnose setup failure and repair within scope. | Matching reviewed and CI-tested tree, successful native job with `include_stage43=true`, all 17 markers exactly once, independent evidence verification. |
| Stage 4.4 protected Controller state | D04, D08–D10. Protected SQLite and ownership, durable grant evidence and revocation/expiry, fresh session on restart, verified schema and fail-closed startup/recovery. | Test ownership, database and schema failure, crash-consistent grant transitions and ambiguous commits, and stale authority after restart. |
| Stage 4.5 OS-bound dispatch | D02–D06, D08–D09. Authenticated controller-to-target IPC and protected scope, policy and previously approved/reserved action validation. | Verify denial of self-approval, direct agent dispatch, expired or mismatched authority, and legacy bypass. Preserve human approval and reservation. The simulated target is the only effect boundary. |
| Stage 4.6 durable target replay and effects | D07, D10. Durable request identity and outcomes, atomic duplicate handling, retained unresolved evidence, explicit uncertainty and fail-closed capacity. | Exercise replay, restart, concurrent requests, crashes and storage failures. Verify at-most-one attempt and durable evidence without claiming exactly-once external effects. |
| Stage 4.7 hardened local acceptance | Verify D01–D10 as a chain from operator, delegation, proposal and PDP through required human approval, reservation, authenticated dispatch, simulated effect and durable evidence. | Cover success, wrong-UID endpoints, forged identity, revoked/expired authority, denial, approval reuse, duplicate requests, audit loss, uncertain persistence, restart and legacy separation. Obtain independent actual native evidence. |

D06 uses OS-bound authorization. JWT and additional portable bearer authority
are excluded. The campaign establishes local guarantees under the accepted
trust assumptions and makes no production readiness claim.

## Roles and development loop

The Orchestrator owns scope, gate decisions and the ledger. The Builder owns
the implementation, tests, documentation, debugging and corrective PRs.
The Independent Verifier remains read-only and checks exact diffs, ADR
conformance, threats, tests and evidence. Use additional specialist agents only
when independent parallel work has a demonstrated benefit. Avoid overlapping
write ownership. The Builder cannot approve its own security-sensitive changes.

Define the contract, implement, run local tests, obtain independent review,
repair, publish, verify CI, squash merge, obtain any required native evidence,
and proceed to the next package once the gates pass. Record and verify exact
reviewed, published and tested diffs and trees. After a failure, inspect the
logs, reproduce where possible, narrow the hypothesis, fix the justified cause,
add regression tests and reverify affected gates. Review material corrections
again. Use bounded repair cycles. After repeated unsuccessful repairs, switch
to independent diagnosis. Keep security assertions intact when repairing a
harness or environment contract. [Developer loop](../developer-loop.md)

## Squash-merge gate

Proceed under the campaign authorization when the PR matches the approved work
package and its proposed merge tree matches independent review and passing CI.
Require the applicable local tests, Independent Verifier PASS, resolution of
all Critical and High findings and any lower finding that violates a mandatory
invariant, and consistency with local ADR scope and the cost and production
limits. Respect GitHub's required checks and branch protection. If a gate
fails, return the work to the Builder and reverify the resulting revision.

## Native execution gate

The owner authorizes **at most eight fresh privileged native dispatches in this
campaign**. The initial historical runs are separate evidence. Count every
campaign dispatch, including unsuccessful or cancelled runs. Before a dispatch,
reconcile the ledger and remote runs and require all of the following.

- Recorded reviewed and merged `main` SHA, matching CI and independent PASS,
  expected marker inventory, and no known unresolved setup defect.
- Protected staging, mount permissions, executable dependency loading and
  isolated service identities appropriate to the workflow.
- Disposable GitHub-hosted Ubuntu 24.04, five-minute maximum job timeout,
  read-only GitHub token, and intact first-attempt and owner-identity guards.
- The actual authenticated repository owner `Iliya-Tsvibel`, zero evidence of
  changed billing risk, and no other active native acceptance run.

Use fresh dispatches. A failed unchanged revision is ineligible for another
dispatch merely to seek a passing result. Do not use GitHub reruns or weaken
identity guards. Record the actual run and job, checkout SHA, input,
actor/triggering actor, attempt, runner, permissions, marker inventory,
conclusion and bounded evidence. Require successful job completion and
independent verification for the relevant native evidence gate.
[Stage 4.3 native acceptance plan](../stage43-native-acceptance.md)

## Continuity and escalation

After every merged PR and native run, persist verified progress in the ledger.
Use a reviewed repository update for durable publication. Record completed and
pending work, worktree/branch, base and head or merge SHAs, exact diff and review
status, PRs, local tests, CI, native run IDs and counts, blockers, ownership and
the next action. Record observable CI/native usage, review and repair cycles,
and owner interactions. Before context or usage limits stop work, leave the
latest accurate checkpoint available to the next session. Resume by reconciling
the checkpoint with remote refs, PRs and Actions. A usage limit records an
interruption. An engineering failure requires actual failure evidence.

Resolve ordinary engineering and review gates within the campaign. Escalate
conflicts with ADR-0001, work outside scope, risk to real systems, credentials
or spending, unresolved Critical/High findings, unattainable mandatory
evidence or execution limits, technically inaccessible owner-only operations,
and irreversible or unrecoverable decisions that require owner judgment.
Obtain authority for exceptions from the owner. Continue safe, independent
work where possible.

## Final acceptance package

Obtain an independent full-stage assessment of merged code, evidence and
reliability across D01–D10. Report final `main` SHA, work packages and PRs,
tests and CI, native runs and markers, findings and resolutions, identity and
authorization threats, crash/replay/audit/recovery evidence, trust assumptions,
limitations, unresolved risks, resource and repair totals, and observed owner
interventions. Include Stage 5 and production gaps and a compact MasterBrief
change proposal. Recommend **ACCEPT LOCAL STAGE 4**, **PARTIAL ACCEPTANCE** or
**NO-GO** based on evidence. The owner records the formal decision.
