# ADR-0003 — Docker Security Portability

**PROPOSED. Production NO-GO.**

Evaluate a hybrid nonproduction profile on a disposable Linux VM. Only the
Agent client is containerized. Controller, Target, requester, approver,
inspector and durable stores remain native under existing contracts.

The initial design placed the Agent in its default private Docker PID
namespace. A native Controller in the ancestor PID namespace is invisible to
the Agent. Linux can report its peer PID as zero, which the accepted
authentication code rejects. Do not relax that check.

The revised candidate uses a separate nonroot Docker namespace holder. Trusted
disposable-runner orchestration joins native fixture processes to that
private PID namespace and launches a nonroot Agent container in it. All
authenticated peers then have positive namespace-local PIDs. Use the initial
user namespace and verify identities and group membership. Verify private
PID visibility, no external network, read-only root, zero capabilities,
no-new-privileges and no daemon socket in the Agent. Allow read-only binds
for the proposal directory at its original path and reviewed client code.
Native state and other role sockets stay outside its mounts.

Host PID sharing exposes host process metadata. Rootless Docker and
userns-remap introduce distinct subordinate UID/GID translations and
provisioning requirements. Single-container deployment needs distinct role
launches and shares mount visibility. Full split-container deployment adds
store and lifecycle questions. These alternatives are outside this slice.

Require real proposal admission, wrong-UID and wrong-server denial, state
and socket isolation, and Agent replacement with retained native state.
Keep native regression evidence separate from Docker observations. Target
container replacement and formal owner acceptance remain separate gates.

[Docker PID settings](https://docs.docker.com/reference/cli/docker/container/run/#pid-settings---pid) ·
[Docker UID and GID mapping](https://docs.docker.com/engine/security/rootless/uid-gid-mapping/) ·
[Linux PID namespaces](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)
