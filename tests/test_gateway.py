import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from adaptive_agentops.audit import (
    ExecutionStatus,
    append_mock_health_result,
    initialize_audit_schema,
)
from adaptive_agentops.gateway import (
    PreflightResult,
    MockHealthReadStatus,
    evaluate_and_audit_preflight,
    execute_mock_health_read,
    preflight_tool_call,
)
from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.mock_adapter import DemoApiHealth
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import MockGetHealthProposal


def authorized_proposal() -> MockGetHealthProposal:
    return MockGetHealthProposal.model_validate(
        {
            "tool": "mock.get_health",
            "resource": "demo-api",
            "arguments": {},
        }
    )


def forbidden_resource_proposal() -> MockGetHealthProposal:
    return MockGetHealthProposal.model_validate(
        {
            "tool": "mock.get_health",
            "resource": "payments-api",
            "arguments": {},
        }
    )


def connect_audit_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "audit.db")
    conn.row_factory = sqlite3.Row
    return conn


def prepared_audit_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect_audit_db(tmp_path)
    initialize_audit_schema(conn)
    conn.commit()
    return conn


def persisted_decisions(tmp_path: Path) -> list[str]:
    reopened = connect_audit_db(tmp_path)
    try:
        rows = reopened.execute(
            "SELECT policy_decision FROM audit_events"
        ).fetchall()
        return [row["policy_decision"] for row in rows]
    finally:
        reopened.close()


@pytest.fixture
def result_append():
    with patch(
        "adaptive_agentops.gateway.append_mock_health_result",
        wraps=append_mock_health_result,
    ) as append:
        yield append


@pytest.fixture
def execution_adapter():
    with patch("adaptive_agentops.gateway.get_demo_api_health") as adapter:
        adapter.return_value = DemoApiHealth(
            service="demo-api", status="unhealthy", reason="stalled_worker"
        )
        yield adapter


def test_execute_allow_only_after_commit(tmp_path, execution_adapter):
    conn = prepared_audit_conn(tmp_path)
    context = create_local_run_context()
    proposal = authorized_proposal()
    health = execution_adapter.return_value

    def read_after_commit():
        assert conn.in_transaction is False
        assert persisted_decisions(tmp_path) == ["ALLOW"]
        return health

    execution_adapter.side_effect = read_after_commit
    try:
        with patch(
            "adaptive_agentops.gateway.evaluate_and_audit_preflight",
            wraps=evaluate_and_audit_preflight,
        ) as preflight, patch(
            "adaptive_agentops.gateway.evaluate_policy", wraps=evaluate_policy
        ) as pdp:
            result = execute_mock_health_read(conn, context, proposal)
        preflight.assert_called_once_with(conn, context, proposal)
        pdp.assert_called_once_with(context, proposal)
        execution_adapter.assert_called_once_with()
        assert result.status is MockHealthReadStatus.SUCCEEDED
        assert result.decision is PolicyDecision.ALLOW
        assert result.health == health
        with pytest.raises(FrozenInstanceError):
            result.health = None
        with pytest.raises(ValidationError):
            result.health.reason = "changed"
    finally:
        conn.close()
    reopened = connect_audit_db(tmp_path)
    try:
        rows = reopened.execute(
            "SELECT event_kind, execution_status FROM audit_events"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("POLICY_DECISION", "NOT_DISPATCHED"), ("RESULT", "SUCCEEDED")
        ]
    finally:
        reopened.close()


@pytest.mark.parametrize("decision", [PolicyDecision.DENY, PolicyDecision.APPROVAL_REQUIRED])
def test_execute_non_allow_never_dispatches(tmp_path, execution_adapter, decision, result_append):
    conn = prepared_audit_conn(tmp_path)
    try:
        with patch("adaptive_agentops.gateway.evaluate_policy", return_value=decision):
            result = execute_mock_health_read(
                conn, create_local_run_context(), authorized_proposal()
            )
        assert result.status is MockHealthReadStatus.NOT_DISPATCHED
        assert result.decision is decision
        assert result.health is None
        execution_adapter.assert_not_called()
        result_append.assert_not_called()
    finally:
        conn.close()
    assert persisted_decisions(tmp_path) == [decision.value]


