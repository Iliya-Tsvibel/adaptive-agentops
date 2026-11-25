"""Authenticated, bounded operator IPC for the local proposal-only lifecycle."""

from dataclasses import dataclass
from enum import Enum, IntEnum
from selectors import DefaultSelector, EVENT_READ
import struct
from types import MappingProxyType
from uuid import UUID, uuid4

from adaptive_agentops.tool_requests import MockGetHealthProposal

from . import (
    BoundaryError, Endpoint, IdentityError, Link, ProtocolError, TrustedUIDs,
    _ACCEPT_SECONDS, _EXCHANGE_SECONDS, _connect_as, _listen, _peer_uid,
    _remaining, _require_local_uid, _send_frame, monotonic,
)
from . import delegation


class OperatorRole(Enum):
    REQUESTER = "requester"
    APPROVER = "approver"


class GrantMethod(IntEnum):
    ISSUE = 1
    INSPECT = 2
    REVOKE = 3


class GrantState(IntEnum):
    ACTIVE = 1
    EXPIRED = 2
    REVOKED = 3


_METHOD_ROLE = MappingProxyType({
    GrantMethod.ISSUE: OperatorRole.REQUESTER,
    GrantMethod.INSPECT: OperatorRole.REQUESTER,
    GrantMethod.REVOKE: OperatorRole.REQUESTER,
})
_REQUEST = b"AAO\x03\x01"
_REPLY = b"AAO\x03\x02"
_DENIED = _REPLY + b"\x00"
_TTL = struct.Struct("!H")
_VIEW = struct.Struct("!B16s16s16sQI")
_REQUEST_LIMIT = len(_REQUEST) + 1 + 16
_MAX_OPERATORS = 8
_REQUESTER = "local-requester"


def _valid_uid(uid: int) -> bool:
    return type(uid) is int and 0 < uid < 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class OperatorIdentity:
    uid: int
    principal: str
    roles: frozenset[OperatorRole]

    def __post_init__(self):
        if not _valid_uid(self.uid):
            raise ValueError("invalid operator UID")
        if (
            type(self.principal) is not str or not 1 <= len(self.principal) <= 64
            or not self.principal.isascii() or not self.principal.isprintable()
        ):
            raise ValueError("invalid operator principal")
        if (
            type(self.roles) is not frozenset or not self.roles
            or not all(type(role) is OperatorRole for role in self.roles)
        ):
            raise ValueError("operator roles must be an immutable set of configured roles")


@dataclass(frozen=True, slots=True)
class GrantView:
    state: GrantState
    grant_id: UUID
    session_id: UUID
    run_id: UUID
    wall_expiry_ns: int
    agent_uid: int


class OperatorResult(Enum):
    ACCEPTED = "ACCEPTED"
    PEER_REJECTED = "PEER_REJECTED"
    METHOD_DENIED = "METHOD_DENIED"
    INVALID_REQUEST = "INVALID_REQUEST"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    GRANT_UNAVAILABLE = "GRANT_UNAVAILABLE"
    POLICY_DENIED = "POLICY_DENIED"
    CLOCK_ERROR = "CLOCK_ERROR"


@dataclass(frozen=True, slots=True)
class OperatorEvent:
    event_id: UUID
    session_id: UUID
    operator_uid: int | None
    grant_id: UUID | None
    method: GrantMethod | None
    result: OperatorResult


@dataclass(frozen=True, slots=True)
class SessionEvidence:
    operator: tuple[OperatorEvent, ...]
    admission: tuple[delegation.AdmissionEvent, ...]


@dataclass(frozen=True, slots=True)
class _Request:
    method: GrantMethod
    argument: int | UUID | None


def _frame_request(method: GrantMethod, argument) -> bytes:
    if method is GrantMethod.ISSUE and type(argument) is int and 1 <= argument <= delegation._MAX_GRANT_SECONDS:
        payload = _TTL.pack(argument)
    elif method is GrantMethod.INSPECT and argument is None:
        payload = b""
    elif method is GrantMethod.REVOKE and type(argument) is UUID:
        payload = argument.bytes
    else:
        raise ProtocolError("invalid operator request")
    return _REQUEST + bytes((method,)) + payload


