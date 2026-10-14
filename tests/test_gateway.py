from unittest.mock import MagicMock, patch

from adaptive_agentops.gateway import preflight_tool_call
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


def forbidden_resource_proposal() -> MockGetHealthProposal:
    return MockGetHealthProposal.model_validate(
        {
            "tool": "mock.get_health",
            "resource": "payments-api",
            "arguments": {},
        }
    )


@patch("adaptive_agentops.gateway.evaluate_policy", wraps=evaluate_policy)
def test_authorized_proposal_returns_allow(mock_evaluate_policy: MagicMock) -> None:
    context = create_local_run_context()
    proposal = authorized_proposal()

    result = preflight_tool_call(context, proposal)

    mock_evaluate_policy.assert_called_once_with(context, proposal)
    assert result.error is False
    assert result.decision is PolicyDecision.ALLOW


def test_forbidden_resource_returns_deny() -> None:
    result = preflight_tool_call(
        create_local_run_context(), forbidden_resource_proposal()
    )
    assert result.error is False
    assert result.decision is PolicyDecision.DENY


def test_invalid_context_returns_deny() -> None:
    result = preflight_tool_call(object(), authorized_proposal())
    assert result.error is False
    assert result.decision is PolicyDecision.DENY


def test_malformed_proposal_returns_deny() -> None:
    result = preflight_tool_call(create_local_run_context(), {"tool": "mock.get_health"})
    assert result.error is False
    assert result.decision is PolicyDecision.DENY


@patch("adaptive_agentops.gateway.evaluate_policy")
def test_pdp_exception_returns_error(mock_evaluate_policy: MagicMock) -> None:
    mock_evaluate_policy.side_effect = RuntimeError("pdp failed")

    result = preflight_tool_call(create_local_run_context(), authorized_proposal())

    mock_evaluate_policy.assert_called_once()
    assert result.error is True
    assert result.decision is None
    assert result.decision is not PolicyDecision.ALLOW


@patch("adaptive_agentops.gateway.evaluate_policy")
def test_invalid_pdp_return_value_returns_error(mock_evaluate_policy: MagicMock) -> None:
    mock_evaluate_policy.return_value = "ALLOW"

    result = preflight_tool_call(create_local_run_context(), authorized_proposal())

    mock_evaluate_policy.assert_called_once()
    assert result.error is True
    assert result.decision is None
    assert result.decision is not PolicyDecision.ALLOW


@patch("adaptive_agentops.gateway.evaluate_policy")
def test_approval_required_is_preserved(mock_evaluate_policy: MagicMock) -> None:
    mock_evaluate_policy.return_value = PolicyDecision.APPROVAL_REQUIRED

    result = preflight_tool_call(create_local_run_context(), authorized_proposal())

    mock_evaluate_policy.assert_called_once()
    assert result.error is False
    assert result.decision is PolicyDecision.APPROVAL_REQUIRED
