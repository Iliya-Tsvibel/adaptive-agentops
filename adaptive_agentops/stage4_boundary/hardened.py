"""Local restart acceptance over the protected agent, operator and target links."""

from contextlib import closing
from dataclasses import dataclass
from enum import IntEnum
import sqlite3
from uuid import UUID, uuid4

from adaptive_agentops.actions import (
    PendingRestartAction, get_pending_restart_action, initialize_action_schema, insert_pending_restart_action,
)
from adaptive_agentops.approvals import (
    ApprovalDecision, RestartApproval, get_approval_by_action_id, initialize_approval_schema,
    record_operator_decision,
)
from adaptive_agentops.identity import LOCAL_APPROVER_ID
from adaptive_agentops.tool_requests import MockRestartServiceProposal

from . import (
    IdentityError, Link, PathSecurityError, ProtocolError, _connect_as, _connect_verified, _require_local_uid,
    _require_peer, _remaining, _send_frame,
)
from . import controller_state as state, delegation, dispatch_state as dispatch, operator


_VERSION = 4
_PROPOSAL = b"AAO\x07\x01"
_PROPOSAL_REPLY = b"AAO\x07\x02"
_OPERATOR = b"AAO\x07\x03"
_OPERATOR_REPLY = b"AAO\x07\x04"
_MAX_ACTION_BYTES = 2048
_REQUEST_LIMIT = len(_OPERATOR) + 1 + 16 + 32


class ActionMethod(IntEnum):
    INSPECT_ACTION = 1
    APPROVE = 2
    REJECT = 3
    DISPATCH = 4


@dataclass(frozen=True, slots=True)
class _Authority:
    session_id: UUID
    grant_id: UUID
    action: PendingRestartAction
    approval: RestartApproval
    approver_uid: int
    wall_expiry: int
    elapsed_expiry: int
    target: tuple


def _schema():
    # Use the exact Stage 2 action and approval tables and approval guards.
    with closing(sqlite3.connect(":memory:")) as conn:
        initialize_action_schema(conn)
        initialize_approval_schema(conn)
        stage2 = tuple(sql for (sql,) in conn.execute("SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL"))
    tables = ("dispatch_reservations", "dispatch_events", "dispatch_outcomes", "dispatch_effect_evidence")
    dispatch_schema = (
        sql.replace("REFERENCES dispatch_actions(session_id, action_id, grant_id)",
                    "REFERENCES pending_bindings(session_id, action_id, grant_id)")
        .replace("FOREIGN KEY (session_id, action_id, approval_id) REFERENCES dispatch_approvals(session_id, action_id, approval_id)",
                 "FOREIGN KEY (approval_id) REFERENCES approvals(approval_id)")
        .replace("REFERENCES dispatch_actions(action_id)", "REFERENCES actions(action_id)")
        .replace("'FIXTURE_CREATED', ", "").replace(", 'INVALIDATED'", "")
        for sql in dispatch._EFFECT_SCHEMA if sql.startswith(tuple(f"CREATE TABLE {name} (" for name in tables))
    )
    return (*state._SCHEMA, *stage2,
        """CREATE TABLE session_targets (
    session_id TEXT PRIMARY KEY NOT NULL REFERENCES controller_sessions(session_id),
    target_uid INTEGER NOT NULL CHECK (target_uid BETWEEN 1 AND 4294967294),
    target_path TEXT NOT NULL CHECK (length(target_path) BETWEEN 1 AND 107),
    target_gid INTEGER NOT NULL CHECK (target_gid BETWEEN 0 AND 4294967294),
    policy_version TEXT NOT NULL CHECK (policy_version = 'local-stage4-v1')
) STRICT""",
        """CREATE TABLE pending_bindings (
    session_id TEXT PRIMARY KEY NOT NULL,
    grant_id TEXT UNIQUE NOT NULL,
    action_id TEXT UNIQUE NOT NULL REFERENCES actions(action_id),
    FOREIGN KEY (session_id, grant_id) REFERENCES controller_grants(session_id, grant_id),
    UNIQUE (session_id, action_id, grant_id)
) STRICT""",
        """CREATE TRIGGER hardened_action_insert BEFORE INSERT ON actions
WHEN EXISTS (SELECT 1 FROM actions WHERE action_id = NEW.action_id OR run_id = NEW.run_id)
BEGIN SELECT RAISE(ABORT, 'Pending action must be new'); END""",
        """CREATE TRIGGER hardened_action_update BEFORE UPDATE ON actions
BEGIN SELECT RAISE(ABORT, 'Pending action is immutable'); END""",
        """CREATE TRIGGER hardened_action_delete BEFORE DELETE ON actions
BEGIN SELECT RAISE(ABORT, 'Pending action cannot be deleted'); END""",
        *dispatch_schema,
    )


