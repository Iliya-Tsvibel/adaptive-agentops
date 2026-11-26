"""Test-only fault injection and modeled reliability for synthetic evidence."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from adaptive_agentops import offline_reconciliation as offline


_CORPUS = Path(__file__).resolve().parents[1] / "evaluations/stage5_offline_cases.json"
_BASE_CASES = ("both-committed", "snapshot-skew-missing-target")
_IO_SEAMS = (
    "builtins.open", "io.open", "os.open", "socket.socket", "sqlite3.connect",
    "logging.getLogger", "logging.Logger._log",
)


@pytest.fixture(scope="module")
def baseline_cases():
    cases = json.loads(_CORPUS.read_bytes())["cases"]
    return {
        record["name"]: json.dumps(record["case"], ensure_ascii=True).encode()
        for record in cases if record["name"] in _BASE_CASES
    }


def _check(condition, scenario, check):
    if not condition:
        pytest.fail(f"{scenario} {check}", pytrace=False)


def _invoke(monkeypatch, scenario, operation, *, seam=None, injection=None, expected=None):
    blocked = Mock(side_effect=AssertionError("External operation"))
    actual = None
    result = None
    with monkeypatch.context() as patch:
        for destination in _IO_SEAMS:
            patch.setattr(destination, blocked)
        if seam is not None:
            patch.setattr(seam, injection)
        try:
            result = operation()
        except (Exception, KeyboardInterrupt) as error:
            actual = type(error)
    _check(blocked.call_count == 0, scenario, "external-operation")
    _check(actual is expected, scenario, "unexpected-exception")
    if injection is not None:
        _check(injection.call_count == 1, scenario, "fault-call-count")
    return result


def _analyze(monkeypatch, scenario, data, **fault):
    result = _invoke(monkeypatch, scenario, lambda: offline.analyze_fixture(data), **fault)
    if result is None:
        return None
    diagnostic = _invoke(monkeypatch, scenario, lambda: offline.format_local_diagnostic(result))
    return result, diagnostic


def _encoded(case):
    return json.dumps(case, ensure_ascii=True).encode()


def _repeat(first, second, scenario):
    _check(first is not None and second is not None, scenario, "missing-observation")
    result, diagnostic = first
    other_result, other_diagnostic = second
    _check(result.model_dump_json() == other_result.model_dump_json(), scenario, "result-repeat")
    _check(diagnostic == other_diagnostic, scenario, "diagnostic-repeat")


def _no_attribution(observation, status, scenario):
    _check(observation is not None, scenario, "missing-observation")
    result, diagnostic = observation
    _check(result.status == status, scenario, "classification")
    _check(result.effect_number is None and result.effect_sources == (), scenario, "result-attribution")
    public = json.dumps({
        "version": 1,
        "status": status,
        "controller_coverage": result.controller_coverage,
        "target_coverage": result.target_coverage,
        "effect_sources": [],
    }, ensure_ascii=True, separators=(",", ":"))
    _check(diagnostic == public, scenario, "diagnostic-disclosure")
    _check(diagnostic.isascii() and len(diagnostic) <= 256, scenario, "diagnostic-bounds")


def test_r1_parser_recursion_fails_closed(monkeypatch, baseline_cases):
    scenario = "R1"
    original = baseline_cases["both-committed"]
    control = _analyze(monkeypatch, scenario, original)
    _check(control[0].status == "SIMULATED_EFFECT_RECORDED", scenario, "clean-control")

    observations = []
    for _ in range(2):
        data = _encoded(json.loads(original))
        observation = _analyze(
            monkeypatch, scenario, data, seam="json.loads", injection=Mock(side_effect=RecursionError)
        )
        _no_attribution(observation, "INVALID", scenario)
        _check(observation[0].reason == "INVALID_FIXTURE", scenario, "fault-mapping")
        _check(data == _encoded(json.loads(original)), scenario, "input-immutability")
        observations.append(observation)
    _repeat(*observations, scenario)
    _repeat(control, _analyze(monkeypatch, scenario, original), scenario)


def test_r2_parser_interruption_propagates_once(monkeypatch, baseline_cases):
    scenario = "R2"
    original = baseline_cases["both-committed"]
    control = _analyze(monkeypatch, scenario, original)
    _check(control[0].status == "SIMULATED_EFFECT_RECORDED", scenario, "clean-control")

    for _ in range(2):
        data = _encoded(json.loads(original))
        interrupted = _analyze(
            monkeypatch, scenario, data, seam="json.loads", injection=Mock(side_effect=KeyboardInterrupt),
            expected=KeyboardInterrupt,
        )
        _check(interrupted is None, scenario, "interrupt-result")
        _check(data == _encoded(json.loads(original)), scenario, "input-immutability")
        _repeat(control, _analyze(monkeypatch, scenario, data), scenario)


def test_r3_diagnostic_interruption_propagates_once(monkeypatch, baseline_cases):
    scenario = "R3"
    original = baseline_cases["both-committed"]
    expected_result, expected_diagnostic = _analyze(monkeypatch, scenario, original)
    _check(expected_result.status == "SIMULATED_EFFECT_RECORDED", scenario, "clean-control")

    for _ in range(2):
        result, clean_diagnostic = _analyze(monkeypatch, scenario, _encoded(json.loads(original)))
        _repeat((expected_result, expected_diagnostic), (result, clean_diagnostic), scenario)
        before = result.model_dump_json()
        interrupted = _invoke(
            monkeypatch, scenario, lambda: offline.format_local_diagnostic(result),
            seam="json.dumps", injection=Mock(side_effect=KeyboardInterrupt), expected=KeyboardInterrupt,
        )
        _check(interrupted is None, scenario, "interrupt-diagnostic")
        _check(result.model_dump_json() == before, scenario, "input-immutability")
        observed = _invoke(monkeypatch, scenario, lambda: offline.format_local_diagnostic(result))
        _check(observed == expected_diagnostic, scenario, "clean-diagnostic")


def test_r4_availability_does_not_resolve_an_open_reservation(monkeypatch, baseline_cases):
    scenario = "R4"
    original = baseline_cases["snapshot-skew-missing-target"]
    earlier = []
    for coverage, fact in (("UNAVAILABLE", "NOT_OBSERVED"), ("PARTIAL", "NOT_OBSERVED"), ("COMPLETE", "NO_ROW")):
        observations = []
        for _ in range(2):
            case = json.loads(original)
            case["target"]["coverage"] = coverage
            data = _encoded(case)
            observation = _analyze(monkeypatch, scenario, data)
            _no_attribution(observation, "UNRESOLVED", scenario)
            result, _ = observation
            _check(result.controller_outcome == "UNKNOWN", scenario, "controller-evidence")
            _check(result.target_coverage == coverage and result.target_fact == fact, scenario, "target-evidence")
            _check(data == _encoded(case), scenario, "input-immutability")
            observations.append(observation)
        _repeat(*observations, scenario)
        earlier.append((result, result.model_dump_json()))
    for result, prior in earlier:
        _check(result.model_dump_json() == prior, scenario, "prior-result-immutability")


def test_r5_invalid_or_contradictory_target_blocks_positive_attribution(monkeypatch, baseline_cases):
    scenario = "R5"
    original = baseline_cases["both-committed"]
    control = _analyze(monkeypatch, scenario, original)
    _check(control[0].status == "SIMULATED_EFFECT_RECORDED", scenario, "clean-control")

    for status in ("INVALID", "INCONSISTENT"):
        observations = []
        for _ in range(2):
            case = json.loads(original)
            if status == "INVALID":
                case["target"].update(coverage="INVALID", rows=[])
            else:
                case["target"]["rows"][0]["effect_number"] = None
                case["target"]["order"] = case["controller"]["order"]
            data = _encoded(case)
            observation = _analyze(monkeypatch, scenario, data)
            _no_attribution(observation, status, scenario)
            _check(data == _encoded(case), scenario, "input-immutability")
            _check(case["controller"] == json.loads(original)["controller"], scenario, "source-evidence")
            observations.append(observation)
        _repeat(*observations, scenario)
    _repeat(control, _analyze(monkeypatch, scenario, original), scenario)


def test_r6_capacity_boundaries_fail_closed(monkeypatch, baseline_cases):
    scenario = "R6"
    original = baseline_cases["both-committed"]
    control = _analyze(monkeypatch, scenario, original)
    _check(control[0].status == "SIMULATED_EFFECT_RECORDED", scenario, "clean-control")

    above_size = []
    for _ in range(2):
        data = _encoded(json.loads(original)).ljust(64 * 1024, b" ")
        _check(len(data) == 64 * 1024, scenario, "exact-byte-limit")
        _repeat(control, _analyze(monkeypatch, scenario, data), scenario)
        oversized = data + b" "
        observation = _analyze(monkeypatch, scenario, oversized)
        _no_attribution(observation, "INVALID", scenario)
        _check(data == _encoded(json.loads(original)).ljust(64 * 1024, b" ")
               and oversized == data + b" ", scenario, "byte-input-immutability")
        above_size.append(observation)
    _repeat(*above_size, scenario)

    for count, status in ((16, "INCONSISTENT"), (17, "INVALID")):
        observations = []
        for _ in range(2):
            case = json.loads(original)
            case["target"]["rows"] *= count
            data = _encoded(case)
            observation = _analyze(monkeypatch, scenario, data)
            _no_attribution(observation, status, scenario)
            _check(data == _encoded(case), scenario, "row-input-immutability")
            observations.append(observation)
        _repeat(*observations, scenario)
