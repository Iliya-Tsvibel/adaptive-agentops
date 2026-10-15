"""Reservation storage only; a record grants neither execution nor retry rights.

Trusted future integration code must verify exact action/approval bindings and
atomically insert a reservation, consume approval, and audit within its own
BEGIN IMMEDIATE transaction. No insertion or authorization API is provided here.
UUID generation is a server-side model default, not an agent input interface.
Triggers protect ordinary SQL writes, not a database owner who can remove them.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, UUID4, ValidationInfo, field_validator


class DispatchReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dispatch_id: UUID4 = Field(default_factory=uuid4)
    action_id: UUID4
    approval_id: UUID4
    run_id: UUID
    action_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    reserved_at: datetime
    state: Literal["RESERVED"]

    @field_validator("reserved_at", mode="before")
    @classmethod
    def require_iso_timestamp(cls, value, info: ValidationInfo):
        if info.mode == "json" and isinstance(value, str):
            # JSON validation must not accept numeric strings as Unix time.
            return datetime.fromisoformat(value)
        return value

    @field_validator("reserved_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("Reservation timestamp must be UTC")
        return value


def initialize_dispatch_reservation_schema(conn: sqlite3.Connection) -> None:
    """Initialize only reservation storage; transaction ownership stays with caller."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dispatch_reservations (
            dispatch_id TEXT PRIMARY KEY NOT NULL,
            action_id TEXT NOT NULL UNIQUE,
            approval_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            action_fingerprint TEXT NOT NULL,
            reserved_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state = 'RESERVED')
        )
    """)
    # REPLACE can bypass DELETE triggers when recursive_triggers is disabled.
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS dispatch_reservations_guard_insert
        BEFORE INSERT ON dispatch_reservations
        WHEN EXISTS (
            SELECT 1 FROM dispatch_reservations
            WHERE dispatch_id = NEW.dispatch_id OR action_id = NEW.action_id
               OR approval_id = NEW.approval_id
        )
        BEGIN SELECT RAISE(ABORT, 'Reservation already exists'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS dispatch_reservations_guard_update
        BEFORE UPDATE ON dispatch_reservations
        BEGIN SELECT RAISE(ABORT, 'Reservation is immutable'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS dispatch_reservations_guard_delete
        BEFORE DELETE ON dispatch_reservations
        BEGIN SELECT RAISE(ABORT, 'Reservation cannot be deleted'); END
    """)


def get_dispatch_reservation_by_action_id(
    conn: sqlite3.Connection, action_id: UUID
) -> DispatchReservation | None:
    """Inspect validated storage only; existence is not permission to dispatch/retry."""
    if type(action_id) is not UUID or action_id.version != 4:
        raise ValueError("Expected UUID4 action ID")
    try:
        cursor = conn.execute("""
            SELECT dispatch_id, action_id, approval_id, run_id,
                   action_fingerprint, reserved_at, state
            FROM dispatch_reservations WHERE action_id = ?
        """, (str(action_id),))
        row = cursor.fetchone()
    except sqlite3.Error:
        raise sqlite3.DatabaseError("Unable to read dispatch reservation") from None
    if row is None:
        return None
    try:
        fields = dict(zip((column[0] for column in cursor.description), row))
        if any(type(value) is not str for value in fields.values()):
            raise ValueError
        for field in ("dispatch_id", "action_id", "approval_id", "run_id"):
            fields[field] = UUID(fields[field])
        fields["reserved_at"] = datetime.fromisoformat(fields["reserved_at"])
        return DispatchReservation.model_validate(fields)
    except Exception:
        raise ValueError("Invalid persisted dispatch reservation") from None
