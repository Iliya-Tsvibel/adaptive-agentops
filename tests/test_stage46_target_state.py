from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import errno
import os
import socket
import sqlite3
import subprocess
import sys
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest

from adaptive_agentops.stage4_boundary import Endpoint, Link, ProtocolError, TrustedUIDs
from adaptive_agentops.stage4_boundary import controller_state, delegation, dispatch_state, target_state
from adaptive_agentops.stage4_boundary.target_state import DuplicateRequest, ReceiptKey, TargetStateError


UID = os.getuid()
ENDPOINT = Endpoint(Link.CONTROL_TARGET, "/run/stage46-test/target/dispatch.sock", 60125)


@pytest.fixture
def state(tmp_path, monkeypatch):
    assert UID != 0
    # Actual protected ancestors and distinct service UIDs are native evidence.
    monkeypatch.setattr(controller_state, "_check_ancestor", lambda _: None)
    path = tmp_path / "target"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def ready(state):
    target_state.provision_target_state(state, UID)
    return state


@pytest.fixture
def key():
    return ReceiptKey(dispatch_id=uuid4(), action_id=uuid4(), request_digest="a" * 64)


def inspect(path, key):
    with target_state._open_target_state(path, UID) as connection:
        result = target_state._lookup(connection, key)
        (count,) = connection.execute("SELECT effect_count FROM simulated_target").fetchone()
        claims = connection.execute("SELECT count(*) FROM target_claims").fetchone()[0]
        return result, count, claims


def test_missing_database_and_unexpected_files_never_start(state):
    with pytest.raises(controller_state.ControllerStateError):
        with target_state._open_target_state(state, UID):
            pytest.fail("Missing database unexpectedly opened")
    assert not list(state.iterdir())
    target_state.provision_target_state(state, UID)
    (state / "other").touch()
    with pytest.raises(controller_state.ControllerStateError):
        with target_state._open_target_state(state, UID):
            pytest.fail("Foreign entry unexpectedly accepted")


def test_missing_effect_singleton_is_not_reinitialized(ready):
    with sqlite3.connect(ready / target_state.DATABASE_NAME) as connection:
        connection.execute("DELETE FROM simulated_target")
    with pytest.raises(TargetStateError, match="inconsistent"):
        with target_state._open_target_state(ready, UID):
            pytest.fail("Missing effect evidence unexpectedly initialized")


def test_one_terminal_receipt_and_collisions_across_reopen(ready, key):
    with target_state._open_target_state(ready, UID) as connection:
        receipt = target_state._apply(connection, key, lambda: None)
    assert receipt.effect_number == 1
    result, count, claims = inspect(ready, key)
    assert (result.status, result.effect_number, count, claims) == ("COMMITTED", 1, 1, 1)
    for duplicate in (
        key,
        key.model_copy(update={"dispatch_id": uuid4()}),
        key.model_copy(update={"action_id": uuid4()}),
        key.model_copy(update={"request_digest": "0" * 64}),
    ):
        with target_state._open_target_state(ready, UID) as connection:
            with pytest.raises(DuplicateRequest):
                target_state._apply(connection, duplicate, lambda: None)
    assert inspect(ready, key) == (result, 1, 1)
    mismatched = key.model_copy(update={"request_digest": "0" * 64})
    assert inspect(ready, mismatched)[0].status == "UNKNOWN"


@pytest.mark.parametrize(("failing_commit", "durable", "expected"), [
    (1, False, (0, 0)),
    (1, True, (0, 1)),
    (2, False, (0, 1)),
    (2, True, (1, 1)),
])
def test_commit_failure_never_acknowledges_or_reconstructs_authority(
    ready, key, monkeypatch, failing_commit, durable, expected,
):
    count = 0
    real_commit = controller_state._commit

    def commit(connection):
        nonlocal count
        count += 1
        if count != failing_commit or durable:
            real_commit(connection)
        if count == failing_commit:
            raise sqlite3.OperationalError("modeled lost commit confirmation")

    with monkeypatch.context() as patch:
        patch.setattr(controller_state, "_commit", commit)
        with pytest.raises(controller_state.ControllerStateError, match="unavailable"):
            with target_state._open_target_state(ready, UID) as connection:
                target_state._apply(connection, key, lambda: None)

    result, effects, claims = inspect(ready, key)
    assert (effects, claims) == expected
    assert result.status == ("COMMITTED" if effects else "UNKNOWN")
    if claims:
        with target_state._open_target_state(ready, UID) as connection:
            with pytest.raises(DuplicateRequest):
                target_state._apply(connection, key, lambda: None)


def test_expiry_after_claim_retains_unknown_and_capacity_denies_without_eviction(ready, key, monkeypatch):
    check = Mock(side_effect=[None, ProtocolError("expired")])
    with target_state._open_target_state(ready, UID) as connection:
        with pytest.raises(ProtocolError, match="expired"):
            target_state._apply(connection, key, check)
    assert check.call_count == 2
    monkeypatch.setattr(target_state, "_MAX_CLAIMS", 1)
    unrelated = ReceiptKey(dispatch_id=uuid4(), action_id=uuid4(), request_digest="0" * 64)
    with target_state._open_target_state(ready, UID) as connection:
        with pytest.raises(TargetStateError, match="capacity"):
            target_state._apply(connection, unrelated, lambda: None)
    result, effects, claims = inspect(ready, key)
    assert (result.status, effects, claims) == ("UNKNOWN", 0, 1)