def _parse_request(frame: bytes) -> _Request:
    if not frame.startswith(_REQUEST) or len(frame) < len(_REQUEST) + 1:
        raise ProtocolError("invalid operator frame")
    try:
        method = GrantMethod(frame[len(_REQUEST)])
    except ValueError as exc:
        raise ProtocolError("unsupported operator method") from exc
    payload = frame[len(_REQUEST) + 1:]
    if method is GrantMethod.ISSUE and len(payload) == _TTL.size:
        (seconds,) = _TTL.unpack(payload)
        if 1 <= seconds <= delegation._MAX_GRANT_SECONDS:
            return _Request(method, seconds)
    if method is GrantMethod.INSPECT and not payload:
        return _Request(method, None)
    if method is GrantMethod.REVOKE and len(payload) == 16:
        return _Request(method, UUID(bytes=payload))
    raise ProtocolError("invalid operator method payload")


def _grant_frame(view: GrantView) -> bytes:
    return _REPLY + _VIEW.pack(
        view.state, view.grant_id.bytes, view.session_id.bytes, view.run_id.bytes,
        view.wall_expiry_ns, view.agent_uid,
    )


def request_grant(
    endpoint: Endpoint, operator_uid: int, control_uid: int, method: GrantMethod,
    argument: int | UUID | None = None,
) -> GrantView | None:
    """Exchange one lifecycle request with the verified Control Plane."""
    if (
        endpoint.link is not Link.OPERATOR_CONTROL or not _valid_uid(operator_uid)
        or not _valid_uid(control_uid) or operator_uid == control_uid
    ):
        raise ValueError("dedicated operator and Control Plane identities are required")
    frame = _frame_request(method, argument)
    with _connect_as(endpoint, operator_uid, control_uid) as (connection, deadline):
        _send_frame(connection, frame, deadline)
        reply = delegation._read_frame(connection, len(_REPLY) + _VIEW.size, deadline)
    if reply == _DENIED:
        return None
    if reply.startswith(_REPLY) and len(reply) == len(_REPLY) + _VIEW.size:
        state, grant, session, run, expiry, agent_uid = _VIEW.unpack(reply[len(_REPLY):])
        identifiers = UUID(bytes=grant), UUID(bytes=session), UUID(bytes=run)
        if not _valid_uid(agent_uid) or expiry == 0 or any(identifier.version != 4 for identifier in identifiers):
            raise ProtocolError("invalid grant identity or expiry")
        try:
            return GrantView(GrantState(state), *identifiers, expiry, agent_uid)
        except ValueError as exc:
            raise ProtocolError("invalid grant state") from exc
    raise ProtocolError("invalid operator reply")


class _Controller:
    def __init__(self, agent_uid: int, operators: tuple[OperatorIdentity, ...]):
        self.agent_uid = agent_uid
        self.operators = MappingProxyType({operator.uid: operator for operator in operators})
        self.admission = delegation._Session(agent_uid, None)
        self.grantor_uid = None
        self.journal: list[OperatorEvent] = []

    def record(self, uid, method, result, grant_id=None):
        if len(self.journal) >= delegation._MAX_CONNECTIONS:
            raise BoundaryError("operator journal exhausted")
        self.journal.append(OperatorEvent(uuid4(), self.admission.session_id, uid, grant_id, method, result))

    def handle_operator(self, connection, deadline: float) -> bool:
        try:
            operator = self.operators.get(_peer_uid(connection))
        except IdentityError:
            operator = None
        if operator is None:
            self.record(None, None, OperatorResult.PEER_REJECTED)
            return True

        try:
            request = _parse_request(delegation._read_frame(connection, _REQUEST_LIMIT, deadline))
        except ProtocolError:
            self.record(operator.uid, None, OperatorResult.INVALID_REQUEST)
            return True
        except OSError:
            self.record(operator.uid, None, OperatorResult.TRANSPORT_ERROR)
            return True

        method = request.method
        journal_size = len(self.journal)
        try:
            if _METHOD_ROLE[method] not in operator.roles or operator.principal != _REQUESTER:
                self.record(operator.uid, method, OperatorResult.METHOD_DENIED)
                reply = _DENIED
            else:
                reply = self.authorized_request(operator.uid, request, deadline)
        except delegation.ClockError:
            # A post-record clock failure can leave a decision with no reply or transition.
            if len(self.journal) == journal_size:
                self.record(operator.uid, method, OperatorResult.CLOCK_ERROR)
            return False
        if reply is None:
            return True
        try:
            _send_frame(connection, reply, deadline)
        except OSError:
            pass  # The evidence records a decision, even if the peer disconnected.
        return True

    def authorized_request(self, uid: int, request: _Request, deadline: float) -> bytes | None:
        session = self.admission
        method = request.method
        sample = session.clock.sample()
        grant = session.grant
        if method is GrantMethod.ISSUE:
            if grant is not None:
                self.record(uid, method, OperatorResult.GRANT_UNAVAILABLE)
                return _DENIED
            try:
                decision = delegation.evaluate_policy(
                    session.context,
                    MockGetHealthProposal(tool=delegation._TOOL, resource=delegation._RESOURCE, arguments={}),
                )
            except Exception:
                decision = None
            if decision is not delegation.PolicyDecision.ALLOW:
                self.record(uid, method, OperatorResult.POLICY_DENIED)
                return _DENIED
            grant = session.new_grant(self.agent_uid, request.argument, session.clock.sample())
        elif (
            grant is None or self.grantor_uid != uid
            or (method is GrantMethod.REVOKE and (
                request.argument != grant.grant_id or session.grant_revoked or not grant.is_live(sample)
            ))
        ):
            self.record(uid, method, OperatorResult.GRANT_UNAVAILABLE)
            return _DENIED

        self.record(uid, method, OperatorResult.ACCEPTED, grant.grant_id)
        sample = session.clock.sample()
        try:
            _remaining(deadline)
        except TimeoutError:
            return None
        if method in (GrantMethod.ISSUE, GrantMethod.REVOKE) and not grant.is_live(sample):
            return _DENIED
        state = GrantState.ACTIVE if grant.is_live(sample) else GrantState.EXPIRED
        if method is GrantMethod.REVOKE or session.grant_revoked:
            state = GrantState.REVOKED
        reply = _grant_frame(GrantView(
            state, grant.grant_id, grant.session_id, grant.run_id, grant.wall_expiry, grant.agent_uid,
        ))
        if method is GrantMethod.ISSUE:
            self.grantor_uid, session.grant = uid, grant
        elif method is GrantMethod.REVOKE:
            session.grant_revoked = True
        return reply


