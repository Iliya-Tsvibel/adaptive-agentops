from contextlib import contextmanager
from datetime import datetime, timezone
import os
import sqlite3
from unittest.mock import Mock
from uuid import uuid4

import pytest

from adaptive_agentops import actions
from adaptive_agentops.identity import LOCAL_APPROVER_ID
from adaptive_agentops.policy import PolicyDecision
from adaptive_agentops.stage4_boundary import Endpoint, IdentityError, Link, TrustedUIDs, monotonic
from adaptive_agentops.stage4_boundary import controller_state as state, delegation, dispatch_state as dispatch
from adaptive_agentops.stage4_boundary import hardened, operator, target_state
from adaptive_agentops.tool_requests import MockRestartServiceProposal


CONTROL = os.getuid()
AGENT, TARGET, REQUESTER, APPROVER = 61001, 61003, 61004, 61005
TARGET_SOCKET = Endpoint(Link.CONTROL_TARGET, "/run/stage47-test/target/effect.sock", 61013)
OPERATORS = (
    operator.OperatorIdentity(REQUESTER, "local-requester", frozenset({operator.OperatorRole.REQUESTER})),
    operator.OperatorIdentity(APPROVER, LOCAL_APPROVER_ID, frozenset({operator.OperatorRole.APPROVER})),
)
PROPOSAL = MockRestartServiceProposal(tool="mock.restart_service", resource="demo-api", arguments={})
PROPOSAL_FRAME = hardened._PROPOSAL + PROPOSAL.model_dump_json().encode()
CLOCK_NS = 1_000_000_000


@pytest.fixture
def clock(monkeypatch):
    wall = int(datetime.now(timezone.utc).timestamp() * CLOCK_NS)
    sample = [wall, 50 * CLOCK_NS]
    monkeypatch.setattr(delegation, "_read_clocks", lambda: tuple(sample))
    return sample


@pytest.fixture
def profile(tmp_path, monkeypatch, clock):
    assert CONTROL != 0
    monkeypatch.setattr(state, "_check_ancestor", lambda _: None)
    directory = tmp_path / "controller"
    directory.mkdir(mode=0o700)
    hardened.provision_hardened_state(directory, CONTROL)
    return directory


@contextmanager
def controller(profile):
    with state._open_controller_state(profile, CONTROL, schema=hardened._schema(), version=hardened._VERSION) as store:
        ctrl = hardened._Controller(TrustedUIDs(AGENT, CONTROL, TARGET), OPERATORS, TARGET_SOCKET, store)
        ctrl.start(OPERATORS)
        yield ctrl, store._connection


def operator_exchange(monkeypatch, ctrl, uid, method, argument=None):
    if type(method) is operator.GrantMethod:
        frame = operator._frame_request(method, argument)
    else:
        frame = hardened._OPERATOR + bytes((method,)) + (b"" if argument is None else argument)
    stream = Mock(recv=Mock(side_effect=[frame, b""]))
    monkeypatch.setattr(operator, "_peer_uid", lambda _: uid)
    assert ctrl.handle_operator(stream, monotonic() + 2)
    return stream.sendall.call_args.args[0] if stream.sendall.called else None


def proposal(monkeypatch, ctrl, frame=PROPOSAL_FRAME, *, authenticated=True):
    def verify(_stream, expected):
        assert expected == AGENT
        if not authenticated:
            raise IdentityError("denied test UID")

    monkeypatch.setattr(hardened, "_require_peer", verify)
    stream = Mock(recv=Mock(side_effect=[frame, b""]))
    assert ctrl.handle_agent(stream, monotonic() + 2)
    return stream.sendall.call_args.args[0] if stream.sendall.called else None


def pending(monkeypatch, ctrl, ttl=60):
    assert operator_exchange(monkeypatch, ctrl, REQUESTER, operator.GrantMethod.ISSUE, ttl) != operator._DENIED
    assert proposal(monkeypatch, ctrl) == hardened._PROPOSAL_REPLY + b"\x01"
    reply = operator_exchange(monkeypatch, ctrl, APPROVER, hardened.ActionMethod.INSPECT_ACTION)
    return actions.PendingRestartAction.model_validate_json(reply[len(hardened._OPERATOR_REPLY):])


