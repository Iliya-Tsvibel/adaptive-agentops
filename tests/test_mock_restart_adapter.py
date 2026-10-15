import asyncio
from contextlib import ExitStack
from dataclasses import FrozenInstanceError
import inspect
import json
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from adaptive_agentops import mock_restart_adapter as adapter


VALID_RESPONSE = {"service": "demo-api", "status": "restart_requested", "simulated": True}


class BodyStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def response(body=None, status=200, headers=None, stream=None):
    if body is None:
        body = json.dumps(VALID_RESPONSE).encode()
    return httpx.Response(
        status, headers={"Content-Type": "application/json"} if headers is None else headers,
        stream=stream if stream is not None else BodyStream([body]),
    )


@pytest.fixture
def mock_http(monkeypatch):
    requests, options, transport_options = [], {}, {}
    real_client = httpx.AsyncClient
    responder = None

    async def handle(request):
        requests.append(request)
        result = responder(request)
        return await result if inspect.isawaitable(result) else result

    def transport(**kwargs):
        transport_options.update(kwargs)
        return httpx.MockTransport(handle)

    def client(**kwargs):
        options.update(kwargs)
        return real_client(**kwargs)

    monkeypatch.setattr(adapter.httpx, "AsyncHTTPTransport", transport)
    monkeypatch.setattr(adapter.httpx, "AsyncClient", client)

    def configure(callback):
        nonlocal responder
        responder = callback
        return requests, options, transport_options

    return configure


def invoke():
    return asyncio.run(adapter.request_demo_api_restart())


def assert_uncertain(result, reason):
    assert result.status is adapter.RestartStatus.UNCERTAIN
    assert result.reason is reason
    assert result.response is None
    assert "SECRET" not in repr(result)


@pytest.mark.parametrize("content_type", ["application/json", "application/json; charset=utf-8"])
def test_one_fixed_empty_post_and_strict_success(mock_http, monkeypatch, content_type):
    monkeypatch.setenv("HTTP_PROXY", "http://SECRET_PROXY.invalid:9999")
    monkeypatch.setenv("ALL_PROXY", "http://SECRET_PROXY.invalid:9999")
    stream = BodyStream([json.dumps(VALID_RESPONSE).encode()])
    requests, options, transport_options = mock_http(
        lambda request: response(stream=stream, headers={"Content-Type": content_type})
    )
    result = invoke()
    assert result.status is adapter.RestartStatus.ACKNOWLEDGED
    assert result.reason is adapter.RestartReason.MOCK_ACKNOWLEDGED
    assert result.response.model_dump() == VALID_RESPONSE
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:8001/services/demo-api/restart"
    assert request.url.port == 8001
    assert request.url.query == b""
    assert request.content == b""
    assert "authorization" not in request.headers
    assert request.headers["accept-encoding"] == "identity"
    assert request.headers["accept"] == "application/json"
    assert request.extensions["timeout"] == {"connect": 2.0, "read": 2.0, "write": 2.0, "pool": 2.0}
    assert adapter._OPERATION_TIMEOUT_SECONDS == 2.0
    assert options["follow_redirects"] is False
    assert options["trust_env"] is False
    assert transport_options == {"retries": 0, "trust_env": False}
    assert not inspect.signature(adapter.request_demo_api_restart).parameters
    assert stream.closed
    with pytest.raises(FrozenInstanceError):
        result.status = adapter.RestartStatus.UNCERTAIN
    with pytest.raises(ValidationError):
        result.response.simulated = False


@pytest.mark.parametrize("status", [201, 202, 204, 301, 302, 303, 307, 308, 400, 401, 404, 429, 500, 503])
def test_http_status_and_redirects_uncertain_without_retry(mock_http, status):
    stream = BodyStream([b"SECRET_BODY"])
    requests, _, _ = mock_http(lambda request: response(
        status=status, stream=stream, headers={"Location": "http://untrusted.invalid/"}
    ))
    assert_uncertain(invoke(), adapter.RestartReason.UNEXPECTED_HTTP_STATUS)
    assert len(requests) == 1
    assert stream.reads == 0
    assert stream.closed


@pytest.mark.parametrize("error, reason", [
    (httpx.ConnectTimeout, adapter.RestartReason.TIMEOUT),
    (httpx.ReadTimeout, adapter.RestartReason.TIMEOUT),
    (httpx.WriteTimeout, adapter.RestartReason.TIMEOUT),
    (httpx.PoolTimeout, adapter.RestartReason.TIMEOUT),
    (httpx.ConnectError, adapter.RestartReason.TRANSPORT_ERROR),
    (httpx.ReadError, adapter.RestartReason.TRANSPORT_ERROR),
    (httpx.RemoteProtocolError, adapter.RestartReason.TRANSPORT_ERROR),
    (RuntimeError, adapter.RestartReason.ADAPTER_ERROR),
])
def test_transport_failure_never_retries_or_leaks_details(mock_http, error, reason):
    def fail(request):
        raise error("SECRET_CREDENTIALS")
    requests, _, _ = mock_http(fail)
    assert_uncertain(invoke(), reason)
    assert len(requests) == 1


@pytest.mark.parametrize("headers", [{}, {"Content-Type": "text/plain"},
    {"Content-Type": "text/html"}, {"Content-Type": "application/problem+json"},
    {"Content-Type": "application/json", "Content-Encoding": "gzip"},
])
def test_invalid_headers_rejected_before_reading(mock_http, headers):
    stream = BodyStream([b"SECRET_BODY"])
    mock_http(lambda request: response(headers=headers, stream=stream))
    assert_uncertain(invoke(), adapter.RestartReason.INVALID_RESPONSE)
    assert stream.reads == 0
    assert stream.closed