@pytest.mark.parametrize("bad_context", [True, False])
def test_execute_invalid_input_never_dispatches(tmp_path, execution_adapter, bad_context, result_append):
    conn = prepared_audit_conn(tmp_path)
    try:
        result = execute_mock_health_read(
            conn,
            object() if bad_context else create_local_run_context(),
            authorized_proposal() if bad_context else {"tool": "mock.get_health"},
        )
        assert result.status is MockHealthReadStatus.NOT_DISPATCHED
        assert result.decision is None
        assert result.health is None
        execution_adapter.assert_not_called()
        result_append.assert_not_called()
    finally:
        conn.close()
    assert persisted_decisions(tmp_path) == []


@pytest.mark.parametrize("pdp_behavior", [
    {"side_effect": RuntimeError("private PDP details")},
    {"return_value": "ALLOW"},
])
def test_execute_pdp_error_never_dispatches(tmp_path, execution_adapter, pdp_behavior, result_append):
    conn = prepared_audit_conn(tmp_path)
    try:
        with patch("adaptive_agentops.gateway.evaluate_policy", **pdp_behavior):
            result = execute_mock_health_read(
                conn, create_local_run_context(), authorized_proposal()
            )
        assert result.status is MockHealthReadStatus.NOT_DISPATCHED
        assert result.decision is None
        assert result.health is None
        execution_adapter.assert_not_called()
        result_append.assert_not_called()
    finally:
        conn.close()
    assert persisted_decisions(tmp_path) == []


@pytest.mark.parametrize("failure", ["insert", "commit", "closed", "active_transaction"])
def test_execute_audit_failure_never_dispatches(tmp_path, execution_adapter, failure, result_append):
    conn = sqlite3.connect(tmp_path / "audit.db", factory=CommitFailingConnection)
    try:
        initialize_audit_schema(conn)
        conn.commit()
        if failure == "insert":
            conn.execute("""
                CREATE TRIGGER reject_audit BEFORE INSERT ON audit_events
                BEGIN SELECT RAISE(ABORT, 'controlled insert failure'); END
            """)
            conn.commit()
        elif failure == "commit":
            conn.fail_commit = True
        elif failure == "closed":
            conn.close()
        else:
            conn.execute("BEGIN")
        result = execute_mock_health_read(
            conn, create_local_run_context(), authorized_proposal()
        )
        assert result.status is MockHealthReadStatus.NOT_DISPATCHED
        assert result.decision is None
        assert result.health is None
        execution_adapter.assert_not_called()
        result_append.assert_not_called()
        if failure == "active_transaction":
            assert conn.in_transaction is True
        elif failure != "closed":
            assert conn.in_transaction is False
    finally:
        conn.close()
    assert persisted_decisions(tmp_path) == []


@pytest.mark.parametrize("failure", ["http", "timeout", "validation", "size", "unexpected"])
def test_execute_adapter_failure_preserves_authorization(tmp_path, execution_adapter, failure):
    request = httpx.Request("GET", "http://127.0.0.1:8001/services/demo-api/health")
    errors = {
        "http": httpx.HTTPStatusError(
            "private HTTP details", request=request,
            response=httpx.Response(500, request=request),
        ),
        "timeout": httpx.ReadTimeout("private timeout details"),
        "size": ValueError("private size details"),
        "unexpected": RuntimeError("private implementation details"),
    }
    if failure == "validation":
        try:
            DemoApiHealth.model_validate({})
        except ValidationError as error:
            execution_adapter.side_effect = error
    else:
        execution_adapter.side_effect = errors[failure]
    conn = prepared_audit_conn(tmp_path)
    try:
        result = execute_mock_health_read(
            conn, create_local_run_context(), authorized_proposal()
        )
        assert result.status is MockHealthReadStatus.FAILED
        assert result.decision is PolicyDecision.ALLOW
        assert result.health is None
        assert "private" not in repr(result)
        execution_adapter.assert_called_once_with()
        rows = conn.execute(
            "SELECT event_kind, execution_status FROM audit_events"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("POLICY_DECISION", "NOT_DISPATCHED"), ("RESULT", "FAILED")
        ]
    finally:
        conn.close()
    assert persisted_decisions(tmp_path) == ["ALLOW", "ALLOW"]


