from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import os
import socket
import sys
from threading import Event, get_ident
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest

from adaptive_agentops import stage4_boundary as boundary
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.stage4_boundary import delegation
from adaptive_agentops.tool_requests import MockGetHealthProposal


UIDS = boundary.TrustedUIDs(10001, 10002, 10003)
ENDPOINT = boundary.Endpoint(boundary.Link.AGENT_CONTROL, "/run/aao/admission.sock", 10011)
PAYLOAD = b'{"tool":"mock.get_health","resource":"demo-api","arguments":{}}'
PROPOSAL = MockGetHealthProposal.model_validate_json(PAYLOAD)
ALLOW = delegation.AdmissionDecision.PROPOSAL_ALLOWED
DENY = delegation.AdmissionDecision.DENY
Reason = delegation.AdmissionReason
SECOND = 1_000_000_000


@pytest.fixture
def clocks(monkeypatch):
    sample = [100 * SECOND, 10 * SECOND]
    monkeypatch.setattr(delegation, "_read_clocks", lambda: tuple(sample))
    return sample


@pytest.fixture
def session(clocks):
    return delegation._Session(UIDS.agent, 10)


def exchange(frame=delegation._PROPOSAL + PAYLOAD):
    connection = MagicMock()
    connection.recv.side_effect = [frame, b""]
    return connection


def test_pdp_permission_does_not_supply_missing_grant(clocks):
    session = delegation._Session(UIDS.agent, None)
    assert evaluate_policy(session.context, PROPOSAL) is PolicyDecision.ALLOW
    verdict = session.evaluate(UIDS.agent, PROPOSAL)
    assert (verdict.decision, verdict.reason, verdict.grant_matched) == (DENY, Reason.MISSING_GRANT, False)


@pytest.mark.parametrize(("field", "substitute"), [
    ("agent_id", "other-agent"),
    ("agent_uid", 10005),
    ("tool", "mock.restart_service"),
    ("resource", "other-service"),
    ("environment", "production"),
    ("session_id", uuid4()),
    ("run_id", uuid4()),
])
def test_grant_requires_exact_binding(session, monkeypatch, field, substitute):
    policy = Mock()
    monkeypatch.setattr(delegation, "evaluate_policy", policy)
    session.grant = replace(session.grant, **{field: substitute})
    assert session.evaluate(UIDS.agent, PROPOSAL).reason is Reason.GRANT_SCOPE_MISMATCH
    policy.assert_not_called()


def test_verified_peer_must_match_bound_uid(session):
    assert session.evaluate(UIDS.agent + 1, PROPOSAL).reason is Reason.GRANT_SCOPE_MISMATCH


@pytest.mark.parametrize("policy_result", [PolicyDecision.DENY, PolicyDecision.APPROVAL_REQUIRED, "ALLOW"])
def test_grant_cannot_override_pdp(session, monkeypatch, policy_result):
    monkeypatch.setattr(delegation, "evaluate_policy", Mock(return_value=policy_result))
    outcome = session.evaluate(UIDS.agent, PROPOSAL)
    assert outcome.decision is DENY
    assert outcome.grant_matched is True
    if isinstance(policy_result, PolicyDecision):
        assert outcome.reason is Reason.POLICY_DENIED
        assert outcome.policy_decision is policy_result
    else:
        assert outcome.reason is Reason.POLICY_ERROR
        assert outcome.policy_decision is None


def test_pdp_exception_is_a_denial(session, monkeypatch):
    monkeypatch.setattr(delegation, "evaluate_policy", Mock(side_effect=RuntimeError))
    assert session.evaluate(UIDS.agent, PROPOSAL).reason is Reason.POLICY_ERROR


@pytest.mark.parametrize("seconds", [True, 0, -1, 901, 1.5])
def test_invalid_fixture_grant_lifetime_rejected_before_listen(monkeypatch, seconds):
    listen = Mock()
    monkeypatch.setattr(delegation, "_listen", listen)
    monkeypatch.setattr(delegation, "_require_local_uid", Mock())
    with pytest.raises(ValueError, match="lifetime"):
        delegation.serve_admission(ENDPOINT, UIDS, fixture_grant_seconds=seconds)
    listen.assert_not_called()


