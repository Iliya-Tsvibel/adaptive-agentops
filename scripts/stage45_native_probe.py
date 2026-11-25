"""Distinct-UID dispatch tests for the isolated no-effect Stage 4.5 profile."""

from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys


def wrong_target(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.settimeout(5)
        listener.bind(path)
        listener.listen(1)
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(3)
            if connection.recv(1):
                raise RuntimeError("Wrong target received dispatch bytes")
    print("NO_DISPATCH_BYTES")


def wrong_controller(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(path)
        if client.recv(1):
            raise RuntimeError("Wrong Controller received a dispatch reply")


def main():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise RuntimeError("The native probe requires Python -I -B -S")
    mode, state, path = sys.argv[1:]
    if mode == "wrong-target":
        wrong_target(path)
        return
    if mode == "wrong-controller":
        wrong_controller(path)
        return

    root = Path(__file__).resolve().parents[1]
    sys.path.extend((str(root), str(root / "deps")))
    from scripts import stage4_native_acceptance as legacy
    from scripts.stage42_native_probe import effect_guard
    from scripts.stage43_native_probe import APPROVER, OPERATOR

    control_mode = mode in {"provision", "controller", "read-state"}
    expected_uid = legacy.CONTROL if control_mode else legacy.TARGET
    if os.getuid() != expected_uid or os.geteuid() != expected_uid:
        raise RuntimeError("Stage 4.5 probe is running under the wrong service UID")

    database = Path(state) / "controller.sqlite3"
    if mode in {"controller", "target", "target-denies", "observe-no-request"}:
        verify = effect_guard(
            "adaptive_agentops.actions",
            "adaptive_agentops.approvals",
            "adaptive_agentops.stage4_boundary.controller_state",
            "adaptive_agentops.stage4_boundary.dispatch_state",
            "adaptive_agentops.stage4_boundary.dispatch_wire",
            "adaptive_agentops.stage4_boundary.operator",
            sqlite_database=database.resolve() if control_mode else None,
            unix_peer=Path(path).resolve() if control_mode else None,
        )

    from adaptive_agentops.stage4_boundary import Endpoint, IdentityError, Link, TrustedUIDs
    uids = TrustedUIDs(legacy.AGENT, legacy.CONTROL, legacy.TARGET)
    endpoint = Endpoint(Link.CONTROL_TARGET, path, legacy.DISPATCHERS)
    if control_mode:
        control_probe(mode, database, endpoint, uids, OPERATOR, APPROVER, verify if mode == "controller" else None)
        return

    from adaptive_agentops.stage4_boundary.dispatch_wire import serve_dispatch_once
    if mode == "observe-no-request":
        from adaptive_agentops.stage4_boundary import _listen, _require_peer

        with _listen(endpoint, uids.target) as listener:
            listener.settimeout(3)
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                pass
            else:
                with connection:
                    _require_peer(connection, uids.control)
                    connection.settimeout(3)
                    if connection.recv(1):
                        raise RuntimeError("Repeated Coordinator sent a second application frame")
        verify()
        print("NO_SECOND_REQUEST")
        return
    try:
        reply = serve_dispatch_once(endpoint, uids)
    except IdentityError:
        if mode != "target-denies":
            raise
        verify()
        print("CONTROLLER_UID_DENIED")
    else:
        if mode != "target":
            raise RuntimeError("Unexpected dispatch acceptance")
        verify()
        print(reply.model_dump_json())


def control_probe(mode, database, endpoint, uids, requester, approver, verify):
    if mode == "read-state":
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
            print(json.dumps(snapshot(conn)))
        return

    from adaptive_agentops.stage4_boundary import IdentityError
    from adaptive_agentops.stage4_boundary.dispatch_state import (
        DispatchFixtureUsedError, dispatch_fixture_once, provision_dispatch_fixture_state,
    )

    if mode == "provision":
        provision_dispatch_fixture_state(database.parent, uids.control)
    elif mode == "controller":
        try:
            result = dispatch_fixture_once(
                database.parent, endpoint, uids, requester_uid=requester, approver_uid=approver,
            )
        except IdentityError:
            status = "PEER_DENIED"
        except DispatchFixtureUsedError:
            status = "FIXTURE_ALREADY_USED"
        else:
            status = result.name
        verify()
        print(status)
    else:
        raise RuntimeError("Unexpected Stage 4.5 Controller probe")


def snapshot(conn):
    return {
        "approvals": conn.execute("SELECT approval_id, consumed FROM dispatch_approvals").fetchall(),
        "reservations": conn.execute(
            "SELECT dispatch_id, action_id, approval_id FROM dispatch_reservations"
        ).fetchall(),
        "evidence": conn.execute(
            "SELECT dispatch_id, kind FROM dispatch_events"
        ).fetchall(),
        "outcomes": conn.execute("SELECT dispatch_id, status FROM dispatch_outcomes").fetchall(),
    }


if __name__ == "__main__":
    main()