def provision_hardened_state(state_dir, control_uid):
    """Explicitly provision an empty protected Controller profile."""
    state._provision_controller_state(state_dir, control_uid, _schema(), _VERSION)


def _action_argument(action):
    if type(action) is not PendingRestartAction:
        raise ValueError("An inspected pending action is required")
    validated = PendingRestartAction.model_validate_json(action.model_dump_json())
    if action != validated:
        raise ValueError("A validated pending action is required")
    return action.action_id.bytes + bytes.fromhex(action.action_fingerprint)


def _parse_operator(frame):
    if not frame.startswith(_OPERATOR):
        return operator._parse_request(frame)
    try:
        method = ActionMethod(frame[len(_OPERATOR)])
        payload = frame[len(_OPERATOR) + 1:]
        if method is ActionMethod.INSPECT_ACTION and not payload:
            return operator._Request(method, None)
        if method is not ActionMethod.INSPECT_ACTION and len(payload) == 48:
            action_id = UUID(bytes=payload[:16])
            if action_id.version == 4:
                return operator._Request(method, (action_id, payload[16:].hex()))
    except (IndexError, ValueError):
        pass
    raise ProtocolError("invalid action method or binding")


def request_action(endpoint, operator_uid, control_uid, method, action=None):
    """Inspect, explicitly decide, or dispatch a bound action via operator IPC."""
    if (
        endpoint.link is not Link.OPERATOR_CONTROL or not operator._valid_uid(operator_uid)
        or not operator._valid_uid(control_uid) or operator_uid == control_uid
        or type(method) is not ActionMethod or (method is ActionMethod.INSPECT_ACTION) != (action is None)
    ):
        raise ValueError("Expected a dedicated operator endpoint and explicit action method")
    frame = _OPERATOR + bytes((method,)) + (b"" if action is None else _action_argument(action))
    with _connect_as(endpoint, operator_uid, control_uid) as (connection, deadline):
        _send_frame(connection, frame, deadline)
        reply = delegation._read_frame(connection, len(_OPERATOR_REPLY) + _MAX_ACTION_BYTES, deadline)
    if reply == operator._DENIED:
        return None
    if not reply.startswith(_OPERATOR_REPLY):
        raise ProtocolError("action decision or dispatch outcome is unavailable")
    payload = reply[len(_OPERATOR_REPLY):]
    try:
        if method is ActionMethod.INSPECT_ACTION:
            return PendingRestartAction.model_validate_json(payload)
        if method is ActionMethod.DISPATCH:
            result = dispatch.DispatchStatus(payload.decode("ascii"))
            if result is not dispatch.DispatchStatus.VALIDATED_NO_EFFECT:
                return result
        else:
            decision = ApprovalDecision(payload.decode("ascii"))
            expected = ApprovalDecision.APPROVED if method is ActionMethod.APPROVE else ApprovalDecision.REJECTED
            if decision is expected:
                return decision
    except ValueError:
        pass
    raise ProtocolError("invalid action reply")


def submit_restart_proposal(endpoint, uids, proposal):
    """Submit a typed proposal and receive an informational pending-action verdict."""
    delegation._require_agent_link(endpoint)
    if type(proposal) is not MockRestartServiceProposal:
        raise ValueError("Expected a restart proposal")
    frame = _PROPOSAL + proposal.model_dump_json().encode("utf-8")
    if len(frame) > delegation._FRAME_LIMIT:
        raise ProtocolError("proposal frame exceeds its byte limit")
    with _connect_verified(endpoint, uids) as (connection, deadline):
        _send_frame(connection, frame, deadline)
        reply = delegation._read_frame(connection, len(_PROPOSAL_REPLY) + 1, deadline)
    if reply in (_PROPOSAL_REPLY + b"\x00", _PROPOSAL_REPLY + b"\x01"):
        return reply[-1] == 1
    raise ProtocolError("pending-action verdict is unavailable")


def _load_authority(store, authority, context):
    store._verify_current_session(authority.session_id)
    conn = store._connection
    session_id = str(authority.session_id)
    action_id = str(authority.action.action_id)
    grant = store._grant
    action = get_pending_restart_action(conn, authority.action.action_id)
    approval = get_approval_by_action_id(conn, authority.action.action_id)
    if (
        grant is None or grant[0] != str(authority.grant_id) or grant[-1] != "ACTIVE"
        or grant[-3:-1] != (authority.wall_expiry, authority.elapsed_expiry)
        or conn.execute("SELECT * FROM session_targets WHERE session_id = ?", (session_id,)).fetchone()
        != authority.target
        or conn.execute("SELECT * FROM pending_bindings WHERE session_id = ?", (session_id,)).fetchone()
        != (session_id, str(authority.grant_id), action_id)
        or store._operators.get(authority.approver_uid) != (LOCAL_APPROVER_ID, ("approver",))
        or conn.execute(
            "SELECT peer_uid FROM controller_events WHERE session_id = ? AND grant_id = ? "
            "AND kind = 'OPERATOR' AND method = 'APPROVE' AND decision = 'APPROVED'",
            (session_id, str(authority.grant_id)),
        ).fetchall() != [(authority.approver_uid,)]
        or action != authority.action or approval != authority.approval
        or approval.decision is not ApprovalDecision.APPROVED or approval.consumed
        or (action.run_id, action.requester_id, action.agent_id, action.environment)
        != (context.run_id, context.requester_id, context.agent_id, context.environment)
        or (action.tool, action.resource, action.environment) != grant[7:10]
    ):
        raise state.ControllerStateError("Hardened dispatch authority binding changed")
    return action, approval


