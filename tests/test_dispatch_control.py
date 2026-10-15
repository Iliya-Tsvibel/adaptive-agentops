import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from threading import Event
from unittest.mock import patch
from uuid import uuid1, uuid4

import pytest

from adaptive_agentops.actions import initialize_action_schema, insert_pending_restart_action
from adaptive_agentops.approvals import (
    ApprovalDecision, initialize_approval_schema, record_operator_decision,
)
from adaptive_agentops.audit import initialize_audit_schema
from adaptive_agentops.dispatch_control import ReservationStatus, reserve_approved_restart
from adaptive_agentops.dispatch_reservations import initialize_dispatch_reservation_schema
from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import MockRestartServiceProposal


def create_approved(conn, context, decision=ApprovalDecision.APPROVED):
    action = insert_pending_restart_action(conn, context, MockRestartServiceProposal(
        tool="mock.restart_service", resource="demo-api", arguments={}
    ))
    conn.commit()
    approval = None
    if decision is not None:
        approval = record_operator_decision(conn, action.action_id, decision)
        conn.commit()
    return action, approval


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path, timeout=0)
    for initialize in (
        initialize_action_schema, initialize_approval_schema,
        initialize_dispatch_reservation_schema, initialize_audit_schema,
    ):
        initialize(conn)
    context = create_local_run_context()
    action, approval = create_approved(conn, context)
    try:
        yield conn, path, context, action, approval
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def no_adapter():
    with patch("adaptive_agentops.mock_adapter.get_demo_api_health") as adapter, patch(
        "adaptive_agentops.gateway.get_demo_api_health"
    ) as gateway_adapter:
        yield
        adapter.assert_not_called()
        gateway_adapter.assert_not_called()


def state(conn):
    return (
        conn.execute("SELECT SUM(consumed) FROM approvals").fetchone()[0] or 0,
        conn.execute("SELECT COUNT(*) FROM dispatch_reservations").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
    )


def test_confirmed_reservation_correlates_all_records(db):
    conn, path, context, action, approval = db
    original_action = conn.execute("SELECT * FROM actions").fetchall()
    with patch("adaptive_agentops.dispatch_control.evaluate_policy", wraps=evaluate_policy) as pdp:
        result = reserve_approved_restart(conn, context, action.action_id)
    pdp.assert_called_once()
    assert pdp.call_args.args[0] == context
    assert pdp.call_args.args[1].model_dump() == {
        "tool": "mock.restart_service", "resource": "demo-api", "arguments": {}
    }
    assert result.status is ReservationStatus.RESERVED
    assert result.reservation.action_id == action.action_id
    assert result.reservation.approval_id == approval.approval_id
    assert result.reservation.dispatch_id.version == 4
    assert result.reservation.action_fingerprint == action.action_fingerprint
    with pytest.raises(FrozenInstanceError):
        result.reservation = None
    assert not conn.in_transaction
    reopened = sqlite3.connect(path)
    reopened.row_factory = sqlite3.Row
    try:
        assert state(reopened) == (1, 1, 1)
        audit = dict(reopened.execute("SELECT * FROM audit_events").fetchone())
        assert audit["event_kind"] == "DISPATCH_RESERVED"
        assert audit["reason_code"] == "MOCK_RESTART_RESERVED"
        assert audit["policy_decision"] == "APPROVAL_REQUIRED"
        assert audit["execution_status"] == "NOT_DISPATCHED"
        for field in ("action_id", "approval_id", "dispatch_id", "run_id"):
            assert audit[field] == str(getattr(result.reservation, field))
        for field in ("requester_id", "agent_id", "environment"):
            assert audit[field] == getattr(context, field)
        assert audit["tool"] == action.tool
        assert audit["resource"] == action.resource
        assert audit["timestamp"].endswith("+00:00")
        assert "SECRET" not in repr(audit)
    finally:
        reopened.close()
    assert conn.execute("SELECT * FROM actions").fetchall() == original_action
    repeated = reserve_approved_restart(conn, context, action.action_id)
    assert repeated.status is ReservationStatus.NOT_RESERVED
    assert repeated.reservation is None
    assert state(conn) == (1, 1, 1)


@pytest.mark.parametrize("decision", [ApprovalDecision.REJECTED, None])
def test_rejected_or_missing_approval(db, decision):
    conn, _, context, _, _ = db
    action, _ = create_approved(conn, context, decision)
    result = reserve_approved_restart(conn, context, action.action_id)
    assert result.status is ReservationStatus.NOT_RESERVED
    assert state(conn) == (0, 0, 0)


