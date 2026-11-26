"""Stage 4.2 proposal admission with explicit, session-only fixture authority."""

from dataclasses import dataclass
from enum import Enum
import json
import time
from uuid import UUID, uuid4

from adaptive_agentops.identity import RunContext
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import MockGetHealthProposal

from . import (
    BoundaryError,
    Endpoint,
    IdentityError,
    Link,
    ProtocolError,
    TrustedUIDs,
    _connect_verified,
    _listen,
    _remaining,
    _require_local_uid,
    _require_peer,
    _send_frame,
)
from . import _EXCHANGE_SECONDS, monotonic


_MAX_CONNECTIONS = 16
_MAX_GRANT_SECONDS = 15 * 60
_SECOND_NS = 1_000_000_000
_FRAME_LIMIT = 512
_PROPOSAL = b"AAO\x02\x01"
_VERDICT = b"AAO\x02\x02"
_AGENT = "incident-investigator-v1"
_ENVIRONMENT = "local-mock"
_TOOL = "mock.get_health"
_RESOURCE = "demo-api"


class AdmissionDecision(Enum):
    PROPOSAL_ALLOWED = 1
    DENY = 0


class AdmissionReason(str, Enum):
    ADMITTED = "ADMITTED"
    PEER_REJECTED = "PEER_REJECTED"
    INVALID_FRAME = "INVALID_FRAME"
    INVALID_PROPOSAL = "INVALID_PROPOSAL"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    MISSING_GRANT = "MISSING_GRANT"
    GRANT_SCOPE_MISMATCH = "GRANT_SCOPE_MISMATCH"
    GRANT_EXPIRED = "GRANT_EXPIRED"
    GRANT_REVOKED = "GRANT_REVOKED"
    POLICY_DENIED = "POLICY_DENIED"
    POLICY_ERROR = "POLICY_ERROR"
    CLOCK_ERROR = "CLOCK_ERROR"


@dataclass(frozen=True, slots=True)
class AdmissionEvent:
    """Volatile decision evidence. A decision does not assert reply delivery."""

    event_id: UUID
    session_id: UUID
    peer_uid: int | None
    agent_id: str | None
    run_id: UUID | None
    grant_id: UUID | None
    decision: AdmissionDecision
    reason: AdmissionReason
    grant_matched: bool | None
    policy_decision: PolicyDecision | None


class ClockError(BoundaryError):
    pass


def _read_clocks() -> tuple[int, int]:
    try:
        return time.time_ns(), time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError, ValueError) as exc:
        raise ClockError("wall and suspend-aware elapsed clocks are required") from exc


class _SessionClock:
    def __init__(self):
        self.start = self.last = _read_clocks()

    def sample(self) -> tuple[int, int]:
        wall, elapsed = _read_clocks()
        if any(
            wall < previous_wall or elapsed < previous_elapsed
            or abs((wall - previous_wall) - (elapsed - previous_elapsed)) > _SECOND_NS
            for previous_wall, previous_elapsed in (self.start, self.last)
        ):
            raise ClockError("clock moved backward or diverged by more than one second")
        self.last = wall, elapsed
        return self.last


@dataclass(frozen=True, slots=True)
class _Grant:
    grant_id: UUID
    session_id: UUID
    run_id: UUID
    agent_uid: int
    agent_id: str
    tool: str
    resource: str
    environment: str
    wall_expiry: int
    elapsed_expiry: int

    def matches(self, session_id, context, peer_uid, proposal) -> bool:
        return (
            self.session_id == session_id
            and self.run_id == context.run_id
            and self.agent_uid == peer_uid
            and self.agent_id == context.agent_id
            and self.tool == proposal.tool
            and self.resource == proposal.resource
            and self.environment == context.environment
        )

    def is_live(self, sample: tuple[int, int]) -> bool:
        wall, elapsed = sample
        return wall < self.wall_expiry and elapsed < self.elapsed_expiry


@dataclass(frozen=True, slots=True)
class _Outcome:
    reason: AdmissionReason
    grant_matched: bool | None = None
    policy_decision: PolicyDecision | None = None

    @property
    def decision(self) -> AdmissionDecision:
        if self.reason is AdmissionReason.ADMITTED:
            return AdmissionDecision.PROPOSAL_ALLOWED
        return AdmissionDecision.DENY


