# Stage 6.D Docker threat model delta

A compromised Agent controls its process and proposals. Model output,
request fields and container metadata convey no authority. The kernel
and trusted disposable host administrator remain in scope as assumptions,
along with the Docker daemon as trusted provisioning infrastructure.

| Boundary | Required control and evidence |
| --- | --- |
| Kernel identity | Fixed distinct nonroot UIDs and GIDs. Real authenticated proposal and wrong peers denied even with socket reachability. Stop on unsupported mapping. |
| Filesystem | Read-only proposal and reviewed client code binds. Separate native sockets and stores. Existing path, ownership, ACL and state checks apply. |
| Process authority | Private shared PID namespace, separate Agent network and mount namespaces, no host PID view, daemon socket, capabilities or new privileges. Distinct UIDs protect peer process access. |
| Continuity | Agent replacement grants no authority. Preserve native replay, UNKNOWN and audit-before-disclosure. Holder loss stops peers and supplies no recovery or execution authority. |

Rootless and remapped identities, host or daemon compromise, containerized
Target replacement, production recovery and remote tenancy are outside
this candidate. See [ADR-0003](../../decisions/ADR-0003-stage6d-docker-portability.md).
