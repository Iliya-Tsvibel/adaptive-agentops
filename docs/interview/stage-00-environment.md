# Stage 0 — Environment Setup & Audit

**Status:** Completed (project MasterBrief). **Nature:** working environment and cost/scope audit, not a security boundary acceptance.

## What problem were we solving?

A Cloud/Platform portfolio project can waste time and money before there is a runnable product: an immediate GKE cluster, several agents, OpenTelemetry, Terraform and a paid model provider do not prove the core authorization design. Stage 0 established a local-first workflow and a dependency order.

## WHY

- **Local-first / zero additional paid spend:** make experiments reproducible without idle cloud resources and avoid conflating an infrastructure bill with engineering progress.
- **Use familiar tools deliberately:** Git/GitHub, Cursor/Codex, Python and a local development environment; install new dependencies only when a tested slice needs them.
- **Do security before AI authority:** design identity, policy, audit and explicit approval even in the deterministic workflow; the AI agent is not allowed to become a privileged shortcut.
- **Prefer a small vertical slice:** test one `demo-api` scenario before deploying an entire platform.
- **Learn Go just in time:** do not let a new programming language displace the flagship security architecture.

## HOW — development workflow

`Requirements / threat model → small task contract → isolated worktree → implementation → deterministic tests → independent review → CI → acceptance evidence`.

The development host later used WSL2; this is a **development environment**, not the native Linux security-acceptance environment. Stage 4 used disposable GitHub-hosted native Ubuntu runners for Linux UID, filesystem ACL and `SO_PEERCRED` claims.

The Stage 0 Project Source does not supply a trustworthy complete inventory of exact OS, Docker, hardware or tool versions. **Do not invent that inventory during an interview.** Check the archived environment audit or reproduce `python --version`, `git --version`, `docker version` and environment information if asked.

## FAILURE MODE / SECURITY

- An agent can propose an implementation that assumes local tooling, permissions or secrets which do not exist. The task must fail with an explicit prerequisite gap rather than silently adding privileged dependencies.
- CI may pass ordinary Python tests yet fail kernel-level isolation. An unsupported sandbox/WSL result is not proof of native Linux UID security.
- A read-only developer script is guidance; actual OS permissions, sandbox restrictions and GitHub permissions are the enforcement layers.
- GitHub-hosted runner failure can be a staging/mount issue; distinguish infrastructure setup failure from demonstrated authorization failure.

## TRADE-OFF

Local SQLite, mock infrastructure and a local model are cheap and controllable but do not prove distributed deployment, cloud IAM, availability or real infrastructure effects. These are assigned to later stages of the 0–9 roadmap.

## Interview Q&A

**Why not start with Kubernetes and Terraform?** Because the differentiator is secure authorization of AI-agent actions. Kubernetes without correct trust boundaries adds components but not evidence of that property.

**Why Cursor, Codex and CI?** Cursor is the developer workspace; Codex assists implementation; GitHub and independently reviewable evidence preserve provenance. AI output is never accepted solely because it appears plausible.

**When does a local test become security acceptance?** Only when the agreed test genuinely exercises the required trust boundary under the supported environment and matches an exact reviewed revision.

**What does zero additional spend mean?** No new paid service, cloud or API expenditure was authorized. It does not mean electricity or existing compute are literally free.

## Explain in 60 seconds

> Stage 0 made the project local-first and evidence-driven. We audited development prerequisites, deferred unnecessary tools and spending, and established a small-slice workflow. We explicitly separated WSL2 development from native Linux security acceptance, so later UID/IPC claims were tested on disposable native runners rather than inferred from ordinary unit tests.

## Source and evidence

- Canonical IT Projects MasterBrief, `Current State → Stage 0`.
- Project Source: `Environment Setup & Audit.txt` (historical).
- [Developer loop](../developer-loop.md), [Stage 4 native acceptance](../stage4-native-acceptance.md).
