from hashlib import file_digest
from pathlib import Path
import stat
import sys
from zipfile import ZipFile, ZipInfo

import pytest

from scripts import stage42_native_acceptance as harness
from scripts import stage42_native_probe as probe


def stage_wheel(tmp_path, *members):
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    wheel = wheelhouse / "pydantic-1.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        for member in members:
            archive.writestr(member, b"fixture")
    with wheel.open("rb") as archive:
        digest = file_digest(archive, "sha256").hexdigest()
    return wheelhouse, {"pydantic": f"pydantic==1.0 --hash=sha256:{digest}"}


def test_dependency_subset_comes_from_the_existing_reviewed_manifest():
    pins = harness.dependency_pins(harness.STAGE / ".github/ci-requirements.txt")
    assert set(pins) == {
        "annotated-types", "pydantic", "pydantic-core", "typing-extensions", "typing-inspection",
    }


def test_missing_and_duplicate_pins_fail_closed(tmp_path):
    manifest = tmp_path / "pins"
    manifest.write_text("pydantic==1.0 --hash=sha256:" + "a" * 64 + "\n")
    with pytest.raises(RuntimeError, match="missing"):
        harness.dependency_pins(manifest)
    manifest.write_text(manifest.read_text() * 2)
    with pytest.raises(RuntimeError, match="duplicate"):
        harness.dependency_pins(manifest)


def test_exact_wheel_bytes_are_required(tmp_path):
    wheelhouse, pins = stage_wheel(tmp_path, "pydantic/__init__.py")
    dest = tmp_path / "deps"
    harness.extract_wheels(wheelhouse, dest, pins)
    assert (dest / "pydantic/__init__.py").read_bytes() == b"fixture"
    wheel, = wheelhouse.iterdir()
    with wheel.open("ab") as archive:
        archive.write(b"tampered")
    rejected = tmp_path / "rejected"
    with pytest.raises(RuntimeError, match="hash mismatch"):
        harness.extract_wheels(wheelhouse, rejected, pins)
    assert not list(rejected.iterdir())


@pytest.mark.parametrize("member", ["../outside", "/outside", "a\\b", "pydantic.data/purelib/unowned.py", "local.pth"])
def test_even_hash_matched_wheel_members_must_be_safe(tmp_path, member):
    wheelhouse, pins = stage_wheel(tmp_path, member)
    with pytest.raises(RuntimeError, match="Unsafe or unsupported wheel member"):
        harness.extract_wheels(wheelhouse, tmp_path / "deps", pins)
    assert not (tmp_path / "outside").exists()


def test_symlinks_in_wheels_are_rejected(tmp_path):
    member = ZipInfo("pydantic/link")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    wheelhouse, pins = stage_wheel(tmp_path, member)
    with pytest.raises(RuntimeError, match="Unsafe or unsupported wheel member"):
        harness.extract_wheels(wheelhouse, tmp_path / "deps", pins)


def test_extra_wheels_are_rejected(tmp_path):
    wheelhouse, pins = stage_wheel(tmp_path, "pydantic/__init__.py")
    (wheelhouse / "extra-1.0-py3-none-any.whl").touch()
    with pytest.raises(RuntimeError, match="Unexpected wheelhouse entry"):
        harness.extract_wheels(wheelhouse, tmp_path / "deps", pins)


def test_probe_invocation_ignores_site_and_environment():
    command = harness.probe("server", Path("/run/fixture.sock"), "live")
    assert command[:4] == (sys.executable, "-I", "-B", "-S")
    assert Path(command[4]).is_absolute()


def test_effect_check_rejects_even_a_swallowed_outbound_attempt(monkeypatch):
    hooks = []
    monkeypatch.setattr(sys, "addaudithook", hooks.append)
    verify = probe.effect_guard()
    with pytest.raises(RuntimeError, match="attempted an effect"):
        hooks[0]("socket.connect", ())
    with pytest.raises(RuntimeError, match="execution path was reached"):
        verify()
