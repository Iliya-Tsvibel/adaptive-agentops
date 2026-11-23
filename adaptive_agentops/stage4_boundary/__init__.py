"""Task 4.1's Linux-only, one-exchange Unix socket boundary."""

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import errno
import os
import socket
import stat
import struct
import sys
from time import monotonic


__all__ = [
    "BoundaryError", "Endpoint", "IdentityError", "Link", "PathSecurityError",
    "ProtocolError", "TrustedUIDs", "handshake", "serve_once",
]

_DIRECTORY_MODE = 0o750
_SOCKET_MODE = 0o660
_UNIX_PATH_MAX = 107
_CONNECT_SECONDS = 1.0
_EXCHANGE_SECONDS = 2.0
_ACCEPT_SECONDS = 10.0

# Magic, version, message type. Each sender must half-close after its frame.
_REQUEST = b"AAO\x01\x01"
_RESPONSE = b"AAO\x01\x02"
_UCRED = struct.Struct("=iII")  # Linux pid_t, uid_t, gid_t.


class BoundaryError(Exception):
    """The boundary rejected the connection or its frame."""


class IdentityError(BoundaryError):
    pass


class PathSecurityError(BoundaryError):
    pass


class ProtocolError(BoundaryError):
    pass


class Link(Enum):
    AGENT_CONTROL = "agent-control"
    CONTROL_TARGET = "control-target"


