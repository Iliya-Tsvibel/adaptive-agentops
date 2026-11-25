from contextlib import contextmanager
import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from adaptive_agentops.actions import _fingerprint
from adaptive_agentops.stage4_boundary import Endpoint, IdentityError, Link, ProtocolError, TrustedUIDs, delegation
from adaptive_agentops.stage4_boundary import dispatch_wire as wire
from adaptive_agentops.tool_requests import EmptyArguments


UIDS = TrustedUIDs(60101, 60102, 60103)
ENDPOINT = Endpoint(Link.CONTROL_TARGET, "/run/aao/target/dispatch.sock", 60112)
WALL, ELAPSED = 1_000_000_000_000, 100_000_000_000
SECOND = 1_000_000_000


@pytest.fixture
def envelope():
    scope = {
        "run_id": str(uuid4()), "requester_id": "local-requester", "agent_id": "incident-investigator-v1",
        "tool": "mock.restart_service", "resource": "demo-api", "environment": "local-mock", "arguments": {},
    }
    return wire.DispatchEnvelope.model_validate_json(json.dumps({
        **scope, "session_id": str(uuid4()), "grant_id": str(uuid4()), "dispatch_id": str(uuid4()),
        "action_id": str(uuid4()), "approval_id": str(uuid4()), "target_id": "demo-api",
        "policy_version": "local-stage4-v1", "fingerprint_version": 1, "action_fingerprint": _fingerprint(scope),
        "issued_wall_ns": WALL, "issued_elapsed_ns": ELAPSED,
        "not_after_wall_ns": WALL + SECOND, "not_after_elapsed_ns": ELAPSED + SECOND,
    }), strict=True)


def raw_frame(body, prefix=wire._REQUEST):
    return prefix + wire._LENGTH.pack(len(body)) + body


def connection(frame):
    client = MagicMock()
    client.recv.side_effect = [frame, b""]
    return client


def test_exact_dispatch_round_trip_and_legacy_fingerprint(envelope):
    encoded = wire._encode_frame(wire._REQUEST, envelope)
    parsed = wire._parse_frame(encoded, wire._REQUEST, wire.DispatchEnvelope)
    assert parsed == envelope and isinstance(parsed.arguments, EmptyArguments)
    assert _fingerprint(parsed.model_dump(mode="json", include=wire._SCOPE_FIELDS)) == parsed.action_fingerprint


@pytest.mark.parametrize("change", [
    lambda data: {**data, "approval_id": "none"},
    lambda data: {**data, "target_id": "real-target"},
    lambda data: {**data, "tool": "mock.get_health"},
    lambda data: {**data, "resource": "other-service"},
    lambda data: {**data, "environment": "production"},
    lambda data: {**data, "arguments": {"force": True}},
    lambda data: {**data, "run_id": str(uuid4())},
    lambda data: {**data, "fingerprint_version": 2},
    lambda data: {**data, "action_fingerprint": "0" * 64},
    lambda data: {**data, "policy_version": "unreviewed-policy"},
    lambda data: {**data, "controller_uid": 60102},
    lambda data: {**data, "issued_wall_ns": True},
    lambda data: {**data, "issued_elapsed_ns": "100000000000"},
    lambda data: {**data, "not_after_wall_ns": data["issued_wall_ns"] + 5 * SECOND + 1},
    lambda data: {**data, "not_after_elapsed_ns": data["issued_elapsed_ns"]},
])
def test_wrong_scope_fingerprint_authority_and_window_rejected(envelope, change):
    data = change(envelope.model_dump(mode="json"))
    body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ProtocolError, match="invalid dispatch message"):
        wire._parse_frame(raw_frame(body), wire._REQUEST, wire.DispatchEnvelope)


@pytest.mark.parametrize("change", [
    lambda body: b'{"target_id":"demo-api",' + body[1:],
    lambda body: b" " + body,
    lambda body: body + b"\n",
    lambda body: body[:-1] + b"\xff",
    lambda body: body[:-1],
    lambda body: b" " * (wire._BODY_MAX + 1),
])
def test_noncanonical_duplicate_invalid_and_oversize_bodies(envelope, change):
    with pytest.raises(ProtocolError):
        wire._parse_frame(raw_frame(change(wire._encode_body(envelope))), wire._REQUEST, wire.DispatchEnvelope)


def test_other_protocol_prefix_size_mismatch_and_trailing_message(envelope):
    frame = wire._encode_frame(wire._REQUEST, envelope)
    for invalid in (b"", frame[:-1], wire._RESPONSE + frame[5:], frame + frame, frame + b"more"):
        with pytest.raises(ProtocolError):
            wire._parse_frame(invalid, wire._REQUEST, wire.DispatchEnvelope)


