from dataclasses import dataclass

from adaptive_agentops.policy import PolicyDecision, evaluate_policy


@dataclass(frozen=True)
class PreflightResult:
    decision: PolicyDecision | None
    error: bool


def preflight_tool_call(context: object, proposal: object) -> PreflightResult:
    try:
        decision = evaluate_policy(context, proposal)
    except Exception:
        return PreflightResult(decision=None, error=True)
    if not isinstance(decision, PolicyDecision):
        return PreflightResult(decision=None, error=True)
    return PreflightResult(decision=decision, error=False)
