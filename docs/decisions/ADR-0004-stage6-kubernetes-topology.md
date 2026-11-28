# ADR-0004 — Kubernetes topology

**DEFER the co-located topology. Reject cross-node UDS and a direct
Agent-Pod/native lift-and-shift under the current protocol. Production NO-GO.**

## Current contract

Stage 4 authenticates a positive peer PID and the configured kernel UID,
then applies role, scope, and per-operation authorization. Both sides also
validate the socket path, ownership, mode and ACLs. The protected state
code pins directories and checks ownership, file modes, links, inode
identity, locking, SQLite journal behavior and schema. Stage 6.E requires
exclusive access to Controller execution state throughout inspection and
durable commit of the separate disclosure audit before reply.

See [ADR-0001](ADR-0001-stage4-identity-boundaries.md),
[ADR-0002](ADR-0002-stage6-evidence-bridge.md),
[peer authentication](../../adaptive_agentops/stage4_boundary/__init__.py),
[Controller state](../../adaptive_agentops/stage4_boundary/controller_state.py),
and [inspection](../../adaptive_agentops/stage4_boundary/evidence_inspection.py).

## Options and evidence

| Topology | Feasibility and disposition |
| --- | --- |
| Agent Pod and native trusted peers | A pathname mount alone cannot make the Controller visible in the Agent's PID namespace. The existing checker rejects a nonpositive peer PID. Host PID sharing and hostPath would widen visibility and require exceptions to the normal Pod Security profile. Reject this direct lift. |
| Separate Pods or nodes | Separate Pod PID namespaces can hide peers even on one node. A filesystem mount over multiple nodes cannot transport a live kernel socket or its credentials. Replacing UDS with a network identity protocol would require a new identity, provisioning and authorization design. Reject this mapping of the current protocol. |
| One node and one Pod with shared process namespace | Positive peer PIDs are plausible. Distinct per-container UID/GID, role-scoped mount visibility, process access denial and persistent ownership remain configuration-dependent. A node-local experiment is possible, but proving only socket connectivity would advance little beyond the tested Docker profile. Defer a workload implementation until storage continuity and workload admission justify one. |
| Tested Docker hybrid | Retain the Agent-only profile with native trusted roles, native stores and shared private PID namespace. The contained Agent has separate mount and network namespaces. Its evidence is bound to the assessed Linux runner and exact revision. |

A Pod shares its network namespace across containers. NetworkPolicy cannot
isolate one container from another within that shared namespace. Sharing
processes also exposes process metadata and makes cross-container procfs
access depend on Unix enforcement. Use fixed nonroot per-container UIDs
and primary GIDs. Pod-wide supplemental groups and fsGroup can grant group
membership to roles that should not have it or change store ownership and
mode. Strict supplementary group handling, where supported, would still
require runtime verification. Pod user namespaces translate host identities
and require a compatible runtime and filesystem, so they need their own
credential and ownership evidence.

[Pods](https://kubernetes.io/docs/concepts/workloads/pods/) ·
[Shared process namespace](https://kubernetes.io/docs/tasks/configure-pod-container/share-process-namespace/) ·
[Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/) ·
[Security contexts](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/) ·
[User namespaces](https://kubernetes.io/docs/concepts/workloads/pods/user-namespaces/) ·
[Linux PID namespaces](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)

## Revisit gates

- Use a dedicated, pinned, disposable single-node Linux cluster. Require
  restricted admission, default seccomp, all capabilities dropped,
  no privilege escalation, no host namespace or hostPath exceptions,
  and no ServiceAccount token in application containers.
- Prove live peer PID/UID mapping, role-specific mounts, protected
  ownership/ACLs, and inaccessible protected process roots. Separate
  trusted operator launch from workload exec and Pod mutation authority.
  A workload or namespace operator able to select role UIDs is privileged.
- Prove actual backing filesystem behavior for SQLite locks, durable
  commit, and directory semantics. A PVC identity or StatefulSet ordinal
  does not attest to storage generation. Local PV placement binds service
  availability to a node. A single-Pod mount mode is not a restoration
  protocol. Validate exclusion of overlapping execution and maintenance,
  retained claims after Pod replacement, and fail-closed behavior after
  missing, stale or incomplete restoration before enabling mutations.
- Exercise the wrong UID/server, unauthorized operator and inspector,
  lost acknowledgement, claim-only evidence, and audit ambiguity using
  the actual deployment. Require a new review for multi-node identity or
  state migration.

[Persistent volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/) ·
[StatefulSets](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/) ·
[Service accounts](https://kubernetes.io/docs/concepts/security/service-accounts/) ·
[RBAC good practices](https://kubernetes.io/docs/concepts/security/rbac-good-practices/)

No Kubernetes workload or cluster was started. This is a feasibility
decision supported by the existing implementation and platform semantics.
