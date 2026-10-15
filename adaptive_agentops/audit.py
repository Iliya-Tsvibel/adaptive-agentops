from datetime import datetime, timezone
from enum import Enum
from sqlite3 import Connection
from uuid import UUID, uuid4

from adaptive_agentops.actions import PendingRestartAction, get_pending_restart_action
from adaptive_agentops.dispatch_reservations import DispatchReservation, get_dispatch_reservation_by_action_id
from adaptive_agentops.identity import RunContext
from adaptive_agentops.mock_restart_adapter import (
    MockRestartAcknowledgement, MockRestartResult, RestartReason, RestartStatus,
)
from adaptive_agentops.policy import PolicyDecision
from adaptive_agentops.tool_requests import EmptyArguments, MockGetHealthProposal, MockRestartServiceProposal


class EventKind(str, Enum):
    POLICY_DECISION = "POLICY_DECISION"
    RESULT = "RESULT"
    DISPATCH_RESERVED = "DISPATCH_RESERVED"


class ExecutionStatus(str, Enum):
    NOT_DISPATCHED = "NOT_DISPATCHED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNCERTAIN = "UNCERTAIN"


class ReasonCode(str, Enum):
    POLICY_ALLOW = "POLICY_ALLOW"
    POLICY_DENY = "POLICY_DENY"
    POLICY_APPROVAL_REQUIRED = "POLICY_APPROVAL_REQUIRED"
    MOCK_HEALTH_READ_SUCCEEDED = "MOCK_HEALTH_READ_SUCCEEDED"
    MOCK_HEALTH_READ_FAILED = "MOCK_HEALTH_READ_FAILED"
    MOCK_RESTART_RESERVED = "MOCK_RESTART_RESERVED"
    MOCK_RESTART_ACKNOWLEDGED = "MOCK_RESTART_ACKNOWLEDGED"
    MOCK_RESTART_UNCERTAIN = "MOCK_RESTART_UNCERTAIN"


_REASON_BY_DECISION = {
    PolicyDecision.ALLOW: ReasonCode.POLICY_ALLOW,
    PolicyDecision.DENY: ReasonCode.POLICY_DENY,
    PolicyDecision.APPROVAL_REQUIRED: ReasonCode.POLICY_APPROVAL_REQUIRED,
}


def _validate_restart_policy_inputs(context, proposal):
    """Revalidate stored fields, not lossy model_dump output, at this boundary."""
    try:
        if (
            type(context) is not RunContext or type(context.run_id) is not UUID
            or set(vars(context)) != set(RunContext.__dataclass_fields__)
            or any(type(getattr(context, field)) is not str for field in (
                "requester_id", "agent_id", "environment"
            ))
            or type(proposal) is not MockRestartServiceProposal or proposal.model_extra
            or type(proposal.arguments) is not EmptyArguments
            or vars(proposal.arguments) or proposal.arguments.model_extra
        ):
            raise ValueError
        return MockRestartServiceProposal.model_validate(
            {**vars(proposal), "arguments": {}}, strict=True,
        )
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid restart policy inputs") from None


def append_restart_policy_decision(
    conn: Connection, context: RunContext, proposal: MockRestartServiceProposal,
    decision: PolicyDecision, action: PendingRestartAction | None = None,
) -> None:
    """Append creation/denial evidence only; caller owns policy and transaction.

    APPROVAL_REQUIRED must bind an inserted action in this transaction. DENY
    records the validated requested resource without inventing an action ID.
    No approval or execution rights are conferred. Trusted context is supplied
    by internal code; Python types do not authenticate the caller.
    """
    proposal = _validate_restart_policy_inputs(context, proposal)
    if decision is PolicyDecision.APPROVAL_REQUIRED:
        if type(action) is not PendingRestartAction or action.model_extra:
            raise ValueError("Expected pending restart action")
        action = PendingRestartAction.model_validate(dict(vars(action)))
        if (
            get_pending_restart_action(conn, action.action_id) != action
            or any(getattr(context, field) != getattr(action, field) for field in (
                "run_id", "requester_id", "agent_id", "environment"
            ))
            or proposal.tool != action.tool or proposal.resource != action.resource
            or type(action.arguments) is not EmptyArguments
            or vars(action.arguments) or action.arguments.model_extra
        ):
            raise ValueError("Inconsistent pending restart audit binding")
    elif decision is not PolicyDecision.DENY or action is not None:
        raise ValueError("Unsupported restart policy audit decision")
    conn.execute("""
        INSERT INTO audit_events (
            event_id, timestamp, event_kind, run_id, requester_id, agent_id,
            tool, resource, environment, policy_decision, execution_status,
            reason_code, action_id, approval_id, dispatch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(uuid4()), datetime.now(timezone.utc).isoformat(), EventKind.POLICY_DECISION.value,
        str(context.run_id), context.requester_id, context.agent_id, proposal.tool,
        proposal.resource, context.environment, decision.value,
        ExecutionStatus.NOT_DISPATCHED.value, _REASON_BY_DECISION[decision].value,
        str(action.action_id) if action is not None else None, None, None,
    ))


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
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS one_mock_restart_result_per_dispatch
        ON audit_events(dispatch_id)
        WHERE event_kind = 'RESULT' AND tool = 'mock.restart_service'
    """)


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


