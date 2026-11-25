"""Protected Stage 4 simulated target state and authenticated receipts."""

from contextlib import contextmanager, suppress
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, UUID4, model_validator

from . import (
    Endpoint, ProtocolError, TrustedUIDs, _EXCHANGE_SECONDS, _connect_verified, _listen, _remaining,
    _require_local_uid, _require_peer, _send_frame, monotonic,
)
from . import controller_state, delegation, dispatch_wire


DATABASE_NAME = "target.sqlite3"
_APPLICATION_ID = 0x41414F54
_SCHEMA_VERSION = 1
_MAX_CLAIMS = 10_000
_MAX_DB_BYTES = 64 * 1024 * 1024
# A bounded DELETE journal can hold one record per database page plus headers.
_MAX_JOURNAL_BYTES = 2 * _MAX_DB_BYTES
_SCHEMA = (
    """CREATE TABLE simulated_target (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    effect_count INTEGER NOT NULL CHECK (effect_count >= 0)
) STRICT""",
    """CREATE TABLE target_claims (
    dispatch_id TEXT PRIMARY KEY NOT NULL CHECK (length(dispatch_id) = 36),
    action_id TEXT UNIQUE NOT NULL CHECK (length(action_id) = 36),
    request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
    effect_number INTEGER UNIQUE CHECK (effect_number > 0)
) STRICT""",
)
_REQUEST = b"AAO\x05\x01"
_RECEIPT = b"AAO\x05\x02"
_LOOKUP = b"AAO\x05\x03"
_LOOKUP_REPLY = b"AAO\x05\x04"


class TargetStateError(ProtocolError):
    """Target storage could not establish safe effect or receipt authority."""


class DuplicateRequest(TargetStateError):
    """A dispatch ID or action ID already has a durable replay claim."""


class ReceiptKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dispatch_id: UUID4
    action_id: UUID4
    request_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class EffectReceipt(ReceiptKey):
    effect_number: Annotated[int, Field(gt=0, le=_MAX_CLAIMS)]


class ReceiptResult(ReceiptKey):
    status: Literal["UNKNOWN", "COMMITTED"]
    effect_number: Annotated[int, Field(gt=0, le=_MAX_CLAIMS)] | None


    @model_validator(mode="after")
    def check_status(self):
        if (self.status == "COMMITTED") != (self.effect_number is not None):
            raise ValueError("Receipt status and effect number disagree")
        return self


def _request_key(envelope):
    return _key(envelope, dispatch_wire._encode_frame(_REQUEST, envelope))


def _key(envelope, frame):
    return ReceiptKey(
        dispatch_id=envelope.dispatch_id, action_id=envelope.action_id,
        request_digest=sha256(frame).hexdigest(),
    )


def _values(key):
    return str(key.dispatch_id), str(key.action_id), key.request_digest


def _initialize(connection):
    connection.execute("INSERT INTO simulated_target VALUES (1, 0)")


def _verify(connection):
    controller_state._verify_schema_metadata(connection, _SCHEMA, _SCHEMA_VERSION, _APPLICATION_ID)
    count, effects, latest = connection.execute(
        "SELECT count(*), count(effect_number), coalesce(max(effect_number), 0) FROM target_claims"
    ).fetchone()
    if (
        count > _MAX_CLAIMS or latest != effects
        or connection.execute("SELECT effect_count FROM simulated_target").fetchall() != [(effects,)]
    ):
        raise TargetStateError("Target effect and replay evidence are inconsistent")
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    max_pages = _MAX_DB_BYTES // page_size
    if connection.execute(f"PRAGMA max_page_count = {max_pages}").fetchone() != (max_pages,):
        raise TargetStateError("Target database exceeds its page budget")


def provision_target_state(state_dir: str | Path, target_uid: int):
    """Create a new isolated target instance in an explicitly empty directory."""
    controller_state._provision_state(
        state_dir, target_uid, DATABASE_NAME, _SCHEMA, _SCHEMA_VERSION, _APPLICATION_ID, _verify, _initialize,
    )


@contextmanager
def _open_target_state(state_dir, target_uid):
    with controller_state._state_lease(state_dir, target_uid) as directory_fd:
        for name, max_size in ((DATABASE_NAME, _MAX_DB_BYTES), (DATABASE_NAME + "-journal", _MAX_JOURNAL_BYTES)):
            with suppress(FileNotFoundError):
                if os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_size > max_size:
                    raise TargetStateError("Target database or journal exceeds its storage budget")
        with controller_state._database(directory_fd, target_uid, DATABASE_NAME) as connection:
            _verify(connection)
            yield connection