def test_new_session_has_no_authority_even_for_identical_bytes(session):
    assert session.evaluate(UIDS.agent, PROPOSAL).decision is ALLOW
    new_session = delegation._Session(UIDS.agent, None)
    assert new_session.session_id != session.session_id
    assert new_session.context.run_id != session.context.run_id
    assert new_session.evaluate(UIDS.agent, PROPOSAL).reason is Reason.MISSING_GRANT
    new_session.grant = session.grant
    assert new_session.evaluate(UIDS.agent, PROPOSAL).reason is Reason.GRANT_SCOPE_MISMATCH


@pytest.mark.parametrize(("wall_delta", "elapsed_delta"), [
    (10 * SECOND, 10 * SECOND - 1),
    (10 * SECOND - 1, 10 * SECOND),
])
def test_grant_expires_at_either_exact_deadline(session, clocks, wall_delta, elapsed_delta):
    clocks[0] += wall_delta - 1
    clocks[1] += elapsed_delta - 1
    assert session.evaluate(UIDS.agent, PROPOSAL).decision is ALLOW
    clocks[0] += 1
    clocks[1] += 1
    assert session.evaluate(UIDS.agent, PROPOSAL).reason is Reason.GRANT_EXPIRED


@pytest.mark.parametrize(("wall_delta", "elapsed_delta"), [
    (-1, 0), (0, -1), (SECOND + 1, 0), (0, SECOND + 1),
])
def test_clock_rollback_or_excess_divergence_denies(session, clocks, wall_delta, elapsed_delta):
    clocks[0] += wall_delta
    clocks[1] += elapsed_delta
    assert session.evaluate(UIDS.agent, PROPOSAL).reason is Reason.CLOCK_ERROR


def test_exact_one_second_drift_is_allowed_but_cumulative_excess_denies(session, clocks):
    clocks[0] += SECOND
    assert session.evaluate(UIDS.agent, PROPOSAL).decision is ALLOW
    clocks[0] += 1
    assert session.evaluate(UIDS.agent, PROPOSAL).reason is Reason.CLOCK_ERROR


def test_small_backward_step_after_progress_denies(session, clocks):
    clocks[0] += SECOND
    clocks[1] += SECOND
    assert session.evaluate(UIDS.agent, PROPOSAL).decision is ALLOW
    clocks[0] -= 1
    assert session.evaluate(UIDS.agent, PROPOSAL).reason is Reason.CLOCK_ERROR


def test_clock_support_is_required_before_listening(monkeypatch):
    monkeypatch.delattr(delegation.time, "CLOCK_BOOTTIME", raising=False)
    monkeypatch.setattr(delegation, "_require_local_uid", Mock())
    listen = Mock()
    monkeypatch.setattr(delegation, "_listen", listen)
    with pytest.raises(delegation.ClockError, match="suspend-aware"):
        delegation.serve_admission(ENDPOINT, UIDS, fixture_grant_seconds=10)
    listen.assert_not_called()


def test_expiry_during_policy_cannot_admit(session, clocks, monkeypatch):
    def slow_policy(_context, _proposal):
        clocks[0] += 10 * SECOND
        clocks[1] += 10 * SECOND
        return PolicyDecision.ALLOW

    monkeypatch.setattr(delegation, "evaluate_policy", slow_policy)
    outcome = session.evaluate(UIDS.agent, PROPOSAL)
    assert outcome.reason is Reason.GRANT_EXPIRED
    assert outcome.policy_decision is PolicyDecision.ALLOW
    assert outcome.decision is DENY


