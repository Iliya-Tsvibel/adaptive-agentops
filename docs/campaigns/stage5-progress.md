# Stage 5 progress ledger

Stage 4 closed on main `bd52eced886768ffee57336724c46cf4ebaeb6d2`,
tree `26db8a0171a1409df8a754986132d65ca5983dde`. The accepted executable
revision is `34c38d9087b5c34b97fa3d4b68cafc3dc8219e0a`, with 32 native
markers. The closure was documentation-only.
[Stage 4 closure](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/25)
· [Stage 4 acceptance](../stage4-acceptance.md)

## Technical baseline

The isolated planning branch started at verified remote main with the
local Stage 5.0 draft as its only untracked input. No Stage 5 application
code was present on main. Controller reservations, outcomes, effect digest
and number, Stage 2 approval consumption and Controller session bindings
supply the Controller evidence. Target claims are keyed by dispatch and
action and carry a digest and nullable effect number. Lookup collapses a
missing row and a claim without a receipt into UNKNOWN.

The local test environment is existing Python 3.12 under WSL2. Ordinary
CI runs pytest and is development evidence. Stage 4 has separate native
acceptance workflows. The Stage 5 campaign has used **0/4** native
dispatches. The offline design requires none. Runtime authentication,
authorization, credential handling and security-rule edits retain the
pre-commit independent review gate. Production remains NO-GO.

## Checkpoint

Stage 5.0 reconciles the local offline-first plan with the actual stores
and schemas. The earlier live-first draft remains historical proposal
material. The owner decision and resulting Stage 5.1 progress appear below.
[Earlier draft #26](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/26)
· [Stage 5.0 plan](../tasks/stage5/5.0-architecture-security-plan.md)

## Stage 5.1

The owner approved Stage 5.0 and the synthetic-only Stage 5.1 slice in the
campaign conversation. PR #27 merged at `fd15fde5569df7a25e2bf848ff3b95605b681199`
with the independently reviewed tree. The older draft #26 was closed.
[Architecture PR](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/27)
· [Architecture CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45833154701)

Stage 5.1 adds a bounded pure projection and versioned fixtures. Its
contract records the evidence and provenance rules. Native usage remains
0/4. Ordinary CI and exact review evidence are recorded in the publication
PR. Production remains NO-GO.
[Stage 5.1 contract](../tasks/stage5/5.1-offline-fixture-contract.md)

## Stage 5.2

The owner approved the exact bounded local diagnostics contract after
independent architecture review. The implementation adds one opt-in
formatter for synthetic Stage 5.1 results. It produces redacted and
bounded local JSON and has no authority over execution or audit state.
Exact-diff review, regression and merge evidence are recorded in the
publication PR. Native usage remains 0/4. Production remains NO-GO.
[Stage 5.2 contract](../tasks/stage5/5.2-bounded-local-diagnostics-contract.md)

## Stage 5.3

The owner authorized the evaluation-only scope in PR #30, including
independent freeze and exact-diff review, ordinary CI and a verified merge.
[Owner authorization](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/30#issuecomment-7202319666)
· [Stage 5.3 contract](../tasks/stage5/5.3-deterministic-evaluation-contract.md)

The read-only oracle reviewer independently passed the pre-score v1
freeze at SHA-256
`7c7341a596ca034ea8f60cfbd932f7108e8c37b8b2af07579211d9cc44d0c755`.
The reviewer verified four new cases per family, including equal capture
order and distinct earlier-complete and earlier-partial provenance.
The frozen v5 regression manifest contributes zero to the new denominator.

The scored local run used Python 3.12.3 and two independent constructions
per case. It passed 16/16 new cases, 4/4 per family, with 13/13 classifications,
13/13 source provenance checks and zero failed critical checks. The three
formatter-only cases were exempt from classification and provenance.
The focused run passed 85 tests, including the existing regression tests.
The publication PR records full regressions, exact implementation review,
CI and merge evidence. Stage 5 native use remains 0/4.
