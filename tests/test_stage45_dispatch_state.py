from contextlib import closing, contextmanager
import json
import os
import sqlite3
import subprocess
import sys
from threading import Event, Thread
from time import monotonic
from unittest.mock import Mock
from uuid import uuid4

import pytest

from adaptive_agentops.policy import PolicyDecision
from adaptive_agentops.stage4_boundary import Endpoint, IdentityError, Link, ProtocolError, TrustedUIDs
from adaptive_agentops.stage4_boundary import controller_state, delegation, dispatch_state
from adaptive_agentops.stage4_boundary.dispatch_state import DispatchStatus


CONTROL = os.getuid()
UIDS = TrustedUIDs(60201, CONTROL, 60203)
REQUESTER, APPROVER = 60204, 60205
TARGET = Endpoint(Link.CONTROL_TARGET, "/run/dispatch-test/target/dispatch.sock", 60211)
NANOSECONDS = 1_000_000_000


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    assert CONTROL != 0, "Stage 4.5 local tests require the unprivileged Controller identity"
    # Native acceptance verifies the protected root-owned path and actual UIDs.
    monkeypatch.setattr(controller_state, "_check_ancestor", lambda info: None)
    path = tmp_path / "state"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def ready_state(state_dir):
    dispatch_state.provision_dispatch_fixture_state(state_dir, CONTROL)
    return state_dir


@pytest.fixture
def clock(monkeypatch):
    samples = [100 * NANOSECONDS, 50 * NANOSECONDS]
    monkeypatch.setattr(delegation, "_read_clocks", lambda: tuple(samples))
    return samples


def invoke(state_dir, endpoint=TARGET, *, requester=REQUESTER, approver=APPROVER):
    return dispatch_state.dispatch_fixture_once(
        state_dir, endpoint, UIDS, requester_uid=requester, approver_uid=approver,
    )


def rows(state_dir, query):
    path = state_dir / controller_state.DATABASE_NAME
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        return conn.execute(query).fetchall()


def connect_stub(monkeypatch, before=None):
    socket = object()
    reached = Mock()

    @contextmanager
    def connect(endpoint, uids):
        assert (endpoint, uids) == (TARGET, UIDS)
        reached()
        if before is not None:
            before()
        deadline = monotonic() + 2
        yield socket, deadline

    monkeypatch.setattr(dispatch_state, "_connect_verified", connect)
    return socket, reached


def test_provision_requires_empty_state_and_keeps_profiles_separate(state_dir, monkeypatch):
    controller_state.provision_controller_state(state_dir, CONTROL)
    connect = Mock(side_effect=AssertionError("wrong profile reached target"))
    monkeypatch.setattr(dispatch_state, "_connect_verified", connect)
    with pytest.raises(controller_state.ControllerStateError, match="unexpected application ID, schema or version"):
        invoke(state_dir)
    with pytest.raises(controller_state.ControllerStateError, match="requires an empty directory"):
        dispatch_state.provision_dispatch_fixture_state(state_dir, CONTROL)
    assert rows(state_dir, "PRAGMA user_version") == [(1,)]
    connect.assert_not_called()


def test_unprovisioned_dispatch_never_creates_its_own_database(state_dir):
    with pytest.raises(controller_state.ControllerStateError, match="database is missing"):
        invoke(state_dir)
    assert not list(state_dir.iterdir())


def test_bad_target_link_and_authority_uids_do_not_create_session(ready_state):
    wrong = Endpoint(Link.AGENT_CONTROL, "/run/dispatch-test/agent/proposal.sock", 60211)
    with pytest.raises(ValueError, match="Controller-to-target"):
        invoke(ready_state, endpoint=wrong)
    for requester, approver in ((REQUESTER, REQUESTER), (UIDS.agent, APPROVER), (REQUESTER, CONTROL)):
        with pytest.raises(ValueError, match="dedicated"):
            invoke(ready_state, requester=requester, approver=approver)
    assert rows(ready_state, "SELECT COUNT(*) FROM controller_sessions") == [(0,)]


