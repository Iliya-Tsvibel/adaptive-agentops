"""Run from the approved disposable native-Linux acceptance workflow."""

from contextlib import contextmanager
import grp
import os
from pathlib import Path
import pwd
import resource
import socket
import stat
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep


AGENT, CONTROL, TARGET = 60101, 60102, 60103
PROPOSERS, DISPATCHERS = 60111, 60112
GROUPS = {AGENT: (PROPOSERS,), CONTROL: (PROPOSERS, DISPATCHERS), TARGET: (DISPATCHERS,)}
PYTHON = (sys.executable, "-B", "-S")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def identity(uid, groups=None):
    return {
        "user": uid, "group": uid,
        "extra_groups": GROUPS[uid] if groups is None else groups,
        "env": {},
    }


def probe(mode, link, path, gid):
    return (*PYTHON, "-m", "scripts.stage4_peer_probe", mode, "--link", link,
            "--socket", str(path), "--access-gid", str(gid),
            "--agent-uid", str(AGENT), "--control-uid", str(CONTROL), "--target-uid", str(TARGET))


def check_probe(returncode, output, error=None):
    expected = "Stage 4.1 handshake verified" if error is None else f"Stage 4.1 handshake failed ({error})"
    require(returncode == (0 if error is None else 1) and output.strip() == expected,
            f"Expected {expected!r}, got exit {returncode}: {output.strip()}")


def run_probe(uid, args, error=None):
    result = subprocess.run(args, **identity(uid), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=5)
    check_probe(result.returncode, result.stdout, error)


@contextmanager
def listener(uid, gid, link, path, error=None):
    with subprocess.Popen(probe("listen", link, path, gid), **identity(uid),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        try:
            # Wait for a listening kernel socket without consuming its only connection.
            deadline = monotonic() + 5
            while monotonic() < deadline:
                rows = (line.split() for line in Path("/proc/net/unix").read_text().splitlines()[1:])
                if any(len(row) == 8 and int(row[3], 16) & 0x10000
                       and row[7].endswith(f"/{path.name}") for row in rows):
                    break
                if process.poll() is not None:
                    raise RuntimeError(f"{link} listener exited before readiness: {process.communicate()[0]}")
                sleep(0.02)
            else:
                raise TimeoutError(f"{link} listener did not become ready")

            info = path.lstat()
            require((info.st_uid, info.st_gid, info.st_mode) == (uid, gid, stat.S_IFSOCK | 0o660),
                    f"{link} socket ownership or mode differs")
            yield
            output, _ = process.communicate(timeout=5)
            check_probe(process.returncode, output, error)
            require(not os.path.lexists(path), f"{link} socket was not cleaned up")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def agent_check(code, *args, groups=None):
    subprocess.run((*PYTHON, "-c", code, *map(str, args)), **identity(AGENT, groups),
                   check=True, timeout=5)


def expect_unix_access_denied(path):
    # Exercise the kernel directly so the probe's local-role check cannot mask denial.
    agent_check("""
import errno
import socket
import sys

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
    sock.settimeout(2)
    actual = sock.connect_ex(sys.argv[1])
    if actual != errno.EACCES:
        sys.exit(f"Expected filesystem denial, got errno {actual}")
""", path)


def expect_no_reply(path, groups=None):
    # A successful connect proves filesystem access. EOF alone is an invalid frame.
    agent_check("""
import socket
import sys

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
    sock.settimeout(2)
    sock.connect(sys.argv[1])
    sock.shutdown(socket.SHUT_WR)
    if sock.recv(1) != b"":
        sys.exit("Rejected peer received protocol data")
""", path, groups=groups)


def check_paths(path):
    directory = path.parent
    args = probe("listen", "agent-control", path, PROPOSERS)
    os.chown(directory, 0, PROPOSERS)
    run_probe(CONTROL, args, "PathSecurityError")
    require(not os.path.lexists(path), "Wrong-owner directory acquired a socket")
    os.chown(directory, CONTROL, PROPOSERS)

    directory.chmod(0o770)
    run_probe(CONTROL, args, "PathSecurityError")
    require(not os.path.lexists(path), "Unsafe-mode directory acquired a socket")
    directory.chmod(0o750)

    # Linux POSIX ACL xattr v2. Add a named entry without changing the mode bits.
    acl = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, perm, qualifier)
        for tag, perm, qualifier in (
            (1, 7, 0xFFFFFFFF), (2, 5, TARGET), (4, 5, 0xFFFFFFFF),
            (16, 5, 0xFFFFFFFF), (32, 0, 0xFFFFFFFF),
        )
    )
    for attribute in ("system.posix_acl_access", "system.posix_acl_default"):
        os.setxattr(directory, attribute, acl)
        require(os.getxattr(directory, attribute) == acl and stat.S_IMODE(directory.stat().st_mode) == 0o750,
                f"Expected {attribute} with unchanged directory mode")
        run_probe(CONTROL, args, "PathSecurityError")
        require(not os.path.lexists(path), f"Directory with {attribute} acquired a socket")
        os.removexattr(directory, attribute)

    path.write_bytes(b"existing entry")
    run_probe(CONTROL, args, "OSError")
    require(path.read_bytes() == b"existing entry", "Listener altered an existing entry")
    path.unlink()
    print("PASS ownership, mode, POSIX ACL and existing-entry denial", flush=True)


