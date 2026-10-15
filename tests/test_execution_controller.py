import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from threading import Event
from unittest.mock import AsyncMock, patch

import pytest

from adaptive_agentops import execution_controller as controller
from adaptive_agentops.actions import initialize_action_schema, insert_pending_restart_action
from adaptive_agentops.approvals import (
    ApprovalDecision, initialize_approval_schema, record_operator_decision,
)
from adaptive_agentops.audit import initialize_audit_schema
from adaptive_agentops.dispatch_control import reserve_approved_restart
from adaptive_agentops.dispatch_reservations import initialize_dispatch_reservation_schema
from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.mock_restart_adapter import (
    MockRestartAcknowledgement, MockRestartResult, RestartReason, RestartStatus,
)
from adaptive_agentops.policy import PolicyDecision
from adaptive_agentops.tool_requests import MockRestartServiceProposal


Status = controller.MockRestartExecutionStatus


def acknowledged():
    return MockRestartResult(
        RestartStatus.ACKNOWLEDGED, RestartReason.MOCK_ACKNOWLEDGED,
        MockRestartAcknowledgement(service="demo-api", status="restart_requested", simulated=True),
    )


@pytest.fixture(autouse=True)
def adapter(monkeypatch):
    mock = AsyncMock(return_value=acknowledged())
    monkeypatch.setattr(controller, "request_demo_api_restart", mock)
    # Catch accidental use of the real HTTP boundary by any other path.
    with patch("httpx.AsyncClient", side_effect=AssertionError("Network forbidden")):
        yield mock


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "execution.db"
    conn = sqlite3.connect(path, timeout=0)
    for initialize in (initialize_action_schema, initialize_approval_schema,
                       initialize_dispatch_reservation_schema, initialize_audit_schema):
        initialize(conn)
    context = create_local_run_context()
    action = insert_pending_restart_action(conn, context, MockRestartServiceProposal(
        tool="mock.restart_service", resource="demo-api", arguments={},
    ))
    conn.commit()
    yield conn, path, context, action
    conn.close()


def approve(db, decision=ApprovalDecision.APPROVED):
    conn, _, _, action = db
    approval = record_operator_decision(conn, action.action_id, decision)
    conn.commit()
    return approval


def invoke(db, conn=None):
    original, _, context, action = db
    return asyncio.run(controller.execute_approved_mock_restart(
        original if conn is None else conn, context, action.action_id,
    ))


def snapshot(conn):
    return (
        conn.execute("SELECT COALESCE(SUM(consumed),0) FROM approvals").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM dispatch_reservations").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
    )


def test_success_ordering_correlation_and_replay(db, adapter):
    conn, path, context, action = db
    approval = approve(db)
    async def attempt():
        assert not conn.in_transaction
        with sqlite3.connect(path) as observer:
            assert snapshot(observer) == (1, 1, 1)
            # A separate writer can acquire the lock during HTTP I/O.
            observer.execute("BEGIN IMMEDIATE")
            observer.rollback()
        return acknowledged()
    adapter.side_effect = attempt
    with patch.object(controller, "reserve_approved_restart", wraps=reserve_approved_restart) as reserve:
        result = invoke(db)
        reserve.assert_called_once_with(conn, context, action.action_id)
    assert result.status is Status.ACKNOWLEDGED
    with pytest.raises(FrozenInstanceError):
        result.status = Status.NOT_DISPATCHED
    with sqlite3.connect(path) as observer:
        observer.row_factory = sqlite3.Row
        assert snapshot(observer) == (1, 1, 2)
        rows = observer.execute("SELECT * FROM audit_events ORDER BY rowid").fetchall()
        assert [row["event_kind"] for row in rows] == ["DISPATCH_RESERVED", "RESULT"]
        event = rows[1]
        assert event["execution_status"] == "ACKNOWLEDGED"
        assert event["reason_code"] == "MOCK_RESTART_ACKNOWLEDGED"
        assert event["action_id"] == str(action.action_id)
        assert event["approval_id"] == str(approval.approval_id)
        assert event["run_id"] == str(context.run_id)
        assert event["dispatch_id"] == rows[0]["dispatch_id"]
        for field in ("requester_id", "agent_id", "environment"):
            assert event[field] == getattr(context, field)
        assert event["tool"] == "mock.restart_service"
        assert event["resource"] == "demo-api"
    assert invoke(db).status is Status.NOT_DISPATCHED
    adapter.assert_awaited_once_with()


