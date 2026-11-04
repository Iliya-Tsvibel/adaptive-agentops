# Adaptive AgentOps

Adaptive AgentOps is a Secure AI Agent Control Plane project.
Its flagship direction is controlled agent tool execution: proposals pass through deterministic policy, scoped approval where required, and audited execution.

Stage 2 implements a local security MVP in Python and FastAPI, without an LLM or agent runtime.
It supports a read-only mock health check and an approval-gated simulated restart of `demo-api`.

## Implemented architecture

| Component | Responsibility |
| --- | --- |
| `identity.py`, `tool_requests.py` | Trusted local `RunContext` and restricted, typed proposals with empty arguments |
| `policy.py` | Default-deny policy decision point (PDP): allow health reads, require approval for restarts in the fixed local scope |
| `gateway.py`, `restart_gateway.py` | Policy enforcement: audit authorization before a health read; atomically create a pending restart and policy evidence |
| `actions.py`, `approvals.py`, `operator_cli.py` | Fingerprinted, expiring actions and local operator review and decision persistence |
| `dispatch_reservations.py`, `dispatch_control.py` | Atomic approval consumption, unique dispatch reservation, and pre-dispatch audit |
| `execution_controller.py` | Reserve, attempt the mock restart once, then commit its result separately |
| `audit.py` | Correlated SQLite policy, reservation, and result events |
| `mock_adapter.py`, `mock_restart_adapter.py`, `mock_api.py` | Fixed loopback HTTP adapters and simulated target |

`main.py` exposes only `GET /health`; it is not an HTTP execution or approval service.
The workflow uses internal Python entry points and a local operator CLI.
SQLite stores `actions`, `approvals`, `dispatch_reservations`, and `audit_events`.

## Audited restart lifecycle

1. Trusted code supplies a `RunContext` and a `mock.restart_service` proposal for `demo-api` with `{}` arguments.
2. `create_audited_pending_restart` evaluates policy. For `APPROVAL_REQUIRED`, it commits a server-generated action, scope fingerprint, ten-minute expiry, and `POLICY_DECISION` together. A valid denied request records denial without creating an action.
3. The operator reviews persisted scope and enters an explicit approval or rejection in the CLI. The decision binds to that action and expires with it; recording approval does not execute anything.
4. `execute_approved_mock_restart` calls `reserve_approved_restart`. Under `BEGIN IMMEDIATE`, it validates context, action, approval, expiry, and fresh policy, then atomically consumes approval, inserts a unique reservation, and records `DISPATCH_RESERVED`.
5. Only a confirmed reservation commit permits one adapter invocation. HTTP runs outside the SQLite write transaction: one empty POST to the fixed mock restart endpoint, without retries or redirects.
6. A separate transaction commits `RESULT`, correlated by run, action, approval, and dispatch IDs. `ACKNOWLEDGED` confirms a simulated response; `UNCERTAIN` leaves the external outcome unresolved. An unconfirmed result commit returns `AUDIT_UNCONFIRMED`. Stored reservations never grant resume or retry rights.

The read-only Gateway commits `POLICY_DECISION` before GET; failure to confirm that commit prevents dispatch.
It commits a separate `RESULT` afterward.
A subsequent `RESULT` audit failure leaves result durability unconfirmed but does not erase the already committed `ALLOW` decision or undo the HTTP attempt.

## Local setup and tests

