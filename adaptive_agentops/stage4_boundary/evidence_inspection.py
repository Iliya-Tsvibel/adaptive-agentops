"""Dedicated Controller maintenance IPC for historical simulated evidence."""

from dataclasses import dataclass
import sqlite3
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from . import (
    BoundaryError, Endpoint, IdentityError, Link, PathSecurityError, ProtocolError, TrustedUIDs, _EXCHANGE_SECONDS,
    _connect_as, _listen, _remaining, _require_local_uid, _require_peer, _send_frame, monotonic,
)
from adaptive_agentops.identity import LOCAL_APPROVER_ID

from . import controller_state as state, delegation, dispatch_state as dispatch
from . import evidence_source, operator, target_state

_REQUEST = b"AAO\x08\x01"
_REPLY = b"AAO\x08\x02"
_LIMIT = 256
_AUDIT_DB = "inspection.sqlite3"
_AUDIT_ID = 0x41414F45
_AUDIT_VERSION = 1
_AUDIT_LIMIT = 1024
_AUDIT_SCHEMA = ("""CREATE TABLE disclosures (
    inspector_uid INTEGER NOT NULL CHECK (inspector_uid BETWEEN 1 AND 4294967294),
    dispatch_id TEXT NOT NULL CHECK (length(dispatch_id) = 36),
    diagnostic TEXT NOT NULL CHECK (length(diagnostic) BETWEEN 1 AND 256)
) STRICT""",)


def _is_dispatch_id(value):
    return type(value) is UUID and value.version == 4


@dataclass(frozen=True, slots=True)
class InspectorScope:
    """Frozen trusted launch configuration, supplied outside request IPC."""

    uid: int
    dispatch_id: UUID

    def __post_init__(self):
        if type(self.uid) is not int or not 0 < self.uid < 0xFFFFFFFF:
            raise ValueError("Invalid inspector UID")
        if not _is_dispatch_id(self.dispatch_id):
            raise ValueError("Expected an exact UUID4 dispatch scope")


