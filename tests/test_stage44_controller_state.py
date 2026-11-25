from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from unittest.mock import Mock

import pytest

from adaptive_agentops.stage4_boundary import BoundaryError, Endpoint, Link, TrustedUIDs
from adaptive_agentops.stage4_boundary import controller_state, delegation, operator
from adaptive_agentops.tool_requests import MockGetHealthProposal


CONTROL_UID = os.getuid()
AGENT_UID, TARGET_UID, OPERATOR_UID = 61001, 61003, 61004
AGENT = Endpoint(Link.AGENT_CONTROL, "/run/state-test/agent/proposal.sock", 61011)
OPERATOR = Endpoint(Link.OPERATOR_CONTROL, "/run/state-test/operator/grant.sock", 61012)
OPERATORS = (operator.OperatorIdentity(OPERATOR_UID, "local-requester", frozenset({operator.OperatorRole.REQUESTER})),)
PROPOSAL = MockGetHealthProposal(tool="mock.get_health", resource="demo-api", arguments={})
PROPOSAL_FRAME = delegation._PROPOSAL + PROPOSAL.model_dump_json().encode()
NS = 1_000_000_000


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    assert CONTROL_UID != 0, "Stage 4.4 local tests must run without privileged elevation"
    # Unit tests use a temporary directory owned by the test user. The native
    # harness verifies the actual protected ancestors and distinct Linux UIDs.
    monkeypatch.setattr(controller_state, "_check_ancestor", lambda info: None)
    path = tmp_path / "state"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def provisioned(state_dir):
    controller_state.provision_controller_state(state_dir, CONTROL_UID)
    return state_dir


@pytest.fixture
def clock(monkeypatch):
    sample = [100 * NS, 50 * NS]
    monkeypatch.setattr(delegation, "_read_clocks", lambda: tuple(sample))
    return sample


def read_rows(state_dir, query):
    uri = (state_dir / controller_state.DATABASE_NAME).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        return conn.execute(query).fetchall()


def create_controller(store):
    managed = operator._Controller(AGENT_UID, OPERATORS, store=store)
    store.start(managed.admission, CONTROL_UID, AGENT_UID, OPERATORS, "mock.get_health", "demo-api")
    return managed


def operator_stream(monkeypatch, method, argument=None):
    monkeypatch.setattr(operator, "_peer_uid", lambda stream: OPERATOR_UID)
    return Mock(recv=Mock(side_effect=[operator._frame_request(method, argument), b""]))


def call_operator(managed, monkeypatch, method, argument=None):
    stream = operator_stream(monkeypatch, method, argument)
    assert managed.handle_operator(stream, operator.monotonic() + 2)
    return stream


def admission_stream(monkeypatch):
    monkeypatch.setattr(delegation, "_require_peer", lambda stream, peer: None)
    return Mock(recv=Mock(side_effect=[PROPOSAL_FRAME, b""]))


def admitted(managed, monkeypatch):
    stream = admission_stream(monkeypatch)
    assert delegation._handle_connection(stream, managed.admission, AGENT_UID, operator.monotonic() + 2)
    return stream.sendall.call_args.args[0]


def test_provision_is_explicit_private_and_one_time(state_dir):
    file = state_dir / controller_state.DATABASE_NAME
    with pytest.raises(controller_state.ControllerStateError, match="database is missing"):
        with controller_state._open_controller_state(state_dir, CONTROL_UID):
            pytest.fail("startup recreated a missing database")
    assert not file.exists()

    controller_state.provision_controller_state(state_dir, CONTROL_UID)
    info = file.stat()
    assert (info.st_uid, info.st_mode, info.st_nlink) == (CONTROL_UID, stat.S_IFREG | 0o600, 1)
    with pytest.raises(controller_state.ControllerStateError, match="requires an empty directory"):
        controller_state.provision_controller_state(state_dir, CONTROL_UID)
    assert read_rows(state_dir, "SELECT COUNT(*) FROM controller_sessions") == [(0,)]


