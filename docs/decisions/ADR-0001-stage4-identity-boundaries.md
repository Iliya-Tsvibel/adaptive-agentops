# ADR-0001 — Stage 4 Local Identity Boundaries

Status: ACCEPTED — LOCAL STAGE 4 ONLY
Approval date: 2026-11-18
Approved source commit: `4cbbc0f325ad1cfc427008c6b55d314355b19550`
Approved source blob: `b8a5653a32b95c966d47d79f05907f4aa3fb4f46`

The project owner accepted D01–D10 on the approval date above, as recorded
in the local ADR at this same path. The local commit also records the
architecture reference and final design review. This transcription preserves
the accepted decisions and constraints, with updated links and evidence
status. [Architecture and source provenance](../architecture/stage4-local.md)

## Accepted decisions

- D01–D03: Dedicated Linux UIDs, authenticated Unix sockets and server-owned identity mappings.
- D04: Bounded server-side delegation grants.
- D05: Authenticated operator IPC with explicit roles.
- D06: OS-bound dispatch authorization without JWT.
- D07: Durable target-side replay and effect records.
- D08: Separate legacy and hardened launch modes.
- D09: Frozen configuration and controller sessions.
- D10: Retained replay evidence and fail-closed capacity.

## Accepted constraints and trade-offs

- Native Linux is required for security acceptance. WSL2 is development-only pending separate review.
- Preserve accepted Stage 2 and Stage 3 invariants. No real infrastructure mutations, agent self-approval, direct target execution, or automatic retries after uncertain outcomes.
- OS identity does not provide OIDC, SPIFFE, or human MFA. Same-UID processes share one security principal. Host administrator compromise is outside scope.
- Cancellation cannot recall an already-sent request. Conservative failure handling can lose an operation.
- Production remains NO-GO. Implementation, provisioning, and publication each require their own authorization.

Stage 4.1 implemented the nonmutating IPC slice. [Stage 4.1 native evidence](../stage4-native-acceptance.md) · [Stage 4.2 task contract](../tasks/stage4/4.2-scoped-delegation-contract.md)
