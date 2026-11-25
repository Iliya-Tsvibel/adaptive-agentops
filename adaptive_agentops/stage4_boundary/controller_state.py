"""Protected and bounded SQLite backing for a single Stage 4 Controller."""

from contextlib import closing, contextmanager, suppress
import errno
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
from uuid import uuid4

from . import BoundaryError, _check_ancestor, _reject_acls, _require_local_uid


DATABASE_NAME = "controller.sqlite3"
_APPLICATION_ID = 0x41414F34
_SCHEMA_VERSION = 1
_MAX_SESSIONS = 256
_MAX_EVENTS = 8192

_SCHEMA = (
    """CREATE TABLE controller_sessions (
    session_id TEXT PRIMARY KEY NOT NULL CHECK (length(session_id) = 36),
    run_id TEXT UNIQUE NOT NULL CHECK (length(run_id) = 36),
    control_uid INTEGER NOT NULL CHECK (control_uid BETWEEN 1 AND 4294967294),
    agent_uid INTEGER NOT NULL CHECK (agent_uid BETWEEN 1 AND 4294967294),
    requester_id TEXT NOT NULL CHECK (length(requester_id) BETWEEN 1 AND 64),
    agent_id TEXT NOT NULL CHECK (length(agent_id) BETWEEN 1 AND 64),
    tool TEXT NOT NULL CHECK (length(tool) BETWEEN 1 AND 64),
    resource TEXT NOT NULL CHECK (length(resource) BETWEEN 1 AND 64),
    environment TEXT NOT NULL CHECK (length(environment) BETWEEN 1 AND 64),
    operator_map TEXT NOT NULL CHECK (length(operator_map) BETWEEN 1 AND 4096),
    wall_started_ns INTEGER NOT NULL CHECK (wall_started_ns >= 0),
    elapsed_started_ns INTEGER NOT NULL CHECK (elapsed_started_ns >= 0),
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'INVALIDATED')),
    UNIQUE (session_id, run_id)
) STRICT""",
    """CREATE UNIQUE INDEX controller_one_active_session
ON controller_sessions(state) WHERE state = 'ACTIVE'""",
    """CREATE TABLE controller_grants (
    grant_id TEXT PRIMARY KEY NOT NULL CHECK (length(grant_id) = 36),
    session_id TEXT UNIQUE NOT NULL,
    run_id TEXT NOT NULL,
    issuer_uid INTEGER NOT NULL CHECK (issuer_uid BETWEEN 1 AND 4294967294),
    issuer_principal TEXT NOT NULL CHECK (length(issuer_principal) BETWEEN 1 AND 64),
    agent_uid INTEGER NOT NULL CHECK (agent_uid BETWEEN 1 AND 4294967294),
    agent_id TEXT NOT NULL CHECK (length(agent_id) BETWEEN 1 AND 64),
    tool TEXT NOT NULL CHECK (length(tool) BETWEEN 1 AND 64),
    resource TEXT NOT NULL CHECK (length(resource) BETWEEN 1 AND 64),
    environment TEXT NOT NULL CHECK (length(environment) BETWEEN 1 AND 64),
    wall_expiry_ns INTEGER NOT NULL CHECK (wall_expiry_ns > 0),
    elapsed_expiry_ns INTEGER NOT NULL CHECK (elapsed_expiry_ns > 0),
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'REVOKED', 'EXPIRED', 'INVALIDATED')),
    FOREIGN KEY (session_id, run_id) REFERENCES controller_sessions(session_id, run_id),
    UNIQUE (session_id, grant_id)
) STRICT""",
    """CREATE TABLE controller_events (
    event_id TEXT PRIMARY KEY NOT NULL CHECK (length(event_id) = 36),
    session_id TEXT NOT NULL REFERENCES controller_sessions(session_id),
    grant_id TEXT,
    peer_uid INTEGER CHECK (peer_uid BETWEEN 1 AND 4294967294),
    kind TEXT NOT NULL CHECK (kind IN ('OPERATOR', 'ADMISSION', 'LIFECYCLE')),
    method TEXT CHECK (length(method) BETWEEN 1 AND 16),
    decision TEXT NOT NULL CHECK (length(decision) BETWEEN 1 AND 64),
    reason TEXT CHECK (length(reason) BETWEEN 1 AND 64),
    grant_matched INTEGER CHECK (grant_matched IN (0, 1)),
    policy_decision TEXT CHECK (length(policy_decision) BETWEEN 1 AND 32),
    FOREIGN KEY (session_id, grant_id) REFERENCES controller_grants(session_id, grant_id)
) STRICT""",
)


class ControllerStateError(BoundaryError):
    """The protected Controller store cannot establish safe authority."""


