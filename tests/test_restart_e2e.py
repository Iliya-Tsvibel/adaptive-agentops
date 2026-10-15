"""Real loopback integration, requiring exclusive port 8001 (no parallel runs).

Only this tested workflow routes execution through the controller. The mock API
is unauthenticated: direct POST remains possible and is not policy enforcement.
Trusted RunContext and mocked terminal interaction do not prove authentication
of either a workload or a human. Acknowledgement proves simulation only.
"""

import asyncio
from contextlib import asynccontextmanager, closing
import json
import socket
import sqlite3

import httpx
import pytest
import uvicorn

from adaptive_agentops import operator_cli
from adaptive_agentops.actions import get_pending_restart_action, initialize_action_schema
from adaptive_agentops.approvals import get_approval_by_action_id, initialize_approval_schema
from adaptive_agentops.audit import initialize_audit_schema
from adaptive_agentops.dispatch_reservations import (
    get_dispatch_reservation_by_action_id, initialize_dispatch_reservation_schema,
)
from adaptive_agentops.execution_controller import (
    MockRestartExecutionStatus as Status, execute_approved_mock_restart,
)
from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.mock_api import app
from adaptive_agentops.restart_gateway import PendingRestartStatus, create_audited_pending_restart
from adaptive_agentops.tool_requests import MockRestartServiceProposal


ADDRESS = ("127.0.0.1", 8001)
HEALTH = {"service": "demo-api", "status": "unhealthy", "reason": "stalled_worker"}


def bind_owned_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Allows sequential test startup after TIME_WAIT, not shared listeners.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(ADDRESS)
        sock.listen(16)
        sock.setblocking(False)
        return sock
    except OSError:
        sock.close()
        raise RuntimeError("Cannot own 127.0.0.1:8001; no HTTP requests sent") from None


@asynccontextmanager
async def owned_server(observed_app):
    sock = bind_owned_socket()  # Never probe or send POST to a pre-existing listener.
    server = uvicorn.Server(uvicorn.Config(
        observed_app, host=ADDRESS[0], port=ADDRESS[1], loop="asyncio",
        http="h11", lifespan="off", access_log=False, log_level="error",
        timeout_graceful_shutdown=2,
    ))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(3):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Owned mock server stopped before readiness")
                await asyncio.sleep(0.01)
        assert not task.done() and sock.getsockname() == ADDRESS
        yield
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise AssertionError("Owned mock server did not stop within deadline")
        finally:
            sock.close()


