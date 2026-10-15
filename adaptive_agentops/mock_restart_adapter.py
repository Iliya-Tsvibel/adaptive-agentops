"""One bounded POST to the local simulated target, without authorization logic.

UNCERTAIN does not mean the POST had no effect. Never retry automatically.
ACKNOWLEDGED confirms only the mock response, not a real infrastructure restart.
Separate invocations can each send a POST; this is not cross-call replay protection.
"""

import asyncio
from dataclasses import dataclass
from enum import Enum
import json
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, StrictBool, field_validator


_OPERATION_TIMEOUT_SECONDS = 2.0


class MockRestartAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    service: Literal["demo-api"]
    status: Literal["restart_requested"]
    simulated: StrictBool

    @field_validator("simulated")
    @classmethod
    def require_simulated(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Expected a simulated restart acknowledgement")
        return value


class RestartStatus(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNCERTAIN = "UNCERTAIN"


class RestartReason(str, Enum):
    MOCK_ACKNOWLEDGED = "MOCK_ACKNOWLEDGED"
    TIMEOUT = "TIMEOUT"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    UNEXPECTED_HTTP_STATUS = "UNEXPECTED_HTTP_STATUS"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    ADAPTER_ERROR = "ADAPTER_ERROR"


@dataclass(frozen=True)
class MockRestartResult:
    status: RestartStatus
    reason: RestartReason
    response: MockRestartAcknowledgement | None = None


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("Duplicate JSON field")
        fields[key] = value
    return fields


async def request_demo_api_restart() -> MockRestartResult:
    """Attempt one fixed POST with two-second phase and total async deadlines.

    The total deadline cancels cooperative HTTPX I/O, including a trickling body.
    It is not a hard real-time guarantee against event-loop blocking or OS stalls.
    """
    try:
        async with asyncio.timeout(_OPERATION_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=2.0, follow_redirects=False, trust_env=False,
                transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            ) as client:
                async with client.stream(
                    "POST", "http://127.0.0.1:8001/services/demo-api/restart",
                    headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                ) as response:
                    if response.status_code != 200:
                        return MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.UNEXPECTED_HTTP_STATUS)
                    if (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        != "application/json"
                        or response.headers.get("content-encoding", "identity").strip().lower() != "identity"
                    ):
                        return MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.INVALID_RESPONSE)
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(body) + len(chunk) > 4096:
                            return MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.RESPONSE_TOO_LARGE)
                        body.extend(chunk)
                    payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_fields)
                    acknowledged = MockRestartAcknowledgement.model_validate(payload)
        return MockRestartResult(RestartStatus.ACKNOWLEDGED, RestartReason.MOCK_ACKNOWLEDGED, acknowledged)
    except (TimeoutError, httpx.TimeoutException):
        return MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.TIMEOUT)
    except httpx.TransportError:
        return MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.TRANSPORT_ERROR)
    except ValueError:
        return MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.INVALID_RESPONSE)
    except Exception:
        return MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.ADAPTER_ERROR)
