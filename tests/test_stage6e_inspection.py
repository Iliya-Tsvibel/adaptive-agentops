from contextlib import closing, contextmanager, nullcontext
from datetime import datetime, timezone
import sqlite3
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from adaptive_agentops import actions, approvals
from adaptive_agentops.stage4_boundary import Endpoint, IdentityError, Link, ProtocolError, TrustedUIDs, monotonic
from adaptive_agentops.stage4_boundary import controller_state as state, evidence_inspection as bridge
from adaptive_agentops.stage4_boundary import hardened, operator, target_state

from test_stage47_hardened import (
    AGENT, APPROVER, CONTROL, OPERATORS, REQUESTER, TARGET, TARGET_SOCKET,
    action_exchange, advance, clock, controller, pending, profile, target_connection,
)

UIDS = TrustedUIDs(AGENT, CONTROL, TARGET)
INSPECTOR = 61006
INSPECT_SOCKET = Endpoint(Link.OPERATOR_CONTROL, "/run/stage6e-test/inspector/read.sock", 61016)
EXEC_ENDPOINTS = (
    Endpoint(Link.AGENT_CONTROL, "/run/stage6e-test/agent/control.sock", 61011),
    Endpoint(Link.OPERATOR_CONTROL, "/run/stage6e-test/operator/control.sock", 61012),
    TARGET_SOCKET,
)


@pytest.fixture
def historical_unknown(profile, monkeypatch, clock):
    historical_time = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return historical_time.astimezone(tz)

    # Match both Stage 4 wall-clock sources to one deterministic history.
    clock[0] = bridge.dispatch._ns(historical_time) - 1_000_000_000
    monkeypatch.setattr(actions, "datetime", FixedDatetime)
    monkeypatch.setattr(approvals, "datetime", FixedDatetime)
    target_connection(monkeypatch)
    send = Mock(side_effect=OSError("receipt lost"))
    monkeypatch.setattr(target_state, "_exchange", send)
    with controller(profile) as (ctrl, conn):
        advance(clock, 1)
        action = pending(monkeypatch, ctrl)
        action_exchange(monkeypatch, ctrl, hardened.ActionMethod.APPROVE, action)
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.DISPATCH, action).endswith(b"UNKNOWN")
        dispatch_id = UUID(conn.execute("SELECT dispatch_id FROM dispatch_reservations").fetchone()[0])
    return bridge.InspectorScope(INSPECTOR, dispatch_id), send


def acquire(profile, scope, target=TARGET_SOCKET):
    with state._state_lease(profile, CONTROL) as fd:
        return bridge._acquire(fd, scope, UIDS, target, monotonic() + 2)


@pytest.mark.parametrize(("target_case", "result", "source"), [
    ("committed", "RECORDED_SIMULATED_EFFECT", "TARGET"),
    ("unknown", "UNRESOLVED", None),
    ("unavailable", "UNRESOLVED", None),
    ("invalid", "INCONSISTENT", None),
])
def test_historical_unknown_observations(historical_unknown, profile, monkeypatch, target_case, result, source):
    scope, mutation = historical_unknown
    original = (profile / state.DATABASE_NAME).read_bytes()

    def lookup(endpoint, uids, key, *, deadline):
        assert endpoint == TARGET_SOCKET and uids == UIDS and key.dispatch_id == scope.dispatch_id
        assert monotonic() < deadline
        if target_case == "unavailable":
            raise TimeoutError
        if target_case == "invalid":
            raise ProtocolError("canary")
        number = 1 if target_case == "committed" else None
        return target_state.ReceiptResult(**key.model_dump(), effect_number=number, status=target_case.upper())

    monkeypatch.setattr(target_state, "lookup_target_receipt", lookup)
    for _ in range(2):
        reply = acquire(profile, scope)
        assert (reply.result, reply.controller, reply.target, reply.effect) == (
            result, "UNKNOWN", target_case.upper(), source,
        )
    assert (profile / state.DATABASE_NAME).read_bytes() == original
    mutation.assert_called_once()