@pytest.mark.parametrize("health", [
    {"service": "demo-api", "status": "healthy", "reason": "ok"},
    None,
    DemoApiHealth.model_construct(service="other", status="healthy", reason="ok"),
])
def test_execute_rejects_unvalidated_adapter_output(tmp_path, execution_adapter, health):
    execution_adapter.return_value = health
    conn = prepared_audit_conn(tmp_path)
    try:
        result = execute_mock_health_read(
            conn, create_local_run_context(), authorized_proposal()
        )
        assert result.status is MockHealthReadStatus.FAILED
        assert result.decision is PolicyDecision.ALLOW
        assert result.health is None
        execution_adapter.assert_called_once_with()
    finally:
        conn.close()


@patch("adaptive_agentops.gateway.evaluate_policy", wraps=evaluate_policy)
def test_authorized_proposal_returns_allow(mock_evaluate_policy: MagicMock) -> None:
    context = create_local_run_context()
    proposal = authorized_proposal()

    result = preflight_tool_call(context, proposal)

    mock_evaluate_policy.assert_called_once_with(context, proposal)
    assert result.error is False
    assert result.decision is PolicyDecision.ALLOW


def test_forbidden_resource_returns_deny() -> None:
    result = preflight_tool_call(
        create_local_run_context(), forbidden_resource_proposal()
    )
    assert result.error is False
    assert result.decision is PolicyDecision.DENY


def test_invalid_context_returns_deny() -> None:
    result = preflight_tool_call(object(), authorized_proposal())
    assert result.error is False
    assert result.decision is PolicyDecision.DENY


def test_malformed_proposal_returns_deny() -> None:
    result = preflight_tool_call(create_local_run_context(), {"tool": "mock.get_health"})
    assert result.error is False
    assert result.decision is PolicyDecision.DENY


@patch("adaptive_agentops.gateway.evaluate_policy")
def test_pdp_exception_returns_error(mock_evaluate_policy: MagicMock) -> None:
    mock_evaluate_policy.side_effect = RuntimeError("pdp failed")

    result = preflight_tool_call(create_local_run_context(), authorized_proposal())

    mock_evaluate_policy.assert_called_once()
    assert result.error is True
    assert result.decision is None
    assert result.decision is not PolicyDecision.ALLOW


@patch("adaptive_agentops.gateway.evaluate_policy")
def test_invalid_pdp_return_value_returns_error(mock_evaluate_policy: MagicMock) -> None:
    mock_evaluate_policy.return_value = "ALLOW"

    result = preflight_tool_call(create_local_run_context(), authorized_proposal())

    mock_evaluate_policy.assert_called_once()
    assert result.error is True
    assert result.decision is None
    assert result.decision is not PolicyDecision.ALLOW


@patch("adaptive_agentops.gateway.evaluate_policy")
def test_approval_required_is_preserved(mock_evaluate_policy: MagicMock) -> None:
    mock_evaluate_policy.return_value = PolicyDecision.APPROVAL_REQUIRED

    result = preflight_tool_call(create_local_run_context(), authorized_proposal())

    mock_evaluate_policy.assert_called_once()
    assert result.error is False
    assert result.decision is PolicyDecision.APPROVAL_REQUIRED


