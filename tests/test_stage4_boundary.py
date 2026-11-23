from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import errno
import os
import socket
import stat
import struct
import sys
from tempfile import TemporaryDirectory
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from adaptive_agentops import stage4_boundary as boundary


UIDS = boundary.TrustedUIDs(agent=10001, control=10002, target=10003)
AGENT = boundary.Link.AGENT_CONTROL
TARGET = boundary.Link.CONTROL_TARGET
LINUX = pytest.mark.skipif(sys.platform != "linux", reason="Linux UDS boundary")


@pytest.mark.parametrize("uid", [0, -1, True, "10001", 0xFFFFFFFF])
def test_invalid_service_uid(uid):
    with pytest.raises(ValueError):
        boundary.TrustedUIDs(uid, UIDS.control, UIDS.target)


def test_roles_are_distinct_and_frozen():
    with pytest.raises(ValueError):
        boundary.TrustedUIDs(UIDS.agent, UIDS.control, UIDS.agent)
    with pytest.raises(FrozenInstanceError):
        UIDS.agent = 10004
    assert boundary.Endpoint(AGENT, "/run/agent/probe", 10010)._uids(UIDS) == (
        UIDS.agent, UIDS.control,
    )
    assert boundary.Endpoint(TARGET, "/run/target/probe", 10011)._uids(UIDS) == (
        UIDS.control, UIDS.target,
    )


@pytest.mark.parametrize("path", [
    "relative/socket", "/socket", "/run//socket", "/run/./socket",
    "/run/../socket", "/run/agent/", "/run/agent/bad\0name", "/run/" + "a" * 108,
])
def test_noncanonical_or_overlong_path_denied(path):
    with pytest.raises(ValueError):
        boundary.Endpoint(AGENT, path, 10010)


def test_non_linux_denied_before_startup(monkeypatch):
    monkeypatch.setattr(boundary.sys, "platform", "darwin")
    with pytest.raises(boundary.IdentityError):
        boundary._require_local_uid(UIDS.agent)


@LINUX
def test_missing_linux_option_denied(monkeypatch):
    monkeypatch.delattr(boundary.socket, "SO_PEERCRED", raising=False)
    with pytest.raises(boundary.IdentityError):
        boundary._require_local_uid(UIDS.agent)


@LINUX
def test_mismatched_real_and_effective_uid_denied(monkeypatch):
    monkeypatch.setattr(boundary.os, "getuid", lambda: UIDS.agent)
    monkeypatch.setattr(boundary.os, "geteuid", lambda: UIDS.control)
    with pytest.raises(boundary.IdentityError):
        boundary._require_local_uid(UIDS.agent)


@LINUX
@pytest.mark.parametrize("raw", [
    b"", b"short", struct.pack("=iII", 1, UIDS.agent, 10010),
    struct.pack("=iII", 0, UIDS.control, 10010),
])
def test_missing_malformed_or_wrong_credentials_denied(raw):
    peer = Mock()
    peer.getsockopt.return_value = raw
    with pytest.raises(boundary.IdentityError):
        boundary._require_peer(peer, UIDS.control)


@LINUX
def test_unavailable_credentials_denied():
    peer = Mock()
    peer.getsockopt.side_effect = OSError("unavailable")
    with pytest.raises(boundary.IdentityError):
        boundary._require_peer(peer, UIDS.control)


@LINUX
def test_kernel_credentials_from_same_uid_socketpair():
    # Exercises the kernel read and the denial rule without claiming isolation.
    left, right = socket.socketpair()
    with left, right:
        boundary._require_peer(left, os.geteuid())
        with pytest.raises(boundary.IdentityError):
            boundary._require_peer(left, os.geteuid() + 1)


@pytest.mark.parametrize(("uid", "mode"), [
    (10001, stat.S_IFDIR | 0o755),
    (0, stat.S_IFDIR | 0o775),
    (0, stat.S_IFDIR | 0o1777),
    (0, stat.S_IFLNK | 0o755),
])
def test_unsafe_ancestors_denied(uid, mode):
    with pytest.raises(boundary.PathSecurityError):
        boundary._check_ancestor(SimpleNamespace(st_uid=uid, st_mode=mode))


