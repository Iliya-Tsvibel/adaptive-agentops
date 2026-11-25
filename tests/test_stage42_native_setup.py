from io import BytesIO
from pathlib import Path
import stat
from types import SimpleNamespace
from zipfile import ZipFile, ZipInfo

import pytest

from scripts import stage42_native_acceptance as driver
from scripts import stage42_native_setup as setup


WHEELS = [
    f"wheels/{name}-1.0-py3-none-any.whl"
    for name in ("annotated_types", "pydantic", "pydantic_core", "typing_extensions", "typing_inspection")
]


def make_bundle(*, omit=None, extra=None, sources=setup.SOURCES):
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        for name in [*sources, *WHEELS]:
            if name != omit:
                archive.writestr(name, b"fixture")
        if extra is not None:
            archive.writestr(extra, b"fixture")
    data.seek(0)
    return data


@pytest.mark.parametrize(("uid", "gid", "mode", "diagnostic"), [
    (0, 0, stat.S_IFDIR | 0o755, None),
    (123, 0, stat.S_IFDIR | 0o755, "uid=123"),
    (0, 123, stat.S_IFDIR | 0o755, "gid=123"),
    (0, 0, stat.S_IFDIR | 0o775, "mode=0775"),
    (0, 0, stat.S_IFDIR | 0o757, "mode=0757"),
    (0, 0, stat.S_IFDIR | 0o750, "mode=0750"),
    (0, 0, stat.S_IFLNK | 0o755, "type=l"),
    (0, 0, stat.S_IFREG | 0o755, "type=-"),
])
def test_parent_and_driver_require_safe_metadata(monkeypatch, uid, gid, mode, diagnostic):
    info = SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=mode)
    parent, stage = Path("/run"), Path("/run/aao-stage42-code.fixture")

    def metadata(path):
        if path == parent:
            return info
        expected = 0o700 if path.name == "wheels" else 0o755
        return SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | expected)

    monkeypatch.setattr(Path, "lstat", metadata)
    for validate, path in ((setup.validate_directory, parent), (driver.validate_staging, stage)):
        if diagnostic is None:
            validate(path)
        else:
            with pytest.raises(RuntimeError, match=diagnostic):
                validate(path)


def test_real_symlink_is_never_followed(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="type=l"):
        setup.validate_directory(link)


def test_parent_rejection_precedes_staging_or_launch(monkeypatch, tmp_path):
    examined = []

    def validate(path, exact_mode=None):
        examined.append(path)
        if path == tmp_path:
            raise RuntimeError("Unsafe parent fixture")

    monkeypatch.setattr(setup, "validate_directory", validate)
    with pytest.raises(RuntimeError, match="Unsafe parent fixture"):
        setup.run_offline(make_bundle(), tmp_path)
    assert examined == [tmp_path.parent, tmp_path]
    assert list(tmp_path.iterdir()) == []


def test_stage_bundle_populates_only_reviewed_paths(tmp_path):
    setup.stage_bundle(make_bundle(), tmp_path)
    assert {
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()
    } == set(setup.SOURCES) | set(WHEELS)
    assert (tmp_path / setup.DRIVER).stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("name", ["../outside", "/etc/outside", "wheels/../../outside"])
