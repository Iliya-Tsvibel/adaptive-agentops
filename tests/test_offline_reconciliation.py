import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from adaptive_agentops import offline_reconciliation as offline


MANIFEST = (Path(__file__).resolve().parents[1] / "evaluations/stage5_offline_cases.json").read_bytes()
CASES = json.loads(MANIFEST)["cases"]


@pytest.mark.parametrize("record", CASES, ids=[record["name"] for record in CASES])
def test_frozen_cases(record):
    data = json.dumps(record["case"]).encode()
    result = offline.analyze_fixture(data)
    assert result.status == record["expected"]
    for field, expected in record.get("expected_facts", {}).items():
        assert result.model_dump(mode="json")[field] == expected
    assert result.model_dump_json() == offline.analyze_fixture(data).model_dump_json()
    assert record["case"] == json.loads(data)
    assert record["case"]["dispatch_id"] not in result.model_dump_json()


def test_manifest_hash():
    assert hashlib.sha256(MANIFEST).hexdigest() == "3674b2b94b828084d6a47bc7136e1d8272bd9caef058cdebb2fc168b48c193ab"


@pytest.mark.parametrize("bad", [b'{"version":1,"version":1}', b'[]', b'\xff', b'{' * (64 * 1024 + 1), b'[' * 1100])
def test_invalid_and_bounded_bytes(bad):
    assert offline.analyze_fixture(bad).status == "INVALID"


def test_unknown_fields_and_row_cap():
    case = json.loads(json.dumps(CASES[0]["case"]))
    case["target"]["rows"] *= 17
    assert offline.analyze_fixture(json.dumps(case).encode()).status == "INVALID"
    case["target"]["rows"] = case["target"]["rows"][:1]
    case["controller"]["raw_payload"] = "secret-canary"
    assert "secret-canary" not in offline.analyze_fixture(json.dumps(case).encode()).model_dump_json()


def test_pure_processing(monkeypatch):
    data = json.dumps(CASES[0]["case"]).encode()
    blocked = Mock(side_effect=AssertionError("External operation"))
    monkeypatch.setattr("socket.socket", blocked)
    monkeypatch.setattr("sqlite3.connect", blocked)
    monkeypatch.setattr("builtins.open", blocked)
    result = offline.analyze_fixture(data)
    assert result.effect_sources == ("TARGET",)
    assert offline.format_result(result) == "SIMULATED_EFFECT_RECORDED (BOUND_EFFECT)"
    blocked.assert_not_called()


@pytest.mark.parametrize("record", CASES, ids=[record["name"] for record in CASES])
def test_diagnostic_frozen_cases(record):
    result = offline.analyze_fixture(json.dumps(record["case"]).encode())
    before = result.model_dump_json()
    sources = list(result.effect_sources) if result.status == "SIMULATED_EFFECT_RECORDED" else []
    expected = json.dumps({
        "version": 1, "status": record["expected"],
        "controller_coverage": result.controller_coverage, "target_coverage": result.target_coverage,
        "effect_sources": sources,
    }, separators=(",", ":"))
    diagnostic = offline.format_local_diagnostic(result)
    assert diagnostic == expected == offline.format_local_diagnostic(result)
    assert diagnostic.isascii() and len(diagnostic) <= 256
    assert result.model_dump_json() == before


class _Unexpected:
    def __hash__(self):
        pytest.fail("Diagnostic hashed an unchecked object")

    def __eq__(self, other):
        pytest.fail("Diagnostic compared an unchecked object")


@pytest.mark.parametrize("fields", [
    {}, {"status": "x" * 100_000}, {"status": _Unexpected()},
    {"status": "UNRESOLVED", "controller_coverage": _Unexpected()},
    {"status": "UNRESOLVED", "target_coverage": "capture-canary"},
    {"status": "UNRESOLVED", "effect_sources": ("TARGET",) * 3},
    {"status": "UNRESOLVED", "effect_sources": [_Unexpected()]},
    {"status": "UNRESOLVED", "effect_sources": (_Unexpected(),)},
])
def test_diagnostic_unchecked_fields(fields):
    result = offline.Result.model_construct(reason="secret-canary", **fields)
    assert offline.format_local_diagnostic(result) == (
        '{"version":1,"status":"UNRECOGNIZED","controller_coverage":null,'
        '"target_coverage":null,"effect_sources":[]}'
    )


def test_diagnostic_rejects_other_inputs():
    with pytest.raises(TypeError, match="^Expected an offline reconciliation Result$"):
        offline.format_local_diagnostic(_Unexpected())


def test_diagnostic_is_redacted_and_pure(monkeypatch):
    result = offline.Result.model_construct(
        status="SIMULATED_EFFECT_RECORDED", reason="secret-canary",
        controller_capture="identifier-canary", target_capture="capture-canary", effect_number=9182,
        effect_sources=("TARGET", "CONTROLLER"),
    )
    blocked = Mock(side_effect=AssertionError("External operation"))
    with monkeypatch.context() as patch:
        for destination in ("socket.socket", "sqlite3.connect", "builtins.open", "logging.getLogger"):
            patch.setattr(destination, blocked)
        diagnostic = offline.format_local_diagnostic(result)
    assert diagnostic == (
        '{"version":1,"status":"SIMULATED_EFFECT_RECORDED","controller_coverage":null,'
        '"target_coverage":null,"effect_sources":["CONTROLLER","TARGET"]}'
    )
    duplicate = result.model_copy(update={"effect_sources": ("TARGET", "TARGET")})
    assert json.loads(offline.format_local_diagnostic(duplicate))["effect_sources"] == ["TARGET"]
    unresolved = result.model_copy(update={"status": "UNRESOLVED"})
    assert json.loads(offline.format_local_diagnostic(unresolved))["effect_sources"] == []
    assert result.effect_sources == ("TARGET", "CONTROLLER")
    blocked.assert_not_called()
