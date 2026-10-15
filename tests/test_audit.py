import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import UUID

import pytest

from adaptive_agentops.audit import (
    EventKind,
    ExecutionStatus,
    ReasonCode,
    append_mock_health_result,
    append_policy_decision,
    initialize_audit_schema,
)
from adaptive_agentops.identity import create_local_run_context
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
