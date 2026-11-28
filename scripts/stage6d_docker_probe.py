"""Check the Agent confinement before exercising the existing protocol client."""

import os
from pathlib import Path
import socket
import sys


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def mount_is_read_only(path):
    return bool(os.statvfs(path).f_flag & os.ST_RDONLY)


def main():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise RuntimeError("Expected isolated container probe")
    uid, proposal_gid, pid_namespace, endpoint, mode, *args = sys.argv[1:]
    identity = int(uid)
    path = Path(endpoint)
    stage = Path(__file__).resolve().parents[1]
    require(os.getresuid() == os.getresgid() == (identity,) * 3
            and set(os.getgroups()) - {identity} == {int(proposal_gid)}, "Unexpected Agent identity")
    for field in ("uid", "gid"):
        mapping = Path(f"/proc/self/{field}_map").read_text().split()
        require(mapping == ["0", "0", "4294967295"], "Unsupported user namespace mapping")
    require(os.readlink("/proc/self/ns/pid") == pid_namespace
            and Path("/proc/1/status").read_text().split("Uid:")[1].split()[0] == "60108",
            "Expected private namespace and distinct nonroot holder")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    require(all(int(status[key], 16) == 0 for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))
            and status["NoNewPrivs"].strip() == "1" and status["Seccomp"].strip() == "2",
            "Expected zero capabilities, no-new-privileges and seccomp")
    require({name for _, name in socket.if_nameindex()} == {"lo"}, "Unexpected network interface")
    require(all(mount_is_read_only(item) for item in (Path("/"), stage, path.parent))
            and not os.path.lexists("/run/docker.sock"), "Unexpected mount or daemon socket")

    if mode == "proposal":
        operator, controller, target_state, target, inspector, audit, resource = args
        for directory in (controller, target_state, audit):
            try:
                with os.scandir(directory):
                    raise RuntimeError("Agent can open a protected store directory")
            except (FileNotFoundError, PermissionError):
                pass
        for endpoint in (operator, target, inspector):
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(1)
                try:
                    connection.connect(endpoint)
                except (FileNotFoundError, PermissionError):
                    pass
                else:
                    raise RuntimeError("Agent reached a protected native socket")
        controller_seen = False
        for proc in Path("/proc").iterdir():
            if proc.name.isdecimal() and proc != Path(f"/proc/{os.getpid()}"):
                try:
                    peer_uid = proc.stat().st_uid
                except FileNotFoundError:
                    continue
                controller_seen |= peer_uid == 60102
                if peer_uid in {0, 60102, 60103, 60104, 60105, 60106, 60108}:
                    require(not os.access(proc / "root", os.R_OK), "Agent can inspect a protected process root")
        require(controller_seen, "Agent cannot see the native Controller in the shared PID namespace")
        script = stage / "scripts/stage47_native_probe.py"
        os.execv(sys.executable, (sys.executable, "-I", "-B", "-S", str(script), "proposal",
                                 controller, str(path), operator, target, resource))

    script = stage / "scripts/stage42_native_probe.py"
    os.execv(sys.executable, (sys.executable, "-I", "-B", "-S", str(script), mode, endpoint, *args))


if __name__ == "__main__":
    main()
