"""Read-only access to the local demo API; no caller-supplied destinations."""

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictStr


class DemoApiHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    service: Literal["demo-api"]
    status: Literal["healthy", "unhealthy"]
    reason: StrictStr = Field(min_length=1, max_length=1024)


def get_demo_api_health() -> DemoApiHealth:
    """Return validated health or raise an HTTPX, validation, or ValueError.

    The two-second timeout applies to individual network operations, not a
    total wall-clock deadline. Encoded bodies are deliberately unsupported.
    """
    with httpx.Client(
        timeout=2.0,
        follow_redirects=False,
        trust_env=False,
        transport=httpx.HTTPTransport(retries=0, trust_env=False),
    ) as client:
        with client.stream(
            "GET",
            "http://127.0.0.1:8001/services/demo-api/health",
            headers={"Accept-Encoding": "identity"},
        ) as response:
            if response.status_code != 200:
                raise httpx.HTTPStatusError(
                    "Expected HTTP 200 from demo-api",
                    request=response.request,
                    response=response,
                )
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError("Encoded health responses are unsupported")

            body = bytearray()
            for chunk in response.iter_raw():
                if len(body) + len(chunk) > 4096:
                    raise ValueError("Health response exceeds 4096 bytes")
                body.extend(chunk)

            return DemoApiHealth.model_validate_json(body)
