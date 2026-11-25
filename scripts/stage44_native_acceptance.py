"""Native durable Controller-state fixture under prevalidated /run."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory

from scripts import stage43_native_acceptance as managed
from scripts.stage43_native_probe import OPERATOR, OPERATORS


_PROBE = Path(__file__).absolute().with_name("stage44_native_probe.py")


def command(mode, state, sockets):
    return (sys.executable, "-I", "-B", "-S", str(_PROBE), mode, str(state), *map(str, sockets))


def exchange(legacy, uid, groups, mode, state, sockets):
    child = subprocess.run(
        command(mode, state, sockets), **legacy.identity(uid, groups),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
    )
    legacy.require(child.returncode == 0 and not child.stderr, f"Stage 4.4 {mode} failed: {child.stderr}")
    return child.stdout.strip()


@contextmanager
def prepare(legacy):
    with TemporaryDirectory(prefix="aao-stage44-", dir="/run") as directory:
        root = Path(directory)
        root.chmod(0o755)
        state = root / "state"
        state.mkdir(mode=0o700)
        os.chown(state, legacy.CONTROL, legacy.CONTROL)
        channels = []
        for name in ("revoke", "crash", "restart", "contender"):
            base = root / name
            base.mkdir(mode=0o755)
            agent = base / "agent" / "agent.sock"
            operator = base / "operator" / "operator.sock"
            for path, group in ((agent, legacy.PROPOSERS), (operator, OPERATORS)):
                path.parent.mkdir(mode=0o750)
                os.chown(path.parent, legacy.CONTROL, group)
            channels.append((agent, operator))
        yield state, tuple(channels)


def run(legacy, state, channels):
    print("START stage44 native protected Controller state probes", flush=True)
    groups = (legacy.PROPOSERS, OPERATORS)
    revoke_sockets, crash_sockets, restart_sockets, contender = channels

    def controller(mode, sockets):
        return exchange(legacy, legacy.CONTROL, groups, mode, state, sockets)

    def open_service(sockets):
        agent, operator = sockets
        return legacy.listening_process(
            command("serve", state, sockets), legacy.CONTROL, agent,
            groups=groups, additional_paths=(operator,),
        )

    def request(sockets, method, *args):
        return json.loads(managed.exchange(legacy, OPERATOR, (OPERATORS,), "operator", *sockets, method, *args))

    def proposal(sockets):
        return managed.exchange(legacy, legacy.AGENT, None, "agent", *sockets)

    def finish(server, sockets, *, accepted_operator_connections):
        for _ in range(16 - accepted_operator_connections):
            request(sockets, "INSPECT")
        output, _ = server.communicate(timeout=5)
        legacy.require(server.returncode == 0 and output.strip() == "CONTROLLER_SESSION_CLOSED",
                       "Protected Controller did not close normally")
        legacy.require(not any(os.path.lexists(path) for path in sockets), "Closed Controller left a socket")

    legacy.require(controller("provision", revoke_sockets) == "", "Unexpected provisioning output")
    file = (state / "controller.sqlite3").lstat()
    directory = state.lstat()
    legacy.require((file.st_mode, file.st_uid, file.st_nlink) == (stat.S_IFREG | 0o600, legacy.CONTROL, 1)
                   and (directory.st_mode, directory.st_uid) == (stat.S_IFDIR | 0o700, legacy.CONTROL),
                   "Unexpected protected state ownership or mode")

    with open_service(revoke_sockets) as server:
        legacy.require(exchange(legacy, legacy.AGENT, None, "access-denied", state, revoke_sockets) == "",
                       "Agent access probe returned unexpected output")
        legacy.require(exchange(legacy, OPERATOR, (OPERATORS,), "access-denied", state, revoke_sockets) == "",
                       "Operator access probe returned unexpected output")
        legacy.require(controller("contend", contender) == "LEASE_DENIED"
                       and not any(os.path.lexists(path) for path in contender),
                       "Competing Controller did not fail before binding")
        first = request(revoke_sockets, "ISSUE", "60")
        legacy.require(first[0] == "ACTIVE" and proposal(revoke_sockets) == "PROPOSAL_ALLOWED",
                       "Protected grant did not authorize proposal admission")
        legacy.require(request(revoke_sockets, "REVOKE", first[1]) == ["REVOKED", *first[1:]]
                       and proposal(revoke_sockets) == "DENY", "Durable revocation failed")
        finish(server, revoke_sockets, accepted_operator_connections=2)

    with open_service(crash_sockets) as server:
        active = request(crash_sockets, "ISSUE", "60")
        legacy.require(active[0] == "ACTIVE" and proposal(crash_sockets) == "PROPOSAL_ALLOWED",
                       "Controller did not send active pre-restart verdict")
        server.kill()
        server.communicate(timeout=5)
        legacy.require(server.returncode < 0, "Expected process interruption")

    before = json.loads(controller("snapshot", crash_sockets))
    legacy.require(any(row[:3] == active[1:4] and row[-1] == "ACTIVE" for row in before["grants"]),
                   "Crash fixture did not retain a durable active grant")
    with open_service(restart_sockets) as server:
        legacy.require(proposal(restart_sockets) == "DENY"
                       and request(restart_sockets, "REVOKE", active[1]) is None
                       and request(restart_sockets, "INSPECT") is None,
                       "Restart restored prior grant authority")
        replacement = request(restart_sockets, "ISSUE", "60")
        legacy.require(replacement[0] == "ACTIVE" and all(
            new not in (old1, old2) for new, old1, old2 in zip(replacement[1:4], first[1:4], active[1:4], strict=True)
        ), "Restart did not allocate fresh session, run and grant")
        finish(server, restart_sockets, accepted_operator_connections=3)

    after = json.loads(controller("snapshot", restart_sockets))
    sessions = {row[0]: row[1:] for row in after["sessions"]}
    grants = {row[0]: row[1:] for row in after["grants"]}
    events = after["events"]
    for issued, session_state, grant_state in (
        (first, "INVALIDATED", "REVOKED"),
        (active, "INVALIDATED", "INVALIDATED"),
        (replacement, "ACTIVE", "ACTIVE"),
    ):
        grant, session, run = issued[1:4]
        legacy.require(sessions.get(session) == [
            run, legacy.CONTROL, legacy.AGENT, "local-requester", session_state,
        ] and grants.get(grant) == [
            session, run, OPERATOR, "local-requester", legacy.AGENT, "incident-investigator-v1",
            "mock.get_health", "demo-api", "local-mock", grant_state,
        ], "Durable session or grant binding differs")
        legacy.require(any(entry[:6] == [session, grant, OPERATOR, "OPERATOR", "ISSUE", "ACCEPTED"]
                           for entry in events), "Missing durable issuance evidence")
    legacy.require(any(entry[:6] == [first[2], first[1], OPERATOR, "OPERATOR", "REVOKE", "ACCEPTED"]
                       for entry in events), "Missing durable revocation evidence")
    legacy.require(any(entry[0] == active[2] and entry[3:6] == ["LIFECYCLE", "RESTART", "INVALIDATED"]
                       for entry in events), "Missing durable restart invalidation evidence")
    legacy.require(any(entry[0] == replacement[2] and entry[2] == legacy.AGENT
                       and entry[3] == "ADMISSION" and entry[5:] == ["DENY", "MISSING_GRANT"]
                       for entry in events), "Missing durable post-restart denial evidence")
    print("PASS 4.4 private Controller state and overlapping service denied", flush=True)
    print("PASS 4.4 durable grant issue and revoke with matching decision evidence", flush=True)
    print("PASS 4.4 fresh Controller session invalidated an active grant after process exit", flush=True)
    print("PASS 4.4 restarted Controller denied stale grant and retained prior evidence", flush=True)
