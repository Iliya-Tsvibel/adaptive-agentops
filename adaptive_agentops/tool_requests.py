from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MockGetHealthProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: Literal["mock.get_health"]
    resource: StrictStr = Field(min_length=1, max_length=128)
    arguments: EmptyArguments


class MockRestartServiceProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: Literal["mock.restart_service"]
    resource: StrictStr = Field(min_length=1, max_length=128)
    arguments: EmptyArguments
