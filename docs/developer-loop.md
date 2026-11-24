# Autonomous DevLoop v0.2

The [repository instructions](../AGENTS.md), [security rules](../SECURITY.md),
and [approved architecture](architecture/stage4-local.md) define the
boundaries. A task can authorize automation within those boundaries. The
owner retains the merge decision and separately authorizes privileged native
acceptance. Production remains NO-GO.

## Task contracts

Before editing, record the approved base SHA, objective, file allowlist,
required verification, and the specific publication actions authorized by
the owner. Check the current remote base with approved read access. Resolve
approval provenance and architecture conflicts before changing files.
[Stage 4.1 accepted scope](stage4-native-acceptance.md) ·
[Stage 4.2 task contract](tasks/stage4/4.2-scoped-delegation-contract.md)

Run the read-only doctor from the isolated worktree before editing.

```bash
python3 scripts/devloop_doctor.py --expected-base <verified-base-sha>
```

The full check requires a clean linked worktree at the specified base and
checks local origin configuration and cached refs. It blocks object
replacement and partial clones and disables Git transport. It does not verify
an owner's identity, inspect sandbox enforcement, or certify that local refs
are current. Use `--docs-only` to validate the required
documents, their inline links, and status metadata while editing or in a
detached CI checkout without running Git checks. External links are
checked as references, with live verification left to the Builder.
A blocked result requires investigation before relying on the affected gate.

## Build, review, validate, and hand off

1. **Builder:** Create one isolated worktree at the verified base. Implement only the allowlisted changes. Run focused and full unprivileged regressions as applicable. Include actual commands and results.
2. **Independent Reviewer:** Inspect the exact diff against the approved base, contract, security assumptions, and test evidence. Return severity, file and line, impact, remediation, and a GO or NO-GO verdict. The Reviewer stays read-only.
3. **Repair and local CI:** Use at most two repair cycles total across review and CI. Keep repairs within scope, repeat affected checks, and ask the same independent Reviewer to assess material changes. Never reuse an earlier verdict for a materially different diff.
4. **Publication and GitHub CI:** When the task authorizes publication, commit and push only the reviewed files and open one PR. Its standard CI starts on the PR event. Check that the PR base and head, published patch, and commit tree match the reviewed base and diff. Verify completed GitHub CI and job evidence for that exact published revision. Post the attributed review and evidence only within the granted publication authority.
5. **Merge gate:** Require verified scope, matching reviewed and published revisions, completed passing CI, and resolution of blocking findings. Disclose residual risks to the owner. The owner decides whether to merge. Native acceptance remains a separate gate where applicable.

Record the reviewed base SHA and an exact diff fingerprint, the published
head SHA, review verdict and findings, local and CI test counts and links,
remaining limitations, and any repairs. For example, fingerprint the
reviewed Git diff with `git diff --binary <base> | sha256sum` and compare
after committing with `git diff --binary <base> <head>`. A patch ID alone
does not verify the exact reviewed tree. Stop if a mismatch, blocker, or
unverifiable gate cannot be resolved inside the task and repair budget.

## Post-merge retrospective

Once the owner merges, record the task and PR, reviewed base and head,
merge SHA, elapsed time to review and CI, repair count, CI failures or
retries, human interventions, remaining risks, and deferred acceptance work.
Use actual evidence rather than estimating unobserved outcomes. Keep
credentials and private logs out of the retrospective.

## Ordinary GitHub CI

The **CI** workflow runs on pull requests into `main`. Once GitHub registers
`workflow_dispatch` from the default branch, use **Actions → CI → Run workflow**
for a manual run. A newer run cancels an older run for the same ref. The test
job has a 10-minute timeout. This private repository uses standard GitHub-hosted
runner minutes. Confirm included capacity and disabled paid overage before
publishing the PR, since opening it can start CI.

The runner checks out the code with read-only repository access and disables
credential persistence. Setup pulls the pinned Python 3.12 image and downloads
the pinned wheels. The project install and sequential pytest run take place in
a disposable, non-root Docker container with only loopback networking. Git
metadata stays available for the evaluator tests, and port 8001 stays private to
the container. CI uses mocked model transports and requires no provider keys.

From a Git checkout with an existing Python 3.12 test environment, run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --tb=short
```

Keep loopback port 8001 free for local runs. For a CI failure, open the job
summary and the first failed step. To rerun a failed test, append its pytest
node ID to the command above. CI keeps diagnostics in the job log and uploads
no artifacts.

When project dependencies change, update the exact versions and SHA-256 hashes
for the Linux x86-64 Python 3.12 wheels in `.github/ci-requirements.txt`.
Review additions and removals in the workflow's package allowlist, including
transitive and build dependencies. The download step accepts only approved
hashed wheel pins from PyPI.
Review the upstream release and its exact commit before updating the checkout
action pin.