@patch("adaptive_agentops.gateway.preflight_tool_call", wraps=preflight_tool_call)
def test_allow_is_durably_audited(
    mock_preflight: MagicMock, tmp_path: Path
) -> None:
    context = create_local_run_context()
    proposal = authorized_proposal()
    conn = prepared_audit_conn(tmp_path)
    try:
        result = evaluate_and_audit_preflight(conn, context, proposal)
        mock_preflight.assert_called_once_with(context, proposal)
        assert result.error is False
        assert result.decision is PolicyDecision.ALLOW
    finally:
        conn.close()

    assert persisted_decisions(tmp_path) == [PolicyDecision.ALLOW.value]


def test_deny_is_durably_audited(tmp_path: Path) -> None:
    conn = prepared_audit_conn(tmp_path)
    try:
        result = evaluate_and_audit_preflight(
            conn, create_local_run_context(), forbidden_resource_proposal()
        )
        assert result.error is False
        assert result.decision is PolicyDecision.DENY
    finally:
        conn.close()

    assert persisted_decisions(tmp_path) == [PolicyDecision.DENY.value]


@patch("adaptive_agentops.gateway.preflight_tool_call")
def test_approval_required_is_preserved_and_audited(
    mock_preflight: MagicMock, tmp_path: Path
) -> None:
    mock_preflight.return_value = PreflightResult(
        decision=PolicyDecision.APPROVAL_REQUIRED, error=False
    )
    conn = prepared_audit_conn(tmp_path)
    try:
        result = evaluate_and_audit_preflight(
            conn, create_local_run_context(), authorized_proposal()
        )
        mock_preflight.assert_called_once()
        assert result.error is False
        assert result.decision is PolicyDecision.APPROVAL_REQUIRED
    finally:
        conn.close()

    assert persisted_decisions(tmp_path) == [PolicyDecision.APPROVAL_REQUIRED.value]


@patch("adaptive_agentops.gateway.append_policy_decision")
def test_audit_write_failure_fails_closed(
    mock_append: MagicMock, tmp_path: Path
) -> None:
    mock_append.side_effect = sqlite3.OperationalError("write failed")
    conn = prepared_audit_conn(tmp_path)
    try:
        result = evaluate_and_audit_preflight(
            conn, create_local_run_context(), authorized_proposal()
        )
        assert result.error is True
        assert result.decision is None
        assert result.decision is not PolicyDecision.ALLOW
    finally:
        conn.close()

    assert persisted_decisions(tmp_path) == []


class CommitFailingConnection(sqlite3.Connection):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fail_commit = False

    def commit(self) -> None:
        if self.fail_commit:
            raise sqlite3.OperationalError("commit failed")
        super().commit()


class ResultFailureConnection(sqlite3.Connection):
    failure = None
    begin_calls = 0
    commit_calls = 0
    rollback_calls = 0

    def execute(self, sql, parameters=()):
        if sql == "BEGIN":
            self.begin_calls += 1
            if self.failure == "begin":
                raise sqlite3.OperationalError("SECRET_BEGIN")
        return super().execute(sql, parameters)

    def commit(self):
        self.commit_calls += 1
        if self.failure in ("commit", "rollback"):
            raise sqlite3.OperationalError("SECRET_COMMIT")
        super().commit()

    def rollback(self):
        self.rollback_calls += 1
        if self.failure == "rollback":
            raise sqlite3.OperationalError("SECRET_ROLLBACK")
        super().rollback()


