from dataclasses import dataclass
from sqlite3 import Connection

from adaptive_agentops.audit import append_policy_decision
from adaptive_agentops.identity import RunContext
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import MockGetHealthProposal


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


def evaluate_and_audit_preflight(
    conn: Connection,
    context: object,
    proposal: object,
) -> PreflightResult:
    fail_closed = PreflightResult(decision=None, error=True)
    if not isinstance(context, RunContext):
        return fail_closed
    if not isinstance(proposal, MockGetHealthProposal):
        return fail_closed

    preflight = preflight_tool_call(context, proposal)
    if preflight.error or not isinstance(preflight.decision, PolicyDecision):
        return fail_closed

    transaction_started = False
    try:
        if conn.in_transaction:
            return fail_closed
        conn.execute("BEGIN")
        transaction_started = True
        append_policy_decision(conn, context, proposal, preflight.decision)
        conn.commit()
    except Exception:
        if transaction_started:
            try:
                conn.rollback()
            except Exception:
                pass
        return fail_closed
    return preflight
