# Stage 4.1 native Linux acceptance

**Status: pending a successful privileged run and human acceptance.**
Ordinary PR CI is the offline regression suite. Its same-UID and mocked tests
do not establish native Linux UID isolation. This harness covers ADR-0001
D01–D03 and the IPC slice of D08. Production remains NO-GO.

After the workflow is on `main`, the owner (`Iliya-Tsvibel`) must separately
authorize privileged execution. Confirm the private repository has included
Actions minutes and paid overage is disabled. The owner selects `main` in
**Actions → Stage 4.1 native Linux acceptance → Run workflow**. GitHub's actor
and triggering actor must both be the owner. The privileged job runs only on
an owner dispatch or an owner rerun of an owner dispatch. A rerun uses the
original revision. To run a new revision, start a new dispatch from `main`.
GitHub Free does not provide environment required reviewers for private
repositories. Review changes to the canonical workflow before merging them
to `main`.

The job uses a disposable standard GitHub-hosted Ubuntu VM. No host accounts,
packages, or services are provisioned. Do not run it on WSL or on a persistent host.

Root stages read-only code and starts a loopback-only network namespace.
Children run as distinct numeric agent, Control Plane, and target UIDs with
explicit groups. The harness checks both legitimate IPC links, direct
agent-to-target permission denial, and target-side wrong-UID rejection with
temporary group reachability. On each link, a wrong-UID listener accepts a
connection on a socket with the expected owner, group, and mode. The client
must report an identity error, and the listener must receive EOF without any
protocol bytes. The harness also checks real ownership, mode and access/default
ACL denials, invalid frames, socket cleanup, and preservation of an existing
entry. The agent checks that the legacy loopback ports 8000 and 8001 refuse
connections while the IPC slice is live. This evidence applies to the isolated
launch profile. A separately started legacy demo is outside that profile.

Record the run URL, commit, and all five `PASS` lines before asking the owner
to accept the native evidence. A skipped workflow provides no acceptance
evidence.