@pytest.mark.parametrize("field, value", [
    ("run_id", uuid4()), ("run_id", "forged"), ("requester_id", "SECRET_REQUESTER"),
    ("agent_id", "other"), ("environment", "production"),
])
def test_context_binding(db, field, value):
    conn, _, context, action, _ = db
    result = reserve_approved_restart(conn, replace(context, **{field: value}), action.action_id)
    assert result.status is ReservationStatus.NOT_RESERVED
    assert "SECRET" not in repr(result)
    assert state(conn) == (0, 0, 0)


@pytest.mark.parametrize("identifier", [None, "bad", uuid1(), uuid4()])
def test_invalid_or_missing_action(db, identifier):
    conn, _, context, _, _ = db
    assert reserve_approved_restart(conn, context, identifier).status is ReservationStatus.NOT_RESERVED
    assert state(conn) == (0, 0, 0)


def test_invalid_context(db):
    conn, _, _, action, _ = db
    assert reserve_approved_restart(conn, {}, action.action_id).status is ReservationStatus.NOT_RESERVED
    assert state(conn) == (0, 0, 0)


@pytest.mark.parametrize("offset", [0, 1])
def test_exact_expiry_and_expired_rejected(db, offset):
    conn, _, context, action, _ = db
    with patch("adaptive_agentops.dispatch_control.datetime") as clock:
        clock.now.return_value = action.expires_at + timedelta(seconds=offset)
        result = reserve_approved_restart(conn, context, action.action_id)
    assert result.status is ReservationStatus.NOT_RESERVED
    assert state(conn) == (0, 0, 0)


def test_expiry_after_policy_is_rechecked(db):
    conn, _, context, action, _ = db
    with patch("adaptive_agentops.dispatch_control.datetime") as clock:
        clock.now.return_value = action.created_at + timedelta(seconds=1)
        def slow_pdp(ctx, proposal):
            clock.now.return_value = action.expires_at
            return PolicyDecision.APPROVAL_REQUIRED
        with patch("adaptive_agentops.dispatch_control.evaluate_policy", side_effect=slow_pdp):
            assert reserve_approved_restart(conn, context, action.action_id).status is ReservationStatus.NOT_RESERVED
    assert state(conn) == (0, 0, 0)


def test_consumed_without_reservation_rejected(db):
    conn, _, context, action, _ = db
    conn.execute("UPDATE approvals SET consumed=1")
    conn.commit()
    assert reserve_approved_restart(conn, context, action.action_id).status is ReservationStatus.NOT_RESERVED
    assert state(conn) == (1, 0, 0)


def test_identical_fingerprints_do_not_share_approval_rights(db):
    conn, _, context, first, _ = db
    second, _ = create_approved(conn, context)
    assert first.action_fingerprint == second.action_fingerprint
    one = reserve_approved_restart(conn, context, first.action_id)
    two = reserve_approved_restart(conn, context, second.action_id)
    assert one.status is two.status is ReservationStatus.RESERVED
    assert one.reservation.dispatch_id != two.reservation.dispatch_id
    assert one.reservation.approval_id != two.reservation.approval_id
    assert state(conn) == (2, 2, 2)


@pytest.mark.parametrize("behavior", [
    {"return_value": PolicyDecision.DENY}, {"return_value": PolicyDecision.ALLOW},
    {"return_value": "APPROVAL_REQUIRED"}, {"return_value": None},
    {"side_effect": RuntimeError("SECRET_PDP")},
])
def test_fresh_pdp_failures(db, behavior):
    conn, _, context, action, _ = db
    with patch("adaptive_agentops.dispatch_control.evaluate_policy", **behavior) as pdp:
        result = reserve_approved_restart(conn, context, action.action_id)
    pdp.assert_called_once()
    assert result.status is ReservationStatus.NOT_RESERVED
    assert result.reservation is None
    assert "SECRET" not in repr(result)
    assert state(conn) == (0, 0, 0)


