"""Internal, no-effect Stage 4.5 authorization fixture with one committed send."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

from adaptive_agentops.actions import PendingRestartAction, _fingerprint
from adaptive_agentops.approvals import ApprovalDecision, RestartApproval
from adaptive_agentops.identity import LOCAL_APPROVER_ID
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import MockRestartServiceProposal

from . import BoundaryError, Endpoint, TrustedUIDs, _connect_verified, _remaining
from . import controller_state, delegation
from .dispatch_wire import DispatchEnvelope, _exchange_reserved, _MAX_WINDOW_NS, _require_dispatch_link
from .operator import OperatorIdentity, OperatorRole


_TOOL = "mock.restart_service"
_RESOURCE = "demo-api"
_POLICY_VERSION = "local-stage4-v1"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_NS = 1_000_000_000
_FIXTURE_LIFETIME_NS = 60 * _NS
_SCHEMA_VERSION = 2
_SCHEMA = (*controller_state._SCHEMA,
    """CREATE TABLE dispatch_grants (
    grant_id TEXT PRIMARY KEY NOT NULL CHECK (length(grant_id) = 36),
    session_id TEXT UNIQUE NOT NULL,
    run_id TEXT NOT NULL,
    issuer_uid INTEGER NOT NULL CHECK (issuer_uid BETWEEN 1 AND 4294967294),
    issuer_principal TEXT NOT NULL CHECK (issuer_principal = 'local-requester'),
    agent_uid INTEGER NOT NULL CHECK (agent_uid BETWEEN 1 AND 4294967294),
    agent_id TEXT NOT NULL CHECK (agent_id = 'incident-investigator-v1'),
    tool TEXT NOT NULL CHECK (tool = 'mock.restart_service'),
    resource TEXT NOT NULL CHECK (resource = 'demo-api'),
    environment TEXT NOT NULL CHECK (environment = 'local-mock'),
    target_id TEXT NOT NULL CHECK (target_id = 'demo-api'),
    target_uid INTEGER NOT NULL CHECK (target_uid BETWEEN 1 AND 4294967294),
    target_path TEXT NOT NULL CHECK (length(target_path) BETWEEN 1 AND 107),
    target_gid INTEGER NOT NULL CHECK (target_gid BETWEEN 0 AND 4294967294),
    policy_version TEXT NOT NULL CHECK (policy_version = 'local-stage4-v1'),
    wall_expiry_ns INTEGER NOT NULL CHECK (wall_expiry_ns > 0),
    elapsed_expiry_ns INTEGER NOT NULL CHECK (elapsed_expiry_ns > 0),
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'EXPIRED', 'REVOKED', 'INVALIDATED')),
    FOREIGN KEY (session_id, run_id) REFERENCES controller_sessions(session_id, run_id),
    UNIQUE (session_id, grant_id)
) STRICT""",
    """CREATE TABLE dispatch_actions (
    action_id TEXT PRIMARY KEY NOT NULL CHECK (length(action_id) = 36),
    session_id TEXT UNIQUE NOT NULL,
    grant_id TEXT UNIQUE NOT NULL,
    payload TEXT NOT NULL CHECK (length(payload) BETWEEN 1 AND 2048),
    FOREIGN KEY (session_id, grant_id) REFERENCES dispatch_grants(session_id, grant_id),
    UNIQUE (session_id, action_id, grant_id)
) STRICT""",
    """CREATE TABLE dispatch_approvals (
    approval_id TEXT PRIMARY KEY NOT NULL CHECK (length(approval_id) = 36),
    action_id TEXT UNIQUE NOT NULL REFERENCES dispatch_actions(action_id),
    session_id TEXT UNIQUE NOT NULL REFERENCES controller_sessions(session_id),
    approver_uid INTEGER NOT NULL CHECK (approver_uid BETWEEN 1 AND 4294967294),
    payload TEXT NOT NULL CHECK (length(payload) BETWEEN 1 AND 2048),
    consumed INTEGER NOT NULL CHECK (consumed IN (0, 1)),
    UNIQUE (session_id, action_id, approval_id)
) STRICT""",
    """CREATE TABLE dispatch_reservations (
    dispatch_id TEXT PRIMARY KEY NOT NULL CHECK (length(dispatch_id) = 36),
    action_id TEXT UNIQUE NOT NULL,
    approval_id TEXT UNIQUE NOT NULL,
    session_id TEXT UNIQUE NOT NULL,
    grant_id TEXT UNIQUE NOT NULL,
    action_fingerprint TEXT NOT NULL CHECK (length(action_fingerprint) = 64),
    target_id TEXT NOT NULL CHECK (target_id = 'demo-api'),
    reserved_wall_ns INTEGER NOT NULL CHECK (reserved_wall_ns > 0),
    reserved_elapsed_ns INTEGER NOT NULL CHECK (reserved_elapsed_ns > 0),
    FOREIGN KEY (session_id, action_id, grant_id) REFERENCES dispatch_actions(session_id, action_id, grant_id),
    FOREIGN KEY (session_id, action_id, approval_id) REFERENCES dispatch_approvals(session_id, action_id, approval_id),
    UNIQUE (session_id, action_id, dispatch_id)
) STRICT""",
    """CREATE TABLE dispatch_events (
    event_id TEXT PRIMARY KEY NOT NULL CHECK (length(event_id) = 36),
    session_id TEXT NOT NULL REFERENCES controller_sessions(session_id),
    action_id TEXT NOT NULL REFERENCES dispatch_actions(action_id),
    dispatch_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('FIXTURE_CREATED', 'RESERVED', 'VALIDATED_NO_EFFECT', 'UNKNOWN',
                                       'RESERVED_NO_SEND', 'INVALIDATED')),
    FOREIGN KEY (session_id, action_id, dispatch_id) REFERENCES dispatch_reservations(session_id, action_id, dispatch_id),
    UNIQUE (session_id, kind)
) STRICT""",
    """CREATE TABLE dispatch_outcomes (
    dispatch_id TEXT PRIMARY KEY NOT NULL REFERENCES dispatch_reservations(dispatch_id),
    status TEXT NOT NULL CHECK (status IN ('VALIDATED_NO_EFFECT', 'UNKNOWN', 'RESERVED_NO_SEND'))
) STRICT""",
)


_EFFECT_SCHEMA_VERSION = 3
_EFFECT_SCHEMA = (
    *(ddl.replace("'VALIDATED_NO_EFFECT'", "'SIMULATED_EFFECT_COMMITTED'") for ddl in _SCHEMA),
    """CREATE TABLE dispatch_effect_evidence (
    dispatch_id TEXT PRIMARY KEY NOT NULL REFERENCES dispatch_reservations(dispatch_id),
    request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
    effect_number INTEGER CHECK (effect_number > 0)
) STRICT""",
)


class DispatchStatus(str, Enum):
    VALIDATED_NO_EFFECT = "VALIDATED_NO_EFFECT"
    SIMULATED_EFFECT_COMMITTED = "SIMULATED_EFFECT_COMMITTED"
    UNKNOWN = "UNKNOWN"
    RESERVED_NO_SEND = "RESERVED_NO_SEND"


class DispatchFixtureUsedError(controller_state.ControllerStateError):
    """Existing fixture state cannot yield a new grant or a recovered permit."""


@dataclass(frozen=True, slots=True)
class _Fixture:
    session_id: UUID
    grant_id: UUID
    action: PendingRestartAction
    approval: RestartApproval
    approver_uid: int
    grant_values: tuple
    wall_expiry: int
    elapsed_expiry: int


@dataclass(frozen=True, slots=True)
class _Reservation:
    dispatch_id: UUID
    values: tuple


class _Permit:
    """One in-process use on the exact previously verified target socket."""

    def __init__(self, connection, reservation):
        self._connection = connection
        self._reservation = reservation

    def consume(self, connection):
        if self._reservation is None or connection is not self._connection:
            raise controller_state.ControllerStateError("Dispatch permit is unavailable on this connection")
        reservation, self._reservation = self._reservation, None
        return reservation


def provision_dispatch_fixture_state(state_dir: str | Path, control_uid: int) -> None:
    """Provision explicitly empty protected state for the isolated dispatch fixture."""
    controller_state._provision_controller_state(state_dir, control_uid, _SCHEMA, _SCHEMA_VERSION)


def provision_effect_fixture_state(state_dir: str | Path, control_uid: int) -> None:
    """Provision explicitly empty Controller state for the simulated-effect fixture."""
    controller_state._provision_controller_state(state_dir, control_uid, _EFFECT_SCHEMA, _EFFECT_SCHEMA_VERSION)


def _ns(value: datetime) -> int:
    delta = value - _EPOCH
    return (delta.days * 86400 + delta.seconds) * _NS + delta.microseconds * 1000


def _record(store, session_id, action_id, dispatch_id, kind):
    conn = store._connection
    count = conn.execute("SELECT (SELECT count(*) FROM controller_events) + (SELECT count(*) FROM dispatch_events)").fetchone()[0]
    if count >= controller_state._MAX_EVENTS:
        raise controller_state.ControllerStateError("Dispatch evidence capacity exhausted")
    conn.execute("INSERT INTO dispatch_events VALUES (?, ?, ?, ?, ?)", (
        str(uuid4()), str(session_id), str(action_id), str(dispatch_id) if dispatch_id else None, kind,
    ))


def _require_unused(store):
    conn = store._connection
    prior = conn.execute("SELECT session_id FROM controller_sessions").fetchone()
    if prior is None:
        return
    # A prior row is history only, even when the previous Controller had
    # failed before reservation or left a confirmed reservation without a reply.
    with store._write():
        active = conn.execute("SELECT session_id FROM controller_sessions WHERE state = 'ACTIVE'").fetchone()
        if active is not None:
            (session_id,) = active
            action = conn.execute("SELECT action_id FROM dispatch_actions WHERE session_id = ?", (session_id,)).fetchone()
            conn.execute("UPDATE dispatch_grants SET state = 'INVALIDATED' WHERE session_id = ? AND state = 'ACTIVE'",
                         (session_id,))
            conn.execute("UPDATE controller_sessions SET state = 'INVALIDATED' WHERE session_id = ?", (session_id,))
            if action is not None:
                _record(store, session_id, action[0], None, "INVALIDATED")
    raise DispatchFixtureUsedError("Existing dispatch fixture state cannot authorize a new attempt")


def _prepare_fixture(store, session, endpoint, uids, requester_uid, approver_uid):
    wall, elapsed = session.clock.sample()
    if min(wall, elapsed) <= 0:
        raise controller_state.ControllerStateError("Dispatch fixture requires valid wall and elapsed clocks")
    now = _EPOCH + timedelta(microseconds=wall // 1000)
    context = session.context
    scope = {
        "run_id": str(context.run_id), "requester_id": context.requester_id,
        "agent_id": context.agent_id, "tool": _TOOL, "resource": _RESOURCE,
        "environment": context.environment, "arguments": {},
    }
    action = PendingRestartAction(
        **{**scope, "run_id": context.run_id}, action_id=uuid4(), action_fingerprint=_fingerprint(scope),
        created_at=now, expires_at=now + timedelta(minutes=10), state="PENDING",
    )
    approval = RestartApproval(
        approval_id=uuid4(), action_id=action.action_id, run_id=action.run_id, requester_id=action.requester_id,
        agent_id=action.agent_id, approver_id=LOCAL_APPROVER_ID, action_fingerprint=action.action_fingerprint,
        decision=ApprovalDecision.APPROVED, created_at=now, expires_at=action.expires_at, consumed=False,
    )
    grant_id = uuid4()
    wall_expiry, elapsed_expiry = wall + _FIXTURE_LIFETIME_NS, elapsed + _FIXTURE_LIFETIME_NS
    grant_values = (
        str(grant_id), str(session.session_id), str(action.run_id), requester_uid, action.requester_id,
        uids.agent, action.agent_id, action.tool, action.resource, action.environment,
        _RESOURCE, uids.target, endpoint.path, endpoint.access_gid, _POLICY_VERSION, wall_expiry, elapsed_expiry, "ACTIVE",
    )
    with store._write():
        conn = store._connection
        store._verify_current_session(session.session_id)
        conn.execute("INSERT INTO dispatch_grants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     grant_values)
        conn.execute("INSERT INTO dispatch_actions VALUES (?, ?, ?, ?)", (
            str(action.action_id), str(session.session_id), str(grant_id), action.model_dump_json(),
        ))
        conn.execute("INSERT INTO dispatch_approvals VALUES (?, ?, ?, ?, ?, 0)", (
            str(approval.approval_id), str(action.action_id), str(session.session_id), approver_uid,
            approval.model_dump_json(),
        ))
        _record(store, session.session_id, action.action_id, None, "FIXTURE_CREATED")
    return _Fixture(session.session_id, grant_id, action, approval, approver_uid, grant_values, wall_expiry, elapsed_expiry)


def _load_fixture(store, fixture, context):
    conn = store._connection
    session_id = str(fixture.session_id)
    store._verify_current_session(fixture.session_id)
    grant = conn.execute("SELECT * FROM dispatch_grants WHERE session_id = ?", (session_id,)).fetchone()
    pending = conn.execute(
        "SELECT action_id, grant_id, payload FROM dispatch_actions WHERE session_id = ?", (session_id,),
    ).fetchone()
    approved = conn.execute(
        "SELECT approval_id, action_id, approver_uid, payload, consumed FROM dispatch_approvals WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if (
        grant != fixture.grant_values or pending is None or approved is None
        or pending[:2] != (str(fixture.action.action_id), str(fixture.grant_id))
        or approved[:3] != (str(fixture.approval.approval_id), str(fixture.action.action_id), fixture.approver_uid)
        or approved[-1] != 0
        or conn.execute("SELECT 1 FROM dispatch_reservations WHERE session_id = ?", (session_id,)).fetchone()
    ):
        raise controller_state.ControllerStateError("Dispatch requires an exact unconsumed current-session fixture")
    try:
        action = PendingRestartAction.model_validate_json(pending[-1], strict=True)
        approval = RestartApproval.model_validate_json(approved[-2], strict=True)
    except ValueError:
        raise controller_state.ControllerStateError("Dispatch action or approval could not be strictly validated") from None
    if (
        action != fixture.action or approval != fixture.approval or action.model_dump_json() != pending[-1]
        or approval.model_dump_json() != approved[-2] or approval.decision is not ApprovalDecision.APPROVED
        or approval.consumed or (action.run_id, action.requester_id, action.agent_id, action.environment)
        != (context.run_id, context.requester_id, context.agent_id, context.environment)
        or (approval.action_id, approval.run_id, approval.requester_id, approval.agent_id,
            approval.action_fingerprint, approval.expires_at)
        != (action.action_id, action.run_id, action.requester_id, action.agent_id,
            action.action_fingerprint, action.expires_at)
        or store._operators.get(grant[3]) != (context.requester_id, ("requester",))
        or store._operators.get(fixture.approver_uid) != (approval.approver_id, ("approver",))
    ):
        raise controller_state.ControllerStateError("Dispatch action, approval or protected grant binding differs")
    return action, approval


def _reserve_fixture(store, session, fixture, connection, deadline):
    conn = store._connection
    with store._write():
        action, approval = _load_fixture(store, fixture, session.context)
        proposal = MockRestartServiceProposal.model_validate({
            "tool": action.tool, "resource": action.resource, "arguments": action.arguments.model_dump(),
        }, strict=True)
        try:
            decision = evaluate_policy(session.context, proposal)
        except Exception:
            raise controller_state.ControllerStateError("Dispatch policy evaluation failed") from None
        if decision is not PolicyDecision.APPROVAL_REQUIRED:
            raise controller_state.ControllerStateError("Dispatch policy did not require the bound approval")
        wall, elapsed = session.clock.sample()
        _remaining(deadline)
        if not (
            _ns(action.created_at) <= _ns(approval.created_at) <= wall
            < min(_ns(action.expires_at), _ns(approval.expires_at), fixture.wall_expiry)
            and 0 < elapsed < fixture.elapsed_expiry
        ):
            raise controller_state.ControllerStateError("Dispatch fixture authority is expired or future-issued")

        dispatch_id = uuid4()
        values = (
            str(dispatch_id), str(action.action_id), str(approval.approval_id), str(session.session_id),
            str(fixture.grant_id), action.action_fingerprint, _RESOURCE, wall, elapsed,
        )
        consumed = conn.execute(
            "UPDATE dispatch_approvals SET consumed = 1 WHERE approval_id = ? AND action_id = ? "
            "AND session_id = ? AND consumed = 0",
            (str(approval.approval_id), str(action.action_id), str(session.session_id)),
        )
        if consumed.rowcount != 1:
            raise controller_state.ControllerStateError("Dispatch approval was not consumed exactly once")
        conn.execute("INSERT INTO dispatch_reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        _record(store, session.session_id, action.action_id, dispatch_id, "RESERVED")
    # Do not let other processes reconstruct this permit from durable rows.
    return _Permit(connection, _Reservation(dispatch_id, values))


def _envelope(session, fixture, reservation, deadline):
    wall, elapsed = session.clock.sample()
    _remaining(deadline)
    action, approval = fixture.action, fixture.approval
    return DispatchEnvelope(
        session_id=fixture.session_id, run_id=action.run_id, grant_id=fixture.grant_id,
        dispatch_id=reservation.dispatch_id, action_id=action.action_id, approval_id=approval.approval_id,
        requester_id=action.requester_id, agent_id=action.agent_id, target_id=_RESOURCE, policy_version=_POLICY_VERSION,
        tool=action.tool, resource=action.resource, environment=action.environment, arguments=action.arguments,
        fingerprint_version=1, action_fingerprint=action.action_fingerprint,
        issued_wall_ns=wall, issued_elapsed_ns=elapsed,
        not_after_wall_ns=min(wall + _MAX_WINDOW_NS, fixture.wall_expiry, _ns(action.expires_at), _ns(approval.expires_at)),
        not_after_elapsed_ns=min(elapsed + _MAX_WINDOW_NS, fixture.elapsed_expiry),
    )


def _record_outcome(store, fixture, reservation, status, receipt=None):
    conn = store._connection
    with store._write():
        store._verify_current_session(fixture.session_id)
        if conn.execute(
            "SELECT * FROM dispatch_reservations WHERE dispatch_id = ?", (str(reservation.dispatch_id),)
        ).fetchone() != reservation.values:
            raise controller_state.ControllerStateError("Dispatch reservation changed before outcome evidence")
        if receipt is not None:
            updated = conn.execute(
                "UPDATE dispatch_effect_evidence SET effect_number = ? "
                "WHERE dispatch_id = ? AND request_digest = ? AND effect_number IS NULL",
                (receipt.effect_number, str(reservation.dispatch_id), receipt.request_digest),
            )
            if updated.rowcount != 1:
                raise controller_state.ControllerStateError("Target receipt differs from the recorded request")
        conn.execute("INSERT INTO dispatch_outcomes VALUES (?, ?)", (str(reservation.dispatch_id), status.value))
        _record(store, fixture.session_id, fixture.action.action_id, reservation.dispatch_id, status.value)


def _record_target_request(store, fixture, key):
    with store._write():
        store._verify_current_session(fixture.session_id)
        store._connection.execute(
            "INSERT INTO dispatch_effect_evidence VALUES (?, ?, NULL)", (str(key.dispatch_id), key.request_digest),
        )


def _coordinate(store, session, fixture, endpoint, uids, *, simulate=False):
    if simulate:
        from . import target_state

    receipt = None
    with _connect_verified(endpoint, uids) as (connection, deadline):
        permit = _reserve_fixture(store, session, fixture, connection, deadline)
        reservation = permit.consume(connection)
        try:
            envelope = _envelope(session, fixture, reservation, deadline)
            if simulate:
                _record_target_request(store, fixture, target_state._request_key(envelope))
        except (BoundaryError, TimeoutError, ValueError):
            status = DispatchStatus.RESERVED_NO_SEND
        else:
            try:
                if simulate:
                    receipt = target_state._exchange(connection, deadline, envelope, clock=session.clock)
                else:
                    _exchange_reserved(connection, deadline, envelope, clock=session.clock)
            except (BoundaryError, OSError, ValueError):
                status = DispatchStatus.UNKNOWN
            else:
                status = DispatchStatus.SIMULATED_EFFECT_COMMITTED if simulate else DispatchStatus.VALIDATED_NO_EFFECT
    try:
        _record_outcome(store, fixture, reservation, status, receipt)
    except controller_state.ControllerStateError:
        return DispatchStatus.UNKNOWN
    return status


def dispatch_fixture_once(
    state_dir: str | Path, endpoint: Endpoint, uids: TrustedUIDs, *, requester_uid: int, approver_uid: int,
) -> DispatchStatus:
    """Internal no-effect fixture entry. Earlier fixture state cannot authorize a new request."""
    return _dispatch_fixture(state_dir, endpoint, uids, requester_uid, approver_uid, simulate=False)


def dispatch_effect_fixture_once(
    state_dir: str | Path, endpoint: Endpoint, uids: TrustedUIDs, *, requester_uid: int, approver_uid: int,
) -> DispatchStatus:
    """Use the isolated effect profile with the same single Controller attempt."""
    return _dispatch_fixture(state_dir, endpoint, uids, requester_uid, approver_uid, simulate=True)


def _dispatch_fixture(state_dir, endpoint, uids, requester_uid, approver_uid, *, simulate):
    _require_dispatch_link(endpoint)
    requester = OperatorIdentity(requester_uid, "local-requester", frozenset({OperatorRole.REQUESTER}))
    approver = OperatorIdentity(approver_uid, LOCAL_APPROVER_ID, frozenset({OperatorRole.APPROVER}))
    if len({requester.uid, approver.uid, uids.agent, uids.control, uids.target}) != 5:
        raise ValueError("Dispatch fixture requires dedicated service, requester and approver UIDs")
    with controller_state._open_controller_state(
        state_dir, uids.control, schema=_EFFECT_SCHEMA if simulate else _SCHEMA,
        version=_EFFECT_SCHEMA_VERSION if simulate else _SCHEMA_VERSION,
    ) as store:
        _require_unused(store)
        session = delegation._Session(uids.agent, None)
        store.start(session, uids.control, uids.agent, (requester, approver), _TOOL, _RESOURCE)
        fixture = _prepare_fixture(store, session, endpoint, uids, requester.uid, approver.uid)
        return _coordinate(store, session, fixture, endpoint, uids, simulate=simulate)
