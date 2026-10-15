import json
import sqlite3
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from adaptive_agentops import operator_cli
from adaptive_agentops.actions import initialize_action_schema, insert_pending_restart_action
from adaptive_agentops.approvals import (
    ApprovalDecision, initialize_approval_schema, record_operator_decision,
)
from adaptive_agentops.identity import create_local_run_context
from adaptive_agentops.tool_requests import MockRestartServiceProposal


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    initialize_action_schema(conn)
    initialize_approval_schema(conn)
    action = insert_pending_restart_action(
        conn, create_local_run_context(), MockRestartServiceProposal(
            tool="mock.restart_service", resource="demo-api", arguments={}
        ),
    )
    conn.commit()
    try:
        yield conn, action, path
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def terminal(monkeypatch, capsys):
    monkeypatch.setattr(type(operator_cli.sys.stdin), "isatty", lambda self: True)
    monkeypatch.setattr(type(operator_cli.sys.stdout), "isatty", lambda self: True)
    # Tests must explicitly supply confirmation; no real terminal reads.
    monkeypatch.setattr("builtins.input", lambda prompt: "")


def run(db, command="decide", action_id=None):
    _, action, path = db
    return operator_cli.main(["--db", str(path), command, str(action_id or action.action_id)])


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]


def test_review_exact_scope_and_no_writes(db, capsys):
    conn, action, path = db
    before = path.read_bytes()
    with patch("adaptive_agentops.operator_cli.record_operator_decision") as record:
        assert run(db, "review") == 0
        record.assert_not_called()
    shown = json.loads(capsys.readouterr().out)
    assert shown == {**action.model_dump(mode="json"), "expired": False}
    assert path.read_bytes() == before
    assert count(conn) == 0
    readonly = operator_cli._open_database(str(path), True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            readonly.execute("DELETE FROM actions")
    finally:
        readonly.close()


@pytest.mark.parametrize("word, decision", [("APPROVE", "APPROVED"), ("REJECT", "REJECTED")])
def test_decision_commits_once_after_review(db, monkeypatch, capsys, word, decision):
    conn, action, path = db
    def confirm(prompt):
        shown = json.loads(capsys.readouterr().out)
        assert shown == {**action.model_dump(mode="json"), "expired": False}
        assert str(action.action_id) in prompt
        assert count(conn) == 0
        return f"{word} {action.action_id}"
    monkeypatch.setattr("builtins.input", confirm)
    with patch("adaptive_agentops.mock_adapter.get_demo_api_health") as adapter:
        assert run(db) == 0
        adapter.assert_not_called()
    assert "Decision committed:" in capsys.readouterr().out
    reopened = sqlite3.connect(path)
    try:
        assert reopened.execute("SELECT action_id, decision, consumed FROM approvals").fetchall() == [
            (str(action.action_id), decision, 0)
        ]
    finally:
        reopened.close()


@pytest.mark.parametrize("confirmation", ["", "yes", "APPROVE", "approve {id}", "APPROVE {id} ", f"APPROVE {uuid4()}"])
def test_bad_confirmation_no_changes(db, monkeypatch, confirmation):
    conn, action, path = db
    before = path.read_bytes()
    monkeypatch.setattr("builtins.input", lambda prompt: confirmation.format(id=action.action_id))
    assert run(db) == 2
    assert count(conn) == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_cancelled_input(db, error, capsys):
    with patch("builtins.input", side_effect=error):
        assert run(db) == 130
    assert count(db[0]) == 0
    assert "Decision committed:" not in capsys.readouterr().out


def test_noninteractive_decision_rejected(db, monkeypatch):
    monkeypatch.setattr(operator_cli.sys.stdin, "isatty", lambda: False)
    assert run(db) == 2
    assert count(db[0]) == 0
    assert run(db, "review") == 0


@pytest.mark.parametrize("identifier", ["bad", str(uuid4())])
def test_missing_or_invalid_action(db, identifier):
    assert run(db, action_id=identifier) != 0
    assert count(db[0]) == 0


def test_expired_review_and_decision(db, capsys):
    with patch("adaptive_agentops.operator_cli.datetime") as clock:
        clock.now.return_value = db[1].expires_at
        assert run(db, "review") == 0
        assert json.loads(capsys.readouterr().out)["expired"] is True
        assert run(db) == 1
    assert count(db[0]) == 0


@pytest.mark.parametrize("decision", list(ApprovalDecision))
def test_previously_decided_action(db, decision):
    conn, action, _ = db
    record_operator_decision(conn, action.action_id, decision)
    conn.commit()
    with patch("builtins.input") as prompt:
        assert run(db) == 1
        prompt.assert_not_called()
    assert count(conn) == 1


@pytest.mark.parametrize("change", ["duplicate", "scope", "expiry"])
def test_revalidate_after_prompt(db, monkeypatch, change):
    conn, action, _ = db
    def confirm(prompt):
        if change == "duplicate":
            record_operator_decision(conn, action.action_id, ApprovalDecision.REJECTED)
            conn.commit()
        elif change == "scope":
            # Still a valid action, but not the exact record shown to the operator.
            conn.execute("UPDATE actions SET created_at=?, expires_at=?", (
                (action.created_at - timedelta(seconds=1)).isoformat(),
                (action.expires_at - timedelta(seconds=1)).isoformat(),
            ))
            conn.commit()
        else:
            clock = type("Clock", (), {"now": staticmethod(lambda tz: action.expires_at)})
            monkeypatch.setattr("adaptive_agentops.approvals.datetime", clock)
        return f"APPROVE {action.action_id}"
    monkeypatch.setattr("builtins.input", confirm)
    assert run(db) == 1
    assert count(conn) == (1 if change == "duplicate" else 0)


@pytest.mark.parametrize("kind", ["missing", "empty", "invalid", "actions_only"])
def test_bad_database_never_initialized(tmp_path, kind):
    path = tmp_path / "bad.db"
    if kind == "invalid":
        path.write_bytes(b"SECRET_INVALID_DATABASE")
    elif kind in ("empty", "actions_only"):
        conn = sqlite3.connect(path)
        if kind == "actions_only":
            initialize_action_schema(conn)
        conn.close()
    before = path.read_bytes() if path.exists() else None
    assert operator_cli.main(["--db", str(path), "review", str(uuid4())]) == 1
    assert (path.read_bytes() if path.exists() else None) == before


def test_corrupt_action_is_not_displayed(db, capsys):
    conn, _, _ = db
    conn.execute("UPDATE actions SET resource='SECRET_RESOURCE'")
    conn.commit()
    assert run(db, "review") == 1
    output = capsys.readouterr()
    assert "SECRET" not in output.out + output.err


class ObservedConnection(sqlite3.Connection):
    failure = None
    commits = 0
    rollbacks = 0
    observer = None

    def execute(self, sql, parameters=()):
        if sql == "BEGIN IMMEDIATE" and self.failure == "begin":
            raise sqlite3.OperationalError("SECRET_BEGIN")
        if "INSERT INTO approvals" in sql and self.failure == "insert":
            raise sqlite3.OperationalError("SECRET_INSERT")
        return super().execute(sql, parameters)

    def commit(self):
        self.commits += 1
        assert count(self.observer) == 0
        assert count(self) == 1
        if self.failure == "commit":
            raise sqlite3.OperationalError("SECRET_COMMIT")
        super().commit()
        if self.failure == "ambiguous":
            raise sqlite3.OperationalError("SECRET_AFTER_COMMIT")

    def rollback(self):
        self.rollbacks += 1
        super().rollback()


@pytest.mark.parametrize("failure", [None, "begin", "insert", "commit", "ambiguous"])
def test_transaction_failures_and_commit_visibility(db, monkeypatch, capsys, failure):
    conn, action, path = db
    tracked = sqlite3.connect(path, factory=ObservedConnection)
    tracked.failure, tracked.observer = failure, conn
    monkeypatch.setattr(operator_cli, "_open_database", lambda path, read_only: tracked)
    monkeypatch.setattr("builtins.input", lambda prompt: f"APPROVE {action.action_id}")
    with patch("adaptive_agentops.operator_cli.record_operator_decision", wraps=record_operator_decision) as record:
        assert run(db) == (0 if failure is None else 1)
        assert record.call_count == (0 if failure == "begin" else 1)
    output = capsys.readouterr()
    assert "SECRET" not in output.out + output.err
    assert ("Decision committed:" in output.out) is (failure is None)
    assert tracked.commits == (0 if failure in ("begin", "insert") else 1)
    assert tracked.rollbacks == (1 if failure in ("insert", "commit", "ambiguous") else 0)
    assert count(conn) == (1 if failure in (None, "ambiguous") else 0)


@pytest.mark.parametrize("extra", [["--yes"], ["--force"], ["APPROVED"], ["--approver-id", "agent"]])
def test_no_unattended_or_identity_options(db, extra):
    with pytest.raises(SystemExit) as error:
        operator_cli.main(["--db", str(db[2]), "decide", str(db[1].action_id), *extra])
    assert error.value.code == 2
    assert count(db[0]) == 0