@pytest.mark.parametrize("adapter_fails", [False, True])
def test_result_contract_is_durable(tmp_path, execution_adapter, result_append, adapter_fails):
    conn = prepared_audit_conn(tmp_path)
    context = create_local_run_context()
    proposal = authorized_proposal()
    if adapter_fails:
        execution_adapter.side_effect = RuntimeError("SECRET_EXCEPTION")
    else:
        execution_adapter.return_value = DemoApiHealth(
            service="demo-api", status="healthy", reason="SECRET_RESPONSE"
        )
    expected = ExecutionStatus.FAILED if adapter_fails else ExecutionStatus.SUCCEEDED
    try:
        result = execute_mock_health_read(conn, context, proposal)
        assert result.status.value == expected.value
        assert result.decision is PolicyDecision.ALLOW
        assert not conn.in_transaction
        result_append.assert_called_once_with(conn, context, proposal, expected)
        execution_adapter.assert_called_once_with()
    finally:
        conn.close()
    reopened = connect_audit_db(tmp_path)
    try:
        rows = reopened.execute("SELECT * FROM audit_events ORDER BY rowid").fetchall()
        assert len(rows) == 2
        policy, outcome = rows
        assert policy["event_kind"] == "POLICY_DECISION"
        assert policy["execution_status"] == "NOT_DISPATCHED"
        assert outcome["event_kind"] == "RESULT"
        assert outcome["execution_status"] == expected.value
        assert outcome["reason_code"] == "MOCK_HEALTH_READ_" + expected.value
        assert outcome["policy_decision"] == "ALLOW"
        assert outcome["run_id"] == str(context.run_id)
        assert outcome["requester_id"] == context.requester_id
        assert outcome["agent_id"] == context.agent_id
        assert outcome["environment"] == "local-mock"
        assert outcome["tool"] == "mock.get_health"
        assert outcome["resource"] == "demo-api"
        assert outcome["event_id"] != policy["event_id"]
        assert outcome["timestamp"].endswith("+00:00")
        assert all(outcome[key] is None for key in ("action_id", "approval_id", "dispatch_id"))
        assert "SECRET_" not in repr([dict(row) for row in rows])
    finally:
        reopened.close()


@pytest.mark.parametrize("failure", ["insert", "begin", "commit", "rollback", "foreign_transaction"])
@pytest.mark.parametrize("adapter_fails", [False, True])
def test_result_audit_failure_is_not_retried(
    tmp_path, execution_adapter, result_append, failure, adapter_fails
):
    conn = sqlite3.connect(tmp_path / "audit.db", factory=ResultFailureConnection)
    context = create_local_run_context()
    proposal = authorized_proposal()
    health = execution_adapter.return_value
    try:
        initialize_audit_schema(conn)
        if failure == "insert":
            conn.execute("""
                CREATE TRIGGER reject_result BEFORE INSERT ON audit_events
                WHEN NEW.event_kind = 'RESULT'
                BEGIN SELECT RAISE(ABORT, 'SECRET_INSERT'); END
            """)
        conn.commit()

        def attempt():
            assert not conn.in_transaction
            assert persisted_decisions(tmp_path) == ["ALLOW"]
            conn.begin_calls = conn.commit_calls = conn.rollback_calls = 0
            conn.failure = failure
            if failure == "foreign_transaction":
                conn.execute("BEGIN")
                conn.execute("UPDATE audit_events SET reason_code = 'caller-marker'")
                conn.begin_calls = 0
            if adapter_fails:
                raise RuntimeError("SECRET_ADAPTER")
            return health

        execution_adapter.side_effect = attempt
        result = execute_mock_health_read(conn, context, proposal)
        assert result.status is MockHealthReadStatus.AUDIT_FAILED
        assert result.decision is PolicyDecision.ALLOW
        assert result.health is None
        assert "SECRET_" not in repr(result)
        execution_adapter.assert_called_once_with()
        assert conn.begin_calls == (0 if failure == "foreign_transaction" else 1)
        assert conn.commit_calls == (1 if failure in ("commit", "rollback") else 0)
        owned = failure in ("insert", "commit", "rollback")
        assert conn.rollback_calls == int(owned)
        assert conn.in_transaction is (failure in ("rollback", "foreign_transaction"))
        if owned:
            expected = ExecutionStatus.FAILED if adapter_fails else ExecutionStatus.SUCCEEDED
            result_append.assert_called_once_with(conn, context, proposal, expected)
        else:
            result_append.assert_not_called()
        if failure == "foreign_transaction":
            assert conn.execute("SELECT reason_code FROM audit_events").fetchone()[0] == "caller-marker"
        reopened = connect_audit_db(tmp_path)
        try:
            rows = reopened.execute("SELECT * FROM audit_events").fetchall()
            assert len(rows) == 1
            assert rows[0]["event_kind"] == "POLICY_DECISION"
            assert rows[0]["reason_code"] == "POLICY_ALLOW"
            assert "SECRET_" not in repr(dict(rows[0]))
        finally:
            reopened.close()
    finally:
        conn.close()


