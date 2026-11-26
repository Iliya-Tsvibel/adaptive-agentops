"""Stage 4.7 client and Controller commands for the offline native harness."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import UUID


def main():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise RuntimeError("The native probe requires Python -I -B -S")
    mode, state, agent_path, operator_path, target_path, *args = sys.argv[1:]
    root = Path(__file__).resolve().parents[1]
    sys.path.extend((str(root), str(root / "deps")))

    from scripts import stage4_native_acceptance as legacy
    from scripts.stage42_native_probe import effect_guard
    from scripts.stage43_native_probe import APPROVER, OPERATOR, OPERATORS
    from adaptive_agentops.actions import PendingRestartAction
    from adaptive_agentops.identity import LOCAL_APPROVER_ID
    from adaptive_agentops.stage4_boundary import BoundaryError, Endpoint, Link, TrustedUIDs
    from adaptive_agentops.stage4_boundary import controller_state, hardened, operator
    from adaptive_agentops.tool_requests import MockRestartServiceProposal

    agent = Endpoint(Link.AGENT_CONTROL, agent_path, legacy.PROPOSERS)
    operator_endpoint = Endpoint(Link.OPERATOR_CONTROL, operator_path, OPERATORS)
    target = Endpoint(Link.CONTROL_TARGET, target_path, legacy.DISPATCHERS)
    uids = TrustedUIDs(legacy.AGENT, legacy.CONTROL, legacy.TARGET)
    uid = os.getuid()
    if uid != os.geteuid():
        raise RuntimeError("Unexpected effective UID")

    if mode in {"provision", "server", "evidence"} and uid == legacy.CONTROL:
        database = (Path(state) / controller_state.DATABASE_NAME).resolve()
        if mode == "evidence":
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
                evidence = {
                    "actions": conn.execute(
                        "SELECT s.session_id, s.run_id, g.grant_id, a.action_id, a.action_fingerprint, "
                        "p.approval_id, p.decision, p.consumed, g.state "
                        "FROM pending_bindings AS b JOIN controller_sessions AS s USING(session_id) "
                        "JOIN controller_grants AS g USING(grant_id) JOIN actions AS a USING(action_id) "
                        "LEFT JOIN approvals AS p USING(action_id) ORDER BY a.created_at"
                    ).fetchall(),
                    "events": conn.execute(
                        "SELECT session_id, grant_id, peer_uid, method, decision, reason, policy_decision "
                        "FROM controller_events ORDER BY rowid"
                    ).fetchall(),
                    "dispatches": conn.execute(
                        "SELECT r.session_id, r.grant_id, r.dispatch_id, r.action_id, r.approval_id, "
                        "r.action_fingerprint, e.request_digest, e.effect_number, o.status "
                        "FROM dispatch_reservations AS r LEFT JOIN dispatch_effect_evidence AS e USING(dispatch_id) "
                        "LEFT JOIN dispatch_outcomes AS o USING(dispatch_id) ORDER BY r.rowid"
                    ).fetchall(),
                }
            print(json.dumps(evidence))
            return
        verify = effect_guard(
            "adaptive_agentops.actions",
            "adaptive_agentops.approvals",
            "adaptive_agentops.stage4_boundary.controller_state",
            "adaptive_agentops.stage4_boundary.dispatch_state",
            "adaptive_agentops.stage4_boundary.dispatch_wire",
            "adaptive_agentops.stage4_boundary.hardened",
            "adaptive_agentops.stage4_boundary.operator",
            "adaptive_agentops.stage4_boundary.target_state",
            sqlite_database=database, unix_peer=Path(target_path), approval_schema=True,
        )
        identities = (
            operator.OperatorIdentity(OPERATOR, "local-requester", frozenset({operator.OperatorRole.REQUESTER})),
            operator.OperatorIdentity(APPROVER, LOCAL_APPROVER_ID, frozenset({operator.OperatorRole.APPROVER})),
        )
        if mode == "provision":
            hardened.provision_hardened_state(state, uid)
        else:
            hardened.serve_hardened(agent, operator_endpoint, target, uids, identities, state_dir=state)
        verify()
        return

    if mode == "proposal" and uid == legacy.AGENT:
        proposal = MockRestartServiceProposal(tool="mock.restart_service", resource=args[0], arguments={})
        print(json.dumps(hardened.submit_restart_proposal(agent, uids, proposal)))
        return

    if mode == "grant" and uid in {APPROVER, OPERATOR}:
        method = operator.GrantMethod[args[0]]
        argument = int(args[1]) if method is operator.GrantMethod.ISSUE else UUID(args[1])
        result = operator.request_grant(operator_endpoint, uid, uids.control, method, argument)
        print(json.dumps(None if result is None else [result.state.name, str(result.grant_id), str(result.session_id)]))
        return

    if mode == "action" and uid in {APPROVER, OPERATOR, legacy.AGENT}:
        method = hardened.ActionMethod[args[0]]
        action = PendingRestartAction.model_validate_json(args[1]) if len(args) == 2 else None
        try:
            result = hardened.request_action(operator_endpoint, uid, uids.control, method, action)
        except (BoundaryError, OSError):
            if uid != legacy.AGENT:
                raise
            result = "AGENT_REJECTED"
        print(result.model_dump_json() if isinstance(result, PendingRestartAction) else json.dumps(result))
        return

    raise RuntimeError("Unexpected Stage 4.7 probe mode or UID")


if __name__ == "__main__":
    main()