@pytest.mark.parametrize("condition", ["incorrect-mode", "database-link", "database-symlink", "foreign", "journal-symlink"])
def test_runtime_rejects_unsafe_files_without_altering_them(provisioned, condition):
    file = provisioned / controller_state.DATABASE_NAME
    other = provisioned.parent / "other"
    if condition == "incorrect-mode":
        file.chmod(0o640)
    elif condition == "database-link":
        other.hardlink_to(file)
    elif condition == "database-symlink":
        file.rename(other)
        file.symlink_to(other)
    elif condition == "journal-symlink":
        other.write_bytes(b"outside")
        file.with_name(file.name + "-journal").symlink_to(other)
    else:
        file.with_name(file.name + "-wal").touch()
    data = file.read_bytes()
    with pytest.raises(controller_state.ControllerStateError):
        with controller_state._open_controller_state(provisioned, CONTROL_UID):
            pytest.fail("unsafe database reached runtime")
    assert file.read_bytes() == data
    if condition == "journal-symlink":
        assert other.read_bytes() == b"outside"


def test_directory_type_mode_and_unsafe_ancestor_fail_closed(provisioned, monkeypatch):
    aliases = provisioned.parent / "alias"
    aliases.symlink_to(provisioned, target_is_directory=True)
    for path in (aliases, f"{provisioned}/", f"{provisioned}/../state"):
        with pytest.raises(controller_state.ControllerStateError):
            with controller_state._open_controller_state(path, CONTROL_UID):
                pytest.fail("noncanonical or symlinked state directory reached runtime")

    provisioned.chmod(0o750)
    with pytest.raises(controller_state.ControllerStateError, match="ownership and mode 0700"):
        with controller_state._open_controller_state(provisioned, CONTROL_UID):
            pytest.fail("unsafe state directory mode reached runtime")
    provisioned.chmod(0o700)

    def reject(info):
        raise BoundaryError("unsafe protected ancestor")

    monkeypatch.setattr(controller_state, "_check_ancestor", reject)
    with pytest.raises(BoundaryError, match="unsafe protected ancestor"):
        with controller_state._open_controller_state(provisioned, CONTROL_UID):
            pytest.fail("unsafe state directory ancestor reached runtime")


def test_file_and_directory_acl_checks_precede_sqlite(provisioned, monkeypatch):
    seen = []

    def check(path, *, directory=False):
        if path.endswith(controller_state.DATABASE_NAME):
            raise BoundaryError("unexpected database ACL")
        seen.append(directory)

    monkeypatch.setattr(controller_state, "_reject_acls", check)
    monkeypatch.setattr(sqlite3, "connect", Mock(side_effect=AssertionError("opened SQLite before ACL checks")))
    with pytest.raises(BoundaryError, match="unexpected database ACL"):
        with controller_state._open_controller_state(provisioned, CONTROL_UID):
            pytest.fail("database ACL bypassed the preflight")
    assert seen and all(seen)


@pytest.mark.parametrize("damage", ["corrupt-bytes", "wrong-version", "extra-table", "foreign-key"])
def test_schema_and_integrity_fail_closed_without_recreation(provisioned, damage):
    file = provisioned / controller_state.DATABASE_NAME
    if damage == "corrupt-bytes":
        file.write_bytes(b"not a SQLite database")
    else:
        with closing(sqlite3.connect(file, isolation_level=None)) as conn:
            if damage == "wrong-version":
                conn.execute("PRAGMA user_version = 17")
            elif damage == "extra-table":
                conn.execute("CREATE TABLE other(untrusted TEXT)")
            else:
                conn.execute(
                    "INSERT INTO controller_events (event_id, session_id, kind, decision) VALUES (?, ?, ?, ?)",
                    ("0" * 36, "1" * 36, "LIFECYCLE", "STARTED"),
                )
    original = file.read_bytes()
    with pytest.raises(controller_state.ControllerStateError):
        with controller_state._open_controller_state(provisioned, CONTROL_UID):
            pytest.fail("invalid database reached runtime")
    assert file.read_bytes() == original