class ObservedCommit(sqlite3.Connection):
    """Observe real commits; no replacement of authorization or persistence."""
    observer = None
    result_visibility = None

    def commit(self):
        sql = "SELECT COUNT(*) FROM audit_events WHERE event_kind='RESULT'"
        if self.observer is not None and self.execute(sql).fetchone()[0]:
            before = self.observer.execute(sql).fetchone()[0]
            super().commit()
            self.result_visibility = (before, self.observer.execute(sql).fetchone()[0])
        else:
            super().commit()


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT", None, "CONSUMED"])
def test_real_restart_workflow(tmp_path, monkeypatch, decision):
    path = tmp_path / "restart.db"
    monkeypatch.setattr(app.state, "restart_count", 0)
    with closing(sqlite3.connect(path, factory=ObservedCommit)) as conn:
        for initialize in (initialize_action_schema, initialize_approval_schema,
                           initialize_dispatch_reservation_schema, initialize_audit_schema):
            initialize(conn)
        context = create_local_run_context()
        created = create_audited_pending_restart(conn, context, MockRestartServiceProposal(
            tool="mock.restart_service", resource="demo-api", arguments={},
        ))
        assert created.status is PendingRestartStatus.CREATED
        assert created.action_id is not None and created.action_id.version == 4
        assert not conn.in_transaction
        # No caller COMMIT: both records must already be independently visible.
        with closing(sqlite3.connect(path)) as observer:
            action = get_pending_restart_action(observer, created.action_id)
            assert action is not None and action.action_id == created.action_id
            assert observer.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1
            initial = observer.execute("SELECT * FROM audit_events ORDER BY rowid")
            initial_rows = [dict(zip([col[0] for col in initial.description], row)) for row in initial]
            assert len(initial_rows) == 1
            initial_event = initial_rows[0]
            assert initial_event["event_kind"] == "POLICY_DECISION"
            assert initial_event["action_id"] == str(action.action_id)
            assert initial_event["run_id"] == str(context.run_id)
            for field in ("requester_id", "agent_id", "environment"):
                assert initial_event[field] == getattr(context, field)
            assert initial_event["tool"] == "mock.restart_service"
            assert initial_event["resource"] == "demo-api"
            assert initial_event["policy_decision"] == "APPROVAL_REQUIRED"
            assert initial_event["execution_status"] == "NOT_DISPATCHED"
            assert initial_event["reason_code"] == "POLICY_APPROVAL_REQUIRED"
            assert initial_event["approval_id"] is initial_event["dispatch_id"] is None
            assert observer.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM dispatch_reservations").fetchone()[0] == 0
        assert app.state.restart_count == 0
        if decision is not None:
            monkeypatch.setattr(type(operator_cli.sys.stdin), "isatty", lambda self: True)
            monkeypatch.setattr(type(operator_cli.sys.stdout), "isatty", lambda self: True)
            def confirm(prompt):
                assert str(action.action_id) in prompt
                return f"{'APPROVE' if decision == 'CONSUMED' else decision} {action.action_id}"
            monkeypatch.setattr("builtins.input", confirm)
            assert operator_cli.main(["--db", str(path), "decide", str(action.action_id)]) == 0
        with closing(sqlite3.connect(path)) as observer:
            approval = get_approval_by_action_id(observer, action.action_id)
            if decision is None:
                assert approval is None
            else:
                assert approval.action_id == action.action_id
                assert approval.run_id == context.run_id
                assert approval.action_fingerprint == action.action_fingerprint
                assert approval.consumed is False
                assert approval.decision.value == ("REJECTED" if decision == "REJECT" else "APPROVED")
            if decision == "CONSUMED":
                conn.execute("UPDATE approvals SET consumed=1 WHERE action_id=?", (str(action.action_id),))
                conn.commit()
            assert app.state.restart_count == 0  # Creating/approving never dispatches.
            posts, responses = [], []

            async def observed_app(scope, receive, send):
                if scope["type"] == "http" and scope["method"] == "POST":
                    posts.append(scope["path"])
                    assert not conn.in_transaction
                    assert get_approval_by_action_id(observer, action.action_id).consumed is True
                    assert get_dispatch_reservation_by_action_id(observer, action.action_id) is not None
                    assert observer.execute("SELECT event_kind FROM audit_events ORDER BY rowid").fetchall() == [
                        ("POLICY_DECISION",), ("DISPATCH_RESERVED",),
                    ]
                    # Prove no SQLite writer lock spans HTTP I/O.
                    observer.execute("BEGIN IMMEDIATE")
                    observer.rollback()
                    body = bytearray()
                    async def capture(message):
                        if message["type"] == "http.response.body":
                            body.extend(message.get("body", b""))
                            if not message.get("more_body", False):
                                responses.append(json.loads(body))
                        await send(message)
                    await app(scope, receive, capture)
                else:
                    await app(scope, receive, send)

            async def exercise():
                async with owned_server(observed_app):
                    async with httpx.AsyncClient(trust_env=False, timeout=2, follow_redirects=False) as client:
                        async def check_health():
                            response = await client.get("http://127.0.0.1:8001/services/demo-api/health")
                            assert response.status_code == 200 and response.json() == HEALTH
                        await check_health()
                        conn.observer = observer
                        result = await execute_approved_mock_restart(conn, context, action.action_id)
                        assert result.status is (Status.ACKNOWLEDGED if decision == "APPROVE" else Status.NOT_DISPATCHED)
                        if decision == "APPROVE":
                            assert conn.result_visibility == (0, 1)
                        again = await execute_approved_mock_restart(conn, context, action.action_id)
                        assert again.status is Status.NOT_DISPATCHED
                        await check_health()  # Simulation never fabricates recovery.
            asyncio.run(exercise())
            expected = int(decision == "APPROVE")
            assert app.state.restart_count == expected
            assert posts == ["/services/demo-api/restart"] * expected
            assert responses == [{"service": "demo-api", "status": "restart_requested", "simulated": True}] * expected
            assert observer.execute("SELECT COUNT(*) FROM dispatch_reservations").fetchone()[0] == expected
            events = observer.execute("SELECT * FROM audit_events ORDER BY rowid")
            rows = [dict(zip([col[0] for col in events.description], row)) for row in events]
            assert [row["event_kind"] for row in rows] == (
                ["POLICY_DECISION", "DISPATCH_RESERVED", "RESULT"] if expected else ["POLICY_DECISION"]
            )
            assert rows[0] == initial_event
            for row in rows:
                assert row["action_id"] == str(action.action_id)
                assert row["run_id"] == str(context.run_id)
                for field in ("requester_id", "agent_id", "environment"):
                    assert row[field] == getattr(context, field)
                assert row["tool"] == action.tool and row["resource"] == action.resource
                assert row["policy_decision"] == "APPROVAL_REQUIRED"
            if expected:
                reservation = get_dispatch_reservation_by_action_id(observer, action.action_id)
                assert reservation.approval_id == approval.approval_id
                assert reservation.action_fingerprint == action.action_fingerprint
                assert get_approval_by_action_id(observer, action.action_id).consumed is True
                for row in rows[1:]:
                    for field in ("action_id", "approval_id", "dispatch_id", "run_id"):
                        assert row[field] == str(getattr(reservation, field))
                assert rows[1]["execution_status"] == "NOT_DISPATCHED"
                assert rows[1]["reason_code"] == "MOCK_RESTART_RESERVED"
                assert rows[2]["execution_status"] == "ACKNOWLEDGED"
                assert rows[2]["reason_code"] == "MOCK_RESTART_ACKNOWLEDGED"
                with pytest.raises(sqlite3.IntegrityError):
                    conn.execute("UPDATE dispatch_reservations SET state='RESERVED'")
                conn.rollback()
                with pytest.raises(sqlite3.IntegrityError):
                    conn.execute("DELETE FROM dispatch_reservations")
                conn.rollback()


