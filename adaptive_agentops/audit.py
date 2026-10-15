from datetime import datetime, timezone
from enum import Enum
from sqlite3 import Connection
from uuid import uuid4

from adaptive_agentops.actions import PendingRestartAction
from adaptive_agentops.dispatch_reservations import DispatchReservation
from adaptive_agentops.identity import RunContext
from adaptive_agentops.policy import PolicyDecision
from adaptive_agentops.tool_requests import MockGetHealthProposal


class EventKind(str, Enum):
    POLICY_DECISION = "POLICY_DECISION"
    RESULT = "RESULT"
    DISPATCH_RESERVED = "DISPATCH_RESERVED"


class ExecutionStatus(str, Enum):
    NOT_DISPATCHED = "NOT_DISPATCHED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ReasonCode(str, Enum):
    POLICY_ALLOW = "POLICY_ALLOW"
    POLICY_DENY = "POLICY_DENY"
    POLICY_APPROVAL_REQUIRED = "POLICY_APPROVAL_REQUIRED"
    MOCK_HEALTH_READ_SUCCEEDED = "MOCK_HEALTH_READ_SUCCEEDED"
    MOCK_HEALTH_READ_FAILED = "MOCK_HEALTH_READ_FAILED"
    MOCK_RESTART_RESERVED = "MOCK_RESTART_RESERVED"


_REASON_BY_DECISION = {
    PolicyDecision.ALLOW: ReasonCode.POLICY_ALLOW,
    PolicyDecision.DENY: ReasonCode.POLICY_DENY,
    PolicyDecision.APPROVAL_REQUIRED: ReasonCode.POLICY_APPROVAL_REQUIRED,
}


def initialize_audit_schema(conn: Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            event_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            event_kind TEXT NOT NULL,
            run_id TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            resource TEXT NOT NULL,
            environment TEXT NOT NULL,
            policy_decision TEXT NOT NULL,
            execution_status TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            action_id TEXT,
            approval_id TEXT,
            dispatch_id TEXT
        )
        """
    )


def append_policy_decision(
    conn: Connection,
    context: RunContext,
    proposal: MockGetHealthProposal,
    decision: PolicyDecision,
) -> None:
    if not isinstance(context, RunContext):
        raise TypeError("context must be a RunContext")
    if not isinstance(proposal, MockGetHealthProposal):
        raise TypeError("proposal must be a MockGetHealthProposal")
    if not isinstance(decision, PolicyDecision):
        raise TypeError("decision must be a PolicyDecision")

    conn.execute(
        """
        INSERT INTO audit_events (
            event_id,
            timestamp,
            event_kind,
            run_id,
            requester_id,
            agent_id,
            tool,
            resource,
            environment,
            policy_decision,
            execution_status,
            reason_code,
            action_id,
            approval_id,
            dispatch_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            datetime.now(timezone.utc).isoformat(),
            EventKind.POLICY_DECISION.value,
            str(context.run_id),
            context.requester_id,
            context.agent_id,
            proposal.tool,
            proposal.resource,
            context.environment,
            decision.value,
            ExecutionStatus.NOT_DISPATCHED.value,
            _REASON_BY_DECISION[decision].value,
            None,
            None,
            None,
        ),
    )


def append_mock_health_result(
    conn: Connection,
    context: RunContext,
    proposal: MockGetHealthProposal,
    status: ExecutionStatus,
) -> None:
    """Append a previously authorized read's outcome; caller owns the transaction.

    This does not verify prior authorization or establish durability. The caller
    must authorize before execution and commit this event for it to persist.
    """
    if not isinstance(context, RunContext):
        raise TypeError("context must be a RunContext")
    if not isinstance(proposal, MockGetHealthProposal):
        raise TypeError("proposal must be a MockGetHealthProposal")
    # Revalidate even models created through Pydantic's unchecked construction.
    proposal = MockGetHealthProposal.model_validate(proposal.model_dump(warnings=False))
    if proposal.resource != "demo-api" or context.environment != "local-mock":
        raise ValueError("Only the local demo-api health read is supported")
    if status is ExecutionStatus.SUCCEEDED:
        reason = ReasonCode.MOCK_HEALTH_READ_SUCCEEDED
    elif status is ExecutionStatus.FAILED:
        reason = ReasonCode.MOCK_HEALTH_READ_FAILED
    else:
        raise ValueError("status must be ExecutionStatus.SUCCEEDED or FAILED")

    conn.execute(
        """
        INSERT INTO audit_events (
            event_id, timestamp, event_kind, run_id, requester_id, agent_id,
            tool, resource, environment, policy_decision, execution_status,
            reason_code, action_id, approval_id, dispatch_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            datetime.now(timezone.utc).isoformat(),
            EventKind.RESULT.value,
            str(context.run_id),
            context.requester_id,
            context.agent_id,
            proposal.tool,
            proposal.resource,
            context.environment,
            PolicyDecision.ALLOW.value,
            status.value,
            reason.value,
            None,
            None,
            None,
        ),
    )


def append_dispatch_reserved(
    conn: Connection,
    context: RunContext,
    action: PendingRestartAction,
    reservation: DispatchReservation,
    decision: PolicyDecision,
) -> None:
    """Append pre-dispatch evidence; caller owns authorization and transaction."""
    if type(context) is not RunContext:
        raise TypeError("Expected trusted RunContext")
    if type(action) is not PendingRestartAction or type(reservation) is not DispatchReservation:
        raise TypeError("Expected action and reservation models")
    if decision is not PolicyDecision.APPROVAL_REQUIRED:
        raise ValueError("Expected fresh APPROVAL_REQUIRED decision")
    action = PendingRestartAction.model_validate(action.model_dump(warnings=False))
    reservation = DispatchReservation.model_validate(reservation.model_dump(warnings=False))
    if (
        any(getattr(context, field) != getattr(action, field) for field in (
            "run_id", "requester_id", "agent_id", "environment"
        ))
        or reservation.action_id != action.action_id
        or reservation.run_id != action.run_id
        or reservation.action_fingerprint != action.action_fingerprint
    ):
        raise ValueError("Inconsistent reservation audit binding")
    conn.execute("""
        INSERT INTO audit_events (
            event_id, timestamp, event_kind, run_id, requester_id, agent_id,
            tool, resource, environment, policy_decision, execution_status,
            reason_code, action_id, approval_id, dispatch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(uuid4()), datetime.now(timezone.utc).isoformat(), EventKind.DISPATCH_RESERVED.value,
        str(context.run_id), context.requester_id, context.agent_id,
        action.tool, action.resource, context.environment, decision.value,
        ExecutionStatus.NOT_DISPATCHED.value, ReasonCode.MOCK_RESTART_RESERVED.value,
        str(action.action_id), str(reservation.approval_id), str(reservation.dispatch_id),
    ))
