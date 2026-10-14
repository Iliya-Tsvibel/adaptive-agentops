import pytest
from pydantic import ValidationError

from adaptive_agentops.tool_requests import MockGetHealthProposal

VALID_PROPOSAL = {
    "tool": "mock.get_health",
    "resource": "demo-api",
    "arguments": {},
}

SERVER_OWNED_FIELDS = (
    "requester_id",
    "agent_id",
    "run_id",
    "environment",
    "role",
    "action_id",
    "approval_id",
    "dispatch_id",
)


def test_valid_proposal_for_demo_api() -> None:
    proposal = MockGetHealthProposal.model_validate(VALID_PROPOSAL)

    assert proposal.tool == "mock.get_health"
    assert proposal.resource == "demo-api"
    assert proposal.arguments.model_dump() == {}


def test_unknown_tool_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MockGetHealthProposal.model_validate(
            {**VALID_PROPOSAL, "tool": "mock.delete_everything"}
        )


@pytest.mark.parametrize("field", SERVER_OWNED_FIELDS)
def test_unexpected_fields_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        MockGetHealthProposal.model_validate({**VALID_PROPOSAL, field: "client-supplied"})


def test_unexpected_arguments_are_rejected() -> None:
    with pytest.raises(ValidationError):
        MockGetHealthProposal.model_validate(
            {**VALID_PROPOSAL, "arguments": {"timeout": 1}}
        )


@pytest.mark.parametrize("resource", [123, True, None, "", "x" * 129])
def test_invalid_resource_types_are_rejected(resource: object) -> None:
    with pytest.raises(ValidationError):
        MockGetHealthProposal.model_validate({**VALID_PROPOSAL, "resource": resource})


def test_proposal_fields_cannot_be_reassigned() -> None:
    proposal = MockGetHealthProposal.model_validate(VALID_PROPOSAL)

    with pytest.raises(ValidationError):
        proposal.resource = "other-api"  # type: ignore[misc]
