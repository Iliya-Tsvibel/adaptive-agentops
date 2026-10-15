import sqlite3
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from adaptive_agentops.actions import initialize_action_schema, insert_pending_restart_action
from adaptive_agentops.approvals import (
    ApprovalDecision, RestartApproval, get_approval_by_action_id,
    initialize_approval_schema, record_operator_decision,
)
from adaptive_agentops.identity import LOCAL_APPROVER_ID, create_local_run_context
from adaptive_agentops.tool_requests import MockRestartServiceProposal


def new_action(conn, context=None):
    return insert_pending_restart_action(
        conn, context or create_local_run_context(),
        MockRestartServiceProposal(tool="mock.restart_service", resource="demo-api", arguments={}),
    )


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db", timeout=0)
    initialize_action_schema(conn)
    initialize_approval_schema(conn)
    action = new_action(conn)
    conn.commit()
    try:
        yield conn, action, tmp_path / "state.db"
    finally:
        conn.close()


@pytest.mark.parametrize("decision", list(ApprovalDecision))
def test_decision_commit_binding_and_immutability(db, decision):
    conn, action, path = db
    original = conn.execute("SELECT * FROM actions").fetchall()
    conn.execute("BEGIN IMMEDIATE")
    with patch("adaptive_agentops.mock_adapter.get_demo_api_health") as adapter:
        approval = record_operator_decision(conn, action.action_id, decision)
        adapter.assert_not_called()
    assert approval.approval_id.version == 4
    assert approval.decision is decision
    assert approval.approver_id == LOCAL_APPROVER_ID
    assert approval.consumed is False
    for field in ("action_id", "run_id", "requester_id", "agent_id", "action_fingerprint", "expires_at"):
        assert getattr(approval, field) == getattr(action, field)
    assert action.created_at <= approval.created_at < action.expires_at
    assert approval.created_at.utcoffset() == timedelta(0)
    with pytest.raises(ValidationError):
        approval.decision = ApprovalDecision.REJECTED
    with pytest.raises(ValidationError):
        RestartApproval.model_validate({**approval.model_dump(), "role": "admin"})
    observer = sqlite3.connect(path)
    try:
        assert get_approval_by_action_id(observer, action.action_id) is None
    finally:
        observer.close()
    assert conn.in_transaction
    conn.commit()
    reopened = sqlite3.connect(path)
    try:
        assert get_approval_by_action_id(reopened, action.action_id) == approval
        assert reopened.execute("SELECT * FROM actions").fetchall() == original
        assert {row[0] for row in reopened.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )} == {"actions", "approvals"}
    finally:
        reopened.close()


@pytest.mark.parametrize("offset", [0, 1, -1])
@pytest.mark.parametrize("decision", list(ApprovalDecision))
def test_expiry_boundary(db, offset, decision):
    conn, action, _ = db
    with patch("adaptive_agentops.approvals.datetime") as clock:
        clock.now.return_value = action.expires_at + timedelta(microseconds=offset)
        if offset < 0:
            approval = record_operator_decision(conn, action.action_id, decision)
            assert approval.expires_at == action.expires_at
        else:
            with pytest.raises(ValueError, match="window"):
                record_operator_decision(conn, action.action_id, decision)
            assert not conn.in_transaction


@pytest.mark.parametrize("first", list(ApprovalDecision))
@pytest.mark.parametrize("second", list(ApprovalDecision))
def test_decision_cannot_be_replaced(db, first, second):
    conn, action, _ = db
    original = record_operator_decision(conn, action.action_id, first)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        record_operator_decision(conn, action.action_id, second)
    conn.rollback()
    assert get_approval_by_action_id(conn, action.action_id) == original


def test_identical_scope_actions_are_independent(db):
    conn, _, _ = db
    context = create_local_run_context()
    first, second = new_action(conn, context), new_action(conn, context)
    conn.commit()
    assert first.action_fingerprint == second.action_fingerprint
    a = record_operator_decision(conn, first.action_id, ApprovalDecision.REJECTED)
    b = record_operator_decision(conn, second.action_id, ApprovalDecision.APPROVED)
    assert a.action_id != b.action_id
    assert a.approval_id != b.approval_id
    conn.commit()
    assert get_approval_by_action_id(conn, first.action_id).decision is ApprovalDecision.REJECTED
    assert get_approval_by_action_id(conn, second.action_id).decision is ApprovalDecision.APPROVED


