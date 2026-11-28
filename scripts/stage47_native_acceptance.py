"""Verify the live operator-to-target chain under distinct Linux UIDs."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from time import sleep

from scripts import stage43_native_acceptance as managed
from scripts import stage46_native_acceptance as effects
from scripts.stage43_native_probe import APPROVER, OPERATOR, OPERATORS


_PROBE = Path(__file__).absolute().with_name("stage47_native_probe.py")


@contextmanager
def prepare(legacy):
    with managed.prepare(legacy) as (root, agent, operator), effects.prepare(legacy) as (states, sockets):
        control = root / "controller-state"
        control.mkdir(mode=0o700)
        os.chown(control, legacy.CONTROL, legacy.CONTROL)
        yield agent, operator, control, states["target"], sockets["apply"]


def run(legacy, agent, operator, control, target_state, target, *, container_proposal=None):
    print("START stage47 native hardened local acceptance", flush=True)

    def command(mode, *args):
        return (sys.executable, "-I", "-B", "-S", str(_PROBE), mode,
                str(control), str(agent), str(operator), str(target), *args)

    def invoke(uid, groups, mode, *args):
        result = subprocess.run(command(mode, *args), **legacy.identity(uid, groups),
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
        legacy.require(result.returncode == 0 and not result.stderr,
                       f"Stage 4.7 {mode} failed (exit={result.returncode}): {result.stderr[:750]}")
        return json.loads(result.stdout) if result.stdout else None

    def action(method, pending=None, *, uid=APPROVER):
        args = (method,) if pending is None else (method, json.dumps(pending))
        return invoke(uid, (OPERATORS,), "action", *args)

    def grant(method, argument, *, uid=OPERATOR):
        return invoke(uid, (OPERATORS,), "grant", method, argument)

    def proposal(resource="demo-api"):
        return invoke(legacy.AGENT, None, "proposal", resource)

    def portable_proposal(resource="demo-api"):
        if container_proposal is None:
            return proposal(resource)
        return container_proposal(legacy, agent, operator, control, target_state, target, resource)

    def evidence():
        return invoke(legacy.CONTROL, (), "evidence")

    @contextmanager
    def server():
        with legacy.listening_process(command("server"), legacy.CONTROL, agent,
                                      groups=(legacy.PROPOSERS, OPERATORS, legacy.DISPATCHERS),
                                      additional_paths=(operator,)) as listener:
            yield listener
            output, _ = listener.communicate(timeout=13)
            legacy.require(listener.returncode == 0 and not output and not agent.exists() and not operator.exists(),
                           f"Stage 4.7 Controller failed to close cleanly: {output[:750]}")

    invoke(legacy.CONTROL, (), "provision")
    effects.invoke(legacy, legacy.TARGET, "provision-target", target_state, target)

    with server():
        legacy.require(proposal() is False and action("INSPECT_ACTION") is None, "Missing grant yielded authority")
        legacy.require(grant("ISSUE", "60", uid=APPROVER) is None, "Approver was allowed to issue a grant")
        issued = grant("ISSUE", "60")
        legacy.require(issued[0] == "ACTIVE" and portable_proposal("outside-scope") is False
                       and portable_proposal() is True,
                       "Protected proposal scope was not enforced")
        pending = action("INSPECT_ACTION")
        legacy.require(pending["tool"] == "mock.restart_service" and pending["resource"] == "demo-api",
                       "Approver did not see exact pending scope")
        legacy.require(action("APPROVE", pending, uid=OPERATOR) is None
                       and action("APPROVE", pending, uid=legacy.AGENT) == "AGENT_REJECTED"
                       and action("DISPATCH", pending) is None,
                       "Approval boundary or mandatory approval failed")
        legacy.require(action("APPROVE", pending) == "APPROVED", "Mapped approver decision failed")

        with effects.serve(legacy, "target", target_state, target) as listener:
            legacy.require(action("DISPATCH", pending) == "SIMULATED_EFFECT_COMMITTED", "Dispatch did not confirm")
            receipt = json.loads(effects.finish(legacy, listener))
        legacy.require(action("APPROVE", pending) is None and action("DISPATCH", pending) is None
                       and proposal() is False, "Approval or proposal reuse was accepted")

    success = evidence()
    (session, run_id, grant_id, action_id, fingerprint, approval, decision, consumed, _) = success["actions"][0]
    (ds, dg, dispatch_id, da, dp, df, digest, number, status) = success["dispatches"][0]
    legacy.require(len(success["actions"]) == len(success["dispatches"]) == 1 and issued[1:] == [grant_id, session]
                   and (ds, dg, da, dp, df) == (session, grant_id, action_id, approval, fingerprint)
                   and (action_id, run_id, decision, consumed) == (pending["action_id"], pending["run_id"], "APPROVED", 1),
                   "Grant, proposal, approval and reservation did not correlate")
    legacy.require([session, grant_id, legacy.AGENT, None, "PENDING_ACTION", "PROPOSAL", "APPROVAL_REQUIRED"]
                   in success["events"] and [session, grant_id, APPROVER, "APPROVE", "APPROVED", None, None]
                   in success["events"] and any(event[4] == "PEER_REJECTED" for event in success["events"]),
                   "Kernel-bound proposal and approval evidence missing")
    legacy.require(status == "SIMULATED_EFFECT_COMMITTED" and number == 1 and receipt == {
        "dispatch_id": dispatch_id, "action_id": action_id, "request_digest": digest, "effect_number": 1,
    }, "Controller reservation and target receipt differ")
    print("PASS 4.7 kernel-bound agent proposal and explicit approver decision", flush=True)
    print("PASS 4.7 atomic approval consumption and correlated SQLite-only effect", flush=True)

    with server():
        legacy.require(action("DISPATCH", pending) is None, "Controller restart restored old authority")
        issued = grant("ISSUE", "60")
        legacy.require(issued[0] == "ACTIVE" and portable_proposal() is True, "Revocation setup failed")
        if container_proposal is not None:
            legacy.require(action("DISPATCH", pending) is None, "Replacement Agent restored old authority")
        revoked = action("INSPECT_ACTION")
        legacy.require(action("APPROVE", revoked) == "APPROVED"
                       and grant("REVOKE", issued[1])[0] == "REVOKED" and action("DISPATCH", revoked) is None,
                       "Revoked grant still authorized an approved action")
    with server():
        legacy.require(grant("ISSUE", "2")[0] == "ACTIVE" and proposal() is True, "Expiry setup failed")
        expiring = action("INSPECT_ACTION")
        sleep(2)
        legacy.require(action("APPROVE", expiring) is None and action("DISPATCH", expiring) is None,
                       "Expired grant conferred action authority")
    denied = evidence()
    legacy.require(denied["dispatches"] == success["dispatches"] and [row[-1] for row in denied["actions"]]
                   == ["INVALIDATED", "REVOKED", "EXPIRED"], "Denied authority altered dispatch or lifecycle evidence")
    print("PASS 4.7 missing, forged, reused, revoked, expired and prior-session authority denied", flush=True)

    with server():
        legacy.require(grant("ISSUE", "60")[0] == "ACTIVE" and proposal() is True, "Crash setup failed")
        interrupted = action("INSPECT_ACTION")
        legacy.require(action("APPROVE", interrupted) == "APPROVED", "Crash approval failed")
        with effects.serve(legacy, "interrupted", target_state, target) as listener:
            legacy.require(action("DISPATCH", interrupted) == "UNKNOWN", "After-claim exit did not leave uncertainty")
            effects.finish(legacy, listener, status=86)
        legacy.require(action("DISPATCH", interrupted) is None, "Ambiguity allowed re-dispatch")

    final = evidence()
    claims = json.loads(effects.invoke(legacy, legacy.TARGET, "target-evidence", target_state, target))
    rows = final["dispatches"]
    expected = sorted([[row[2], row[3], row[6], row[7]] for row in rows])
    legacy.require(len(rows) == 2 and rows[1][7:] == [None, "UNKNOWN"] and final["actions"][-1][7] == 1
                   and claims == {"count": 1, "claims": expected},
                   "Interrupted target claim was lost or gained another effect")
    print("PASS 4.7 interrupted target claim retained unknown evidence without retry", flush=True)