def action_exchange(monkeypatch, ctrl, method, action, *, uid=APPROVER):
    return operator_exchange(monkeypatch, ctrl, uid, method, hardened._action_argument(action))


def target_connection(monkeypatch):
    connections = []

    @contextmanager
    def connect(endpoint, uids):
        assert endpoint == TARGET_SOCKET and uids.target == TARGET
        connections.append(Mock())
        yield connections[-1], monotonic() + 2

    monkeypatch.setattr(dispatch, "_connect_verified", connect)
    return connections


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def advance(clock, seconds):
    clock[:] = [value + seconds * CLOCK_NS for value in clock]


def test_live_approval_and_atomic_reservation_before_one_send(profile, monkeypatch, clock):
    advance(clock, 1)
    sent = []
    connections = target_connection(monkeypatch)
    database = profile / state.DATABASE_NAME

    def exchange(connection, deadline, envelope, *, clock):
        with sqlite3.connect(database) as observer:
            reservation = observer.execute(
                "SELECT action_id, approval_id, action_fingerprint FROM dispatch_reservations"
            ).fetchone()
            approval = observer.execute("SELECT approval_id, consumed FROM approvals").fetchone()
            evidence = observer.execute("SELECT kind FROM dispatch_events").fetchall()
            digest = observer.execute("SELECT request_digest FROM dispatch_effect_evidence").fetchone()[0]
        assert connection is connections[0]
        assert reservation == (str(envelope.action_id), str(envelope.approval_id), envelope.action_fingerprint)
        assert approval == (reservation[1], 1) and evidence == [("RESERVED",)]
        key = target_state._request_key(envelope)
        assert key.request_digest == digest
        sent.append(key)
        return target_state.EffectReceipt(**key.model_dump(), effect_number=1)

    monkeypatch.setattr(target_state, "_exchange", exchange)
    with controller(profile) as (ctrl, conn):
        action = pending(monkeypatch, ctrl)
        assert count(conn, "approvals") == 0
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.DISPATCH, action) == operator._DENIED
        assert not connections
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.APPROVE, action) == hardened._OPERATOR_REPLY + b"APPROVED"
        assert not connections and count(conn, "dispatch_reservations") == 0
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.DISPATCH, action) == (
            hardened._OPERATOR_REPLY + b"SIMULATED_EFFECT_COMMITTED"
        )
        assert conn.execute("SELECT effect_number FROM dispatch_effect_evidence").fetchone() == (1,)
        assert conn.execute("SELECT peer_uid, decision FROM controller_events WHERE method = 'APPROVE'").fetchall() == [
            (APPROVER, "APPROVED"),
        ]
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.DISPATCH, action) == operator._DENIED
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.APPROVE, action) == operator._DENIED
        assert proposal(monkeypatch, ctrl) == hardened._PROPOSAL_REPLY + b"\x00"
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE actions SET arguments = 'different'")
    assert len(connections) == len(sent) == 1


def test_missing_denied_forged_and_rejected_operations(profile, monkeypatch, clock):
    advance(clock, 1)
    with controller(profile) as (ctrl, conn):
        assert proposal(monkeypatch, ctrl) == hardened._PROPOSAL_REPLY + b"\x00"
        assert operator_exchange(monkeypatch, ctrl, APPROVER, operator.GrantMethod.ISSUE, 60) == operator._DENIED
        operator_exchange(monkeypatch, ctrl, REQUESTER, operator.GrantMethod.ISSUE, 60)
        assert proposal(monkeypatch, ctrl, authenticated=False) is None
        untrusted = PROPOSAL_FRAME[:-1] + b',"requester_id":"local-requester"}'
        assert proposal(monkeypatch, ctrl, untrusted) is None
        with monkeypatch.context() as denial:
            denial.setattr(actions, "evaluate_policy", lambda *_: PolicyDecision.DENY)
            assert proposal(monkeypatch, ctrl) == hardened._PROPOSAL_REPLY + b"\x00"
        assert proposal(monkeypatch, ctrl) == hardened._PROPOSAL_REPLY + b"\x01"
        action = ctrl._pending()
        binding = hardened._action_argument(action)
        for uid, argument, expected in (
            (REQUESTER, binding, operator._DENIED),
            (AGENT, binding, None),
            (APPROVER, uuid4().bytes + binding[16:], operator._DENIED),
            (APPROVER, binding[:16] + bytes(32), operator._DENIED),
        ):
            assert operator_exchange(monkeypatch, ctrl, uid, hardened.ActionMethod.APPROVE, argument) == expected
        assert count(conn, "approvals") == 0
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.REJECT, action) == hardened._OPERATOR_REPLY + b"REJECTED"
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.APPROVE, action) == operator._DENIED
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.DISPATCH, action) == operator._DENIED
        assert count(conn, "dispatch_reservations") == 0


