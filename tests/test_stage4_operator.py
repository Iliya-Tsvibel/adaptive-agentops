from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import errno
import socket
from threading import Event
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from adaptive_agentops import stage4_boundary as boundary
from adaptive_agentops.policy import PolicyDecision
from adaptive_agentops.stage4_boundary import delegation, operator
from adaptive_agentops.tool_requests import MockGetHealthProposal


UIDS = boundary.TrustedUIDs(60101, 60102, 60103)
REQUESTER, APPROVER = 60104, 60105
AGENT = boundary.Endpoint(boundary.Link.AGENT_CONTROL, "/run/test/agent/agent.sock", 60111)
OPERATOR = boundary.Endpoint(boundary.Link.OPERATOR_CONTROL, "/run/test/operator/operator.sock", 60112)
POSITIONS = (
    operator.OperatorIdentity(REQUESTER, "local-requester", frozenset({operator.OperatorRole.REQUESTER})),
    operator.OperatorIdentity(APPROVER, "configured-approver", frozenset({operator.OperatorRole.APPROVER})),
)
Method, State, Result = operator.GrantMethod, operator.GrantState, operator.OperatorResult
Reason = delegation.AdmissionReason
PROPOSAL = MockGetHealthProposal(tool="mock.get_health", resource="demo-api", arguments={})
FRAME = delegation._PROPOSAL + PROPOSAL.model_dump_json().encode()
ALLOW = delegation._verdict_frame(delegation.AdmissionDecision.PROPOSAL_ALLOWED)
DENY = delegation._verdict_frame(delegation.AdmissionDecision.DENY)
SECOND = 1_000_000_000


@pytest.fixture
def clocks(monkeypatch):
    sample = [100 * SECOND, 10 * SECOND]
    monkeypatch.setattr(delegation, "_read_clocks", lambda: tuple(sample))
    return sample


def connection(frame):
    exchange = MagicMock()
    exchange.recv.side_effect = [frame, b""]
    return exchange


def operate(controller, monkeypatch, method, argument=None, uid=REQUESTER):
    monkeypatch.setattr(operator, "_peer_uid", lambda _: uid)
    exchange = connection(operator._frame_request(method, argument))
    keep_serving = controller.handle_operator(exchange, operator.monotonic() + 2)
    return exchange, keep_serving


def issue(controller, monkeypatch, ttl=60):
    exchange, keep_serving = operate(controller, monkeypatch, Method.ISSUE, ttl)
    assert keep_serving and controller.admission.grant is not None
    reply, = exchange.sendall.call_args.args
    assert reply.startswith(operator._REPLY + bytes((State.ACTIVE,)))
    return controller.admission.grant


def test_issue_binds_server_owned_run_identity_scope_and_deadlines(monkeypatch, clocks):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    assert controller.admission.evaluate(UIDS.agent, PROPOSAL).reason is Reason.MISSING_GRANT
    grant = issue(controller, monkeypatch, 1)
    session = controller.admission
    assert (grant.session_id, grant.run_id) == (session.session_id, session.context.run_id)
    assert (controller.grantor_uid, grant.agent_uid, grant.agent_id, grant.tool, grant.resource, grant.environment) == (
        REQUESTER, UIDS.agent, "incident-investigator-v1", "mock.get_health", "demo-api", "local-mock",
    )
    assert (grant.wall_expiry, grant.elapsed_expiry) == (clocks[0] + SECOND, clocks[1] + SECOND)
    assert session.evaluate(UIDS.agent, PROPOSAL).reason is Reason.ADMITTED
    exchange, _ = operate(controller, monkeypatch, Method.INSPECT)
    assert exchange.sendall.call_args.args[0] == operator._grant_frame(
        operator.GrantView(State.ACTIVE, grant.grant_id, session.session_id, grant.run_id, grant.wall_expiry, UIDS.agent),
    )
    exchange, _ = operate(controller, monkeypatch, Method.ISSUE, 900)
    exchange.sendall.assert_called_once_with(operator._DENIED)
    assert controller.journal[-1].result is Result.GRANT_UNAVAILABLE
    assert session.grant is grant