def test_policy_denied_restart_is_audited_without_action_or_http(tmp_path, monkeypatch):
    path = tmp_path / "denied.db"
    monkeypatch.setattr(app.state, "restart_count", 0)
    def forbidden_network(*args, **kwargs):
        pytest.fail("Denied Gateway request must not use HTTP")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden_network)
    monkeypatch.setattr(httpx, "Client", forbidden_network)
    with closing(sqlite3.connect(path)) as conn:
        for initialize in (initialize_action_schema, initialize_approval_schema,
                           initialize_dispatch_reservation_schema, initialize_audit_schema):
            initialize(conn)
        context = create_local_run_context()
        result = create_audited_pending_restart(conn, context, MockRestartServiceProposal(
            tool="mock.restart_service", resource="other-service", arguments={},
        ))
        assert result.status is PendingRestartStatus.NOT_CREATED and result.action_id is None
        assert not conn.in_transaction
    with closing(sqlite3.connect(path)) as observer:
        for table in ("actions", "approvals", "dispatch_reservations"):
            assert observer.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert observer.execute("""SELECT event_kind, policy_decision, execution_status,
            reason_code, resource, action_id, approval_id, dispatch_id FROM audit_events""").fetchall() == [
            ("POLICY_DECISION", "DENY", "NOT_DISPATCHED", "POLICY_DENY", "other-service", None, None, None)
        ]
    assert app.state.restart_count == 0


def test_occupied_port_fails_before_serving_or_sending():
    with closing(bind_owned_socket()) as occupied:
        async def must_not_run(scope, receive, send):
            pytest.fail("No application may run on an unowned endpoint")
        async def exercise():
            with pytest.raises(RuntimeError, match="Cannot own"):
                async with owned_server(must_not_run):
                    pytest.fail("An occupied port must not become ready")
        asyncio.run(exercise())
        # No readiness probe or mutation was sent to the occupied listener.
        with pytest.raises(BlockingIOError):
            occupied.accept()
