"""Verify SQLite-only durable target behavior under distinct service UIDs."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


_PROBE = Path(__file__).absolute().with_name("stage46_native_probe.py")


@contextmanager
def prepare(legacy):
    with TemporaryDirectory(prefix="aao-stage46-", dir="/run") as directory:
        root = Path(directory)
        root.chmod(0o755)
        states = {}
        for name, uid in (("effect", legacy.CONTROL), ("interrupted", legacy.CONTROL), ("target", legacy.TARGET)):
            states[name] = root / name
            states[name].mkdir(mode=0o700)
            os.chown(states[name], uid, uid)
        sockets = {}
        for name in ("apply", "replay", "lookup", "interrupt", "retained"):
            parent = root / name
            parent.mkdir(mode=0o750)
            os.chown(parent, legacy.TARGET, legacy.DISPATCHERS)
            sockets[name] = parent / "effect.sock"
        yield states, sockets


def command(mode, state, path, *args):
    return (sys.executable, "-I", "-B", "-S", str(_PROBE), mode, str(state), str(path), *args)


def invoke(legacy, uid, mode, state, path, *args):
    result = subprocess.run(
        command(mode, state, path, *args), **legacy.identity(uid),
        capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=5,
    )
    legacy.require(result.returncode == 0 and not result.stderr,
                   f"Stage 4.6 {mode} failed (exit={result.returncode}): {result.stderr[:500]}")
    return result.stdout.strip()


def serve(legacy, mode, state, path):
    return legacy.listening_process(command(mode, state, path), legacy.TARGET, path, groups=(legacy.DISPATCHERS,))


def finish(legacy, child, *, status=0):
    output, _ = child.communicate(timeout=5)
    legacy.require(child.returncode == status,
                   f"Stage 4.6 target listener failed (exit={child.returncode}): {output[:500]}")
    return output.strip()


def correlation(row):
    return dict(zip(("dispatch_id", "action_id", "request_digest"), row[:3], strict=True))


def run(legacy, states, sockets):
    print("START stage46 native durable simulated effects", flush=True)

    def control(mode, state, socket, *args):
        return invoke(legacy, legacy.CONTROL, mode, states[state], sockets[socket], *args)

    def target(mode, socket):
        return serve(legacy, mode, states["target"], sockets[socket])

    def target_evidence():
        return json.loads(invoke(legacy, legacy.TARGET, "target-evidence", states["target"], sockets["apply"]))

    for state in ("effect", "interrupted"):
        control("provision-controller", state, "apply")
    invoke(legacy, legacy.TARGET, "provision-target", states["target"], sockets["apply"])

    with target("target", "apply") as listener:
        applied = json.loads(control("dispatch", "effect", "apply"))
        receipt = json.loads(finish(legacy, listener))
    with target("duplicate", "replay") as listener:
        control("replay", "effect", "replay", applied["frame"])
        legacy.require(finish(legacy, listener) == "REPLAY_DENIED", "Stage 4.6 replay was accepted")
    confirmed = json.loads(control("controller-evidence", "effect", "apply"))
    legacy.require(len(confirmed) == 1 and confirmed[0][3:] == [1, "SIMULATED_EFFECT_COMMITTED"]
                   and applied["outcome"] == confirmed[0][4]
                   and receipt == {**correlation(confirmed[0]), "effect_number": 1},
                   "Stage 4.6 Controller and target effect evidence differ")
    print("PASS 4.6 one correlated durable SQLite simulated effect", flush=True)

    with target("target", "lookup") as listener:
        recovered = json.loads(control("lookup", "effect", "lookup", json.dumps(correlation(confirmed[0]))))
        legacy.require(json.loads(finish(legacy, listener)) == recovered,
                       "Stage 4.6 lookup client and target evidence differ")
    legacy.require(recovered == {**receipt, "status": "COMMITTED"}
                   and target_evidence() == {"count": 1, "claims": [confirmed[0][:4]]},
                   "Stage 4.6 replay or lookup changed the durable effect")
    print("PASS 4.6 restarted target denied replay and authenticated lookup retained the receipt", flush=True)

    with target("interrupted", "interrupt") as listener:
        lost = json.loads(control("dispatch", "interrupted", "interrupt"))
        finish(legacy, listener, status=86)
    with target("duplicate", "retained") as listener:
        control("replay", "interrupted", "retained", lost["frame"])
        legacy.require(finish(legacy, listener) == "REPLAY_DENIED", "Interrupted claim allowed replay")
    unresolved = json.loads(control("controller-evidence", "interrupted", "interrupt"))
    expected_claims = sorted([confirmed[0][:4], unresolved[0][:4]])
    legacy.require(len(unresolved) == 1 and unresolved[0][3:] == [None, "UNKNOWN"]
                   and lost["outcome"] == "UNKNOWN"
                   and target_evidence() == {"count": 1, "claims": expected_claims},
                   "Stage 4.6 interrupted claim did not remain unresolved without another effect")
    print("PASS 4.6 abrupt exit after claim retained unknown evidence and no second effect", flush=True)
