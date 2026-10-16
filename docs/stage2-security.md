# Stage 2 security boundaries

Stage 2 is a deterministic, trusted local MVP.
Its security claim concerns the controller workflow with intact storage, not enforcement against hostile code on the host or an authenticated production service.
Code and tests are the evidence for this document; canonical MasterBrief and PRD sources are not present in the local repository.

## Identity and policy

`RunContext` is an immutable Python data object supplied by trusted internal code.
The local factory generates a run UUID and fixed `local-requester`, `incident-investigator-v1`, and `local-mock` labels.
These are scope and correlation values, not credentials or cryptographically verified agent identities.
Code able to construct a matching context can assert those labels; there is no real workload authentication.

The default-deny PDP accepts only supported proposal types and the fixed local combination of identity, environment, and resource.
`mock.get_health` returns `ALLOW`; `mock.restart_service` returns `APPROVAL_REQUIRED`.
Unknown or malformed proposals and out-of-scope requests are denied.
Proposals allow only tool, resource, and empty arguments, excluding caller-selected destinations and approval metadata.
Gateways revalidate inputs and fail closed when required policy or audit work fails.
These controls depend on callers using the trusted entry points.

## Pending actions and local approval

The restart gateway commits a pending action and its policy event together.
IDs and timestamps are generated internally.
The action expires ten minutes after creation.
Its version-1 SHA-256 fingerprint covers canonical JSON containing run ID, requester, agent, tool, resource, environment, and empty arguments.
Action ID and timestamps are excluded.
The hash binds scope; it is not a signature or proof against database tampering.
Identical scopes can yield identical fingerprints, but distinct action IDs require distinct decisions and reservations.

The CLI reviews stored scope and records one `APPROVED` or `REJECTED` decision per action.
It requires a terminal and exact confirmation containing the action ID, revalidates after the prompt, and commits under `BEGIN IMMEDIATE`.
A separate read-only connection verifies that the action is committed, so approval persistence requires a file-backed database.
Approval inherits the action expiry and binds its ID, run, requester, agent, and fingerprint.
Rejection cannot be replaced by approval through the supported workflow.

The fixed approver label is `local-approver`.
Terminal presence and typed confirmation do not authenticate a human, establish independent separation of duties, or prevent trusted code from calling persistence primitives directly.
The CLI neither consumes approvals nor dispatches tools.
Its decision is stored in `approvals`; there is no separate approval event kind in `audit_events`.

## Atomic reservation and attempt bound

The controller requires an exclusively held connection with no active transaction.
Reservation rechecks bindings between context, action, and approval, an unconsumed `APPROVED` decision, absence of a prior reservation, fresh `APPROVAL_REQUIRED` policy, and expiry after policy evaluation.
One `BEGIN IMMEDIATE` transaction conditionally changes `consumed` from 0 to 1, inserts a reservation unique by action and approval, and appends `DISPATCH_RESERVED`.
Only a confirmed commit returns usable reservation authority.

Under trusted-code and intact-storage assumptions, this gives **at most one controller-mediated HTTP attempt per action**.
Concurrent callers cannot both reserve that action; later calls cannot reuse consumed approval or resume a stored reservation.
The adapter itself has no authorization or cross-call replay protection: invoking it separately can send another POST.
A new action also has independent approval rights.
This is not exactly-once execution or exactly-once external effects.

The restart adapter uses a fixed loopback URL, an empty POST, no redirects, no environment proxies, and zero transport retries.
It validates the response contract and limits the response to 4096 bytes.
Two-second network timeouts and a two-second cooperative async deadline bound ordinary I/O, including a trickling response; they are not a hard real-time guarantee against event-loop or OS stalls.

## Crash windows and uncertain outcomes

SQLite transactions and HTTP effects do not share an atomic commit:

| Boundary or outcome | Meaning and required interpretation |
| --- | --- |
| Pending-action COMMIT raises or is ambiguous | Creation may have persisted. `UNCERTAIN` returns no usable action ID; the gateway does not retry. |
| Reservation fails before a confirmed commit | No controller dispatch. Reservation `UNCERTAIN` becomes `RESERVATION_UNCERTAIN`; storage may already contain consumed approval and a reservation. |
| Crash or cancellation after reservation, before HTTP | Approval may be spent with no attempt and no result. Availability is sacrificed; the action cannot be resumed. |
| Timeout, disconnect, invalid response, or crash during or after HTTP | The request may have taken effect. `UNCERTAIN` does not mean no effect and never permits automatic retry. |
| Result write or COMMIT fails | `AUDIT_UNCONFIRMED` means an adapter attempt occurred but result durability is unconfirmed. A result may already have committed. |
| Controller returns `ACKNOWLEDGED` or `UNCERTAIN` | A corresponding result commit was confirmed. Only the former confirms the validated mock acknowledgement. |