@pytest.mark.parametrize("table, field, value", [
    ("actions", "resource", "SECRET_RESOURCE"),
    ("actions", "arguments", '{"force":true}'),
    ("actions", "state", "EXECUTED"),
    ("actions", "action_fingerprint", "0" * 64),
    ("approvals", "run_id", str(uuid4())),
    ("approvals", "action_id", str(uuid4())),
    ("approvals", "action_fingerprint", "0" * 64),
    ("approvals", "approver_id", "SECRET_APPROVER"),
    ("approvals", "expires_at", "2000-01-01T00:00:00Z"),
    ("approvals", "created_at", "bad"),
])
def test_corrupted_storage_fails_closed(db, table, field, value):
    conn, _, context, action, _ = db
    conn.execute("DROP TRIGGER approvals_guard_update")
    conn.execute(f"UPDATE {table} SET {field}=?", (value,))
    conn.commit()
    result = reserve_approved_restart(conn, context, action.action_id)
    assert result.status is ReservationStatus.NOT_RESERVED
    assert "SECRET" not in repr(result)
    assert state(conn) == (0, 0, 0)


@pytest.mark.parametrize("phase", ["update", "reservation", "audit", "zero_rows"])
def test_each_write_failure_rolls_back_everything(db, phase):
    conn, _, context, action, _ = db
    table = {"update": "approvals", "zero_rows": "approvals",
             "reservation": "dispatch_reservations", "audit": "audit_events"}[phase]
    operation = "UPDATE" if table == "approvals" else "INSERT"
    failure = "RAISE(IGNORE)" if phase == "zero_rows" else "RAISE(ABORT, 'SECRET_INSERT')"
    conn.execute(f"CREATE TRIGGER fail_write BEFORE {operation} ON {table} BEGIN SELECT {failure}; END")
    conn.commit()
    result = reserve_approved_restart(conn, context, action.action_id)
    assert result.status is ReservationStatus.NOT_RESERVED
    assert result.reservation is None
    assert "SECRET" not in repr(result)
    assert not conn.in_transaction
    assert state(conn) == (0, 0, 0)


class ObservedConnection(sqlite3.Connection):
    commits = 0
    rollbacks = 0
    begins = 0
    closes = 0
    failure = None
    before_commit = None

    def execute(self, sql, parameters=()):
        if sql == "BEGIN IMMEDIATE":
            self.begins += 1
            if self.failure == "begin":
                raise sqlite3.OperationalError("SECRET_BEGIN")
        return super().execute(sql, parameters)

    def commit(self):
        self.commits += 1
        if self.before_commit:
            self.before_commit()
        if self.failure in ("commit", "rollback"):
            raise sqlite3.OperationalError("SECRET_COMMIT")
        super().commit()
        if self.failure == "ambiguous":
            raise sqlite3.OperationalError("SECRET_AFTER_COMMIT")

    def rollback(self):
        self.rollbacks += 1
        if self.failure == "rollback":
            raise sqlite3.OperationalError("SECRET_ROLLBACK")
        super().rollback()

    def close(self):
        self.closes += 1
        super().close()


@pytest.mark.parametrize("failure", [None, "begin", "commit", "ambiguous", "rollback"])
def test_commit_visibility_and_uncertainty_without_retry(db, failure):
    observer, path, context, action, _ = db
    conn = sqlite3.connect(path, factory=ObservedConnection)
    conn.failure = failure
    def check_visibility():
        assert state(conn) == (1, 1, 1)
        assert state(observer) == (0, 0, 0)
    conn.before_commit = check_visibility
    try:
        result = reserve_approved_restart(conn, context, action.action_id)
        expected = ReservationStatus.RESERVED if failure is None else (
            ReservationStatus.NOT_RESERVED if failure == "begin" else ReservationStatus.UNCERTAIN
        )
        assert result.status is expected
        if failure:
            assert result.reservation is None
        assert "SECRET" not in repr(result)
        assert conn.begins == 1
        assert conn.commits == (0 if failure == "begin" else 1)
        assert conn.rollbacks == (1 if failure in ("commit", "ambiguous", "rollback") else 0)
        assert conn.closes == (1 if failure == "rollback" else 0)
        if failure == "rollback":
            with pytest.raises(sqlite3.ProgrammingError):
                sqlite3.Connection.commit(conn)
        else:
            assert not conn.in_transaction
    finally:
        conn.close()
    assert state(observer) == ((1, 1, 1) if failure in (None, "ambiguous") else (0, 0, 0))