def _consume_approval(conn, _authority, action, approval):
    return conn.execute(
        "UPDATE approvals SET consumed = 1 WHERE approval_id = ? AND action_id = ? AND consumed = 0",
        (str(approval.approval_id), str(action.action_id)),
    )


def _reserve(store, session, authority, connection, deadline):
    return dispatch._reserve(store, session, authority, connection, deadline, _load_authority, _consume_approval)


class _Controller(operator._Controller):
    _parse_operator_request = staticmethod(_parse_operator)
    _request_limit = _REQUEST_LIMIT
    _proposal_model = MockRestartServiceProposal
    _tool = dispatch._TOOL
    _policy = delegation.PolicyDecision.APPROVAL_REQUIRED

    def __init__(self, uids, operators, target, store):
        super().__init__(uids.agent, operators, store=store)
        self._uids = uids
        self._target = target
        self._target_binding = (
            str(self.admission.session_id), uids.target, target.path, target.access_gid, dispatch._POLICY_VERSION,
        )

    def start(self, operators):
        store = self._store
        store.start(self.admission, self._uids.control, self.agent_uid, operators, self._tool, dispatch._RESOURCE)
        with store._write():
            store._verify_current_session(self.admission.session_id)
            store._connection.execute("INSERT INTO session_targets VALUES (?, ?, ?, ?, ?)", self._target_binding)

    def allows(self, identity, method):
        if type(method) is ActionMethod:
            return identity.principal == LOCAL_APPROVER_ID and identity.roles == frozenset({operator.OperatorRole.APPROVER})
        return super().allows(identity, method)

    def _live_grant(self):
        session = self.admission
        sample = session.clock.sample()
        session.observe_expiry(sample)
        grant = session.grant
        if grant is not None and not session.grant_revoked and grant.is_live(sample):
            return grant
        return None

    def _pending(self):
        row = self._store._connection.execute(
            "SELECT grant_id, action_id FROM pending_bindings WHERE session_id = ?",
            (str(self.admission.session_id),),
        ).fetchone()
        grant = self.admission.grant
        if row is None or grant is None or row[0] != str(grant.grant_id):
            return None
        action = get_pending_restart_action(self._store._connection, UUID(row[1]))
        if action is None:
            raise state.ControllerStateError("Pending action evidence is missing")
        return action

    def _record_agent(self, uid, reason, *, proposal=None, deadline=None):
        store, session = self._store, self.admission
        with store._write():
            store._verify_current_session(session.session_id)
            grant = session.grant
            accepted = False
            if proposal is not None:
                store._check_grant(grant, live=True)
                try:
                    action = insert_pending_restart_action(store._connection, session.context, proposal)
                except ValueError:
                    reason = "POLICY_DENIED"
                else:
                    store._connection.execute("INSERT INTO pending_bindings VALUES (?, ?, ?)", (
                        str(session.session_id), str(grant.grant_id), str(action.action_id),
                    ))
                    accepted = True
            store._record(
                uuid4(), session.session_id, grant.grant_id if grant and uid is not None else None, uid,
                "ADMISSION", None, "PENDING_ACTION" if accepted else "DENY", reason,
                True if proposal is not None else None, self._policy.value if accepted else None,
            )
            if accepted:
                self._check_decision_window(grant, action.created_at, deadline)
        return accepted

    def _check_decision_window(self, grant, created_at, deadline):
        sample = self.admission.clock.sample()
        _remaining(deadline)
        if not grant.is_live(sample) or dispatch._ns(created_at) > sample[0]:
            raise state.ControllerStateError("Grant is unavailable at the decision boundary")

    def handle_agent(self, connection, deadline):
        try:
            _require_peer(connection, self.agent_uid)
        except IdentityError:
            self._record_agent(None, "PEER_REJECTED")
            return True
        try:
            frame = delegation._read_frame(connection, delegation._FRAME_LIMIT, deadline)
            proposal = delegation._parse_proposal(frame, prefix=_PROPOSAL, model=self._proposal_model)
        except ProtocolError:
            self._record_agent(self.agent_uid, "INVALID_PROPOSAL")
            return True
        except OSError:
            self._record_agent(self.agent_uid, "TRANSPORT_ERROR")
            return True
        grant = self._live_grant()
        if grant is None:
            accepted = self._record_agent(self.agent_uid, "GRANT_UNAVAILABLE")
        elif not grant.matches(self.admission.session_id, self.admission.context, self.agent_uid, proposal):
            accepted = self._record_agent(self.agent_uid, "GRANT_SCOPE_MISMATCH")
        elif self._pending() is not None:
            accepted = self._record_agent(self.agent_uid, "PENDING_EXISTS")
        else:
            accepted = self._record_agent(self.agent_uid, "PROPOSAL", proposal=proposal, deadline=deadline)
        if accepted and self._live_grant() is None:
            return True
        try:
            _send_frame(connection, _PROPOSAL_REPLY + bytes((accepted,)), deadline)
        except OSError:
            pass
        return True

    def _record_action_event(self, uid, method, grant, decision):
        self._store._record(uuid4(), self.admission.session_id, grant.grant_id if grant else None,
                            uid, "OPERATOR", method.name, decision)

    def authorized_request(self, uid, request, deadline):
        method = request.method
        if type(method) is not ActionMethod:
            return super().authorized_request(uid, request, deadline)
        store, session = self._store, self.admission
        grant = self._live_grant()
        _remaining(deadline)
        with store._write():
            store._verify_current_session(session.session_id)
            action = self._pending() if grant else None
            reply = operator._DENIED
            authority = None
            recorded_approval = None
            decision = "ACTION_UNAVAILABLE"
            sample = session.clock.sample()
            if action is not None and grant.is_live(sample) and (
                dispatch._ns(action.created_at) <= sample[0] < dispatch._ns(action.expires_at)
            ):
                if method is ActionMethod.INSPECT_ACTION:
                    decision = "ACTION_INSPECTED"
                    reply = _OPERATOR_REPLY + action.model_dump_json().encode("utf-8")
                elif request.argument == (action.action_id, action.action_fingerprint):
                    approval = get_approval_by_action_id(store._connection, action.action_id)
                    if method is ActionMethod.DISPATCH:
                        if approval and approval.decision is ApprovalDecision.APPROVED and not approval.consumed:
                            authority = _Authority(session.session_id, grant.grant_id, action, approval, uid,
                                                   grant.wall_expiry, grant.elapsed_expiry, self._target_binding)
                            decision = "DISPATCH_REQUESTED"
                    elif approval is None:
                        selection = ApprovalDecision.APPROVED if method is ActionMethod.APPROVE else ApprovalDecision.REJECTED
                        recorded_approval = record_operator_decision(store._connection, action.action_id, selection)
                        decision = recorded_approval.decision.value
                        reply = _OPERATOR_REPLY + decision.encode("ascii")
            self._record_action_event(uid, method, grant, decision)
            if recorded_approval is not None:
                self._check_decision_window(grant, recorded_approval.created_at, deadline)
        if grant is not None and self._live_grant() is None:
            # A committed decision may be durable even when its reply is lost.
            return None
        if authority is None:
            return reply

        _remaining(deadline)
        try:
            outcome = dispatch._coordinate(
                store, session, authority, self._target, self._uids, simulate=True, reserve=_reserve, request_deadline=deadline,
            )
        except (IdentityError, PathSecurityError, OSError):
            # An unavailable target or peer mismatch cannot grant dispatch authority.
            # A failed store is terminal, including an uncertain reservation commit.
            if store._failed:
                raise
            return operator._DENIED
        return _OPERATOR_REPLY + outcome.value.encode("ascii")


def serve_hardened(agent, operator_endpoint, target, uids, operators, *, state_dir):
    """Launch the local SQLite-only profile with frozen service and operator identities."""
    operator._validate_launch(agent, operator_endpoint, uids, operators)
    dispatch._require_dispatch_link(target)
    endpoints = (agent, operator_endpoint, target)
    if len({endpoint.access_gid for endpoint in endpoints}) != 3 or len({
        endpoint.path.rsplit("/", 1)[0] for endpoint in endpoints
    }) != 3:
        raise ValueError("Hardened links require separate directories and access groups")
    expected = {
        (operator._REQUESTER, frozenset({operator.OperatorRole.REQUESTER})),
        (LOCAL_APPROVER_ID, frozenset({operator.OperatorRole.APPROVER})),
    }
    if {(identity.principal, identity.roles) for identity in operators} != expected:
        raise ValueError("Hardened launch requires dedicated requester and approver identities")
    _require_local_uid(uids.control)
    with state._open_controller_state(state_dir, uids.control, schema=_schema(), version=_VERSION) as store:
        controller = _Controller(uids, operators, target, store)
        controller.start(operators)
        return operator._serve_controller(agent, operator_endpoint, uids, controller)
