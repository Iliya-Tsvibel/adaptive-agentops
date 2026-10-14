import sqlite3
from pathlib import Path

import pytest

from adaptive_agentops.audit import (
    EventKind,
    ExecutionStatus,
    ReasonCode,
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