def test_missing_and_uncommitted_actions_rejected(db):
    conn, _, _ = db
    with pytest.raises(ValueError):
        record_operator_decision(conn, uuid4(), ApprovalDecision.APPROVED)
    uncommitted = new_action(conn)
    with pytest.raises(ValueError, match="committed"):
        record_operator_decision(conn, uncommitted.action_id, ApprovalDecision.APPROVED)
    assert conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


@pytest.mark.parametrize("decision", ["APPROVED", "REJECTED", None, True, {}, 1, RuntimeError("SECRET")])
def test_invalid_decisions_rejected(db, decision):
    conn, action, _ = db
    with pytest.raises(ValueError) as error:
        record_operator_decision(conn, action.action_id, decision)
    assert "SECRET" not in str(error.value)
    assert not conn.in_transaction


@pytest.mark.parametrize("field, value", [
    ("run_id", str(uuid4())), ("requester_id", "SECRET"), ("agent_id", "other"),
    ("resource", "other"), ("arguments", '{"secret":"SECRET"}'),
    ("action_fingerprint", "0" * 64), ("state", "APPROVED"),
])
def test_tampered_actions_rejected(db, field, value):
    conn, action, _ = db
    conn.execute(f"UPDATE actions SET {field}=? WHERE action_id=?", (value, str(action.action_id)))
    conn.commit()
    with pytest.raises(ValueError) as error:
        record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
    assert "SECRET" not in str(error.value)
    assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


@pytest.mark.parametrize("field, value", [
    ("approval_id", "invalid"), ("run_id", str(uuid4())),
    ("requester_id", "SECRET"), ("agent_id", "other"),
    ("action_fingerprint", "0" * 64), ("approver_id", "requester"),
    ("created_at", "2000-01-01T00:00:00Z"),
    ("expires_at", "2099-01-01T00:00:00Z"),
])
def test_tampered_approvals_rejected(db, field, value):
    conn, action, _ = db
    record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
    conn.commit()
    # Simulate corruption by an owner who can bypass database protections;
    # retrieval must still validate the stored record independently.
    conn.execute("DROP TRIGGER approvals_guard_update")
    conn.execute(f"UPDATE approvals SET {field}=? WHERE action_id=?", (value, str(action.action_id)))
    conn.commit()
    with pytest.raises(ValueError) as error:
        get_approval_by_action_id(conn, action.action_id)
    assert "SECRET" not in str(error.value)


def test_rollback_and_write_failure_propagate(db):
    conn, action, path = db
    record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
    conn.rollback()
    assert get_approval_by_action_id(conn, action.action_id) is None
    conn.execute("""
        CREATE TRIGGER reject_approval BEFORE INSERT ON approvals
        BEGIN SELECT RAISE(ABORT, 'SECRET_DB'); END
    """)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.IntegrityError):
        record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
    assert conn.in_transaction
    conn.rollback()
    observer = sqlite3.connect(path)
    try:
        assert observer.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    finally:
        observer.close()


def test_concurrent_decisions_have_one_winner(db):
    conn, action, path = db
    other = sqlite3.connect(path, timeout=0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
        with pytest.raises(sqlite3.OperationalError):
            record_operator_decision(other, action.action_id, ApprovalDecision.REJECTED)
        other.rollback()
        conn.commit()
        # A separate explicit attempt still cannot replace the committed winner.
        with pytest.raises(sqlite3.IntegrityError):
            record_operator_decision(other, action.action_id, ApprovalDecision.REJECTED)
        other.rollback()
        assert get_approval_by_action_id(other, action.action_id).decision is ApprovalDecision.APPROVED
    finally:
        other.close()


def test_uncommitted_scope_change_and_expired_read_rejected(db):
    conn, action, _ = db
    record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
    conn.commit()
    with patch("adaptive_agentops.approvals.datetime") as clock:
        clock.now.return_value = action.expires_at
        with pytest.raises(ValueError):
            get_approval_by_action_id(conn, action.action_id)
    conn.execute("UPDATE actions SET state='APPROVED'")
    with pytest.raises(ValueError):
        get_approval_by_action_id(conn, action.action_id)


def test_no_caller_metadata_and_no_sensitive_storage(db):
    conn, action, _ = db
    for key in ("approver_id", "approval_id", "expires_at", "scope", "credentials"):
        with pytest.raises(TypeError):
            record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED, **{key: "SECRET"})
    record_operator_decision(conn, action.action_id, ApprovalDecision.REJECTED)
    assert "SECRET" not in repr(conn.execute("SELECT * FROM approvals").fetchall())


