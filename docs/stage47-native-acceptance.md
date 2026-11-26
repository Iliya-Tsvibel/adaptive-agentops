# Stage 4.7 native hardened local acceptance

[Work-package contract](tasks/stage4/4.7-hardened-local-acceptance-contract.md)
· [Stage 4.6 cumulative plan](stage46-native-acceptance.md)
· [Campaign contract](campaigns/stage4-autonomous-completion.md)

After independent exact-diff PASS, ordinary CI, and verified squash merge,
apply the campaign owner, revision, usage and runner gates. Select every
option from `include_stage43` through `include_stage47` on the existing
read-only native workflow. Its isolated runner starts with the 28 cumulative
Stage 4.1–4.6 markers and adds the four below. Require each expected marker
exactly once and the native job to succeed.

- `PASS 4.7 kernel-bound agent proposal and explicit approver decision`
- `PASS 4.7 atomic approval consumption and correlated SQLite-only effect`
- `PASS 4.7 missing, forged, reused, revoked, expired and prior-session authority denied`
- `PASS 4.7 interrupted target claim retained unknown evidence without retry`

The test processes use distinct Linux UIDs and perform the requester,
agent and approver interactions over their normal authenticated IPC. The
approver inspects pending scope, explicitly approves the action ID and
fingerprint, then requests dispatch. The harness correlates the durable
grant, agent PDP decision, approver UID, approval consumption, reservation,
target request digest and SQLite receipt. The interrupted target retains
its claim with no effect or retry. The GitHub owner authorizes the workflow.
The test establishes the local OS principal boundary. It supplies neither
physical-human presence nor MFA for individual approvals.