def test_wrong_historical_target_blocks_lookup(historical_unknown, profile, monkeypatch):
    scope, _ = historical_unknown
    lookup = Mock()
    monkeypatch.setattr(target_state, "lookup_target_receipt", lookup)
    result = acquire(profile, scope, EXEC_ENDPOINTS[1])
    assert (result.result, result.controller, result.effect) == ("INCONSISTENT", "INVALID", None)
    lookup.assert_not_called()


@pytest.mark.parametrize("uid", [CONTROL, AGENT, TARGET, REQUESTER, APPROVER])
def test_frozen_inspector_cannot_be_an_existing_principal(uid):
    with pytest.raises(ValueError, match="distinct UID"):
        bridge._validate_inspector(INSPECT_SOCKET, bridge.InspectorScope(uid, uuid4()),
                                   UIDS, OPERATORS, EXEC_ENDPOINTS)


@pytest.mark.parametrize("bad_request", [b"{}", b"AAO\x07\x03", b"AAO\x08\x01" + b"x" * 16])
def test_out_of_scope_or_wrong_method_never_reads(monkeypatch, bad_request):
    monkeypatch.setattr(bridge, "_require_peer", Mock())
    peer = Mock(recv=Mock(side_effect=[bad_request, b""]))
    acquire = Mock()
    bridge._inspect_peer(peer, monotonic() + 2, bridge.InspectorScope(INSPECTOR, uuid4()), None, acquire)
    acquire.assert_not_called()
    peer.sendall.assert_not_called()


def test_wrong_peer_is_denied_before_frame_read(monkeypatch):
    monkeypatch.setattr(bridge, "_require_peer", Mock(side_effect=IdentityError("wrong principal")))
    peer, acquire = Mock(), Mock()
    bridge._inspect_peer(peer, monotonic() + 2, bridge.InspectorScope(INSPECTOR, uuid4()), None, acquire)
    acquire.assert_not_called()
    peer.recv.assert_not_called()
    peer.sendall.assert_not_called()


@pytest.mark.parametrize("commit", ["success", "failure", "ambiguous"])
def test_disclosure_requires_confirmed_durable_audit(tmp_path, monkeypatch, commit):
    monkeypatch.setattr(state, "_check_ancestor", lambda _: None)
    monkeypatch.setattr(bridge, "_require_peer", Mock())
    directory = tmp_path / "audit"
    directory.mkdir(mode=0o700)
    bridge.provision_inspection_audit(directory, CONTROL)
    scope = bridge.InspectorScope(INSPECTOR, uuid4())
    result = bridge.EvidenceDiagnostic(result="UNRESOLVED", controller="UNKNOWN", target="UNKNOWN")
    peer = Mock(recv=Mock(side_effect=[bridge._REQUEST + scope.dispatch_id.bytes, b""]))

    if commit != "success":
        def fail(connection):
            if commit == "ambiguous":
                connection.commit()
            raise state.ControllerStateError("audit canary")
        monkeypatch.setattr(state, "_commit", fail)

    with state._state_lease(directory, CONTROL) as fd:
        with state._database(fd, CONTROL, bridge._AUDIT_DB) as conn:
            store = state._ControllerStore(conn)
            expected = (scope.uid, str(scope.dispatch_id), result.model_dump_json())
            peer.sendall.side_effect = lambda _: (
                not conn.in_transaction and conn.execute("SELECT * FROM disclosures").fetchone() == expected
            ) or pytest.fail("Disclosure happened before the audit commit")
            bridge._inspect_peer(peer, monotonic() + 2, scope, store, lambda _: result)
            assert conn.execute("SELECT * FROM disclosures").fetchone() == (None if commit == "failure" else expected)
    if commit == "success":
        peer.sendall.assert_called_once_with(bridge._REPLY + result.model_dump_json().encode("ascii"))
    else:
        peer.sendall.assert_not_called()