@pytest.mark.parametrize("payload", [
    b"",
    b"\xff",
    b"null",
    b"[]",
    PAYLOAD[:-1],
    PAYLOAD + b" ",
    PAYLOAD + delegation._PROPOSAL + PAYLOAD,
    PAYLOAD.replace(b'"demo-api"', b"1"),
    PAYLOAD.replace(b'"mock.get_health"', b'"mock.restart_service"'),
    PAYLOAD.replace(b'"arguments":{}', b'"arguments":{"nested":{"x":1,"x":2}}'),
    PAYLOAD.replace(b'"arguments":{}', b'"arguments":NaN'),
    PAYLOAD[:-1] + b',"resource":"demo-api"}',
    *(PAYLOAD[:-1] + b',"' + field + b'":"forged"}' for field in (
        b"agent_id", b"requester_id", b"grant_id", b"run_id", b"role", b"approval", b"destination",
    )),
])
def test_invalid_proposals_do_not_reach_policy(monkeypatch, session, payload):
    monkeypatch.setattr(delegation, "_require_peer", Mock())
    policy = Mock()
    monkeypatch.setattr(delegation, "evaluate_policy", policy)
    connection = exchange(delegation._PROPOSAL + payload)
    delegation._handle_connection(connection, session, UIDS.agent, boundary.monotonic() + 2)
    policy.assert_not_called()
    connection.sendall.assert_not_called()
    assert session.journal[0].reason is Reason.INVALID_PROPOSAL
    assert session.journal[0].decision is DENY


def test_frame_limit_and_eof_are_enforced(monkeypatch, session):
    monkeypatch.setattr(delegation, "_require_peer", Mock())
    parser = Mock()
    monkeypatch.setattr(delegation, "_parse_proposal", parser)
    overlong = exchange(b"a" * 513)
    stalled = exchange()
    stalled.recv.side_effect = [delegation._PROPOSAL + PAYLOAD, TimeoutError]
    for connection in (overlong, stalled):
        delegation._handle_connection(connection, session, UIDS.agent, boundary.monotonic() + 2)
        connection.sendall.assert_not_called()
    parser.assert_not_called()
    assert [event.reason for event in session.journal] == [Reason.INVALID_FRAME, Reason.TRANSPORT_ERROR]


def test_exact_frame_byte_limit_still_requires_eof():
    connection = exchange(b"a" * 512)
    assert delegation._read_frame(connection, 512, boundary.monotonic() + 2) == b"a" * 512
    assert [call.args for call in connection.recv.call_args_list] == [(513,), (1,)]


def test_no_reply_before_audit_and_no_reply_after_audit_failure(monkeypatch, session):
    monkeypatch.setattr(delegation, "_require_peer", Mock())
    connection = exchange()

    def after_record(frame):
        assert len(session.journal) == 1
        assert frame == delegation._verdict_frame(ALLOW)

    connection.sendall.side_effect = after_record
    delegation._handle_connection(connection, session, UIDS.agent, boundary.monotonic() + 2)
    connection.sendall.assert_called_once()
    event = session.journal[0]
    assert (event.peer_uid, event.agent_id) == (UIDS.agent, session.context.agent_id)
    assert (event.session_id, event.run_id, event.grant_id) == (
        session.session_id, session.context.run_id, session.grant.grant_id,
    )
    assert event.decision is ALLOW
    assert event.grant_matched is True
    assert event.policy_decision is PolicyDecision.ALLOW

    monkeypatch.setattr(session, "record", Mock(side_effect=OSError("journal failure")))
    connection = exchange()
    with pytest.raises(OSError, match="journal failure"):
        delegation._handle_connection(connection, session, UIDS.agent, boundary.monotonic() + 2)
    connection.sendall.assert_not_called()


def test_journal_exhaustion_prevents_positive_reply(monkeypatch, session):
    monkeypatch.setattr(delegation, "_require_peer", Mock())
    monkeypatch.setattr(delegation, "_MAX_CONNECTIONS", 1)
    session.record(None, delegation._Outcome(Reason.PEER_REJECTED))
    connection = exchange()
    with pytest.raises(boundary.BoundaryError, match="journal exhausted"):
        delegation._handle_connection(connection, session, UIDS.agent, boundary.monotonic() + 2)
    connection.sendall.assert_not_called()


