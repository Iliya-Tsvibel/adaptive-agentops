# Stage 5 — Observability, Evals & Reliability: architecture/security proposal v0.1

**Status: DRAFT / NOT YET APPROVED.** Stage 5.0 architecture gate and independent security review are pending. This document grants no new runtime authority. Stage 4 ADR-0001 D01–D10 remains accepted only for local, nonproduction use; production remains **NO-GO**.

**Verified baseline (2026-11-26):** `main` `135eecc57abcca4c39f6e4d3342576d5ce3eda1c`; independently accepted *executable* revision `34c38d9087b5c34b97fa3d4b68cafc3dc8219e0a`; [owner decision](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/24#issuecomment-7188763211). Acceptance report has 1,755 regression tests and 32/32 native markers. Neither the report commit nor this proposal is a new executable acceptance.

## Objective and non-goals

Build the smallest operator-safe workflow that explains an unresolved Controller dispatch using existing durable evidence, then add minimal local telemetry, reproducible security/operations evaluations and failure-path evidence. Measure useful accepted results and controlled risk, **not** generated response counts, dashboards or tool adoption.

**Not authorized:** mutation replay, new dispatch permits, disposition edits, cleanup/deletion of unresolved evidence, direct operator access to protected database/UID, arbitrary Target selection, production, cloud, Kubernetes/GitOps, OIDC/JWT/OPA additions, paid telemetry, real infrastructure mutations or destructive recovery. The deferred real GitHub/logs/provenance/final-report Incident Investigator product scope remains explicitly incomplete.

## Actual interfaces and evidence (verified at baseline)

- `stage4_boundary/hardened.py`: protected requester/agent/approver/Controller/Target flow; authenticated operator methods `INSPECT_ACTION / APPROVE / REJECT / DISPATCH`; current action inspection depends on live grant and current pending action. This is **not** historical reconciliation.
- `stage4_boundary/controller_state.py`: Controller-owned SQLite sessions, grants and events, protected file/directory lease, fail-closed storage, schema bounds; process restart invalidates prior live authority, not historical evidence.
- `stage4_boundary/dispatch_state.py`: unique reservation, approval consumption, `dispatch_effect_evidence.request_digest`, `dispatch_outcomes` and events. Pre-send request digest is recorded if construction succeeds; a reservation is never proof of a send.
- `stage4_boundary/target_state.py`: separate protected Target SQLite replay claims and simulated counter receipts; existing authenticated `lookup_target_receipt(endpoint, uids, key)` requires exact `dispatch_id`, `action_id`, `request_digest`. Target returns `COMMITTED` + `effect_number` or `UNKNOWN`.
- **Important limit:** Target `UNKNOWN` currently conflates absent claim and a claim with no committed effect. It cannot prove non-execution, nor can a lookup grant retry authority. Target `COMMITTED` proves only a matching *SQLite simulated* effect in the trusted local Target state.
- Hardened `_Controller.authorized_request` writes a required operator event, so a sensitive inspection can be *nonmutating to authority/effects* while still producing append-only security audit. That distinction must remain explicit.

## Proposed Stage 5.1: one read-only reconciliation slice

**Actor:** only an OS-authenticated, currently configured approver principal, via the existing protected operator-to-Controller socket. No new Linux UID, socket, credential, agent power or direct database access. Requester, agent, unknown UID, forged role and wrong endpoint receive denial with no target access or protected details.

**Input:** one strict versioned `RECONCILE_DISPATCH` request with exactly one UUIDv4 `dispatch_id`; no caller-specified path, UID, target, digest, principal, SQL or arbitrary query. Bounded canonical request and bounded response; no enumeration/listing in v0.1.

**Controller evidence:** load the exact reservation from protected Controller storage and cross-check its session/run, grant, immutable action/fingerprint, approval, target binding, dispatch events/outcome and `dispatch_effect_evidence.request_digest`. Verify relationships; do not infer missing rows are negative effect proof. Historical sessions may be read for this purpose without reinstating their grants or approval rights. Current configured approver-role authorization is independent of historical grant liveness; no removed/mismatched operator is reinstated.

**Target evidence:** only when the stored request key is complete and the trusted target endpoint UID/path/binding still matches the configured endpoint, use the existing mutually authenticated `lookup_target_receipt`. Compare returned full key with the stored key. Do not create/resume permits; never call `_exchange`, `_apply`, `_reserve`, `DISPATCH` or action approval methods. If the target is unavailable, mismatched or state is inconsistent, report uncertainty/integrity error; do not fall back to raw filesystem access.

**Output:** a small typed, redacted provenance-oriented explanation:
- `controller_outcome`: persisted `UNKNOWN`, `RESERVED_NO_SEND`, `SIMULATED_EFFECT_COMMITTED`, missing or inconsistent (as directly supported).
- `target_observation`: `MATCHING_SIMULATED_COMMIT`, `UNRESOLVED` (including Target `UNKNOWN`), `UNAVAILABLE`, or `INCONSISTENT`; include effect number **only** for authenticated matching commit.
- `evidence`: bounded identifiers for the matched Controller reservation, action, approval and digest and named source of each finding; do not expose raw prompts, secrets, unrestricted historical event payloads or paths.
- `certainty`: target-side matching simulated commit can clarify a Controller `UNKNOWN`, but **never silently update** the Controller outcome to `SUCCESS`. Target `UNKNOWN`, absent rows and timeouts never mean “no effect.” Report observation time and whether the result is merely a snapshot.

**Read-only invariant:** no changes to `actions`, `approvals`, `pending_bindings`, `dispatch_reservations`, `dispatch_outcomes`, `dispatch_effect_evidence`, Target claims or simulated counter; no outgoing mutation frames, no permission/dispatch issuance. A *new append-only Controller security audit event for the authorized lookup/denial* is allowed and should be required before sensitive disclosure; if required audit cannot commit, deny disclosure. Audit additions must respect the existing bounded-capacity fail-closed contract. Metrics/logs may not authorize anything and do not replace durable audit.

**Concurrency/time semantics:** query is an observation, not a lock on execution. The absence of a claim or effect in one snapshot does not establish final absence while a mutation may still be in flight. No reconciliation response authorizes another send. A corrupt, absent, mismatched, or unavailable storage source is not a reason to reinitialize or repair evidence.

**Explicitly deferred within Stage 5.1:** enriching Target's protocol to distinguish `NO_CLAIM` from `CLAIMED_NO_RECEIPT` requires its own focused review and wire-compatibility tests. It is not necessary for the first usable slice because neither state would permit replay.

## Stage 5.0 security gate (must pass before runtime implementation)

1. Review actual schema, socket/UID mapping, current/historical session lookup and receipt protocol against the exact base revision; document a traceability map of claims to rows.
2. Independent read-only security/architecture review: OS-principal disclosure, historical access, field redaction, audit-before-disclosure, target binding, no mutation path, uncertainty and fault behavior.
3. Agree on the exact one-method request/response contract, permitted file scope, schema compatibility, bounded response size and regression test inventory.
4. Record owner-approved technical architecture gate. **The kickoff campaign permission is not itself approval of this proposed architecture or a new runtime authorization path.**

## Stage 5.1 acceptance tests (proposed)

- Positive: authenticated approver obtains matching Controller IDs and a Target `COMMITTED` simulated receipt; Controller `UNKNOWN` remains unchanged. A Target `UNKNOWN` yields unresolved, without inventing no effect.
- Negative: wrong UID/requester/agent/forged role/unknown dispatch ID; spoofed or wrong Target peer; wrong digest/action/dispatch; malformed, duplicate-field or oversized frame; unauthorized lookup discloses no protected evidence.
- Crash/recovery: historical reservation after Controller restart, target offline, lost reply, claim without receipt, missing/corrupt database, exhausted audit/capacity and ambiguous audit commit all fail safe without creating authority.
- **Effect boundary:** compare pre/post Controller and Target authority/effect tables; only expected append-only lookup audit may increase. Instrument target-message types and assert zero mutation request frames and zero additional dispatch attempts.
- Race: snapshot while claim/effect is in flight must never assert irreversible “no effect.”
- Review full diff independently; pass focused + full deterministic CI on the reviewed tree and native UID/UDS negative checks only if the security gate justifies an authorized native run. Match CI/review/native SHAs and trees.

## Later, explicitly separate work packages

**5.2 Observability:** minimal structured local logs and counters with safe correlation; audit is durable authority evidence, logs/traces are operational diagnostics; no paid backend or raw prompt/secret logging. Evaluate OpenTelemetry only after a concrete instrumentation gap is demonstrated.

**5.3 Evals:** freeze *new* deterministic datasets/labels before execution; score correct accepted requests, denials, wrong principal/scope, approval misuse, evidence completeness, classification, failure handling, latency/resource bounds and useful outcomes. Stage 3's 26-case holdout is historical/consumed, not fresh blind evidence.

**5.4 Reliability:** fault injection for Controller/Target restart, receipt loss, corrupt/missing/full storage, ambiguous SQLite commit, revoked/expired grants, deadlines, partial telemetry; no auto retry or destructive self-healing.

**5.5 Acceptance:** evidence-backed independent security/reliability review and owner decision: ACCEPT LOCAL STAGE 5, PARTIAL ACCEPTANCE or NO-GO. No production-readiness or generic exactly-once claim.

## Critical trade-offs and open issues

1. **Audit vs “read-only”:** protect durable evidence against mutation; permit strictly append-only access audit, or fail closed. Do not call the whole database “read-only.”
2. **Historical authorization:** current `INSPECT_ACTION` cannot inspect consumed/expired historical dispatches; a new *read-only* method under the existing approver identity is needed. Do not revive historical grants.
3. **Target `UNKNOWN` ambiguity:** first slice keeps it unresolved. Do not widen the wire/status schema without a separate benefit and independent review.
4. **Target identity and continuity:** UID/path matching is within a trusted local kernel/admin/launch threat model, not a remote attestation or proof against replaced storage.
5. **Availability:** the existing one-connection Target test harness and bounded Controller journal impose limits; quantify them instead of introducing a server cluster or retention deletion.
6. **Governance:** `main` is currently unprotected; release gates depend on verified review, exact SHA/tree and controlled repository write access.

Sources: [ADR-0001](../decisions/ADR-0001-stage4-identity-boundaries.md), [Stage 4 acceptance](../stage4-acceptance.md), [Stage 4 campaign](../campaigns/stage4-progress.md), source modules named above, and formal [owner decision](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/24#issuecomment-7188763211).
