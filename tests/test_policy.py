from dataclasses import replace

import pytest

from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import EmptyArguments, MockGetHealthProposal, MockRestartServiceProposal


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


def restart_proposal():
    return MockRestartServiceProposal(
        tool="mock.restart_service", resource="demo-api", arguments={}
    )


def test_restart_requires_approval_deterministically():
    context = create_local_run_context()
    proposal = restart_proposal()
    for _ in range(3):
        assert evaluate_policy(context, proposal) is PolicyDecision.APPROVAL_REQUIRED


@pytest.mark.parametrize("field, value", [
    ("requester_id", "other"), ("agent_id", "other"),
    ("environment", "production"), ("requester_id", None),
])
def test_restart_wrong_context_denied(field, value):
    context = replace(create_local_run_context(), **{field: value})
    assert evaluate_policy(context, restart_proposal()) is PolicyDecision.DENY


@pytest.mark.parametrize("context", [None, {}, "local-requester", object()])
def test_restart_invalid_context_denied(context):
    assert evaluate_policy(context, restart_proposal()) is PolicyDecision.DENY


def test_restart_wrong_resource_denied():
    proposal = restart_proposal().model_copy(update={"resource": "payments-api"})
    assert evaluate_policy(create_local_run_context(), proposal) is PolicyDecision.DENY


@pytest.mark.parametrize("factory", [authorized_proposal, restart_proposal])
@pytest.mark.parametrize("update", [
    {"tool": "unknown"}, {"resource": 123}, {"resource": None},
    {"arguments": {"force": True}}, {"arguments": None},
    {"arguments": EmptyArguments().model_copy(update={"url": "secret"})},
    {"requester_id": "local-requester"}, {"role": "admin"},
    {"approval_id": "approved"}, {"action_id": "action"},
    {"dispatch_id": "dispatch"}, {"environment": "local-mock"},
    {"url": "http://untrusted.invalid"}, {"credentials": "secret"},
])
def test_tampered_copies_fail_closed(factory, update):
    proposal = factory().model_copy(update=update)
    assert evaluate_policy(create_local_run_context(), proposal) is PolicyDecision.DENY


@pytest.mark.parametrize("model, tool", [
    (MockGetHealthProposal, "mock.get_health"),
    (MockRestartServiceProposal, "mock.restart_service"),
])
@pytest.mark.parametrize("overrides", [
    {"tool": "unknown"}, {"resource": True},
    {"arguments": {"approval_id": "forged"}}, {"arguments": None},
])
def test_unchecked_invalid_models_fail_closed(model, tool, overrides):
    proposal = model.model_construct(**{
        "tool": tool, "resource": "demo-api", "arguments": {}, **overrides
    })
    assert evaluate_policy(create_local_run_context(), proposal) is PolicyDecision.DENY


@pytest.mark.parametrize("model", [MockGetHealthProposal, MockRestartServiceProposal])
def test_unchecked_missing_fields_fail_closed(model):
    assert evaluate_policy(create_local_run_context(), model.model_construct()) is PolicyDecision.DENY


def test_tool_type_confusion_cannot_allow_restart():
    context = create_local_run_context()
    disguised_restart = authorized_proposal().model_copy(update={"tool": "mock.restart_service"})
    disguised_read = restart_proposal().model_copy(update={"tool": "mock.get_health"})
    for proposal in (disguised_restart, disguised_read):
        assert evaluate_policy(context, proposal) is PolicyDecision.DENY
    unchecked_restart = MockRestartServiceProposal.model_construct(
        tool="mock.restart_service", resource="demo-api", arguments={}
    )
    assert evaluate_policy(context, unchecked_restart) is PolicyDecision.APPROVAL_REQUIRED


def test_proposal_subclasses_fail_closed():
    class ExtendedRestart(MockRestartServiceProposal):
        approval_id: str = "forged"

    proposal = ExtendedRestart(tool="mock.restart_service", resource="demo-api", arguments={})
    assert evaluate_policy(create_local_run_context(), proposal) is PolicyDecision.DENY