@dataclass(frozen=True, slots=True)
class TrustedUIDs:
    """Dedicated UIDs supplied by protected launch configuration."""

    agent: int
    control: int
    target: int

    def __post_init__(self):
        uids = (self.agent, self.control, self.target)
        if any(type(uid) is not int or not 0 < uid < 0xFFFFFFFF for uid in uids):
            raise ValueError("service UIDs must be valid non-root Linux UIDs")
        if len(set(uids)) != len(uids):
            raise ValueError("service UIDs must be distinct")


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Trusted socket path and its dedicated reachability group.

    The absolute path must traverse root-owned, non-group/world-writable
    ancestors. The immediate directory is owned by the listener UID and
    access GID, mode 0750. The socket uses the same UID/GID, mode 0660.
    The caller supplies preprovisioned directories and group memberships.
    """

    link: Link
    path: str
    access_gid: int

    def __post_init__(self):
        if not isinstance(self.link, Link):
            raise ValueError("unsupported IPC link")
        if type(self.access_gid) is not int or not 0 <= self.access_gid < 0xFFFFFFFF:
            raise ValueError("invalid access GID")
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ValueError("socket path must be absolute")
        parts = self.path.split("/")[1:]
        if len(parts) < 2 or any(part in ("", ".", "..") for part in parts):
            raise ValueError("socket path must be canonical and have a dedicated directory")
        if "\0" in self.path or len(os.fsencode(self.path)) > _UNIX_PATH_MAX:
            raise ValueError("invalid Unix socket path")

    def _uids(self, uids: TrustedUIDs) -> tuple[int, int]:
        if self.link is Link.AGENT_CONTROL:
            return uids.agent, uids.control
        return uids.control, uids.target


def _require_local_uid(expected: int) -> None:
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        raise IdentityError("Linux SO_PEERCRED is required")
    if os.getuid() != expected or os.geteuid() != expected:
        raise IdentityError("process UID does not match the configured role")


def _require_peer(sock: socket.socket, expected: int) -> None:
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _UCRED.size)
        pid, uid, _gid = _UCRED.unpack(raw)
    except (AttributeError, OSError, struct.error) as exc:
        raise IdentityError("peer credentials unavailable") from exc
    if pid <= 0 or uid != expected:
        raise IdentityError("peer credentials do not match the configured role")


def _check_ancestor(info: os.stat_result) -> None:
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PathSecurityError("unsafe socket ancestor")


def _check_endpoint(
    info: os.stat_result, uid: int, gid: int, expected_mode: int,
) -> None:
    if info.st_uid != uid or info.st_gid != gid or info.st_mode != expected_mode:
        raise PathSecurityError("unexpected socket directory or entry ownership or mode")


def _reject_acls(path: str, *, directory: bool = False) -> None:
    attributes = ("system.posix_acl_access",)
    if directory:
        attributes += ("system.posix_acl_default",)
    for attribute in attributes:
        try:
            os.getxattr(path, attribute, follow_symlinks=directory)
        except OSError as exc:
            if exc.errno == errno.ENODATA:
                continue
            raise PathSecurityError("cannot verify socket path ACLs") from exc
        raise PathSecurityError("unexpected socket path ACL")


@contextmanager
def _socket_directory(endpoint: Endpoint, server_uid: int):
    # Walk without symlinks and pin the parent for subsequent filesystem and
    # socket operations. Linux resolves the procfs FD path to that directory.
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    directory, name = endpoint.path.rsplit("/", 1)
    fd = os.open("/", flags)
    try:
        for part in directory.split("/")[1:]:
            _check_ancestor(os.fstat(fd))
            _reject_acls(f"/proc/self/fd/{fd}", directory=True)
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        _check_endpoint(
            os.fstat(fd), server_uid, endpoint.access_gid, stat.S_IFDIR | _DIRECTORY_MODE,
        )
        _reject_acls(f"/proc/self/fd/{fd}", directory=True)
        address = f"/proc/self/fd/{fd}/{name}"
        if len(os.fsencode(address)) > _UNIX_PATH_MAX:
            raise PathSecurityError("socket address exceeds the Unix path limit")
        yield fd, name, address
    finally:
        os.close(fd)


def _entry(fd: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=fd, follow_symlinks=False)


def _same_entry(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("IPC deadline expired")
    return remaining


def _send_frame(sock: socket.socket, frame: bytes, deadline: float) -> None:
    sock.settimeout(_remaining(deadline))
    sock.sendall(frame)
    sock.shutdown(socket.SHUT_WR)


def _expect_frame(sock: socket.socket, expected: bytes, deadline: float) -> None:
    frame = bytearray()
    while len(frame) <= len(expected):
        sock.settimeout(_remaining(deadline))
        chunk = sock.recv(len(expected) + 1 - len(frame))
        if not chunk:
            _remaining(deadline)
            if frame == expected:
                return
            break
        frame.extend(chunk)
    raise ProtocolError("invalid, unsupported, truncated or overlong IPC frame")


def handshake(endpoint: Endpoint, uids: TrustedUIDs) -> None:
    """Verify the listener UID and complete one handshake, or raise on denial."""
    client_uid, server_uid = endpoint._uids(uids)
    _require_local_uid(client_uid)
    with _socket_directory(endpoint, server_uid) as (fd, name, address):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            expected_mode = stat.S_IFSOCK | _SOCKET_MODE
            before = _entry(fd, name)
            _check_endpoint(before, server_uid, endpoint.access_gid, expected_mode)
            _reject_acls(address)
            connection.settimeout(_CONNECT_SECONDS)
            connection.connect(address)
            deadline = monotonic() + _EXCHANGE_SECONDS
            _require_peer(connection, server_uid)
            after = _entry(fd, name)
            _check_endpoint(after, server_uid, endpoint.access_gid, expected_mode)
            _reject_acls(address)
            if not _same_entry(before, after):
                raise PathSecurityError("socket entry changed during connection")
            _send_frame(connection, _REQUEST, deadline)
            _expect_frame(connection, _RESPONSE, deadline)


def serve_once(endpoint: Endpoint, uids: TrustedUIDs) -> None:
    """Listen for one peer and answer one valid handshake. Existing entries deny."""
    client_uid, server_uid = endpoint._uids(uids)
    _require_local_uid(server_uid)
    with _socket_directory(endpoint, server_uid) as (fd, name, address):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            entry_fd = None
            created = None
            try:
                listener.bind(address)
                # Keep the verified inode pinned through validation and cleanup.
                entry_fd = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
                info = os.fstat(entry_fd)
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != server_uid:
                    raise PathSecurityError("unexpected bound socket entry")
                created = info
                bound_path = f"/proc/self/fd/{entry_fd}"
                if created.st_gid != endpoint.access_gid:
                    os.chown(bound_path, -1, endpoint.access_gid)
                os.chmod(bound_path, _SOCKET_MODE)
                ready = _entry(fd, name)
                if not _same_entry(ready, created):
                    raise PathSecurityError("bound socket entry changed")
                _check_endpoint(
                    ready, server_uid, endpoint.access_gid, stat.S_IFSOCK | _SOCKET_MODE,
                )
                _reject_acls(address)
                listener.listen(1)
                listener.settimeout(_ACCEPT_SECONDS)
                connection, _address = listener.accept()
                with connection:
                    deadline = monotonic() + _EXCHANGE_SECONDS
                    _require_peer(connection, client_uid)
                    _expect_frame(connection, _REQUEST, deadline)
                    _send_frame(connection, _RESPONSE, deadline)
            finally:
                # Without a verified inode, leave the entry in place. Malicious
                # same-UID writers in the protected directory are out of scope.
                try:
                    if created is not None and _same_entry(created, _entry(fd, name)):
                        os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    pass
                finally:
                    if entry_fd is not None:
                        os.close(entry_fd)
