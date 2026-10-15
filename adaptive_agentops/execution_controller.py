"""Trusted internal mock execution; never recover POST rights from stored rows.

Use an exclusively held connection with no active transaction. Reservation owns
its transaction; HTTP runs without a write transaction; this controller owns a
separate RESULT transaction. ACKNOWLEDGED means only a simulated acknowledgement.

A crash/cancellation after reservation can leave a consumed approval and no
RESULT, whether or not the POST took effect. Never resume or retry that action.
This trades availability for at-most-one adapter invocation per reserved action,
not exactly-once infrastructure effects. Trusted code and intact SQLite storage
are assumed; RunContext is not authentication. If rollback and close both fail,
connection isolation cannot be guaranteed: discard the connection, never commit
or reuse it. No automatic reconciliation is provided.
"""

from dataclasses import dataclass
from enum import Enum
from sqlite3 import Connection
from uuid import UUID

from adaptive_agentops.audit import append_mock_restart_result
from adaptive_agentops.dispatch_control import ReservationStatus, reserve_approved_restart
from adaptive_agentops.identity import RunContext
from adaptive_agentops.mock_restart_adapter import (
    MockRestartResult, RestartReason, RestartStatus, request_demo_api_restart,
)


class MockRestartExecutionStatus(str, Enum):
    NOT_DISPATCHED = "NOT_DISPATCHED"
    RESERVATION_UNCERTAIN = "RESERVATION_UNCERTAIN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNCERTAIN = "UNCERTAIN"
    AUDIT_UNCONFIRMED = "AUDIT_UNCONFIRMED"


@dataclass(frozen=True)
class MockRestartExecutionResult:
    status: MockRestartExecutionStatus


async def execute_approved_mock_restart(
    conn: Connection, context: RunContext, action_id: UUID,
) -> MockRestartExecutionResult:
    """Reserve once, attempt once, then audit once; no retry on any outcome.

ACKNOWLEDGED/UNCERTAIN confirm a committed RESULT. AUDIT_UNCONFIRMED means an
adapter attempt occurred but its audit durability is unknown, not no effect.
Malformed adapter results are rejected by the audit primitive and reported as
AUDIT_UNCONFIRMED, without returning their contents.
"""
    status = MockRestartExecutionStatus
    try:
        if conn.in_transaction:
            return MockRestartExecutionResult(status.NOT_DISPATCHED)
        reserved = reserve_approved_restart(conn, context, action_id)
        if reserved.status is ReservationStatus.NOT_RESERVED:
            return MockRestartExecutionResult(status.NOT_DISPATCHED)
        if (
            reserved.status is not ReservationStatus.RESERVED
            or reserved.reservation is None or conn.in_transaction
        ):
            return MockRestartExecutionResult(status.RESERVATION_UNCERTAIN)
    except Exception:
        return MockRestartExecutionResult(status.RESERVATION_UNCERTAIN)

    try:
        outcome = await request_demo_api_restart()
    except Exception:
        outcome = MockRestartResult(RestartStatus.UNCERTAIN, RestartReason.ADAPTER_ERROR)

    owned = False
    try:
        # Never commit/rollback a transaction another component opened during I/O.
        if conn.in_transaction:
            return MockRestartExecutionResult(status.AUDIT_UNCONFIRMED)
        conn.execute("BEGIN IMMEDIATE")
        owned = True
        append_mock_restart_result(conn, context, reserved.reservation, outcome)
        conn.commit()
        owned = False
    except BaseException as error:
        if owned:
            try:
                conn.rollback()
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass  # See the explicit connection-isolation limit above.
        if not isinstance(error, Exception):
            raise
        return MockRestartExecutionResult(status.AUDIT_UNCONFIRMED)
    confirmed = status.ACKNOWLEDGED if outcome.status is RestartStatus.ACKNOWLEDGED else status.UNCERTAIN
    return MockRestartExecutionResult(confirmed)
