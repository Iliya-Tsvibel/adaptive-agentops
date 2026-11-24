# Approved Stage 4 local architecture

Status: ACCEPTED DECISIONS — LOCAL STAGE 4 ONLY
Approved source commit: `4cbbc0f325ad1cfc427008c6b55d314355b19550`

[ADR-0001](../decisions/ADR-0001-stage4-identity-boundaries.md) is the approval
record for D01–D10. The table is an index of those accepted decisions. Task
contracts and revision-bound acceptance records define what was implemented
and verified.

| Decision | Accepted local boundary |
| --- | --- |
| D01 | Dedicated Linux UIDs for agent, Control Plane, and target. |
| D02 | Restricted Unix sockets with peer-credential checks at both ends. |
| D03 | Protected UID-to-principal mappings and server-owned run associations. |
| D04 | Bounded, Control Plane–stored delegation records. |
| D05 | Authenticated operator IPC with explicit per-method roles. |
| D06 | OS-bound dispatch authorization without signed bearer artifacts. |
| D07 | Durable target-side replay and effect records. |
| D08 | Separate legacy and hardened launch modes. |
| D09 | Policy and identity configuration frozen for the controller session. |
| D10 | Retain unresolved reservation and replay evidence and fail closed at capacity. |

## Current evidence

- [Stage 2 security](../stage2-security.md) and [Stage 3 acceptance](../stage3-acceptance.md) describe the trusted local MVP and its limits.
- [Stage 4.1 native acceptance](../stage4-native-acceptance.md) covers the isolated, nonmutating authenticated IPC boundary at its recorded commit.
- [Stage 4.2 contract](../tasks/stage4/4.2-scoped-delegation-contract.md) defines a fixture-issued, session-only proposal-admission slice. [Stage 4.2 native acceptance](../stage42-native-acceptance.md) remains pending separate owner authorization and evidence.

## Provenance and historical references

The local source commit above records these original paths and Git blobs.
The original objects are held in the local source repository.

| Original path | Git blob | Status |
| --- | --- | --- |
| `docs/decisions/ADR-0001-stage4-identity-boundaries.md` | `b8a5653a32b95c966d47d79f05907f4aa3fb4f46` | Accepted owner decision record. |
| `docs/stage4-architecture-v0.2-draft.md` | `b847798d9fe6899b85597d416115c34e67869ec2` | Historical draft linked by the ADR. Acceptance covers D01–D10 through the ADR. Other draft proposals retain their recorded status. |
| `docs/reviews/stage4-v0.2-final-review.md` | `89c2ca278b082bd74df6bcdef49a358d54eb8e93` | Historical independent design review. |
| `docs/stage4-architecture-security-contract.md` | `5cd8e8cbec2b557117dfe96d402b64a927bc3874` | Superseded, unapproved signed-artifact proposal. |
| `docs/reference/stage1/Adaptive_AgentOps_Threat_Model_v0.1.1.md` | `105ee5a20f241d5d632c9d6a7aba976937a77603` | Historical baseline threat model. |

Production remains NO-GO.