@pytest.mark.parametrize("kind", ["missing", "rejected", "expired", "consumed", "denied"])
def test_no_post_without_eligible_approval(db, adapter, kind):
    conn, _, _, action = db
    if kind != "missing":
        approve(db, ApprovalDecision.REJECTED if kind == "rejected" else ApprovalDecision.APPROVED)
    if kind == "consumed":
        conn.execute("UPDATE approvals SET consumed=1")
        conn.commit()
    with patch("adaptive_agentops.dispatch_control.datetime") as clock, patch(
        "adaptive_agentops.dispatch_control.evaluate_policy",
        return_value=PolicyDecision.DENY if kind == "denied" else PolicyDecision.APPROVAL_REQUIRED,
    ):
        clock.now.return_value = action.expires_at if kind == "expired" else action.created_at
        # Approval timestamps follow action creation; use a valid time otherwise.
        if kind != "expired":
            clock.now.return_value = datetime.now(timezone.utc)
        assert invoke(db).status is Status.NOT_DISPATCHED
    adapter.assert_not_called()
    assert snapshot(conn)[1:] == (0, 0)


@pytest.mark.parametrize("outcome", [
    MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.TIMEOUT),
    RuntimeError("SECRET_ADAPTER"),
])
def test_uncertain_and_exception_are_audited_once(db, adapter, outcome):
    approve(db)
    if isinstance(outcome, Exception):
        adapter.side_effect = outcome
    else:
        adapter.return_value = outcome
    result = invoke(db)
    assert result.status is Status.UNCERTAIN
    row = db[0].execute("SELECT execution_status, reason_code FROM audit_events WHERE event_kind='RESULT'").fetchone()
    assert row == ("UNCERTAIN", "MOCK_RESTART_UNCERTAIN")
    assert "SECRET" not in repr(result) + repr(db[0].execute("SELECT * FROM audit_events").fetchall())
    assert invoke(db).status is Status.NOT_DISPATCHED
    adapter.assert_awaited_once()


class FaultConnection(sqlite3.Connection):
    failure = None
    commits = 0
    rollbacks = 0
    closes = 0

    def commit(self):
        self.commits += 1
        if self.failure in ("commit", "rollback", "close"):
            raise sqlite3.OperationalError("SECRET_COMMIT")
        super().commit()
        if self.failure == "ambiguous":
            raise sqlite3.OperationalError("SECRET_AMBIGUOUS")

    def rollback(self):
        self.rollbacks += 1
        if self.failure in ("rollback", "close"):
            raise sqlite3.OperationalError("SECRET_ROLLBACK")
        super().rollback()

    def close(self):
        self.closes += 1
        if self.failure == "close":
            raise sqlite3.OperationalError("SECRET_CLOSE")
        super().close()


@pytest.mark.parametrize("failure", ["insert", "commit", "ambiguous", "rollback", "close"])
def test_result_audit_failure_never_replays(db, adapter, failure):
    approve(db)
    observer, path, _, _ = db
    conn = sqlite3.connect(path, factory=FaultConnection)
    if failure == "insert":
        observer.execute("""CREATE TRIGGER fail_result BEFORE INSERT ON audit_events
            WHEN NEW.event_kind='RESULT' BEGIN SELECT RAISE(ABORT, 'SECRET_INSERT'); END""")
        observer.commit()
    async def attempt():
        conn.failure = failure
        return acknowledged()
    adapter.side_effect = attempt
    try:
        result = invoke(db, conn)
        assert result.status is Status.AUDIT_UNCONFIRMED
        assert "SECRET" not in repr(result)
        assert conn.commits == (1 if failure == "insert" else 2)
        assert conn.rollbacks == 1
        assert snapshot(observer) == (1, 1, 2 if failure == "ambiguous" else 1)
        if failure == "rollback":
            assert conn.closes == 1
            with pytest.raises(sqlite3.ProgrammingError):
                sqlite3.Connection.commit(conn)
        if failure == "close":
            assert conn.closes == 1
            assert conn.in_transaction  # Explicit limitation: close did not isolate.
    finally:
        sqlite3.Connection.close(conn)
    assert invoke(db).status is Status.NOT_DISPATCHED
    adapter.assert_awaited_once()


