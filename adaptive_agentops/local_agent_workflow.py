"""Explicitly opted-in trusted local preparation; stop at pending creation.

The caller owns an exclusively held connection and supplies trusted RunContext.
No outer transaction, approval, reservation, execution, or retries occur here.
Model output never supplies identity. Preserve Gateway cleanup/discard rules.
"""

from dataclasses import dataclass
from enum import Enum
from sqlite3 import Connection
from uuid import UUID

from adaptive_agentops.agent_proposals import ProposalStatus
from adaptive_agentops.gateway import execute_mock_health_read
from adaptive_agentops.identity import RunContext
from adaptive_agentops.ollama_adapter import LocalDecisionError, request_local_agent_decision
from adaptive_agentops.restart_gateway import PendingRestartStatus, create_audited_pending_restart
from adaptive_agentops.tool_requests import MockGetHealthProposal


class LocalPreparationStatus(str, Enum):
    DISABLED = "DISABLED"
    HEALTH_UNAVAILABLE = "HEALTH_UNAVAILABLE"
    ADAPTER_ERROR = "ADAPTER_ERROR"
    NO_ACTION = "NO_ACTION"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    PENDING_CREATED = "PENDING_CREATED"
    PENDING_NOT_CREATED = "PENDING_NOT_CREATED"
    PENDING_UNCERTAIN = "PENDING_UNCERTAIN"


@dataclass(frozen=True)
class LocalPreparationResult:
    status: LocalPreparationStatus
    action_id: UUID | None = None
    error: LocalDecisionError | None = None


async def prepare_local_agent_action(
    conn: Connection, context: RunContext, *, allow_local_inference: bool = False,
) -> LocalPreparationResult:
    """Do nothing unless exactly True; read, infer once, then create at most once.

    Only confirmed creation returns an ID. Uncertain creation may have persisted
    an action; never infer no effect or retry permission. Cancellation propagates.
    """
    status = LocalPreparationStatus
    if allow_local_inference is not True:
        return LocalPreparationResult(status.DISABLED)
    health_result = execute_mock_health_read(conn, context, MockGetHealthProposal(
        tool="mock.get_health", resource="demo-api", arguments={},
    ))
    # The adapter validates the actual Gateway result before constructing HTTP.
    decision = await request_local_agent_decision(health_result)
    if decision.error is LocalDecisionError.NO_OBSERVATION:
        return LocalPreparationResult(status.HEALTH_UNAVAILABLE)
    if decision.error is not None:
        return LocalPreparationResult(status.ADAPTER_ERROR, error=decision.error)
    parsed = decision.proposal_result
    if parsed is None:
        return LocalPreparationResult(status.ADAPTER_ERROR, error=LocalDecisionError.INVALID_RESPONSE)
    if parsed.status is ProposalStatus.NO_ACTION:
        return LocalPreparationResult(status.NO_ACTION)
    if parsed.status is not ProposalStatus.VALID_PROPOSAL or parsed.proposal is None:
        return LocalPreparationResult(status.INVALID_OUTPUT)
    pending = create_audited_pending_restart(conn, context, parsed.proposal)
    if pending.status is PendingRestartStatus.CREATED:
        return LocalPreparationResult(status.PENDING_CREATED, action_id=pending.action_id)
    if pending.status is PendingRestartStatus.NOT_CREATED:
        return LocalPreparationResult(status.PENDING_NOT_CREATED)
    return LocalPreparationResult(status.PENDING_UNCERTAIN)
