import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from adaptive_agentops.audit import (
    EventKind,
    ExecutionStatus,
    ReasonCode,
    append_mock_health_result,
    append_mock_restart_result,
    append_policy_decision,
    initialize_audit_schema,
)
from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.mock_restart_adapter import (
    MockRestartAcknowledgement, MockRestartResult, RestartReason, RestartStatus,
)
from adaptive_agentops.policy import PolicyDecision
from adaptive_agentops.tool_requests import MockGetHealthProposal


def authorized_proposal() -> MockGetHealthProposal:
    return MockGetHealthProposal.model_validate(
        {
            "tool": "mock.get_health",
            "resource": "demo-api",
            "arguments": {},
        }
    )


def connect_audit_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "audit.db")
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def result_conn(tmp_path):
    conn = connect_audit_db(tmp_path)
    initialize_audit_schema(conn)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize("status, reason", [
    (ExecutionStatus.SUCCEEDED, "MOCK_HEALTH_READ_SUCCEEDED"),
    (ExecutionStatus.FAILED, "MOCK_HEALTH_READ_FAILED"),
])
def test_result_commit_and_metadata(tmp_path, result_conn, status, reason):
    context = create_local_run_context()
    proposal = authorized_proposal()
    append_policy_decision(result_conn, context, proposal, PolicyDecision.ALLOW)
    result_conn.commit()
    before = datetime.now(timezone.utc)
    assert append_mock_health_result(result_conn, context, proposal, status) is None
    after = datetime.now(timezone.utc)
    assert result_conn.in_transaction

    observer = connect_audit_db(tmp_path)
    try:
        assert observer.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1
    finally:
        observer.close()
    result_conn.commit()
    result_conn.close()

    reopened = connect_audit_db(tmp_path)
    try:
        rows = reopened.execute("SELECT * FROM audit_events ORDER BY rowid").fetchall()
        assert len(rows) == 2
        policy, result = rows
        assert policy["event_kind"] == "POLICY_DECISION"
        assert policy["execution_status"] == "NOT_DISPATCHED"
        assert result["event_kind"] == EventKind.RESULT.value
        assert result["execution_status"] == status.value
        assert result["policy_decision"] == "ALLOW"
        assert result["reason_code"] == reason
        assert result["reason_code"] == ReasonCode(reason).value
        assert len(result["reason_code"]) <= 32
        assert result["run_id"] == str(context.run_id)
        assert result["requester_id"] == context.requester_id
        assert result["agent_id"] == context.agent_id
        assert result["tool"] == "mock.get_health"
        assert result["resource"] == "demo-api"
        assert result["environment"] == "local-mock"
        assert all(result[key] is None for key in ("action_id", "approval_id", "dispatch_id"))
        assert UUID(result["event_id"]).version == 4
        assert result["event_id"] != policy["event_id"]
        timestamp = datetime.fromisoformat(result["timestamp"])
        assert timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)
        assert before <= timestamp <= after
    finally:
        reopened.close()


@pytest.mark.parametrize("status", [ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED])
def test_result_rollback_preserves_prior_policy(tmp_path, result_conn, status):
    context = create_local_run_context()
    proposal = authorized_proposal()
    append_policy_decision(result_conn, context, proposal, PolicyDecision.ALLOW)
    result_conn.commit()
    append_mock_health_result(result_conn, context, proposal, status)
    assert result_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 2
    result_conn.rollback()
    reopened = connect_audit_db(tmp_path)
    try:
        rows = reopened.execute("SELECT event_kind FROM audit_events").fetchall()
        assert [row[0] for row in rows] == ["POLICY_DECISION"]
    finally:
        reopened.close()


def test_result_insert_error_propagates_without_rollback(result_conn):
    context = create_local_run_context()
    proposal = authorized_proposal()
    result_conn.execute("""
        CREATE TRIGGER reject_result BEFORE INSERT ON audit_events
        WHEN NEW.event_kind = 'RESULT'
        BEGIN SELECT RAISE(ABORT, 'insert failure'); END
    """)
    result_conn.commit()
    append_policy_decision(result_conn, context, proposal, PolicyDecision.ALLOW)
    with pytest.raises(sqlite3.IntegrityError, match="insert failure"):
        append_mock_health_result(result_conn, context, proposal, ExecutionStatus.FAILED)
    assert result_conn.in_transaction
    assert result_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1
    result_conn.rollback()


class OtherStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"


