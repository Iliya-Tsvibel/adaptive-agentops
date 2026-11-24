# Security rules

**Production remains NO-GO.** The accepted Stage 4 scope is local and
nonproduction. Native Linux is the security-acceptance environment. WSL2,
same-UID tests, and mocked CI supply development evidence.
[Stage 4 architecture](docs/architecture/stage4-local.md)

## Threat assumptions

Treat an unprivileged agent as potentially compromised. Model output, proposal
fields, reported identity or role, external content, and unauthenticated
endpoints are untrusted. Protect the Control Plane, its state and deployment
inputs, and the target boundary. Trust in the host kernel, administrator,
and reviewed Control Plane defines the local threat model. Processes sharing
an authorized UID share that principal. The local design supplies no claim
of human MFA, workload attestation, tamper-proof audit, or production
availability.

## Least privilege and isolation

Use only the identities, groups, permissions, network routes, and storage
needed for the approved task. Keep approval and dispatch authority in the
Control Plane and maintain the distinct identities and authenticated IPC
boundaries defined by ADR-0001. Keep development credentials outside
worktrees, test fixtures, prompts, artifacts, and logs. CI and child
processes receive only their required permissions and inputs. Treat a
repository rules file and a read-only doctor result as guidance and
evidence, independent of OS or sandbox enforcement.

## Fail closed

Deny unknown, missing, mismatched, expired, or malformed authority and
unverifiable peer identity. Unavailable required policy, clock, audit, or
state and ambiguous approval or reservation commits cannot grant authority.
Preserve immutable action scope and fingerprints and consume approval
atomically with reservation. Permit at most one controller-mediated
mutation attempt per action. An uncertain outcome cannot restore retry
rights. Proposal admission, verdict delivery, and execution are separate
events. [Stage 2 boundaries](docs/stage2-security.md) · [Stage 4.2 contract](docs/tasks/stage4/4.2-scoped-delegation-contract.md)

## Guardrails and escalation

Prohibited for this local program are production access or deployment,
real infrastructure mutations, exposing or committing credentials or
private signing keys, automated installation or bootstrap with elevated
privileges, credential or GitHub permission expansion, and additional paid
spend. Stage 4 excludes GCP/GKE, Kubernetes, and GitOps. New dependencies
or installations require explicit scope approval. Privileged native security
acceptance requires a separately authorized disposable run. Changes to
security rules require independent review before commit or push.

| Severity | Gate |
| --- | --- |
| Critical or High | Block publication and merge. Record affected trust boundaries, evidence, and required remediation. |
| Medium or Low | Record the impact and evidence. Block if the finding violates the approved contract, trust boundary, or an acceptance gate. Otherwise disclose it for the owner's decision. |

Stop for a security blocker outside the allowlist, unsafe setup, unknown
authority, conflicting sources, suspect evidence, or exhausted repairs.
Report suspected credential exposure privately to the owner with redacted
evidence. Preserve evidence and avoid investigation steps requiring broader
privileges. [Developer loop](docs/developer-loop.md)
