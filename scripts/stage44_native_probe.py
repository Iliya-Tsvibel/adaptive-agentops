"""Stage 4.4 probes. Every SQLite operation runs under the Control Plane UID."""

from contextlib import closing
import errno
import json
import os
from pathlib import Path
import sqlite3
import sys


def denied_state(state):
    for path, flags in (
        (state, os.O_RDONLY | os.O_DIRECTORY),
        (state / "controller.sqlite3", os.O_RDONLY),
    ):
        try:
            fd = os.open(path, flags | os.O_NOFOLLOW)
        except OSError as error:
            if error.errno == errno.EACCES:
                continue
            raise
        os.close(fd)
        raise RuntimeError("Untrusted service reached protected Controller state")


def snapshot(state):
    with closing(sqlite3.connect((state / "controller.sqlite3").as_uri() + "?mode=ro", uri=True)) as conn:
        return {
            "sessions": conn.execute(
                "SELECT session_id, run_id, control_uid, agent_uid, requester_id, state FROM controller_sessions"
            ).fetchall(),
            "grants": conn.execute(
                "SELECT grant_id, session_id, run_id, issuer_uid, issuer_principal, agent_uid, agent_id, "
                "tool, resource, environment, state FROM controller_grants"
            ).fetchall(),
            "events": conn.execute(
                "SELECT session_id, grant_id, peer_uid, kind, method, decision, reason FROM controller_events"
            ).fetchall(),
        }


def main():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise RuntimeError("The native probe requires Python -I -B -S")
    mode, state_dir, agent, operator = sys.argv[1:]
    state = Path(state_dir)
    if mode == "access-denied":
        denied_state(state)
        return

    stage = Path(__file__).resolve().parents[1]
    sys.path.extend((str(stage), str(stage / "deps")))
    from scripts import stage4_native_acceptance as fixture
    from scripts.stage43_native_probe import APPROVER, OPERATOR, OPERATORS

    if os.getuid() != fixture.CONTROL or os.geteuid() != fixture.CONTROL:
        raise RuntimeError("Controller state probes require the Control Plane UID")
    from scripts.stage42_native_probe import effect_guard

    if mode in {"serve", "contend"}:
        verify = effect_guard(
            "adaptive_agentops.stage4_boundary.operator",
            "adaptive_agentops.stage4_boundary.controller_state",
            sqlite_database=(state / "controller.sqlite3").resolve(),
        )
    from adaptive_agentops.stage4_boundary import Endpoint, Link, TrustedUIDs
    from adaptive_agentops.stage4_boundary.controller_state import ControllerLeaseError, provision_controller_state
    from adaptive_agentops.stage4_boundary.operator import OperatorIdentity, OperatorRole, serve_protected_admission

    if mode == "provision":
        provision_controller_state(state, fixture.CONTROL)
    elif mode == "snapshot":
        print(json.dumps(snapshot(state)))
    elif mode in {"serve", "contend"}:
        uids = TrustedUIDs(fixture.AGENT, fixture.CONTROL, fixture.TARGET)
        agent_ep = Endpoint(Link.AGENT_CONTROL, agent, fixture.PROPOSERS)
        operator_ep = Endpoint(Link.OPERATOR_CONTROL, operator, OPERATORS)
        identities = (
            OperatorIdentity(OPERATOR, "local-requester", frozenset({OperatorRole.REQUESTER})),
            OperatorIdentity(APPROVER, "local-approver", frozenset({OperatorRole.APPROVER})),
        )

        try:
            serve_protected_admission(agent_ep, operator_ep, uids, identities, state_dir=state)
        except ControllerLeaseError:
            if mode != "contend":
                raise
            verify()
            print("LEASE_DENIED")
        else:
            if mode != "serve":
                raise RuntimeError("Competing Controller opened the lease")
            verify()
            print("CONTROLLER_SESSION_CLOSED")
    else:
        raise ValueError("Unexpected Stage 4.4 probe invocation")


if __name__ == "__main__":
    main()
