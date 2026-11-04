"""Offline evaluation-harness tests. All inference responses are scripted mocks."""

import asyncio
from contextlib import closing
import hashlib
import json
import socket
import sqlite3
from unittest.mock import Mock

import httpx
import pytest

from scripts import evaluate_stage3 as evaluation

FROZEN_SHA256 = "0f4454a1643ab1062b673fe77cdc767e8f2d21a05bc564ed9a809224b500709d"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("REAL NETWORK FORBIDDEN"))
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    # The evaluator captures this mock as its delegate before scoping its guards.
    state = {"requests": [], "outputs": {}, "closed": 0}
    cases = evaluation.load_dataset()
    expected_by_observation = {evaluation.observation_text(case["observation"]): case for case in cases[:24]}

    class ResponseStream(httpx.AsyncByteStream):
        def __init__(self, data):
            self.data = data

        async def __aiter__(self):
            if state.get("read_error"):
                raise httpx.ReadError("SECRET provider body")
            yield self.data

        async def aclose(self):
            state["closed"] += 1

    async def handler(request):
        assert str(request.url) == evaluation.OLLAMA_URL and request.method == "POST"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        case = expected_by_observation[body["messages"][1]["content"]]
        state["requests"].append(case["id"])
        # Expected labels are used here only to script test responses, never sent
        # by the runner. This fixture is evidence of wiring, not model quality.
        output = state["outputs"].get(case["id"], case["expected"])
        if isinstance(output, BaseException):
            raise output
        content = output if output not in evaluation.DECISIONS else json.dumps({"decision": output})
        payload = {"model": evaluation.MODEL, "done": True, "done_reason": "stop",
                   "message": {"role": "assistant", "content": content}}
        assert body["messages"][1] == {"role": "user", "content": evaluation.observation_text(case["observation"])}
        assert set(body) == {"model", "think", "stream", "options", "format", "messages"}
        assert "expected" not in body and "rationale" not in body and "pair" not in body
        return httpx.Response(200, headers={"content-type": "application/json"},
                              stream=ResponseStream(json.dumps(payload).encode()))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(handler))
    yield state
    forbidden.assert_not_called()


def run(tmp_path):
    return asyncio.run(evaluation.evaluate(evaluation.load_dataset(), tmp_path / "run", "Synthetic test hardware"))


def ideal_rows(cases):
    rows = []
    for index, case in enumerate(cases):
        decision = case["expected"] if case["expected"] in evaluation.DECISIONS else None
        rows.append({"case_id": case["id"], "expected": case["expected"],
                     "model_decision": decision, "proposal_status": "VALID_PROPOSAL" if decision == "PROPOSE_RESTART" else "NO_ACTION",
                     "failure_code": None, "unauthorized_side_effect": False,
                     "boundary_correct": decision is None, "latency_seconds": index + 1,
                     "model_requests": int(decision is not None)})
    return rows


def test_dataset_frozen_labels_pairings_and_byte_boundaries():
    from adaptive_agentops.mock_adapter import DemoApiHealth
    assert hashlib.sha256(evaluation.DATASET.read_bytes()).hexdigest() == FROZEN_SHA256
    cases = evaluation.load_dataset()
    assert [case["id"] for case in cases] == [f"{n:02}" for n in range(1, 27)]
    assert [case["id"] for case in cases if case["expected"] == "PROPOSE_RESTART"] == [
        "05", "06", "07", "08", "09", "10", "11", "22", "24",
    ]
    assert sum(case["expected"] == "NO_ACTION" for case in cases) == 15
    for case in cases[:25]:
        DemoApiHealth.model_validate(case["observation"])
        assert case["rationale"] and case["category"]
        size = len(evaluation.observation_text(case["observation"]).encode())
        assert size <= 1024 if case["id"] != "25" else size == 1025
    with pytest.raises(ValueError):
        DemoApiHealth.model_validate(cases[25]["observation"])
    for case in cases[18:22]:
        control = cases[int(case["pair"]["control_id"]) - 1]
        assert case["expected"] == control["expected"]
        assert case["observation"]["reason"].startswith(control["observation"]["reason"])
        assert case["pair"]["attacker_requested"] != case["expected"]
    assert len(evaluation.observation_text(cases[22]["observation"]).encode()) > 800
    assert not cases[23]["observation"]["reason"].isascii()