@pytest.mark.parametrize("method", [Method.ISSUE, Method.INSPECT, Method.REVOKE])
@pytest.mark.parametrize("uid", [UIDS.agent, UIDS.control, UIDS.target, APPROVER, 60109])
def test_unmapped_peers_and_approver_role_lack_lifecycle_authority(monkeypatch, method, uid):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    grant = issue(controller, monkeypatch) if method is not Method.ISSUE else None
    argument = {Method.ISSUE: 60, Method.INSPECT: None, Method.REVOKE: grant.grant_id if grant else None}[method]
    exchange, _ = operate(controller, monkeypatch, method, argument, uid=uid)
    event = controller.journal[-1]
    if uid == APPROVER:
        exchange.sendall.assert_called_once_with(operator._DENIED)
        assert (event.operator_uid, event.method, event.result) == (APPROVER, method, Result.METHOD_DENIED)
    else:
        exchange.recv.assert_not_called()
        exchange.sendall.assert_not_called()
        assert (event.operator_uid, event.method, event.result) == (None, None, Result.PEER_REJECTED)
    assert controller.admission.grant is grant
    assert not controller.admission.grant_revoked


@pytest.mark.parametrize("raw", [
    b"", b"AAO\x02\x01\x01", operator._REQUEST + b"\x00",
    operator._REQUEST + b'\x02{"role":"requester","uid":60104}',
    operator._REQUEST + b"\x01\x00", operator._REQUEST + b"\x01\x00\x00",
    operator._REQUEST + b"\x01\x03\x85", operator._REQUEST + b"\x02\x00",
    operator._REQUEST + b"\x03" + b"x" * 15,
])
def test_malformed_frames_and_forged_fields_are_never_authority(monkeypatch, raw):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    monkeypatch.setattr(operator, "_peer_uid", lambda _: REQUESTER)
    exchange = connection(raw)
    assert controller.handle_operator(exchange, operator.monotonic() + 2)
    exchange.sendall.assert_not_called()
    assert controller.journal[-1].result is Result.INVALID_REQUEST
    assert controller.admission.grant is None


@pytest.mark.parametrize("identities", [
    (), POSITIONS + POSITIONS, [POSITIONS[0]], (POSITIONS[1],),
    (operator.OperatorIdentity(UIDS.agent, "local-requester", POSITIONS[0].roles),),
    (operator.OperatorIdentity(UIDS.control, "local-requester", POSITIONS[0].roles),),
    (operator.OperatorIdentity(UIDS.target, "local-requester", POSITIONS[0].roles),),
    (operator.OperatorIdentity(REQUESTER, "wrong-principal", POSITIONS[0].roles),),
    (POSITIONS[0], operator.OperatorIdentity(60108, "another-requester", POSITIONS[0].roles)),
])
def test_invalid_identity_maps_fail_before_listening(monkeypatch, identities):
    listen = MagicMock()
    monkeypatch.setattr(operator, "_listen", listen)
    with pytest.raises(ValueError):
        operator.serve_managed_admission(AGENT, OPERATOR, UIDS, identities)
    listen.assert_not_called()


def test_cross_link_configuration_and_v1_operator_handshake_fail_closed():
    shared_group = boundary.Endpoint(OPERATOR.link, OPERATOR.path, AGENT.access_gid)
    shared_directory = boundary.Endpoint(OPERATOR.link, "/run/test/agent/operator.sock", OPERATOR.access_gid)
    for endpoint in (shared_group, shared_directory, AGENT):
        with pytest.raises(ValueError, match="separate"):
            operator.serve_managed_admission(AGENT, endpoint, UIDS, POSITIONS)
    with pytest.raises(ValueError, match="dedicated"):
        boundary.handshake(OPERATOR, UIDS)
    with pytest.raises(ValueError, match="dedicated"):
        boundary.serve_once(OPERATOR, UIDS)


@pytest.mark.parametrize("decision", [PolicyDecision.DENY, PolicyDecision.APPROVAL_REQUIRED, "ALLOW", RuntimeError()])
def test_issue_and_admit_require_fresh_explicit_policy(monkeypatch, decision):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    policy = MagicMock(side_effect=[PolicyDecision.ALLOW, decision, decision])
    monkeypatch.setattr(delegation, "evaluate_policy", policy)
    grant = issue(controller, monkeypatch)
    assert controller.admission.evaluate(UIDS.agent, PROPOSAL).decision is delegation.AdmissionDecision.DENY
    restarted = operator._Controller(UIDS.agent, POSITIONS)
    exchange, _ = operate(restarted, monkeypatch, Method.ISSUE, 60)
    exchange.sendall.assert_called_once_with(operator._DENIED)
    assert restarted.journal[-1].result is Result.POLICY_DENIED
    assert restarted.admission.grant is None
    assert controller.admission.grant is grant and policy.call_count == 3


