from enum import Enum

from adaptive_agentops.identity import RunContext
from adaptive_agentops.tool_requests import MockGetHealthProposal


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


def evaluate_policy(context: object, proposal: object) -> PolicyDecision:
    if not isinstance(context, RunContext):
        return PolicyDecision.DENY
    if not isinstance(proposal, MockGetHealthProposal):
        return PolicyDecision.DENY
    if (
        context.requester_id == "local-requester"
        and context.agent_id == "incident-investigator-v1"
        and context.environment == "local-mock"
        and proposal.tool == "mock.get_health"
        and proposal.resource == "demo-api"
    ):
        return PolicyDecision.ALLOW
    return PolicyDecision.DENY
