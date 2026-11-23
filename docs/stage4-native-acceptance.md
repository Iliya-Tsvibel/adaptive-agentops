# Stage 4.1 native Linux acceptance

**Status: pending a successful privileged run and human acceptance.**
Ordinary PR CI is the offline regression suite. Its same-UID and mocked tests
do not establish native Linux UID isolation. This harness covers ADR-0001
D01–D03 and the IPC slice of D08. Production remains NO-GO.

After the workflow is on `main`, obtain separate owner approval for privileged
execution. Confirm the private repository has included Actions minutes and
paid overage is disabled. In **Actions → Stage 4.1 native Linux acceptance →
Run workflow**, select `main` and confirm the approval input. The job uses a
disposable standard GitHub-hosted Ubuntu VM. No host accounts, packages, or
services are provisioned. Do not run it on WSL or on a persistent host.

Root stages read-only code and starts a loopback-only network namespace.
Children run as distinct numeric agent, Control Plane, and target UIDs with
explicit groups. The harness checks both legitimate IPC links, direct
agent-to-target permission denial, and wrong-UID rejection after a successful
connect with temporary group reachability. It also checks real ownership, mode
and access/default ACL denials, invalid frames, socket cleanup, and preservation of an existing
entry. The agent checks that the legacy loopback ports 8000 and 8001 refuse
connections while the IPC slice is live. This evidence applies to the isolated
launch profile. A separately started legacy demo is outside that profile.

Record the run URL, commit, and all four `PASS` lines before asking the owner
to accept the native evidence. A skipped workflow provides no acceptance
evidence.