@pytest.mark.parametrize("status", [
    ExecutionStatus.NOT_DISPATCHED, "SUCCEEDED", "FAILED", OtherStatus.SUCCEEDED,
    None, True, 1, {}, object(), RuntimeError("SECRET_EXCEPTION_CANARY"),
    '{"reason": "SECRET_RESPONSE_CANARY"}', "SECRET_STRING_CANARY",
])
def test_result_rejects_unsupported_status_without_serializing(result_conn, status):
    with pytest.raises(ValueError):
        append_mock_health_result(
            result_conn, create_local_run_context(), authorized_proposal(), status
        )
    assert not result_conn.in_transaction
    # A subsequent valid failure stores only the fixed reason code.
    append_mock_health_result(
        result_conn, create_local_run_context(), authorized_proposal(), ExecutionStatus.FAILED
    )
    result_conn.commit()
    rows = result_conn.execute("SELECT * FROM audit_events").fetchall()
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "MOCK_HEALTH_READ_FAILED"
    stored = repr([dict(row) for row in rows])
    assert "SECRET_" not in stored


@pytest.mark.parametrize("bad_input", ["context", "proposal", "resource", "environment", "tool", "arguments"])
def test_result_rejects_invalid_inputs(result_conn, bad_input):
    context = create_local_run_context()
    proposal = authorized_proposal()
    if bad_input == "context":
        context = {"requester_id": "SECRET_IDENTITY_CANARY"}
    elif bad_input == "proposal":
        proposal = proposal.model_dump()
    elif bad_input == "environment":
        context = replace(context, environment="production")
    elif bad_input == "resource":
        proposal = MockGetHealthProposal.model_validate({
            **proposal.model_dump(), "resource": "payments-api"
        })
    elif bad_input == "tool":
        proposal = proposal.model_copy(update={"tool": "mock.mutate"})
    else:
        proposal = proposal.model_copy(update={"arguments": {"secret": "SECRET_ARGUMENT_CANARY"}})
    with pytest.raises((TypeError, ValueError)):
        append_mock_health_result(result_conn, context, proposal, ExecutionStatus.SUCCEEDED)
    assert not result_conn.in_transaction
    assert result_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0


def test_result_does_not_accept_free_form_metadata(result_conn):
    for extra in (
        {"reason": "SECRET_REASON_CANARY"},
        {"exception": RuntimeError("SECRET_EXCEPTION_CANARY")},
        {"response": {"body": "SECRET_RESPONSE_CANARY"}},
    ):
        with pytest.raises(TypeError):
            append_mock_health_result(
                result_conn, create_local_run_context(), authorized_proposal(),
                ExecutionStatus.FAILED, **extra
            )
    assert result_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0


def test_successful_durable_insert_after_commit(tmp_path: Path) -> None:
    conn = connect_audit_db(tmp_path)
    try:
        initialize_audit_schema(conn)
        append_policy_decision(
            conn,
            create_local_run_context(),
            authorized_proposal(),
            PolicyDecision.ALLOW,
        )
        conn.commit()

        rows = conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchall()
        assert rows[0]["n"] == 1
    finally:
        conn.close()

    reopened = connect_audit_db(tmp_path)
    try:
        persisted = reopened.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()
        assert persisted["n"] == 1
    finally:
        reopened.close()


def test_persisted_trusted_identity_and_decision(tmp_path: Path) -> None:
    context = create_local_run_context()
    proposal = authorized_proposal()
    conn = connect_audit_db(tmp_path)
    try:
        initialize_audit_schema(conn)
        append_policy_decision(conn, context, proposal, PolicyDecision.DENY)
        conn.commit()
    finally:
        conn.close()

    reopened = connect_audit_db(tmp_path)
    try:
        row = reopened.execute("SELECT * FROM audit_events").fetchone()
        assert row["event_kind"] == EventKind.POLICY_DECISION.value
        assert row["run_id"] == str(context.run_id)
        assert row["requester_id"] == context.requester_id
        assert row["agent_id"] == context.agent_id
        assert row["environment"] == context.environment
        assert row["tool"] == proposal.tool
        assert row["resource"] == proposal.resource
        assert row["policy_decision"] == PolicyDecision.DENY.value
        assert row["reason_code"] == ReasonCode.POLICY_DENY.value
        assert row["action_id"] is None
        assert row["approval_id"] is None
        assert row["dispatch_id"] is None
        assert row["event_id"]
        assert row["timestamp"].endswith("+00:00") or "Z" in row["timestamp"]
    finally:
        reopened.close()


