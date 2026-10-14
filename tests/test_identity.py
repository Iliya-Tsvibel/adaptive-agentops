from dataclasses import FrozenInstanceError

import pytest

from adaptive_agentops.identity import LOCAL_APPROVER_ID, create_local_run_context


def test_create_local_run_context_uses_trusted_fixture_values() -> None:
    context = create_local_run_context()

    assert context.requester_id == "local-requester"
    assert context.agent_id == "incident-investigator-v1"
    assert context.environment == "local-mock"
    assert context.run_id is not None
    assert LOCAL_APPROVER_ID == "local-approver"
    assert LOCAL_APPROVER_ID not in {
        context.requester_id,
        context.agent_id,
        context.environment,
    }


def test_create_local_run_context_assigns_unique_run_id() -> None:
    first = create_local_run_context()
    second = create_local_run_context()

    assert first.run_id != second.run_id


def test_run_context_fields_cannot_be_reassigned() -> None:
    context = create_local_run_context()

    with pytest.raises(FrozenInstanceError):
        context.requester_id = "other-requester"  # type: ignore[misc]
