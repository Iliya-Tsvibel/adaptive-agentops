from dataclasses import replace

import pytest

from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import MockGetHealthProposal


def authorized_proposal() -> MockGetHealthProposal:
    return MockGetHealthProposal.model_validate(
        {
            "tool": "mock.get_health",
            "resource": "demo-api",
            "arguments": {},
        }
    )


def test_authorized_request_returns_allow() -> None:
    decision = evaluate_policy(create_local_run_context(), authorized_proposal())
    assert decision is PolicyDecision.ALLOW


def test_wrong_agent_returns_deny() -> None:
    context = replace(create_local_run_context(), agent_id="other-agent")
    assert evaluate_policy(context, authorized_proposal()) is PolicyDecision.DENY


def test_wrong_requester_returns_deny() -> None:
    context = replace(create_local_run_context(), requester_id="other-requester")
    assert evaluate_policy(context, authorized_proposal()) is PolicyDecision.DENY


def test_wrong_environment_returns_deny() -> None:
    context = replace(create_local_run_context(), environment="production")
    assert evaluate_policy(context, authorized_proposal()) is PolicyDecision.DENY


def test_wrong_resource_returns_deny() -> None:
    proposal = MockGetHealthProposal.model_validate(
        {
            "tool": "mock.get_health",
            "resource": "payments-api",
            "arguments": {},
        }
    )
    assert evaluate_policy(create_local_run_context(), proposal) is PolicyDecision.DENY


@pytest.mark.parametrize("proposal", [None, {}, "mock.get_health", object()])
def test_unknown_or_malformed_proposal_returns_deny(proposal: object) -> None:
    assert evaluate_policy(create_local_run_context(), proposal) is PolicyDecision.DENY
