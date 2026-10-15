import inspect
import json

import httpx
import pytest
from pydantic import ValidationError

from adaptive_agentops import mock_adapter


VALID_HEALTH = {
    "service": "demo-api",
    "status": "unhealthy",
    "reason": "stalled_worker",
}


class BodyStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


@pytest.fixture
def mock_http(monkeypatch):
    requests = []
    options = {}
    transport_options = {}
    responder = None
    real_client = httpx.Client

    def handle(request):
        requests.append(request)
        assert responder is not None
        return responder(request)

    def transport(**kwargs):
        transport_options.update(kwargs)
        return httpx.MockTransport(handle)

    def client(**kwargs):
        options.update(kwargs)
        return real_client(**kwargs)

    monkeypatch.setattr(mock_adapter.httpx, "HTTPTransport", transport)
    monkeypatch.setattr(mock_adapter.httpx, "Client", client)

    def configure(callback):
        nonlocal responder
        responder = callback
        return requests, options, transport_options

    return configure


def response(body, status=200, headers=None):
    return httpx.Response(status, headers=headers, stream=BodyStream([body]))


@pytest.mark.parametrize("status", ["healthy", "unhealthy"])
def test_success_and_fixed_request(mock_http, monkeypatch, status):
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.invalid:9999")
    payload = {**VALID_HEALTH, "status": status}
    requests, options, transport_options = mock_http(
        lambda request: response(json.dumps(payload).encode())
    )

    result = mock_adapter.get_demo_api_health()

    assert isinstance(result, mock_adapter.DemoApiHealth)
    assert result.model_dump() == payload
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == "http://127.0.0.1:8001/services/demo-api/health"
    assert requests[0].extensions["timeout"] == {
        "connect": 2.0, "read": 2.0, "write": 2.0, "pool": 2.0
    }
    assert requests[0].headers["accept-encoding"] == "identity"
    assert options["follow_redirects"] is False
    assert options["trust_env"] is False
    assert transport_options == {"retries": 0, "trust_env": False}
    assert not inspect.signature(mock_adapter.get_demo_api_health).parameters


@pytest.mark.parametrize("status", [201, 204, 301, 302, 307, 400, 404, 500, 503])
def test_non_200_rejected_without_retry_or_redirect(mock_http, status):
    stream = BodyStream([b"untrusted"])
    requests, _, _ = mock_http(lambda request: httpx.Response(
        status, headers={"Location": "http://untrusted.invalid/"}, stream=stream
    ))
    with pytest.raises(httpx.HTTPStatusError):
        mock_adapter.get_demo_api_health()
    assert len(requests) == 1
    assert stream.reads == 0
    assert stream.closed


@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError])
def test_network_errors_are_not_retried(mock_http, error):
    def fail(request):
        raise error("controlled failure", request=request)

    requests, _, _ = mock_http(fail)
    with pytest.raises(error):
        mock_adapter.get_demo_api_health()
    assert len(requests) == 1


@pytest.mark.parametrize("payload", [
    {}, [], None,
    {**VALID_HEALTH, "service": "other"},
    {**VALID_HEALTH, "status": "unknown"},
    {**VALID_HEALTH, "reason": 123},
    {**VALID_HEALTH, "reason": True},
    {**VALID_HEALTH, "reason": None},
    {**VALID_HEALTH, "reason": ""},
    {**VALID_HEALTH, "reason": "x" * 1025},
    {**VALID_HEALTH, "extra": "forbidden"},
    {"service": "demo-api", "status": "healthy"},
])
def test_invalid_schema(mock_http, payload):
    mock_http(lambda request: response(json.dumps(payload).encode()))
    with pytest.raises(ValidationError):
        mock_adapter.get_demo_api_health()


def test_invalid_json(mock_http):
    mock_http(lambda request: response(b"not JSON"))
    with pytest.raises(ValidationError):
        mock_adapter.get_demo_api_health()


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}])
@pytest.mark.parametrize("chunks", [[b"x" * 4097], [b"x" * 4096, b"x"]])
def test_oversized_body_stops_reading(mock_http, headers, chunks):
    stream = BodyStream([*chunks, b"must not be read"])
    requests, _, _ = mock_http(lambda request: httpx.Response(
        200, headers=headers, stream=stream
    ))
    with pytest.raises(ValueError, match="4096 bytes"):
        mock_adapter.get_demo_api_health()
    assert stream.reads == len(chunks)
    assert stream.closed
    assert len(requests) == 1


def test_exact_byte_limit_and_reason_limit(mock_http):
    payload = {**VALID_HEALTH, "reason": "x" * 1024}
    body = json.dumps(payload).encode().ljust(4096, b" ")
    mock_http(lambda request: response(body))
    assert mock_adapter.get_demo_api_health().model_dump() == payload


def test_encoded_body_is_rejected_before_reading(mock_http):
    stream = BodyStream([b"compressed data"])
    mock_http(lambda request: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, stream=stream
    ))
    with pytest.raises(ValueError, match="Encoded"):
        mock_adapter.get_demo_api_health()
    assert stream.reads == 0
    assert stream.closed
