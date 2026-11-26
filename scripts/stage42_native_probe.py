"""Test-side fault fixtures and clients, invoked under dedicated service UIDs."""

from dataclasses import replace
import json
from pathlib import Path
import socket
import sys
from urllib.parse import unquote, urlsplit


class InjectedJournalError(OSError):
    pass


def server(delegation, endpoint, uids, scenario, verify_effects):
    if scenario not in {"live", "missing", "expired", "policy-deny", "audit-failure", "post-audit-expiry"}:
        raise ValueError("Unknown admission fixture")
    grant_seconds = None if scenario == "missing" else 60
    if scenario in {"expired", "post-audit-expiry"}:
        # Fixed wall and elapsed samples move together to the same exact deadline.
        clocks = [100_000_000_000, 10_000_000_000]
        deadline = tuple(clock + grant_seconds * 1_000_000_000 for clock in clocks)
        delegation._read_clocks = lambda: tuple(clocks)
        if scenario == "expired":
            samples = iter((tuple(clocks), deadline))
            delegation._read_clocks = lambda: next(samples, deadline)
        else:
            record = delegation._Session.record

            def record_then_expire(session, peer_uid, outcome):
                record(session, peer_uid, outcome)
                clocks[:] = deadline

            delegation._Session.record = record_then_expire
    elif scenario == "policy-deny":
        policy = delegation.evaluate_policy
        delegation.evaluate_policy = lambda context, proposal: policy(
            replace(context, requester_id="unauthorized-fixture"), proposal,
        )
    elif scenario == "audit-failure":
        def fail_record(_session, _peer_uid, outcome):
            if outcome.decision is not delegation.AdmissionDecision.PROPOSAL_ALLOWED:
                raise RuntimeError("Journal fixture did not reach an admitted decision")
            raise InjectedJournalError

        delegation._Session.record = fail_record

    try:
        events = delegation.serve_admission(endpoint, uids, fixture_grant_seconds=grant_seconds)
    except InjectedJournalError:
        if scenario != "audit-failure":
            raise
        evidence = "INJECTED_JOURNAL_FAILURE"
    else:
        evidence = [
            {
                "session": str(event.session_id),
                "peer_uid": event.peer_uid,
                "grant_id": str(event.grant_id) if event.grant_id is not None else None,
                "grant": event.grant_matched,
                "policy": event.policy_decision.value if event.policy_decision is not None else None,
                "reason": event.reason.value,
                "decision": event.decision.name,
            }
            for event in events
        ]
    verify_effects()
    print(json.dumps(evidence))


def effect_guard(*additional_modules, sqlite_database=None, unix_peer=None, approval_schema=False):
    allowed_modules = {
        "adaptive_agentops.identity", "adaptive_agentops.policy", "adaptive_agentops.tool_requests",
        "adaptive_agentops.stage4_boundary", "adaptive_agentops.stage4_boundary.delegation",
        *additional_modules,
    }
    violations = []
    blocked = {
        "socket.connect", "socket.sendto", "socket.sendmsg", "socket.sendmsg_afalg",
        "socket.getaddrinfo", "sqlite3.connect",
        "subprocess.Popen", "os.system", "os.exec", "os.fork", "os.posix_spawn",
    }

    def forbidden_module(name):
        return name.startswith("adaptive_agentops.") and name not in allowed_modules

    def forbid_effects(event, args):
        if event == "socket.connect" and unix_peer is not None and len(args) == 2:
            connection, address = args
            if (getattr(connection, "family", None) == socket.AF_UNIX
                    and getattr(connection, "type", None) == socket.SOCK_STREAM
                    and type(address) is str and address.startswith("/")):
                try:
                    if Path(address).resolve() == unix_peer:
                        return
                except (ValueError, OSError, RuntimeError):
                    pass
        if (event == "sqlite3.connect" and sqlite_database is not None
                and len(args) == 1 and type(args[0]) is str):
            if approval_schema and args[0] == ":memory:":
                return
            try:
                database = urlsplit(args[0])
                if (database.scheme == "file" and not database.netloc and not database.fragment
                        and (database.query == "mode=rw" or (approval_schema and database.query == "mode=ro"))
                        and Path(unquote(database.path)).resolve() == sqlite_database):
                    return
            except (ValueError, OSError, RuntimeError):
                pass
        if event in blocked or (event == "import" and forbidden_module(args[0])):
            violations.append(event)
            raise RuntimeError("Admission attempted an effect")

    def verify():
        if violations or any(forbidden_module(name) for name in sys.modules):
            raise RuntimeError("An execution path was reached")

    sys.addaudithook(forbid_effects)
    return verify


def wrong_server(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.settimeout(10)
        listener.bind(path)
        listener.listen(1)
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(2)
            if connection.recv(1) != b"":
                raise RuntimeError("Client sent proposal bytes to a wrong-UID server")
    print(json.dumps("NO_PROPOSAL_BYTES"))


def send_rejected_proposal(path, frame, mode):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(4)
        connection.connect(path)  # Filesystem reachability must hold even for the wrong agent.
        try:
            connection.sendall(frame)
            if mode != "stall-before-eof":
                connection.shutdown(socket.SHUT_WR)
            reply = connection.recv(1)
        except (BrokenPipeError, ConnectionResetError):
            if mode != "wrong-agent":
                raise
            return
        if reply != b"":
            raise RuntimeError("Rejected proposal received a verdict")


def main():
    if not sys.flags.isolated or not sys.flags.no_site:
        raise RuntimeError("The native probe requires Python -I -S")
    mode, path, *args = sys.argv[1:]
    if mode == "wrong-server" and not args:
        wrong_server(path)
        return
    if mode in {"wrong-agent", "expect-eof", "stall-before-eof"} and len(args) == 1:
        send_rejected_proposal(path, bytes.fromhex(args[0]), mode)
        return

    # -I -S leaves only the system standard library on sys.path.
    # Both additions come from the protected staging tree.
    stage = Path(__file__).resolve().parents[1]
    sys.path.extend((str(stage), str(stage / "deps")))
    verify_effects = effect_guard() if mode == "server" else None
    from adaptive_agentops.stage4_boundary import Endpoint, IdentityError, Link, TrustedUIDs
    from adaptive_agentops.stage4_boundary import delegation
    from adaptive_agentops.tool_requests import MockGetHealthProposal
    from scripts import stage4_native_acceptance as legacy

    endpoint = Endpoint(Link.AGENT_CONTROL, path, legacy.PROPOSERS)
    uids = TrustedUIDs(legacy.AGENT, legacy.CONTROL, legacy.TARGET)
    if mode == "dependency-preflight" and not args:
        from pydantic_core import _pydantic_core

        if not Path(_pydantic_core.__file__).is_relative_to(stage / "deps"):
            raise RuntimeError("Native dependency did not load from staged wheels")
        print("DEPENDENCIES_READY")
    elif mode == "server" and len(args) == 1:
        server(delegation, endpoint, uids, args[0], verify_effects)
    elif mode == "client" and len(args) <= 1:
        proposal = MockGetHealthProposal(tool="mock.get_health", resource=args[0] if args else "demo-api",
                                         arguments={})
        try:
            verdict = delegation.request_admission(endpoint, uids, proposal)
        except IdentityError:
            print("WRONG_SERVER")
        except FileNotFoundError:
            print("NO_SOCKET")
        else:
            print(verdict.name)
    else:
        raise ValueError("Unknown probe invocation")


if __name__ == "__main__":
    main()
