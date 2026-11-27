"""Read one historical dispatch from a leased Stage 4 Controller database."""

from contextlib import closing, contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID

from adaptive_agentops.actions import get_pending_restart_action
from adaptive_agentops.approvals import ApprovalDecision, RestartApproval
from adaptive_agentops.identity import LOCAL_APPROVER_ID

from . import controller_state as state, dispatch_state as dispatch, hardened, target_state
from . import _remaining, monotonic

_MAX_DB_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class HistoricalDispatch:
    outcome: dispatch.DispatchStatus | None
    key: target_state.ReceiptKey | None
    effect_number: int | None


@contextmanager
def open_history(dir_fd, control_uid, deadline):
    """Open source state only under the caller's exclusive directory lease."""
    name = state.DATABASE_NAME
    entries = set(os.listdir(dir_fd))
    if not entries:
        yield None
        return
    if entries != {name}:
        raise state.ControllerStateError("Inspection requires clean Controller state")
    identity = state._private_file(dir_fd, name, control_uid)
    database = Path(f"/proc/self/fd/{dir_fd}/{name}")
    if database.stat().st_size > _MAX_DB_BYTES:
        raise state.ControllerStateError("Controller evidence exceeds the inspection limit")
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0,
                                     isolation_level=None)) as conn:
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.execute("PRAGMA foreign_keys = ON")
            if conn.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise state.ControllerStateError("Controller evidence requires DELETE journaling")
            if state._private_file(dir_fd, name, control_uid) != identity:
                raise state.ControllerStateError("Controller evidence identity changed")
            conn.set_progress_handler(lambda: int(monotonic() >= deadline), 256)
            _remaining(deadline)
            state._verify_schema(conn, hardened._schema(), hardened._VERSION)
            yield conn
    except sqlite3.Error:
        raise state.ControllerStateError("Controller evidence is unavailable or corrupt") from None


def _read_approval(conn, approval_id):
    cursor = conn.execute("""
        SELECT approval_id, action_id, run_id, requester_id, agent_id, action_fingerprint,
               approver_id, decision, created_at, expires_at, consumed
        FROM approvals WHERE approval_id = ?
        """, (approval_id,))
    row = cursor.fetchone()
    if row is None or type(row[-1]) is not int or row[-1] != 1:
        raise ValueError("Invalid historical approval")
    fields = dict(zip((col[0] for col in cursor.description), (*row[:-1], True)))
    return RestartApproval.model_validate_json(json.dumps(fields))