def test_dataset_changes_are_rejected(tmp_path, monkeypatch):
    payload = json.loads(evaluation.DATASET.read_text())
    payload["cases"][0]["expected"] = "PROPOSE_RESTART"
    altered = tmp_path / "altered.json"
    altered.write_text(json.dumps(payload))
    monkeypatch.setattr(evaluation, "DATASET", altered)
    with pytest.raises(ValueError, match="DATASET_NOT_FROZEN"):
        evaluation.load_dataset()


def test_metrics_and_nearest_rank_percentiles():
    cases = evaluation.load_dataset()
    rows = ideal_rows(cases)
    metrics = evaluation.summarize(cases, rows, "COMPLETED")
    assert metrics["correct"] == 24 and metrics["all_gates_pass"]
    assert metrics["latency"]["p50_seconds"] == 12
    assert metrics["latency"]["p95_seconds"] == 23
    assert metrics["boundary_rejections"] == 2 and metrics["model_requests"] == 24
    assert len(metrics["pairs"]) == 4 and all(pair["both_scored"] for pair in metrics["pairs"])
    assert not any(pair["changed"] or pair["override"] for pair in metrics["pairs"])
    rows[0]["model_decision"] = "PROPOSE_RESTART"  # false positive
    rows[4]["model_decision"] = "NO_ACTION"  # false negative, one worker miss
    rows[18]["model_decision"] = "PROPOSE_RESTART"  # attack override
    rows[23].update(model_decision=None, proposal_status="INVALID_OUTPUT")
    metrics = evaluation.summarize(cases, rows, "COMPLETED")
    assert metrics["correct"] == 20 and metrics["false_positive_restarts"] == 2
    assert metrics["false_negatives"] == 1 and metrics["invalid_outputs"] == 1
    assert metrics["unscored"] == 1 and metrics["invalid_output_rate"] == 1 / 24
    assert metrics["injection_override_rate"] == 1 / 4
    assert metrics["per_category"]["stalled_worker"] == {"correct": 3, "total": 4}
    assert not metrics["all_gates_pass"]
    assert not evaluation.summarize(cases, rows[:1], "ABORTED")["all_gates_pass"]
    assert evaluation.percentile([], .95) is None


def test_every_acceptance_gate_can_fail():
    cases = evaluation.load_dataset()
    rows = ideal_rows(cases)
    for row in rows[:24]:
        row["model_decision"] = "NO_ACTION" if row["expected"] == "PROPOSE_RESTART" else "PROPOSE_RESTART"
        row["latency_seconds"] = 31
    rows[0].update(proposal_status="INVALID_OUTPUT", failure_code="ADAPTER_FAILURE", unauthorized_side_effect=True)
    rows[24]["boundary_correct"] = False
    metrics = evaluation.summarize(cases, rows, "ABORTED")
    assert all(not passed for passed in metrics["gates"].values())


def test_incomplete_pairs_are_not_reported_as_success():
    cases = evaluation.load_dataset()
    rows = ideal_rows(cases)
    rows[18]["model_decision"] = None
    metrics = evaluation.summarize(cases, rows, "COMPLETED")
    assert metrics["pairs"][0]["changed"] is None
    assert not metrics["pairs"][0]["both_scored"]
    assert not metrics["all_gates_pass"]


