# ADR-0002 — Stage 6.E trusted evidence bridge

**Accepted by the owner on 2026-11-28 for the documented native Linux,
local, nonproduction scope. Production NO-GO.** See the
[owner decision and campaign closure](../campaigns/stage6e-progress.md).

The ADR was originally proposed under the owner-authorized Stage 6.E
campaign and required independent architecture review before implementation.
The [architecture PR and review](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/38)
and the [technical assessment](../stage6e-acceptance.md) preserve that evidence.

## Context

[ADR-0001](ADR-0001-stage4-identity-boundaries.md) established distinct local
Linux principals, mutual Unix-socket authentication, scoped grants, immutable
actions, human approval, atomic consumption and reservation, one Controller
mutation attempt, durable Target replay protection and fail-closed handling
of ambiguous outcomes. [Stage 4 acceptance](../stage4-acceptance.md) covers
its exact historical executable. [Stage 5 acceptance](../stage5-acceptance.md)
covers synthetic offline analysis. A configured operator needs a way to
investigate one historical simulated dispatch using live protected evidence
while preserving the original execution record.

## Decision

Use an explicit Controller-owned maintenance launch that holds the existing
exclusive Controller state lease while the execution Controller is stopped.
Keep the accepted launches and their on-disk schemas intact. The maintenance
profile offers only inspection. It starts no Controller session, grant
lifecycle, proposal listener or dispatch path. This trades concurrent
execution availability for a narrow, independently verifiable boundary.

Use a dedicated inspector UID, distinct from the Controller, Target, agent,
requester and approver, with an independent protected socket directory and
reachability group. Trusted launch configuration fixes these identities, the
endpoints and one historical dispatch UUID. Reuse the accepted path, ACL and
mutual kernel peer verification primitives. Authenticate the peer before
reading the bounded request. The wire accepts one exact inspection operation
and canonical UUID, with the UUID matched against the protected configured
value before any dispatch-specific state query or Target request. Unknown
peers, methods, malformed requests and out-of-scope UUIDs fail closed.
Configuration is owned by the trusted deployment operator. Obtaining the
UUID for configuration is a privileged provisioning action outside the
inspector interface. Current or historical execution grants confer no
inspection authority. The inspector receives neither store access nor
Target socket reachability.

Reusing a requester or approver UID would combine incompatible privileges.
Extending the existing operator socket would broaden the accepted role and
method surface. Direct inspector-to-Target access would bypass Controller
authorization, evidence correlation and disclosure auditing. A maintenance
socket avoids those changes. Any future concurrent launch needs separate
review of scheduling, lease, consistency and availability.

## Acquisition and provenance

Open the exact hardened Stage 4 Controller schema under its existing pinned,
protected directory and lease, with a read-only SQLite connection. Require
verified ownership, modes, ACLs, schema, integrity and database identity.
Fail closed when recovery would be required. The inspection process cannot
recover or update execution state. Use indexed, parameterized reads bounded
to the configured dispatch, a finite set of immutable relationships and
bounded validation work. Read Controller evidence in one local transaction,
close it before IPC, and retain only the typed evidence needed for the reply
and audit.

Positive attribution requires the reservation, action fingerprint and run,
session and grant bindings, approved and consumed approval with historical
approver evidence, reserved and applicable outcome events, and the original
durable request digest. Validate the historical Target UID, path, access
group and policy against protected launch configuration before using that
endpoint. Historical grant expiry and session invalidation do not erase
evidence or establish current execution rights. Absent or incompatible
bindings prevent positive attribution.

The Controller makes at most one existing authenticated Target receipt
lookup with the exact dispatch ID, action ID and request digest. Honor one
bounded inspector exchange deadline including Target connection and response,
with a bounded audit step and no post-deadline disclosure. The existing
Target wire format suffices. A strict, correlated committed receipt can
corroborate a recorded simulated effect; a malformed or mismatched response
prevents positive attribution. Target UNKNOWN covers both no matching row
and a matching claim without receipt. Off-key records may be unobservable.
Unavailable Target, UNKNOWN, and partial observations never prove
non-execution. There is no Target mutation or replay request.

Each observation records its source and availability; local acquisition
timing is descriptive only. There is no consistent cross-store snapshot.
Never synthesize Stage 5 capture labels or order. A separate typed bridge
applies the compatible conservative Stage 5 principles to these inputs
without changing the frozen offline classifier. Preserve the Controller's
historical UNKNOWN even when Target evidence supports an attributed effect.
Conflicting evidence is reported conservatively. No output establishes retry,
dispatch, approval or grant authority.

## Audit and disclosure

Use a separate explicitly provisioned Controller-owned protected audit
directory and exact, bounded schema, with the same ownership, lease and
durability checks. It must be independent of Stage 4 execution event capacity.
Record the authenticated inspector UID, authorized dispatch, bounded
classification and source disposition for the exact reply. Confirm the
durable commit before sending protected information. Treat storage errors,
ambiguous commits and expiry as no-disclosure outcomes. A committed audit
can precede a lost reply; it records authorization to disclose a particular
result rather than certifying receipt by the inspector. Do not retry an
uncertain audit commit. Repeated successful inspections may produce new
audit records; they cannot alter execution history.

Wire output uses a strict fixed-schema bounded ASCII diagnostic with only
enumerated classification, source attribution and historical outcome fields.
Exclude IDs, paths, timestamps, row payloads, effect counters and exception
text. Errors outside the authorized evidence flow yield a generic denial
or no reply. The minimal client authenticates the Controller and checks
the exact reply schema before displaying output.

## Threat model and acceptance

A compromised agent, unrecognized UID, malformed caller or false server
cannot gain authority by supplying identity or provenance. Keep trusted
launch configuration, authorized service UIDs, OS administrators, the running
kernel and local storage integrity within the accepted Stage 4 trust
assumptions. A compromised authorized service UID and tamper-proof audit
are outside the model. Finite capacity, bounded exchanges and exclusive
maintenance create availability limits. Validate timeouts, restart, scope,
negative disclosure, unchanged execution records and audit failure in
focused tests, and distinguish fault injection from native evidence.

Require independent exact-diff review before publication, ordinary CI and
merge-tree verification for each package. Native separate-UID Linux evidence
must prove filesystem and socket isolation, mutual authentication, correct
inspector and denied principals, preserved UNKNOWN and replay evidence,
positive and unresolved retrieval, and audit-before-disclosure. Record
actual markers and revision within the eight-run campaign authorization.
Formal acceptance remains an owner decision.

Docker portability, multi-tenant and remote identity, cross-store snapshots,
general incident investigation, production recovery and external effects
are deferred. Escalate wider identity or store access, any mutation or
replay redesign, host privilege expansion, new deployment authority, and
native budget exhaustion to the owner.