@pytest.mark.parametrize(("uid", "gid", "mode"), [
    (0, 10010, stat.S_IFDIR | 0o750),
    (10002, 10011, stat.S_IFDIR | 0o750),
    (10002, 10010, stat.S_IFDIR | 0o770),
    (10002, 10010, stat.S_IFDIR | 0o755),
    (10002, 10010, stat.S_IFLNK | 0o750),
])
def test_unsafe_endpoint_directory_denied(uid, gid, mode):
    info = SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=mode)
    with pytest.raises(boundary.PathSecurityError):
        boundary._check_endpoint(info, UIDS.control, 10010, stat.S_IFDIR | 0o750)


@pytest.fixture
def local_endpoint(monkeypatch):
    # The test runner's temporary directory is outside the production layout.
    monkeypatch.setattr(boundary, "_check_ancestor", lambda info: None)
    monkeypatch.setattr(boundary, "_require_local_uid", lambda expected: None)
    with TemporaryDirectory(prefix="aao-") as root:
        directory = os.path.join(root, "socket-dir")
        os.mkdir(directory)
        os.chmod(directory, 0o750)
        endpoint = boundary.Endpoint(AGENT, os.path.join(directory, "ipc"), os.stat(directory).st_gid)
        # Exercise the filesystem under the test runner's identity, even as root.
        # Production TrustedUIDs rejects root and requires distinct role UIDs.
        identities = SimpleNamespace(agent=os.geteuid() + 1, control=os.geteuid())
        yield endpoint, identities


@LINUX
@pytest.mark.parametrize(("component", "acl"), [
    ("root", "access"), ("ancestor", "access"), ("ancestor", "default"),
    ("directory", "access"), ("directory", "default"), ("socket", "access"),
])
def test_protected_path_rejects_acl(local_endpoint, monkeypatch, component, acl):
    endpoint, identities = local_endpoint
    directory = os.path.dirname(endpoint.path)
    protected = {
        "root": "/", "ancestor": os.path.dirname(directory), "directory": directory,
        "socket": endpoint.path,
    }[component]
    seen = []

    def getxattr(path, attribute, *, follow_symlinks):
        assert follow_symlinks == (not path.endswith("/ipc"))
        if os.path.realpath(path) == protected and attribute == f"system.posix_acl_{acl}":
            seen.append(attribute)
            return b"present"
        raise OSError(errno.ENODATA, "no ACL")

    monkeypatch.setattr(boundary.os, "getxattr", getxattr)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(endpoint.path)
        os.chmod(endpoint.path, 0o660)
        with pytest.raises(boundary.PathSecurityError, match="unexpected socket path ACL"):
            boundary.handshake(endpoint, identities)
    assert seen == [f"system.posix_acl_{acl}"]


@LINUX
@pytest.mark.parametrize("error", [errno.EACCES, errno.EOPNOTSUPP])
def test_socket_directory_denies_unverifiable_acl(local_endpoint, monkeypatch, error):
    endpoint, identities = local_endpoint
    monkeypatch.setattr(boundary.os, "getxattr", Mock(side_effect=OSError(error, "ACL lookup")))
    with pytest.raises(boundary.PathSecurityError, match="cannot verify socket path ACLs"):
        boundary.serve_once(endpoint, identities)
    assert not os.path.lexists(endpoint.path)


@LINUX
def test_symlinked_parent_denied(local_endpoint):
    endpoint, identities = local_endpoint
    directory = os.path.dirname(endpoint.path)
    symlink = directory + "-link"
    os.symlink(directory, symlink)
    substituted = boundary.Endpoint(AGENT, os.path.join(symlink, "ipc"), endpoint.access_gid)
    with pytest.raises(OSError):
        with boundary._socket_directory(substituted, identities.control):
            pytest.fail("symlinked directory admitted")


@LINUX
@pytest.mark.parametrize("substitution", ["symlink", "file", "unsafe-socket"])
def test_client_rejects_ineligible_entry(local_endpoint, substitution):
    endpoint, identities = local_endpoint
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        if substitution == "symlink":
            destination = endpoint.path + "-actual"
            listener.bind(destination)
            os.chmod(destination, 0o660)
            os.symlink(destination, endpoint.path)
        elif substitution == "file":
            with open(endpoint.path, "xb"):
                pass
        else:
            listener.bind(endpoint.path)
            os.chmod(endpoint.path, 0o666)
        with pytest.raises(boundary.PathSecurityError):
            boundary.handshake(endpoint, identities)


