import json

import pytest

from adaptive_agentops.agent_proposals import (
    MAX_INPUT_BYTES,
    ProposalStatus,
    parse_agent_proposal,
)
from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import MockRestartServiceProposal


@pytest.mark.parametrize("as_bytes", [False, True])
@pytest.mark.parametrize("decision", ["NO_ACTION", "PROPOSE_RESTART"])
def test_valid_decisions(decision, as_bytes):
    output = json.dumps({"decision": decision})
    result = parse_agent_proposal(output.encode() if as_bytes else output)
    if decision == "NO_ACTION":
        assert result.status is ProposalStatus.NO_ACTION
        assert result.proposal is None
    else:
        assert result.status is ProposalStatus.VALID_PROPOSAL
        assert type(result.proposal) is MockRestartServiceProposal
        assert result.proposal.model_dump() == {
            "tool": "mock.restart_service", "resource": "demo-api", "arguments": {},
        }
        assert evaluate_policy(
            create_local_run_context(), result.proposal
        ) is PolicyDecision.APPROVAL_REQUIRED
        assert evaluate_policy(None, result.proposal) is PolicyDecision.DENY


def assert_invalid(output):
    result = parse_agent_proposal(output)
    assert result.status is ProposalStatus.INVALID_OUTPUT
    assert result.proposal is None


@pytest.mark.parametrize("decision", [
    "RESTART", "no_action", "", " NO_ACTION", "PROPOSE_RESTART ",
    None, True, False, 0, 1, [], {}, ["PROPOSE_RESTART"],
])
def test_invalid_decision(decision):
    assert_invalid(json.dumps({"decision": decision}))


@pytest.mark.parametrize("decision", ["NO_ACTION", "PROPOSE_RESTART"])
@pytest.mark.parametrize("field", [
    "identity", "requester_id", "agent_id", "run_id", "environment",
    "permissions", "role", "approval", "approval_id", "approved",
    "action_id", "dispatch_id", "credentials", "resource", "tool",
    "execution_authority", "arguments", "unknown",
])
def test_forged_and_extra_fields(decision, field):
    assert_invalid(json.dumps({"decision": decision, field: "forged"}))


@pytest.mark.parametrize("arguments", [{}, {"resource": "other-api"}, []])
def test_arguments_are_forbidden_even_when_empty(arguments):
    assert_invalid(json.dumps({"decision": "PROPOSE_RESTART", "arguments": arguments}))


@pytest.mark.parametrize("output", [
    '{}', '{"other":"NO_ACTION"}',
    '{"decision":"NO_ACTION","decision":"PROPOSE_RESTART"}',
    '{"decision":"PROPOSE_RESTART","decision":"NO_ACTION"}',
    '{"decision":"NO_ACTION","decision":"NO_ACTION"}',
    r'{"decision":"NO_ACTION","\u0064ecision":"NO_ACTION"}',
    '', ' ', '{', '{"decision":"NO_ACTION",}',
    "{'decision':'NO_ACTION'}", '{decision:"NO_ACTION"}',
    '{"decision":"NO_ACTION"} trailing',
    '{"decision":"NO_ACTION"}{"decision":"PROPOSE_RESTART"}',
    '```json\n{"decision":"NO_ACTION"}\n```',
    '{"decision":"NO_ACTION" /* comment */}',
    '{"decision":"NO_ACTION\x00"}',
    'null', 'true', 'false', '0', '1.5', '"NO_ACTION"', '[]',
    '[{"decision":"PROPOSE_RESTART"}]',
    'NaN', 'Infinity', '-Infinity', '{"decision":NaN}',
    '\ufeff{"decision":"NO_ACTION"}',
    '[' * 1000 + '0' + ']' * 1000,
])
def test_invalid_json_and_shape(output):
    assert_invalid(output)


@pytest.mark.parametrize("output", [
    b'\xff', b'{"decision":"NO_ACTION"}\xff',
    b'{"decision":"\xc0\xaf"}', '\ud800',
    '{"decision":"NO_ACTION"}'.encode("utf-16"),
    '{"decision":"NO_ACTION"}'.encode("utf-32"),
])
def test_invalid_encoding(output):
    assert_invalid(output)


@pytest.mark.parametrize("output", [None, {}, [], 1, True, bytearray(b'{}')])
def test_no_input_coercion(output):
    assert_invalid(output)


@pytest.mark.parametrize("as_bytes", [False, True])
@pytest.mark.parametrize("decision", ["NO_ACTION", "PROPOSE_RESTART"])
def test_size_boundary(decision, as_bytes):
    output = json.dumps({"decision": decision}).ljust(MAX_INPUT_BYTES)
    if as_bytes:
        output = output.encode()
    assert parse_agent_proposal(output).status is not ProposalStatus.INVALID_OUTPUT
    assert_invalid(output + (b' ' if as_bytes else ' '))


def test_size_is_utf8_bytes_not_character_count():
    output = json.dumps({"decision": "é" * 1024}, ensure_ascii=False)
    assert len(output) < MAX_INPUT_BYTES < len(output.encode("utf-8"))
    assert_invalid(output)


def test_json_whitespace_and_equivalent_escapes_are_valid():
    result = parse_agent_proposal(' \r\n\t' + r'{"decision":"NO_\u0041CTION"}' + '\n')
    assert result.status is ProposalStatus.NO_ACTION
    assert result.proposal is None
