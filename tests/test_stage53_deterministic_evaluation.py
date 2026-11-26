"""Score the independently reviewed synthetic oracle. No fixture payloads in reports."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import platform

import pytest

from adaptive_agentops import offline_reconciliation as offline


_EVALUATIONS = Path(__file__).resolve().parents[1] / "evaluations"
_DATASET_SHA256 = "7c7341a596ca034ea8f60cfbd932f7108e8c37b8b2af07579211d9cc44d0c755"
_REGRESSION_SHA256 = "3674b2b94b828084d6a47bc7136e1d8272bd9caef058cdebb2fc168b48c193ab"
_FAMILIES = ("corroboration", "uncertainty", "invalid", "diagnostic")
_DIAGNOSTIC_KEYS = ("version", "status", "controller_coverage", "target_coverage", "effect_sources")
_COVERAGES = (None, "COMPLETE", "PARTIAL", "UNAVAILABLE", "INVALID")
_STATUSES = (
    "INVALID", "INCONSISTENT", "SIMULATED_EFFECT_RECORDED", "CONTROLLER_NO_SEND_RECORDED",
    "UNRESOLVED", "UNAVAILABLE", "MISSING_EVIDENCE", "UNRECOGNIZED",
)
_PROVENANCE_FIELDS = (
    "controller_coverage", "target_coverage", "controller_capture", "target_capture",
    "controller_outcome", "target_fact", "effect_number", "effect_sources", "capture_order",
)


class _ForbiddenOperation(Exception):
    pass


def _make_unchecked(stimulus, blocked):
    class Guard:
        __slots__ = ()
        __eq__ = __hash__ = blocked

    class StatusSubclass(str):
        __slots__ = ()
        __eq__ = __hash__ = blocked

    constructors = {
        "source": lambda source: offline.Result.model_construct(
            status="SIMULATED_EFFECT_RECORDED", reason="CANARY_STAGE53", effect_sources=(source,),
        ),
        "oversize_guard": lambda: offline.Result.model_construct(
            status="SIMULATED_EFFECT_RECORDED", reason="CANARY_STAGE53", effect_sources=(Guard(), "TARGET", "TARGET"),
        ),
        "status_subclass": lambda status: offline.Result.model_construct(
            status=StatusSubclass(status), reason="CANARY_STAGE53",
        ),
    }
    return constructors[stimulus["constructor"]](*stimulus["params"])


def _diagnostic_valid(output, canaries):
    if not (type(output) is str and output.isascii() and len(output) <= 256
            and "\n" not in output and all(word not in output for word in canaries)):
        return False
    obj = json.loads(output)
    return (
        type(obj) is dict and tuple(obj) == _DIAGNOSTIC_KEYS
        and type(obj["version"]) is int and obj["version"] == 1 and obj["status"] in _STATUSES
        and all(obj[field] in _COVERAGES for field in ("controller_coverage", "target_coverage"))
        and obj["effect_sources"] in ([], ["CONTROLLER"], ["TARGET"], ["CONTROLLER", "TARGET"])
        and (obj["status"] == "SIMULATED_EFFECT_RECORDED" or not obj["effect_sources"])
    )


def _run_once(stimulus, monkeypatch):
    kind = stimulus["kind"]
    forbidden_calls = 0

    def block(*args, **kwargs):
        nonlocal forbidden_calls
        forbidden_calls += 1
        raise _ForbiddenOperation

    value = (
        _make_unchecked(stimulus, block) if kind == "unchecked"
        else bytes.fromhex(stimulus["data"]) if kind == "bytes_hex"
        else json.dumps(stimulus["case"], separators=(",", ":")).encode("utf-8")
    )
    snapshot = tuple((key, id(item)) for key, item in vars(value).items()) if kind == "unchecked" else value
    canaries = ["CANARY_STAGE53"]
    if kind == "fixture":
        case = stimulus["case"]
        canaries.extend((case["dispatch_id"], case["controller"]["capture"], case["target"]["capture"]))
    with monkeypatch.context() as patch:
        for destination in ("builtins.open", "io.open", "os.open", "socket.socket",
                            "sqlite3.connect", "logging.getLogger", "logging.Logger._log"):
            patch.setattr(destination, block)
        result = value if kind == "unchecked" else offline.analyze_fixture(value)
        output = offline.format_local_diagnostic(result)
    if forbidden_calls:
        raise _ForbiddenOperation
    after = tuple((key, id(item)) for key, item in vars(value).items()) if kind == "unchecked" else value
    return (
        result.model_dump(mode="json") if kind != "unchecked" else None,
        output,
        snapshot == after,
        _diagnostic_valid(output, canaries),
    )


def _score(case, monkeypatch):
    expected = case["expected_result"]
    try:
        first, second = (_run_once(case["stimulus"], monkeypatch) for _ in range(2))
    except _ForbiddenOperation:
        return {"critical": {"forbidden_operation_or_hook"}, "classification": False, "provenance": False}
    except Exception:
        return {"critical": {"scoring_error"}, "classification": False, "provenance": False}

    actual, output, _, _ = first
    critical = set()
    if first != second:
        critical.add("nondeterminism")
    if not (first[2] and second[2]):
        critical.add("input_mutation")
    if not (first[3] and second[3]):
        critical.add("output_boundary")
    if output != case["expected_diagnostic"]:
        critical.add("diagnostic_mismatch")
    classification = expected is None or (
        type(actual) is dict and actual.get("status") == expected["status"]
    )
    provenance = expected is None or (
        type(actual) is dict and all(actual.get(key) == expected[key] for key in _PROVENANCE_FIELDS)
    )
    if not classification:
        critical.add("classification_mismatch")
    if not provenance:
        critical.add("provenance_mismatch")
    if actual != expected:
        critical.add("result_mismatch")
    return {"critical": critical, "classification": classification, "provenance": provenance}


def _pinned_bytes(name, expected):
    data = (_EVALUATIONS / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        pytest.fail("Synthetic manifest digest mismatch", pytrace=False)
    return data


def test_stage53_deterministic_evaluation(monkeypatch):
    frozen = _pinned_bytes("stage5_deterministic_cases.json", _DATASET_SHA256)
    _pinned_bytes("stage5_offline_cases.json", _REGRESSION_SHA256)
    manifest = json.loads(frozen)
    cases = manifest["cases"]
    if (manifest["version"] != 1 or manifest["repetitions"] != 2
        or Counter(c["family"] for c in cases) != dict.fromkeys(_FAMILIES, 4)
        or len({c["id"] for c in cases}) != 16
        or sum(c["expected_result"] is not None for c in cases) != 13):
        pytest.fail("Unexpected frozen evaluation shape", pytrace=False)

    passed = Counter()
    failed = []
    classifications = provenance = critical_count = 0
    for case in cases:
        score = _score(case, monkeypatch)
        if case["expected_result"] is not None:
            classifications += score["classification"]
            provenance += score["provenance"]
        if score["critical"]:
            failed.append(f"{case['id']}:{','.join(sorted(score['critical']))}")
            critical_count += len(score["critical"])
        else:
            passed[case["family"]] += 1
    summary = (
        f"Stage 5.3 Python {platform.python_version()} repeats=2 "
        f"classification={classifications}/13 provenance={provenance}/13 "
        f"new={sum(passed.values())}/16 regression_excluded=33 critical_checks_failed={critical_count} "
        + " ".join(f"{family}={passed[family]}/4" for family in _FAMILIES)
    )
    print(summary)
    if failed:
        pytest.fail(summary + "\n" + "\n".join(failed), pytrace=False)
