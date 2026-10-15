"""Internal operator persistence only: no authentication, dispatch, or policy override.

The future trusted operator handler should use BEGIN IMMEDIATE, then record a
decision and COMMIT. These primitives never commit, roll back, or retry. A second
read-only connection verifies committed action scope, so a file-backed main
database is required. SQLite UNIQUE constraints arbitrate duplicate decisions.
Triggers preserve decisions against ordinary SQL writes, not a database owner
who can remove the protections. Existing pre-release tables require a separate
migration to replace their old consumed=0 CHECK constraint.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, UUID4, model_validator

from adaptive_agentops.actions import PendingRestartAction, get_pending_restart_action
from adaptive_agentops.identity import LOCAL_APPROVER_ID


class ApprovalDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RestartApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    approval_id: UUID4
    action_id: UUID4
    run_id: UUID
    requester_id: Literal["local-requester"]
    agent_id: Literal["incident-investigator-v1"]
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approver_id: str
    decision: ApprovalDecision
    created_at: datetime
    expires_at: datetime
    consumed: bool

    @model_validator(mode="after")
    def validate_metadata(self):
        if (
            self.approver_id != LOCAL_APPROVER_ID
            or self.created_at.utcoffset() != timedelta(0)
            or self.expires_at.utcoffset() != timedelta(0)
            or self.created_at >= self.expires_at
        ):
            raise ValueError("Invalid approval metadata")
        return self


def initialize_approval_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY NOT NULL,
            action_id TEXT NOT NULL UNIQUE REFERENCES actions(action_id),
            run_id TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            action_fingerprint TEXT NOT NULL,
            approver_id TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed INTEGER NOT NULL CHECK (typeof(consumed) = 'integer' AND consumed IN (0, 1))
        )
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS approvals_guard_insert BEFORE INSERT ON approvals
        WHEN NEW.consumed IS NOT 0 OR EXISTS (
            SELECT 1 FROM approvals
            WHERE approval_id = NEW.approval_id OR action_id = NEW.action_id
        )
        BEGIN SELECT RAISE(ABORT, 'Approval must be new and unconsumed'); END
    """)
    # The INSERT guard also blocks INSERT OR REPLACE, whose implicit DELETE
    # need not fire DELETE triggers when recursive_triggers is disabled.
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS approvals_guard_update BEFORE UPDATE ON approvals
        WHEN NEW.approval_id IS NOT OLD.approval_id
          OR NEW.action_id IS NOT OLD.action_id
          OR NEW.run_id IS NOT OLD.run_id
          OR NEW.requester_id IS NOT OLD.requester_id
          OR NEW.agent_id IS NOT OLD.agent_id
          OR NEW.action_fingerprint IS NOT OLD.action_fingerprint
          OR NEW.approver_id IS NOT OLD.approver_id
          OR NEW.decision IS NOT OLD.decision
          OR NEW.created_at IS NOT OLD.created_at
          OR NEW.expires_at IS NOT OLD.expires_at
          OR NEW.consumed < OLD.consumed
        BEGIN SELECT RAISE(ABORT, 'Approval decision is immutable'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS approvals_guard_delete BEFORE DELETE ON approvals
        BEGIN SELECT RAISE(ABORT, 'Approval decision cannot be deleted'); END
    """)


def _committed_action(conn: sqlite3.Connection, action_id: UUID) -> PendingRestartAction:
    local = get_pending_restart_action(conn, action_id)
    databases = conn.execute("PRAGMA database_list").fetchall()
    path = next((row[2] for row in databases if row[1] == "main"), "")
    if not path:
        raise ValueError("A file-backed action database is required")
    observer = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
    try:
        committed = get_pending_restart_action(observer, action_id)
    finally:
        observer.close()
    if committed is None or local != committed:
        raise ValueError("A matching committed pending action is required")
    return committed


def record_operator_decision(
    conn: sqlite3.Connection, action_id: UUID, decision: ApprovalDecision
) -> RestartApproval:
    """Record one operator decision; the returned record is not yet durable."""
    if type(decision) is not ApprovalDecision:
        raise ValueError("An explicit ApprovalDecision is required")
    action = _committed_action(conn, action_id)
    now = datetime.now(timezone.utc)
    if not action.created_at <= now < action.expires_at:
        raise ValueError("Action is outside its decision window")
    approval = RestartApproval(
        approval_id=uuid4(), action_id=action.action_id, run_id=action.run_id,
        requester_id=action.requester_id, agent_id=action.agent_id,
        action_fingerprint=action.action_fingerprint, approver_id=LOCAL_APPROVER_ID,
        decision=decision, created_at=now, expires_at=action.expires_at, consumed=False,
    )
    fields = approval.model_dump(mode="json")
    fields["consumed"] = 0
    conn.execute("""
        INSERT INTO approvals (
            approval_id, action_id, run_id, requester_id, agent_id,
            action_fingerprint, approver_id, decision, created_at, expires_at, consumed
        ) VALUES (
            :approval_id, :action_id, :run_id, :requester_id, :agent_id,
            :action_fingerprint, :approver_id, :decision, :created_at, :expires_at, :consumed
        )
    """, fields)
    return approval


def get_approval_by_action_id(
    conn: sqlite3.Connection, action_id: UUID
) -> RestartApproval | None:
    """Inspect a validated, unexpired decision, including consumed records.

    Retrieval is NOT dispatch authorization. Future dispatch must independently
    require APPROVED, unconsumed, unexpired, matching scope and a fresh PDP
    decision, with atomic reservation/consumption in its own transaction.
    """
    if type(action_id) is not UUID or action_id.version != 4:
        raise ValueError("Expected UUID4 action ID")
    cursor = conn.execute("""
        SELECT approval_id, action_id, run_id, requester_id, agent_id,
               action_fingerprint, approver_id, decision, created_at, expires_at, consumed
        FROM approvals WHERE action_id = ?
    """, (str(action_id),))
    row = cursor.fetchone()
    if row is None:
        return None
    try:
        fields = dict(zip((column[0] for column in cursor.description), row))
        if type(fields["consumed"]) is not int or fields["consumed"] not in (0, 1):
            raise ValueError
        fields["consumed"] = bool(fields["consumed"])
        approval = RestartApproval.model_validate_json(json.dumps(fields))
    except Exception:
        raise ValueError("Invalid persisted approval") from None
    action = _committed_action(conn, action_id)
    if (
        any(getattr(approval, field) != getattr(action, field) for field in (
            "action_id", "run_id", "requester_id", "agent_id", "action_fingerprint", "expires_at"
        ))
        or not action.created_at <= approval.created_at <= datetime.now(timezone.utc) < action.expires_at
    ):
        raise ValueError("Invalid or expired approval binding")
    return approval