@LINUX
def test_server_preserves_existing_path(local_endpoint):
    endpoint, identities = local_endpoint
    with open(endpoint.path, "xb") as existing:
        existing.write(b"existing")
    with pytest.raises(OSError):
        boundary.serve_once(endpoint, identities)
    with open(endpoint.path, "rb") as existing:
        assert existing.read() == b"existing"


@LINUX
@pytest.mark.parametrize(("failure", "replace"), [
    ("open", False), ("fstat", False), ("fstat", True),
])
def test_server_preserves_unverified_entry_after_bind(
    local_endpoint, monkeypatch, failure, replace,
):
    endpoint, identities = local_endpoint
    entry_fd = None
    open_path = os.open
    fstat = os.fstat
    error = OSError(errno.EIO, f"entry {failure} failed")

    def open_entry(path, flags, *, dir_fd=None):
        nonlocal entry_fd
        if path != os.path.basename(endpoint.path):
            return open_path(path, flags, dir_fd=dir_fd)
        assert stat.S_ISSOCK(os.lstat(endpoint.path).st_mode)
        if failure == "open":
            raise error
        entry_fd = open_path(path, flags, dir_fd=dir_fd)
        return entry_fd

    def stat_entry(fd):
        if fd == entry_fd:
            if replace:
                os.unlink(endpoint.path)
                with open(endpoint.path, "xb") as entry:
                    entry.write(b"replacement")
            raise error
        return fstat(fd)

    with monkeypatch.context() as patch:
        patch.setattr(boundary.os, "open", open_entry)
        patch.setattr(boundary.os, "fstat", stat_entry)
        with pytest.raises(OSError) as caught:
            boundary.serve_once(endpoint, identities)
        assert caught.value is error
        if entry_fd is not None:
            with pytest.raises(OSError) as closed:
                fstat(entry_fd)
            assert closed.value.errno == errno.EBADF

    if replace:
        with open(endpoint.path, "rb") as entry:
            assert entry.read() == b"replacement"
    else:
        assert stat.S_ISSOCK(os.lstat(endpoint.path).st_mode)


@LINUX
def test_server_removes_verified_socket_after_normal_exchange(local_endpoint, monkeypatch):
    endpoint, identities = local_endpoint
    client, connection = socket.socketpair()
    with client, connection:
        client.settimeout(1)
        client.sendall(boundary._REQUEST)
        client.shutdown(socket.SHUT_WR)
        monkeypatch.setattr(socket.socket, "accept", lambda listener: (connection, None))
        monkeypatch.setattr(boundary, "_require_peer", lambda peer, expected: None)
        boundary.serve_once(endpoint, identities)
        boundary._expect_frame(client, boundary._RESPONSE, boundary.monotonic() + 1)
    assert not os.path.lexists(endpoint.path)


@LINUX
def test_server_cleans_up_verified_socket_after_acl_denial(local_endpoint, monkeypatch):
    endpoint, identities = local_endpoint
    getxattr = os.getxattr

    def unexpected_socket_acl(path, attribute, *, follow_symlinks):
        if not follow_symlinks:
            return b"present"
        return getxattr(path, attribute, follow_symlinks=True)

    monkeypatch.setattr(boundary.os, "getxattr", unexpected_socket_acl)
    with pytest.raises(boundary.PathSecurityError, match="unexpected socket path ACL"):
        boundary.serve_once(endpoint, identities)
    assert not os.path.lexists(endpoint.path)