class EvidenceDiagnostic(BaseModel):
    """The full operator disclosure allowlist."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    version: Literal[1] = 1
    result: Literal["RECORDED_SIMULATED_EFFECT", "CONTROLLER_NO_SEND", "UNRESOLVED", "INCONSISTENT"]
    controller: Literal["COMMITTED", "NO_SEND", "UNKNOWN", "MISSING_OUTCOME", "UNAVAILABLE", "INVALID"]
    target: Literal["COMMITTED", "UNKNOWN", "UNAVAILABLE", "INVALID", "NOT_QUERIED"]
    effect: Literal["CONTROLLER", "TARGET", "BOTH"] | None = None


def _verify_audit(conn):
    state._verify_schema_metadata(conn, _AUDIT_SCHEMA, _AUDIT_VERSION, _AUDIT_ID)
    if conn.execute("SELECT count(*) FROM disclosures").fetchone()[0] > _AUDIT_LIMIT:
        raise state.ControllerStateError("Inspection audit exceeds capacity")


def provision_inspection_audit(directory, control_uid):
    """Explicitly provision a separate protected, empty audit directory."""
    state._provision_state(
        directory, control_uid, _AUDIT_DB, _AUDIT_SCHEMA, _AUDIT_VERSION, _AUDIT_ID, _verify_audit,
    )


def _audit(store, scope, diagnostic, deadline):
    conn = store._connection
    with store._write():
        _remaining(deadline)
        if conn.execute("SELECT count(*) FROM disclosures").fetchone()[0] >= _AUDIT_LIMIT:
            raise state.ControllerStateError("Inspection audit is full")
        conn.execute("INSERT INTO disclosures VALUES (?, ?, ?)", (scope.uid, str(scope.dispatch_id), diagnostic))
        _remaining(deadline)


def _validate_inspector(endpoint, scope, uids, operators, execution_endpoints):
    if type(scope) is not InspectorScope or type(uids) is not TrustedUIDs:
        raise ValueError("Expected trusted inspection configuration")
    agent, operator_endpoint, target = execution_endpoints
    operator._validate_launch(agent, operator_endpoint, uids, operators)
    expected = {
        (operator._REQUESTER, frozenset({operator.OperatorRole.REQUESTER})),
        (LOCAL_APPROVER_ID, frozenset({operator.OperatorRole.APPROVER})),
    }
    if {(item.principal, item.roles) for item in operators} != expected:
        raise ValueError("Expected the hardened requester and approver mapping")
    if scope.uid in (uids.agent, uids.control, uids.target, *(identity.uid for identity in operators)):
        raise ValueError("Inspector requires a distinct UID")
    if type(endpoint) is not Endpoint or endpoint.link is not Link.OPERATOR_CONTROL:
        raise ValueError("Expected a dedicated inspector endpoint")
    if any(endpoint.access_gid == existing.access_gid
           or endpoint.path.rsplit("/", 1)[0] == existing.path.rsplit("/", 1)[0]
           for existing in (agent, operator_endpoint, target)):
        raise ValueError("Inspector requires a dedicated directory and group")


def _inspect_peer(peer, deadline, scope, audit_store, acquire):
    """Authenticate before parsing and authorize before any scoped read."""
    try:
        _require_peer(peer, scope.uid)
        frame = delegation._read_frame(peer, len(_REQUEST) + 16, deadline)
        if frame != _REQUEST + scope.dispatch_id.bytes:
            return
        result = acquire(deadline)
        diagnostic = result.model_dump_json()
        wire = diagnostic.encode("ascii")
        if len(wire) > _LIMIT:
            raise ProtocolError("Inspection reply exceeds its bound")
        _audit(audit_store, scope, diagnostic, deadline)
        _send_frame(peer, _REPLY + wire, deadline)
    except (BoundaryError, OSError, ValueError):
        # Absence of a reply carries no evidence about a particular dispatch.
        return


def inspect_dispatch(endpoint, inspector_uid, control_uid, dispatch_id):
    """Inspect a single dispatch through mutually authenticated local IPC."""
    if type(endpoint) is not Endpoint or endpoint.link is not Link.OPERATOR_CONTROL:
        raise ValueError("Expected an inspector endpoint")
    if not _is_dispatch_id(dispatch_id):
        raise ValueError("Expected a UUID4 dispatch")
    if inspector_uid == control_uid or any(
        type(uid) is not int or not 0 < uid < 0xFFFFFFFF for uid in (inspector_uid, control_uid)
    ):
        raise ValueError("Expected distinct non-root UIDs")
    with _connect_as(endpoint, inspector_uid, control_uid) as (peer, deadline):
        _send_frame(peer, _REQUEST + dispatch_id.bytes, deadline)
        frame = delegation._read_frame(peer, len(_REPLY) + _LIMIT, deadline)
    try:
        if not frame.startswith(_REPLY):
            raise ValueError
        body = frame[len(_REPLY):]
        result = EvidenceDiagnostic.model_validate_json(body)
        if result.model_dump_json().encode("ascii") != body:
            raise ValueError
        return result
    except ValueError:
        raise ProtocolError("Inspection did not yield a valid diagnostic") from None


def _project(history, receipt, target_status):
    outcome = history.outcome
    controller = {
        dispatch.DispatchStatus.SIMULATED_EFFECT_COMMITTED: "COMMITTED",
        dispatch.DispatchStatus.RESERVED_NO_SEND: "NO_SEND",
        dispatch.DispatchStatus.UNKNOWN: "UNKNOWN",
        None: "MISSING_OUTCOME",
    }[outcome]
    if (
        target_status == "INVALID"
        or receipt is not None and history.effect_number is not None and receipt != history.effect_number
    ):
        return EvidenceDiagnostic(result="INCONSISTENT", controller=controller, target=target_status)
    if history.effect_number is not None or receipt is not None:
        effect = ("CONTROLLER" if receipt is None else
                  "TARGET" if history.effect_number is None else "BOTH")
        return EvidenceDiagnostic(result="RECORDED_SIMULATED_EFFECT", controller=controller,
                                  target=target_status, effect=effect)
    result = "CONTROLLER_NO_SEND" if outcome is dispatch.DispatchStatus.RESERVED_NO_SEND else "UNRESOLVED"
    return EvidenceDiagnostic(result=result, controller=controller, target=target_status)


def _acquire(dir_fd, scope, uids, target, deadline):
    try:
        with evidence_source.open_history(dir_fd, uids.control, deadline) as conn:
            history = evidence_source.read_dispatch(conn, scope.dispatch_id, uids, target, deadline) if conn else None
            if history is None:
                return EvidenceDiagnostic(result="UNRESOLVED", controller="UNAVAILABLE", target="NOT_QUERIED")
            if history.key is None:
                return _project(history, None, "NOT_QUERIED")

            # Allow time for the local durable audit after a Target timeout.
            lookup_deadline = min(deadline - 0.25, monotonic() + 0.75)
            try:
                receipt = target_state.lookup_target_receipt(target, uids, history.key, deadline=lookup_deadline)
            except (IdentityError, PathSecurityError, ProtocolError):
                return _project(history, None, "INVALID")
            except (BoundaryError, OSError):
                return _project(history, None, "UNAVAILABLE")
            return _project(history, receipt.effect_number, receipt.status)
    except (state.ControllerStateError, ValueError, TypeError, sqlite3.Error):
        return EvidenceDiagnostic(result="INCONSISTENT", controller="INVALID", target="NOT_QUERIED")


def serve_inspection_once(endpoint, scope, uids, operators, *, execution_endpoints,
                          state_dir, audit_dir):
    """Serve one connection in the dedicated, paused-execution maintenance profile."""
    _validate_inspector(endpoint, scope, uids, operators, execution_endpoints)
    target = execution_endpoints[2]
    dispatch._require_dispatch_link(target)
    _require_local_uid(uids.control)
    with (state._state_lease(state_dir, uids.control) as source_fd,
          state._state_lease(audit_dir, uids.control) as fd):
        with state._database(fd, uids.control, _AUDIT_DB) as conn:
            _verify_audit(conn)
            store = state._ControllerStore(conn)
            with _listen(endpoint, uids.control) as listener:
                peer, _ = listener.accept()
                with peer:
                    _inspect_peer(
                        peer, monotonic() + _EXCHANGE_SECONDS, scope, store,
                        lambda deadline: _acquire(source_fd, scope, uids, target, deadline),
                    )