@contextmanager
def _write(connection):
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield
        controller_state._commit(connection)
    except BaseException:
        if connection.in_transaction:
            with suppress(sqlite3.Error):
                connection.rollback()
        raise


def _apply(connection, key, check_live):
    values = _values(key)
    with _write(connection):
        if connection.execute(
            "SELECT 1 FROM target_claims WHERE dispatch_id = ? OR action_id = ?", values[:2],
        ).fetchone():
            raise DuplicateRequest("Target already claimed the dispatch or action")
        check_live()
        if connection.execute("SELECT count(*) FROM target_claims").fetchone()[0] >= _MAX_CLAIMS:
            raise TargetStateError("Target replay evidence capacity exhausted")
        connection.execute("INSERT INTO target_claims VALUES (?, ?, ?, NULL)", values)

    # Only this invocation can advance its confirmed claim. A restart or an
    # uncertain claim commit has no route into this transaction.
    with _write(connection):
        check_live()
        (number,) = connection.execute(
            "UPDATE simulated_target SET effect_count = effect_count + 1 WHERE singleton = 1 RETURNING effect_count"
        ).fetchone()
        updated = connection.execute(
            "UPDATE target_claims SET effect_number = ? "
            "WHERE dispatch_id = ? AND action_id = ? AND request_digest = ? AND effect_number IS NULL",
            (number, *values),
        )
        if updated.rowcount != 1:
            raise TargetStateError("Target claim changed before the simulated effect")
    return EffectReceipt(**key.model_dump(), effect_number=number)


def _lookup(connection, key):
    row = connection.execute(
        "SELECT effect_number FROM target_claims WHERE dispatch_id = ? AND action_id = ? AND request_digest = ?",
        _values(key),
    ).fetchone()
    number = None if row is None else row[0]
    return ReceiptResult(
        **key.model_dump(), status="UNKNOWN" if number is None else "COMMITTED", effect_number=number,
    )


def _exchange(connection, deadline, envelope, *, clock):
    wire = dispatch_wire
    frame = wire._encode_frame(_REQUEST, envelope)
    key = _key(envelope, frame)
    wire._require_live(envelope, clock.sample())
    _send_frame(connection, frame, deadline)
    reply = delegation._read_frame(connection, wire._FRAME_MAX, deadline)
    receipt = wire._parse_frame(reply, _RECEIPT, EffectReceipt)
    if _values(receipt) != _values(key):
        raise ProtocolError("Target effect receipt correlation failed")
    return receipt


def lookup_target_receipt(endpoint: Endpoint, uids: TrustedUIDs, key: ReceiptKey) -> ReceiptResult:
    """Read a correlated receipt over authenticated IPC, without reserving or sending a mutation."""
    wire = dispatch_wire
    wire._require_dispatch_link(endpoint)
    with _connect_verified(endpoint, uids) as (peer, deadline):
        _send_frame(peer, wire._encode_frame(_LOOKUP, key), deadline)
        frame = delegation._read_frame(peer, wire._FRAME_MAX, deadline)
        result = wire._parse_frame(frame, _LOOKUP_REPLY, ReceiptResult)
        if _values(result) != _values(key):
            raise ProtocolError("Target receipt lookup correlation failed")
        return result


def serve_target_once(endpoint: Endpoint, uids: TrustedUIDs, state_dir: str | Path):
    """Handle one authenticated effect or read-only lookup on the target socket."""
    wire = dispatch_wire
    wire._require_dispatch_link(endpoint)
    client_uid, target_uid = endpoint._uids(uids)
    _require_local_uid(target_uid)
    clock = delegation._SessionClock()
    with _open_target_state(state_dir, target_uid) as state, _listen(endpoint, target_uid) as listener:
        peer, _address = listener.accept()
        with peer:
            deadline = monotonic() + _EXCHANGE_SECONDS
            _require_peer(peer, client_uid)
            frame = delegation._read_frame(peer, wire._FRAME_MAX, deadline)
            if frame.startswith(_LOOKUP):
                key = wire._parse_frame(frame, _LOOKUP, ReceiptKey)
                reply = _lookup(state, key)
                prefix = _LOOKUP_REPLY
            else:
                envelope = wire._parse_frame(frame, _REQUEST, wire.DispatchEnvelope)

                def check_live():
                    _remaining(deadline)
                    wire._require_live(envelope, clock.sample())

                reply = _apply(state, _key(envelope, frame), check_live)
                prefix = _RECEIPT
            _send_frame(peer, wire._encode_frame(prefix, reply), deadline)
            return reply