def test_caller_transaction_untouched(db):
    _, path, context, action, _ = db
    conn = sqlite3.connect(path, factory=ObservedConnection)
    try:
        conn.execute("BEGIN")
        conn.execute("UPDATE actions SET resource='caller-marker'")
        result = reserve_approved_restart(conn, context, action.action_id)
        assert result.status is ReservationStatus.NOT_RESERVED
        assert conn.in_transaction
        assert conn.commits == conn.rollbacks == conn.begins == conn.closes == 0
        assert conn.execute("SELECT resource FROM actions").fetchone()[0] == "caller-marker"
    finally:
        conn.close()


def test_two_connections_compete_with_deterministic_coordination(db):
    conn, path, context, action, _ = db
    locked, release = Event(), Event()
    def held_pdp(ctx, proposal):
        locked.set()
        assert release.wait(5)
        return evaluate_policy(ctx, proposal)
    def winner():
        owned = sqlite3.connect(path, timeout=0)
        try:
            return reserve_approved_restart(owned, context, action.action_id)
        finally:
            owned.close()
    with patch("adaptive_agentops.dispatch_control.evaluate_policy", side_effect=held_pdp):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(winner)
            try:
                assert locked.wait(5)
                loser = reserve_approved_restart(conn, context, action.action_id)
                assert loser.status is ReservationStatus.NOT_RESERVED
            finally:
                release.set()
            assert pending.result(timeout=5).status is ReservationStatus.RESERVED
    assert state(conn) == (1, 1, 1)
    assert reserve_approved_restart(conn, context, action.action_id).status is ReservationStatus.NOT_RESERVED


def test_existing_reservation_is_not_retry_permission(db):
    conn, _, context, action, approval = db
    conn.execute("""
        INSERT INTO dispatch_reservations VALUES (?, ?, ?, ?, ?, ?, 'RESERVED')
    """, (str(uuid4()), str(action.action_id), str(approval.approval_id), str(action.run_id),
          action.action_fingerprint, action.created_at.isoformat()))
    conn.commit()
    assert reserve_approved_restart(conn, context, action.action_id).status is ReservationStatus.NOT_RESERVED
    assert state(conn) == (0, 1, 0)


def test_no_caller_selected_scope_or_identifiers(db):
    conn, _, context, action, _ = db
    for field in ("approval_id", "dispatch_id", "tool", "resource", "arguments", "credentials"):
        with pytest.raises(TypeError):
            reserve_approved_restart(conn, context, action.action_id, **{field: "SECRET"})
    assert state(conn) == (0, 0, 0)


class AuditRollbackFailingConnection(ObservedConnection):
    audit_attempts = 0
    fail_close = False
    partial_state = None

    def execute(self, sql, parameters=()):
        if "INSERT INTO audit_events" in sql:
            self.audit_attempts += 1
            self.partial_state = state(self)
        return super().execute(sql, parameters)

    def rollback(self):
        self.rollbacks += 1
        raise sqlite3.OperationalError("SECRET_ROLLBACK_FAILURE")

    def close(self):
        if self.fail_close:
            self.closes += 1
            raise sqlite3.OperationalError("SECRET_CLOSE_FAILURE")
        super().close()


@pytest.mark.parametrize("fail_close", [False, True])
def test_audit_insert_then_rollback_failure_is_uncertain(db, fail_close):
    observer, path, context, action, _ = db
    observer.execute("""
        CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events
        BEGIN SELECT RAISE(ABORT, 'SECRET_AUDIT_FAILURE'); END
    """)
    observer.commit()
    conn = sqlite3.connect(path, factory=AuditRollbackFailingConnection)
    conn.fail_close = fail_close
    try:
        result = reserve_approved_restart(conn, context, action.action_id)
        assert conn.partial_state == (1, 1, 0)
        assert result.status is ReservationStatus.UNCERTAIN
        assert result.reservation is None
        assert "SECRET" not in repr(result)
        assert conn.begins == conn.audit_attempts == conn.rollbacks == conn.closes == 1
        assert conn.commits == 0
        if fail_close:
            # Closing failed: the API cannot promise isolation. Caller must
            # discard this connection; do not commit the unresolved writes.
            assert conn.in_transaction
            assert state(conn) == (1, 1, 0)
        else:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.commit()
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
        fresh = sqlite3.connect(path)
        try:
            assert state(fresh) == (0, 0, 0)
        finally:
            fresh.close()
    finally:
        # Test cleanup bypasses the deliberately failing close override.
        sqlite3.Connection.close(conn)
    assert state(observer) == (0, 0, 0)
