"""Run the Stage 4.3 fixture inside the authorized offline native VM."""

from contextlib import contextmanager
import grp
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import UUID

from .stage43_native_probe import APPROVER, OPERATOR, OPERATORS


PROBE = Path(__file__).absolute().with_name("stage43_native_probe.py")


def command(mode, agent, operator, *args):
    return (sys.executable, "-I", "-B", "-S", str(PROBE), mode, str(agent), str(operator), *args)


def exchange(legacy, uid, groups, mode, agent, operator, *args):
    child = subprocess.run(
        command(mode, agent, operator, *args), **legacy.identity(uid, groups),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
    )
    legacy.require(child.returncode == 0 and not child.stderr, f"Stage 4.3 {mode} probe failed: {child.stderr}")
    return child.stdout.strip()


def check_session(legacy, agent, operator):
    def request(uid, method, *args):
        return json.loads(exchange(legacy, uid, (OPERATORS,), "operator", agent, operator, method, *args))

    def proposal():
        return exchange(legacy, legacy.AGENT, None, "agent", agent, operator)

    def raw(uid, groups, mode):
        legacy.require(exchange(legacy, uid, groups, mode, agent, operator) == "", "Unexpected raw probe output")

    with legacy.listening_process(
        command("server", agent, operator), legacy.CONTROL, agent,
        groups=(legacy.PROPOSERS, OPERATORS), additional_paths=(operator,),
    ) as server:
        legacy.check_socket_metadata(agent, legacy.CONTROL, legacy.PROPOSERS)
        legacy.check_socket_metadata(operator, legacy.CONTROL, OPERATORS)
        raw(legacy.AGENT, None, "filesystem-denial")
        raw(legacy.AGENT, (OPERATORS,), "unmapped")
        raw(legacy.TARGET, (OPERATORS,), "unmapped")
        raw(OPERATOR, (OPERATORS,), "malformed")
        legacy.require(proposal() == "DENY", "Unissued session admitted a proposal")
        legacy.require(request(APPROVER, "ISSUE", "60") is None, "Approver acquired requester authority")
        issued = request(OPERATOR, "ISSUE", "60")
        state, grant, session, run, expiry, agent_uid = issued
        legacy.require(state == "ACTIVE" and all(UUID(value).version == 4 for value in (grant, session, run))
                       and type(expiry) is int and expiry > 0 and agent_uid == legacy.AGENT, "Invalid issued grant")
        legacy.require(request(OPERATOR, "INSPECT") == issued, "Own grant inspection did not match issuance")
        legacy.require(request(APPROVER, "REVOKE", grant) is None, "Approver revoked the requester's grant")
        legacy.require(proposal() == "PROPOSAL_ALLOWED", "Live managed grant did not yield a positive verdict")
        revoked = ["REVOKED", *issued[1:]]
        legacy.require(request(OPERATOR, "REVOKE", grant) == revoked, "Requester revocation failed")
        legacy.require(proposal() == "DENY", "Revoked grant admitted a proposal")
        # Eight operator exchanges have consumed slots, including the two unmapped peers.
        for _ in range(8):
            legacy.require(request(OPERATOR, "INSPECT") == revoked, "Revoked grant changed during inspection")
        output, errors = server.communicate(timeout=5)
        legacy.require(server.returncode == 0 and errors is None, "Managed controller session did not complete")
        evidence = json.loads(output)

    expected_operator = [
        [None, None, "PEER_REJECTED", None, session],
        [None, None, "PEER_REJECTED", None, session],
        [OPERATOR, None, "INVALID_REQUEST", None, session],
        [APPROVER, "ISSUE", "METHOD_DENIED", None, session],
        [OPERATOR, "ISSUE", "ACCEPTED", grant, session],
        [OPERATOR, "INSPECT", "ACCEPTED", grant, session],
        [APPROVER, "REVOKE", "METHOD_DENIED", None, session],
        [OPERATOR, "REVOKE", "ACCEPTED", grant, session],
        *[[OPERATOR, "INSPECT", "ACCEPTED", grant, session]] * 8,
    ]
    legacy.require(evidence["operator"] == expected_operator, "Operator decision evidence differs")
    legacy.require(evidence["admission"] == [
        [legacy.AGENT, "MISSING_GRANT", None, session, None],
        [legacy.AGENT, "ADMITTED", grant, session, "ALLOW"],
        [legacy.AGENT, "GRANT_REVOKED", grant, session, None],
    ], "Managed admission evidence differs")
    legacy.require(not os.path.lexists(agent) and not os.path.lexists(operator), "Managed sockets were not removed")
    print("PASS 4.3 operator filesystem isolation and kernel UID rejection", flush=True)
    print("PASS 4.3 authenticated requester issuance and own grant inspection", flush=True)
    print("PASS 4.3 approver-only and forged operator authority denied", flush=True)
    print("PASS 4.3 live managed grant and revoked proposal denial", flush=True)
    print("PASS 4.3 bounded operator connections and both sockets cleaned up", flush=True)


def check_wrong_server(legacy, root, agent, operator):
    with TemporaryDirectory(prefix="wrong-", dir=root) as directory:
        wrong = Path(directory)
        os.chown(wrong, legacy.TARGET, legacy.TARGET)
        staged = wrong / operator.name
        with legacy.listening_process(command("wrong-server", agent, staged), legacy.TARGET, staged) as server:
            fd = os.open(staged, os.O_PATH | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                legacy.require(info.st_uid == legacy.TARGET, "Wrong server has unexpected UID")
                pinned = f"/proc/self/fd/{fd}"
                os.chown(pinned, legacy.CONTROL, OPERATORS)
                os.chmod(pinned, 0o660)
                staged.rename(operator)
                try:
                    moved = operator.lstat()
                    legacy.require((moved.st_dev, moved.st_ino) == (info.st_dev, info.st_ino),
                                   "Wrong server socket changed during staging")
                    legacy.check_socket_metadata(operator, legacy.CONTROL, OPERATORS)
                    legacy.require(json.loads(exchange(
                        legacy, OPERATOR, (OPERATORS,), "operator", agent, operator, "ISSUE", "60",
                    )) == "WRONG_SERVER", "Operator did not reject wrong server UID")
                    output, _ = server.communicate(timeout=5)
                    legacy.require(server.returncode == 0 and json.loads(output) == "NO_PROPOSAL_BYTES",
                                   "Wrong server received operator request bytes")
                finally:
                    operator.unlink()
            finally:
                os.close(fd)
    print("PASS 4.3 operator rejected wrong server UID before sending data", flush=True)


@contextmanager
def prepare(legacy):
    reserved = {OPERATOR, APPROVER, OPERATORS}
    legacy.require(not reserved.intersection(entry.pw_uid for entry in pwd.getpwall())
                   and not reserved.intersection(entry.gr_gid for entry in grp.getgrall()),
                   "Operator test UID or GID is already assigned")
    with TemporaryDirectory(prefix="aao-stage43-", dir="/run") as directory:
        root = Path(directory)
        root.chmod(0o755)
        agent, operator = root / "agent" / "agent.sock", root / "operator" / "operator.sock"
        for path, group in ((agent, legacy.PROPOSERS), (operator, OPERATORS)):
            path.parent.mkdir(mode=0o750)
            os.chown(path.parent, legacy.CONTROL, group)
            path.parent.chmod(0o750)
        yield root, agent, operator


def run(legacy, root, agent, operator):
    print("START stage43 native operator and delegation probes", flush=True)
    check_session(legacy, agent, operator)
    check_wrong_server(legacy, root, agent, operator)
