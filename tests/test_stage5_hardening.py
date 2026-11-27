"""Additional contract regressions, outside both frozen Stage 5 populations."""

import json
from pathlib import Path

import pytest

from adaptive_agentops import offline_reconciliation as offline


MANIFEST = Path(__file__).resolve().parents[1] / "evaluations/stage5_offline_cases.json"


def fresh_case(name):
    return next(record["case"] for record in json.loads(MANIFEST.read_bytes())["cases"]
                if record["name"] == name)


def analyze(case):
    return offline.analyze_fixture(json.dumps(case).encode())


def assert_no_positive(result, status, reason):
    assert (result.status, result.reason) == (status, reason)
    assert result.effect_number is None
    assert result.effect_sources == ()
    assert json.loads(offline.format_local_diagnostic(result))["effect_sources"] == []


@pytest.mark.parametrize(("name", "positive"), [
    ("both-committed", "SIMULATED_EFFECT_RECORDED"),
    ("no-send", "CONTROLLER_NO_SEND_RECORDED"),
])
@pytest.mark.parametrize("corroborator", ["action", "approval"])
@pytest.mark.parametrize(("coverage", "status", "reason"), [
    ("COMPLETE", "INCONSISTENT", "CONTROLLER_EVIDENCE_CONFLICT"),
    ("PARTIAL", "UNRESOLVED", "OBSERVED_WITHOUT_BOUND_OUTCOME"),
])
def test_removing_corroboration_suppresses_positive(name, positive, corroborator, coverage, status, reason):
    case = fresh_case(name)
    case["controller"]["coverage"] = coverage
    assert analyze(case).status == positive
    case["controller"]["rows"][0][corroborator] = None
    assert_no_positive(analyze(case), status, reason)


@pytest.mark.parametrize("field", ["dispatch_id", "action_id", "session_id"])
def test_controller_effect_requires_matching_event(field):
    case = fresh_case("both-committed")
    assert analyze(case).effect_sources == ("CONTROLLER", "TARGET")
    case["controller"]["rows"][0]["event"][field] = "00000000-0000-4000-8000-000000009999"
    assert_no_positive(analyze(case), "INCONSISTENT", "EVENT_BINDING_CONFLICT")


@pytest.mark.parametrize("order", [1, 2], ids=["equal", "later"])
def test_later_missing_target_only_contradicts_effect_if_complete(order):
    case = fresh_case("both-committed")
    case["target"].update(order=order, rows=[], coverage="PARTIAL")
    result = analyze(case)
    assert (result.status, result.reason) == ("SIMULATED_EFFECT_RECORDED", "BOUND_EFFECT")
    assert (result.effect_sources, result.target_fact) == (("CONTROLLER",), "NOT_OBSERVED")

    case["target"]["coverage"] = "COMPLETE"
    assert_no_positive(analyze(case), "INCONSISTENT", "LATER_TARGET_EVIDENCE_CONFLICT")


def test_internal_formatter_reason_is_outside_diagnostic_disclosure():
    reason = "synthetic-canary\n\u2603" * 32
    result = offline.Result(status="UNRESOLVED", reason=reason)
    assert offline.format_result(result) == f"UNRESOLVED ({reason})"
    diagnostic = offline.format_local_diagnostic(result)
    assert diagnostic == (
        '{"version":1,"status":"UNRESOLVED","controller_coverage":null,'
        '"target_coverage":null,"effect_sources":[]}'
    )
    assert diagnostic.isascii() and len(diagnostic) <= 256
    assert result.reason == reason


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf"), True, "1"],
                         ids=["nan", "positive-infinity", "negative-infinity", "boolean", "string"])
def test_target_effect_must_be_a_bounded_json_integer(number):
    case = fresh_case("both-committed")
    case["target"]["rows"][0]["effect_number"] = number
    assert_no_positive(analyze(case), "INVALID", "INVALID_FIXTURE")
