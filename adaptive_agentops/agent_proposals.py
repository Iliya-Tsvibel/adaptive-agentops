"""Parse untrusted model output into intent, never execution authority."""

import json
from dataclasses import dataclass
from enum import Enum

from adaptive_agentops.tool_requests import EmptyArguments, MockRestartServiceProposal

MAX_INPUT_BYTES = 2048


class ProposalStatus(str, Enum):
    NO_ACTION = "NO_ACTION"
    VALID_PROPOSAL = "VALID_PROPOSAL"
    INVALID_OUTPUT = "INVALID_OUTPUT"


@dataclass(frozen=True)
class ProposalResult:
    status: ProposalStatus
    proposal: MockRestartServiceProposal | None = None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-JSON numeric constant")


def parse_agent_proposal(output: object) -> ProposalResult:
    """Accept only the two decision objects in strict UTF-8 JSON, up to 2048 bytes.

    Input must be a built-in str or bytes. JSON whitespace and equivalent
    escapes are allowed; repair, coercion, and retries are not. A valid proposal
    still requires the existing trusted policy, approval, and execution flow.
    """
    invalid = ProposalResult(ProposalStatus.INVALID_OUTPUT)
    if type(output) not in (str, bytes):
        return invalid
    # Bound work before encoding; UTF-8 uses at least one byte per code point.
    if len(output) > MAX_INPUT_BYTES:
        return invalid
    try:
        encoded = output.encode("utf-8") if type(output) is str else output
        if len(encoded) > MAX_INPUT_BYTES:
            return invalid
        decoded = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        return invalid

    if type(decoded) is not dict or set(decoded) != {"decision"}:
        return invalid
    decision = decoded["decision"]
    if type(decision) is not str:
        return invalid
    if decision == "NO_ACTION":
        return ProposalResult(ProposalStatus.NO_ACTION)
    if decision == "PROPOSE_RESTART":
        return ProposalResult(
            ProposalStatus.VALID_PROPOSAL,
            MockRestartServiceProposal(
                tool="mock.restart_service",
                resource="demo-api",
                arguments=EmptyArguments(),
            ),
        )
    return invalid
