"""Disposable Stage 4.6 Controller and target evidence processes."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys


_INTERRUPTED = 86


def main():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise RuntimeError("The native probe requires Python -I -B -S")
    mode, state, path, *args = sys.argv[1:]
    root = Path(__file__).resolve().parents[1]
    sys.path.extend((str(root), str(root / "deps")))

    from scripts import stage4_native_acceptance as legacy
    from scripts.stage42_native_probe import effect_guard
    from scripts.stage43_native_probe import APPROVER, OPERATOR

    control = mode in {"provision-controller", "dispatch", "controller-evidence", "lookup", "replay"}
    expected_uid = legacy.CONTROL if control else legacy.TARGET
    if (os.getuid(), os.geteuid()) != (expected_uid, expected_uid):
        raise RuntimeError("Stage 4.6 probe is running under the wrong service UID")

    from adaptive_agentops.stage4_boundary import Endpoint, Link, TrustedUIDs
    from adaptive_agentops.stage4_boundary import controller_state, dispatch_state, dispatch_wire, target_state

    uids = TrustedUIDs(legacy.AGENT, legacy.CONTROL, legacy.TARGET)
    endpoint = Endpoint(Link.CONTROL_TARGET, path, legacy.DISPATCHERS)
    database = Path(state) / (controller_state.DATABASE_NAME if control else target_state.DATABASE_NAME)

    if mode in {"target-evidence", "controller-evidence"}:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
            if control:
                rows = conn.execute(
                    "SELECT e.dispatch_id, r.action_id, e.request_digest, e.effect_number, o.status "
                    "FROM dispatch_effect_evidence AS e JOIN dispatch_reservations AS r USING(dispatch_id) "
                    "JOIN dispatch_outcomes AS o USING(dispatch_id)"
                ).fetchall()
                print(json.dumps(rows))
            else:
                print(json.dumps({
                    "count": conn.execute("SELECT effect_count FROM simulated_target").fetchone()[0],
                    "claims": conn.execute(
                        "SELECT dispatch_id, action_id, request_digest, effect_number FROM target_claims ORDER BY dispatch_id"
                    ).fetchall(),
                }))
        return

    verify = effect_guard(
        "adaptive_agentops.actions",
        "adaptive_agentops.approvals",
        "adaptive_agentops.stage4_boundary.controller_state",
        "adaptive_agentops.stage4_boundary.dispatch_state",
        "adaptive_agentops.stage4_boundary.dispatch_wire",
        "adaptive_agentops.stage4_boundary.operator",
        "adaptive_agentops.stage4_boundary.target_state",
        sqlite_database=database.resolve(),
        unix_peer=Path(path).resolve() if control else None,
    )
    if mode == "provision-controller":
        dispatch_state.provision_effect_fixture_state(state, uids.control)
    elif mode == "provision-target":
        target_state.provision_target_state(state, uids.target)
    elif mode == "dispatch":
        sent = []
        exchange = target_state._exchange

        def observe(connection, deadline, envelope, *, clock):
            sent.append(dispatch_wire._encode_frame(target_state._REQUEST, envelope).hex())
            return exchange(connection, deadline, envelope, clock=clock)

        target_state._exchange = observe
        result = dispatch_state.dispatch_effect_fixture_once(
            state, endpoint, uids, requester_uid=OPERATOR, approver_uid=APPROVER,
        )
        if len(sent) != 1:
            raise RuntimeError("Stage 4.6 did not enter one bounded fixture exchange")
        print(json.dumps({"outcome": result.value, "frame": sent[0]}))
    elif mode == "lookup":
        key = target_state.ReceiptKey.model_validate_json(args[0], strict=True)
        print(target_state.lookup_target_receipt(endpoint, uids, key).model_dump_json())
    elif mode == "replay":
        expect_replay_denial(endpoint, uids, bytes.fromhex(args[0]))
    elif mode in {"target", "interrupted", "duplicate"}:
        if mode == "interrupted":
            commit = controller_state._commit

            def exit_after_claim(connection):
                commit(connection)
                verify()
                os._exit(_INTERRUPTED)

            controller_state._commit = exit_after_claim
        try:
            receipt = target_state.serve_target_once(endpoint, uids, state)
        except target_state.DuplicateRequest:
            if mode != "duplicate":
                raise
            print("REPLAY_DENIED")
        else:
            if mode != "target":
                raise RuntimeError("Unexpected Stage 4.6 positive target reply")
            print(receipt.model_dump_json())
    else:
        raise ValueError("Unknown Stage 4.6 probe")
    verify()


def expect_replay_denial(endpoint, uids, frame):
    from adaptive_agentops.stage4_boundary import _connect_verified, _send_frame
    from adaptive_agentops.stage4_boundary import dispatch_wire, target_state

    dispatch_wire._parse_frame(frame, target_state._REQUEST, dispatch_wire.DispatchEnvelope)
    with _connect_verified(endpoint, uids) as (peer, deadline):
        _send_frame(peer, frame, deadline)
        if peer.recv(1):
            raise RuntimeError("Duplicate target request received reply bytes")


if __name__ == "__main__":
    main()
