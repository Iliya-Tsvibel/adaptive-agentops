import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest

from adaptive_agentops import restart_gateway as gateway
from adaptive_agentops.actions import get_pending_restart_action, initialize_action_schema
from adaptive_agentops.approvals import initialize_approval_schema
from adaptive_agentops.audit import initialize_audit_schema
from adaptive_agentops.dispatch_reservations import initialize_dispatch_reservation_schema
from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.policy import PolicyDecision
from adaptive_agentops.tool_requests import EmptyArguments, MockRestartServiceProposal


Status = gateway.PendingRestartStatus


def proposal():
    return MockRestartServiceProposal(tool="mock.restart_service", resource="demo-api", arguments={})


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "gateway.db"
    with closing(sqlite3.connect(path)) as conn:
        for initialize in (initialize_action_schema, initialize_audit_schema,
                           initialize_approval_schema, initialize_dispatch_reservation_schema):
            initialize(conn)
        conn.commit()
        yield conn, path, create_local_run_context()
        assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM dispatch_reservations").fetchone()[0] == 0


@pytest.fixture(autouse=True)
def no_execution():
    with ExitStack() as stack:
        mocks = [stack.enter_context(patch(name, side_effect=AssertionError("Forbidden side effect"))) for name in (
            "adaptive_agentops.approvals.record_operator_decision",
            "adaptive_agentops.dispatch_control.reserve_approved_restart",
            "adaptive_agentops.execution_controller.execute_approved_mock_restart",
            "adaptive_agentops.mock_restart_adapter.request_demo_api_restart",
            "httpx.AsyncClient", "httpx.Client",
        )]
        yield
        for mock in mocks:
            mock.assert_not_called()


def counts(conn):
    return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                 for table in ("actions", "audit_events"))


