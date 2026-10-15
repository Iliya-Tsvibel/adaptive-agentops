"""Trusted internal reservation checkpoint only; never executes or retries a tool.

RunContext is a local trusted fixture, not cryptographic workload authentication.
A RESERVED outcome confirms database durability, not mutation execution. An
UNCERTAIN commit requires reconciliation; a stored reservation is not retry permission.
For ordinary errors, rollback failure returns UNCERTAIN and attempts to close
the owned connection.
If closing itself fails, invalidation cannot be guaranteed: the caller must discard
the connection and must not commit or reuse it. Interruption exceptions propagate
after owned-transaction cleanup. An interruption during COMMIT does not establish
whether it committed; never infer dispatch permission or retry from that exception.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from sqlite3 import Connection
from uuid import UUID, uuid4

from adaptive_agentops.actions import get_pending_restart_action
from adaptive_agentops.approvals import ApprovalDecision, get_approval_by_action_id
from adaptive_agentops.audit import append_dispatch_reserved
from adaptive_agentops.dispatch_reservations import (
    DispatchReservation, get_dispatch_reservation_by_action_id,
)
from adaptive_agentops.identity import RunContext
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import MockRestartServiceProposal


class ReservationStatus(str, Enum):
    RESERVED = "RESERVED"
    NOT_RESERVED = "NOT_RESERVED"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class ReservationResult:
    status: ReservationStatus
    reservation: DispatchReservation | None = None


def reserve_approved_restart(
    conn: Connection, context: RunContext, action_id: UUID
) -> ReservationResult:
    """Consume approval, reserve, and audit atomically; no automatic retries."""
    owned = False
    commit_attempted = False
    try:
        if conn.in_transaction:
            return ReservationResult(ReservationStatus.NOT_RESERVED)
        if (
            type(context) is not RunContext or type(context.run_id) is not UUID
            or type(action_id) is not UUID or action_id.version != 4
        ):
            return ReservationResult(ReservationStatus.NOT_RESERVED)
        conn.execute("BEGIN IMMEDIATE")
        owned = True
        action = get_pending_restart_action(conn, action_id)
        approval = get_approval_by_action_id(conn, action_id)
        if (
            action is None or approval is None or action.state != "PENDING"
            or approval.decision is not ApprovalDecision.APPROVED
            or approval.consumed is not False
            or action.action_id != action_id or approval.action_id != action_id
            or any(getattr(context, field) != getattr(action, field) for field in (
                "run_id", "requester_id", "agent_id", "environment"
            ))
            or any(getattr(approval, field) != getattr(action, field) for field in (
                "run_id", "requester_id", "agent_id", "action_fingerprint", "expires_at"
            ))
            or get_dispatch_reservation_by_action_id(conn, action_id) is not None
        ):
            raise ValueError("Invalid reservation prerequisites")
        proposal = MockRestartServiceProposal.model_validate({
            "tool": action.tool, "resource": action.resource,
            "arguments": action.arguments.model_dump(),
        }, strict=True)
        decision = evaluate_policy(context, proposal)
        if decision is not PolicyDecision.APPROVAL_REQUIRED:
            raise ValueError("Policy does not permit approval-gated reservation")
        now = datetime.now(timezone.utc)
        if not action.created_at <= approval.created_at <= now < min(action.expires_at, approval.expires_at):
            raise ValueError("Expired reservation prerequisites")
        reservation = DispatchReservation(
            dispatch_id=uuid4(), action_id=action_id, approval_id=approval.approval_id,
            run_id=action.run_id, action_fingerprint=action.action_fingerprint,
            reserved_at=now, state="RESERVED",
        )
        updated = conn.execute("""
            UPDATE approvals SET consumed=1
            WHERE approval_id=? AND action_id=? AND consumed=0 AND decision=?
        """, (str(approval.approval_id), str(action_id), ApprovalDecision.APPROVED.value))
        if updated.rowcount != 1:
            raise ValueError("Approval was not consumed exactly once")
        conn.execute("""
            INSERT INTO dispatch_reservations (
                dispatch_id, action_id, approval_id, run_id, action_fingerprint, reserved_at, state
            ) VALUES (:dispatch_id, :action_id, :approval_id, :run_id, :action_fingerprint, :reserved_at, :state)
        """, reservation.model_dump(mode="json"))
        append_dispatch_reserved(conn, context, action, reservation, decision)
        commit_attempted = True
        conn.commit()
        owned = False
        return ReservationResult(ReservationStatus.RESERVED, reservation)
    except BaseException as error:
        interruption = error if not isinstance(error, Exception) else None
        rollback_failed = False
        if owned:
            try:
                conn.rollback()
            except BaseException as cleanup_error:
                rollback_failed = True
                if interruption is None and not isinstance(cleanup_error, Exception):
                    interruption = cleanup_error
                # Do not leave partial writes available for a later caller commit.
                try:
                    conn.close()
                except BaseException as close_error:
                    # Isolation is unconfirmed; caller must permanently discard.
                    if interruption is None and not isinstance(close_error, Exception):
                        interruption = close_error
        if interruption is not None:
            raise interruption
        status = ReservationStatus.UNCERTAIN if commit_attempted or rollback_failed else ReservationStatus.NOT_RESERVED
        return ReservationResult(status)