def test_allow_still_means_not_dispatched(tmp_path: Path) -> None:
    conn = connect_audit_db(tmp_path)
    try:
        initialize_audit_schema(conn)
        append_policy_decision(
            conn,
            create_local_run_context(),
            authorized_proposal(),
            PolicyDecision.ALLOW,
        )
        conn.commit()

        row = conn.execute("SELECT policy_decision, execution_status FROM audit_events").fetchone()
        assert row["policy_decision"] == PolicyDecision.ALLOW.value
        assert row["execution_status"] == ExecutionStatus.NOT_DISPATCHED.value
    finally:
        conn.close()


def test_transaction_rollback_removes_uncommitted_event(tmp_path: Path) -> None:
    conn = connect_audit_db(tmp_path)
    try:
        initialize_audit_schema(conn)
        conn.commit()
        append_policy_decision(
            conn,
            create_local_run_context(),
            authorized_proposal(),
            PolicyDecision.ALLOW,
        )
        conn.rollback()

        count = conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()["n"]
        assert count == 0
    finally:
        conn.close()


def test_sqlite_write_failure_is_not_swallowed(tmp_path: Path) -> None:
    conn = connect_audit_db(tmp_path)
    try:
        initialize_audit_schema(conn)
        conn.commit()
        conn.execute("PRAGMA query_only = ON")

        with pytest.raises(sqlite3.Error):
            append_policy_decision(
                conn,
                create_local_run_context(),
                authorized_proposal(),
                PolicyDecision.ALLOW,
            )
    finally:
        conn.close()


def acknowledged_restart():
    # Synthetic adapter-boundary result; audit tests never send HTTP requests.
    return MockRestartResult(
        RestartStatus.ACKNOWLEDGED, RestartReason.MOCK_ACKNOWLEDGED,
        MockRestartAcknowledgement(service="demo-api", status="restart_requested", simulated=True),
    )


def reserve_for_audit(conn, context):
    from adaptive_agentops.actions import initialize_action_schema, insert_pending_restart_action
    from adaptive_agentops.approvals import (
        ApprovalDecision, initialize_approval_schema, record_operator_decision,
    )
    from adaptive_agentops.dispatch_control import ReservationStatus, reserve_approved_restart
    from adaptive_agentops.dispatch_reservations import initialize_dispatch_reservation_schema
    from adaptive_agentops.tool_requests import MockRestartServiceProposal

    initialize_action_schema(conn)
    initialize_approval_schema(conn)
    initialize_dispatch_reservation_schema(conn)
    action = insert_pending_restart_action(conn, context, MockRestartServiceProposal(
        tool="mock.restart_service", resource="demo-api", arguments={}
    ))
    conn.commit()
    record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
    conn.commit()
    outcome = reserve_approved_restart(conn, context, action.action_id)
    assert outcome.status is ReservationStatus.RESERVED
    return outcome.reservation


@pytest.fixture
def restart_audit(result_conn):
    context = create_local_run_context()
    with patch("adaptive_agentops.mock_restart_adapter.request_demo_api_restart") as adapter:
        reservation = reserve_for_audit(result_conn, context)
        yield result_conn, context, reservation
        adapter.assert_not_called()