def test_wrong_server_prevents_any_request(monkeypatch):
    @contextmanager
    def connect(*_args):
        raise IdentityError("wrong Controller")
        yield

    monkeypatch.setattr(bridge, "_connect_as", connect)
    send = Mock()
    monkeypatch.setattr(bridge, "_send_frame", send)
    with pytest.raises(IdentityError):
        bridge.inspect_dispatch(INSPECT_SOCKET, INSPECTOR, CONTROL, uuid4())
    send.assert_not_called()


def test_no_controller_database_never_queries_target(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_check_ancestor", lambda _: None)
    directory = tmp_path / "empty"
    directory.mkdir(mode=0o700)
    lookup = Mock()
    monkeypatch.setattr(target_state, "lookup_target_receipt", lookup)
    reply = acquire(directory, bridge.InspectorScope(INSPECTOR, uuid4()))
    assert (reply.result, reply.controller, reply.target) == ("UNRESOLVED", "UNAVAILABLE", "NOT_QUERIED")
    lookup.assert_not_called()


@pytest.mark.parametrize("field", ["dispatch_id", "action_id", "request_digest"])
def test_authenticated_lookup_rejects_wrong_receipt_key(monkeypatch, field):
    key = target_state.ReceiptKey(dispatch_id=uuid4(), action_id=uuid4(), request_digest="a" * 64)
    mismatch = "b" * 64 if field == "request_digest" else uuid4()
    reply = target_state.ReceiptResult(
        **{**key.model_dump(), field: mismatch}, status="COMMITTED", effect_number=1,
    )
    frame = target_state.dispatch_wire._encode_frame(target_state._LOOKUP_REPLY, reply)
    stream = Mock(recv=Mock(side_effect=[frame, b""]))
    cutoff = monotonic() + 1

    @contextmanager
    def connect(endpoint, client, server, *, deadline):
        assert (endpoint, client, server, deadline) == (TARGET_SOCKET, CONTROL, TARGET, cutoff)
        yield stream, deadline

    monkeypatch.setattr(target_state, "_connect_as", connect)
    with pytest.raises(ProtocolError, match="correlation"):
        target_state.lookup_target_receipt(TARGET_SOCKET, UIDS, key, deadline=cutoff)


@pytest.mark.parametrize(("target", "number", "expected", "source"), [
    ("UNAVAILABLE", None, "RECORDED_SIMULATED_EFFECT", "CONTROLLER"),
    ("UNKNOWN", None, "RECORDED_SIMULATED_EFFECT", "CONTROLLER"),
    ("INVALID", None, "INCONSISTENT", None),
    ("COMMITTED", 1, "RECORDED_SIMULATED_EFFECT", "BOTH"),
    ("COMMITTED", 2, "INCONSISTENT", None),
])
def test_committed_controller_projection(target, number, expected, source):
    history = bridge.evidence_source.HistoricalDispatch(bridge.dispatch.DispatchStatus.SIMULATED_EFFECT_COMMITTED,
                                                        None, 1)
    result = bridge._project(history, number, target)
    assert (result.result, result.effect) == (expected, source)


def test_no_send_projection():
    history = bridge.evidence_source.HistoricalDispatch(bridge.dispatch.DispatchStatus.RESERVED_NO_SEND, None, None)
    result = bridge._project(history, None, "NOT_QUERIED")
    assert (result.result, result.controller, result.effect) == ("CONTROLLER_NO_SEND", "NO_SEND", None)


def test_historical_approval_identity_gates_target_read(historical_unknown, profile, monkeypatch):
    scope, _ = historical_unknown
    with closing(sqlite3.connect(profile / state.DATABASE_NAME)) as conn, conn:
        conn.execute("UPDATE controller_events SET peer_uid = ? WHERE method = 'APPROVE'", (AGENT,))
    lookup = Mock()
    monkeypatch.setattr(target_state, "lookup_target_receipt", lookup)
    reply = acquire(profile, scope)
    assert (reply.result, reply.controller, reply.effect) == ("INCONSISTENT", "INVALID", None)
    lookup.assert_not_called()


def test_target_connect_and_exchange_share_the_absolute_deadline(monkeypatch):
    from adaptive_agentops import stage4_boundary as boundary

    connection = Mock()
    monkeypatch.setattr(boundary.socket, "socket", Mock(return_value=nullcontext(connection)))
    monkeypatch.setattr(boundary, "_socket_directory", Mock(return_value=nullcontext((1, "ignored", "/ignored"))))
    entry = Mock()
    monkeypatch.setattr(boundary, "_entry", Mock(return_value=entry))
    for operation in ("_require_local_uid", "_check_endpoint", "_reject_acls", "_require_peer"):
        monkeypatch.setattr(boundary, operation, Mock())
    monkeypatch.setattr(boundary, "monotonic", Mock(side_effect=[10.0, 10.125]))
    with boundary._connect_as(TARGET_SOCKET, CONTROL, TARGET, deadline=10.25) as (peer, deadline):
        assert peer is connection and deadline == 10.25
    connection.settimeout.assert_called_once_with(0.25)


def test_capture_interruption_releases_no_evidence(monkeypatch):
    scope = bridge.InspectorScope(INSPECTOR, uuid4())
    monkeypatch.setattr(bridge, "_require_peer", Mock())
    audit = Mock()
    monkeypatch.setattr(bridge, "_audit", audit)
    peer = Mock(recv=Mock(side_effect=[bridge._REQUEST + scope.dispatch_id.bytes, b""]))
    capture = Mock(side_effect=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        bridge._inspect_peer(peer, monotonic() + 2, scope, None, capture)
    capture.assert_called_once()
    audit.assert_not_called()
    peer.sendall.assert_not_called()


def test_audit_capacity_prevents_disclosure(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_check_ancestor", lambda _: None)
    monkeypatch.setattr(bridge, "_require_peer", Mock())
    monkeypatch.setattr(bridge, "_AUDIT_LIMIT", 1)
    directory = tmp_path / "audit"
    directory.mkdir(mode=0o700)
    bridge.provision_inspection_audit(directory, CONTROL)
    scope = bridge.InspectorScope(INSPECTOR, uuid4())
    result = bridge.EvidenceDiagnostic(result="UNRESOLVED", controller="UNKNOWN", target="UNKNOWN")
    with state._state_lease(directory, CONTROL) as fd:
        with state._database(fd, CONTROL, bridge._AUDIT_DB) as conn:
            for allowed in (True, False):
                peer = Mock(recv=Mock(side_effect=[bridge._REQUEST + scope.dispatch_id.bytes, b""]))
                bridge._inspect_peer(peer, monotonic() + 2, scope, state._ControllerStore(conn),
                                     lambda _: result)
                assert peer.sendall.called is allowed
            assert conn.execute("SELECT count(*) FROM disclosures").fetchone() == (1,)


def test_corrupt_controller_file_is_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_check_ancestor", lambda _: None)
    directory = tmp_path / "history"
    directory.mkdir(mode=0o700)
    corrupt = directory / state.DATABASE_NAME
    canary = b"corrupt-private-source"
    corrupt.write_bytes(canary)
    corrupt.chmod(0o600)
    lookup = Mock()
    monkeypatch.setattr(target_state, "lookup_target_receipt", lookup)
    reply = acquire(directory, bridge.InspectorScope(INSPECTOR, uuid4()))
    assert (reply.result, reply.controller, reply.target) == ("INCONSISTENT", "INVALID", "NOT_QUERIED")
    assert canary not in reply.model_dump_json().encode("ascii")
    assert corrupt.read_bytes() == canary
    lookup.assert_not_called()
