"""Read-only Gateway; audit failure never confirms authorization or durability.

Owned transactions are rolled back on failure, including interruptions. Failed
rollback triggers connection close. If close also fails, isolation is unconfirmed:
the caller must permanently discard the connection, never commit or reuse it.
Failure outcomes do not promise connection usability. Interrupted/ambiguous COMMIT
may have persisted evidence but never authorizes dispatch or confirms a RESULT.
POLICY_DECISION commits before dispatch; RESULT cleanup preserves that evidence.
"""

from dataclasses import dataclass
from enum import Enum
from sqlite3 import Connection

from adaptive_agentops.audit import (
    ExecutionStatus,
    append_mock_health_result,
    append_policy_decision,
)
from adaptive_agentops.identity import RunContext
from adaptive_agentops.mock_adapter import DemoApiHealth, get_demo_api_health
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import MockGetHealthProposal


def _cleanup_audit_failure(conn: Connection, owned: bool, error: BaseException) -> None:
    """Clean only Gateway-owned work; propagate the first interruption encountered."""
    interruption = error if not isinstance(error, Exception) else None
    if owned:
        try:
            conn.rollback()
        except BaseException as cleanup_error:
            if interruption is None and not isinstance(cleanup_error, Exception):
                interruption = cleanup_error
            try:
                conn.close()
            except BaseException as close_error:
                # Caller must discard: closing was attempted, not confirmed.
                if interruption is None and not isinstance(close_error, Exception):
                    interruption = close_error
    if interruption is not None:
        raise interruption


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
    except BaseException as error:
        _cleanup_audit_failure(conn, transaction_started, error)
        return fail_closed
    return preflight


class MockHealthReadStatus(str, Enum):
    NOT_DISPATCHED = "NOT_DISPATCHED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    AUDIT_FAILED = "AUDIT_FAILED"


@dataclass(frozen=True)
class MockHealthReadResult:
    status: MockHealthReadStatus
    decision: PolicyDecision | None
    health: DemoApiHealth | None = None


def execute_mock_health_read(
    conn: Connection,
    context: object,
    proposal: object,
) -> MockHealthReadResult:
    """Dispatch once after committed authorization, then commit a separate RESULT."""
    preflight = evaluate_and_audit_preflight(conn, context, proposal)
    # A successful audited preflight returns only after its transaction commits.
    if preflight.error is not False or preflight.decision is not PolicyDecision.ALLOW:
        return MockHealthReadResult(
            status=MockHealthReadStatus.NOT_DISPATCHED, decision=preflight.decision
        )

    try:
        health = get_demo_api_health()
        if not isinstance(health, DemoApiHealth):
            raise TypeError("Unexpected adapter result")
        health = DemoApiHealth.model_validate(health.model_dump(warnings=False))
    except Exception:
        health = None
        status = ExecutionStatus.FAILED
    else:
        status = ExecutionStatus.SUCCEEDED

    transaction_started = False
    try:
        if conn.in_transaction:
            return MockHealthReadResult(
                status=MockHealthReadStatus.AUDIT_FAILED, decision=preflight.decision
            )
        conn.execute("BEGIN")
        transaction_started = True
        append_mock_health_result(conn, context, proposal, status)
        conn.commit()
    except BaseException as error:
        _cleanup_audit_failure(conn, transaction_started, error)
        return MockHealthReadResult(
            status=MockHealthReadStatus.AUDIT_FAILED, decision=preflight.decision
        )
    return MockHealthReadResult(
        status=MockHealthReadStatus(status.value),
        decision=preflight.decision,
        health=health,
    )