@pytest.mark.parametrize("decision", list(ApprovalDecision))
def test_consumed_record_inspection_and_transaction_ownership(db, decision):
    conn, action, path = db
    original = record_operator_decision(conn, action.action_id, decision)
    assert original.consumed is False
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE approvals SET consumed=1 WHERE action_id=? AND consumed=0", (str(action.action_id),))
    inspected = get_approval_by_action_id(conn, action.action_id)
    assert inspected == original.model_copy(update={"consumed": True})
    assert conn.in_transaction
    conn.rollback()
    assert get_approval_by_action_id(conn, action.action_id).consumed is False
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE approvals SET consumed=1 WHERE action_id=? AND consumed=0", (str(action.action_id),))
    conn.commit()
    reopened = sqlite3.connect(path)
    try:
        assert get_approval_by_action_id(reopened, action.action_id) == inspected
    finally:
        reopened.close()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE approvals SET consumed=0 WHERE action_id=?", (str(action.action_id),))
    conn.rollback()
    assert get_approval_by_action_id(conn, action.action_id).consumed is True


@pytest.mark.parametrize("value", [None, -1, 2, 0.5, "true", "false", b"0"])
def test_invalid_consumption_rejected_by_schema_and_reader(db, value):
    conn, action, _ = db
    record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE approvals SET consumed=?", (value,))
    conn.rollback()
    # Owner-level corruption bypass: retain independent read validation.
    if value is not None:  # NOT NULL is not disabled by ignore_check_constraints.
        conn.execute("DROP TRIGGER approvals_guard_update")
        conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute("UPDATE approvals SET consumed=?", (value,))
        with pytest.raises(ValueError, match="Invalid persisted"):
            get_approval_by_action_id(conn, action.action_id)


@pytest.mark.parametrize("value", [0, 1, "false", "true", None])
def test_model_consumption_requires_boolean(db, value):
    conn, action, _ = db
    approval = record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
    with pytest.raises(ValidationError):
        RestartApproval.model_validate({**approval.model_dump(), "consumed": value})


@pytest.mark.parametrize("field, value", [
    ("decision", "APPROVED"), ("approval_id", str(uuid4())),
    ("action_id", str(uuid4())), ("run_id", str(uuid4())),
    ("requester_id", "other"), ("agent_id", "other"),
    ("action_fingerprint", "0" * 64), ("approver_id", "other"),
    ("created_at", "2000-01-01T00:00:00Z"), ("expires_at", "2099-01-01T00:00:00Z"),
])
def test_immutable_metadata_update_blocked(db, field, value):
    conn, action, _ = db
    original = record_operator_decision(conn, action.action_id, ApprovalDecision.REJECTED)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"UPDATE approvals SET {field}=? WHERE action_id=?", (value, str(action.action_id)))
    conn.rollback()
    assert get_approval_by_action_id(conn, action.action_id) == original


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize("conflict", ["action_id", "approval_id"])
def test_delete_and_replace_blocked(db, recursive, conflict):
    conn, action, _ = db
    original = record_operator_decision(conn, action.action_id, ApprovalDecision.REJECTED)
    other = new_action(conn)
    conn.commit()
    conn.execute(f"PRAGMA recursive_triggers={recursive}")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM approvals WHERE action_id=?", (str(action.action_id),))
    conn.rollback()
    replacement = original.model_dump(mode="json")
    replacement.update(consumed=0, decision="APPROVED")
    if conflict == "action_id":
        replacement["approval_id"] = str(uuid4())
    else:
        replacement["action_id"] = str(other.action_id)
    columns = ", ".join(replacement)
    placeholders = ", ".join("?" for _ in replacement)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT OR REPLACE INTO approvals ({columns}) VALUES ({placeholders})", tuple(replacement.values()))
    conn.rollback()
    assert get_approval_by_action_id(conn, action.action_id) == original
    assert get_approval_by_action_id(conn, other.action_id) is None


def test_direct_insert_must_start_unconsumed(db):
    conn, action, _ = db
    approval = record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
    conn.rollback()
    fields = approval.model_dump(mode="json")
    fields["consumed"] = 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"INSERT INTO approvals ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
            tuple(fields.values()),
        )
    assert get_approval_by_action_id(conn, action.action_id) is None
