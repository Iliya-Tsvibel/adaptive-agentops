from enum import Enum

from adaptive_agentops.identity import RunContext
from adaptive_agentops.tool_requests import (
    EmptyArguments,
    MockGetHealthProposal,
    MockRestartServiceProposal,
)


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


def evaluate_policy(context: object, proposal: object) -> PolicyDecision:
    if not isinstance(context, RunContext):
        return PolicyDecision.DENY
    proposal_type = type(proposal)
    if proposal_type not in (MockGetHealthProposal, MockRestartServiceProposal):
        return PolicyDecision.DENY
    try:
        # model_copy can inject fields that model_dump silently omits. Validate
        # stored fields, including nested arguments, against the concrete model.
        fields = dict(vars(proposal))
        if proposal.model_extra:
            return PolicyDecision.DENY
        arguments = fields.get("arguments")
        if isinstance(arguments, EmptyArguments):
            if type(arguments) is not EmptyArguments or arguments.model_extra:
                return PolicyDecision.DENY
            fields["arguments"] = dict(vars(arguments))
        proposal = proposal_type.model_validate(fields)
    except Exception:
        return PolicyDecision.DENY
    if (
        context.requester_id == "local-requester"
        and context.agent_id == "incident-investigator-v1"
        and context.environment == "local-mock"
        and proposal.resource == "demo-api"
    ):
        if proposal_type is MockRestartServiceProposal:
            return PolicyDecision.APPROVAL_REQUIRED
        return PolicyDecision.ALLOW
    return PolicyDecision.DENY