@pytest.mark.parametrize("payload", [
    {}, [], None, True, "SECRET_BODY",
    {**VALID_RESPONSE, "service": "other"},
    {**VALID_RESPONSE, "status": "succeeded"},
    {**VALID_RESPONSE, "simulated": False},
    {**VALID_RESPONSE, "simulated": 1},
    {**VALID_RESPONSE, "simulated": 0},
    {**VALID_RESPONSE, "simulated": "true"},
    {**VALID_RESPONSE, "simulated": None},
    {**VALID_RESPONSE, "extra": "SECRET_EXTRA"},
    {"service": "demo-api", "status": "restart_requested"},
])
def test_invalid_response_contract(mock_http, payload):
    requests, _, _ = mock_http(lambda request: response(json.dumps(payload).encode()))
    assert_uncertain(invoke(), adapter.RestartReason.INVALID_RESPONSE)
    assert len(requests) == 1


@pytest.mark.parametrize("body", [
    b"", b"SECRET_NOT_JSON", b"\xff", b'{"service":',
    b'{"service":"other","service":"demo-api","status":"restart_requested","simulated":true}',
])
def test_malformed_json_and_duplicate_fields(mock_http, body):
    mock_http(lambda request: response(body))
    assert_uncertain(invoke(), adapter.RestartReason.INVALID_RESPONSE)


@pytest.mark.parametrize("chunks", [[b"x" * 4097], [b"x" * 4096, b"x"]])
@pytest.mark.parametrize("declared_length", [None, "1"])
def test_size_limit_stops_stream_without_trusting_content_length(mock_http, chunks, declared_length):
    stream = BodyStream([*chunks, b"MUST_NOT_READ"])
    headers = {"Content-Type": "application/json"}
    if declared_length is not None:
        headers["Content-Length"] = declared_length
    requests, _, _ = mock_http(lambda request: response(stream=stream, headers=headers))
    assert_uncertain(invoke(), adapter.RestartReason.RESPONSE_TOO_LARGE)
    assert stream.reads == len(chunks)
    assert stream.closed
    assert len(requests) == 1


def test_exact_size_limit_valid_json(mock_http):
    body = json.dumps(VALID_RESPONSE).encode().ljust(4096, b" ")
    mock_http(lambda request: response(body))
    assert invoke().status is adapter.RestartStatus.ACKNOWLEDGED


def test_connection_loss_during_body_is_uncertain(mock_http):
    class BrokenStream(BodyStream):
        async def __aiter__(self):
            yield b'{"service":"demo-api"'
            raise httpx.ReadError("SECRET_CONNECTION_LOSS")
    stream = BrokenStream([])
    requests, _, _ = mock_http(lambda request: response(stream=stream))
    assert_uncertain(invoke(), adapter.RestartReason.TRANSPORT_ERROR)
    assert stream.closed
    assert len(requests) == 1


def test_overall_deadline_cancels_blocked_request(mock_http, monkeypatch):
    monkeypatch.setattr(adapter, "_OPERATION_TIMEOUT_SECONDS", 0.01)
    cancelled = []
    async def stalled(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    requests, _, _ = mock_http(stalled)
    assert_uncertain(invoke(), adapter.RestartReason.TIMEOUT)
    assert cancelled == [True]
    assert len(requests) == 1


def test_overall_deadline_bounds_trickling_body(mock_http, monkeypatch):
    monkeypatch.setattr(adapter, "_OPERATION_TIMEOUT_SECONDS", 0.01)
    class TrickleStream(BodyStream):
        async def __aiter__(self):
            while True:
                self.reads += 1
                yield b" "
                await asyncio.sleep(0.001)
    stream = TrickleStream([])
    requests, _, _ = mock_http(lambda request: response(stream=stream))
    assert_uncertain(invoke(), adapter.RestartReason.TIMEOUT)
    assert stream.reads > 0
    assert stream.closed
    assert len(requests) == 1


def test_two_invocations_are_not_cross_call_replay_protection(mock_http):
    requests, _, _ = mock_http(lambda request: response())
    assert invoke().status is adapter.RestartStatus.ACKNOWLEDGED
    assert invoke().status is adapter.RestartStatus.ACKNOWLEDGED
    assert len(requests) == 2


def test_no_database_authorization_or_process_side_effects(mock_http):
    mock_http(lambda request: response())
    with ExitStack() as stack:
        effects = [stack.enter_context(patch(name, side_effect=AssertionError("SECRET_EFFECT"))) for name in (
            "sqlite3.connect", "subprocess.Popen", "os.system", "os.open",
            "pathlib.Path.write_text", "pathlib.Path.write_bytes",
            "adaptive_agentops.policy.evaluate_policy",
            "adaptive_agentops.actions.insert_pending_restart_action",
            "adaptive_agentops.approvals.record_operator_decision",
            "adaptive_agentops.dispatch_control.reserve_approved_restart",
            "adaptive_agentops.audit.append_dispatch_reserved",
            "adaptive_agentops.audit.append_policy_decision",
            "adaptive_agentops.audit.append_mock_health_result",
        )]
        assert invoke().status is adapter.RestartStatus.ACKNOWLEDGED
        for effect in effects:
            effect.assert_not_called()
