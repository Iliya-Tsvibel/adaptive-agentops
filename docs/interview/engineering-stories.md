# Cross-Stage Interview Stories — Problem → Architecture → Evidence

**Scope:** Reusable STAR-style stories from accepted local Stages 0–4. The user acted as project owner / architecture and acceptance decision maker, using Codex for much implementation. Avoid overstating hands-on code ownership or production readiness.

## Story A — Securing an LLM tool call

**Situation:** The model could be wrong, prompt-injected, or ask for a tool beyond its intended scope. A refusal in prose would not stop direct execution.

**Task:** Establish an explicit and testable security boundary around every permitted tool request.

**Design:** Strict named operations and typed proposals; deterministic default-deny PDP; Gateway PEP checks every adapter call; high-risk restart becomes a pending action that cannot execute without a separate approved state and atomic reservation.

**Security test:** Forged role, unexpected tools, mismatched resources and denied policy produce **zero downstream adapter calls**. A required audit failure prevents dispatch.

**Trade-off:** Static allowlist and local mock, not a generic agent framework or full dynamic policy service.

**Source:** Stage 1 Permission Matrix v0.1.1; [Stage 2 security](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/stage2-security.md).

**Good answer:** “I treated model output as candidate intent and made authorization a separate deterministic state machine. The tool boundary, not the system prompt, enforced what could actually execute.”

## Story B — From trust labels to authenticated Linux principals

**Situation:** Early `RunContext` values were trusted in-process labels. They could not resist an untrusted agent process asserting privileged identity or bypassing a mock API.

**Task:** Make local process identity and target isolation demonstrable.

**Design:** Separate Linux UIDs, protected Unix-domain sockets, mutual `SO_PEERCRED`, frozen UID/principal map, role-based operator IPC and no direct Agent→Target authorization.

**Alternative:** JWT, OIDC or SPIFFE for distributed identity. Rejected for the *local slice* because of token/key custody and unnecessary infrastructure. This is not a generic claim that OS identity always supersedes JWT.

**Proof:** Native distinct-UID Ubuntu checks, wrong-client/wrong-listener denial and cumulative Stage 4 evidence.

**Limitation:** Same-UID code and host admin remain trusted; no human MFA or cryptographic federation was demonstrated.

**Good answer:** “I chose the kernel credential boundary because our accepted problem was local, not multi-host identity federation. I can point to the exact trust assumption and would redesign it for Kubernetes/GCP.”

## Story C — Why UNKNOWN is not FAILED

**Situation:** Controller can commit a reservation, send a mutation, and lose the reply; SQLite and networked target effects are not one atomic transaction.

**Task:** Avoid duplicate effects on crash, timeouts and concurrent requests.

**Design:** Immutable action ID/fingerprint; atomic approval-consume + reservation + audit; one-process-local send permit; Target durable replay claim; simulated SQLite counter and receipt in one effect transaction; no automatic redispatch.

**Failure path:** Crash after reservation can permanently lose an attempt. Target claim then crash can leave no effect but still prevent re-execution. Reply loss can leave Controller UNKNOWN after effect was committed.

**Trade-off:** Availability loss is intentional to prevent uncontrolled duplicate mutations. Reconciliation must use read-only evidence, not retries.

**Proof:** Native Stage 4.6–4.7 replay/crash markers and exact state tests. It does **not** prove exactly-once arbitrary Kubernetes effects.

**Good answer:** “A timeout is a missing observation, not a rollback. We preserved unknown evidence and never made a second mutation attempt automatically.”

## Story D — Evaluating the model without overclaiming quality

**Situation:** A one-off local inference may look convincing but cannot substantiate reliability or prompt-injection safety.

**Task:** Freeze labels, evaluate bounded decisions, inspect the actual persisted action state, and report negative cases.

**Design:** 26 curated cases (24 scored + 2 boundary rejects), paired attack controls, immutable dataset, traceable manifest, source SQLite verification, zero native tools.

**Result:** 24/24 scored decisions correct in one run; 0/15 false-positive restarts, 0/9 false negatives, 0/4 injection overrides, 2/2 boundary rejects; nine pending actions, no approvals/dispatches.

**Limits:** Consumed synthetic holdout, one run, no inference hardware/weights attestation; doesn't complete the full Incident Investigator PRD.

**Good answer:** “I separated model quality from security and reported the actual dataset denominator. Perfect results on one small curated test set don't prove production accuracy.”

## Story E — Bounded autonomous engineering campaign

**Situation:** The owner was repeatedly asked to approve routine PR publishing, squash merges, CI and native test runs, causing stop/start overhead.

**Task:** Increase agent autonomy without unlimited privileges or reducing independent review.

**Design:** A versioned Stage 4 campaign contract defined exact scope, work-package tasks, Builder/Independent Reviewer, review-to-tree matching, CI, merge gate, bounded owner-authenticated native dispatches, spending limits and escalation conditions. Checkpoints allowed continuation after context/usage interruptions.

**Recovery example:** Native acceptance failed while loading a shared object from `/run`; a protected `/var/lib` executable staging preflight corrected an environment issue without relaxing application authorization. Later independent security review identified and corrected an expiry/acknowledgement issue.

**Outcome:** 5 of 8 approved native dispatches were used; Stage 4 ended with 32/32 cumulative native security markers and 1,755 regression tests. Owner retained the final local acceptance decision; production remained NO-GO.

**Good answer:** “I did not give the coding agent blanket power. I encoded what it could do, how another agent must verify its work, which evidence unlocks merge and native testing, and when a human must still decide.”

## Five interview traps

1. “We implemented OIDC” — **incorrect**. The accepted local Stage 4 used Linux UID/UDS identity; cloud identity remains future work.
2. “Our action always executes exactly once” — **incorrect**. We can lose an operation after reservation; generic external exactly-once effects were not demonstrated.
3. “Stage 3 scored 100%, so the model is safe” — **incorrect**. A single small synthetic evaluation is not universal reliability proof.
4. “SO_PEERCRED authenticates the human approving the action” — **incorrect**. It authenticates the OS process principal; no MFA or physical presence.
5. “Codex passed CI, so the code was independently certified” — **incorrect**. Reviewed exact-diff evidence and native runtime checks are stronger than green CI alone, but not a production security certification.

## Personal study plan

For each story: (a) draw the data and trust-boundary flow, (b) open one accepted source module and one failure-path test, (c) explain the reason for the design in 60 seconds, (d) demonstrate one negative test, (e) state the residual risk unprompted.

The comprehensive Stages 0–9 combined interview cheat sheet is deferred until the post-Stage-9 Work review.
