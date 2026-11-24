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
def listening_process(args, uid, path):
    with subprocess.Popen(args, **identity(uid),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        try:
            # Match the child's listening inode and its resolved bound path without connecting.
            fd_dir = Path(f"/proc/{process.pid}/fd")
            deadline = monotonic() + 5
            while monotonic() < deadline:
                entries = process_fds(fd_dir)
                rows = (line.split(maxsplit=7) for line in Path("/proc/net/unix").read_text().splitlines()[1:])
                ready = any(
                    len(row) == 8 and int(row[3], 16) & 0x10000
                    and f"socket:[{row[6]}]" in entries.values()
                    and bound_path(row[7], entries) == path
                    for row in rows
                )
                if process.poll() is not None:
                    raise RuntimeError(f"{path} listener exited before readiness: {process.communicate()[0]}")
                if ready:
                    break
                sleep(0.02)
            else:
                raise TimeoutError(f"{path} listener did not become ready")
            yield process
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def process_fds(fd_dir):
    entries = {}
    try:
        for fd in fd_dir.iterdir():
            try:
                entries[fd.name] = os.readlink(fd)
            except (FileNotFoundError, PermissionError):
                continue
    except (FileNotFoundError, PermissionError):
        # A child can exit or change credentials while procfs is scanned.
        pass
    return entries


def bound_path(address, entries):
    prefix = "/proc/self/fd/"
    if address.startswith(prefix):
        fd, _, name = address[len(prefix):].partition("/")
        directory = entries.get(fd)
        return Path(directory) / name if directory is not None else None
    return Path(address)


def check_socket_metadata(path, uid, gid):
    info = path.lstat()
    require((info.st_uid, info.st_gid, info.st_mode) == (uid, gid, stat.S_IFSOCK | 0o660),
            f"{path} socket ownership or mode differs")


@contextmanager
def listener(uid, gid, link, path, error=None):
    with listening_process(probe("listen", link, path, gid), uid, path) as process:
        check_socket_metadata(path, uid, gid)
        yield
        output, _ = process.communicate(timeout=5)
        check_probe(process.returncode, output, error)
        require(not os.path.lexists(path), f"{link} socket was not cleaned up")


def expect_client_peer_denial(client_uid, server_uid, wrong_uid, gid, link, path):
    # Bind and listen under the wrong UID. Root moves the live socket into the
    # trusted directory with the correct entry metadata; its peer UID stays put.
    with TemporaryDirectory(prefix="wrong-peer-", dir=path.parent.parent) as directory:
        staging = Path(directory)
        os.chown(staging, wrong_uid, wrong_uid)
        staged_path = staging / path.name
        args = (*PYTHON, "-c", """
import socket
import sys

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
    listener.settimeout(10)
    listener.bind(sys.argv[1])
    listener.listen(1)
    connection, _ = listener.accept()
    with connection:
        connection.settimeout(5)
        if connection.recv(1) != b"":
            sys.exit("Client sent protocol data to the wrong UID")
""", str(staged_path))
        with listening_process(args, wrong_uid, staged_path) as process:
            os.chown(staged_path, server_uid, gid)
            staged_path.chmod(0o660)
            staged_path.rename(path)
            try:
                check_socket_metadata(path, server_uid, gid)
                run_probe(client_uid, probe("connect", link, path, gid), "IdentityError")
                output, _ = process.communicate(timeout=5)
                require(process.returncode == 0, f"{link} wrong-UID listener failed: {output.strip()}")
            finally:
                path.unlink()


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

        expect_client_peer_denial(AGENT, CONTROL, TARGET, PROPOSERS, "agent-control", agent_path)
        expect_client_peer_denial(CONTROL, TARGET, AGENT, DISPATCHERS, "control-target", target_path)
        print("PASS both clients rejected wrong-UID listeners before sending data", flush=True)


if __name__ == "__main__":
    main()