def append_mock_restart_result(
    conn: Connection,
    context: RunContext,
    reservation: DispatchReservation,
    restart_result: MockRestartResult,
) -> None:
    """Append an attempted restart's outcome; caller owns transaction and COMMIT.

    Call only after an actual adapter attempt returns. The result type cannot
    attest to an HTTP attempt; enforcing that ordering belongs to a future
    trusted controller. ACKNOWLEDGED means mock acknowledgement, not a real
    restart. UNCERTAIN does not mean no effect and never authorizes a retry.
    """
    try:
        if (
            type(context) is not RunContext or type(context.run_id) is not UUID
            or set(vars(context)) != set(RunContext.__dataclass_fields__)
            or type(reservation) is not DispatchReservation or reservation.model_extra
            or set(vars(reservation)) != set(DispatchReservation.model_fields)
            or type(restart_result) is not MockRestartResult
            or set(vars(restart_result)) != set(MockRestartResult.__dataclass_fields__)
            or type(restart_result.status) is not RestartStatus
            or type(restart_result.reason) is not RestartReason
        ):
            raise ValueError
        reservation = DispatchReservation.model_validate(dict(vars(reservation)))
        if restart_result.status is RestartStatus.ACKNOWLEDGED:
            response = restart_result.response
            if (
                restart_result.reason is not RestartReason.MOCK_ACKNOWLEDGED
                or type(response) is not MockRestartAcknowledgement or response.model_extra
            ):
                raise ValueError
            MockRestartAcknowledgement.model_validate(dict(vars(response)))
            status = ExecutionStatus.ACKNOWLEDGED
            reason = ReasonCode.MOCK_RESTART_ACKNOWLEDGED
        else:
            if restart_result.reason is RestartReason.MOCK_ACKNOWLEDGED or restart_result.response is not None:
                raise ValueError
            status = ExecutionStatus.UNCERTAIN
            reason = ReasonCode.MOCK_RESTART_UNCERTAIN
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid mock restart result inputs") from None

    stored = get_dispatch_reservation_by_action_id(conn, reservation.action_id)
    action = get_pending_restart_action(conn, reservation.action_id)
    if (
        stored != reservation or action is None
        or action.run_id != reservation.run_id
        or action.action_fingerprint != reservation.action_fingerprint
        or any(getattr(context, field) != getattr(action, field) for field in (
            "run_id", "requester_id", "agent_id", "environment"
        ))
    ):
        raise ValueError("Inconsistent restart result binding")
    # Preserve the actual historical PDP decision; do not reauthorize or enforce
    # current expiry when recording an outcome that may arrive after expiry.
    evidence = conn.execute("""
        SELECT 1 FROM audit_events
        WHERE event_kind=? AND action_id=? AND approval_id=? AND dispatch_id=?
          AND run_id=? AND requester_id=? AND agent_id=? AND environment=?
          AND tool=? AND resource=? AND policy_decision=? AND execution_status=?
          AND reason_code=?
    """, (
        EventKind.DISPATCH_RESERVED.value, str(reservation.action_id), str(reservation.approval_id),
        str(reservation.dispatch_id), str(context.run_id), context.requester_id, context.agent_id,
        context.environment, action.tool, action.resource, PolicyDecision.APPROVAL_REQUIRED.value,
        ExecutionStatus.NOT_DISPATCHED.value, ReasonCode.MOCK_RESTART_RESERVED.value,
    )).fetchone()
    if evidence is None:
        raise ValueError("Missing correlated pre-dispatch evidence")
    conn.execute("""
        INSERT INTO audit_events (
            event_id, timestamp, event_kind, run_id, requester_id, agent_id,
            tool, resource, environment, policy_decision, execution_status,
            reason_code, action_id, approval_id, dispatch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(uuid4()), datetime.now(timezone.utc).isoformat(), EventKind.RESULT.value,
        str(context.run_id), context.requester_id, context.agent_id, action.tool,
        action.resource, context.environment, PolicyDecision.APPROVAL_REQUIRED.value,
        status.value, reason.value, str(reservation.action_id), str(reservation.approval_id),
        str(reservation.dispatch_id),
    ))