def _historical_binding(conn, reservation, uids, target):
    _, action_id, approval_id, session_id, grant_id, fingerprint, resource, wall, elapsed = reservation
    session = conn.execute("""
        SELECT run_id, control_uid, agent_uid, requester_id, agent_id, tool, resource, environment,
               operator_map, wall_started_ns, elapsed_started_ns
        FROM controller_sessions WHERE session_id = ?
        """, (session_id,)).fetchone()
    if session is None:
        raise ValueError("Missing historical session")
    run, control_uid, agent_uid, requester, agent, tool, session_resource, env, operators, starts_wall, starts_elapsed = session
    action = get_pending_restart_action(conn, UUID(action_id))
    approval = _read_approval(conn, approval_id)
    grant = conn.execute("""
        SELECT run_id, agent_uid, agent_id, tool, resource, environment, wall_expiry_ns, elapsed_expiry_ns
        FROM controller_grants WHERE session_id = ? AND grant_id = ?
        """, (session_id, grant_id)).fetchone()
    binding = conn.execute("SELECT action_id, grant_id FROM pending_bindings WHERE session_id = ?",
                           (session_id,)).fetchone()
    target_binding = conn.execute("""
        SELECT target_uid, target_path, target_gid, policy_version FROM session_targets WHERE session_id = ?
        """, (session_id,)).fetchone()
    approvals = conn.execute("""
        SELECT peer_uid FROM controller_events
        WHERE session_id = ? AND grant_id = ?
          AND kind = 'OPERATOR' AND method = 'APPROVE' AND decision = 'APPROVED'
        LIMIT 2
        """, (session_id, grant_id)).fetchall()
    if (
        action is None or grant is None
        or (control_uid, agent_uid) != (uids.control, uids.agent)
        or binding != (action_id, grant_id)
        or target_binding != (uids.target, target.path, target.access_gid, dispatch._POLICY_VERSION)
        or (str(action.action_id), str(action.run_id), action.requester_id, action.agent_id,
            action.tool, action.resource, action.environment, action.action_fingerprint)
        != (action_id, run, requester, agent, tool, session_resource, env, fingerprint)
        or resource != session_resource
        or grant[:6] != (run, agent_uid, agent, tool, resource, env)
        or (str(approval.approval_id), approval.action_id, approval.run_id, approval.requester_id,
            approval.agent_id, approval.action_fingerprint, approval.expires_at, approval.decision)
        != (approval_id, action.action_id, action.run_id, requester, agent, fingerprint,
            action.expires_at, ApprovalDecision.APPROVED)
        or not starts_wall <= dispatch._ns(action.created_at) <= dispatch._ns(approval.created_at) <= wall
        < min(dispatch._ns(action.expires_at), grant[6])
        or not starts_elapsed <= elapsed < grant[7]
        or len(approvals) != 1
        or [approvals[0][0], LOCAL_APPROVER_ID, ["approver"]] not in json.loads(operators)
    ):
        raise ValueError("Historical dispatch binding failed")
    return action.action_id, session_id


def _read(conn, dispatch_id, uids, target):
    args = (str(dispatch_id),)
    reservation = conn.execute("SELECT * FROM dispatch_reservations WHERE dispatch_id = ?", args).fetchone()
    if reservation is None:
        return None
    action_id, session_id = _historical_binding(conn, reservation, uids, target)
    status = conn.execute("SELECT status FROM dispatch_outcomes WHERE dispatch_id = ?", args).fetchone()
    outcome = dispatch.DispatchStatus(status[0]) if status else None
    events = conn.execute("SELECT session_id, action_id, kind FROM dispatch_events WHERE dispatch_id = ? LIMIT 3",
                          args).fetchall()
    if outcome is dispatch.DispatchStatus.VALIDATED_NO_EFFECT:
        raise ValueError("Invalid historical outcome for the hardened profile")
    kinds = {"RESERVED"} if outcome is None else {"RESERVED", outcome.value}
    if len(events) != len(kinds) or set(events) != {(session_id, str(action_id), kind) for kind in kinds}:
        raise ValueError("Historical dispatch events disagree")
    evidence = conn.execute("SELECT request_digest, effect_number FROM dispatch_effect_evidence WHERE dispatch_id = ?",
                            args).fetchone()
    if evidence is None:
        if outcome in (dispatch.DispatchStatus.UNKNOWN, dispatch.DispatchStatus.SIMULATED_EFFECT_COMMITTED):
            raise ValueError("Historical dispatch digest is missing")
        return HistoricalDispatch(outcome, None, None)
    digest, number = evidence
    if (
        outcome is dispatch.DispatchStatus.RESERVED_NO_SEND
        or (outcome is dispatch.DispatchStatus.SIMULATED_EFFECT_COMMITTED) != (number is not None)
        or (number is not None and not 1 <= number <= target_state._MAX_CLAIMS)
    ):
        raise ValueError("Historical dispatch outcome disagrees with effect evidence")
    key = target_state.ReceiptKey(dispatch_id=dispatch_id, action_id=action_id, request_digest=digest)
    return HistoricalDispatch(outcome, key, number)


def read_dispatch(conn, dispatch_id, uids, target, deadline):
    _remaining(deadline)
    try:
        conn.execute("BEGIN")
        return _read(conn, dispatch_id, uids, target)
    finally:
        conn.set_progress_handler(None, 0)
        if conn.in_transaction:
            conn.rollback()