@LINUX
@pytest.mark.parametrize("replace_before_verification", [False, True])
def test_server_preserves_replacement_during_startup(
    local_endpoint, monkeypatch, replace_before_verification,
):
    endpoint, identities = local_endpoint
    open_path = os.open
    chmod = os.chmod
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as replacement:
        def replace():
            os.unlink(endpoint.path)
            if replace_before_verification:
                with open(endpoint.path, "xb") as entry:
                    entry.write(b"replacement")
            else:
                replacement.bind(endpoint.path)

        def open_with_replacement(path, flags, *, dir_fd=None):
            if path == os.path.basename(endpoint.path):
                replace()
            return open_path(path, flags, dir_fd=dir_fd)

        def chmod_with_replacement(path, mode):
            replace()
            chmod(path, mode)

        if replace_before_verification:
            monkeypatch.setattr(boundary.os, "open", open_with_replacement)
        else:
            monkeypatch.setattr(boundary.os, "chmod", chmod_with_replacement)
        with pytest.raises(boundary.PathSecurityError, match="bound socket entry"):
            boundary.serve_once(endpoint, identities)

        if replace_before_verification:
            with open(endpoint.path, "rb") as entry:
                assert entry.read() == b"replacement"
        else:
            assert stat.S_ISSOCK(os.lstat(endpoint.path).st_mode)


@LINUX
def test_client_rejects_changed_socket_inode_before_sending(local_endpoint, monkeypatch):
    endpoint, identities = local_endpoint
    before = SimpleNamespace(
        st_uid=identities.control, st_gid=endpoint.access_gid,
        st_mode=stat.S_IFSOCK | 0o660, st_dev=1, st_ino=2,
    )
    after = SimpleNamespace(**vars(before))
    after.st_ino = 3
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.getsockopt.return_value = struct.pack("=iII", 1, identities.control, 0)
    monkeypatch.setattr(boundary.socket, "socket", Mock(return_value=connection))
    monkeypatch.setattr(boundary, "_entry", Mock(side_effect=[before, after]))
    with pytest.raises(boundary.PathSecurityError):
        boundary.handshake(endpoint, identities)
    connection.sendall.assert_not_called()


@pytest.mark.parametrize("expected", [boundary._REQUEST, boundary._RESPONSE])
@pytest.mark.parametrize("invalid", [
    b"", b"AAO\x01", b"AAO\x02\x01", b"AAO\x01\xff", b"AAO\x01\x01x",
    b"x" * 16385, b'{"role":"control"}',
])
def test_rejects_invalid_frame_in_either_direction(expected, invalid):
    peer = Mock()
    # Model recv's upper bound without queueing a large real socket write.
    peer.recv.side_effect = [invalid[:len(expected) + 1], b""]
    with pytest.raises(boundary.ProtocolError):
        boundary._expect_frame(peer, expected, boundary.monotonic() + 1)
    assert peer.recv.call_args_list[0].args == (len(expected) + 1,)


def test_requires_eof_and_rejects_trailing_bytes_after_a_complete_frame():
    peer = Mock()
    peer.recv.side_effect = [boundary._REQUEST, b"x"]
    with pytest.raises(boundary.ProtocolError):
        boundary._expect_frame(peer, boundary._REQUEST, boundary.monotonic() + 1)
    assert peer.recv.call_args.args == (1,)


def test_frame_deadline_does_not_reset_on_partial_read(monkeypatch):
    peer = Mock()
    peer.recv.side_effect = [b"A", b"A"]
    monkeypatch.setattr(boundary, "monotonic", Mock(side_effect=[0.0, 0.75, 1.0]))
    with pytest.raises(TimeoutError):
        boundary._expect_frame(peer, boundary._REQUEST, 1.0)
    assert [call.args[0] for call in peer.settimeout.call_args_list] == [1.0, 0.25]


@LINUX
def test_stalled_peer_times_out():
    left, right = socket.socketpair()
    with left, right, pytest.raises(TimeoutError):
        boundary._expect_frame(left, boundary._REQUEST, boundary.monotonic() + 0.05)