def test_lease_contention_precedes_socket_binding(provisioned, monkeypatch):
    monkeypatch.setattr(operator, "_listen", Mock(side_effect=AssertionError("opened socket before acquiring lease")))
    with controller_state._open_controller_state(provisioned, CONTROL_UID):
        with pytest.raises(controller_state.ControllerLeaseError, match="already leased"):
            operator.serve_protected_admission(
                AGENT, OPERATOR, TrustedUIDs(AGENT_UID, CONTROL_UID, TARGET_UID), OPERATORS,
                state_dir=provisioned,
            )


def test_confirmed_startup_precedes_socket_binding(provisioned, monkeypatch):
    def observe(launch_agent, launch_operator, uids, controller):
        assert (launch_agent, launch_operator) == (AGENT, OPERATOR)
        assert controller.admission.grant is None
        rows = read_rows(provisioned, "SELECT state, control_uid FROM controller_sessions")
        assert rows == [("ACTIVE", uids.control)]
        assert read_rows(provisioned, "SELECT method, decision FROM controller_events") == [("START", "STARTED")]
        with pytest.raises(controller_state.ControllerLeaseError):
            with controller_state._open_controller_state(provisioned, CONTROL_UID):
                pytest.fail("lease failed to cover listener lifetime")
        return operator.SessionEvidence((), ())

    monkeypatch.setattr(operator, "_serve_controller", observe)
    evidence = operator.serve_protected_admission(
        AGENT, OPERATOR, TrustedUIDs(AGENT_UID, CONTROL_UID, TARGET_UID), OPERATORS,
        state_dir=provisioned,
    )
    assert evidence == operator.SessionEvidence((), ())


def test_durable_issued_binding_admission_and_revocation(provisioned, monkeypatch, clock):
    with controller_state._open_controller_state(provisioned, CONTROL_UID) as store:
        controlled = create_controller(store)
        issued = call_operator(controlled, monkeypatch, operator.GrantMethod.ISSUE, 20)
        grant = controlled.admission.grant
        rows = read_rows(provisioned, "SELECT * FROM controller_grants")
        assert rows == [(
            str(grant.grant_id), str(grant.session_id), str(grant.run_id), OPERATOR_UID, "local-requester",
            AGENT_UID, "incident-investigator-v1", "mock.get_health", "demo-api", "local-mock",
            clock[0] + 20 * NS, clock[1] + 20 * NS, "ACTIVE",
        )]
        map_json, = read_rows(provisioned, "SELECT operator_map FROM controller_sessions")[0]
        assert json.loads(map_json) == [[OPERATOR_UID, "local-requester", ["requester"]]]
        issued.sendall.assert_called_once()
        assert read_rows(provisioned, "SELECT method, decision FROM controller_events WHERE kind='OPERATOR'") == [
            ("ISSUE", "ACCEPTED"),
        ]
        assert admitted(controlled, monkeypatch) == delegation._verdict_frame(delegation.AdmissionDecision.PROPOSAL_ALLOWED)
        call_operator(controlled, monkeypatch, operator.GrantMethod.REVOKE, grant.grant_id).sendall.assert_called_once()
        assert admitted(controlled, monkeypatch) == delegation._verdict_frame(delegation.AdmissionDecision.DENY)
        assert read_rows(provisioned, "SELECT state FROM controller_grants") == [("REVOKED",)]
        assert read_rows(provisioned, "SELECT decision, reason FROM controller_events WHERE kind='ADMISSION' ORDER BY rowid") == [
            ("PROPOSAL_ALLOWED", "ADMITTED"), ("DENY", "GRANT_REVOKED"),
        ]