def test_expiry_during_journal_write_prevents_admission_reply(monkeypatch, session, clocks):
    monkeypatch.setattr(delegation, "_require_peer", Mock())
    record = session.record

    def record_then_expire(peer_uid, outcome):
        record(peer_uid, outcome)
        clocks[0] += 10 * SECOND
        clocks[1] += 10 * SECOND

    monkeypatch.setattr(session, "record", record_then_expire)
    connection = exchange()
    delegation._handle_connection(connection, session, UIDS.agent, boundary.monotonic() + 2)
    connection.sendall.assert_not_called()
    assert session.journal[0].decision is ALLOW  # Decision evidence, independent of delivery.


def test_clock_error_stops_session_after_recorded_denial(monkeypatch):
    connection = exchange()
    listener = Mock()
    listener.accept.return_value = (connection, None)

    @contextmanager
    def listen(_endpoint, _server_uid):
        yield listener

    monkeypatch.setattr(delegation, "_listen", listen)
    monkeypatch.setattr(delegation, "_require_local_uid", Mock())
    monkeypatch.setattr(delegation, "_require_peer", Mock())
    monkeypatch.setattr(delegation, "_read_clocks", Mock(side_effect=[
        (100 * SECOND, 10 * SECOND),
        (99 * SECOND, 10 * SECOND),
    ]))
    evidence, = delegation.serve_admission(ENDPOINT, UIDS, fixture_grant_seconds=10)
    listener.accept.assert_called_once()
    assert (evidence.decision, evidence.reason) == (DENY, Reason.CLOCK_ERROR)
    connection.sendall.assert_called_once_with(delegation._verdict_frame(DENY))


def test_every_accept_counts_and_repeat_uses_same_grant(monkeypatch, clocks):
    connections = [exchange(), exchange(b"bogus")] + [exchange() for _ in range(15)]
    listener = Mock()
    listener.accept.side_effect = [(conn, None) for conn in connections]

    @contextmanager
    def listen(endpoint, server_uid):
        assert (endpoint, server_uid) == (ENDPOINT, UIDS.control)
        yield listener

    monkeypatch.setattr(delegation, "_listen", listen)
    monkeypatch.setattr(delegation, "_require_local_uid", Mock())
    monkeypatch.setattr(delegation, "_require_peer", Mock(side_effect=[
        boundary.IdentityError("wrong peer"), *([None] * 15),
    ]))
    journal = delegation.serve_admission(ENDPOINT, UIDS, fixture_grant_seconds=10)
    assert listener.accept.call_count == len(journal) == 16
    assert [event.reason for event in journal[:2]] == [Reason.PEER_REJECTED, Reason.INVALID_PROPOSAL]
    assert journal[0].peer_uid is journal[0].agent_id is journal[0].run_id is journal[0].grant_id is None
    connections[0].recv.assert_not_called()
    connections[0].sendall.assert_not_called()
    assert [event.decision for event in journal[2:]] == [ALLOW] * 14
    assert len({event.grant_id for event in journal[2:]}) == 1
    assert len({event.event_id for event in journal}) == 16
    connections[-1].recv.assert_not_called()


def test_well_formed_scope_denial_is_logged_separately_from_pdp(monkeypatch, session):
    monkeypatch.setattr(delegation, "_require_peer", Mock())
    connection = exchange(delegation._PROPOSAL + PAYLOAD.replace(b"demo-api", b"other-api"))
    delegation._handle_connection(connection, session, UIDS.agent, boundary.monotonic() + 2)
    connection.sendall.assert_called_once_with(delegation._verdict_frame(DENY))
    evidence, = session.journal
    assert (evidence.reason, evidence.grant_matched, evidence.policy_decision) == (
        Reason.GRANT_SCOPE_MISMATCH, False, None,
    )


