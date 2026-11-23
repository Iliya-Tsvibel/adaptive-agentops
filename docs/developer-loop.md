# Developer loop

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

When project dependencies change, update the matching pins in
`.github/ci-requirements.txt`, including transitive and build dependencies.
Review the upstream release and its exact commit before updating the checkout
action pin.