@pytest.mark.parametrize("sample", [
    (WALL - 1, ELAPSED), (WALL, ELAPSED - 1),
    (WALL + SECOND, ELAPSED + SECOND - 1), (WALL + SECOND - 1, ELAPSED + SECOND),
])
def test_future_issue_or_expiry_on_either_clock_blocks_before_send(envelope, monkeypatch, sample):
    monkeypatch.setattr(delegation, "_read_clocks", lambda: sample)
    client = MagicMock()
    with pytest.raises(ProtocolError, match="future-issued or expired"):
        wire._exchange_reserved(client, wire.monotonic() + 2, envelope)
    client.sendall.assert_not_called()


def test_acknowledgement_must_match_both_exact_ids_and_never_resends(envelope, monkeypatch):
    monkeypatch.setattr(delegation, "_read_clocks", lambda: (WALL, ELAPSED))
    ack = wire.DispatchAck(
        dispatch_id=envelope.dispatch_id, action_id=envelope.action_id, outcome="VALIDATED_NO_EFFECT",
    )
    good = connection(wire._encode_frame(wire._RESPONSE, ack))
    assert wire._exchange_reserved(good, wire.monotonic() + 2, envelope) == ack
    good.sendall.assert_called_once_with(wire._encode_frame(wire._REQUEST, envelope))
    good.shutdown.assert_called_once()
    for field in ("dispatch_id", "action_id"):
        wrong = connection(wire._encode_frame(wire._RESPONSE, ack.model_copy(update={field: uuid4()})))
        with pytest.raises(ProtocolError, match="correlation"):
            wire._exchange_reserved(wrong, wire.monotonic() + 2, envelope)
        wrong.sendall.assert_called_once()


@pytest.fixture
def target(monkeypatch, envelope):
    peer = connection(wire._encode_frame(wire._REQUEST, envelope))
    listener = MagicMock()
    listener.accept.return_value = (peer, None)

    @contextmanager
    def listen(endpoint, uid):
        assert endpoint is ENDPOINT and uid == UIDS.target
        yield listener

    monkeypatch.setattr(wire, "_listen", listen)
    monkeypatch.setattr(wire, "_require_local_uid", lambda uid: uid == UIDS.target)
    monkeypatch.setattr(delegation, "_read_clocks", lambda: (WALL, ELAPSED))
    return peer


def test_target_verifies_controller_before_reading_or_sending(target, monkeypatch):
    def wrong_peer(client, uid):
        assert client is target and uid == UIDS.control
        raise IdentityError("Wrong peer")

    monkeypatch.setattr(wire, "_require_peer", wrong_peer)
    with pytest.raises(IdentityError):
        wire.serve_dispatch_once(ENDPOINT, UIDS)
    target.recv.assert_not_called()
    target.sendall.assert_not_called()


def test_authenticated_target_sends_correlated_no_effect_reply(target, envelope, monkeypatch):
    def verify_peer(client, uid):
        assert client is target and uid == UIDS.control
        target.recv.assert_not_called()

    monkeypatch.setattr(wire, "_require_peer", verify_peer)
    ack = wire.serve_dispatch_once(ENDPOINT, UIDS)
    assert (ack.dispatch_id, ack.action_id, ack.outcome) == (
        envelope.dispatch_id, envelope.action_id, "VALIDATED_NO_EFFECT",
    )
    target.sendall.assert_called_once_with(wire._encode_frame(wire._RESPONSE, ack))


def test_target_detects_clock_divergence_before_ack(target, monkeypatch):
    samples = iter(((WALL, ELAPSED), (WALL + SECOND + 1, ELAPSED)))
    monkeypatch.setattr(delegation, "_read_clocks", lambda: next(samples))
    monkeypatch.setattr(wire, "_require_peer", lambda *_: None)
    with pytest.raises(delegation.ClockError):
        wire.serve_dispatch_once(ENDPOINT, UIDS)
    target.sendall.assert_not_called()


@pytest.mark.parametrize("link", [Link.AGENT_CONTROL, Link.OPERATOR_CONTROL])
def test_dispatch_link_does_not_bind_agent_or_operator_routes(link):
    with pytest.raises(ValueError, match="dedicated"):
        wire.serve_dispatch_once(Endpoint(link, ENDPOINT.path, ENDPOINT.access_gid), UIDS)