def test_committed_reservation_and_audit_precede_one_no_effect_send(ready_state, monkeypatch, clock):
    socket, connected = connect_stub(monkeypatch)

    def exchange(connection, deadline, envelope, *, clock=None):
        assert connection is socket
        assert clock is not None and clock.sample() == (100 * NANOSECONDS, 50 * NANOSECONDS)
        assert deadline > monotonic()
        assert rows(ready_state, "SELECT approval_id, consumed FROM dispatch_approvals") == [
            (str(envelope.approval_id), 1),
        ]
        assert rows(ready_state, "SELECT dispatch_id, action_id, approval_id, grant_id, action_fingerprint, target_id "
                    "FROM dispatch_reservations") == [(
            str(envelope.dispatch_id), str(envelope.action_id), str(envelope.approval_id),
            str(envelope.grant_id), envelope.action_fingerprint, "demo-api",
        )]
        assert rows(ready_state, "SELECT kind FROM dispatch_events ORDER BY rowid") == [
            ("FIXTURE_CREATED",), ("RESERVED",),
        ]
        assert envelope.tool == "mock.restart_service" and envelope.arguments.model_dump() == {}
        assert envelope.not_after_wall_ns - envelope.issued_wall_ns == 5 * NANOSECONDS
        assert envelope.not_after_elapsed_ns - envelope.issued_elapsed_ns == 5 * NANOSECONDS

    send = Mock(side_effect=exchange)
    monkeypatch.setattr(dispatch_state, "_exchange_reserved", send)
    assert invoke(ready_state) is DispatchStatus.VALIDATED_NO_EFFECT
    send.assert_called_once()
    connected.assert_called_once()
    assert rows(ready_state, "SELECT status FROM dispatch_outcomes") == [("VALIDATED_NO_EFFECT",)]
    with pytest.raises(dispatch_state.DispatchFixtureUsedError):
        invoke(ready_state)
    send.assert_called_once()
    connected.assert_called_once()
    assert rows(ready_state, "SELECT state FROM dispatch_grants") == [("INVALIDATED",)]
    assert rows(ready_state, "SELECT state FROM controller_sessions") == [("INVALIDATED",)]


def test_wrong_target_gets_no_reservation_and_fixture_cannot_resume(ready_state, monkeypatch):
    def wrong_peer():
        raise IdentityError("wrong target UID")

    _, connected = connect_stub(monkeypatch, wrong_peer)
    send = Mock()
    monkeypatch.setattr(dispatch_state, "_exchange_reserved", send)
    with pytest.raises(IdentityError, match="wrong target"):
        invoke(ready_state)
    assert rows(ready_state, "SELECT consumed FROM dispatch_approvals") == [(0,)]
    assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_reservations") == [(0,)]
    with pytest.raises(dispatch_state.DispatchFixtureUsedError):
        invoke(ready_state)
    connected.assert_called_once()
    send.assert_not_called()


@pytest.mark.parametrize("tampering", ["revoked", "cross-action", "cross-run", "consumed", "health-scope"])
def test_wrong_persisted_bindings_never_authorize_a_send(ready_state, monkeypatch, tampering):
    def change_state():
        with closing(sqlite3.connect(ready_state / controller_state.DATABASE_NAME, isolation_level=None)) as conn:
            if tampering == "revoked":
                conn.execute("UPDATE dispatch_grants SET state = 'REVOKED'")
            elif tampering == "consumed":
                conn.execute("UPDATE dispatch_approvals SET consumed = 1")
            elif tampering == "cross-action":
                payload, = conn.execute("SELECT payload FROM dispatch_approvals").fetchone()
                other = json.loads(payload)
                other["action_id"] = str(uuid4())
                conn.execute("UPDATE dispatch_approvals SET payload = ?", (json.dumps(other),))
            elif tampering == "cross-run":
                conn.execute("UPDATE dispatch_grants SET run_id = ?", (str(uuid4()),))
            else:
                payload, = conn.execute("SELECT payload FROM dispatch_actions").fetchone()
                other = json.loads(payload)
                other["tool"] = "mock.get_health"
                conn.execute("UPDATE dispatch_actions SET payload = ?", (json.dumps(other),))

    connect_stub(monkeypatch, change_state)
    send = Mock()
    monkeypatch.setattr(dispatch_state, "_exchange_reserved", send)
    with pytest.raises(controller_state.ControllerStateError):
        invoke(ready_state)
    send.assert_not_called()
    assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_reservations") == [(0,)]


