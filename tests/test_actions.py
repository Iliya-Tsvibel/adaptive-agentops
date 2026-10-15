import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from adaptive_agentops.actions import (
    _fingerprint, get_pending_restart_action, initialize_action_schema,
    insert_pending_restart_action,
)
from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import EmptyArguments, MockRestartServiceProposal


def proposal():
    return MockRestartServiceProposal(
        tool="mock.restart_service", resource="demo-api", arguments={}
    )


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "actions.db")
    initialize_action_schema(connection)
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def test_create_commit_reopen_and_immutable_scope(conn, tmp_path):
    context = create_local_run_context()
    request = proposal()
    before = datetime.now(timezone.utc)
    with patch("adaptive_agentops.actions.evaluate_policy", wraps=evaluate_policy) as pdp:
        action = insert_pending_restart_action(conn, context, request)
    pdp.assert_called_once_with(context, request)
    assert action.action_id.version == 4
    assert action.run_id == context.run_id
    assert action.requester_id == context.requester_id
    assert action.agent_id == context.agent_id
    assert action.tool == "mock.restart_service"
    assert action.resource == "demo-api"
    assert action.environment == "local-mock"
    assert action.arguments.model_dump() == {}
    assert action.state == "PENDING"
    assert before <= action.created_at <= datetime.now(timezone.utc)
    assert action.created_at.utcoffset() == timedelta(0)
    assert action.expires_at - action.created_at == timedelta(minutes=10)
    with pytest.raises(ValidationError):
        action.resource = "other"
    with pytest.raises(ValidationError):
        action.arguments.force = True
    observer = sqlite3.connect(tmp_path / "actions.db")
    try:
        assert get_pending_restart_action(observer, action.action_id) is None
    finally:
        observer.close()
    assert conn.in_transaction
    conn.commit()
    reopened = sqlite3.connect(tmp_path / "actions.db")
    try:
        assert get_pending_restart_action(reopened, action.action_id) == action
        assert get_pending_restart_action(reopened, uuid4()) is None
    finally:
        reopened.close()


def test_identity_and_fingerprint_contract(conn):
    context = create_local_run_context()
    first = insert_pending_restart_action(conn, context, proposal())
    second = insert_pending_restart_action(conn, context, proposal())
    assert first.action_id != second.action_id
    assert first.action_fingerprint == second.action_fingerprint
    scope = {
        "run_id": str(context.run_id), "requester_id": context.requester_id,
        "agent_id": context.agent_id, "tool": "mock.restart_service",
        "resource": "demo-api", "environment": "local-mock", "arguments": {},
    }
    encoded = json.dumps({**scope, "version": 1}, sort_keys=True, separators=(",", ":"))
    assert first.action_fingerprint == hashlib.sha256(encoded.encode()).hexdigest()
    changed = insert_pending_restart_action(conn, replace(context, run_id=uuid4()), proposal())
    assert changed.action_fingerprint != first.action_fingerprint
    for key in scope:
        updated = {**scope, key: {"force": True} if key == "arguments" else "changed"}
        assert _fingerprint(updated) != first.action_fingerprint
    assert _fingerprint(dict(reversed(list(scope.items())))) == first.action_fingerprint
    with patch("adaptive_agentops.actions.uuid4", return_value=first.action_id):
        with pytest.raises(sqlite3.IntegrityError):
            insert_pending_restart_action(conn, context, proposal())


@pytest.mark.parametrize("field", ["requester_id", "agent_id", "environment", "run_id"])
def test_wrong_context_rejected(conn, field):
    context = replace(create_local_run_context(), **{field: "forged"})
    with pytest.raises(ValueError):
        insert_pending_restart_action(conn, context, proposal())
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0


@pytest.mark.parametrize("field, value", [
    ("action_id", "forged"), ("approval_id", "forged"), ("role", "admin"),
    ("requester_id", "forged"), ("agent_id", "forged"), ("environment", "production"),
    ("created_at", "forged"), ("expires_at", "forged"), ("credentials", "SECRET"),
    ("resource", "other"), ("tool", "mock.get_health"), ("resource", 42),
    ("arguments", {"force": True}), ("arguments", {}),
    ("arguments", EmptyArguments().model_copy(update={"force": True})),
])
def test_tampered_copies_rejected(conn, field, value):
    request = proposal().model_copy(update={field: value})
    with pytest.raises(ValueError, match="Invalid or unauthorized"):
        insert_pending_restart_action(conn, create_local_run_context(), request)
    assert not conn.in_transaction