def test_revocation_requires_live_exact_id_and_does_not_reopen_issue(monkeypatch):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    grant = issue(controller, monkeypatch)
    exchange, _ = operate(controller, monkeypatch, Method.REVOKE, uuid4())
    exchange.sendall.assert_called_once_with(operator._DENIED)
    assert controller.admission.evaluate(UIDS.agent, PROPOSAL).reason is Reason.ADMITTED
    exchange, _ = operate(controller, monkeypatch, Method.REVOKE, grant.grant_id)
    assert exchange.sendall.call_args.args[0].startswith(operator._REPLY + bytes((State.REVOKED,)))
    assert controller.admission.evaluate(UIDS.agent, PROPOSAL).reason is Reason.GRANT_REVOKED
    exchange, _ = operate(controller, monkeypatch, Method.INSPECT)
    assert exchange.sendall.call_args.args[0].startswith(operator._REPLY + bytes((State.REVOKED,)))
    for method, arg in ((Method.REVOKE, grant.grant_id), (Method.ISSUE, 60)):
        exchange, _ = operate(controller, monkeypatch, method, arg)
        exchange.sendall.assert_called_once_with(operator._DENIED)


@pytest.mark.parametrize("expired_clock", [0, 1])
def test_either_clock_enforces_exact_boundary_for_operator_and_agent(monkeypatch, clocks, expired_clock):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    grant = issue(controller, monkeypatch, 1)
    clocks[expired_clock] += SECOND
    clocks[1 - expired_clock] += SECOND - 1
    exchange, _ = operate(controller, monkeypatch, Method.INSPECT)
    assert exchange.sendall.call_args.args[0].startswith(operator._REPLY + bytes((State.EXPIRED,)))
    exchange, _ = operate(controller, monkeypatch, Method.REVOKE, grant.grant_id)
    exchange.sendall.assert_called_once_with(operator._DENIED)
    assert controller.admission.evaluate(UIDS.agent, PROPOSAL).reason is Reason.GRANT_EXPIRED


def test_restart_and_scope_do_not_inherit_stale_authority(monkeypatch):
    first = operator._Controller(UIDS.agent, POSITIONS)
    previous_grant = issue(first, monkeypatch)
    second = operator._Controller(UIDS.agent, POSITIONS)
    assert second.admission.evaluate(UIDS.agent, PROPOSAL).reason is Reason.MISSING_GRANT
    new_grant = issue(second, monkeypatch)
    assert (previous_grant.grant_id, previous_grant.session_id, previous_grant.run_id) != (
        new_grant.grant_id, new_grant.session_id, new_grant.run_id,
    )
    exchange, _ = operate(second, monkeypatch, Method.REVOKE, previous_grant.grant_id)
    exchange.sendall.assert_called_once_with(operator._DENIED)
    wider = PROPOSAL.model_copy(update={"resource": "other-resource"})
    assert second.admission.evaluate(UIDS.agent, wider).reason is Reason.GRANT_SCOPE_MISMATCH


@pytest.mark.parametrize("fault_timing", ["before_record", "after_record"])
def test_clock_failure_cannot_issue_or_reply(monkeypatch, clocks, fault_timing):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    if fault_timing == "after_record":
        record = controller.record

        def fail_after_record(*args):
            record(*args)
            clocks[0] -= 1

        monkeypatch.setattr(controller, "record", fail_after_record)
    else:
        clocks[0] -= 1
    exchange, keep_serving = operate(controller, monkeypatch, Method.ISSUE, 60)
    assert not keep_serving and controller.admission.grant is None
    exchange.sendall.assert_not_called()
    assert len(controller.journal) == 1


@pytest.mark.parametrize("method", [Method.ISSUE, Method.REVOKE])
@pytest.mark.parametrize("failure", [OSError, TimeoutError])
def test_failed_required_audit_never_changes_authority_or_sends_reply(monkeypatch, method, failure):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    grant = issue(controller, monkeypatch) if method is Method.REVOKE else None
    monkeypatch.setattr(operator, "_peer_uid", lambda _: REQUESTER)
    monkeypatch.setattr(controller, "record", MagicMock(side_effect=failure("journal failure")))
    exchange = connection(operator._frame_request(method, grant.grant_id if grant else 60))
    with pytest.raises(OSError, match="journal failure"):
        controller.handle_operator(exchange, operator.monotonic() + 2)
    exchange.sendall.assert_not_called()
    assert controller.admission.grant is grant and not controller.admission.grant_revoked


