"""Pending restart records; creation grants neither approval nor execution rights.

Fingerprint v1 is SHA-256 of UTF-8 JSON with sorted keys, no whitespace,
ensure_ascii=True, and no NaN. The object contains version=1 plus run_id
(canonical lowercase UUID text), requester_id, agent_id, tool, resource,
environment, and arguments ({}). IDs and times of the action are excluded.
The hash binds scope, not identity, and is not a signature against DB tampering.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from sqlite3 import Connection
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, UUID4, model_validator

from adaptive_agentops.identity import RunContext
from adaptive_agentops.policy import PolicyDecision, evaluate_policy
from adaptive_agentops.tool_requests import EmptyArguments, MockRestartServiceProposal


def _fingerprint(scope: dict) -> str:
    canonical = json.dumps(
        {**scope, "version": 1}, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PendingRestartAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_id: UUID4
    run_id: UUID
    requester_id: Literal["local-requester"]
    agent_id: Literal["incident-investigator-v1"]
    tool: Literal["mock.restart_service"]
    resource: Literal["demo-api"]
    environment: Literal["local-mock"]
    arguments: EmptyArguments
    action_fingerprint: str
    created_at: datetime
    expires_at: datetime
    state: Literal["PENDING"]

    @model_validator(mode="after")
    def validate_scope(self):
        if (
            self.created_at.utcoffset() != timedelta(0)
            or self.expires_at.utcoffset() != timedelta(0)
            or self.expires_at - self.created_at != timedelta(minutes=10)
        ):
            raise ValueError("Invalid action timestamps")
        scope = self.model_dump(mode="json", include={
            "run_id", "requester_id", "agent_id", "tool", "resource",
            "environment", "arguments",
        })
        if self.action_fingerprint != _fingerprint(scope):
            raise ValueError("Invalid action fingerprint")
        return self


def initialize_action_schema(conn: Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS actions (
            action_id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            resource TEXT NOT NULL,
            environment TEXT NOT NULL,
            arguments TEXT NOT NULL,
            action_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            state TEXT NOT NULL
        )
    """)


def insert_pending_restart_action(
    conn: Connection, context: RunContext, proposal: MockRestartServiceProposal
) -> PendingRestartAction:
    """Create and insert server-owned scope. Durable only after caller COMMIT.

    The application must supply the trusted context; Python types are not
    authentication. No caller-supplied action object, ID, or time is accepted.
    """
    try:
        if type(context) is not RunContext or type(context.run_id) is not UUID:
            raise ValueError
        if type(proposal) is not MockRestartServiceProposal or proposal.model_extra:
            raise ValueError
        fields = dict(vars(proposal))
        arguments = fields.get("arguments")
        if type(arguments) is not EmptyArguments or vars(arguments) or arguments.model_extra:
            raise ValueError
        fields["arguments"] = {}
        proposal = MockRestartServiceProposal.model_validate(fields, strict=True)
        scope = {
            "run_id": str(context.run_id),
            "requester_id": context.requester_id,
            "agent_id": context.agent_id,
            "tool": proposal.tool,
            "resource": proposal.resource,
            "environment": context.environment,
            "arguments": {},
        }
        if evaluate_policy(context, proposal) is not PolicyDecision.APPROVAL_REQUIRED:
            raise ValueError
        now = datetime.now(timezone.utc)
        action = PendingRestartAction(
            **{**scope, "run_id": context.run_id}, action_id=uuid4(),
            action_fingerprint=_fingerprint(scope), created_at=now,
            expires_at=now + timedelta(minutes=10), state="PENDING",
        )
    except Exception:
        raise ValueError("Invalid or unauthorized pending restart action") from None

    values = action.model_dump(mode="json")
    values["arguments"] = "{}"
    conn.execute("""
        INSERT INTO actions (
            action_id, run_id, requester_id, agent_id, tool, resource,
            environment, arguments, action_fingerprint, created_at, expires_at, state
        ) VALUES (
            :action_id, :run_id, :requester_id, :agent_id, :tool, :resource,
            :environment, :arguments, :action_fingerprint, :created_at, :expires_at, :state
        )
    """, values)
    return action


def get_pending_restart_action(
    conn: Connection, action_id: UUID
) -> PendingRestartAction | None:
    """Read validated stored scope; retrieval is not approval or an expiry check."""
    if type(action_id) is not UUID or action_id.version != 4:
        raise ValueError("Expected UUID4 action ID")
    cursor = conn.execute("""
        SELECT action_id, run_id, requester_id, agent_id, tool, resource,
               environment, arguments, action_fingerprint, created_at, expires_at, state
        FROM actions WHERE action_id = ?
    """, (str(action_id),))
    row = cursor.fetchone()
    if row is None:
        return None
    try:
        fields = dict(zip((column[0] for column in cursor.description), row))
        if fields["arguments"] != "{}":
            raise ValueError
        fields["arguments"] = {}
        return PendingRestartAction.model_validate_json(json.dumps(fields))
    except Exception:
        raise ValueError("Invalid persisted pending restart action") from None