def test_server_and_client_reject_target_link_before_socket_access(monkeypatch):
    connect = Mock()
    listen = Mock()
    monkeypatch.setattr(delegation, "_connect_verified", connect)
    monkeypatch.setattr(delegation, "_listen", listen)
    target = replace(ENDPOINT, link=boundary.Link.CONTROL_TARGET)
    with pytest.raises(ValueError, match="only available on the agent link"):
        delegation.request_admission(target, UIDS, PROPOSAL)
    with pytest.raises(ValueError, match="only available on the agent link"):
        delegation.serve_admission(target, UIDS)
    connect.assert_not_called()
    listen.assert_not_called()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux Unix sockets and peer credentials")
@pytest.mark.parametrize("reject", [None, "client", "server"])
def test_admission_over_one_live_connection(tmp_path, monkeypatch, reject):
    # Both sides share the test UID. Substitute expected kernel UIDs to
    # exercise each role's verification without claiming separate-UID isolation.
    actual_uid = os.geteuid()
    if actual_uid == 0:
        pytest.skip("requires unprivileged test execution")
    uids = boundary.TrustedUIDs(actual_uid + 1, actual_uid, actual_uid + 2)
    tmp_path.chmod(0o750)
    endpoint = boundary.Endpoint(boundary.Link.AGENT_CONTROL, str(tmp_path / "a.sock"), os.getgid())
    owner = get_ident()
    ready = Event()
    listen = socket.socket.listen
    require_peer = boundary._require_peer
    send_frame = delegation._send_frame
    frames = []

    def verify_local(expected):
        assert expected == (uids.agent if get_ident() == owner else uids.control)

    def notify(listener, backlog):
        listen(listener, backlog)
        ready.set()

    def verify(peer, expected):
        side = "client" if get_ident() == owner else "server"
        assert expected == (uids.control if side == "client" else uids.agent)
        require_peer(peer, actual_uid + (side == reject))

    def record(peer, frame, deadline):
        frames.append(frame)
        send_frame(peer, frame, deadline)

    monkeypatch.setattr(socket.socket, "listen", notify)
    monkeypatch.setattr(boundary, "_check_ancestor", Mock())  # pytest uses /tmp.
    monkeypatch.setattr(boundary, "_reject_acls", Mock())  # Covered by Stage 4.1 tests.
    monkeypatch.setattr(boundary, "_require_local_uid", verify_local)
    monkeypatch.setattr(delegation, "_require_local_uid", verify_local)
    monkeypatch.setattr(boundary, "_require_peer", verify)
    monkeypatch.setattr(delegation, "_require_peer", verify)
    monkeypatch.setattr(delegation, "_send_frame", record)
    monkeypatch.setattr(delegation, "_MAX_CONNECTIONS", 1)
    with ThreadPoolExecutor(max_workers=1) as executor:
        server = executor.submit(delegation.serve_admission, endpoint, uids, fixture_grant_seconds=10)
        assert ready.wait(3)
        if reject is None:
            assert delegation.request_admission(endpoint, uids, PROPOSAL) is ALLOW
        elif reject == "client":
            with pytest.raises(boundary.IdentityError):
                delegation.request_admission(endpoint, uids, PROPOSAL)
        else:
            with pytest.raises((boundary.ProtocolError, OSError)):
                delegation.request_admission(endpoint, uids, PROPOSAL)
        event, = server.result(timeout=3)

    assert not os.path.lexists(endpoint.path)
    if reject is None:
        assert frames == [delegation._PROPOSAL + PAYLOAD, delegation._verdict_frame(ALLOW)]
        assert event.reason is Reason.ADMITTED
    elif reject == "client":
        assert frames == []
        assert event.reason is Reason.INVALID_PROPOSAL
    else:
        assert all(not frame.startswith(delegation._VERDICT) for frame in frames)
        assert event.reason is Reason.PEER_REJECTED
