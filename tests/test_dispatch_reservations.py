import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid1, uuid4

import pytest
from pydantic import ValidationError

from adaptive_agentops.dispatch_reservations import (
    DispatchReservation, get_dispatch_reservation_by_action_id,
    initialize_dispatch_reservation_schema,
)


def reservation(**overrides):
    return DispatchReservation(**{
        "action_id": uuid4(), "approval_id": uuid4(), "run_id": uuid4(),
        "action_fingerprint": "a" * 64, "reserved_at": datetime.now(timezone.utc),
        "state": "RESERVED", **overrides,
    })


def insert_row(conn, fields, replace=False):
    # Synthetic storage test helper, not an authorization or production API.
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    conn.execute(f"""
        {verb} INTO dispatch_reservations (
            dispatch_id, action_id, approval_id, run_id, action_fingerprint, reserved_at, state
        ) VALUES (:dispatch_id, :action_id, :approval_id, :run_id, :action_fingerprint, :reserved_at, :state)
    """, fields)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path, timeout=0)
    initialize_dispatch_reservation_schema(conn)
    conn.commit()
    try:
        yield conn, path
    finally:
        conn.close()


def test_schema_is_idempotent_and_required_columns_exist(db):
    conn, _ = db
    initialize_dispatch_reservation_schema(conn)
    columns = conn.execute("PRAGMA table_info(dispatch_reservations)").fetchall()
    assert {row[1] for row in columns} == set(DispatchReservation.model_fields)
    assert all(row[3] == 1 for row in columns)
    assert [row[1] for row in columns if row[5]] == ["dispatch_id"]


def test_valid_immutable_model_and_fresh_server_uuid():
    first = reservation()
    second = reservation(**first.model_dump(exclude={"dispatch_id"}))
    assert first.dispatch_id.version == second.dispatch_id.version == 4
    assert first.dispatch_id != second.dispatch_id
    for field in DispatchReservation.model_fields:
        with pytest.raises(ValidationError):
            setattr(first, field, getattr(first, field))
    with pytest.raises(ValidationError):
        DispatchReservation.model_validate({**first.model_dump(), "role": "admin"})


@pytest.mark.parametrize("field", ["dispatch_id", "action_id", "approval_id"])
@pytest.mark.parametrize("value", [uuid1(), "bad", str(uuid4()), 1, None, True])
def test_uuid_fields_are_strict_uuid4(field, value):
    with pytest.raises(ValidationError):
        reservation(**{field: value})


@pytest.mark.parametrize("field, value", [
    ("run_id", "invalid"), ("run_id", 123), ("run_id", None),
    ("action_fingerprint", "A" * 64), ("action_fingerprint", "g" * 64),
    ("action_fingerprint", "a" * 63), ("action_fingerprint", "a" * 65),
    ("action_fingerprint", "a" * 64 + "\n"), ("action_fingerprint", 123),
    ("action_fingerprint", b"a" * 64),
    ("reserved_at", datetime(2026, 1, 1)),
    ("reserved_at", datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))),
    ("reserved_at", "2026-01-01T00:00:00Z"), ("reserved_at", 0),
    ("state", "DISPATCHED"), ("state", None),
])
def test_model_rejects_invalid_scope_and_timestamp(field, value):
    with pytest.raises(ValidationError):
        reservation(**{field: value})


def test_json_model_requires_iso_utc_timestamp():
    stored = reservation()
    assert DispatchReservation.model_validate_json(stored.model_dump_json()) == stored
    fields = stored.model_dump(mode="json")
    for value in ("123", 123, "2026-01-01T00:00:00", "not a timestamp"):
        with pytest.raises(ValidationError):
            DispatchReservation.model_validate_json(json.dumps({**fields, "reserved_at": value}))


@pytest.mark.parametrize("field", list(DispatchReservation.model_fields))
def test_database_not_null_enforced(db, field):
    conn, _ = db
    fields = reservation().model_dump(mode="json")
    fields[field] = None
    with pytest.raises(sqlite3.IntegrityError):
        insert_row(conn, fields)


