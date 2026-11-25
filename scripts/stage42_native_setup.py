"""Stage the reviewed native harness inside the disposable runner's offline namespace."""

from io import BytesIO
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZIP_STORED, ZipFile


SOURCES = (
    ".github/ci-requirements.txt",
    "adaptive_agentops/__init__.py",
    "adaptive_agentops/identity.py",
    "adaptive_agentops/policy.py",
    "adaptive_agentops/tool_requests.py",
    "adaptive_agentops/stage4_boundary/__init__.py",
    "adaptive_agentops/stage4_boundary/delegation.py",
    "scripts/stage4_peer_probe.py",
    "scripts/stage4_native_acceptance.py",
    "scripts/stage42_native_acceptance.py",
    "scripts/stage42_native_probe.py",
)
DRIVER = "scripts/stage42_native_acceptance.py"
WHEEL_NAME = re.compile(
    r"(annotated_types|pydantic|pydantic_core|typing_extensions|typing_inspection)"
    r"-[a-zA-Z0-9_.-]{1,180}\.whl"
)
WHEEL_COUNT = 5
SETUP_FAILED = 78
PROBE_FAILED = 79
SOURCE_LIMIT = 128 * 1024
WHEEL_LIMIT = 10 * 1024**2
BUNDLE_LIMIT = len(SOURCES) * SOURCE_LIMIT + WHEEL_COUNT * WHEEL_LIMIT + 16384


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_directory(path, exact_mode=None):
    try:
        info = path.lstat()
    except OSError as error:
        raise RuntimeError(f"{path}: metadata unavailable (errno={error.errno})") from None
    mode = stat.S_IMODE(info.st_mode)
    expected = ("other search access and no group or other write access" if exact_mode is None
                    else f"mode {exact_mode:04o}")
    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0)
            or (mode & 0o023 != 0o001 if exact_mode is None else mode != exact_mode)):
        raise RuntimeError(
            f"{path}: expected root:root directory with {expected} "
            f"(uid={info.st_uid} gid={info.st_gid} mode={mode:04o} type={stat.filemode(info.st_mode)[0]})"
        )


def git(*args):
    return subprocess.run(("/usr/bin/git", "--no-replace-objects", *args),
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout


def pack(revision, wheelhouse, output):
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "Expected exact commit SHA")
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED) as bundle:
        for name in SOURCES:
            entry = git("ls-tree", "--full-tree", revision, "--", name)
            require(entry.startswith(b"100644 blob ") and entry.endswith(b"\t" + name.encode() + b"\n"),
                    f"Expected committed regular file: {name}")
            data = git("cat-file", "blob", f"{revision}:{name}")
            require(len(data) <= SOURCE_LIMIT, f"Committed file exceeds staging bound: {name}")
            bundle.writestr(name, data)

        directory = os.open(wheelhouse, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            names = sorted(name for name in os.listdir(directory) if name != "requirements.txt")
            require(len(names) == WHEEL_COUNT and all(WHEEL_NAME.fullmatch(name) for name in names),
                    "Unexpected wheelhouse layout")
            for name in names:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "rb") as wheel:
                    info = os.fstat(wheel.fileno())
                    require(stat.S_ISREG(info.st_mode) and info.st_size <= WHEEL_LIMIT,
                            "Expected bounded regular wheel")
                    data = wheel.read(WHEEL_LIMIT + 1)
                    require(len(data) <= WHEEL_LIMIT, "Wheel grew beyond staging bound")
                    bundle.writestr(f"wheels/{name}", data)
        finally:
            os.close(directory)
    output.write(buffer.getbuffer())


def stage_bundle(source, stage):
    data = source.read(BUNDLE_LIMIT + 1)
    require(len(data) <= BUNDLE_LIMIT, "Staging bundle exceeds size limit")
    with ZipFile(BytesIO(data)) as bundle:
        entries = bundle.infolist()
        seen = set()
        for entry in entries:
            name = entry.filename
            wheel = name.startswith("wheels/") and WHEEL_NAME.fullmatch(name[7:]) is not None
            limit = WHEEL_LIMIT if wheel else SOURCE_LIMIT
            require((name in SOURCES or wheel) and name not in seen and name == entry.orig_filename
                    and entry.compress_type == ZIP_STORED and entry.file_size <= limit
                    and entry.file_size == entry.compress_size
                    and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG),
                    "Unexpected staging bundle entry")
            seen.add(name)
        require(set(SOURCES) <= seen and len(seen) == len(SOURCES) + WHEEL_COUNT,
                "Staging bundle is missing reviewed source or wheel artifacts")

        for entry in entries:
            target = stage / entry.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(bundle.read(entry))
            if entry.filename in SOURCES:
                target.chmod(0o644)
        for directory in {(stage / name).parent for name in SOURCES}:
            directory.chmod(0o755)
        stage.chmod(0o755)


def run_offline(source, run_parent):
    # Check the ancestors in order. Neither permits an unprivileged rename or replacement.
    validate_directory(run_parent.parent)
    validate_directory(run_parent)
    require("system.posix_acl_default" not in os.listxattr(run_parent, follow_symlinks=False),
            "/run: unexpected default ACL on staging parent")
    with TemporaryDirectory(prefix="aao-stage42-code.", dir=run_parent) as directory:
        stage = Path(directory)
        validate_directory(stage, 0o700)
        stage_bundle(source, stage)
        validate_directory(stage, 0o755)
        print("SETUP stage42 protected staging populated", flush=True)
        result = subprocess.run((sys.executable, "-I", "-B", "-S", str(stage / DRIVER), "run"),
                                cwd=stage, stdin=subprocess.DEVNULL)
        return result.returncode if result.returncode in (0, PROBE_FAILED) else SETUP_FAILED


def interrupted(signum, _frame):
    raise KeyboardInterrupt(f"signal {signum}")


def main():
    try:
        if sys.argv[1:2] == ["pack"] and len(sys.argv) == 4:
            pack(sys.argv[2], Path(sys.argv[3]), sys.stdout.buffer)
            return 0
        require(sys.argv[1:] == ["run"] and os.geteuid() == 0
                and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode,
                "Expected isolated root setup in the acceptance namespace")
        signal.signal(signal.SIGTERM, interrupted)
        os.umask(0o077)
        return run_offline(sys.stdin.buffer, Path("/run"))
    except (RuntimeError, OSError, BadZipFile, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        if isinstance(error, OSError):
            detail = f"filesystem or process operation failed (errno={error.errno})"
        elif isinstance(error, (BadZipFile, subprocess.CalledProcessError, KeyboardInterrupt)):
            detail = f"setup interrupted or input rejected ({type(error).__name__})"
        else:
            detail = str(error)
        print(f"::error title=Stage 4.2 setup failure::{detail}", file=sys.stderr)
        return SETUP_FAILED


if __name__ == "__main__":
    sys.exit(main())
