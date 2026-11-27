"""Stage 6.E evidence checks using protected, separate native Linux identities."""

from contextlib import contextmanager
from hashlib import sha256
import grp
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import UUID

from scripts import stage46_native_acceptance as effects
from scripts import stage47_native_acceptance as hardened
from scripts.stage43_native_probe import APPROVER, OPERATOR, OPERATORS
from scripts.stage6e_native_probe import INSPECTOR, INSPECTORS


PROBE = Path(__file__).absolute().with_name("stage6e_native_probe.py")


@contextmanager
def prepare(legacy):
    legacy.require(INSPECTOR not in {entry.pw_uid for entry in pwd.getpwall()}
                   and not {INSPECTOR, INSPECTORS} & {entry.gr_gid for entry in grp.getgrall()},
                   "Stage 6.E test UID or group already assigned")
    with hardened.prepare(legacy) as paths, TemporaryDirectory(prefix="aao-6e-", dir="/run") as directory:
        root = Path(directory)
        root.chmod(0o755)
        audit = root / "audit"
        audit.mkdir(mode=0o700)
        os.chown(audit, legacy.CONTROL, legacy.CONTROL)
        endpoint = root / "inspector" / "read.sock"
        endpoint.parent.mkdir(mode=0o750)
        os.chown(endpoint.parent, legacy.CONTROL, INSPECTORS)
        endpoint.parent.chmod(0o750)
        yield *paths, audit, endpoint


