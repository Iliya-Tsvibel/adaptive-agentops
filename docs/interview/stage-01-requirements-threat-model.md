# Stage 1 — Requirements, Architecture, Threat Model and Permission Matrix

**Status:** Corrected Baseline v0.1.1 accepted as the Stage 1 specification, *not* a proof of running code.

## Problem / product use case

The intended Incident Investigator should examine a bounded test incident, read allowed GitHub and mock service evidence, distinguish observations from hypotheses and recommend action. It must not transform untrusted tool output or LLM text into the authority to execute infrastructure changes.

The original PRD describes **four demonstrations**: (A) allowed investigation with evidence, (B) denied tool call with zero downstream effect, (C) one approval-gated mock restart, (D) graceful source failure without invented facts. This complete Incident Investigator (including real GitHub reads, logs, sourced final report) is **still deferred** after Stage 4. Don't claim it is done.

## WHY — define security contracts before coding

Stage 1 produced five artifacts: MVP Scope/PRD, Architecture, Threat Model, Permission Matrix and Baseline Decision Record. The corrected v0.1.1 resolved three blocking review issues:

1. **G1 / R1 — atomic authorization lifecycle:** one immutable `action_id` and fingerprint, bounded TTL, one transaction for approval consumption, unique reservation and pre-dispatch audit; `UNKNOWN` and no automatic mutation retry after uncertainty.
2. **G2 / R2 — separate local operator:** approval is recorded only through an operator CLI, never through the agent or requester HTTP interface. This initially assumed trusted local OS and did **not** authenticate an independent human.
3. **G3 / R7 — split test ownership:** Stage 2 proves deterministic gateway controls before LLM integration; Stage 3 verifies model boundaries. Stage 4 later hardens identity and separation of privileges.

The original v0.1 Work review was NO-GO for missing specification clarity. The corrected v0.1.1 allowed moving to Stage 2; that initial NO-GO is **historical design feedback**, not evidence of an implementation breach.

## HOW — core architecture vocabulary

- **PEP / Tool Gateway:** enforcement point before any tool I/O.
- **PDP:** deterministic default-deny policy deciding `ALLOW`, `DENY`, `APPROVAL_REQUIRED`.
- **Run context:** trusted local labels in early stages, **not verified identity**.
- **Typed proposal:** fixed operation, target, environment and bounded arguments, not arbitrary shell/HTTP.
- **Pending action + fingerprint:** server-owned immutable description of the proposed mutation.
- **Approval:** one decision about one exact pending action.
- **Reservation:** atomic consumption preventing two controller-mediated attempts.
- **Audit:** separately records decision, reservation and observed result.
- **Tool adapter:** a bounded connection to the mock API. LLM content must pass the PEP.

### Trust boundaries from the accepted threat model

| Boundary | Threat | Minimum control |
| --- | --- | --- |
| Human/request → service | Caller impersonates user, claims `X-Role` | Server-owned run context; bounded input |
| LLM → gateway | Model asks for arbitrary or privileged operation | Named tool registry; strict schema; default deny |
| Gateway → target | Bypass / arbitrary destination | Fixed allowed adapter and target; policy before I/O |
| External GitHub/logs → LLM | Prompt injection, forged incident evidence | Treat content as data; provenance checks; no authority from content |
| Operator → approval store | Self-approval, substitution, replay | Separate decision path; action binding and one-time consumption |
| Execution → audit | Missing evidence or ambiguous commit | Required pre-dispatch commit, explicit UNKNOWN |
| Runtime ↔ model provider | Secrets, unbounded retries/outputs | Fixed endpoint/model, bounded requests, no model-native tools |

## FAILURE MODE / SECURITY

- Policy exception: **deny**, not an implicit allow.
- Missing/mismatched/expired/reused approval: **zero mutation calls**.
- Pre-dispatch audit failure: **no dispatch**.
- Controller crashes after reservation but before sending: the authorized opportunity may be lost; never auto-retry.
- Lost reply after send: **UNKNOWN**, not `FAILED` and not proof of no effect.
- Prompt-injected text from logs or README is untrusted even if persuasive.
- A local logical identity fixture does not protect against arbitrary hostile code on the same host; this motivated Stage 4.

## TRADE-OFF

Static deterministic policy + SQLite + narrow named tools maximize inspectability and testability at local scope. OPA, OIDC, a model framework and Kubernetes were intentionally deferred. The earliest design did not provide real workload identity or protected Target-side enforcement.

## Interview questions

**What is the difference between a requirement, a threat and an invariant?** A requirement defines desired behavior; a threat describes a failure or attack path; an invariant states a property all allowed paths must preserve, such as no dispatch without a valid approval.

**Why isn't a prompt saying “never restart” sufficient?** Model output is untrusted intent. Only a separate deterministic gateway can mediate authority.

**How does fingerprint binding help?** It binds the approval to a canonical immutable action scope. The hash is not a signature or proof against a compromised database.

**What does “complete mediation” mean?** Every tool call crosses the authorized enforcement boundary; tests must assert zero adapter calls on denial. This was only an application-level guarantee before native process isolation.

**Why is the four-demo PRD not yet complete?** Later accepted work focused on the security vertical slice; real GitHub/log investigation, provenance-backed diagnosis and final report were not implemented.

## Explain in 60 seconds

> We wrote the PRD and threat model before coding. The key review correction was to make the action, approval, reservation and audit semantics unambiguous and separate operator approval from agent proposals. The resulting contract let us build a deterministic gateway first, then introduce LLMs and finally harden OS identity without confusing a policy decision with execution authority.

## Source and evidence

- Project Source: `Adaptive_AgentOps_Stage_1_Corrected_Baseline_v0.1.1.zip` (five Markdown documents); especially Permission Matrix v0.1.1 §§5,7.
- [Stage 2 security implementation evidence](../stage2-security.md).
- [Stage 4 ADR-0001](../decisions/ADR-0001-stage4-identity-boundaries.md).