@pytest.mark.parametrize("failure", ["insert", "ambiguous"])
def test_reservation_failure_prevents_adapter(db, adapter, failure):
    approve(db)
    observer, path, _, _ = db
    conn = sqlite3.connect(path, factory=FaultConnection)
    if failure == "insert":
        observer.execute("""CREATE TRIGGER fail_reservation BEFORE INSERT ON dispatch_reservations
            BEGIN SELECT RAISE(ABORT, 'SECRET'); END""")
        observer.commit()
    else:
        conn.failure = "ambiguous"
    try:
        result = invoke(db, conn)
        assert result.status is (Status.NOT_DISPATCHED if failure == "insert" else Status.RESERVATION_UNCERTAIN)
        assert snapshot(observer) == ((0, 0, 0) if failure == "insert" else (1, 1, 1))
        adapter.assert_not_called()
    finally:
        conn.close()


@pytest.mark.parametrize("during_http", [False, True])
def test_caller_transaction_is_untouched(db, adapter, during_http):
    approve(db)
    _, path, _, _ = db
    conn = sqlite3.connect(path, factory=FaultConnection)
    async def attempt():
        conn.execute("BEGIN")
        return acknowledged()
    if during_http:
        adapter.side_effect = attempt
    else:
        conn.execute("BEGIN")
    try:
        result = invoke(db, conn)
        assert result.status is (Status.AUDIT_UNCONFIRMED if during_http else Status.NOT_DISPATCHED)
        assert conn.in_transaction
        assert conn.rollbacks == conn.closes == 0
        assert conn.commits == int(during_http)
        assert adapter.await_count == int(during_http)
    finally:
        conn.close()


def test_cancellation_leaves_spent_reservation_without_result(db, adapter):
    approve(db)
    adapter.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        invoke(db)
    assert snapshot(db[0]) == (1, 1, 1)
    assert not db[0].in_transaction
    assert invoke(db).status is Status.NOT_DISPATCHED
    adapter.assert_awaited_once()


def test_crash_before_adapter_cannot_resume_stored_reservation(db, adapter):
    approve(db)
    conn, _, context, action = db
    reserve_approved_restart(conn, context, action.action_id)
    assert invoke(db).status is Status.NOT_DISPATCHED
    assert snapshot(conn) == (1, 1, 1)
    adapter.assert_not_called()


def test_concurrent_calls_attempt_once(db, adapter):
    approve(db)
    _, path, _, _ = db
    entered, release = Event(), Event()
    async def attempt():
        entered.set()
        assert release.wait(5)
        return acknowledged()
    adapter.side_effect = attempt
    def winner():
        conn = sqlite3.connect(path, timeout=0)
        try:
            return invoke(db, conn)
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(winner)
        try:
            assert entered.wait(5)
            assert invoke(db).status is Status.NOT_DISPATCHED
        finally:
            release.set()
        assert pending.result(timeout=5).status is Status.ACKNOWLEDGED
    adapter.assert_awaited_once()
    assert snapshot(db[0]) == (1, 1, 2)


@pytest.mark.parametrize("invalid", [
    {"secret": "SECRET_BODY"},
    MockRestartResult(RestartStatus.ACKNOWLEDGED, RestartReason.TIMEOUT),
])
def test_invalid_adapter_results_never_claim_success(db, adapter, invalid):
    approve(db)
    adapter.return_value = invalid
    result = invoke(db)
    assert result.status is Status.AUDIT_UNCONFIRMED
    assert snapshot(db[0]) == (1, 1, 1)
    assert "SECRET" not in repr(result)
    adapter.assert_awaited_once()


@pytest.mark.parametrize("bad_context", [False, True])
def test_invalid_inputs_do_not_dispatch(db, adapter, bad_context):
    approve(db)
    conn, _, context, action = db
    result = asyncio.run(controller.execute_approved_mock_restart(
        conn, {} if bad_context else context,
        action.action_id if bad_context else "SECRET_INVALID_ID",
    ))
    assert result.status is Status.NOT_DISPATCHED
    assert snapshot(conn) == (0, 0, 0)
    adapter.assert_not_called()
