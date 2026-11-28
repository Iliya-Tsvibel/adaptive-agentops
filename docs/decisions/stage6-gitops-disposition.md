# Stage 6 GitOps disposition

**DEFER runtime GitOps. Production NO-GO.**

The tested profile uses native trusted services and a disposable Docker
Agent launch. Reviewed Git already records exact source, workflow and
image inputs. Required CI has no standing deployment role. There is no
supported Kubernetes deployment for a reconciler to manage. Introducing
Flux or Argo CD now would add a privileged writer and a new trust boundary
without evidence of a useful convergence target.

A future reconciler for a separately supported topology should act under
a dedicated scoped deployment identity. Protect branch review, verify
source and image provenance, and keep secrets and policy authority outside
workload Git writes. Use admission controls as well as scoped RBAC because
workload-template writes may change identity, mounts, image or service
account. Deny writes to trust configuration, authorization policy,
secrets, persistent stores and their retention policies unless separately
authorized. Recovery and rollback must preserve replay generations and
must never trigger an ambiguous mutation retry.

For a future disposable evaluation, demonstrate actual convergence and
drift correction for one safe workload field, denial of an attempted
prohibited change, and preservation of storage and identity across
rollback and prune. CI should validate changes without cluster
credentials. No reconciler was installed and no runtime convergence,
drift or policy denial is claimed.

[Kubernetes decision](ADR-0004-stage6-kubernetes-topology.md) ·
[Flux service-account impersonation](https://fluxcd.io/flux/installation/configuration/multitenancy/) ·
[Kubernetes RBAC good practices](https://kubernetes.io/docs/concepts/security/rbac-good-practices/)
