"""Offline health observations: validated data, never instructions or authority."""

from adaptive_agentops.gateway import MockHealthReadResult, MockHealthReadStatus
from adaptive_agentops.mock_adapter import DemoApiHealth
from adaptive_agentops.policy import PolicyDecision


def build_agent_observation(result: object) -> DemoApiHealth | None:
    """Return a fresh health snapshot, or None for invalid/ineligible results.

    Trusted orchestration must supply the actual gateway result: these checks
    cannot prove provenance, audit persistence, run binding, or freshness.
    The reason remains untrusted data, including any instruction-like text.
    None means no usable observation, not an agent NO_ACTION decision.
    """
    if type(result) is not MockHealthReadResult:
        return None
    try:
        fields = vars(result)
        if set(fields) != {"status", "decision", "health"}:
            return None
        if (
            fields["status"] is not MockHealthReadStatus.SUCCEEDED
            or fields["decision"] is not PolicyDecision.ALLOW
        ):
            return None
        health = fields["health"]
        if type(health) is not DemoApiHealth:
            return None
        stored = vars(health)
        if set(stored) != {"service", "status", "reason"}:
            return None
        extras = health.__pydantic_extra__
        if extras is not None and (type(extras) is not dict or extras):
            return None
        if any(type(value) is not str for value in stored.values()):
            return None
        # Validate a new primitive mapping, not the potentially bypassed model.
        observation = DemoApiHealth.model_validate(dict(stored))
        observation.reason.encode("utf-8", errors="strict")
    except (AttributeError, TypeError, ValueError):
        return None
    return observation
