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