Use Python 3.12 or newer, from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install '.[dev]'
python -m pytest -q
```

The `dev` extra is needed for this local workflow: it includes HTTPX (used by the adapters) and pytest.
FastAPI and Uvicorn are base dependencies; Pydantic is supplied through FastAPI.
Versions use lower bounds rather than a lockfile.

Run tests sequentially.
The real loopback integration tests require exclusive use of `127.0.0.1:8001` and start and stop their own mock server.
Stop any manually started mock server before testing.
To run just that module:

```bash
python -m pytest -q tests/test_restart_e2e.py
```

Optional local servers, each in its own terminal with the environment activated:

```bash
python -m uvicorn adaptive_agentops.main:app --host 127.0.0.1 --port 8000
python -m uvicorn adaptive_agentops.mock_api:app --host 127.0.0.1 --port 8001
```

The CLI requires an existing initialized, file-backed database and a committed pending action.
Replace the example path and UUID with those supplied by trusted integration code:

```bash
python -m adaptive_agentops.operator_cli --db /path/to/existing.db review ACTION_UUID
python -m adaptive_agentops.operator_cli --db /path/to/existing.db decide ACTION_UUID
```

`decide` requires interactive stdin and stdout and exact `APPROVE ACTION_UUID` or `REJECT ACTION_UUID` confirmation.
There is no database bootstrap or execution CLI.
See [the end-to-end test](tests/test_restart_e2e.py) for schema initialization and complete internal API wiring; its terminal interaction is simulated.

## Offline agent preparation (Stage 3)

`prepare_offline_agent_action(conn, context, *, mock_output=...)` in
`adaptive_agentops.agent_workflow` constructs the fixed health proposal, calls
the authorized and audited health Gateway, validates the resulting observation,
and strictly parses caller-supplied scripted text. Only a valid restart proposal
reaches the existing restart Gateway and PDP. Preparation stops after audited
pending-action creation; it never approves, reserves, or executes an action.

The caller supplies a trusted `RunContext` separately from the script and an
exclusively held connection to an initialized, file-backed SQLite database.
Use the existing schema initialization functions, as demonstrated in
`tests/test_agent_workflow.py`. The script is test input, not AI inference:
`{"decision":"NO_ACTION"}` or `{"decision":"PROPOSE_RESTART"}`.

The frozen result distinguishes `HEALTH_UNAVAILABLE` (including invalid or
audit-unconfirmed observations), `NO_ACTION`, `INVALID_OUTPUT`, `PENDING_CREATED`,
`PENDING_NOT_CREATED`, and `PENDING_UNCERTAIN`. Only confirmed creation returns
an action ID. Uncertainty does not establish whether an action persisted; the
coordinator never retries. Separate preparation calls can create separate actions.

After creation, an operator must explicitly review and approve or reject the
action using the existing CLI. Approval alone never dispatches. Trusted code
may separately call `execute_approved_mock_restart` with the original context
and action ID; its existing consumption, reservation, expiry, fresh-policy,
at-most-one-attempt, and audit rules remain authoritative. Uncertain execution,
cancellation, and unconfirmed result audit never restore dispatch rights.

This workflow makes no LLM calls, reads no credentials, and has no provider or
live fallback. Existing health/restart adapters address only the fixed local
mock API. The offline integration tests intercept both with HTTPX MockTransport,
reject unexpected requests, and block socket connections and the live LLM entry
point. They require no server, cloud service, new dependency, or paid API:

```bash
python -m pytest -q tests/test_agent_workflow.py
```

Scripted decisions do not prove LLM decision quality, model compatibility, human
identity, or runtime provenance beyond trusted in-process orchestration.
Acknowledgement proves simulation only. The separately callable live LLM adapter
is outside this offline path; this is not a repository-wide network prohibition.
Parser failures and NO_ACTION return statuses without inventing execution audit
events; existing policy/result evidence and approval records retain their scope.

## Local Ollama preparation (Stage 3)

`prepare_local_agent_action(conn, context, *, allow_local_inference=False)` in
`adaptive_agentops.local_agent_workflow` is a separate trusted integration path.
Only the literal `True` enables any work, including health reads and audit writes.
It uses the existing health Gateway, validates its actual result through
`build_agent_observation()`, makes one bounded local inference request, parses
its exact content through `parse_agent_proposal()`, and sends only valid proposals
to the existing restart Gateway/PDP. It stops at committed pending-action creation.
The offline workflow and existing OpenAI adapter are unchanged and are not called.

The fixed endpoint is `http://127.0.0.1:11434/api/chat`, with the already installed
`qwen3.5:2b-q4_K_M` model. Requests set `think=false`, `stream=false`,
`temperature=0`, `num_ctx=2048`, `num_predict=128`, and a JSON Schema allowing
exactly one `decision`: `NO_ACTION` or `PROPOSE_RESTART`. Trusted system
instructions are separate from the JSON observation; `reason` is untrusted data.
There are no tools, conversation history, credentials, dynamic destinations,
model overrides, automatic retries, downloads, or cloud fallback.

Requests are capped at 16 KiB and raw responses at 32 KiB. A conservative 1 KiB
UTF-8 serialized-observation cap leaves context headroom; larger observations are
rejected, never truncated. This is not a tokenizer-based guarantee that the
2,048-token context contains the entire prompt or that the daemon processed it
fully. The adapter requires a completed assistant response from the expected
model with `done=true` and `done_reason="stop"`. It rejects malformed envelopes,
nonempty thinking, tool calls, unexpected fields, compression, and non-JSON
responses. Documented optional timestamp/statistics fields are type-checked but
never confer authority. The existing strict proposal parser remains mandatory.