@pytest.mark.parametrize("loss", ["expiry", "revocation", "wrong-target", "fresh-policy-denial"])
def test_dispatch_rechecks_authority(profile, monkeypatch, clock, loss):
    advance(clock, 1)
    sent = Mock()
    monkeypatch.setattr(target_state, "_exchange", sent)
    with controller(profile) as (ctrl, conn):
        action = pending(monkeypatch, ctrl, ttl=10)
        action_exchange(monkeypatch, ctrl, hardened.ActionMethod.APPROVE, action)
        connections = target_connection(monkeypatch)
        if loss == "expiry":
            advance(clock, 10)
        elif loss == "revocation":
            operator_exchange(monkeypatch, ctrl, REQUESTER, operator.GrantMethod.REVOKE, ctrl.admission.grant.grant_id)
        elif loss == "wrong-target":
            monkeypatch.setattr(dispatch, "_connect_verified", Mock(side_effect=IdentityError("wrong peer")))
        if loss == "fresh-policy-denial":
            monkeypatch.setattr(dispatch, "evaluate_policy", lambda *_: PolicyDecision.DENY)
            with pytest.raises(state.ControllerStateError, match="policy"):
                action_exchange(monkeypatch, ctrl, hardened.ActionMethod.DISPATCH, action)
        else:
            assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.DISPATCH, action) == operator._DENIED
            assert not connections
        assert count(conn, "dispatch_reservations") == 0
        assert conn.execute("SELECT consumed FROM approvals").fetchone() == (0,)
        sent.assert_not_called()


@pytest.mark.parametrize("phase", ["proposal", "approval", "reservation"])
def test_required_audit_failure_rolls_back_authority(profile, monkeypatch, clock, phase):
    advance(clock, 1)
    sent = Mock()
    monkeypatch.setattr(target_state, "_exchange", sent)
    target_connection(monkeypatch)
    with controller(profile) as (ctrl, conn):
        if phase == "proposal":
            operator_exchange(monkeypatch, ctrl, REQUESTER, operator.GrantMethod.ISSUE, 60)
            operation = lambda: proposal(monkeypatch, ctrl)
        else:
            action = pending(monkeypatch, ctrl)
            method = hardened.ActionMethod.APPROVE
            if phase == "reservation":
                action_exchange(monkeypatch, ctrl, method, action)
                method = hardened.ActionMethod.DISPATCH
            operation = lambda: action_exchange(monkeypatch, ctrl, method, action)

        if phase == "reservation":
            monkeypatch.setattr(dispatch, "_record", Mock(side_effect=state.ControllerStateError("audit unavailable")))
        else:
            monkeypatch.setattr(ctrl._store, "_record", Mock(side_effect=state.ControllerStateError("audit unavailable")))
        with pytest.raises(state.ControllerStateError, match="audit unavailable"):
            operation()
        assert count(conn, "dispatch_reservations") == 0
        assert count(conn, "actions") == (phase != "proposal")
        assert conn.execute("SELECT consumed FROM approvals").fetchall() == ([(0,)] if phase == "reservation" else [])
        sent.assert_not_called()