@pytest.mark.parametrize("denial", [PolicyDecision.DENY, PolicyDecision.ALLOW])
def test_fresh_policy_must_require_approval_for_restart(ready_state, monkeypatch, denial):
    connect_stub(monkeypatch)
    monkeypatch.setattr(dispatch_state, "evaluate_policy", lambda context, proposal: denial)
    send = Mock()
    monkeypatch.setattr(dispatch_state, "_exchange_reserved", send)
    with pytest.raises(controller_state.ControllerStateError, match="policy"):
        invoke(ready_state)
    send.assert_not_called()
    assert rows(ready_state, "SELECT consumed FROM dispatch_approvals") == [(0,)]


@pytest.mark.parametrize("clock_signal", ["expired", "backward", "divergent"])
def test_stale_or_unsafe_clock_precedes_reservation(ready_state, monkeypatch, clock, clock_signal):
    def displace():
        if clock_signal == "expired":
            clock[0] += 60 * NANOSECONDS
            clock[1] += 60 * NANOSECONDS
        elif clock_signal == "backward":
            clock[0] -= 1
        else:
            clock[0] += NANOSECONDS + 1

    connect_stub(monkeypatch, displace)
    send = Mock()
    monkeypatch.setattr(dispatch_state, "_exchange_reserved", send)
    with pytest.raises((controller_state.ControllerStateError, delegation.ClockError)):
        invoke(ready_state)
    send.assert_not_called()
    assert rows(ready_state, "SELECT consumed FROM dispatch_approvals") == [(0,)]
    assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_reservations") == [(0,)]


@pytest.mark.parametrize("persisted", [False, True])
def test_error_during_reservation_commit_never_sends_or_reconstructs_permit(ready_state, monkeypatch, persisted):
    connect_stub(monkeypatch)
    send = Mock()
    monkeypatch.setattr(dispatch_state, "_exchange_reserved", send)

    def uncertain(conn):
        if conn.execute("SELECT 1 FROM dispatch_reservations").fetchone():
            if persisted:
                conn.commit()
            raise sqlite3.OperationalError("injected reservation commit fault")
        conn.commit()

    with monkeypatch.context() as fault:
        fault.setattr(controller_state, "_commit", uncertain)
        with pytest.raises(controller_state.ControllerStateError, match="commit may be uncertain"):
            invoke(ready_state)
    assert rows(ready_state, "SELECT consumed FROM dispatch_approvals") == [(int(persisted),)]
    assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_reservations") == [(int(persisted),)]
    assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_events WHERE kind = 'RESERVED'") == [(int(persisted),)]
    with pytest.raises(dispatch_state.DispatchFixtureUsedError):
        invoke(ready_state)
    send.assert_not_called()


@pytest.mark.parametrize("failure", ["expired", "encode"])
def test_post_commit_failure_burns_reservation_before_any_send(ready_state, monkeypatch, clock, failure):
    connect_stub(monkeypatch)
    send = Mock()
    monkeypatch.setattr(dispatch_state, "_exchange_reserved", send)

    if failure == "encode":
        monkeypatch.setattr(dispatch_state, "_envelope", Mock(side_effect=ValueError("injected envelope failure")))
    else:
        def delayed(conn):
            is_reserved = conn.execute("SELECT 1 FROM dispatch_reservations").fetchone()
            conn.commit()
            if is_reserved:
                clock[0] = 160 * NANOSECONDS
                clock[1] = 110 * NANOSECONDS

        monkeypatch.setattr(controller_state, "_commit", delayed)
    assert invoke(ready_state) is DispatchStatus.RESERVED_NO_SEND
    assert rows(ready_state, "SELECT consumed FROM dispatch_approvals") == [(1,)]
    assert rows(ready_state, "SELECT status FROM dispatch_outcomes") == [("RESERVED_NO_SEND",)]
    send.assert_not_called()


@pytest.mark.parametrize("failure", [TimeoutError, BrokenPipeError, ProtocolError])
def test_missing_or_uncorrelated_acknowledgment_does_not_reconnect(ready_state, monkeypatch, failure):
    _, connected = connect_stub(monkeypatch)
    send = Mock(side_effect=failure("injected post-permit failure"))
    monkeypatch.setattr(dispatch_state, "_exchange_reserved", send)
    assert invoke(ready_state) is DispatchStatus.UNKNOWN
    assert rows(ready_state, "SELECT status FROM dispatch_outcomes") == [("UNKNOWN",)]
    assert rows(ready_state, "SELECT consumed FROM dispatch_approvals") == [(1,)]
    connected.assert_called_once()
    send.assert_called_once()


