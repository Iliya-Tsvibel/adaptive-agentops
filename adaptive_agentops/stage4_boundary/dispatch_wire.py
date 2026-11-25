"""Stage 4.5 authenticated dispatch framing and no-effect target acknowledgement."""

import json
import struct
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, UUID4, model_validator

from adaptive_agentops.actions import _fingerprint
from adaptive_agentops.tool_requests import EmptyArguments

from . import (
    Endpoint, Link, ProtocolError, TrustedUIDs, _EXCHANGE_SECONDS, _listen, _require_local_uid,
    _require_peer, _send_frame, monotonic,
)
from . import delegation


_REQUEST = b"AAO\x04\x01"
_RESPONSE = b"AAO\x04\x02"
_LENGTH = struct.Struct("!I")
_BODY_MAX = 2048
_FRAME_MAX = len(_REQUEST) + _LENGTH.size + _BODY_MAX
_MAX_WINDOW_NS = 5_000_000_000
_SCOPE_FIELDS = {"run_id", "requester_id", "agent_id", "tool", "resource", "environment", "arguments"}
_Nanoseconds = Annotated[int, Field(gt=0, le=(1 << 63) - 1)]


class DispatchEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    session_id: UUID4
    run_id: UUID4
    grant_id: UUID4
    dispatch_id: UUID4
    action_id: UUID4
    approval_id: UUID4
    requester_id: Literal["local-requester"]
    agent_id: Literal["incident-investigator-v1"]
    target_id: Literal["demo-api"]
    policy_version: Literal["local-stage4-v1"]
    tool: Literal["mock.restart_service"]
    resource: Literal["demo-api"]
    environment: Literal["local-mock"]
    arguments: EmptyArguments
    fingerprint_version: Literal[1]
    action_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    issued_wall_ns: _Nanoseconds
    issued_elapsed_ns: _Nanoseconds
    not_after_wall_ns: _Nanoseconds
    not_after_elapsed_ns: _Nanoseconds

    @model_validator(mode="after")
    def verify_scope_and_window(self):
        scope = self.model_dump(mode="json", include=_SCOPE_FIELDS)
        if self.action_fingerprint != _fingerprint(scope):
            raise ValueError("dispatch does not match the existing action fingerprint")
        wall_window = self.not_after_wall_ns - self.issued_wall_ns
        elapsed_window = self.not_after_elapsed_ns - self.issued_elapsed_ns
        if not (0 < wall_window <= _MAX_WINDOW_NS and 0 < elapsed_window <= _MAX_WINDOW_NS):
            raise ValueError("invalid bounded dispatch window")
        return self


class DispatchAck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dispatch_id: UUID4
    action_id: UUID4
    outcome: Literal["VALIDATED_NO_EFFECT"]


def _encode_body(message: BaseModel) -> bytes:
    return json.dumps(
        message.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def _encode_frame(prefix: bytes, message: BaseModel) -> bytes:
    body = _encode_body(message)
    if not 0 < len(body) <= _BODY_MAX:
        raise ProtocolError("dispatch frame body exceeds its bound")
    return prefix + _LENGTH.pack(len(body)) + body


def _parse_frame(frame: bytes, prefix: bytes, model):
    body_start = len(prefix) + _LENGTH.size
    if not frame.startswith(prefix) or len(frame) < body_start:
        raise ProtocolError("unsupported or truncated dispatch frame")
    body = frame[body_start:]
    (declared,) = _LENGTH.unpack(frame[len(prefix):body_start])
    if declared != len(body) or not 0 < declared <= _BODY_MAX:
        raise ProtocolError("invalid dispatch frame length")
    try:
        message = model.model_validate_json(body, strict=True)
        # Canonical equality also rejects duplicate keys and alternate spellings.
        if _encode_body(message) != body:
            raise ValueError("noncanonical dispatch body")
    except ValueError as exc:
        raise ProtocolError("invalid dispatch message") from exc
    return message


def _require_dispatch_link(endpoint: Endpoint) -> None:
    if endpoint.link is not Link.CONTROL_TARGET:
        raise ValueError("dispatch requires the dedicated Controller-to-target link")


def _require_live(envelope: DispatchEnvelope, sample: tuple[int, int]) -> None:
    wall, elapsed = sample
    if not (
        envelope.issued_wall_ns <= wall < envelope.not_after_wall_ns
        and envelope.issued_elapsed_ns <= elapsed < envelope.not_after_elapsed_ns
    ):
        raise ProtocolError("dispatch is future-issued or expired")
    if abs((wall - envelope.issued_wall_ns) - (elapsed - envelope.issued_elapsed_ns)) > delegation._SECOND_NS:
        raise ProtocolError("dispatch clocks diverged from issuance")


def _exchange_reserved(connection, deadline: float, envelope: DispatchEnvelope, *, clock=None) -> DispatchAck:
    """One bounded exchange on the coordinator's already authenticated socket."""
    frame = _encode_frame(_REQUEST, envelope)
    _require_live(envelope, delegation._read_clocks() if clock is None else clock.sample())
    _send_frame(connection, frame, deadline)
    reply = _parse_frame(delegation._read_frame(connection, _FRAME_MAX, deadline), _RESPONSE, DispatchAck)
    if (reply.dispatch_id, reply.action_id) != (envelope.dispatch_id, envelope.action_id):
        raise ProtocolError("dispatch acknowledgement correlation failed")
    return reply


def serve_dispatch_once(endpoint: Endpoint, uids: TrustedUIDs) -> DispatchAck:
    """Validate one Controller request and send a correlated no-effect acknowledgement."""
    _require_dispatch_link(endpoint)
    client_uid, server_uid = endpoint._uids(uids)
    _require_local_uid(server_uid)
    clock = delegation._SessionClock()
    with _listen(endpoint, server_uid) as listener:
        connection, _address = listener.accept()
        with connection:
            deadline = monotonic() + _EXCHANGE_SECONDS
            _require_peer(connection, client_uid)
            frame = delegation._read_frame(connection, _FRAME_MAX, deadline)
            envelope = _parse_frame(frame, _REQUEST, DispatchEnvelope)
            _require_live(envelope, clock.sample())
            reply = DispatchAck(
                dispatch_id=envelope.dispatch_id, action_id=envelope.action_id, outcome="VALIDATED_NO_EFFECT",
            )
            _send_frame(connection, _encode_frame(_RESPONSE, reply), deadline)
            return reply
