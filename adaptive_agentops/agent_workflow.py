"""Trusted offline preparation only; scripted text is intent, never authority.

The caller supplies the trusted context and owns an exclusively held connection.
No inference, credentials, approval, execution, or retries occur here. Uncertain
creation does not establish whether an action persisted; never retry blindly.
"""

from dataclasses import dataclass
from enum import Enum
from sqlite3 import Connection
from uuid import UUID

from adaptive_agentops.agent_observations import build_agent_observation
from adaptive_agentops.agent_proposals import ProposalStatus, parse_agent_proposal
from adaptive_agentops.gateway import execute_mock_health_read
from adaptive_agentops.identity import RunContext
from adaptive_agentops.restart_gateway import PendingRestartStatus, create_audited_pending_restart
from adaptive_agentops.tool_requests import MockGetHealthProposal


class OfflinePreparationStatus(str, Enum):
    HEALTH_UNAVAILABLE = "HEALTH_UNAVAILABLE"
    NO_ACTION = "NO_ACTION"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    PENDING_CREATED = "PENDING_CREATED"
    PENDING_NOT_CREATED = "PENDING_NOT_CREATED"
    PENDING_UNCERTAIN = "PENDING_UNCERTAIN"


@dataclass(frozen=True)
class OfflinePreparationResult:
    status: OfflinePreparationStatus
    action_id: UUID | None = None


def prepare_offline_agent_action(
    conn: Connection, context: RunContext, *, mock_output: object,
) -> OfflinePreparationResult:
    """Read through the gateway, validate, parse a script, and stop at pending.

    HEALTH_UNAVAILABLE includes invalid and audit-unconfirmed observations.
    Only confirmed pending creation returns an ID. No outer transaction spans
    the existing gateways; each retains its own policy and audit boundary.
    """
    status = OfflinePreparationStatus
    result = execute_mock_health_read(conn, context, MockGetHealthProposal(
        tool="mock.get_health", resource="demo-api", arguments={},
    ))
    if build_agent_observation(result) is None:
        return OfflinePreparationResult(status.HEALTH_UNAVAILABLE)

    parsed = parse_agent_proposal(mock_output)
    if parsed.status is ProposalStatus.NO_ACTION:
        return OfflinePreparationResult(status.NO_ACTION)
    if parsed.status is not ProposalStatus.VALID_PROPOSAL or parsed.proposal is None:
        return OfflinePreparationResult(status.INVALID_OUTPUT)

    pending = create_audited_pending_restart(conn, context, parsed.proposal)
    if pending.status is PendingRestartStatus.CREATED:
        return OfflinePreparationResult(status.PENDING_CREATED, pending.action_id)
    if pending.status is PendingRestartStatus.NOT_CREATED:
        return OfflinePreparationResult(status.PENDING_NOT_CREATED)
    return OfflinePreparationResult(status.PENDING_UNCERTAIN)