def _validate_launch(agent: Endpoint, operator: Endpoint, uids: TrustedUIDs, operators) -> None:
    delegation._require_agent_link(agent)
    if (
        operator.link is not Link.OPERATOR_CONTROL or operator.access_gid == agent.access_gid
        or operator.path.rsplit("/", 1)[0] == agent.path.rsplit("/", 1)[0]
    ):
        raise ValueError("operator IPC requires a separate directory and access group")
    if type(operators) is not tuple or not 1 <= len(operators) <= _MAX_OPERATORS:
        raise ValueError("expected a bounded immutable operator map")
    if any(type(operator) is not OperatorIdentity for operator in operators):
        raise ValueError("invalid operator configuration")
    configured_uids = {operator.uid for operator in operators}
    principals = {operator.principal for operator in operators}
    if (
        len(configured_uids) != len(operators) or configured_uids & {uids.agent, uids.control, uids.target}
        or len(principals) != len(operators)
    ):
        raise ValueError("operator UIDs and principals must be distinct from protected service identities")
    requesters = [operator.principal for operator in operators if OperatorRole.REQUESTER in operator.roles]
    if requesters != [_REQUESTER]:
        raise ValueError("exactly one operator can represent the configured PDP requester")


def serve_managed_admission(
    agent: Endpoint, operator: Endpoint, uids: TrustedUIDs, operators: tuple[OperatorIdentity, ...],
) -> SessionEvidence:
    """Run both protected links in one serial controller session."""
    _validate_launch(agent, operator, uids, operators)
    _require_local_uid(uids.control)
    controller = _Controller(uids.agent, operators)
    with _listen(agent, uids.control) as agent_socket, _listen(operator, uids.control) as operator_socket:
        with DefaultSelector() as selector:
            selector.register(operator_socket, EVENT_READ, True)
            selector.register(agent_socket, EVENT_READ, False)
            counts = {True: 0, False: 0}
            while ready := selector.select(_ACCEPT_SECONDS):
                # Prefer revocation-capable IPC when both listeners are already ready.
                key, _ = max(ready, key=lambda event: event[0].data)
                connection, _address = key.fileobj.accept()
                counts[key.data] += 1
                with connection:
                    deadline = monotonic() + _EXCHANGE_SECONDS
                    if key.data:
                        keep_serving = controller.handle_operator(connection, deadline)
                    else:
                        keep_serving = delegation._handle_connection(
                            connection, controller.admission, uids.agent, deadline,
                        )
                if not keep_serving or counts[key.data] >= delegation._MAX_CONNECTIONS:
                    break
    return SessionEvidence(tuple(controller.journal), tuple(controller.admission.journal))
