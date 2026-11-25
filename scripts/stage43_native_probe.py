"""Side-specific native probes for the separate operator and agent sockets."""

import errno
import json
import os
from pathlib import Path
import socket
import sys
from uuid import UUID


OPERATOR, APPROVER, OPERATORS = 60104, 60105, 60113
FORGED = b'AAO\x03\x01\x02{"uid":60104}'


def verify_denial(path, mode):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(4)
        try:
            client.connect(path)
        except OSError as error:
            if mode == "filesystem-denial" and error.errno == errno.EACCES:
                return
            raise
        if mode == "filesystem-denial":
            raise RuntimeError("Agent reached the operator socket")
        try:
            if mode == "malformed":
                client.sendall(FORGED)
            client.shutdown(socket.SHUT_WR)
            if client.recv(1):
                raise RuntimeError("Rejected operator request received protocol bytes")
        except (BrokenPipeError, ConnectionResetError):
            if mode != "unmapped":
                raise


def main():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise RuntimeError("The native probe requires Python -I -B -S")
    mode, agent_path, operator_path, *args = sys.argv[1:]
    if mode in {"filesystem-denial", "unmapped", "malformed"} and not args:
        verify_denial(operator_path, mode)
        return

    stage = Path(__file__).resolve().parents[1]
    sys.path.extend((str(stage), str(stage / "deps")))
    from scripts import stage4_native_acceptance as legacy
    from scripts.stage42_native_probe import effect_guard, wrong_server

    if mode == "wrong-server" and not args:
        wrong_server(operator_path)
        return
    verify_effects = effect_guard("adaptive_agentops.stage4_boundary.operator") if mode == "server" else None
    from adaptive_agentops.stage4_boundary import Endpoint, IdentityError, Link, TrustedUIDs, delegation, operator
    from adaptive_agentops.tool_requests import MockGetHealthProposal

    uids = TrustedUIDs(legacy.AGENT, legacy.CONTROL, legacy.TARGET)
    agent = Endpoint(Link.AGENT_CONTROL, agent_path, legacy.PROPOSERS)
    operator_endpoint = Endpoint(Link.OPERATOR_CONTROL, operator_path, OPERATORS)
    if mode == "server" and not args:
        roles = (
            operator.OperatorIdentity(OPERATOR, "local-requester", frozenset({operator.OperatorRole.REQUESTER})),
            operator.OperatorIdentity(APPROVER, "local-approver", frozenset({operator.OperatorRole.APPROVER})),
        )
        evidence = operator.serve_managed_admission(agent, operator_endpoint, uids, roles)
        verify_effects()
        print(json.dumps({
            "operator": [
                [event.operator_uid, event.method.name if event.method else None, event.result.value,
                 str(event.grant_id) if event.grant_id else None, str(event.session_id)]
                for event in evidence.operator
            ],
            "admission": [
                [event.peer_uid, event.reason.value, str(event.grant_id) if event.grant_id else None,
                 str(event.session_id), event.policy_decision.value if event.policy_decision else None]
                for event in evidence.admission
            ],
        }))
    elif mode == "agent" and not args:
        proposal = MockGetHealthProposal(tool="mock.get_health", resource="demo-api", arguments={})
        print(delegation.request_admission(agent, uids, proposal).name)
    elif mode == "operator" and args:
        method = operator.GrantMethod[args[0]]
        argument = {"ISSUE": lambda: int(args[1]), "REVOKE": lambda: UUID(args[1]), "INSPECT": lambda: None}[args[0]]()
        try:
            view = operator.request_grant(operator_endpoint, os.getuid(), uids.control, method, argument)
        except IdentityError:
            print(json.dumps("WRONG_SERVER"))
        else:
            print(json.dumps(
                [view.state.name, str(view.grant_id), str(view.session_id), str(view.run_id),
                 view.wall_expiry_ns, view.agent_uid] if view is not None else None
            ))
    else:
        raise ValueError("Unexpected Stage 4.3 probe invocation")


if __name__ == "__main__":
    main()