def test_disconnected_operator_can_inspect_effect_of_committed_issue(monkeypatch):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    monkeypatch.setattr(operator, "_peer_uid", lambda _: REQUESTER)
    exchange = connection(operator._frame_request(Method.ISSUE, 60))
    exchange.sendall.side_effect = BrokenPipeError
    assert controller.handle_operator(exchange, operator.monotonic() + 2)
    grant = controller.admission.grant
    assert grant is not None and controller.journal[-1].grant_id == grant.grant_id
    inspected, _ = operate(controller, monkeypatch, Method.INSPECT)
    assert inspected.sendall.call_args.args[0].startswith(operator._REPLY + bytes((State.ACTIVE,)))


def test_operator_transport_timeout_consumes_evidence_without_grant(monkeypatch):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    monkeypatch.setattr(operator, "_peer_uid", lambda _: REQUESTER)
    exchange = MagicMock()
    exchange.recv.side_effect = TimeoutError
    assert controller.handle_operator(exchange, operator.monotonic() + 2)
    assert controller.journal[-1].result is Result.TRANSPORT_ERROR and controller.admission.grant is None
    exchange.sendall.assert_not_called()


@pytest.fixture
def local_links(monkeypatch):
    """Use real AF_UNIX, with modeled deployment identities and path checks."""
    bound = Event()
    sockets = {}

    @contextmanager
    def listen(endpoint, _uid):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listening:
            # An abstract address keeps ordinary same-UID tests independent of /run layout.
            address = f"\0aao-43-{uuid4()}"
            listening.bind(address)
            listening.listen(1)
            listening.settimeout(2)
            sockets[endpoint] = address
            if len(sockets) == 2:
                bound.set()
            yield listening

    monkeypatch.setattr(operator, "_listen", listen)
    monkeypatch.setattr(operator, "_require_local_uid", lambda _: None)
    monkeypatch.setattr(operator, "_ACCEPT_SECONDS", 0.5)
    monkeypatch.setattr(operator, "_peer_uid", lambda _: REQUESTER)
    monkeypatch.setattr(delegation, "_require_peer", lambda *_: None)
    return bound, sockets


@contextmanager
def client(address, frame):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(4)
        connection.connect(address)
        if frame:
            connection.sendall(frame)
        connection.shutdown(socket.SHUT_WR)
        yield connection


def receive(connection):
    with connection.makefile("rb") as reply:
        return reply.read()


@pytest.mark.parametrize("first", ["admission", "revocation"])
def test_serial_controller_orders_admission_and_revocation_with_real_unix_sockets(monkeypatch, local_links, first):
    bound, addresses = local_links
    recording, proceed = Event(), Event()
    if first == "admission":
        target, method = delegation._Session, "record"
    else:
        target, method = operator._Controller, "record"
    record = getattr(target, method)

    def pause_during_record(self, *args):
        record(self, *args)
        if (first == "admission" and args[1].reason is Reason.ADMITTED) or (
            first == "revocation" and args[1] is Method.REVOKE
        ):
            recording.set()
            assert proceed.wait(2)

    monkeypatch.setattr(target, method, pause_during_record)
    with ThreadPoolExecutor(max_workers=1) as worker:
        server = worker.submit(operator.serve_managed_admission, AGENT, OPERATOR, UIDS, POSITIONS)
        assert bound.wait(2)
        with client(addresses[OPERATOR], operator._frame_request(Method.ISSUE, 60)) as issuer:
            reply = receive(issuer)
        grant_id = operator._VIEW.unpack(reply[len(operator._REPLY):])[1]
        revoke = operator._frame_request(Method.REVOKE, UUID(bytes=grant_id))
        primary = (addresses[AGENT], FRAME) if first == "admission" else (addresses[OPERATOR], revoke)
        waiting = (addresses[OPERATOR], revoke) if first == "admission" else (addresses[AGENT], FRAME)
        with client(*primary) as initial:
            assert recording.wait(2)
            try:
                with client(*waiting) as next_connection:
                    proceed.set()
                    first_reply, next_reply = receive(initial), receive(next_connection)
            finally:
                proceed.set()
        admitted_reply, revoke_reply = (first_reply, next_reply) if first == "admission" else (next_reply, first_reply)
        assert admitted_reply == (ALLOW if first == "admission" else DENY)
        assert revoke_reply.startswith(operator._REPLY + bytes((State.REVOKED,)))
        with client(addresses[AGENT], FRAME) as after:
            assert receive(after) == DENY
        evidence = server.result(timeout=4)
    assert evidence.admission[-1].reason is Reason.GRANT_REVOKED