def test_simulated_commit_failure_fails_closed(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "audit.db",
        factory=CommitFailingConnection,
    )
    conn.row_factory = sqlite3.Row
    try:
        initialize_audit_schema(conn)
        conn.commit()
        conn.fail_commit = True
        result = evaluate_and_audit_preflight(
            conn, create_local_run_context(), authorized_proposal()
        )
        assert result.error is True
        assert result.decision is None
        assert result.decision is not PolicyDecision.ALLOW
        assert conn.in_transaction is False
    finally:
        conn.close()

    assert persisted_decisions(tmp_path) == []


@patch("adaptive_agentops.gateway.evaluate_policy")
def test_pdp_error_fails_closed(
    mock_evaluate_policy: MagicMock, tmp_path: Path
) -> None:
    mock_evaluate_policy.side_effect = RuntimeError("pdp failed")
    conn = prepared_audit_conn(tmp_path)
    try:
        result = evaluate_and_audit_preflight(
            conn, create_local_run_context(), authorized_proposal()
        )
        assert result.error is True
        assert result.decision is None
        assert result.decision is not PolicyDecision.ALLOW
    finally:
        conn.close()

    assert persisted_decisions(tmp_path) == []


def test_invalid_inputs_fail_closed(tmp_path: Path) -> None:
    conn = prepared_audit_conn(tmp_path)
    try:
        result = evaluate_and_audit_preflight(
            conn, object(), authorized_proposal()
        )
        assert result.error is True
        assert result.decision is None

        result = evaluate_and_audit_preflight(
            conn, create_local_run_context(), {"tool": "mock.get_health"}
        )
        assert result.error is True
        assert result.decision is None
        assert result.decision is not PolicyDecision.ALLOW
    finally:
        conn.close()

    assert persisted_decisions(tmp_path) == []


def test_closed_connection_fails_closed(tmp_path: Path) -> None:
    conn = prepared_audit_conn(tmp_path)
    conn.close()

    result = evaluate_and_audit_preflight(
        conn, create_local_run_context(), authorized_proposal()
    )

    assert result.error is True
    assert result.decision is None
    assert result.decision is not PolicyDecision.ALLOW
    assert persisted_decisions(tmp_path) == []


def test_active_caller_transaction_is_left_untouched(tmp_path: Path) -> None:
    conn = prepared_audit_conn(tmp_path)
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            INSERT INTO audit_events (
                event_id, timestamp, event_kind, run_id, requester_id,
                agent_id, tool, resource, environment, policy_decision,
                execution_status, reason_code
            )
            VALUES (
                'caller-marker', '2026-01-01T00:00:00+00:00',
                'POLICY_DECISION', 'run', 'req', 'agent', 'tool',
                'resource', 'env', 'DENY', 'NOT_DISPATCHED', 'POLICY_DENY'
            )
            """
        )

        result = evaluate_and_audit_preflight(
            conn, create_local_run_context(), authorized_proposal()
        )

        assert result.error is True
        assert result.decision is None
        assert result.decision is not PolicyDecision.ALLOW
        assert conn.in_transaction is True
        event_ids = [
            row["event_id"]
            for row in conn.execute("SELECT event_id FROM audit_events")
        ]
        assert event_ids == ["caller-marker"]

        conn.rollback()
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events"
        ).fetchone()["n"]
        assert remaining == 0
    finally:
        conn.close()

    assert persisted_decisions(tmp_path) == []
