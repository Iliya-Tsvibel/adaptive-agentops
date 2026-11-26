# Stage 2 — Deterministic PEP/PDP, Approval and SQLite

**Status:** Accepted **trusted local** deterministic security MVP (historical baseline `5962e0a`). No hostile-process workload authentication is claimed.

## Problem

Build a deterministic tool authorization path *before* introducing an LLM. A model displaying “I refuse” is not security evidence if an unguarded adapter still executes the operation.

## HOW: the request lifecycle

1. Trusted internal code constructs `RunContext` labels for one fixed requester, agent and environment. These labels are **not authenticated principals**.
2. The strict proposal schema names a recognized operation: `mock.get_health` or `mock.restart_service` for `demo-api` in `local-mock`, with empty arguments.
3. The **PDP** returns `ALLOW`, `DENY` or `APPROVAL_REQUIRED` by a default-deny static policy. The **PEP** / Gateway enforces the decision before adapter calls.
4. A permitted health read requires confirmed `POLICY_DECISION` audit *before* GET. If required audit fails, no read is dispatched.
5. A restart proposal creates an immutable `PENDING` action with server-issued `action_id`, canonical SHA-256 fingerprint, bounded ten-minute lifetime and policy evidence in one database transaction.
6. The separate local operator CLI reviews exact stored scope and commits `APPROVED` or `REJECTED` for one action. A typed terminal confirmation **does not** cryptographically verify a human.
7. The Controller freshly rechecks action, approval, run/agent/scope, fingerprint, expiry, unconsumed status and current PDP decision.
8. In one SQLite `BEGIN IMMEDIATE` transaction it **consumes approval, inserts unique dispatch reservation and records `DISPATCH_RESERVED`**. Only a *confirmed* commit permits at most one Controller-mediated attempt.
9. The fixed mock HTTP adapter attempts one POST with transport retries, redirects and environment proxies disabled; outcome is recorded separately. `ACKNOWLEDGED` means the Controller validated the simulated acknowledgement and confirmed its result commit.

## WHY these constructs exist

**PEP vs PDP:** PDP calculates what should be allowed. PEP guarantees that forbidden I/O does not occur. A policy document alone is not enforcement.

**Action ID vs fingerprint:** a fingerprint identifies immutable canonical scope, but two valid actions can have the same scope; an action ID distinguishes their separate approval histories. SHA-256 is *not* a digital signature and does not protect a database owner from rewriting records.

**SQLite `BEGIN IMMEDIATE`:** competing writers cannot both win a unique approval/reservation race; atomic consume+reserve+audit prevents double-dispatch by the supported Controller path. SQLite does **not** atomically commit an external HTTP effect with its own rows.

**Recheck at dispatch:** policy, expiry or approval state may change since the pending action was first created. Initial intent and approval recording are not a perpetual right to send.

## FAILURE MODE and correct semantics

| Window | Known versus unknown | Allowed response |
| --- | --- | --- |
| Pending action `COMMIT` ambiguous | An action may have persisted | Return uncertainty, no duplicate authorization |
| Approval expired, fingerprint mismatch, DENY | No valid authority | Zero adapter calls |
| Reservation rollback confirmed | No usable reservation | No send |
| Reservation `COMMIT` ambiguous | Approval might already be consumed | No send, no rehydrated permit |
| Crash after reservation before POST | Approval consumed, effect possibly absent | Lose the opportunity rather than retry |
| Timeout or connection loss after POST | Target may have acted | `UNCERTAIN`, no automatic mutation retry |
| Result audit write ambiguous | Effect may have happened and evidence may be incomplete | `AUDIT_UNCONFIRMED`; never infer no effect |

A stored reservation is not proof that POST occurred. An outcome record is not an atomic guarantee of external behavior. These are the foundations of later Stage 4 replay protection.

## SECURITY LIMITS

The Stage 2 mock target HTTP port has no independent identity/authorization and can be called directly by a process with network access. Same-user hostile code can bypass trusted Python entry points. SQLite audit is **not tamper-proof**. The project did not yet prove OS identity, authenticated operator separation or target-side replay enforcement.

## TRADE-OFF

Python/FastAPI/SQLite and a fixed mock API reduce complexity and support deterministic negative tests. This is a safer learning foundation than a premature distributed stack but deliberately does not provide high availability, cryptographic workload identity or genuine production restart.

## Interview questions

**Why not retry after timeout?** The target might have applied the mutation before the acknowledgement was lost; retry can duplicate the effect.

**How do you verify deny behavior?** Assert the real downstream adapter invocation count is zero, not just the assistant's text.

**What does the reservation do?** It uniquely spends an approval and records one potential send opportunity, without proving delivery or execution.

**What are the major gaps?** Host/process identity and direct Target access; Stage 4 explicitly introduced a stronger local hardened profile.

## 60-second pitch

> Stage 2 established deterministic policy enforcement. Typed proposals pass a default-deny PDP and Gateway. A restart becomes an immutable pending action, requiring separate operator approval. An atomic SQLite transaction consumes one approval and reserves one dispatch before a bounded mock HTTP call. Ambiguous persistence or response never restores mutation retry rights. The accepted guarantee was local and assumed trusted process and storage boundaries.

## Evidence

[Stage 2 security](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/stage2-security.md), [policy tests](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/tests/test_policy.py), [dispatch tests](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/tests/test_dispatch_control.py), [execution controller](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/adaptive_agentops/execution_controller.py).