@pytest.mark.parametrize("restart_result, status, reason", [
    (acknowledged_restart(), "ACKNOWLEDGED", "MOCK_RESTART_ACKNOWLEDGED"),
    *[(MockRestartResult(RestartStatus.UNCERTAIN, reason), "UNCERTAIN", "MOCK_RESTART_UNCERTAIN")
      for reason in RestartReason if reason is not RestartReason.MOCK_ACKNOWLEDGED],
])
def test_restart_result_correlation_and_commit_visibility(
    restart_audit, tmp_path, restart_result, status, reason
):
    conn, context, reservation = restart_audit
    before = datetime.now(timezone.utc)
    assert append_mock_restart_result(conn, context, reservation, restart_result) is None
    after = datetime.now(timezone.utc)
    assert conn.in_transaction
    observer = connect_audit_db(tmp_path)
    try:
        assert observer.execute("SELECT COUNT(*) FROM audit_events WHERE event_kind='RESULT'").fetchone()[0] == 0
    finally:
        observer.close()
    conn.commit()
    reopened = connect_audit_db(tmp_path)
    try:
        rows = reopened.execute("SELECT * FROM audit_events ORDER BY rowid").fetchall()
        assert len(rows) == 2
        assert rows[0]["event_kind"] == "DISPATCH_RESERVED"
        assert rows[0]["execution_status"] == "NOT_DISPATCHED"
        result = rows[1]
        assert result["event_kind"] == "RESULT"
        assert result["execution_status"] == status
        assert result["reason_code"] == reason
        assert result["policy_decision"] == "APPROVAL_REQUIRED"
        for field in ("action_id", "approval_id", "dispatch_id", "run_id"):
            assert result[field] == str(getattr(reservation, field))
        for field in ("requester_id", "agent_id", "environment"):
            assert result[field] == getattr(context, field)
        assert result["tool"] == "mock.restart_service"
        assert result["resource"] == "demo-api"
        assert UUID(result["event_id"]).version == 4
        assert result["event_id"] != rows[0]["event_id"]
        timestamp = datetime.fromisoformat(result["timestamp"])
        assert timestamp.utcoffset() == timedelta(0)
        assert before <= timestamp <= after
        assert "SECRET" not in repr([dict(row) for row in rows])
    finally:
        reopened.close()


@pytest.mark.parametrize("result", [
    {}, None, RuntimeError("SECRET_ERROR"),
    MockRestartResult("ACKNOWLEDGED", RestartReason.MOCK_ACKNOWLEDGED, acknowledged_restart().response),
    MockRestartResult(RestartStatus.ACKNOWLEDGED, "MOCK_ACKNOWLEDGED", acknowledged_restart().response),
    MockRestartResult(RestartStatus.ACKNOWLEDGED, RestartReason.TIMEOUT, acknowledged_restart().response),
    MockRestartResult(RestartStatus.ACKNOWLEDGED, RestartReason.MOCK_ACKNOWLEDGED),
    MockRestartResult(RestartStatus.ACKNOWLEDGED, RestartReason.MOCK_ACKNOWLEDGED, {"secret": "SECRET"}),
    MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.MOCK_ACKNOWLEDGED),
    MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.TIMEOUT, acknowledged_restart().response),
    MockRestartResult(RestartStatus.UNCERTAIN, "SECRET_REASON"),
    MockRestartResult(None, RestartReason.TIMEOUT),
    MockRestartResult(RestartStatus.ACKNOWLEDGED, RestartReason.MOCK_ACKNOWLEDGED,
                      acknowledged_restart().response.model_copy(update={"simulated": 1})),
    MockRestartResult(RestartStatus.ACKNOWLEDGED, RestartReason.MOCK_ACKNOWLEDGED,
                      acknowledged_restart().response.model_copy(update={"status": "SECRET_STATUS"})),
    MockRestartResult(RestartStatus.ACKNOWLEDGED, RestartReason.MOCK_ACKNOWLEDGED,
                      acknowledged_restart().response.model_copy(update={"credentials": "SECRET"})),
])
def test_restart_rejects_invalid_or_contradictory_result(restart_audit, result):
    conn, context, reservation = restart_audit
    with pytest.raises(ValueError, match="Invalid mock restart result inputs") as error:
        append_mock_restart_result(conn, context, reservation, result)
    assert "SECRET" not in str(error.value)
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1


@pytest.mark.parametrize("field, value", [
    ("action_id", uuid4()), ("approval_id", uuid4()), ("dispatch_id", uuid4()),
    ("run_id", uuid4()), ("action_fingerprint", "0" * 64),
    ("state", "EXECUTED"), ("approval_id", "SECRET_ID"),
    ("credentials", "SECRET_METADATA"),
])
def test_restart_rejects_wrong_reservation_binding(restart_audit, field, value):
    conn, context, reservation = restart_audit
    forged = reservation.model_copy(update={field: value})
    with pytest.raises(ValueError):
        append_mock_restart_result(conn, context, forged, acknowledged_restart())
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1


@pytest.mark.parametrize("field, value", [
    ("run_id", uuid4()), ("requester_id", "SECRET_REQUESTER"),
    ("agent_id", "other"), ("environment", "production"),
])
def test_restart_rejects_untrusted_context_binding(restart_audit, field, value):
    conn, context, reservation = restart_audit
    with pytest.raises(ValueError):
        append_mock_restart_result(conn, replace(context, **{field: value}), reservation, acknowledged_restart())
    assert not conn.in_transaction


