# Stage 6.D Docker Security Portability technical assessment

**Formal owner acceptance pending. Production NO-GO.**

## Evaluated scope

The candidate puts only the Agent's proposal client in Docker. The Controller,
Target, requester, approver, inspector and their stores remain native. A trusted
nonroot holder anchors a private PID namespace shared by the communicating
principals. The container has its own mount and network namespaces. The
profile assumes the initial user namespace, trusted VM administrator and
trusted Docker daemon.

Linux may translate an ancestor peer's PID to zero in Docker's default PID
namespace. That fails the accepted positive-PID check. The private shared
namespace preserves that check without using host PID visibility. Rootless
and remapped Docker use different UID and GID translations and were outside
this implementation. [ADR-0003](decisions/ADR-0003-stage6d-docker-portability.md) ·
[Threat model](tasks/stage6d/threat-model.md)

## Reviewed implementation

[PR #44](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/44)
merged as `af410f24e353b466657006fb5d14a92125ee7382` from base
`be0f0237725068c4cedc19e713c197297e6ee5fe`. The reviewed PR head was
`dc2b3ea601bead996f297361ced87a70acee1937`. The review, ordinary CI,
and squash merge shared tree `1bd9d314d79714cb1f3cee77adbc3cc980348eca`
and the published binary diff SHA-256 was
`421324d870781a922d8bea7fbd6379007b8c4b47059cf50223a672637fde757c`.
[Ordinary CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46133085164/job/140942370569)
passed 1,898 tests against synthetic merge
`1f5a7f12c475fdbe4879c955b8e640eaa8ccceb9`.

The separately instantiated architecture reviewer identified the PID
namespace issue before implementation. The separately instantiated exact-diff
reviewer requested live scope denial, stale dispatch verification after Agent
replacement, stronger process and storage reachability checks, and clear
authorization status. One repair cycle cleared those findings. The separate
read-only review supplied no human security certification or platform approval.

Local focused runs passed the three unchanged cases that failed individually
across three full workstation attempts. Each full attempt passed 1,897 and
failed one different existing test. The ordinary CI run passed the complete
suite. No application runtime or ordinary pytest tests changed.

## Disposable Linux and Docker result

[Docker run 46133235506](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46133235506/job/140942822779)
passed on `af410f24e353b466657006fb5d14a92125ee7382` as a fresh attempt 1.
The recorded actor and triggering actor are both `Iliya-Tsvibel`. The
reviewed revision, staged committed source and image digest were pinned.
The public repository used a standard GitHub-hosted Ubuntu 24.04.5 runner
with Docker Engine 28.0.4.
Campaign use was **1/4 authorized runs** and **$0 additional spend**.

The job produced **3/3 distinct Stage 6.D markers**.

1. A wrong Agent UID could connect using the proposal GID but received no
   verdict. The native Controller journal recorded peer rejection.
2. The container client sent zero proposal bytes to a wrong-UID listener
   with the expected socket inode metadata.
3. The restricted container submitted a scope-denied proposal and a valid
   proposal to the existing hardened Controller. A fresh Agent container
   used a fresh grant after Controller restart. Old dispatch authority
   remained spent and the existing native receipt still described one
   simulated SQLite effect.

The proposal checkpoint checked the expected initial UID/GID maps, fixed
identities, private namespace, read-only mounts, no network beyond loopback,
zero capabilities, no-new-privileges and active seccomp. The Agent could not
open native store directories, connect to protected role socket paths, or
read the visible Controller's proc root. These are properties of the
reviewed disposable launch on the tested runner.

The same job emitted **32/32 distinct cumulative Stage 4 labels** and
**7/7 distinct Stage 6.E labels**. Their distinct names exactly matched the
[accepted Stage 6.E reference run](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/46005768880/job/140528417433).
The reused wrong-server helper emits one Stage 4.2 label again during the
Docker check. It was counted once in the native set. The native probes
covered replay and claim continuity, historical UNKNOWN, read-only
inspection, audit-before-disclosure and fail-closed fault handling under
their existing scope. They establish no new Target container or recovery
guarantee.

**Technical recommendation: ACCEPT the tested hybrid Agent-only profile for
documented local nonproduction use.** The owner retains formal acceptance.

## Boundaries that remain

- The Docker daemon and host administrator have provisioning authority.
  The container process is treated as potentially compromised. Private PID
  sharing exposes metadata about processes within that private namespace.
- The Agent container retains no durable application state. Controller
  history, Target replay/effect records, and inspection audit remain native.
  Loss of the namespace holder stops its processes. Recovery and restoration
  are outside the profile.
- Rootless, userns-remap, Docker Desktop, full containerization, root daemon
  compromise, multi-tenancy, remote identity, Kubernetes, production-grade
  audit, real external effects and external exactly-once guarantees remain
  unproven or out of scope.
- The protected operator socket is live during the Agent denial check.
  Target and inspector reachability are checked at their native fixture paths.
  Their live peer and state behavior is exercised separately by the native
  part of the run.

## Proposed MasterBrief delta

Record Stage 6.D as a bounded hybrid Agent-only Docker evaluation, with native
trusted roles and a separate owner acceptance decision. Cite the exact
assessed revision and Docker job, distinguish Docker from native
markers, and keep Production NO-GO.