Interruption exceptions can propagate after cleanup instead of returning a status.
An exception during COMMIT does not establish whether it persisted.
Never recover POST rights from stored rows or infer permission from an exception.
There is no automatic reconciliation or recovery worker; unresolved states require inspection without replaying the action.

## Audit and storage trust

The read-only Gateway commits `POLICY_DECISION` before dispatching GET.
Failure to confirm that commit prevents dispatch.
After an authorized HTTP attempt, it commits `RESULT` in a separate transaction.
A subsequent `RESULT` audit failure leaves result durability unconfirmed but does not erase the already committed `ALLOW` decision or undo the HTTP attempt; cleanup of the result transaction preserves the prior policy evidence.

The restart audit chain is `POLICY_DECISION` → `DISPATCH_RESERVED` → `RESULT`.
Policy evidence includes run and action IDs; reservation and result evidence additionally includes approval and dispatch IDs.
Events include identity labels, tool, resource, environment, timestamp, historical policy decision, execution status, and bounded reason codes.
Reservation is recorded as `NOT_DISPATCHED`; it is not evidence that HTTP occurred.
The result primitive checks stored action and reservation bindings and correlated pre-dispatch evidence.
A partial unique index prevents duplicate mock restart results for a dispatch under the installed schema.

Creation and policy evidence commit together; consumption, reservation, and pre-dispatch evidence commit together; the result commits separately after HTTP.
Persistence primitives alone do not commit: their callers own durability.
Tests check visibility from separate connections and rollback behavior.
Durability relies on SQLite and the underlying filesystem and storage; this is not an external immutable audit service.
A crash may leave pre-dispatch evidence without a result, and malformed requests or failed audit writes need not produce an event.

**The `actions` and `audit_events` tables are NOT tamper-proof or generally append-only.** Their schemas do not prohibit general updates or deletes.
Approval triggers protect decision metadata and allow only monotonic consumption; reservation triggers prohibit updates or deletes and replacement of existing reservations.
These protect ordinary SQL operations under the installed schema, not a database owner who can remove protections, rewrite data, or restore an older database.
Fingerprints, model immutability, and result uniqueness do not provide cryptographic integrity or rollback protection.
Trusted access to the database, schema, process, and host is essential.
Schema initialization is not a general migration system for older pre-release databases.

## Clock and connection failure boundaries

Expiry checks use UTC wall-clock time, with validity ending strictly at `expires_at`.
There is no trusted time source or monotonic persistent TTL.
Clock changes can shorten or extend practical eligibility.
Expiry is checked at decision and reservation boundaries, not atomically with the eventual HTTP effect; a delay after reservation can outlast the window.
A result can still be audited after expiry because it records historical execution rather than granting new authority.

The gateways and controller that own transactions attempt rollback on failures; rollback failure triggers an attempted connection close in their cleanup paths.
A failure return does not promise that the connection remains usable. **If rollback fails, discard the connection; if close also fails, isolation is unconfirmed and the connection must never be committed or reused.** Partial writes could otherwise be committed later.
Interruptions can complicate cleanup and do not establish isolation.
Caller-owned transactions are left untouched.
The operator CLI scopes its connection with closing and reports no confirmed success on failure; its failure message is not proof that COMMIT did not persist.

## Mock target and production exclusions

The mock API does not authenticate callers or enforce approval, reservation, or replay policy.
Anyone with network access can POST directly to `/services/demo-api/restart`, bypassing the control plane.
The adapter uses plain HTTP on `127.0.0.1:8001` without target authentication; another listener on that port could impersonate the target.
The end-to-end tests bind their own socket and fail if the port is occupied, which protects that test setup rather than establishing general target identity.

A successful POST increments a process-local counter and returns `simulated: true`; it does not restart a process, mutate infrastructure, or persist a target-side operation.
The counter resets with the mock application.
The mock health endpoint remains unhealthy after acknowledgement.
`ACKNOWLEDGED` therefore proves neither real restart nor service recovery.

Deferred capabilities include agent and LLM integration, cryptographic workload identity, authenticated operator access and role enforcement, authenticated and protected targets, real infrastructure adapters, tamper-resistant audit storage, protection against database rollback, trusted time, and production reconciliation or distributed effect guarantees.
None is implied by the current module boundaries.

These limitations are acceptable only for the agreed trusted local MVP: a controlled host, trusted code and operator access, intact local storage, and a target whose only mutation is a simulation.
Within that boundary the tests demonstrate deterministic authorization, atomic reservation, and conservative failure handling.
Exposing the workflow to untrusted workloads or connecting it to real infrastructure would exceed the demonstrated security model.
