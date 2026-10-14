from dataclasses import dataclass
from uuid import UUID, uuid4

# Approval identity is owned separately from the run context.
# No approval mechanism is implemented at this stage.
LOCAL_APPROVER_ID = "local-approver"


@dataclass(frozen=True)
class RunContext:
    run_id: UUID
    requester_id: str
    agent_id: str
    environment: str


def create_local_run_context() -> RunContext:
    return RunContext(
        run_id=uuid4(),
        requester_id="local-requester",
        agent_id="incident-investigator-v1",
        environment="local-mock",
    )
