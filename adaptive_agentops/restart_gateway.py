"""Trusted pending-action entry point, with no approval or execution capability.

Creation and policy evidence commit together. Ambiguous commits are never
retried and return no usable ID. If rollback and close both fail, isolation
cannot be guaranteed: discard the connection and never commit/reuse it.
RunContext is supplied by trusted internal code, not an authentication token.
"""

from dataclasses import dataclass
from enum import Enum
from sqlite3 import Connection
from uuid import UUID

from adaptive_agentops.actions import insert_pending_restart_action
from adaptive_agentops.audit import _validate_restart_policy_inputs, append_restart_policy_decision
from adaptive_agentops.identity import RunContext
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import MockRestartServiceProposal


class PendingRestartStatus(str, Enum):
    CREATED = "CREATED"
    NOT_CREATED = "NOT_CREATED"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class PendingRestartResult:
    status: PendingRestartStatus
    action_id: UUID | None = None


def create_audited_pending_restart(
    conn: Connection, context: RunContext, proposal: MockRestartServiceProposal,
) -> PendingRestartResult:
    """Create once with committed evidence; leave caller transactions untouched."""
    owned = False
    commit_attempted = False
    try:
        if conn.in_transaction:
            return PendingRestartResult(PendingRestartStatus.NOT_CREATED)
        conn.execute("BEGIN IMMEDIATE")
        owned = True
        proposal = _validate_restart_policy_inputs(context, proposal)
        decision = evaluate_policy(context, proposal)
        if decision is PolicyDecision.APPROVAL_REQUIRED:
            # Preserve the primitive's independent PDP gate; never bypass it.
            action = insert_pending_restart_action(conn, context, proposal)
        elif decision is PolicyDecision.DENY:
            action = None
        else:
            raise ValueError("Unexpected restart policy decision")
        append_restart_policy_decision(conn, context, proposal, decision, action)
        commit_attempted = True
        conn.commit()
        owned = False
        if action is not None:
            return PendingRestartResult(PendingRestartStatus.CREATED, action.action_id)
        return PendingRestartResult(PendingRestartStatus.NOT_CREATED)
    except BaseException as error:
        rollback_failed = False
        if owned:
            try:
                conn.rollback()
            except Exception:
                rollback_failed = True
                try:
                    conn.close()
                except Exception:
                    pass  # Caller must discard; isolation is not confirmed.
        if not isinstance(error, Exception):
            raise
        status = PendingRestartStatus.UNCERTAIN if commit_attempted or rollback_failed else PendingRestartStatus.NOT_CREATED
        return PendingRestartResult(status)