def test_database_state_enforced(db):
    fields = reservation().model_dump(mode="json")
    fields["state"] = "EXECUTED"
    with pytest.raises(sqlite3.IntegrityError):
        insert_row(db[0], fields)


@pytest.mark.parametrize("field", ["dispatch_id", "action_id", "approval_id"])
@pytest.mark.parametrize("guard_present", [False, True])
def test_sqlite_uniqueness_independent_of_insert_guard(db, field, guard_present):
    conn, _ = db
    if not guard_present:
        conn.execute("DROP TRIGGER dispatch_reservations_guard_insert")
    first = reservation()
    insert_row(conn, first.model_dump(mode="json"))
    second = reservation(**{field: getattr(first, field)})
    with pytest.raises(sqlite3.IntegrityError):
        insert_row(conn, second.model_dump(mode="json"))
    assert conn.execute("SELECT COUNT(*) FROM dispatch_reservations").fetchone()[0] == 1


@pytest.mark.parametrize("field", list(DispatchReservation.model_fields))
def test_all_updates_blocked(db, field):
    conn, _ = db
    stored = reservation()
    insert_row(conn, stored.model_dump(mode="json"))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"UPDATE dispatch_reservations SET {field}=?", ("changed",))
    conn.rollback()
    assert get_dispatch_reservation_by_action_id(conn, stored.action_id) == stored


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize("conflict", ["dispatch_id", "action_id", "approval_id"])
def test_delete_and_replace_blocked(db, recursive, conflict):
    conn, _ = db
    stored = reservation()
    insert_row(conn, stored.model_dump(mode="json"))
    conn.commit()
    conn.execute(f"PRAGMA recursive_triggers={recursive}")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM dispatch_reservations")
    conn.rollback()
    replacement = reservation(**{conflict: getattr(stored, conflict)})
    with pytest.raises(sqlite3.IntegrityError):
        insert_row(conn, replacement.model_dump(mode="json"), replace=True)
    conn.rollback()
    assert get_dispatch_reservation_by_action_id(conn, stored.action_id) == stored


@pytest.mark.parametrize("row_factory", [None, sqlite3.Row])
def test_commit_reopen_and_lookup(db, row_factory):
    conn, path = db
    stored = reservation()
    conn.execute("BEGIN IMMEDIATE")
    insert_row(conn, stored.model_dump(mode="json"))
    assert get_dispatch_reservation_by_action_id(conn, stored.action_id) == stored
    assert conn.in_transaction
    observer = sqlite3.connect(path)
    try:
        assert get_dispatch_reservation_by_action_id(observer, stored.action_id) is None
    finally:
        observer.close()
    conn.commit()
    reopened = sqlite3.connect(path)
    reopened.row_factory = row_factory
    try:
        assert get_dispatch_reservation_by_action_id(reopened, stored.action_id) == stored
        assert get_dispatch_reservation_by_action_id(reopened, uuid4()) is None
    finally:
        reopened.close()


def test_caller_rollback_removes_row(db):
    conn, path = db
    conn.execute("BEGIN IMMEDIATE")
    stored = reservation()
    insert_row(conn, stored.model_dump(mode="json"))
    assert get_dispatch_reservation_by_action_id(conn, stored.action_id) == stored
    conn.rollback()
    reopened = sqlite3.connect(path)
    try:
        assert get_dispatch_reservation_by_action_id(reopened, stored.action_id) is None
    finally:
        reopened.close()


