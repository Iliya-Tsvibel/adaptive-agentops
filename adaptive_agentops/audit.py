from datetime import datetime, timezone
from enum import Enum
from sqlite3 import Connection
from uuid import uuid4

from adaptive_agentops.identity import RunContext
from adaptive_agentops.policy import PolicyDecision
from adaptive_agentops.tool_requests import MockGetHealthProposal


class EventKind(str, Enum):
    POLICY_DECISION = "POLICY_DECISION"


class ExecutionStatus(str, Enum):
    NOT_DISPATCHED = "NOT_DISPATCHED"


class ReasonCode(str, Enum):
    POLICY_ALLOW = "POLICY_ALLOW"
    POLICY_DENY = "POLICY_DENY"
    POLICY_APPROVAL_REQUIRED = "POLICY_APPROVAL_REQUIRED"


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