class _Session:
    def __init__(self, agent_uid: int, grant_seconds: int | None, *, store=None):
        if grant_seconds is not None and (
            type(grant_seconds) is not int or not 1 <= grant_seconds <= _MAX_GRANT_SECONDS
        ):
            raise ValueError("fixture grant lifetime must be in 1..900 seconds")

        self.session_id = uuid4()
        self.context = RunContext(uuid4(), "local-requester", _AGENT, _ENVIRONMENT)
        self.clock = _SessionClock()
        self.journal: list[AdmissionEvent] = []
        self.grant = None
        self.grant_revoked = False
        self._grant_expired = False
        self._store = store
        if grant_seconds is not None:
            self.grant = self.new_grant(agent_uid, grant_seconds, self.clock.start)

    def new_grant(self, agent_uid: int, seconds: int, sample: tuple[int, int], *, tool=_TOOL) -> _Grant:
        wall, elapsed = sample
        lifetime = seconds * _SECOND_NS
        return _Grant(
            uuid4(), self.session_id, self.context.run_id, agent_uid, self.context.agent_id,
            tool, _RESOURCE, self.context.environment, wall + lifetime, elapsed + lifetime,
        )

    def observe_expiry(self, sample) -> None:
        if (
            self._store is not None and self.grant is not None and not self.grant_revoked
            and not self._grant_expired and not self.grant.is_live(sample)
        ):
            self._store.expire(self.grant)
            self._grant_expired = True

    def evaluate(self, peer_uid: int, proposal: MockGetHealthProposal) -> _Outcome:
        try:
            sample = self.clock.sample()
        except ClockError:
            return _Outcome(AdmissionReason.CLOCK_ERROR)

        self.observe_expiry(sample)
        grant = self.grant
        if grant is None:
            return _Outcome(AdmissionReason.MISSING_GRANT, False)
        if not grant.matches(self.session_id, self.context, peer_uid, proposal):
            return _Outcome(AdmissionReason.GRANT_SCOPE_MISMATCH, False)
        if self.grant_revoked:
            return _Outcome(AdmissionReason.GRANT_REVOKED, False)
        if not grant.is_live(sample):
            return _Outcome(AdmissionReason.GRANT_EXPIRED, False)

        try:
            decision = evaluate_policy(self.context, proposal)
        except Exception:
            return _Outcome(AdmissionReason.POLICY_ERROR, True)
        if not isinstance(decision, PolicyDecision):
            return _Outcome(AdmissionReason.POLICY_ERROR, True)
        if decision is not PolicyDecision.ALLOW:
            return _Outcome(AdmissionReason.POLICY_DENIED, True, decision)
        try:
            sample = self.clock.sample()
        except ClockError:
            return _Outcome(AdmissionReason.CLOCK_ERROR, None, decision)
        self.observe_expiry(sample)
        if not grant.is_live(sample):
            return _Outcome(AdmissionReason.GRANT_EXPIRED, False, decision)
        return _Outcome(AdmissionReason.ADMITTED, True, decision)

    def record(self, peer_uid: int | None, outcome: _Outcome) -> None:
        if len(self.journal) >= _MAX_CONNECTIONS:
            raise BoundaryError("admission journal exhausted")
        event = AdmissionEvent(
            event_id=uuid4(),
            session_id=self.session_id,
            peer_uid=peer_uid,
            agent_id=self.context.agent_id if peer_uid is not None else None,
            run_id=self.context.run_id if peer_uid is not None else None,
            grant_id=self.grant.grant_id if peer_uid is not None and self.grant else None,
            decision=outcome.decision,
            reason=outcome.reason,
            grant_matched=outcome.grant_matched,
            policy_decision=outcome.policy_decision,
        )
        if self._store is not None:
            self._store.record_admission(event, self.grant)
        self.journal.append(event)