class ControllerLeaseError(ControllerStateError):
    """Another Controller holds the protected state directory lease."""


@contextmanager
def _leased_directory(path: str | Path, control_uid: int):
    if type(control_uid) is not int or not 0 < control_uid < 0xFFFFFFFF:
        raise ControllerStateError("Controller state requires a non-root service UID")
    _require_local_uid(control_uid)
    name = os.fspath(path)
    if not isinstance(name, str) or not name.startswith("/") or "\0" in name:
        raise ControllerStateError("Controller state directory must be canonical and absolute")
    parts = name[1:].split("/")
    if len(parts) < 2 or any(part in ("", ".", "..") for part in parts):
        raise ControllerStateError("Controller state directory must be canonical and absolute")

    dir_flags = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", os.O_PATH | dir_flags)
    try:
        for index, part in enumerate(parts, start=1):
            _check_ancestor(os.fstat(fd))
            _reject_acls(f"/proc/self/fd/{fd}", directory=True)
            access = os.O_RDONLY if index == len(parts) else os.O_PATH
            child = os.open(part, access | dir_flags, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_mode != stat.S_IFDIR | 0o700 or info.st_uid != control_uid:
            raise ControllerStateError("Controller state directory requires service ownership and mode 0700")
        _reject_acls(f"/proc/self/fd/{fd}", directory=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise ControllerLeaseError("Controller state directory is already leased") from None
            raise
        yield fd
    finally:
        os.close(fd)


def _private_file(directory_fd: int, name: str, control_uid: int):
    fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if info.st_mode != stat.S_IFREG | 0o600 or info.st_nlink != 1 or info.st_uid != control_uid:
            raise ControllerStateError("Controller state files require service ownership, mode 0600 and one link")
        _reject_acls(f"/proc/self/fd/{directory_fd}/{name}")
        return info.st_dev, info.st_ino
    finally:
        os.close(fd)


def _verified_files(directory_fd: int, control_uid: int, filename: str):
    journal = filename + "-journal"
    entries = set(os.listdir(directory_fd))
    if entries not in ({filename}, {filename, journal}):
        raise ControllerStateError("Controller database is missing or its state directory contains foreign entries")
    identity = _private_file(directory_fd, filename, control_uid)
    if journal in entries:
        _private_file(directory_fd, journal, control_uid)
    return identity


@contextmanager
def _database(directory_fd: int, control_uid: int, filename: str = DATABASE_NAME):
    identity = _verified_files(directory_fd, control_uid, filename)
    # Retain the pinned protected directory for the duration of the connection.
    # SQLite opens the database and its rollback journal under this directory.
    uri = Path(f"/proc/self/fd/{directory_fd}/{filename}").as_uri() + "?mode=rw"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA synchronous = EXTRA")
            pragmas = {
                name: connection.execute(f"PRAGMA {name}").fetchone()
                for name in ("journal_mode", "foreign_keys", "trusted_schema", "synchronous")
            }
            if pragmas != {
                "journal_mode": ("delete",), "foreign_keys": (1,), "trusted_schema": (0,), "synchronous": (3,),
            }:
                raise ControllerStateError("Controller database requires DELETE journaling and durable secure settings")
            if _verified_files(directory_fd, control_uid, filename) != identity:
                raise ControllerStateError("Controller database changed during open")
            yield connection
    except sqlite3.Error:
        raise ControllerStateError("Controller database is unavailable or corrupt") from None


def _verify_schema_metadata(connection: sqlite3.Connection, schema, version, application_id):
    if (
        connection.execute("PRAGMA application_id").fetchone() != (application_id,)
        or connection.execute("PRAGMA user_version").fetchone() != (version,)
        or {sql for (sql,) in connection.execute("SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL")} != set(schema)
    ):
        raise ControllerStateError("Controller database has an unexpected application ID, schema or version")
    if (
        connection.execute("PRAGMA integrity_check(1)").fetchall() != [("ok",)]
        or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
    ):
        raise ControllerStateError("Controller database failed integrity checks")
def _verify_schema(connection: sqlite3.Connection, schema, version):
    _verify_schema_metadata(connection, schema, version, _APPLICATION_ID)
    if connection.execute(
        "SELECT 1 FROM controller_grants AS g JOIN controller_sessions AS s USING(session_id) "
        "WHERE g.state = 'ACTIVE' AND s.state != 'ACTIVE' LIMIT 1"
    ).fetchone():
        raise ControllerStateError("Controller database contains inconsistent live authority")
    for table, maximum in (("controller_sessions", _MAX_SESSIONS), ("controller_events", _MAX_EVENTS)):
        if connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > maximum:
            raise ControllerStateError("Controller database exceeds evidence capacity")


def provision_controller_state(state_dir: str | Path, control_uid: int):
    """Initialize an existing empty service-private directory explicitly."""
    _provision_controller_state(state_dir, control_uid, _SCHEMA, _SCHEMA_VERSION)


def _provision_controller_state(state_dir, control_uid, schema, version):
    _provision_state(
        state_dir, control_uid, DATABASE_NAME, schema, version, _APPLICATION_ID,
        lambda connection: _verify_schema(connection, schema, version),
    )


def _provision_state(state_dir, control_uid, filename, schema, version, application_id, verify, initialize=None):
    with _state_lease(state_dir, control_uid) as directory_fd:
        if os.listdir(directory_fd):
            raise ControllerStateError("Controller state provisioning requires an empty directory")
        fd = os.open(
            filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            mode=0o600, dir_fd=directory_fd,
        )
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with _database(directory_fd, control_uid, filename) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for ddl in schema:
                connection.execute(ddl)
            if initialize is not None:
                initialize(connection)
            connection.execute(f"PRAGMA application_id = {application_id}")
            connection.execute(f"PRAGMA user_version = {version}")
            _commit(connection)
            verify(connection)
        os.fsync(directory_fd)


@contextmanager
def _state_lease(state_dir, control_uid):
    try:
        with _leased_directory(state_dir, control_uid) as directory_fd:
            yield directory_fd
    except OSError:
        raise ControllerStateError("Controller state filesystem operation failed") from None


def _commit(connection):
    connection.commit()


class _ControllerStore:
    def __init__(self, connection):
        self._connection = connection
        self._failed = False
        self._session = None
        self._grant = None
        self._operators = {}

    @contextmanager
    def _write(self):
        if self._failed:
            raise ControllerStateError("Controller session is unusable after a state failure")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            yield
            _commit(self._connection)
        except BaseException as exc:
            self._failed = True
            if self._connection.in_transaction:
                with suppress(sqlite3.Error):
                    self._connection.rollback()
            if isinstance(exc, sqlite3.Error):
                raise ControllerStateError("Controller state transaction failed; commit may be uncertain") from None
            raise

    def _record(self, event_id, session_id, grant_id, peer_uid, kind, method, decision,
                reason=None, grant_matched=None, policy_decision=None):
        if self._connection.execute("SELECT count(*) FROM controller_events").fetchone()[0] >= _MAX_EVENTS:
            raise ControllerStateError("Controller evidence capacity exhausted")
        self._connection.execute("INSERT INTO controller_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            str(event_id), str(session_id), str(grant_id) if grant_id else None, peer_uid, kind, method,
            decision, reason, grant_matched, policy_decision,
        ))

    def start(self, session, control_uid, agent_uid, operators, tool, resource):
        if self._session is not None or session.grant is not None:
            raise ControllerStateError("Controller state requires a fresh session")
        ctx = session.context
        mapping = tuple(sorted(
            (identity.uid, identity.principal, tuple(sorted(role.value for role in identity.roles)))
            for identity in operators
        ))
        binding = (
            str(session.session_id), str(ctx.run_id), control_uid, agent_uid, ctx.requester_id,
            ctx.agent_id, tool, resource, ctx.environment, json.dumps(mapping, separators=(",", ":")),
            *session.clock.start, "ACTIVE",
        )
        with self._write():
            if self._connection.execute("SELECT count(*) FROM controller_sessions").fetchone()[0] >= _MAX_SESSIONS:
                raise ControllerStateError("Controller session capacity exhausted")
            old = self._connection.execute(
                "SELECT session_id FROM controller_sessions WHERE state = 'ACTIVE'"
            ).fetchone()
            if old:
                (old_session,) = old
                previous = self._connection.execute(
                    "SELECT grant_id FROM controller_grants WHERE session_id = ? AND state = 'ACTIVE'",
                    (old_session,),
                ).fetchone()
                self._connection.execute(
                    "UPDATE controller_grants SET state = 'INVALIDATED' "
                    "WHERE session_id = ? AND state = 'ACTIVE'", (old_session,),
                )
                self._connection.execute(
                    "UPDATE controller_sessions SET state = 'INVALIDATED' WHERE session_id = ?", (old_session,),
                )
                self._record(uuid4(), old_session, previous[0] if previous else None, None,
                             "LIFECYCLE", "RESTART", "INVALIDATED")
            self._connection.execute(
                "INSERT INTO controller_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", binding,
            )
            self._record(uuid4(), session.session_id, None, None, "LIFECYCLE", "START", "STARTED")
        self._session = binding
        self._operators = {uid: (principal, roles) for uid, principal, roles in mapping}

    def _verify_current_session(self, session_id):
        if (
            self._session is None or str(session_id) != self._session[0]
            or self._connection.execute(
                "SELECT * FROM controller_sessions WHERE state = 'ACTIVE'"
            ).fetchone() != self._session
            or self._connection.execute(
                "SELECT * FROM controller_grants WHERE session_id = ?", (self._session[0],),
            ).fetchone() != self._grant
        ):
            raise ControllerStateError("Controller session or grant binding changed")

    @staticmethod
    def _bound_grant(grant, uid, principal, state):
        return (
            str(grant.grant_id), str(grant.session_id), str(grant.run_id), uid, principal, grant.agent_uid,
            grant.agent_id, grant.tool, grant.resource, grant.environment, grant.wall_expiry,
            grant.elapsed_expiry, state,
        )

    def _check_grant(self, grant, *, live=False):
        if (
            self._grant is None or grant is None or (live and self._grant[-1] != "ACTIVE")
            or self._bound_grant(grant, self._grant[3], self._grant[4], self._grant[-1]) != self._grant
        ):
            raise ControllerStateError("Controller has no exact grant binding")

    def record_operator(self, event, *, grant=None):
        method = event.method.name if event.method else None
        decision = event.result.name
        updated = self._grant
        with self._write():
            self._verify_current_session(event.session_id)
            if decision == "ACCEPTED":
                if method == "ISSUE":
                    operator = self._operators.get(event.operator_uid)
                    if (
                        self._grant is not None or operator is None or operator[0] != self._session[4]
                        or "requester" not in operator[1] or grant is None or event.grant_id != grant.grant_id
                        or (str(grant.session_id), str(grant.run_id), grant.agent_uid, grant.agent_id,
                            grant.tool, grant.resource, grant.environment)
                        != (self._session[0], self._session[1], self._session[3], *self._session[5:9])
                    ):
                        raise ControllerStateError("Controller issue does not match the protected session")
                    updated = self._bound_grant(grant, event.operator_uid, operator[0], "ACTIVE")
                    self._connection.execute(
                        "INSERT INTO controller_grants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", updated,
                    )
                elif method in ("REVOKE", "INSPECT"):
                    self._check_grant(grant)
                    if str(event.grant_id) != self._grant[0] or event.operator_uid != self._grant[3]:
                        raise ControllerStateError("Controller operator does not own the protected grant")
                    if method == "REVOKE":
                        updated = self._terminal_grant(grant, "REVOKED")
                else:
                    raise ControllerStateError("Controller attempted an unexpected grant operation")
            self._record(event.event_id, event.session_id, event.grant_id, event.operator_uid,
                         "OPERATOR", method, decision)
        self._grant = updated

    def _terminal_grant(self, grant, state):
        self._check_grant(grant, live=True)
        updated = self._connection.execute(
            "UPDATE controller_grants SET state = ? WHERE grant_id = ? AND state = 'ACTIVE'",
            (state, str(grant.grant_id)),
        )
        if updated.rowcount != 1:
            raise ControllerStateError("Controller grant is no longer live")
        return (*self._grant[:-1], state)

    def expire(self, grant):
        with self._write():
            self._verify_current_session(grant.session_id)
            updated = self._terminal_grant(grant, "EXPIRED")
            self._record(uuid4(), grant.session_id, grant.grant_id, None, "LIFECYCLE", "EXPIRE", "EXPIRED")
        self._grant = updated

    def record_admission(self, event, grant):
        with self._write():
            self._verify_current_session(event.session_id)
            if event.decision.name == "PROPOSAL_ALLOWED":
                self._check_grant(grant, live=True)
                if (
                    event.peer_uid != grant.agent_uid or event.agent_id != grant.agent_id
                    or event.run_id != grant.run_id or event.grant_id != grant.grant_id
                    or event.grant_matched is not True or getattr(event.policy_decision, "value", None) != "ALLOW"
                ):
                    raise ControllerStateError("Controller admission does not match the protected grant and policy")
            self._record(
                event.event_id, event.session_id, event.grant_id, event.peer_uid, "ADMISSION", None,
                event.decision.name, event.reason.value, event.grant_matched,
                event.policy_decision.value if event.policy_decision else None,
            )


@contextmanager
def _open_controller_state(state_dir, control_uid, *, schema=_SCHEMA, version=_SCHEMA_VERSION):
    with _state_lease(state_dir, control_uid) as directory_fd:
        with _database(directory_fd, control_uid) as connection:
            _verify_schema(connection, schema, version)
            yield _ControllerStore(connection)
