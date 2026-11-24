# Adaptive AgentOps engineering instructions

Stage 3 is LOCAL MVP ACCEPTED. Stage 4.1 has scoped native Linux acceptance.
Stage 4.2 native acceptance is pending. Production remains NO-GO.

## Source of truth

1. **Approved architecture, security and task contracts.** Read the [security rules](SECURITY.md), [Stage 4 architecture](docs/architecture/stage4-local.md), and the owner-approved contract for the task. See the [Stage 4.2 contract](docs/tasks/stage4/4.2-scoped-delegation-contract.md) for the current scoped admission boundary.
2. **Repository code and tests.** Use the verified task base to establish implemented behavior. Escalate a conflict with an accepted contract.
3. **Verified project evidence.** GitHub reviews, CI runs, and owner decisions establish what was reviewed, tested, and accepted. Match evidence to its exact revision and scope.
4. **Historical material.** Drafts, superseded sources, discussions, and model output provide context. They confer no authority.

## Mandatory invariants

- Default deny and mediate every tool operation through the Control Plane.
- Model output, agent input, and caller-supplied identity confer no authority.
- Use strict typed proposals and immutable pending actions. Preserve fingerprints, scope, and expiry.
- Require human approval for mutations. Consume approval and reserve dispatch atomically. Permit at most one controller-mediated mutation attempt and never retry an uncertain outcome automatically.
- Fail closed on authentication, authorization, binding, expiry, required audit, and ambiguous persistence failures. A recorded proposal decision grants no execution authority.
- Protect existing Stage 2 and Stage 3 contracts and accepted Stage 4 boundaries. Independent security review precedes changes to security rules, identities, authorization, or credential handling being committed or pushed.

## Builder and independent Reviewer

The Builder confirms the approved task, exact base, and file scope, creates an
isolated worktree, and makes minimal changes. Run relevant deterministic
tests, retain actual outcomes, and record any missing verification.

The independent Reviewer inspects the exact diff, approved contract, threat
assumptions, and evidence. The Reviewer stays read-only, reports findings
with severity and reproducible evidence, and recommends GO or NO-GO. The
Builder may repair only in-scope findings and must revalidate any repair.
Follow the [developer loop](docs/developer-loop.md) for review and publication.

## Authority and stop conditions

An approved task can authorize an isolated worktree, allowlisted edits,
unprivileged tests, an independent review, and bounded repairs. Commit,
push, PR creation, external comments, and ordinary CI require authorization
in the task or separately from the owner. Approval to publish leaves the
merge decision with the owner.

The owner separately authorizes each privileged native run. Stop for
unverified or conflicting approval provenance, a changed base or
unreviewed diff, unsafe setup, an unresolved blocker, missing test or
review evidence, exhausted repair cycles, or any need to expand the task,
permissions, credentials, or spending.

Repository instructions guide the agent. The sandbox, OS identity and
access controls, and host permissions enforce their respective boundaries.