def test_restart_requires_typed_context_reservation_and_result(restart_audit):
    conn, context, reservation = restart_audit
    for inputs in (
        ({"run_id": context.run_id}, reservation, acknowledged_restart()),
        (context, reservation.model_dump(), acknowledged_restart()),
        (context, reservation, None),
    ):
        with pytest.raises(ValueError):
            append_mock_restart_result(conn, *inputs)
    assert not conn.in_transaction


@pytest.mark.parametrize("field", ["action_id", "approval_id", "dispatch_id", "requester_id", "policy_decision"])
def test_restart_requires_correlated_pre_dispatch_evidence(restart_audit, field):
    conn, context, reservation = restart_audit
    conn.execute(f"UPDATE audit_events SET {field}=?", ("wrong",))
    conn.commit()
    with pytest.raises(ValueError, match="pre-dispatch"):
        append_mock_restart_result(conn, context, reservation, acknowledged_restart())
    assert not conn.in_transaction


def test_restart_duplicate_result_sqlite_guard_and_independent_dispatches(restart_audit):
    conn, context, first = restart_audit
    append_mock_restart_result(conn, context, first, acknowledged_restart())
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        append_mock_restart_result(
            conn, context, first, MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.TIMEOUT)
        )
    conn.rollback()
    # Direct SQL also hits the unique index, independently of Python validation.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO audit_events
            SELECT ?, timestamp, event_kind, run_id, requester_id, agent_id,
                   tool, resource, environment, policy_decision, execution_status,
                   reason_code, action_id, approval_id, dispatch_id
            FROM audit_events WHERE event_kind='RESULT'
        """, (str(uuid4()),))
    conn.rollback()
    second = reserve_for_audit(conn, context)
    assert first.action_fingerprint == second.action_fingerprint
    assert first.dispatch_id != second.dispatch_id
    append_mock_restart_result(conn, context, second, acknowledged_restart())
    # Read RESULT rows remain independent and do not participate in this index.
    for _ in range(2):
        append_mock_health_result(conn, context, authorized_proposal(), ExecutionStatus.SUCCEEDED)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE event_kind='RESULT'").fetchone()[0] == 4


def test_restart_result_rollback_removes_only_result(restart_audit, tmp_path):
    conn, context, reservation = restart_audit
    conn.execute("BEGIN IMMEDIATE")
    append_mock_restart_result(conn, context, reservation, acknowledged_restart())
    assert conn.in_transaction
    conn.rollback()
    reopened = connect_audit_db(tmp_path)
    try:
        assert [row[0] for row in reopened.execute("SELECT event_kind FROM audit_events")] == ["DISPATCH_RESERVED"]
    finally:
        reopened.close()


def test_restart_result_insert_error_propagates_without_retry(restart_audit):
    conn, context, reservation = restart_audit
    attempts = []
    conn.create_function("note_result_attempt", 0, lambda: attempts.append(1) or 0)
    conn.execute("""
        CREATE TRIGGER fail_restart_result BEFORE INSERT ON audit_events
        WHEN NEW.event_kind='RESULT'
        BEGIN
            SELECT note_result_attempt();
            SELECT RAISE(ABORT, 'SECRET_INSERT_FAILURE');
        END
    """)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.IntegrityError):
        append_mock_restart_result(conn, context, reservation, acknowledged_restart())
    assert attempts == [1]
    assert conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1
    conn.rollback()


def test_restart_audit_does_not_change_reservation_or_consume_approval(restart_audit):
    conn, context, reservation = restart_audit
    tables = ("actions", "approvals", "dispatch_reservations")
    before = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables}
    with patch("adaptive_agentops.policy.evaluate_policy") as pdp:
        append_mock_restart_result(conn, context, reservation, acknowledged_restart())
        pdp.assert_not_called()
    conn.commit()
    assert {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables} == before


def test_restart_outcome_can_be_audited_after_original_expiry(restart_audit):
    conn, context, reservation = restart_audit
    with patch("adaptive_agentops.audit.datetime") as clock:
        clock.now.return_value = reservation.reserved_at + timedelta(hours=1)
        append_mock_restart_result(conn, context, reservation, MockRestartResult(
            RestartStatus.UNCERTAIN, RestartReason.TIMEOUT
        ))
    conn.commit()
    row = conn.execute("SELECT execution_status FROM audit_events WHERE event_kind='RESULT'").fetchone()
    assert row[0] == "UNCERTAIN"