def test_schema_initialization_does_not_commit_or_rollback(tmp_path):
    conn = sqlite3.connect(tmp_path / "schema.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        initialize_dispatch_reservation_schema(conn)
        assert conn.in_transaction
        conn.rollback()
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []
    finally:
        conn.close()


@pytest.mark.parametrize("field, value", [
    ("dispatch_id", "SECRET_UUID"), ("dispatch_id", str(uuid1())),
    ("approval_id", str(uuid1())), ("run_id", "SECRET_RUN"),
    ("action_fingerprint", "SECRET_DIGEST"), ("action_fingerprint", "A" * 64),
    ("reserved_at", "SECRET_TIMESTAMP"), ("reserved_at", "2026-01-01T00:00:00"),
    ("reserved_at", "2026-01-01T01:00:00+01:00"), ("reserved_at", 123),
    ("state", "EXECUTED"),
])
def test_corrupted_records_rejected_without_exposing_data(db, field, value):
    conn, _ = db
    stored = reservation()
    insert_row(conn, stored.model_dump(mode="json"))
    # Simulate a database owner bypassing write protections.
    conn.execute("DROP TRIGGER dispatch_reservations_guard_update")
    conn.execute("PRAGMA ignore_check_constraints=ON")
    conn.execute(f"UPDATE dispatch_reservations SET {field}=?", (value,))
    with pytest.raises(ValueError, match="Invalid persisted dispatch reservation") as error:
        get_dispatch_reservation_by_action_id(conn, stored.action_id)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("action_id", [None, 1, True, {}, uuid1(), str(uuid4()), "' OR 1=1 --"])
def test_invalid_lookup_rejected_before_sql(db, action_id):
    statements = []
    db[0].set_trace_callback(statements.append)
    with pytest.raises(ValueError):
        get_dispatch_reservation_by_action_id(db[0], action_id)
    assert statements == []


def test_lookup_uses_bound_parameter_and_sanitizes_database_errors():
    conn = MagicMock(spec=sqlite3.Connection)
    conn.execute.return_value.fetchone.return_value = None
    action_id = uuid4()
    assert get_dispatch_reservation_by_action_id(conn, action_id) is None
    sql, parameters = conn.execute.call_args.args
    assert "WHERE action_id = ?" in sql
    assert str(action_id) not in sql
    assert parameters == (str(action_id),)
    conn.execute.side_effect = sqlite3.OperationalError("SECRET_DB")
    with pytest.raises(sqlite3.DatabaseError) as error:
        get_dispatch_reservation_by_action_id(conn, action_id)
    assert str(error.value) == "Unable to read dispatch reservation"
    assert conn.execute.call_count == 2  # One call per request, no retry.
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()


def test_existing_state_and_approval_consumption_untouched(tmp_path):
    from adaptive_agentops.actions import initialize_action_schema, insert_pending_restart_action
    from adaptive_agentops.approvals import (
        ApprovalDecision, initialize_approval_schema, record_operator_decision,
    )
    from adaptive_agentops.audit import initialize_audit_schema
    from adaptive_agentops.identity import create_local_run_context
    from adaptive_agentops.tool_requests import MockRestartServiceProposal

    conn = sqlite3.connect(tmp_path / "existing.db")
    try:
        initialize_action_schema(conn)
        initialize_approval_schema(conn)
        initialize_audit_schema(conn)
        action = insert_pending_restart_action(
            conn, create_local_run_context(), MockRestartServiceProposal(
                tool="mock.restart_service", resource="demo-api", arguments={}
            ),
        )
        conn.commit()
        approval = record_operator_decision(conn, action.action_id, ApprovalDecision.APPROVED)
        conn.commit()
        before = list(conn.iterdump())
        with patch("adaptive_agentops.mock_adapter.get_demo_api_health") as adapter, patch(
            "adaptive_agentops.policy.evaluate_policy"
        ) as pdp, patch("adaptive_agentops.approvals.record_operator_decision") as decide:
            initialize_dispatch_reservation_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            stored = reservation(
                action_id=action.action_id, approval_id=approval.approval_id,
                run_id=action.run_id, action_fingerprint=action.action_fingerprint,
            )
            insert_row(conn, stored.model_dump(mode="json"))
            assert get_dispatch_reservation_by_action_id(conn, action.action_id) == stored
            conn.commit()
            adapter.assert_not_called()
            pdp.assert_not_called()
            decide.assert_not_called()
        assert all(line in list(conn.iterdump()) for line in before)
        assert conn.execute("SELECT consumed FROM approvals").fetchall() == [(0,)]
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        conn.close()