@pytest.mark.parametrize("uncertain_phase", ["APPROVE", "DISPATCH"])
def test_post_commit_error_never_sends_or_recovers_permit(profile, monkeypatch, clock, uncertain_phase):
    advance(clock, 1)
    sent = Mock()
    monkeypatch.setattr(target_state, "_exchange", sent)
    target_connection(monkeypatch)

    with controller(profile) as (ctrl, conn):
        action = pending(monkeypatch, ctrl)
        if uncertain_phase == "DISPATCH":
            action_exchange(monkeypatch, ctrl, hardened.ActionMethod.APPROVE, action)
        commit = state._commit

        def uncertain(connection):
            commit(connection)
            if uncertain_phase == "DISPATCH" and not count(connection, "dispatch_reservations"):
                return
            raise sqlite3.OperationalError("commit acknowledgment lost")

        with monkeypatch.context() as failure:
            failure.setattr(state, "_commit", uncertain)
            with pytest.raises(state.ControllerStateError, match="uncertain"):
                action_exchange(monkeypatch, ctrl, hardened.ActionMethod[uncertain_phase], action)
        assert count(conn, "dispatch_reservations") == (uncertain_phase == "DISPATCH")
        assert conn.execute("SELECT consumed FROM approvals").fetchone() == (int(uncertain_phase == "DISPATCH"),)
    with controller(profile) as (restarted, conn):
        assert action_exchange(monkeypatch, restarted, hardened.ActionMethod.DISPATCH, action) == operator._DENIED
        assert conn.execute("SELECT state FROM controller_grants").fetchone() == ("INVALIDATED",)
    sent.assert_not_called()


def test_lost_receipt_spends_approval_and_preserves_exact_unknown(profile, monkeypatch, clock):
    advance(clock, 1)
    connections = target_connection(monkeypatch)
    send = Mock(side_effect=OSError("receipt lost"))
    monkeypatch.setattr(target_state, "_exchange", send)
    with controller(profile) as (ctrl, conn):
        action = pending(monkeypatch, ctrl)
        action_exchange(monkeypatch, ctrl, hardened.ActionMethod.APPROVE, action)
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.DISPATCH, action) == hardened._OPERATOR_REPLY + b"UNKNOWN"
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.DISPATCH, action) == operator._DENIED
        digest, number = conn.execute("SELECT request_digest, effect_number FROM dispatch_effect_evidence").fetchone()
        assert len(digest) == 64 and number is None
        assert conn.execute("SELECT status FROM dispatch_outcomes").fetchone() == ("UNKNOWN",)
        assert conn.execute("SELECT consumed FROM approvals").fetchone() == (1,)
    assert len(connections) == send.call_count == 1


def test_hardened_and_legacy_state_profiles_are_distinct(profile):
    with pytest.raises(state.ControllerStateError, match="unexpected application ID, schema or version"):
        with state._open_controller_state(profile, CONTROL):
            pytest.fail("legacy profile accepted hardened state")


@pytest.mark.parametrize("phase", ["proposal", "approval"])
def test_grant_expiring_during_decision_rolls_it_back(profile, monkeypatch, clock, phase):
    advance(clock, 1)
    with controller(profile) as (ctrl, conn):
        if phase == "proposal":
            operator_exchange(monkeypatch, ctrl, REQUESTER, operator.GrantMethod.ISSUE, 10)
            primitive = "insert_pending_restart_action"
            operation = lambda: proposal(monkeypatch, ctrl)
        else:
            action = pending(monkeypatch, ctrl, ttl=10)
            primitive = "record_operator_decision"
            operation = lambda: action_exchange(monkeypatch, ctrl, hardened.ActionMethod.APPROVE, action)
        record = getattr(hardened, primitive)

        def expire_after_primitive(*args):
            result = record(*args)
            advance(clock, 10)
            return result

        monkeypatch.setattr(hardened, primitive, expire_after_primitive)
        with pytest.raises(state.ControllerStateError, match="decision boundary"):
            operation()
        assert count(conn, "actions") == (phase == "approval")
        assert count(conn, "approvals") == count(conn, "dispatch_reservations") == 0


def test_grant_expiring_after_confirmed_approval_commit_suppresses_reply(profile, monkeypatch, clock):
    advance(clock, 1)
    with controller(profile) as (ctrl, conn):
        action = pending(monkeypatch, ctrl, ttl=10)
        commit = state._commit

        def expire_after_commit(connection):
            commit(connection)
            if ctrl.admission.grant.is_live(tuple(clock)):
                advance(clock, 10)

        monkeypatch.setattr(state, "_commit", expire_after_commit)
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.APPROVE, action) is None
        assert conn.execute("SELECT decision, consumed FROM approvals").fetchall() == [("APPROVED", 0)]
        assert conn.execute("SELECT state FROM controller_grants").fetchall() == [("EXPIRED",)]
        assert action_exchange(monkeypatch, ctrl, hardened.ActionMethod.DISPATCH, action) == operator._DENIED
        assert count(conn, "dispatch_reservations") == 0
