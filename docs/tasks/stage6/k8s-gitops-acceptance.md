# Kubernetes and Argo CD vertical slice

**Proposed disposable lab. Production NO-GO.**

Build a single-node kind cluster. One Restricted Deployment runs one Pod
with a private shared PID namespace and six nonroot role containers:
agent, controller, target, requester, approver, inspector. A seventh
container supplies only the reachable wrong-UID fixture. Distinct
per-role groups are supplied by the reviewed image's passwd and group
tables and verified at runtime. No pod-wide supplemental groups or
fsGroup. No workload token or API RBAC. Application roles run the
existing protocol clients and services using distinct identities.

The disposable administrator provisions seven narrow local volumes
outside reconciliation. Four isolate proposal, operator, Target and
inspection sockets. Three retain Controller, Target and audit state.
Only designated peers receive socket mounts, and client mounts are read-only. Application
containers drop all capabilities, use default seccomp with no privilege
escalation, and have read-only roots. Bootstrap creates initial stores
once. Startup and replacement must never reinitialize missing stores.
Restoration and rollback remain unsupported and require explicit
operator disablement and a separately trusted generation check before
execution can resume. The lab does not claim such an anchor exists.

Argo CD uses the pinned upstream namespace distribution. An exact
AppProject source and destination admits a single Deployment. A scoped
destination account receives Deployment creation during initial sync of
a pinned trusted Git commit. The trusted runner then removes create
authority before permitting Git branch changes. Ongoing access grants
only read and named Deployment patch/update, without workload exec,
delete, persistent volume, secret, RBAC or policy writes. A bootstrap-owned
ValidatingAdmissionPolicy freezes the Deployment spec. The harmless
Git-controlled value is a top-level Deployment annotation. The repository
is served read-only inside the lab's Docker network and no Git credential
is provided to Argo or CI. No automated pruning or cascade finalizer.

Acceptance requires actual authenticated approval, simulated effect,
denied replay, historical UNKNOWN, durable inspection audit, Pod
replacement with all stores retained, real Git initial sync and change,
live drift correction, and admission-denied trust-boundary edits. The
disposable test operator can exec scripted clients in the existing role
containers and is trusted. The Agent has neither those rights nor their
mounts. Evidence reports distinguish simulated failure injection from
actual Kubernetes and Argo observations.

[Historical Kubernetes evaluation](../../decisions/ADR-0004-stage6-kubernetes-topology.md) ·
[Kubernetes shared process namespace](https://kubernetes.io/docs/tasks/configure-pod-container/share-process-namespace/) ·
[Argo CD installation](https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/)