@pytest.mark.parametrize("expiry_clock", ["wall", "elapsed"])
def test_expiry_at_either_exact_deadline_is_terminal(provisioned, monkeypatch, clock, expiry_clock):
    with controller_state._open_controller_state(provisioned, CONTROL_UID) as store:
        controlled = create_controller(store)
        call_operator(controlled, monkeypatch, operator.GrantMethod.ISSUE, 20)
        clock[0] += 20 * NS - (expiry_clock == "elapsed")
        clock[1] += 20 * NS - (expiry_clock == "wall")
        assert admitted(controlled, monkeypatch) == delegation._verdict_frame(delegation.AdmissionDecision.DENY)
        assert read_rows(provisioned, "SELECT state FROM controller_grants") == [("EXPIRED",)]
        assert read_rows(provisioned, "SELECT decision FROM controller_events WHERE method='EXPIRE'") == [("EXPIRED",)]


@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize("method", [operator.GrantMethod.ISSUE, operator.GrantMethod.REVOKE])
def test_commit_error_never_sends_or_retries_and_restart_never_recovers_authority(
    provisioned, monkeypatch, durable, method,
):
    with controller_state._open_controller_state(provisioned, CONTROL_UID) as store:
        controlled = create_controller(store)
        old_session = str(controlled.admission.session_id)
        if method is operator.GrantMethod.REVOKE:
            call_operator(controlled, monkeypatch, operator.GrantMethod.ISSUE, 20)
            argument = controlled.admission.grant.grant_id
        else:
            argument = 20
        stream = operator_stream(monkeypatch, method, argument)

        def commit(connection):
            if durable:
                connection.commit()
            raise sqlite3.OperationalError("injected failure crossing commit")

        with monkeypatch.context() as fault:
            failing = Mock(side_effect=commit)
            fault.setattr(controller_state, "_commit", failing)
            with pytest.raises(controller_state.ControllerStateError, match="commit may be uncertain"):
                controlled.handle_operator(stream, operator.monotonic() + 2)
            stream.sendall.assert_not_called()
            assert not controlled.admission.grant_revoked
            if method is operator.GrantMethod.ISSUE:
                assert controlled.admission.grant is None
            with pytest.raises(controller_state.ControllerStateError, match="unusable"):
                controlled.handle_operator(
                    operator_stream(monkeypatch, operator.GrantMethod.INSPECT), operator.monotonic() + 2,
                )
            failing.assert_called_once()
        if durable:
            assert read_rows(provisioned, "SELECT decision FROM controller_events WHERE method = '%s'" % method.name) == [
                ("ACCEPTED",),
            ]

    with controller_state._open_controller_state(provisioned, CONTROL_UID) as store:
        new = create_controller(store)
        assert str(new.admission.session_id) != old_session
        assert new.admission.grant is None
        assert admitted(new, monkeypatch) == delegation._verdict_frame(delegation.AdmissionDecision.DENY)
        assert read_rows(provisioned, "SELECT session_id FROM controller_sessions WHERE state='INVALIDATED'") == [(old_session,)]
        assert read_rows(provisioned, "SELECT state FROM controller_grants WHERE state='ACTIVE'") == []


def test_evidence_capacity_denies_positive_reply(provisioned, monkeypatch):
    with controller_state._open_controller_state(provisioned, CONTROL_UID) as store:
        controlled = create_controller(store)
        call_operator(controlled, monkeypatch, operator.GrantMethod.ISSUE, 20)
        monkeypatch.setattr(controller_state, "_MAX_EVENTS", 2)
        stream = admission_stream(monkeypatch)
        with pytest.raises(controller_state.ControllerStateError, match="capacity exhausted"):
            delegation._handle_connection(stream, controlled.admission, AGENT_UID, operator.monotonic() + 2)
        stream.sendall.assert_not_called()
    assert read_rows(provisioned, "SELECT COUNT(*) FROM controller_events") == [(2,)]