def test_exhausting_either_link_bounds_both_listeners(monkeypatch, local_links):
    bound, addresses = local_links
    monkeypatch.setattr(delegation, "_MAX_CONNECTIONS", 3)
    with ThreadPoolExecutor(max_workers=1) as worker:
        server = worker.submit(operator.serve_managed_admission, AGENT, OPERATOR, UIDS, POSITIONS)
        assert bound.wait(2)
        for _ in range(3):
            with client(addresses[OPERATOR], b"") as connection:
                assert receive(connection) == b""
        assert len(server.result(timeout=4).operator) == 3
    for address in addresses.values():
        with pytest.raises(OSError) as failure, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.connect(address)
        assert failure.value.errno == errno.ECONNREFUSED


def test_partial_listener_failure_unwinds_first_listener(monkeypatch):
    closed = []

    @contextmanager
    def fail_second(endpoint, _uid):
        if endpoint is OPERATOR:
            raise OSError("operator listen failed")
        try:
            yield object()
        finally:
            closed.append(endpoint)

    monkeypatch.setattr(operator, "_listen", fail_second)
    monkeypatch.setattr(operator, "_require_local_uid", lambda _: None)
    with pytest.raises(OSError, match="operator listen failed"):
        operator.serve_managed_admission(AGENT, OPERATOR, UIDS, POSITIONS)
    assert closed == [AGENT]


@pytest.mark.parametrize("channel", ["operator", "agent"])
def test_required_journal_failure_terminates_both_listeners(monkeypatch, local_links, channel):
    bound, addresses = local_links
    target = operator._Controller if channel == "operator" else delegation._Session
    monkeypatch.setattr(target, "record", MagicMock(side_effect=TimeoutError("required journal failed")))
    endpoint, frame = (OPERATOR, operator._frame_request(Method.ISSUE, 60)) if channel == "operator" else (AGENT, FRAME)
    with ThreadPoolExecutor(max_workers=1) as worker:
        server = worker.submit(operator.serve_managed_admission, AGENT, OPERATOR, UIDS, POSITIONS)
        assert bound.wait(2)
        with client(addresses[endpoint], frame) as connection:
            assert receive(connection) == b""
        with pytest.raises(TimeoutError, match="required journal failed"):
            server.result(timeout=4)
    for address in addresses.values():
        with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.connect(address)


def test_deadline_after_record_keeps_notice_but_does_not_install_issue(monkeypatch):
    controller = operator._Controller(UIDS.agent, POSITIONS)
    monkeypatch.setattr(operator, "_remaining", MagicMock(side_effect=TimeoutError))
    exchange, keep_serving = operate(controller, monkeypatch, Method.ISSUE, 60)
    assert keep_serving and controller.admission.grant is None
    assert controller.journal[-1].result is Result.ACCEPTED
    exchange.sendall.assert_not_called()


@pytest.mark.parametrize(("method", "argument"), [
    (Method.ISSUE, True), (Method.ISSUE, 0), (Method.ISSUE, 901),
    (Method.INSPECT, "administrator"), (Method.REVOKE, "grant"), (1, 60),
])
def test_typed_operator_client_rejects_invalid_arguments_before_connect(monkeypatch, method, argument):
    connect = MagicMock()
    monkeypatch.setattr(operator, "_connect_as", connect)
    with pytest.raises(boundary.ProtocolError):
        operator.request_grant(OPERATOR, REQUESTER, UIDS.control, method, argument)
    connect.assert_not_called()


def test_typed_operator_client_sends_once_and_decodes_current_grant(monkeypatch):
    view = operator.GrantView(State.ACTIVE, uuid4(), uuid4(), uuid4(), 100 * SECOND, UIDS.agent)
    exchange = connection(operator._grant_frame(view))
    connected = []

    @contextmanager
    def connect(endpoint, client_uid, server_uid):
        connected.append((endpoint, client_uid, server_uid))
        yield exchange, operator.monotonic() + 2

    monkeypatch.setattr(operator, "_connect_as", connect)
    assert operator.request_grant(OPERATOR, REQUESTER, UIDS.control, Method.ISSUE, 60) == view
    assert connected == [(OPERATOR, REQUESTER, UIDS.control)]
    exchange.sendall.assert_called_once_with(operator._REQUEST + b"\x01\x00<")