def test_created_scope_fingerprint_and_correlated_evidence(db):
    conn, path, context = db
    result = gateway.create_audited_pending_restart(conn, context, proposal())
    assert result.status is Status.CREATED and result.action_id.version == 4
    with pytest.raises(FrozenInstanceError):
        result.action_id = uuid4()
    with closing(sqlite3.connect(path)) as reopened:
        assert counts(reopened) == (1, 1)
        action = get_pending_restart_action(reopened, result.action_id)
        assert action.run_id == context.run_id
        assert action.expires_at - action.created_at == timedelta(minutes=10)
        assert action.created_at.utcoffset() == timedelta(0)
        assert action.state == "PENDING" and action.arguments.model_dump() == {}
        scope = {"version": 1, "run_id": str(context.run_id), "requester_id": context.requester_id,
                 "agent_id": context.agent_id, "environment": context.environment,
                 "tool": "mock.restart_service", "resource": "demo-api", "arguments": {}}
        assert action.action_fingerprint == hashlib.sha256(
            json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        reopened.row_factory = sqlite3.Row
        event = dict(reopened.execute("SELECT * FROM audit_events").fetchone())
        assert event["event_kind"] == "POLICY_DECISION"
        assert event["policy_decision"] == "APPROVAL_REQUIRED"
        assert event["execution_status"] == "NOT_DISPATCHED"
        assert event["reason_code"] == "POLICY_APPROVAL_REQUIRED"
        assert event["action_id"] == str(action.action_id)
        assert event["run_id"] == str(context.run_id)
        for field in ("requester_id", "agent_id", "environment", "tool", "resource"):
            assert event[field] == getattr(action, field)
        assert event["approval_id"] is event["dispatch_id"] is None
        assert event["timestamp"].endswith("+00:00")


@pytest.mark.parametrize("field", ["requester_id", "agent_id", "environment", "resource"])
def test_valid_out_of_scope_denial_is_durable(db, field):
    conn, path, context = db
    request = proposal()
    if field == "resource":
        request = request.model_copy(update={field: "other"})
    else:
        context = replace(context, **{field: "other"})
    result = gateway.create_audited_pending_restart(conn, context, request)
    assert result.status is Status.NOT_CREATED and result.action_id is None
    with closing(sqlite3.connect(path)) as observer:
        assert counts(observer) == (0, 1)
        assert observer.execute("SELECT policy_decision, reason_code, action_id, approval_id, dispatch_id FROM audit_events").fetchone() == (
            "DENY", "POLICY_DENY", None, None, None,
        )


@pytest.mark.parametrize("decision", [PolicyDecision.ALLOW, "APPROVAL_REQUIRED", None, RuntimeError("SECRET_PDP")])
def test_unexpected_policy_never_creates(db, decision):
    conn, _, context = db
    behavior = {"side_effect": decision} if isinstance(decision, Exception) else {"return_value": decision}
    with patch.object(gateway, "evaluate_policy", **behavior) as pdp:
        result = gateway.create_audited_pending_restart(conn, context, proposal())
    pdp.assert_called_once()
    assert result.status is Status.NOT_CREATED and result.action_id is None
    assert "SECRET" not in repr(result)
    assert counts(conn) == (0, 0)


def test_action_primitive_policy_gate_is_preserved(db):
    conn, _, context = db
    with patch("adaptive_agentops.actions.evaluate_policy", return_value=PolicyDecision.DENY) as inner:
        assert gateway.create_audited_pending_restart(conn, context, proposal()).status is Status.NOT_CREATED
    inner.assert_called_once()
    assert counts(conn) == (0, 0)


@pytest.mark.parametrize("field", ["action_id", "approval_id", "approver_id", "role", "credentials"])
def test_injected_proposal_fields_rejected(db, field):
    conn, _, context = db
    request = proposal().model_copy(update={field: "SECRET"})
    result = gateway.create_audited_pending_restart(conn, context, request)
    assert result.status is Status.NOT_CREATED and result.action_id is None
    assert counts(conn) == (0, 0)


@pytest.mark.parametrize("bad_proposal", [
    {}, None,
    MockRestartServiceProposal.model_construct(tool="wrong", resource="demo-api", arguments=EmptyArguments()),
    MockRestartServiceProposal.model_construct(tool="mock.restart_service", resource=123, arguments=EmptyArguments()),
    proposal().model_copy(update={"arguments": {}}),
    proposal().model_copy(update={"arguments": EmptyArguments().model_copy(update={"force": True})}),
])
def test_malformed_requests_rejected(db, bad_proposal):
    conn, _, context = db
    assert gateway.create_audited_pending_restart(conn, context, bad_proposal).status is Status.NOT_CREATED
    assert counts(conn) == (0, 0)


@pytest.mark.parametrize("kind", ["dict", "extra", "run", "identity_type"])
def test_malformed_context_rejected(db, kind):
    conn, _, context = db
    if kind == "dict":
        context = vars(context)
    elif kind == "extra":
        object.__setattr__(context, "approver_id", "SECRET")
    else:
        context = replace(context, **({"run_id": "forged"} if kind == "run" else {"requester_id": 12}))
    assert gateway.create_audited_pending_restart(conn, context, proposal()).status is Status.NOT_CREATED
    assert counts(conn) == (0, 0)


@pytest.mark.parametrize("table", ["actions", "audit_events"])
def test_insert_failure_rolls_back_both(db, table):
    conn, _, context = db
    conn.execute(f"CREATE TRIGGER fail_insert BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'SECRET'); END")
    result = gateway.create_audited_pending_restart(conn, context, proposal())
    assert result.status is Status.NOT_CREATED and result.action_id is None
    assert counts(conn) == (0, 0) and not conn.in_transaction


class FaultConnection(sqlite3.Connection):
    failure = None
    commits = 0
    rollbacks = 0
    closes = 0
    observer = None

    def commit(self):
        self.commits += 1
        assert counts(self) == (1, 1)
        assert counts(self.observer) == (0, 0)
        if self.failure in ("commit", "rollback", "close"):
            raise sqlite3.OperationalError("SECRET_COMMIT")
        super().commit()
        if self.failure == "ambiguous":
            raise sqlite3.OperationalError("SECRET_AFTER_COMMIT")

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


@pytest.mark.parametrize("failure", [None, "commit", "ambiguous", "rollback", "close"])
def test_commit_visibility_and_failure_semantics(db, failure):
    observer, path, context = db
    conn = sqlite3.connect(path, factory=FaultConnection)
    conn.observer, conn.failure = observer, failure
    try:
        result = gateway.create_audited_pending_restart(conn, context, proposal())
        assert result.status is (Status.CREATED if failure is None else Status.UNCERTAIN)
        assert (result.action_id is not None) is (failure is None)
        assert "SECRET" not in repr(result)
        assert conn.commits == 1 and conn.rollbacks == int(failure is not None)
        assert counts(observer) == ((1, 1) if failure in (None, "ambiguous") else (0, 0))
        if failure == "rollback":
            assert conn.closes == 1
            with pytest.raises(sqlite3.ProgrammingError):
                sqlite3.Connection.commit(conn)
        elif failure == "close":
            assert conn.closes == 1 and conn.in_transaction
            # No claim of isolation when close also fails. Discard, never commit.
    finally:
        sqlite3.Connection.close(conn)


def test_rollback_failure_after_partial_insert_is_uncertain(db):
    observer, path, context = db
    observer.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events BEGIN SELECT RAISE(ABORT, 'SECRET'); END")
    conn = sqlite3.connect(path, factory=FaultConnection)
    conn.failure = "rollback"
    try:
        result = gateway.create_audited_pending_restart(conn, context, proposal())
        assert result.status is Status.UNCERTAIN and result.action_id is None
        assert conn.commits == 0 and conn.rollbacks == conn.closes == 1
        assert counts(observer) == (0, 0)
        with pytest.raises(sqlite3.ProgrammingError):
            sqlite3.Connection.commit(conn)
    finally:
        sqlite3.Connection.close(conn)


def test_caller_transaction_untouched(db):
    observer, path, context = db
    with closing(sqlite3.connect(path, factory=FaultConnection)) as conn:
        conn.execute("BEGIN")
        result = gateway.create_audited_pending_restart(conn, context, proposal())
        assert result.status is Status.NOT_CREATED and result.action_id is None
        assert conn.in_transaction and conn.commits == conn.rollbacks == conn.closes == 0
        assert counts(observer) == (0, 0)


def test_concurrent_requests_get_independent_ids(db):
    conn, path, context = db
    ready = Barrier(2)
    def create():
        with closing(sqlite3.connect(path, timeout=5)) as own:
            ready.wait(timeout=5)
            return gateway.create_audited_pending_restart(own, context, proposal())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert all(result.status is Status.CREATED for result in results)
    assert results[0].action_id != results[1].action_id
    assert counts(conn) == (2, 2)
    assert {row[0] for row in conn.execute("SELECT action_id FROM audit_events")} == {str(r.action_id) for r in results}
