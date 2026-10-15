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
