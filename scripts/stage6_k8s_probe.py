"""Real application exchanges launched as distinct Kubernetes containers."""

from contextlib import contextmanager
import json
import subprocess
import time

from stage6_k8s_lab import NAME, NODE, NS, k, run, wait_for


PROPOSAL = "/lab/proposal/proposal.sock"
OPERATOR = "/lab/operator/operator.sock"
TARGET = "/lab/target/target.sock"
INSPECTOR = "/lab/inspection/inspect.sock"
CONTROLLER_STATE = "/lab/controller-state"
TARGET_STATE = "/lab/target-state"
AUDIT = "/lab/audit-state"
FLAGS = ("python", "-I", "-B", "-S")
PATH = "/opt/aao/scripts/"
ROLE_IDS = {
    "agent": (60101, [60111]), "controller": (60102, [60111, 60112, 60113, 60116]),
    "target": (60103, [60112]), "requester": (60104, [60113]),
    "approver": (60105, [60113]), "inspector": (60106, [60116]),
    "wrong-peer": (60109, [60111]),
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Pod:
    def __init__(self):
        k("-n", NS, "rollout", "status", f"deployment/{NAME}", "--timeout=120s")
        pods = json.loads(k("-n", NS, "get", "pods", "-l", f"app={NAME}", "-o", "json").stdout)["items"]
        active, = [pod for pod in pods if not pod["metadata"].get("deletionTimestamp")]
        self.name = active["metadata"]["name"]
        self.uid = active["metadata"]["uid"]

    def command(self, role, *args):
        return ("kubectl", "-n", NS, "exec", self.name, "-c", role, "--", *args)

    def exec(self, role, *args, **kwargs):
        return run(*self.command(role, *args), **kwargs)

    def probe(self, role, stage, *args, **kwargs):
        return self.exec(role, *FLAGS, f"{PATH}{stage}", *args, **kwargs).stdout.strip()

    def controller(self, role, mode, *args):
        return self.probe(role, "stage47_native_probe.py", mode, CONTROLLER_STATE, PROPOSAL, OPERATOR, TARGET, *args)

    def target(self, mode):
        return json.loads(self.probe("target", "stage46_native_probe.py", mode, TARGET_STATE, TARGET))

    def inspection(self, role, mode, *args):
        return self.probe(role, "stage6e_native_probe.py", mode, CONTROLLER_STATE, TARGET_STATE, AUDIT,
                          PROPOSAL, OPERATOR, TARGET, INSPECTOR, *args)

    def action(self, role, mode, pending=None):
        args = (mode,) if pending is None else (mode, json.dumps(pending))
        return json.loads(self.controller(role, "action", *args))

    @contextmanager
    def listen(self, role, stage, *args, ready, expected=0):
        command = self.command(role, *FLAGS, f"{PATH}{stage}", *args)
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"Listener exited early: {process.communicate()}")
                    if self.exec(role, "/usr/bin/test", "-S", ready, check=False).returncode == 0:
                        break
                else:
                    raise RuntimeError(f"Listener not ready at {ready}")
                yield
                stdout, stderr = process.communicate(timeout=16)
                require(process.returncode == expected, f"{role} listener failed: {stdout} {stderr}")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

    def inspect_confinement(self):
        identity_code = """
import json, os
from pathlib import Path
state = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
print(json.dumps([os.getuid(), os.getgid(), sorted(set(os.getgroups()) - {os.getgid()}),
                  os.readlink('/proc/self/ns/pid'), all(int(state[key], 16) == 0
                  for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')),
                  state['NoNewPrivs'].strip(), state['Seccomp'].strip(),
                  bool(os.statvfs('/').f_flag & os.ST_RDONLY),
                  os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount/token'),
                  os.path.lexists('/var/run/docker.sock')]))
"""
        namespaces = set()
        for role, (uid, gids) in ROLE_IDS.items():
            observed = json.loads(self.exec(role, *FLAGS, "-c", identity_code).stdout)
            require(observed[:3] == [uid, uid, gids], f"Wrong {role} kernel identity: {observed[:3]}")
            namespaces.add(observed[3])
            require(observed[4:] == [True, "1", "2", True, False, False], f"Wrong {role} confinement")
        require(len(namespaces) == 1, "Role PID namespaces differ")
        self.exec("agent", *FLAGS, "-c", """
import os
from pathlib import Path
for path in ('operator', 'target', 'inspection', 'controller-state', 'target-state', 'audit-state'):
    assert not Path('/lab', path).exists()
assert os.statvfs('/lab/proposal').f_flag & os.ST_RDONLY
controllers = [p for p in Path('/proc').iterdir() if p.name.isdigit() and p.stat().st_uid == 60102]
assert controllers, 'Controller not visible'
for controller in controllers:
    try:
        os.kill(int(controller.name), 0)
    except PermissionError:
        pass
    else:
        raise RuntimeError('Agent may signal Controller')
    try:
        fd = os.open(controller / 'root', os.O_RDONLY)
    except PermissionError:
        pass
    else:
        os.close(fd)
        raise RuntimeError('Agent accessed Controller process root')
""")
        print("PASS Kubernetes actual role UID/GID/PID, confinement and procfs denial", flush=True)

    def reject_wrong_server(self):
        wrong = "/lab/target/wrong.sock"
        with self.listen("target", "stage42_native_probe.py", "wrong-server", wrong, ready=wrong):
            target = "/var/lib/aao-lab/target/wrong.sock"
            proposal = "/var/lib/aao-lab/proposal/proposal.sock"
            run("docker", "exec", NODE, "chown", "60102:60111", target)
            run("docker", "exec", NODE, "chmod", "0660", target)
            run("docker", "exec", NODE, "mv", "-T", target, proposal)
            try:
                result = self.probe("agent", "stage42_native_probe.py", "client", PROPOSAL)
                require(result == "WRONG_SERVER", "Wrong Controller accepted")
            finally:
                run("docker", "exec", NODE, "rm", proposal)
        print("PASS Kubernetes wrong-server UID with valid socket metadata received zero bytes", flush=True)

    def dispatch(self, uncertain=False):
        with self.listen("controller", "stage47_native_probe.py", "server", CONTROLLER_STATE,
                         PROPOSAL, OPERATOR, TARGET, ready=PROPOSAL):
            if not uncertain:
                frame = b'AAO\x02\x01{"tool":"mock.get_health","resource":"demo-api","arguments":{}}'
                self.probe("wrong-peer", "stage42_native_probe.py", "wrong-agent", PROPOSAL, frame.hex())
                require(self.controller("agent", "proposal", "demo-api") == "false", "Grant bypass")
            require(json.loads(self.controller("requester", "grant", "ISSUE", "60"))[0] == "ACTIVE",
                    "Requester grant failed")
            require(self.controller("agent", "proposal", "demo-api") == "true", "Real proposal was denied")
            pending = self.action("approver", "INSPECT_ACTION")
            require(self.action("requester", "APPROVE", pending) is None, "Requester approved own action")
            require(self.action("approver", "APPROVE", pending) == "APPROVED", "Approval failed")
            if uncertain:
                command = ("stage6e_native_probe.py", "target-claim-only", CONTROLLER_STATE, TARGET_STATE,
                           AUDIT, PROPOSAL, OPERATOR, TARGET, INSPECTOR)
            else:
                command = ("stage46_native_probe.py", "target", TARGET_STATE, TARGET)
            with self.listen("target", *command, ready=TARGET, expected=86 if uncertain else 0):
                expected = "UNKNOWN" if uncertain else "SIMULATED_EFFECT_COMMITTED"
                require(self.action("approver", "DISPATCH", pending) == expected, "Unexpected dispatch outcome")
            if uncertain:
                self.exec("target", "/usr/bin/test", "-S", TARGET)
                self.exec("target", "/usr/bin/rm", TARGET)
            require(self.action("approver", "DISPATCH", pending) is None, "Repeat gained authority")
        return pending

    def evidence(self):
        return json.loads(self.controller("controller", "evidence")), self.target("target-evidence")