HTTPX uses no environment proxies, redirects, or transport retries. Connection
timeout is two seconds; read/write/pool operations are bounded at twenty seconds;
a thirty-second cooperative async deadline covers the request and response.
Cancellation propagates after transport cleanup. Timeout/cancellation can leave
local inference running, and never authorizes an automatic retry.

Results distinguish disabled operation, unusable health, bounded adapter errors,
NO_ACTION, INVALID_OUTPUT, and pending creation/not-creation/uncertainty. Only
confirmed pending creation returns an action ID. Errors contain no raw prompt,
reason, provider body, or exception text. Pending uncertainty may mean an action
persisted; do not blindly repeat preparation. Separate invocations can create
separate actions. Existing Gateway connection cleanup/discard rules still apply.
Approval, reservation, execution and their audit mechanisms remain separate.

### Manual experiment: explicit opt-in only

Do not run inference as part of setup, import, tests, or application startup.
For a separately approved manual experiment, use the existing installed model and
mock API service at `127.0.0.1:8001`; do not install, pull, upgrade, or purchase
anything. Configure the **daemon**, not just the client shell, for local-only
operation (`OLLAMA_NO_CLOUD=1` or `disable_ollama_cloud: true`) and restrict external
egress for both daemon and application. See [Ollama's local-only settings](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features).
If these prerequisites cannot be established, do not run the experiment.
A missing model or unreachable fixed endpoint is a failure, not permission to
pull a model, select another host, or use a cloud service.

The following manual invocation creates a temporary file-backed database, makes
one real local inference request at most, and stops before approval or dispatch.
It retains the database for inspection and prints only bounded outcomes and IDs.
Do not repeat it to force a desired model decision.

```bash
python - <<'PY_LOCAL'
import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile

from adaptive_agentops.actions import initialize_action_schema
from adaptive_agentops.approvals import initialize_approval_schema
from adaptive_agentops.audit import initialize_audit_schema
from adaptive_agentops.dispatch_reservations import initialize_dispatch_reservation_schema
from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.local_agent_workflow import prepare_local_agent_action

path = Path(tempfile.mkdtemp(prefix="agentops-local-")) / "experiment.db"
with closing(sqlite3.connect(path)) as conn:
    for initialize in (initialize_action_schema, initialize_approval_schema,
                       initialize_dispatch_reservation_schema, initialize_audit_schema):
        initialize(conn)
    conn.commit()
    context = create_local_run_context()
    result = asyncio.run(prepare_local_agent_action(
        conn, context, allow_local_inference=True,
    ))
    print(result.status.value, result.action_id,
          result.error.value if result.error else None)
with closing(sqlite3.connect(path)) as observer:
    for table in ("actions", "approvals", "dispatch_reservations", "audit_events"):
        print(table, observer.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
print("Review database:", path)
# STOP: no approval, reservation, or execution call.
PY_LOCAL
```

All new automated tests use HTTPX MockTransport, reject unexpected requests,
block sockets and OpenAI calls, and work without Ollama. No dependencies or paid
services are needed. This client cannot prove daemon provenance, absence of daemon
external egress, full prompt-injection immunity, production readiness, universal
model reliability, or reproducibility merely from temperature zero. Model output
is intent only: trusted context, PDP, human approval, atomic reservation and audit
remain the authorization boundaries. Local inference uses existing hardware and
energy; zero additional spend means no paid APIs, services or purchases.

## Stage 3 evaluation and acceptance

Stage 3 is **LOCAL MVP ACCEPTED / PRODUCTION NO-GO**, based on the completed
local evaluation and supplied independent review. See the [final acceptance
report](docs/stage3-acceptance.md) and [exact archived evidence](evaluations/stage3_results.json).
The unchanged manifest records 26/26 completed scenarios, 24/24 correct decisions,
zero false positive restarts, false negatives or injection overrides, and 2/2
boundary rejections. The source SQLite snapshots match nine pending actions,
61 correlated audit events, and zero approvals or reservations. All 11 reviewed
gate groups passed (12 individual manifest flags, with category minima separate).

Preparation latency was p50 **0.671212 s**, p95 **0.807475 s**, maximum
**8.713817 s**. The nearest-rank p95 is the 23rd of 24 ordered samples: its value
excludes the 8.714-second cold-start maximum, which remains in the sample set.
This is one run on 24 curated decision scenarios; the dataset is now consumed
holdout evidence. There is no independent attestation of model weights or GPU
usage, authenticated production workload/operator identity, protected real target,
tamper-proof audit, or production reliability/recovery claim. Zero additional paid
services or API usage is reported, not independently billed-audited.
The 26 frozen synthetic cases contain 24 decisions (9 restart, 15 NO_ACTION) and
two input-boundary rejections. Expected labels are not sent to the model.

Using the existing environment, validation-only mode is safe without Ollama:

```bash
python -m scripts.evaluate_stage3
```

This validates the frozen dataset and prints its hash/counts. It makes no health
or inference request and writes no results/database. New evaluator tests use
mocked transports exclusively. Existing owned-localhost E2E tests are unchanged.

**Do not rerun the accepted evaluation automatically.** Any additional live run
requires fresh explicit authorization and a reviewed launch plan providing
local-only daemon configuration and egress protection for evaluator and daemon.
The previously prepared smoke egress guard launches only one smoke script; it
cannot be assumed to run this evaluation.
The confirmation flag below is an operator attestation, not firewall enforcement.
No missing prerequisite permits a model download, install, cloud fallback or API
credential. No firewall settings are changed by the evaluator.

After those prerequisites and authorization, the manual command shape is:

```bash
python -m scripts.evaluate_stage3 --run-local-evaluation \
  --confirm-local-only-egress --output /tmp/agentops-stage3-UNIQUE-RUN \
  --operator-hardware 'WSL2; Quadro T2000; 4 GB VRAM (operator-reported)'
```

Use a new directory outside the repository for every authorized run. Existing
directories are refused; failed or partial evidence is never overwritten/resumed.
The runner serially supplies synthetic health via scoped MockTransport and calls
the existing coordinator, adapter and PDP. It uses fresh file-backed SQLite per
case, verifies committed effects independently, and stops before any approval,
reservation or restart. Only the fixed installed Ollama model can receive real
requests: at most one per eligible case, maximum 24; no warm-up, retries or
fallback. This does not need the mock API service to be running.

`results.json` is capped at 128 KiB and checkpointed before/after cases. It records
commit/source/dataset hashes, requested settings, observed versus operator-reported
hardware, bounded per-case outcomes, persisted counts/audit correlation, latency,
category metrics and paired attacks. It stores no raw provider messages, prompts,
exception text or credentials. Per-case databases remain for inspection. Abort or
interruption never authorizes retry; retain evidence and obtain fresh authorization
for a new run. SIGKILL/storage failure can leave incomplete RUNNING/STARTED evidence.

Exit 0 means validation succeeded or all evaluation gates passed; exit 1 means a
completed failed evaluation or an aborted run; exit 2 means refusal/input/output
failure. A later passing run does not automatically establish new acceptance;
it still requires independent review. The archived run and supplied review support
the current **LOCAL MVP ACCEPTED** decision. Do not relabel cases, alter accepted
prompts, or relax gates to improve scores. Local inference does not prove
production readiness, daemon provenance, full-context processing or universal
prompt-injection immunity.

## CI and evidence

[GitHub Actions](.github/workflows/ci.yml) runs on pushes and pull requests to `main`, on Ubuntu with Python 3.12.
It installs `.[dev]` and runs `python -m pytest -q` sequentially, with a ten-minute job timeout, read-only repository permissions, SHA-pinned actions, and checkout credential persistence disabled.

Tests exercise policy rejection, malformed inputs, expiry, scope binding, competing reservations, replay prevention, transaction and audit failures, interruptions, adapter limits, and the real loopback restart workflow.
This demonstrates local authorization and persistence ordering under tested conditions.
It does not prove production security, authenticated workloads or humans, exactly-once external effects, or real service recovery.

## Boundaries and future direction

The MVP assumes trusted local code, operators, host, clock, and intact SQLite storage.
The mock API is unauthenticated and accepts direct POSTs outside the controller.
Audit and action tables are neither tamper-proof nor generally append-only.
See [Stage 2 security boundaries](docs/stage2-security.md) for the full contract and failure semantics.

Future agent integration would submit proposals through these control points while keeping identity, approval, and execution authority outside agent-supplied input.
Agent and LLM integration, real workload and operator authentication, protected targets, and production infrastructure adapters are deferred, not implemented features.

This documentation is based on repository code, tests, dependencies, and CI at starting commit `9f765fb`.
Canonical MasterBrief and PRD sources are not present in the local repository; alignment with their full contents could not be verified.