def test_bundle_path_substitution_is_rejected_before_writes(tmp_path, name):
    with pytest.raises(RuntimeError, match="Unexpected staging bundle entry"):
        setup.stage_bundle(make_bundle(extra=name), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_bundle_rejects_symlink_artifact(tmp_path):
    symlink = ZipInfo(setup.DRIVER)
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(RuntimeError, match="Unexpected staging bundle entry"):
        setup.stage_bundle(make_bundle(omit=setup.DRIVER, extra=symlink), tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("omitted", [setup.DRIVER, WHEELS[0]])
def test_missing_artifacts_fail_closed(tmp_path, omitted):
    with pytest.raises(RuntimeError, match="missing reviewed source or wheel artifacts"):
        setup.stage_bundle(make_bundle(omit=omitted), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_pack_rejects_symlink_wheel(monkeypatch, tmp_path):
    def committed(*args):
        return (b"100644 blob fixture\t" + args[-1].encode() + b"\n"
                if args[0] == "ls-tree" else b"fixture")

    monkeypatch.setattr(setup, "git", committed)
    for name in WHEELS[1:]:
        (tmp_path / Path(name).name).write_bytes(b"fixture")
    (tmp_path / Path(WHEELS[0]).name).symlink_to(tmp_path / Path(WHEELS[1]).name)
    output = BytesIO()
    with pytest.raises(OSError):
        setup.pack("a" * 40, tmp_path, output)
    assert output.getvalue() == b""


def test_partial_staging_failure_cleans_up(monkeypatch, tmp_path):
    def fail_copy(source, stage):
        (stage / "partial").touch()
        raise OSError("copy fixture failed")

    monkeypatch.setattr(setup, "validate_directory", lambda *args: None)
    monkeypatch.setattr(setup, "stage_bundle", fail_copy)
    with pytest.raises(OSError, match="copy fixture failed"):
        setup.run_offline(make_bundle(), tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(("child_status", "expected"), [(0, 0), (78, 78), (79, 79), (1, 78)])
def test_driver_status_and_cleanup(monkeypatch, tmp_path, child_status, expected):
    def launch(command, *, cwd, stdin):
        assert command[1:4] == ("-I", "-B", "-S")
        assert Path(command[4]) == cwd / setup.DRIVER
        assert Path(command[4]).is_file()
        assert stdin == setup.subprocess.DEVNULL
        return SimpleNamespace(returncode=child_status)

    monkeypatch.setattr(setup, "validate_directory", lambda *args: None)
    monkeypatch.setattr(setup.subprocess, "run", launch)
    assert setup.run_offline(make_bundle(), tmp_path) == expected
    assert list(tmp_path.iterdir()) == []


def test_driver_setup_failure_emits_no_probe_evidence(monkeypatch, capsys):
    def fail_setup():
        raise RuntimeError("Wheel hash mismatch for pydantic")

    monkeypatch.setattr(driver, "prepare", fail_setup)
    assert driver.run() == setup.SETUP_FAILED
    output = capsys.readouterr()
    assert not output.out
    assert "Stage 4.2 setup failure::Wheel hash mismatch for pydantic" in output.err


def test_inherited_default_acl_stops_staging(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "validate_directory", lambda *args: None)
    monkeypatch.setattr(setup.os, "listxattr", lambda *args, **kwargs: ["system.posix_acl_default"])
    with pytest.raises(RuntimeError, match="unexpected default ACL"):
        setup.run_offline(make_bundle(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_pack_rejects_committed_symlink_source(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "git", lambda *args: b"120000 blob symlink\t" + args[-1].encode() + b"\n")
    output = BytesIO()
    with pytest.raises(RuntimeError, match="Expected committed regular file"):
        setup.pack("a" * 40, tmp_path, output)
    assert output.getvalue() == b""


def test_pack_and_stage_use_revision_bound_source(monkeypatch, tmp_path):
    revision = "a" * 40

    def committed(*args):
        if args[0] == "ls-tree":
            assert args == ("ls-tree", "--full-tree", revision, "--", args[-1])
            return b"100644 blob fixture\t" + args[-1].encode() + b"\n"
        assert args[:2] == ("cat-file", "blob")
        commit, name = args[2].split(":", 1)
        assert commit == revision
        return f"Reviewed bytes of {name}".encode()

    monkeypatch.setattr(setup, "git", committed)
    wheelhouse, stage = tmp_path / "wheels", tmp_path / "stage"
    wheelhouse.mkdir()
    stage.mkdir()
    for name in WHEELS:
        (wheelhouse / Path(name).name).write_bytes(b"wheel fixture")
    output = BytesIO()
    setup.pack(revision, wheelhouse, output)
    output.seek(0)
    setup.stage_bundle(output, stage)
    assert (stage / setup.DRIVER).read_text() == f"Reviewed bytes of {setup.DRIVER}"
    assert (stage / WHEELS[0]).read_bytes() == b"wheel fixture"


@pytest.mark.parametrize("failure", ["legacy", "proposal", None])
def test_probe_start_follows_both_native_setups(monkeypatch, capsys, failure):
    from contextlib import contextmanager
    from scripts import stage4_native_acceptance as legacy

    actions = []

    @contextmanager
    def prepare_legacy():
        actions.append("legacy setup")
        if failure == "legacy":
            raise RuntimeError("Legacy infrastructure unavailable")
        try:
            yield "agent", "target"
        finally:
            actions.append("legacy cleanup")

    @contextmanager
    def prepare_proposal(_legacy):
        actions.append("proposal setup")
        if failure == "proposal":
            raise RuntimeError("Proposal infrastructure unavailable")
        try:
            yield "proposal"
        finally:
            actions.append("proposal cleanup")

    monkeypatch.setattr(driver.sys, "path", driver.sys.path.copy())
    monkeypatch.setattr(driver, "prepare", lambda: None)
    monkeypatch.setattr(legacy, "prepare_native", prepare_legacy)
    monkeypatch.setattr(driver, "proposal_socket", prepare_proposal)
    monkeypatch.setattr(legacy, "run_probes", lambda agent, target: actions.append((agent, target)))
    for check in ("check_live_session", "check_denials", "check_wrong_server", "check_evidence_failure"):
        monkeypatch.setattr(driver, check, lambda _legacy, path: actions.append(path))

    assert driver.run() == (setup.SETUP_FAILED if failure else 0)
    output = capsys.readouterr()
    if failure:
        assert output.out == ""
        assert "Stage 4.2 setup failure" in output.err
        assert actions == (["legacy setup"] if failure == "legacy" else
                           ["legacy setup", "proposal setup", "legacy cleanup"])
    else:
        assert output.out.strip() == "START stage42 native security probes"
        assert actions == [
            "legacy setup", "proposal setup", ("agent", "target"), *["proposal"] * 4,
            "proposal cleanup", "legacy cleanup",
        ]


def test_stage43_is_an_explicit_exact_native_source_allowlist(tmp_path):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    with pytest.raises(RuntimeError, match="Unexpected staging bundle entry"):
        setup.stage_bundle(make_bundle(sources=setup.STAGE43_SOURCES), baseline)
    assert not list(baseline.iterdir())
    with pytest.raises(RuntimeError, match="missing reviewed source"):
        setup.stage_bundle(make_bundle(), baseline, setup.STAGE43_SOURCES)
    assert not list(baseline.iterdir())

    setup.stage_bundle(make_bundle(sources=setup.STAGE43_SOURCES), baseline, setup.STAGE43_SOURCES)
    assert {
        path.relative_to(baseline).as_posix() for path in baseline.rglob("*") if path.is_file()
    } == set(setup.STAGE43_SOURCES) | set(WHEELS)
