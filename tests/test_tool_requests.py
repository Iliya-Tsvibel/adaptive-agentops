import pytest
from pydantic import ValidationError

from adaptive_agentops.tool_requests import EmptyArguments, MockGetHealthProposal, MockRestartServiceProposal

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


VALID_RESTART = {**VALID_PROPOSAL, "tool": "mock.restart_service"}


def test_valid_restart_is_frozen_and_has_empty_arguments():
    proposal = MockRestartServiceProposal.model_validate(VALID_RESTART)
    assert proposal.tool == "mock.restart_service"
    assert proposal.resource == "demo-api"
    assert isinstance(proposal.arguments, EmptyArguments)
    assert proposal.arguments.model_dump() == {}
    with pytest.raises(ValidationError):
        proposal.resource = "other"
    with pytest.raises(ValidationError):
        proposal.arguments.url = "http://untrusted.invalid"


@pytest.mark.parametrize("field", [*SERVER_OWNED_FIELDS, "identity", "url", "credentials"])
@pytest.mark.parametrize("nested", [False, True])
def test_restart_rejects_caller_metadata(field, nested):
    payload = {**VALID_RESTART}
    if nested:
        payload["arguments"] = {field: "forged"}
    else:
        payload[field] = "forged"
    with pytest.raises(ValidationError):
        MockRestartServiceProposal.model_validate(payload)


@pytest.mark.parametrize("field, value", [
    ("tool", "mock.get_health"), ("tool", "unknown"), ("tool", None),
    ("resource", 1), ("resource", True), ("resource", None),
    ("resource", b"demo-api"), ("resource", ""), ("resource", "x" * 129),
    ("arguments", {"force": True}), ("arguments", None),
    ("arguments", []), ("arguments", "{}"),
])
def test_restart_rejects_invalid_fields(field, value):
    with pytest.raises(ValidationError):
        MockRestartServiceProposal.model_validate({**VALID_RESTART, field: value})


@pytest.mark.parametrize("field", ["tool", "resource", "arguments"])
def test_restart_requires_all_fields(field):
    payload = {**VALID_RESTART}
    del payload[field]
    with pytest.raises(ValidationError):
        MockRestartServiceProposal.model_validate(payload)
