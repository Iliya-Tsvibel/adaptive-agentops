# Stage 4.2 native Linux acceptance

**Pending a separately authorized native run and owner acceptance.** The
workflow exercises the scoped proposal-admission contract and reruns the
accepted Stage 4.1 harness against the same checkout. Ordinary PR CI exercises
deterministic tests. Production remains NO-GO.

## Trust and staging

The workflow accepts a fresh owner dispatch from `main` in
`Iliya-Tsvibel/adaptive-agentops`. It checks the original and triggering
actors, grants the job only read access to contents, and pins checkout by
SHA. The disposable standard GitHub-hosted x86-64 Ubuntu 24.04 VM must run
native Linux outside WSL and containers.

As the runner user, pip downloads binary wheels using `--require-hashes`.
The driver selects five existing pins from `.github/ci-requirements.txt`.
The exact set is Pydantic, pydantic-core, annotated-types, typing-extensions,
and typing-inspection. The runner reads the reviewed setup program and
the allowlisted source blobs from the exact commit through Git. It sends
the sources and downloaded wheel bytes through a bounded bundle on standard
input. The root process never opens source paths in the checkout or the
runner's wheel directory. It runs with a cleared environment inside a
loopback-only network namespace, under the 180-second privileged deadline.

The failed run stopped at the original staging metadata guard before either
IPC harness started. The recorded Ubuntu image's build recipe sets `/opt`
to mode `0777`, which explains the expected parent-mode failure. The job
did not log the observed metadata, so the exact failed predicate on that VM
remains unconfirmed. The workflow now uses `/run`, Linux's transient runtime
directory. [Failed run](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45604533282)
· [Runner image build recipe](https://github.com/actions/runner-images/blob/ubuntu24/20261116.376/images/ubuntu/scripts/build/configure-system.sh#L19)

Before writing below `/run`, the setup program checks `/` and `/run` without
following final symlinks and requires root ownership, search access for the
probe UIDs, and no group or other write access. It rejects an inherited default ACL and atomically creates a
private staging directory. Allowlisted files are created exclusively inside
that directory. The directory becomes traversable for service UIDs only
after staging, with root-owned read-only source and private wheel inputs.
Protected ancestors keep unprivileged users from swapping the checked names
between validation and use. The driver independently checks the staging
metadata before importing staged code and revalidates dependencies before
any security probe. The design trusts the approved commit, workflow,
disposable runner administrator, system Python, and kernel.

The root orchestrator uses the standard library. It verifies the staged
wheel set and each SHA-256 before extracting regular files into a new
root-owned dependency directory. Extraction bounds size and rejects unsafe
paths, links, duplicate files, wheel relocation directories, and `.pth`
files. The probe starts with `-I -B -S` and adds only the staged source and
dependencies to the standard-library search path. Only the unprivileged
probe imports third-party code. No installation changes the host Python.

Root reuses the Stage 4.1 numeric identities, explicit groups, socket
metadata checks, listener readiness checks, and cleanup. Each wrong-peer
test gives the peer enough filesystem access to reach the application
credential check. The wrong-server case pins the socket inode while
correcting its filesystem metadata. The peer UID remains the UID of the
process that bound the socket. The bounded job removes the staging tree,
and the driver closes child processes and removes its temporary sockets.
Catchable setup failures unwind the staging directory. A hard kill can
interrupt cleanup, with VM disposal as the backstop. Setup failures report
bounded ownership, mode, type, or operation diagnostics. A separate start
line marks entry into the security probes. A setup failure leaves both slices
unverified.

## Evidence and limitations

The genuine live-grant case uses the existing PDP and admission client. The
other cases distinguish peer rejection, delegation denial, PDP denial,
invalid framing, a frame-read timeout, injected journal failure, and a
recorded decision without verdict delivery. Expiry and journal faults are
deterministic test fixtures.
The PDP denial fixture calls the existing evaluator with a requester outside
the policy scope to exercise denial after a live grant matches. It creates
no new caller-selectable authority. The wrong-UID and framing cases use raw
clients to establish filesystem reachability or observe EOF independently
of the proposal client.

The proposal phase stages no execution adapters, target service, approvals,
or dispatch code. Probe-side checks fail on attempts to contact a target,
open SQLite, start subprocesses, or use application modules outside the
staged admission path. These checks detect accidental execution paths in
the reviewed fixture. They do not sandbox a compromised Control Plane.
The journal records the evaluation. A recorded admission does not prove
verdict delivery or convey execution authority or target effects.

## Separately authorized run

After merge, verify the approved `main` SHA and confirm included Actions
usage and blocked paid overage. Verify the reviewed workflow is registered
on the disposable Ubuntu 24.04 runner with a read-only contents token, a
five-minute job limit, the owner and attempt-one guards, and no competing
native run. With separate approval for one native run, the owner may dispatch
the exact workflow from `main`.

```bash
gh workflow run stage4-2-native-acceptance.yml --repo Iliya-Tsvibel/adaptive-agentops --ref main
```

Record the workflow URL, exact executed SHA, successful job conclusion, all
five Stage 4.1 markers and the six Stage 4.2 markers exactly once.

- `PASS ownership, mode, POSIX ACL and existing-entry denial`
- `PASS agent to control mutual authentication and cleanup`
- `PASS control to target, agent denied directly, legacy loopback ports closed`
- `PASS wrong UID with socket access, invalid frame and denial cleanup`
- `PASS both clients rejected wrong-UID listeners before sending data`


- `PASS 4.2 distinct UID proposal admission and no execution`
- `PASS 4.2 wrong agent UID with socket reachability`
- `PASS 4.2 framing and transport rejection and reconnect budget enforced`
- `PASS 4.2 missing expired and out-of-scope delegation and PDP denial`
- `PASS 4.2 wrong server UID received zero proposal bytes`
- `PASS 4.2 journal failure and recorded decision without a delivered verdict`

A failed or skipped run provides incomplete evidence. Stop after the
authorized attempt and request owner acceptance of the complete evidence.