def test_final_send_rejects_divergence_even_inside_both_numeric_windows(envelope, monkeypatch):
    wider = envelope.model_copy(update={
        "not_after_wall_ns": WALL + 5 * SECOND,
        "not_after_elapsed_ns": ELAPSED + 5 * SECOND,
    })
    monkeypatch.setattr(delegation, "_read_clocks", lambda: (WALL + 4 * SECOND, ELAPSED + SECOND))
    client = MagicMock()
    with pytest.raises(ProtocolError, match="diverged"):
        wire._exchange_reserved(client, wire.monotonic() + 2, wider)
    client.sendall.assert_not_called()


def test_final_send_uses_the_continuous_coordinator_clock(envelope, monkeypatch):
    samples = iter(((WALL, ELAPSED), (WALL + SECOND - 1, ELAPSED + SECOND - 1),
                    (WALL + 100, ELAPSED + 100)))
    monkeypatch.setattr(delegation, "_read_clocks", lambda: next(samples))
    clock = delegation._SessionClock()
    clock.sample()
    client = MagicMock()
    with pytest.raises(delegation.ClockError):
        wire._exchange_reserved(client, wire.monotonic() + 2, envelope, clock=clock)
    client.sendall.assert_not_called()


def test_no_effect_dispatch_round_trips_on_real_unix_stream(envelope, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import os
    import socket

    local_uid = os.getuid()
    if not local_uid:
        pytest.skip("test requires an unprivileged local UID")
    identities = TrustedUIDs(local_uid + 1, local_uid, local_uid + 2)
    monkeypatch.setattr(delegation, "_read_clocks", lambda: (WALL, ELAPSED))
    monkeypatch.setattr(wire, "_require_local_uid", lambda expected: identities.target == expected)

    client, server = socket.socketpair()
    listener = MagicMock()
    listener.accept.return_value = (server, None)

    @contextmanager
    def listen(_endpoint, expected):
        assert expected == identities.target
        yield listener

    monkeypatch.setattr(wire, "_listen", listen)
    with client, server, ThreadPoolExecutor(max_workers=1) as worker:
        result = worker.submit(wire.serve_dispatch_once, ENDPOINT, identities)
        acknowledgement = wire._exchange_reserved(client, wire.monotonic() + 2, envelope)
        assert result.result(timeout=3) == acknowledgement
        assert acknowledgement.outcome == "VALIDATED_NO_EFFECT"


def test_protected_reservation_and_real_unix_round_trip_fail_closed_on_repeat(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing
    import os
    import socket
    import sqlite3

    from adaptive_agentops.stage4_boundary import controller_state, dispatch_state
    from scripts.stage45_native_probe import snapshot

    uid = os.getuid()
    if not uid:
        pytest.skip("test requires an unprivileged local UID")
    uids = TrustedUIDs(uid + 1, uid, uid + 2)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    client, peer = socket.socketpair()
    listener = MagicMock()
    listener.accept.return_value = (peer, None)

    @contextmanager
    def listen(endpoint, expected):
        assert endpoint is ENDPOINT and expected == uids.target
        yield listener

    @contextmanager
    def connect(endpoint, identities):
        assert endpoint is ENDPOINT and identities == uids
        yield client, wire.monotonic() + 2

    monkeypatch.setattr(controller_state, "_check_ancestor", lambda _: None)
    monkeypatch.setattr(wire, "_listen", listen)
    monkeypatch.setattr(wire, "_require_local_uid", lambda expected: expected == uids.target)
    monkeypatch.setattr(dispatch_state, "_connect_verified", connect)
    dispatch_state.provision_dispatch_fixture_state(state, uid)
    with client, peer, ThreadPoolExecutor(max_workers=1) as worker:
        target = worker.submit(wire.serve_dispatch_once, ENDPOINT, uids)
        result = dispatch_state.dispatch_fixture_once(
            state, ENDPOINT, uids, requester_uid=uid + 3, approver_uid=uid + 4,
        )
        ack = target.result(timeout=4)
    assert result is dispatch_state.DispatchStatus.VALIDATED_NO_EFFECT
    with closing(sqlite3.connect(state / "controller.sqlite3")) as conn:
        evidence = snapshot(conn)
    assert evidence["outcomes"] == [(str(ack.dispatch_id), "VALIDATED_NO_EFFECT")]
    assert evidence["approvals"][0][1] == 1

    def no_connect(*_args):
        pytest.fail("consumed fixture tried to connect again")

    monkeypatch.setattr(dispatch_state, "_connect_verified", no_connect)
    with pytest.raises(dispatch_state.DispatchFixtureUsedError):
        dispatch_state.dispatch_fixture_once(
            state, ENDPOINT, uids, requester_uid=uid + 3, approver_uid=uid + 4,
        )