@pytest.mark.parametrize("candidate", [
    None, {}, "mock.restart_service", MockRestartServiceProposal.model_construct(),
    MockRestartServiceProposal.model_construct(
        tool="mock.restart_service", resource="demo-api", arguments={}
    ),
    MockRestartServiceProposal.model_construct(
        tool="other", resource="demo-api", arguments=EmptyArguments()
    ),
])
def test_invalid_and_unchecked_proposals_rejected(conn, candidate):
    with pytest.raises(ValueError):
        insert_pending_restart_action(conn, create_local_run_context(), candidate)
    assert not conn.in_transaction


@pytest.mark.parametrize("behavior", [
    {"return_value": PolicyDecision.DENY}, {"return_value": PolicyDecision.ALLOW},
    {"return_value": "APPROVAL_REQUIRED"}, {"return_value": None},
    {"side_effect": RuntimeError("SECRET_PDP")},
])
def test_pdp_gate_fails_closed(conn, behavior):
    with patch("adaptive_agentops.actions.evaluate_policy", **behavior) as pdp:
        with pytest.raises(ValueError) as error:
            insert_pending_restart_action(conn, create_local_run_context(), proposal())
    pdp.assert_called_once()
    assert "SECRET" not in str(error.value)
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0


def test_rollback_and_insert_failure_leave_transaction_to_caller(conn, tmp_path):
    action = insert_pending_restart_action(conn, create_local_run_context(), proposal())
    conn.rollback()
    assert get_pending_restart_action(conn, action.action_id) is None
    conn.execute("""
        CREATE TRIGGER reject_action BEFORE INSERT ON actions
        BEGIN SELECT RAISE(ABORT, 'controlled failure'); END
    """)
    conn.commit()
    conn.execute("BEGIN")
    with pytest.raises(sqlite3.IntegrityError):
        insert_pending_restart_action(conn, create_local_run_context(), proposal())
    assert conn.in_transaction
    conn.rollback()
    reopened = sqlite3.connect(tmp_path / "actions.db")
    try:
        assert reopened.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0
    finally:
        reopened.close()


@pytest.mark.parametrize("field, value", [
    ("run_id", str(uuid4())), ("requester_id", "forged"), ("agent_id", "forged"),
    ("tool", "other"), ("resource", "other"), ("environment", "production"),
    ("arguments", '{"secret":"SECRET"}'), ("arguments", "[]"),
    ("action_fingerprint", "0" * 64), ("created_at", "bad"),
    ("expires_at", "2000-01-01T00:00:00+00:00"), ("state", "APPROVED"),
])
def test_tampered_persisted_records_rejected(conn, field, value):
    action = insert_pending_restart_action(conn, create_local_run_context(), proposal())
    # Column names come exclusively from this fixed test parameter list.
    conn.execute(f"UPDATE actions SET {field} = ? WHERE action_id = ?", (value, str(action.action_id)))
    conn.commit()
    with pytest.raises(ValueError, match="Invalid persisted") as error:
        get_pending_restart_action(conn, action.action_id)
    assert "SECRET" not in str(error.value)


def test_no_dispatch_or_additional_tables(conn):
    with patch("adaptive_agentops.mock_adapter.get_demo_api_health") as adapter:
        insert_pending_restart_action(conn, create_local_run_context(), proposal())
        adapter.assert_not_called()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    assert tables == [("actions",)]


def test_no_client_selected_identifiers_or_times(conn):
    for name in ("action_id", "created_at", "expires_at", "approval_id"):
        with pytest.raises(TypeError):
            insert_pending_restart_action(
                conn, create_local_run_context(), proposal(), **{name: "forged"}
            )
    assert not conn.in_transaction


def test_invalid_lookup_and_database_failure(conn):
    with pytest.raises(ValueError):
        get_pending_restart_action(conn, "' OR 1=1 --")
    conn.close()
    with pytest.raises(sqlite3.Error):
        get_pending_restart_action(conn, uuid4())