def test_subprocess_crash_releases_lease_and_rolls_back_before_restart(provisioned, monkeypatch):
    with controller_state._open_controller_state(provisioned, CONTROL_UID) as store:
        controlled = create_controller(store)
        call_operator(controlled, monkeypatch, operator.GrantMethod.ISSUE, 900)
        old_id = str(controlled.admission.session_id)
    crash = """
import os
import sys
from uuid import uuid4
from adaptive_agentops.stage4_boundary import controller_state as state

state._check_ancestor = lambda info: None
with state._open_controller_state(sys.argv[1], os.getuid()) as store:
    connection = store._connection
    connection.execute('PRAGMA cache_size = 1')
    connection.execute('BEGIN IMMEDIATE')
    connection.execute("UPDATE controller_sessions SET state='INVALIDATED'")
    for _ in range(80):
        connection.execute(
            "INSERT INTO controller_events (event_id,session_id,kind,decision) VALUES (?,?,'LIFECYCLE','CRASH')",
            (str(uuid4()), sys.argv[2]),
        )
    os._exit(37)
"""
    child = subprocess.run([sys.executable, "-c", crash, str(provisioned), old_id], timeout=8,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert (child.returncode, child.stderr) == (37, b"")
    assert (provisioned / (controller_state.DATABASE_NAME + "-journal")).is_file()
    with controller_state._open_controller_state(provisioned, CONTROL_UID) as store:
        # The read/write startup lets SQLite replay the verified hot rollback journal.
        assert store._connection.execute("SELECT state FROM controller_sessions").fetchone() == ("ACTIVE",)
        assert store._connection.execute("SELECT 1 FROM controller_events WHERE decision='CRASH'").fetchone() is None
        new = create_controller(store)
        assert new.admission.grant is None
        assert read_rows(provisioned, "SELECT state FROM controller_grants") == [("INVALIDATED",)]
        assert read_rows(provisioned, "SELECT decision FROM controller_events WHERE method='RESTART'") == [("INVALIDATED",)]


def test_in_memory_authority_cannot_override_persisted_binding(provisioned, monkeypatch):
    with controller_state._open_controller_state(provisioned, CONTROL_UID) as store:
        managed = create_controller(store)
        call_operator(managed, monkeypatch, operator.GrantMethod.ISSUE, 20)
        store._connection.execute("UPDATE controller_grants SET agent_uid = ?", (TARGET_UID,))
        stream = admission_stream(monkeypatch)
        with pytest.raises(controller_state.ControllerStateError, match="binding changed"):
            delegation._handle_connection(stream, managed.admission, AGENT_UID, operator.monotonic() + 2)
        stream.sendall.assert_not_called()


@pytest.mark.parametrize("clock_change", ["expires", "regresses"])
def test_confirmed_issue_with_unsafe_final_clock_never_sends_success(provisioned, monkeypatch, clock, clock_change):
    with controller_state._open_controller_state(provisioned, CONTROL_UID) as store:
        managed = create_controller(store)
        stream = operator_stream(monkeypatch, operator.GrantMethod.ISSUE, 20)

        def commit(connection):
            connection.commit()
            if clock_change == "regresses":
                clock[0] -= 1
            else:
                clock[0] = 120 * NS
                clock[1] = 70 * NS

        with monkeypatch.context() as slow:
            slow.setattr(controller_state, "_commit", commit)
            assert managed.handle_operator(stream, operator.monotonic() + 2) is (clock_change == "expires")
        stream.sendall.assert_not_called()
        expected = "EXPIRED" if clock_change == "expires" else "ACTIVE"
        assert read_rows(provisioned, "SELECT state FROM controller_grants") == [(expected,)]


def test_known_rollback_cannot_persist_transition_without_its_audit(provisioned, monkeypatch):
    with controller_state._open_controller_state(provisioned, CONTROL_UID) as store:
        managed = create_controller(store)
        monkeypatch.setattr(controller_state, "_MAX_EVENTS", 1)
        stream = operator_stream(monkeypatch, operator.GrantMethod.ISSUE, 20)
        with pytest.raises(controller_state.ControllerStateError, match="capacity exhausted"):
            managed.handle_operator(stream, operator.monotonic() + 2)
        stream.sendall.assert_not_called()
        assert managed.admission.grant is None
        assert read_rows(provisioned, "SELECT COUNT(*) FROM controller_grants") == [(0,)]
        assert read_rows(provisioned, "SELECT method FROM controller_events") == [("START",)]
