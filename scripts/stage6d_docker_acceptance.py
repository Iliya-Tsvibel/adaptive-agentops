"""Drive the only containerized application principal from the trusted VM fixture."""

import json
import os
import re
import subprocess

from scripts import stage42_native_acceptance as admission


IMAGE = "python:3.12.16-bookworm@sha256:4209fae09a3e20afd863e528bfddc235da5fd5977983e1996cd54cc9a72e6267"
DOCKER = ("/usr/bin/docker", "--host", "unix:///var/run/docker.sock")
WRONG_UID = 60109
PROBE = admission.STAGE / "scripts/stage6d_docker_probe.py"


def agent(legacy, path, mode, *arguments, uid=None):
    if uid is None:
        uid = legacy.AGENT
    holder = os.environ.get("STAGE6D_HOLDER", "")
    legacy.require(re.fullmatch(r"[a-f0-9]{64}", holder), "Missing exact Docker namespace holder ID")
    namespace = os.readlink("/proc/self/ns/pid")
    result = subprocess.run(
        (*DOCKER, "run", "--rm", "--pull=never", "--pid", f"container:{holder}",
         "--network=none", "--ipc=none", "--read-only", "--cap-drop=ALL",
         "--security-opt=no-new-privileges", "--pids-limit=16",
         "--user", f"{uid}:{uid}", "--group-add", str(legacy.PROPOSERS),
         "--mount", f"type=bind,source={path.parent},target={path.parent},readonly,bind-recursive=disabled",
         "--mount", f"type=bind,source={admission.STAGE},target={admission.STAGE},readonly,bind-recursive=disabled",
         IMAGE, "/usr/local/bin/python", "-I", "-B", "-S", str(PROBE),
         str(uid), str(legacy.PROPOSERS), namespace, str(path), mode, *map(str, arguments)),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=12,
    )
    legacy.require(result.returncode == 0 and not result.stderr,
                   f"Container Agent {mode} failed (exit={result.returncode}): {result.stderr[:600]}")
    return result.stdout.strip()


def check_peers(legacy, path):
    with admission.server(legacy, path, "missing") as process:
        legacy.require(agent(legacy, path, "wrong-agent", admission.FRAME.hex(), uid=WRONG_UID) == "",
                       "Unexpected wrong-Agent output")
        admission.check_events(admission.finish(process), [admission.REJECTED_PEER], legacy)
    print("PASS 6.D reachable wrong Agent UID received no verdict", flush=True)

    def check_server(_legacy, endpoint, expected):
        legacy.require(agent(legacy, endpoint, "client") == expected, "Unexpected wrong-server result")

    admission.check_wrong_server(legacy, path, invoke=check_server)
    print("PASS 6.D container Agent sent zero bytes to a wrong server with matching path metadata", flush=True)


def proposal(legacy, agent_path, operator_path, controller_state, target_state, target_path, resource, *, audit,
             inspector):
    response = agent(legacy, agent_path, "proposal", operator_path, controller_state, target_state, target_path,
                     inspector, audit, resource)
    result = json.loads(response)
    legacy.require(type(result) is bool, "Malformed proposal observation")
    return result