def run(legacy, agent, operator, controller_dir, target_dir, target, audit, inspector):
    stage47_probe = PROBE.with_name("stage47_native_probe.py")
    control_groups = (legacy.PROPOSERS, OPERATORS, legacy.DISPATCHERS)

    def stage47(mode, *args):
        return (sys.executable, "-I", "-B", "-S", str(stage47_probe), mode,
                str(controller_dir), str(agent), str(operator), str(target), *args)

    def command(mode, *args):
        return (sys.executable, "-I", "-B", "-S", str(PROBE), mode,
                str(controller_dir), str(target_dir), str(audit), str(agent), str(operator),
                str(target), str(inspector), *args)

    def invoke(uid, groups, args):
        result = subprocess.run(args, **legacy.identity(uid, groups), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=5)
        legacy.require(result.returncode == 0 and not result.stderr,
                       f"Stage 6.E subprocess failed (exit {result.returncode}): {result.stderr[:500]}")
        return result.stdout.strip()

    def action(method, pending=None):
        args = (method,) if pending is None else (method, json.dumps(pending))
        return json.loads(invoke(APPROVER, (OPERATORS,), stage47("action", *args)))

    def start_dispatch(target_mode, target_exit):
        with legacy.listening_process(stage47("server"), legacy.CONTROL, agent,
                                      groups=control_groups, additional_paths=(operator,)) as control:
            grant = json.loads(invoke(OPERATOR, (OPERATORS,), stage47("grant", "ISSUE", "60")))
            allowed = json.loads(invoke(legacy.AGENT, (legacy.PROPOSERS,), stage47("proposal", "demo-api")))
            legacy.require(grant[0] == "ACTIVE" and allowed, "Setup did not authorize the proposal")
            pending = action("INSPECT_ACTION")
            legacy.require(action("APPROVE", pending) == "APPROVED", "Setup approval failed")
            with legacy.listening_process(command(target_mode), legacy.TARGET, target,
                                          groups=(legacy.DISPATCHERS,)) as process:
                legacy.require(action("DISPATCH", pending) == "UNKNOWN", "Expected historical UNKNOWN")
                effects.finish(legacy, process, status=target_exit)
            if target_exit == 86:
                # An abrupt exit leaves a socket entry. Remove only that
                # verified fixture socket before starting the next Target.
                legacy.check_socket_metadata(target, legacy.TARGET, legacy.DISPATCHERS)
                target.unlink()
            legacy.require(action("DISPATCH", pending) is None, "UNKNOWN restored dispatch authority")
            output, _ = control.communicate(timeout=13)
            legacy.require(control.returncode == 0 and not output, "Execution Controller did not exit cleanly")

    def snapshot():
        return tuple(sha256((directory / name).read_bytes()).digest() for directory, name in (
            (controller_dir, "controller.sqlite3"), (target_dir, "target.sqlite3"),
        ))

    @contextmanager
    def serve(dispatch_id, *args):
        with legacy.listening_process(command("serve", dispatch_id, *args), legacy.CONTROL, inspector,
                                      groups=(*control_groups, INSPECTORS)) as process:
            legacy.check_socket_metadata(inspector, legacy.CONTROL, INSPECTORS)
            yield
            effects.finish(legacy, process)

    def inspect(dispatch_id):
        return invoke(INSPECTOR, (INSPECTORS,), command("inspect", dispatch_id))

    def lookup(dispatch_id):
        with legacy.listening_process(command("target-lookup"), legacy.TARGET, target,
                                      groups=(legacy.DISPATCHERS,)) as process:
            with serve(dispatch_id):
                result = json.loads(inspect(dispatch_id))
            effects.finish(legacy, process)
        return result

    invoke(legacy.CONTROL, (), stage47("provision"))
    invoke(legacy.CONTROL, (), command("provision-audit"))
    effects.invoke(legacy, legacy.TARGET, "provision-target", target_dir, target)
    start_dispatch("target-lose-ack", 0)
    start_dispatch("target-claim-only", 86)
    state = json.loads(invoke(legacy.CONTROL, (), stage47("evidence")))
    rows = state["dispatches"]
    legacy.require(len(rows) == 2 and all(row[7:] == [None, "UNKNOWN"] for row in rows),
                   "Failed to establish two historical UNKNOWN records")
    positive_id, unresolved_id = (row[2] for row in rows)
    original = snapshot()

    positive = lookup(positive_id)
    legacy.require(positive == {
        "version": 1, "result": "RECORDED_SIMULATED_EFFECT", "controller": "UNKNOWN",
        "target": "COMMITTED", "effect": "TARGET",
    }, "Wrong lost-ack reconciliation")
    print("PASS 6.E committed SQLite effect after lost acknowledgement; Controller UNKNOWN preserved", flush=True)

    unresolved = lookup(unresolved_id)
    with serve(unresolved_id):
        unavailable = json.loads(inspect(unresolved_id))
    legacy.require(unresolved == {
        "version": 1, "result": "UNRESOLVED", "controller": "UNKNOWN", "target": "UNKNOWN", "effect": None,
    } and unavailable == {**unresolved, "target": "UNAVAILABLE"}, "Partial or unavailable evidence resolved the dispatch")
    print("PASS 6.E Target restart preserved claim-only uncertainty and unavailability", flush=True)

    for uid in (legacy.AGENT, OPERATOR, APPROVER):
        with serve(positive_id):
            # Give socket reachability solely in this negative test to isolate the kernel UID check.
            legacy.require(invoke(uid, (INSPECTORS,), command("raw", (b"AAO\x08\x01" + UUID(positive_id).bytes).hex())) == "DENIED",
                           "Unexpected non-inspector disclosure")
    with serve(positive_id):
        legacy.require(inspect(unresolved_id) == "DENIED", "Inspector escaped the immutable dispatch scope")
    print("PASS 6.E reachable agent, requester and approver denied; inspector scope bound to one dispatch", flush=True)

    for uid in (legacy.AGENT, INSPECTOR):
        legacy.require(invoke(uid, (), command("stores-denied")) == "DENIED", "Protected store access was possible")
    with serve(positive_id):
        competing = subprocess.run(stage47("server"), **legacy.identity(legacy.CONTROL, control_groups),
                                   capture_output=True, timeout=5)
        legacy.require(competing.returncode != 0 and b"already leased" in competing.stderr,
                       "Execution Controller obtained inspection lease")
        legacy.require(invoke(INSPECTOR, (INSPECTORS,), command("raw", b"AAO\x07\x03\x04".hex())) == "DENIED",
                       "Mutation frame was not denied")
    print("PASS 6.E protected stores, exclusive Controller lease and mutation frame denial", flush=True)

    with serve(positive_id, "audit-fail"):
        legacy.require(inspect(positive_id) == "DENIED", "Ambiguous audit disclosed protected evidence")
    count = invoke(legacy.CONTROL, (), command("audit-count"))
    legacy.require(count == "4" and snapshot() == original, "Inspection changed historical data or audit cardinality")
    print("PASS 6.E durable disclosure gate and byte-for-byte execution history preservation", flush=True)

    with legacy.listening_process(command("target-lookup"), legacy.TARGET, target,
                                  groups=(legacy.DISPATCHERS,)):
        target.chmod(0o666)
        with serve(positive_id):
            invalid = json.loads(inspect(positive_id))
        target.chmod(0o660)
    legacy.require(invalid == {**unresolved, "result": "INCONSISTENT", "target": "INVALID"}
                   and snapshot() == original, "Unsafe Target socket yielded positive attribution")
    print("PASS 6.E unsafe Target socket prevented positive attribution", flush=True)

    with TemporaryDirectory(prefix="candidate-", dir=inspector.parent.parent) as path:
        staged = Path(path) / inspector.name
        os.chown(path, legacy.AGENT, legacy.AGENT)
        fake = (sys.executable, "-I", "-B", "-S", "-c", """
import socket
import sys
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
    listener.settimeout(5)
    listener.bind(sys.argv[1])
    listener.listen(1)
    client, _ = listener.accept()
    with client:
        client.settimeout(2)
        if client.recv(1) != b"":
            raise RuntimeError("Inspection sent bytes to a wrong-UID server")
""", str(staged))
        with legacy.listening_process(fake, legacy.AGENT, staged, groups=()) as process:
            os.chown(staged, legacy.CONTROL, INSPECTORS)
            staged.chmod(0o660)
            staged.rename(inspector)
            try:
                legacy.check_socket_metadata(inspector, legacy.CONTROL, INSPECTORS)
                legacy.require(inspect(positive_id) == "DENIED", "Client trusted wrong Controller UID")
                effects.finish(legacy, process)
            finally:
                inspector.unlink()
    print("PASS 6.E inspector sent no bytes to a wrong-UID Controller", flush=True)