@LINUX
@pytest.mark.parametrize("link", [AGENT, TARGET])
@pytest.mark.parametrize("denied", [None, "client", "server"])
def test_public_exchange_requires_both_peer_checks(local_endpoint, monkeypatch, link, denied):
    endpoint, identities = local_endpoint
    if link is TARGET:
        endpoint = boundary.Endpoint(link, endpoint.path, endpoint.access_gid)
        identities = SimpleNamespace(control=identities.agent, target=identities.control)
    if link is AGENT:
        client_uid, listener_uid = identities.agent, identities.control
    else:
        client_uid, listener_uid = identities.control, identities.target
    expected_local = {"client": client_uid, "server": listener_uid}
    expected_peer = {"client": listener_uid, "server": client_uid}
    caller = get_ident()
    ready = Event()
    listen = socket.socket.listen
    read_credentials = boundary._require_peer
    sent = []
    authenticate = []
    local_checks = []

    def verify_local(expected):
        side = "client" if get_ident() == caller else "server"
        assert expected == expected_local[side]
        local_checks.append(side)

    def notify(listener, backlog):
        listen(listener, backlog)
        ready.set()

    def verify(peer, expected):
        side = "client" if get_ident() == caller else "server"
        assert expected == expected_peer[side]
        authenticate.append(side)
        # All sockets belong to the same test UID. Substitute the expected
        # kernel UID to exercise each role decision through the real API.
        read_credentials(peer, os.geteuid() + (side == denied))

    send_frame = boundary._send_frame

    def record(peer, frame, deadline):
        sent.append(frame)
        send_frame(peer, frame, deadline)

    monkeypatch.setattr(socket.socket, "listen", notify)
    monkeypatch.setattr(boundary, "_require_local_uid", verify_local)
    monkeypatch.setattr(boundary, "_require_peer", verify)
    monkeypatch.setattr(boundary, "_send_frame", record)
    monkeypatch.setattr(boundary, "_ACCEPT_SECONDS", 2.0)
    with ThreadPoolExecutor(max_workers=1) as executor:
        server = executor.submit(boundary.serve_once, endpoint, identities)
        assert ready.wait(2)
        if denied == "client":
            with pytest.raises(boundary.IdentityError):
                boundary.handshake(endpoint, identities)
            with pytest.raises((boundary.ProtocolError, OSError)):
                server.result(timeout=3)
            assert not sent
        elif denied == "server":
            # The server can reject before the client finishes its path check.
            with pytest.raises((boundary.BoundaryError, OSError)):
                boundary.handshake(endpoint, identities)
            with pytest.raises(boundary.IdentityError):
                server.result(timeout=3)
            assert boundary._RESPONSE not in sent
        else:
            boundary.handshake(endpoint, identities)
            server.result(timeout=3)
            assert sent == [boundary._REQUEST, boundary._RESPONSE]
    assert sorted(local_checks) == ["client", "server"]
    assert sorted(authenticate) == ["client", "server"]
    assert not os.path.lexists(endpoint.path)


@LINUX
@pytest.mark.parametrize("link", [AGENT, TARGET])
@pytest.mark.parametrize(("denial", "error"), [
    ("uid", boundary.IdentityError),
    ("malformed", boundary.ProtocolError),
    ("missing-eof", TimeoutError),
])
def test_listener_denial_closes_without_response(local_endpoint, monkeypatch, link, denial, error):
    endpoint, identities = local_endpoint
    endpoint = boundary.Endpoint(link, endpoint.path, endpoint.access_gid)
    if link is TARGET:
        identities = SimpleNamespace(control=identities.agent, target=identities.control)
    expected_client = identities.agent if link is AGENT else identities.control
    ready = Event()
    listen = socket.socket.listen
    require_peer = boundary._require_peer

    def notify(listener, backlog):
        listen(listener, backlog)
        ready.set()

    def verify(peer, expected):
        assert expected == expected_client
        require_peer(peer, os.geteuid() + (denial == "uid"))

    monkeypatch.setattr(socket.socket, "listen", notify)
    monkeypatch.setattr(boundary, "_require_peer", verify)
    monkeypatch.setattr(boundary, "_ACCEPT_SECONDS", 2.0)
    monkeypatch.setattr(boundary, "_EXCHANGE_SECONDS", 0.2)
    with ThreadPoolExecutor(max_workers=1) as executor:
        server = executor.submit(boundary.serve_once, endpoint, identities)
        assert ready.wait(2)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(endpoint.path)
            if denial == "malformed":
                client.sendall(b"AAO\x01\xff")
                client.shutdown(socket.SHUT_WR)
            elif denial == "missing-eof":
                client.sendall(boundary._REQUEST)
            with pytest.raises(error):
                server.result(timeout=3)
            assert client.recv(1) == b""
    assert not os.path.lexists(endpoint.path)