def _read_frame(connection, limit: int, deadline: float) -> bytes:
    frame = bytearray()
    while len(frame) <= limit:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(limit + 1 - len(frame))
        if not chunk:
            _remaining(deadline)
            return bytes(frame)
        frame.extend(chunk)
    raise ProtocolError("admission frame exceeds its byte limit")


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj


def _reject_constant(_value):
    raise ValueError("non-finite JSON constant")


def _parse_proposal(frame: bytes, *, prefix=_PROPOSAL, model=MockGetHealthProposal):
    if not frame.startswith(prefix):
        raise ProtocolError("unsupported admission frame")
    try:
        text = frame[len(prefix):].decode("utf-8")
        decoder = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        payload, end = decoder.raw_decode(text)
        if end != len(text):
            raise ValueError("trailing proposal data")
        return model.model_validate(payload, strict=True)
    except (ValueError, RecursionError) as exc:
        raise ProtocolError("invalid admission proposal") from exc


def _verdict_frame(decision: AdmissionDecision) -> bytes:
    return _VERDICT + bytes((decision.value,))


def _require_agent_link(endpoint: Endpoint) -> None:
    if endpoint.link is not Link.AGENT_CONTROL:
        raise ValueError("proposal admission is only available on the agent link")


def request_admission(
    endpoint: Endpoint, uids: TrustedUIDs, proposal: MockGetHealthProposal,
) -> AdmissionDecision:
    """Request an informational verdict from the mutually verified Control Plane."""
    _require_agent_link(endpoint)
    frame = _PROPOSAL + proposal.model_dump_json().encode("utf-8")
    if len(frame) > _FRAME_LIMIT:
        raise ProtocolError("admission frame exceeds its byte limit")
    with _connect_verified(endpoint, uids) as (connection, deadline):
        _send_frame(connection, frame, deadline)
        reply = _read_frame(connection, len(_VERDICT) + 1, deadline)
        for decision in AdmissionDecision:
            if reply == _verdict_frame(decision):
                return decision
        raise ProtocolError("invalid admission verdict")


def _handle_connection(connection, session: _Session, client_uid: int, deadline: float) -> bool:
    try:
        _require_peer(connection, client_uid)
    except IdentityError:
        session.record(None, _Outcome(AdmissionReason.PEER_REJECTED))
        return True

    try:
        frame = _read_frame(connection, _FRAME_LIMIT, deadline)
    except ProtocolError:
        session.record(client_uid, _Outcome(AdmissionReason.INVALID_FRAME))
        return True
    except OSError:
        session.record(client_uid, _Outcome(AdmissionReason.TRANSPORT_ERROR))
        return True

    try:
        proposal = _parse_proposal(frame)
    except ProtocolError:
        session.record(client_uid, _Outcome(AdmissionReason.INVALID_PROPOSAL))
        return True

    outcome = session.evaluate(client_uid, proposal)
    session.record(client_uid, outcome)
    if outcome.decision is AdmissionDecision.PROPOSAL_ALLOWED:
        try:
            sample = session.clock.sample()
        except ClockError:
            return False
        session.observe_expiry(sample)
        if session.grant_revoked or not session.grant.is_live(sample):
            return True

    try:
        _send_frame(connection, _verdict_frame(outcome.decision), deadline)
    except OSError:
        # The journal describes the decision even when the reply cannot be sent.
        pass
    return outcome.reason is not AdmissionReason.CLOCK_ERROR


def serve_admission(
    endpoint: Endpoint, uids: TrustedUIDs, *, fixture_grant_seconds: int | None = None,
) -> tuple[AdmissionEvent, ...]:
    """Serve one isolated session. Only trusted bootstrap supplies fixture authority."""
    _require_agent_link(endpoint)
    client_uid, server_uid = endpoint._uids(uids)
    _require_local_uid(server_uid)
    session = _Session(client_uid, fixture_grant_seconds)
    with _listen(endpoint, server_uid) as listener:
        for _ in range(_MAX_CONNECTIONS):
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                break
            # Every successful accept occupies a slot, even when authentication fails.
            with connection:
                if not _handle_connection(connection, session, client_uid, monotonic() + _EXCHANGE_SECONDS):
                    break
    return tuple(session.journal)
