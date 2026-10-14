import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from adaptive_agentops.audit import initialize_audit_schema
from adaptive_agentops.gateway import (
    PreflightResult,
    evaluate_and_audit_preflight,
    preflight_tool_call,
)
from adaptive_agentops.identity import create_local_run_context
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