def main():
    require(sys.platform == "linux" and not any(word in os.uname().release.lower() for word in ("wsl", "microsoft")),
            "Native Linux is required")
    require(os.geteuid() == 0 and os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net")
            and {name for _, name in socket.if_nameindex()} == {"lo"},
            "Root orchestration in a fresh loopback-only network namespace is required")
    require(not set(GROUPS) & {entry.pw_uid for entry in pwd.getpwall()}
            and not ({*GROUPS, PROPOSERS, DISPATCHERS} & {entry.gr_gid for entry in grp.getgrall()}),
            "Test UID or GID is already assigned")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    with TemporaryDirectory(prefix="aao-native-", dir="/run") as directory:
        root = Path(directory)
        root.chmod(0o755)
        agent_path, target_path = root / "agent" / "agent.sock", root / "target" / "target.sock"
        for path, uid, gid in ((agent_path, CONTROL, PROPOSERS), (target_path, TARGET, DISPATCHERS)):
            path.parent.mkdir(mode=0o750)
            os.chown(path.parent, uid, gid)
            path.parent.chmod(0o750)

        check_paths(agent_path)
        with listener(CONTROL, PROPOSERS, "agent-control", agent_path):
            run_probe(AGENT, probe("connect", "agent-control", agent_path, PROPOSERS))
        print("PASS agent to control mutual authentication and cleanup", flush=True)

        with listener(TARGET, DISPATCHERS, "control-target", target_path):
            expect_unix_access_denied(target_path)
            agent_check("""
import errno
import socket
import sys

for port in (8000, 8001):
    with socket.socket() as sock:
        sock.settimeout(2)
        actual = sock.connect_ex(("127.0.0.1", port))
        if actual != errno.ECONNREFUSED:
            sys.exit(f"Expected no legacy HTTP listener on port {port}, got errno {actual}")
""")
            run_probe(CONTROL, probe("connect", "control-target", target_path, DISPATCHERS))
        print("PASS control to target, agent denied directly, legacy loopback ports closed", flush=True)

        with listener(TARGET, DISPATCHERS, "control-target", target_path, "IdentityError"):
            expect_no_reply(target_path, groups=(DISPATCHERS,))
        with listener(CONTROL, PROPOSERS, "agent-control", agent_path, "ProtocolError"):
            expect_no_reply(agent_path)
        print("PASS wrong UID with socket access, invalid frame and denial cleanup", flush=True)


if __name__ == "__main__":
    main()