@pytest.mark.parametrize("capacity", [2, 3])
def test_capacity_abort_preserves_reservation_and_evidence_atomicity(ready_state, monkeypatch, capacity):
    connect_stub(monkeypatch)
    send = Mock()
    monkeypatch.setattr(controller_state, "_MAX_EVENTS", capacity)
    monkeypatch.setattr(dispatch_state, "_exchange_reserved", send)
    if capacity == 2:
        with pytest.raises(controller_state.ControllerStateError, match="evidence capacity"):
            invoke(ready_state)
        send.assert_not_called()
        assert rows(ready_state, "SELECT consumed FROM dispatch_approvals") == [(0,)]
        assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_reservations") == [(0,)]
    else:
        assert invoke(ready_state) is DispatchStatus.UNKNOWN
        send.assert_called_once()
        assert rows(ready_state, "SELECT consumed FROM dispatch_approvals") == [(1,)]
        assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_reservations") == [(1,)]
    assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_outcomes") == [(0,)]


def test_single_lease_and_unique_fixture_abort_concurrent_coordinator(ready_state, monkeypatch, clock):
    _, connected = connect_stub(monkeypatch)
    first_reached, release = Event(), Event()
    result = []

    def exchange(*args, **kwargs):
        first_reached.set()
        assert release.wait(timeout=2)

    def first():
        try:
            result.append(invoke(ready_state))
        except BaseException as error:
            result.append(error)

    monkeypatch.setattr(dispatch_state, "_exchange_reserved", exchange)
    process = Thread(target=first)
    process.start()
    try:
        assert first_reached.wait(timeout=2)
        with pytest.raises(controller_state.ControllerLeaseError):
            invoke(ready_state)
    finally:
        release.set()
        process.join(timeout=2)
    assert result == [DispatchStatus.VALIDATED_NO_EFFECT]
    assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_reservations") == [(1,)]
    connected.assert_called_once()


def test_in_process_permit_rejects_other_connection_and_reuse():
    connection = object()
    reserved = object()
    permit = dispatch_state._Permit(connection, reserved)
    with pytest.raises(controller_state.ControllerStateError, match="this connection"):
        permit.consume(object())
    assert permit.consume(connection) is reserved
    with pytest.raises(controller_state.ControllerStateError, match="this connection"):
        permit.consume(connection)


def test_crash_after_confirmed_reservation_leaves_no_recoverable_send_permit(ready_state, monkeypatch):
    crash = """
from contextlib import contextmanager
import os
import sys
from time import monotonic
from adaptive_agentops.stage4_boundary import Endpoint, Link, TrustedUIDs
from adaptive_agentops.stage4_boundary import controller_state, dispatch_state

controller_state._check_ancestor = lambda _: None

@contextmanager
def connect(*args):
    yield object(), monotonic() + 2

def commit_and_crash_after_reservation(connection):
    reserved = connection.execute('SELECT 1 FROM dispatch_reservations').fetchone()
    connection.commit()
    if reserved:
        os._exit(46)

def unexpected_send(*args, **kwargs):
    os._exit(47)

controller_state._commit = commit_and_crash_after_reservation
dispatch_state._connect_verified = connect
dispatch_state._exchange_reserved = unexpected_send
dispatch_state.dispatch_fixture_once(
    sys.argv[1], Endpoint(Link.CONTROL_TARGET, '/run/dispatch-test/target/dispatch.sock', 60211),
    TrustedUIDs(60201, os.getuid(), 60203), requester_uid=60204, approver_uid=60205,
)
"""
    result = subprocess.run([sys.executable, "-c", crash, str(ready_state)], capture_output=True, timeout=8)
    assert (result.returncode, result.stderr) == (46, b"")
    assert rows(ready_state, "SELECT consumed FROM dispatch_approvals") == [(1,)]
    assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_reservations") == [(1,)]
    assert rows(ready_state, "SELECT COUNT(*) FROM dispatch_outcomes") == [(0,)]
    connected = Mock(side_effect=AssertionError("restart opened a target connection"))
    monkeypatch.setattr(dispatch_state, "_connect_verified", connected)
    with pytest.raises(dispatch_state.DispatchFixtureUsedError):
        invoke(ready_state)
    connected.assert_not_called()