def exercise(snapshot_only=False):
    pod = Pod()
    if snapshot_only:
        execution, target = pod.evidence()
        return pod.uid, execution, target, pod.inspection("controller", "audit-count")
    pod.inspect_confinement()
    pod.reject_wrong_server()
    pod.controller("controller", "provision")
    pod.probe("target", "stage46_native_probe.py", "provision-target", TARGET_STATE, TARGET)
    pod.inspection("controller", "provision-audit")
    positive = pod.dispatch()
    print("PASS Kubernetes real scoped proposal, positive dispatch and spent approval", flush=True)
    uncertain = pod.dispatch(uncertain=True)
    execution, target = pod.evidence()
    require(target["count"] == 1 and len(target["claims"]) == 2, "Replay/effect evidence differs")
    require(execution["dispatches"][-1][-1] == "UNKNOWN", "Historical UNKNOWN lost")
    dispatch_id = execution["dispatches"][-1][2]
    uid = pod.uid
    k("-n", NS, "delete", "pod", pod.name, "--wait", "--timeout=90s")
    def replacement_ready():
        rows = json.loads(k("-n", NS, "get", "pods", "-l", f"app={NAME}", "-o", "json").stdout)["items"]
        return any(row["metadata"]["uid"] != uid and any(
            condition["type"] == "Ready" and condition["status"] == "True"
            for condition in row.get("status", {}).get("conditions", [])) for row in rows)

    wait_for("replacement Pod", replacement_ready)
    fresh = Pod()
    require(fresh.uid != uid and fresh.evidence() == (execution, target), "Replacement lost protected state")
    with fresh.listen("controller", "stage47_native_probe.py", "server", CONTROLLER_STATE,
                      PROPOSAL, OPERATOR, TARGET, ready=PROPOSAL):
        require(fresh.action("approver", "DISPATCH", positive) is None
                and fresh.action("approver", "DISPATCH", uncertain) is None,
                "Replacement Controller restored previously spent authority")
    replacement_execution, replacement_target = fresh.evidence()
    require(replacement_target == target and replacement_execution["dispatches"] == execution["dispatches"],
            "Replacement dispatch check changed prior durable evidence")
    execution = replacement_execution
    print("PASS Kubernetes Pod replacement preserved native replay claims and historical UNKNOWN", flush=True)

    with fresh.listen("target", "stage6e_native_probe.py", "target-lookup", CONTROLLER_STATE, TARGET_STATE,
                      AUDIT, PROPOSAL, OPERATOR, TARGET, INSPECTOR, ready=TARGET):
        with fresh.listen("controller", "stage6e_native_probe.py", "serve", CONTROLLER_STATE, TARGET_STATE,
                          AUDIT, PROPOSAL, OPERATOR, TARGET, INSPECTOR, dispatch_id, ready=INSPECTOR):
            result = json.loads(fresh.inspection("inspector", "inspect", dispatch_id))
            require(result["controller"] == "UNKNOWN" and result["result"] == "UNRESOLVED",
                    "Inspection attributed an ambiguous effect")
    with fresh.listen("controller", "stage6e_native_probe.py", "serve", CONTROLLER_STATE, TARGET_STATE,
                      AUDIT, PROPOSAL, OPERATOR, TARGET, INSPECTOR, dispatch_id, ready=INSPECTOR):
        require(fresh.inspection("inspector", "raw", "41414f070304") == "DENIED",
                "Inspector obtained execution authority")
    require(fresh.evidence() == (execution, target) and fresh.inspection("controller", "audit-count") == "1",
            "Inspection audit or immutable execution evidence differs")
    print("PASS Kubernetes read-only inspection required durable disclosure audit", flush=True)
    return fresh.uid, execution, target, "1"


def verify_missing_state():
    pod = Pod()
    database = "/var/lib/aao-lab/target-state/target.sqlite3"
    run("docker", "exec", NODE, "mv", "-T", database, "/var/lib/aao-lab/target-unavailable.sqlite3")
    result = pod.exec("target", *FLAGS, f"{PATH}stage46_native_probe.py", "target", TARGET_STATE, TARGET,
                      check=False, timeout=15)
    require(result.returncode != 0 and "database is missing" in result.stderr,
            "Target failed for an unexpected reason after state loss")
    pod.exec("target", "/usr/bin/test", "!", "-e", f"{TARGET_STATE}/target.sqlite3")
    pod.exec("target", "/usr/bin/test", "!", "-e", TARGET)
    print("PASS Kubernetes absent Target state blocked startup without provisioning or a socket", flush=True)
