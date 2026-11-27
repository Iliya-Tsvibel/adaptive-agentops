"""Subprocess probes for the disposable, offline Stage 6.E acceptance run."""

import os
from pathlib import Path
import socket
import sqlite3
import sys
from contextlib import closing
from uuid import UUID


INSPECTOR, INSPECTORS = 60106, 60116


def main():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise RuntimeError("Expected isolated native probe")
    root = Path(__file__).resolve().parents[1]
    sys.path.extend((str(root), str(root / "deps")))
    from scripts import stage4_native_acceptance as legacy
    from scripts.stage43_native_probe import APPROVER, OPERATOR, OPERATORS
    from adaptive_agentops.identity import LOCAL_APPROVER_ID
    from adaptive_agentops.stage4_boundary import BoundaryError, Endpoint, Link, TrustedUIDs
    from adaptive_agentops.stage4_boundary import controller_state as state, evidence_inspection as bridge
    from adaptive_agentops.stage4_boundary import operator, target_state

    mode, controller_dir, target_dir, audit_dir, agent_path, operator_path, target_path, inspect_path, *args = sys.argv[1:]
    uid = os.getuid()
    if uid != os.geteuid() or uid == 0:
        raise RuntimeError("Expected one dedicated service UID")
    uids = TrustedUIDs(legacy.AGENT, legacy.CONTROL, legacy.TARGET)
    target = Endpoint(Link.CONTROL_TARGET, target_path, legacy.DISPATCHERS)
    inspect = Endpoint(Link.OPERATOR_CONTROL, inspect_path, INSPECTORS)

    if mode == "provision-audit" and uid == legacy.CONTROL:
        bridge.provision_inspection_audit(audit_dir, uid)
    elif mode == "serve" and uid == legacy.CONTROL:
        agent = Endpoint(Link.AGENT_CONTROL, agent_path, legacy.PROPOSERS)
        operator_endpoint = Endpoint(Link.OPERATOR_CONTROL, operator_path, OPERATORS)
        operators = (
            operator.OperatorIdentity(OPERATOR, "local-requester", frozenset({operator.OperatorRole.REQUESTER})),
            operator.OperatorIdentity(APPROVER, LOCAL_APPROVER_ID, frozenset({operator.OperatorRole.APPROVER})),
        )
        if len(args) == 2 and args[1] == "audit-fail":
            original = state._commit

            def fail_after_commit(connection):
                original(connection)
                raise state.ControllerStateError("Modeled ambiguous inspection audit")

            state._commit = fail_after_commit
        bridge.serve_inspection_once(inspect, bridge.InspectorScope(INSPECTOR, UUID(args[0])), uids, operators,
                                     execution_endpoints=(agent, operator_endpoint, target),
                                     state_dir=controller_dir, audit_dir=audit_dir)
    elif mode == "inspect" and uid == INSPECTOR:
        try:
            print(bridge.inspect_dispatch(inspect, uid, uids.control, UUID(args[0])).model_dump_json())
        except (BoundaryError, OSError):
            print("DENIED")
    elif mode == "raw":
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(2)
                peer.connect(inspect.path)
                peer.sendall(bytes.fromhex(args[0]))
                peer.shutdown(socket.SHUT_WR)
                if peer.recv(1):
                    raise RuntimeError("Denied client received reply bytes")
        except (BrokenPipeError, ConnectionResetError, PermissionError):
            pass
        print("DENIED")
    elif mode == "stores-denied" and uid in {INSPECTOR, uids.agent}:
        for file in (Path(controller_dir) / state.DATABASE_NAME, Path(target_dir) / target_state.DATABASE_NAME,
                     Path(audit_dir) / bridge._AUDIT_DB):
            try:
                with file.open("rb"):
                    raise RuntimeError("Unprivileged process opened protected store")
            except PermissionError:
                pass
        print("DENIED")
    elif mode in {"target-lose-ack", "target-lookup", "target-claim-only"} and uid == uids.target:
        if mode == "target-lose-ack":
            def suppress_receipt(_peer, _frame, _deadline):
                raise BrokenPipeError("Modeled lost receipt after effect commit")
            target_state._send_frame = suppress_receipt
        if mode == "target-claim-only":
            commit = state._commit

            def exit_after_claim(connection):
                commit(connection)
                os._exit(86)

            state._commit = exit_after_claim
        try:
            reply = target_state.serve_target_once(target, uids, target_dir)
        except BrokenPipeError:
            if mode != "target-lose-ack":
                raise
        else:
            if mode != "target-lookup" or type(reply) is not target_state.ReceiptResult:
                raise RuntimeError("Unexpected Target operation")
    elif mode == "audit-count" and uid == uids.control:
        path = Path(audit_dir) / bridge._AUDIT_DB
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            print(conn.execute("SELECT count(*) FROM disclosures").fetchone()[0])
    else:
        raise RuntimeError("Unexpected Stage 6.E probe mode or principal")


if __name__ == "__main__":
    main()
