from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaptive_agentops.main import app as backend_app
from adaptive_agentops.mock_api import app as mock_app

client = TestClient(mock_app)

EXPECTED_HEALTH = {
    "service": "demo-api",
    "status": "unhealthy",
    "reason": "stalled_worker",
}


def test_demo_api_health_returns_unhealthy() -> None:
    response = client.get("/services/demo-api/health")
    assert response.status_code == 200
    assert response.json() == EXPECTED_HEALTH


def test_unknown_service_returns_404() -> None:
    response = client.get("/services/payments-api/health")
    assert response.status_code == 404


def test_mock_api_is_distinct_from_backend() -> None:
    assert isinstance(backend_app, FastAPI)
    assert isinstance(mock_app, FastAPI)
    assert mock_app is not backend_app


@pytest.fixture
def restart_counter(monkeypatch):
    monkeypatch.setattr(mock_app.state, "restart_count", 0)


def test_restart_is_one_simulated_attempt(restart_counter):
    response = client.post("/services/demo-api/restart")
    assert response.status_code == 200
    assert response.json() == {
        "service": "demo-api", "status": "restart_requested", "simulated": True
    }
    assert mock_app.state.restart_count == 1
    # Restart does not fabricate recovery or change the existing health contract.
    assert client.get("/services/demo-api/health").json() == EXPECTED_HEALTH
    assert mock_app.state.restart_count == 1


def test_independent_posts_are_not_deduplicated(restart_counter):
    first = client.post("/services/demo-api/restart")
    assert mock_app.state.restart_count == 1
    second = client.post("/services/demo-api/restart")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert mock_app.state.restart_count == 2


@pytest.mark.parametrize("path", [
    "/services/payments-api/restart", "/services/other/restart",
    "/services/demo-api/execute", "/restart",
])
def test_unsupported_restart_paths_rejected(restart_counter, path):
    assert client.post(path).status_code == 404
    assert mock_app.state.restart_count == 0


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_restart_wrong_methods_rejected(restart_counter, method):
    assert client.request(method, "/services/demo-api/restart").status_code == 405
    assert mock_app.state.restart_count == 0


@pytest.mark.parametrize("body", [
    b"{}", b"null", b"[]", b" ", b"\x00", b"not-json",
    b'{"service":"other","command":"systemctl restart demo-api"}',
    b'{"url":"http://untrusted.invalid","credentials":"SECRET"}',
])
def test_any_nonempty_body_rejected(restart_counter, body):
    response = client.post("/services/demo-api/restart", content=body)
    assert response.status_code == 400
    assert mock_app.state.restart_count == 0
    assert "SECRET" not in response.text


@pytest.mark.parametrize("query", [
    "service=other", "command=restart", "credentials=SECRET", "url=http://untrusted.invalid",
    "force=true", "unexpected=", "unexpected", "x=1&x=2",
])
def test_any_query_parameter_rejected(restart_counter, query):
    response = client.post("/services/demo-api/restart?" + query)
    assert response.status_code == 400
    assert mock_app.state.restart_count == 0
    assert "SECRET" not in response.text


def test_rejected_requests_do_not_change_existing_counter(restart_counter):
    mock_app.state.restart_count = 7
    assert client.post("/services/demo-api/restart", json={}).status_code == 400
    assert client.post("/services/demo-api/restart?force=true").status_code == 400
    assert mock_app.state.restart_count == 7


def test_restart_has_no_external_or_persistence_effects(restart_counter):
    # Initialize the in-process test transport before blocking effects.
    with TestClient(mock_app) as isolated_client:
        with ExitStack() as stack:
            effects = [stack.enter_context(patch(name, side_effect=AssertionError(name))) for name in (
                "subprocess.Popen", "os.system", "socket.create_connection",
                "socket.socket.connect", "sqlite3.connect", "os.open",
                "pathlib.Path.write_text", "pathlib.Path.write_bytes",
                "adaptive_agentops.actions.insert_pending_restart_action",
                "adaptive_agentops.approvals.record_operator_decision",
                "adaptive_agentops.dispatch_control.reserve_approved_restart",
                "adaptive_agentops.audit.append_policy_decision",
                "adaptive_agentops.audit.append_mock_health_result",
                "adaptive_agentops.audit.append_dispatch_reserved",
            )]
            response = isolated_client.post("/services/demo-api/restart")
            assert response.status_code == 200
            assert mock_app.state.restart_count == 1
            for effect in effects:
                effect.assert_not_called()


def test_only_one_fixed_post_route_added():
    assert [route.path for route in mock_app.routes if "POST" in getattr(route, "methods", set())] == [
        "/services/demo-api/restart"
    ]
