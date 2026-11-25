"""Native workload mediation proof for the synthetic no-effect dispatch."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory


_PROBE = Path(__file__).absolute().with_name("stage45_native_probe.py")


def command(mode, state, path):
    return (sys.executable, "-I", "-B", "-S", str(_PROBE), mode, str(state), str(path))


def exchange(legacy, uid, groups, mode, state, path):
    child = subprocess.run(
        command(mode, state, path), **legacy.identity(uid, groups), capture_output=True,
        stdin=subprocess.DEVNULL, text=True, timeout=5,
    )
    legacy.require(child.returncode == 0 and not child.stderr, f"Stage 4.5 {mode} failed: {child.stderr}")
    return child.stdout.strip()


def finished(legacy, child):
    output, _ = child.communicate(timeout=5)
    legacy.require(child.returncode == 0, f"Stage 4.5 listener failed: {output.strip()}")
    return output.strip()


@contextmanager
def prepare(legacy):
    with TemporaryDirectory(prefix="aao-stage45-", dir="/run") as directory:
        root = Path(directory)
        root.chmod(0o755)
        states = []
        for name in ("wrong-target-state", "valid-state"):
            state = root / name
            state.mkdir(mode=0o700)
            os.chown(state, legacy.CONTROL, legacy.CONTROL)
            states.append(state)
        sockets = []
        for name in ("wrong-control", "wrong-target", "approved", "repeat"):
            parent = root / name
            parent.mkdir(mode=0o750)
            os.chown(parent, legacy.TARGET, legacy.DISPATCHERS)
            sockets.append(parent / "dispatch.sock")
        yield root, states, sockets


def check_wrong_target(legacy, root, state, target):
    with TemporaryDirectory(prefix="rogue-", dir=root) as directory:
        staged = Path(directory) / target.name
        os.chown(staged.parent, legacy.AGENT, legacy.AGENT)
        with legacy.listening_process(
            command("wrong-target", state, staged), legacy.AGENT, staged,
        ) as listener:
            fd = os.open(staged, os.O_PATH | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                legacy.require(stat.S_ISSOCK(info.st_mode) and info.st_uid == legacy.AGENT,
                               "Wrong target identity fixture differs")
                pinned = f"/proc/self/fd/{fd}"
                os.chown(pinned, legacy.TARGET, legacy.DISPATCHERS)
                os.chmod(pinned, 0o660)
                staged.rename(target)
                try:
                    moved = target.lstat()
                    legacy.require((moved.st_dev, moved.st_ino) == (info.st_dev, info.st_ino),
                                   "Wrong target socket changed while being staged")
                    legacy.check_socket_metadata(target, legacy.TARGET, legacy.DISPATCHERS)
                    result = exchange(legacy, legacy.CONTROL, None, "controller", state, target)
                    legacy.require(result == "PEER_DENIED",
                                   "Wrong target did not fail before reservation")
                    legacy.require(finished(legacy, listener) == "NO_DISPATCH_BYTES",
                                   "Wrong target socket did not verify zero bytes")
                finally:
                    target.unlink()
            finally:
                os.close(fd)


def run(legacy, root, states, sockets):
    print("START stage45 native no-effect dispatch probes", flush=True)
    denied, wrong, valid, repeat = sockets
    wrong_state, state = states

    def control(mode, path, state=state):
        return exchange(legacy, legacy.CONTROL, None, mode, state, path)

    def target(mode, path):
        return legacy.listening_process(
            command(mode, state, path), legacy.TARGET, path, groups=(legacy.DISPATCHERS,),
        )

    def snapshot(state=state):
        return json.loads(control("read-state", valid, state))

    with target("target-denies", denied) as listener:
        legacy.require(exchange(legacy, legacy.AGENT, (legacy.DISPATCHERS,), "wrong-controller", state, denied) == "",
                       "Wrong Controller was not denied")
        legacy.require(finished(legacy, listener) == "CONTROLLER_UID_DENIED",
                       "Target did not report connected UID rejection")
    legacy.require(not os.path.lexists(denied), "Denied target left a socket")
    print("PASS 4.5 authenticated target rejected wrong Controller UID before reading", flush=True)

    legacy.require(control("provision", wrong, wrong_state) == "", "Unexpected dispatch fixture provisioning output")
    check_wrong_target(legacy, root, wrong_state, wrong)
    rejected = snapshot(wrong_state)
    legacy.require(
        not rejected["reservations"] and not rejected["outcomes"]
        and len(rejected["approvals"]) == 1 and rejected["approvals"][0][1] == 0,
        "Wrong target consumed approval or created a durable reservation",
    )
    print("PASS 4.5 wrong target received zero dispatch bytes and created no reservation", flush=True)

    legacy.require(control("provision", valid) == "", "Unexpected dispatch fixture provisioning output")
    with target("target", valid) as listener:
        result = control("controller", valid)
        observed = json.loads(finished(legacy, listener))
        legacy.require(result == observed["outcome"] == "VALIDATED_NO_EFFECT", "No-effect acknowledgment differed")
    durable = snapshot()
    legacy.require(len(durable["reservations"]) == len(durable["approvals"]) == 1,
                   "Durable fixture did not retain exactly one approval and reservation")
    dispatch, action, approval = durable["reservations"][0]
    legacy.require((dispatch, action) == (observed["dispatch_id"], observed["action_id"])
                   and durable["approvals"] == [[approval, 1]]
                   and [dispatch, "RESERVED"] in durable["evidence"]
                   and [dispatch, "VALIDATED_NO_EFFECT"] in durable["evidence"]
                   and durable["outcomes"] == [[dispatch, "VALIDATED_NO_EFFECT"]],
                   "Durable approval consumption, reservation or outcome evidence differs")
    print("PASS 4.5 durable fixture reservation and correlated no-effect acknowledgement", flush=True)

    with target("observe-no-request", repeat) as listener:
        legacy.require(control("controller", repeat) == "FIXTURE_ALREADY_USED",
                       "Committed reservation was eligible again")
        legacy.require(finished(legacy, listener) == "NO_SECOND_REQUEST", "Repeated Controller sent a request")
    after = snapshot()
    legacy.require(after["reservations"] == durable["reservations"] and after["approvals"] == durable["approvals"]
                   and after["outcomes"] == durable["outcomes"]
                   and not any(os.path.lexists(path) for path in (valid, repeat)),
                   "Second invocation changed durable authority or left a live socket")
    print("PASS 4.5 consumed fixture approval cannot authorize a second application send", flush=True)