def test_competing_lease_cannot_add_effect_while_winner_holds_claim(ready, key):
    claimed = Event()
    finish = Event()

    def winner():
        checks = iter((False, True))

        def await_contender():
            if next(checks):
                claimed.set()
                assert finish.wait(5)

        with target_state._open_target_state(ready, UID) as connection:
            return target_state._apply(connection, key, await_contender)

    with ThreadPoolExecutor(max_workers=1) as workers:
        result = workers.submit(winner)
        try:
            assert claimed.wait(5)
            with pytest.raises(controller_state.ControllerLeaseError):
                with target_state._open_target_state(ready, UID):
                    pytest.fail("Competing process unexpectedly acquired lease")
        finally:
            finish.set()
        assert result.result(timeout=5).effect_number == 1
    assert inspect(ready, key)[1:] == (1, 1)


@pytest.mark.parametrize(("crash_commit", "status", "count"), [(1, "UNKNOWN", 0), (2, "COMMITTED", 1)])
def test_abrupt_exit_preserves_claim_and_any_committed_effect(ready, key, crash_commit, status, count):
    source = """
import os
import sys
from adaptive_agentops.stage4_boundary import controller_state, target_state
controller_state._check_ancestor = lambda _: None
key = target_state.ReceiptKey.model_validate_json(sys.argv[2])
commit = controller_state._commit
remaining = int(sys.argv[3])
def exit_after_commit(connection):
    global remaining
    commit(connection)
    remaining -= 1
    if not remaining:
        os._exit(86)
controller_state._commit = exit_after_commit
with target_state._open_target_state(sys.argv[1], os.getuid()) as connection:
    target_state._apply(connection, key, lambda: None)
"""
    child = subprocess.run(
        [sys.executable, "-B", "-c", source, str(ready), key.model_dump_json(), str(crash_commit)], timeout=6,
    )
    assert child.returncode == 86
    observed, effects, claims = inspect(ready, key)
    assert (observed.status, observed.effect_number, effects, claims) == (status, count or None, count, 1)
    with target_state._open_target_state(ready, UID) as connection:
        with pytest.raises(DuplicateRequest):
            target_state._apply(connection, key, lambda: None)


@pytest.mark.parametrize("lose_receipt", [False, True])
def test_controller_receipt_and_authenticated_lookup_over_unix_stream(ready, tmp_path, monkeypatch, lose_receipt):
    state = tmp_path / "controller"
    state.mkdir(mode=0o700)
    dispatch_state.provision_effect_fixture_state(state, UID)
    uids = TrustedUIDs(UID + 1, UID, UID + 2)
    monkeypatch.setattr(target_state, "_require_local_uid", lambda uid: uid == uids.target)
    load_state = target_state._open_target_state
    monkeypatch.setattr(target_state, "_open_target_state", lambda path, uid: load_state(path, UID))

    @contextmanager
    def transport():
        client, server = socket.socketpair()
        with client, server:
            try:
                client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            except PermissionError as exc:
                assert exc.errno == errno.EPERM
                pytest.skip("Unix peer credentials unavailable in the restricted sandbox")
            listener = Mock()
            listener.accept.return_value = (server, None)

            @contextmanager
            def listen(endpoint, uid):
                assert (endpoint, uid) == (ENDPOINT, uids.target)
                yield listener

            @contextmanager
            def connect(endpoint, identities):
                assert (endpoint, identities) == (ENDPOINT, uids)
                yield client, target_state.monotonic() + 2

            monkeypatch.setattr(target_state, "_listen", listen)
            monkeypatch.setattr(target_state, "_connect_verified", connect)
            monkeypatch.setattr(dispatch_state, "_connect_verified", connect)
            with ThreadPoolExecutor(max_workers=1) as workers:
                yield workers.submit(target_state.serve_target_once, ENDPOINT, uids, ready)

    send = target_state._send_frame

    def maybe_lose_receipt(peer, frame, deadline):
        if not (lose_receipt and frame.startswith(target_state._RECEIPT)):
            send(peer, frame, deadline)

    monkeypatch.setattr(target_state, "_send_frame", maybe_lose_receipt)
    with transport() as server:
        status = dispatch_state.dispatch_effect_fixture_once(
            state, ENDPOINT, uids, requester_uid=UID + 3, approver_uid=UID + 4,
        )
        assert server.result(timeout=4).effect_number == 1
    expected = (
        dispatch_state.DispatchStatus.UNKNOWN if lose_receipt
        else dispatch_state.DispatchStatus.SIMULATED_EFFECT_COMMITTED
    )
    assert status is expected
    with sqlite3.connect(state / controller_state.DATABASE_NAME) as conn:
        dispatch, action, digest, effect = conn.execute(
            "SELECT r.dispatch_id, r.action_id, e.request_digest, e.effect_number "
            "FROM dispatch_reservations AS r JOIN dispatch_effect_evidence AS e USING(dispatch_id)"
        ).fetchone()
    assert effect == (None if lose_receipt else 1)
    key = ReceiptKey.model_validate_strings({"dispatch_id": dispatch, "action_id": action, "request_digest": digest})
    with transport() as server:
        result = target_state.lookup_target_receipt(ENDPOINT, uids, key)
        assert server.result(timeout=4) == result
    assert (result.status, result.effect_number) == ("COMMITTED", 1)
    assert inspect(ready, key)[1:] == (1, 1)