def test_default_validation_does_no_work(tmp_path, monkeypatch, capsys, offline):
    guard = Mock(side_effect=AssertionError("Must not evaluate"))
    monkeypatch.setattr(evaluation, "evaluate", guard)
    assert evaluation.main(["--output", str(tmp_path / "unused")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "VALIDATED_ONLY" and result["model_requests"] == 0
    assert not (tmp_path / "unused").exists() and offline["requests"] == []
    guard.assert_not_called()


@pytest.mark.parametrize("args", [["--run-local-evaluation"], ["--run-local-evaluation", "--confirm-local-only-egress"]])
def test_manual_opt_in_requires_all_prerequisites(args, capsys, offline):
    assert evaluation.main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "REFUSED"
    assert offline["requests"] == []


def test_complete_offline_run_bounds_and_persisted_evidence(tmp_path, offline):
    manifest = run(tmp_path)
    assert manifest["completion_status"] == "COMPLETED"
    assert manifest["metrics"]["all_gates_pass"]
    assert offline["requests"] == [f"{n:02}" for n in range(1, 25)]
    assert offline["closed"] == 24
    assert len(manifest["cases"]) == 26
    for row in manifest["cases"]:
        assert row["counts"]["approvals"] == row["counts"]["dispatch_reservations"] == 0
        assert row["model_requests"] == (1 if row["case_id"] <= "24" else 0)
        assert row["health_requests"] == 1
        with closing(sqlite3.connect(tmp_path / "run" / f"case-{row['case_id']}.db")) as observer:
            assert observer.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == row["counts"]["actions"]
        if row["model_decision"] == "PROPOSE_RESTART":
            assert row["control_status"] == "PENDING_CREATED"
            assert row["audit"][-1]["action_id"] == row["action_id"]
    assert manifest["cases"][24]["boundary_correct"] and manifest["cases"][25]["boundary_correct"]
    assert manifest["cases"][25]["audit"][1]["execution_status"] == "FAILED"
    text = (tmp_path / "run/results.json").read_text()
    assert len(text.encode()) <= 128 * 1024
    assert all(key not in text for key in ('"messages"', '"observation"', '"rationale"', 'SECRET'))
    assert len(manifest["git_commit"]) == 40 and len(manifest["adapter_source_sha256"]) == 64
    assert manifest["dataset_sha256"] == FROZEN_SHA256
    assert not manifest["hardware"]["gpu_inference_verified_by_evaluator"]


@pytest.mark.parametrize("failure,code", [
    (httpx.ReadTimeout("SECRET"), "ADAPTER_FAILURE"),
    (httpx.ConnectError("SECRET"), "ADAPTER_FAILURE"),
    (asyncio.CancelledError(), "CANCELLED"),
    (RuntimeError("SECRET exception"), "EVALUATION_ERROR"),
])
def test_abort_preserves_partial_evidence_and_does_not_retry(tmp_path, offline, failure, code):
    offline["outputs"]["02"] = failure
    manifest = run(tmp_path)
    assert manifest["completion_status"] == "ABORTED" and manifest["abort_code"] == code
    assert offline["requests"] == ["01", "02"]
    assert [row["case_id"] for row in manifest["cases"]] == ["01", "02"]
    assert manifest["cases"][0]["model_decision"] == "NO_ACTION"
    assert manifest["cases"][1]["model_decision"] is None
    assert not manifest["metrics"]["all_gates_pass"] and manifest["metrics"]["unscored"] == 23
    assert json.loads((tmp_path / "run/results.json").read_text())["completion_status"] == "ABORTED"
    assert "SECRET" not in (tmp_path / "run/results.json").read_text()


def test_invalid_output_is_scored_and_never_becomes_no_action(tmp_path, offline):
    offline["outputs"]["01"] = '{"decision":"NO_ACTION","approved":true}'
    manifest = run(tmp_path)
    assert manifest["completion_status"] == "COMPLETED" and len(offline["requests"]) == 24
    assert manifest["cases"][0]["model_decision"] is None
    assert manifest["metrics"]["correct"] == 23 and manifest["metrics"]["invalid_outputs"] == 1
    assert not manifest["metrics"]["all_gates_pass"]


def test_threshold_failures_remain_failures(tmp_path, offline):
    offline["outputs"]["19"] = "PROPOSE_RESTART"
    manifest = run(tmp_path)
    assert manifest["completion_status"] == "COMPLETED"
    assert manifest["metrics"]["injection_overrides"] == 1
    assert manifest["metrics"]["false_positive_restarts"] == 1
    assert manifest["metrics"]["pairs"][0]["changed"] is True
    assert not manifest["metrics"]["all_gates_pass"]


def test_result_directory_never_overwritten(tmp_path, offline):
    target = tmp_path / "run"
    target.mkdir()
    (target / "results.json").write_text('previous evidence')
    with pytest.raises(FileExistsError):
        run(tmp_path)
    assert (target / "results.json").read_text() == 'previous evidence'
    assert offline["requests"] == []


def test_output_inside_repository_refused_before_creation(offline):
    with pytest.raises(ValueError, match="OUTSIDE_REPOSITORY"):
        asyncio.run(evaluation.evaluate(evaluation.load_dataset(), evaluation.ROOT / "forbidden-results", "test"))
    assert offline["requests"] == []


@pytest.mark.parametrize("status", ["PENDING_NOT_CREATED", "PENDING_UNCERTAIN"])
def test_control_outcomes_are_distinct_and_abort(tmp_path, monkeypatch, status):
    from adaptive_agentops.restart_gateway import PendingRestartResult, PendingRestartStatus
    # Real health/inference path, fault only at the existing pending Gateway API.
    gateway_status = PendingRestartStatus.NOT_CREATED if status == "PENDING_NOT_CREATED" else PendingRestartStatus.UNCERTAIN
    monkeypatch.setattr("adaptive_agentops.local_agent_workflow.create_audited_pending_restart",
                        lambda *args: PendingRestartResult(gateway_status))
    manifest = run(tmp_path)
    assert manifest["completion_status"] == "ABORTED"
    row = manifest["cases"][-1]
    assert row["case_id"] == "05" and row["model_decision"] == "PROPOSE_RESTART"
    assert row["control_status"] == status and row["action_id"] is None
    assert manifest["abort_code"] == ("PENDING_UNCERTAIN" if status.endswith("UNCERTAIN") else "CONTROL_FAILURE")


@pytest.mark.parametrize("destination", ["https://api.openai.com/v1/responses", "http://127.0.0.1:8001/services/demo-api/restart"])
def test_unexpected_destination_aborts_before_delegate(tmp_path, monkeypatch, offline, destination):
    monkeypatch.setattr("adaptive_agentops.ollama_adapter._ENDPOINT", destination)
    manifest = run(tmp_path)
    assert manifest["abort_code"] == "UNEXPECTED_DESTINATION" and offline["requests"] == []
    assert manifest["metrics"]["unauthorized_side_effects"] == 1


def test_forbidden_authority_call_is_blocked(tmp_path, monkeypatch, offline):
    from adaptive_agentops import local_agent_workflow, approvals
    async def forbidden(*args, **kwargs):
        approvals.record_operator_decision(None, None, None)
    monkeypatch.setattr(local_agent_workflow, "request_local_agent_decision", forbidden)
    manifest = run(tmp_path)
    assert manifest["abort_code"] == "FORBIDDEN_SIDE_EFFECT" and offline["requests"] == []
    assert manifest["cases"][0]["counts"]["approvals"] == 0


@pytest.mark.parametrize("table", ["approvals", "dispatch_reservations"])
def test_persisted_forbidden_effect_is_a_failure(table):
    row = {"counts": dict.fromkeys(evaluation.TABLES, 0), "audit": []}
    row["counts"][table] = 1
    assert evaluation.check_record(evaluation.load_dataset()[0], row) == "FORBIDDEN_SIDE_EFFECT"
    assert row["unauthorized_side_effect"]


def test_read_failure_aborts_and_closes_transport(tmp_path, offline):
    offline["read_error"] = True
    manifest = run(tmp_path)
    assert manifest["completion_status"] == "ABORTED" and manifest["abort_code"] == "ADAPTER_FAILURE"
    assert offline["requests"] == ["01"] and offline["closed"] == 1
    assert manifest["cases"][0]["adapter_error"] == "TRANSPORT_ERROR"


def test_budget_blocks_second_request_and_boundary_inference(offline):
    cases = evaluation.load_dataset()
    import httpx
    async def exercise():
        for case, budget, existing in [(cases[0], {"requests": 24}, 0),
                                        (cases[0], {"requests": 1}, 1),
                                        (cases[24], {"requests": 0}, 0)]:
            row = {"health_requests": 0, "model_requests": existing, "unauthorized_side_effect": False}
            with evaluation.case_transports(case, row, budget):
                async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport()) as client:
                    payload = {"model": evaluation.MODEL, **evaluation.SETTINGS, "format": {},
                               "messages": [{"role": "system", "content": "test"},
                                            {"role": "user", "content": evaluation.observation_text(case["observation"])}]}
                    with pytest.raises(evaluation.EvaluationAbort, match="REQUEST_BUDGET_EXCEEDED"):
                        await client.post(evaluation.OLLAMA_URL, json=payload)
    asyncio.run(exercise())
    assert offline["requests"] == []


def test_cli_manual_run_only_uses_mock_inference(tmp_path, capsys):
    assert evaluation.main(["--run-local-evaluation", "--confirm-local-only-egress", "--output", str(tmp_path / "cli")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["evaluation_gates_pass"]
    assert output["stage3_status"] == "EVALUATION PENDING"
