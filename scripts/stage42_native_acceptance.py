"""Root orchestrator for the separately authorized Stage 4.2 VM workflow."""

from contextlib import ExitStack, contextmanager
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
from shutil import copyfileobj
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from traceback import print_exc
from zipfile import BadZipFile, ZipFile


PACKAGES = frozenset({
    "annotated-types", "pydantic", "pydantic-core", "typing-extensions", "typing-inspection",
})
PIN = re.compile(r"([a-z-]+)==[0-9]+(?:\.[0-9]+)+ --hash=sha256:([0-9a-f]{64})")
FRAME = b'AAO\x02\x01{"tool":"mock.get_health","resource":"demo-api","arguments":{}}'
STAGE = Path(__file__).absolute().parents[1]
CODE_PARENT = Path("/var/lib")
SOCKET_PARENT = Path("/run")
SETUP_FAILED = 78
PROBE_FAILED = 79


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def dependency_pins(manifest):
    pins = {}
    for line in manifest.read_text(encoding="ascii").splitlines():
        name = line.partition("==")[0]
        if name not in PACKAGES:
            continue
        require(PIN.fullmatch(line) and name not in pins, f"Invalid or duplicate pin for {name}")
        pins[name] = line
    require(pins.keys() == PACKAGES, "An acceptance wheel pin is missing")
    return pins


def extract_wheels(wheelhouse, destination, pins):
    """Verify the staged archive bytes before copying regular wheel members."""
    wheels = {}
    for wheel in wheelhouse.iterdir():
        name = wheel.name.partition("-")[0].replace("_", "-")
        info = wheel.lstat()
        require(wheel.suffix == ".whl" and name in pins and name not in wheels
                and stat.S_ISREG(info.st_mode) and info.st_size <= 10 * 1024**2,
                "Unexpected wheelhouse entry")
        wheels[name] = wheel
    require(wheels.keys() == pins.keys(), "Wheelhouse does not match the acceptance pins")

    destination.mkdir(mode=0o755)
    for name, wheel in sorted(wheels.items()):
        with wheel.open("rb") as archive:
            digest = pins[name].rsplit(":", 1)[1]
            require(file_digest(archive, "sha256").hexdigest() == digest, f"Wheel hash mismatch for {name}")
            archive.seek(0)
            with ZipFile(archive) as package:
                members = package.infolist()
                require(len(members) <= 4096 and sum(item.file_size for item in members) <= 32 * 1024**2,
                        "Wheel exceeds extraction limits")
                for item in members:
                    kind = stat.S_IFDIR if item.is_dir() else stat.S_IFREG
                    parts = item.filename.removesuffix("/").split("/")
                    require(stat.S_IFMT(item.external_attr >> 16) in (0, kind)
                            and "\\" not in item.filename and item.orig_filename == item.filename
                            and all(part not in ("", ".", "..") for part in parts)
                            and not parts[0].endswith(".data") and not parts[-1].endswith(".pth"),
                            "Unsafe or unsupported wheel member")
                    target = destination.joinpath(*parts)
                    if item.is_dir():
                        target.mkdir(mode=0o755, parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                        with package.open(item) as source, target.open("xb") as output:
                            copyfileobj(source, output)


def probe(mode, path, *args):
    return (sys.executable, "-I", "-B", "-S", str(STAGE / "scripts/stage42_native_probe.py"),
            mode, str(path), *args)


def client(legacy, path, expected, *args):
    result = subprocess.run(probe("client", path, *args), **legacy.identity(legacy.AGENT),
                            capture_output=True, text=True, timeout=5)
    require(result.returncode == 0 and result.stdout.strip() == expected and not result.stderr,
            f"Expected {expected}, got exit {result.returncode}: {result.stdout}{result.stderr}")


def raw(legacy, uid, path, mode, frame, groups=None):
    subprocess.run(probe(mode, path, frame.hex()), **legacy.identity(uid, groups),
                   check=True, timeout=5, stdin=subprocess.DEVNULL)


@contextmanager
def server(legacy, path, scenario):
    with legacy.listening_process(probe("server", path, scenario), legacy.CONTROL, path) as process:
        legacy.check_socket_metadata(path, legacy.CONTROL, legacy.PROPOSERS)
        yield process
        require(not os.path.lexists(path), f"{scenario} admission socket was not cleaned up")


def finish(process, timeout=14):
    output, _ = process.communicate(timeout=timeout)
    require(process.returncode == 0, f"Admission server exited {process.returncode}: {output}")
    return json.loads(output)


def check_events(events, expected, legacy):
    require(len(events) == len(expected), "Unexpected connection journal length")
    require(len({event["session"] for event in events}) == 1, "Journal spans multiple sessions")
    for event, (reason, decision, grant, policy) in zip(events, expected, strict=True):
        require((event["reason"], event["decision"], event["grant"], event["policy"])
                == (reason, decision, grant, policy), f"Unexpected admission evidence: {event}")
        expected_uid = None if reason == "PEER_REJECTED" else legacy.AGENT
        require(event["peer_uid"] == expected_uid, "Wrong UID in admission journal")


ADMITTED = ("ADMITTED", "PROPOSAL_ALLOWED", True, "ALLOW")
OUT_OF_SCOPE = ("GRANT_SCOPE_MISMATCH", "DENY", False, None)
REJECTED_PEER = ("PEER_REJECTED", "DENY", None, None)
INVALID_PROPOSAL = ("INVALID_PROPOSAL", "DENY", None, None)
TRANSPORT_ERROR = ("TRANSPORT_ERROR", "DENY", None, None)


def check_live_session(legacy, path):
    with server(legacy, path, "live") as process:
        client(legacy, path, "PROPOSAL_ALLOWED")
        client(legacy, path, "DENY", "other-api")
        raw(legacy, legacy.TARGET, path, "wrong-agent", FRAME, (legacy.PROPOSERS,))
        raw(legacy, legacy.AGENT, path, "expect-eof", FRAME + FRAME)
        raw(legacy, legacy.AGENT, path, "stall-before-eof", FRAME)
        for _ in range(11):
            client(legacy, path, "PROPOSAL_ALLOWED")
        events = finish(process, timeout=5)
        check_events(events, [
            ADMITTED, OUT_OF_SCOPE, REJECTED_PEER, INVALID_PROPOSAL, TRANSPORT_ERROR,
        ] + [ADMITTED] * 11, legacy)
        require(len({event["grant_id"] for event in events if event["peer_uid"] is not None}) == 1
                and events[0]["grant_id"] is not None, "Reconnect changed or lost the session grant")
    client(legacy, path, "NO_SOCKET")
    print("PASS 4.2 distinct UID proposal admission and no execution", flush=True)
    print("PASS 4.2 wrong agent UID with socket reachability", flush=True)
    print("PASS 4.2 framing and transport rejection and reconnect budget enforced", flush=True)


def check_denials(legacy, path):
    for scenario, decision in (
        ("missing", ("MISSING_GRANT", "DENY", False, None)),
        ("expired", ("GRANT_EXPIRED", "DENY", False, None)),
        ("policy-deny", ("POLICY_DENIED", "DENY", True, "DENY")),
    ):
        with server(legacy, path, scenario) as process:
            client(legacy, path, "DENY")
            check_events(finish(process), [decision], legacy)
    print("PASS 4.2 missing expired and out-of-scope delegation and PDP denial", flush=True)


def check_wrong_server(legacy, path):
    with TemporaryDirectory(prefix="wrong-", dir=path.parent.parent) as directory:
        staging = Path(directory)
        os.chown(staging, legacy.TARGET, legacy.TARGET)
        staged = staging / path.name
        with legacy.listening_process(probe("wrong-server", staged), legacy.TARGET, staged) as process:
            fd = os.open(staged, os.O_PATH | os.O_NOFOLLOW)
            try:
                original = os.fstat(fd)
                require(stat.S_ISSOCK(original.st_mode) and original.st_uid == legacy.TARGET,
                        "Unexpected wrong-server socket inode")
                pinned = f"/proc/self/fd/{fd}"
                os.chown(pinned, legacy.CONTROL, legacy.PROPOSERS)
                os.chmod(pinned, 0o660)
                staged.rename(path)
                try:
                    current = path.lstat()
                    require((current.st_dev, current.st_ino) == (original.st_dev, original.st_ino),
                            "Wrong-server socket changed during staging")
                    legacy.check_socket_metadata(path, legacy.CONTROL, legacy.PROPOSERS)
                    client(legacy, path, "WRONG_SERVER")
                    require(finish(process, timeout=5) == "NO_PROPOSAL_BYTES",
                            "Wrong-UID server did not confirm zero proposal bytes")
                finally:
                    path.unlink()
            finally:
                os.close(fd)
    print("PASS 4.2 wrong server UID received zero proposal bytes", flush=True)


def check_evidence_failure(legacy, path):
    with server(legacy, path, "audit-failure") as process:
        raw(legacy, legacy.AGENT, path, "expect-eof", FRAME)
        require(finish(process, timeout=5) == "INJECTED_JOURNAL_FAILURE", "Expected journal failure")
    with server(legacy, path, "post-audit-expiry") as process:
        raw(legacy, legacy.AGENT, path, "expect-eof", FRAME)
        check_events(finish(process), [ADMITTED], legacy)
    print("PASS 4.2 journal failure and recorded decision without a delivered verdict", flush=True)


def validate_staging(stage):
    directories = [(path, None) for path in reversed(stage.parents)]
    directories.extend(((stage, 0o755), (stage / "wheels", 0o700), (SOCKET_PARENT, None)))
    for path, exact_mode in directories:
        try:
            info = path.lstat()
        except OSError as error:
            raise RuntimeError(f"{path}: metadata unavailable (errno={error.errno})") from None
        mode = stat.S_IMODE(info.st_mode)
        expected = ("other search access and no group or other write access" if exact_mode is None
                    else f"mode {exact_mode:04o}")
        require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == (0, 0)
                and (mode & 0o023 == 0o001 if exact_mode is None else mode == exact_mode),
                f"{path}: expected root:root directory with {expected} "
                f"(uid={info.st_uid} gid={info.st_gid} mode={mode:04o} type={stat.filemode(info.st_mode)[0]})")


def prepare():
    require(os.geteuid() == 0 and STAGE.parent == CODE_PARENT
            and STAGE.name.startswith("aao-stage42-code.") and sys.flags.isolated
            and sys.flags.no_site and sys.flags.dont_write_bytecode,
            "Expected isolated root driver in /var/lib staging")
    validate_staging(STAGE)
    for path in (STAGE, SOCKET_PARENT):
        require("system.posix_acl_default" not in os.listxattr(path, follow_symlinks=False),
                f"{path}: unexpected default ACL")
        flags = os.statvfs(path).f_flag
        require(not flags & os.ST_RDONLY, f"{path}: read-only acceptance mount")
        if path == STAGE:
            require(not flags & os.ST_NOEXEC, f"{path}: native dependencies require a mount without noexec")
    require(Path.cwd() == STAGE, "Expected working directory to match the validated staging directory")
    os.umask(0o022)
    extract_wheels(STAGE / "wheels", STAGE / "deps", dependency_pins(STAGE / ".github/ci-requirements.txt"))


def verify_dependency_runtime(legacy):
    result = subprocess.run(
        probe("dependency-preflight", STAGE), **legacy.identity(legacy.CONTROL),
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5,
    )
    if result.returncode != 0 or result.stdout != "DEPENDENCIES_READY\n" or result.stderr:
        reason = (
            "shared object executable segment could not be mapped"
            if "failed to map segment from shared object" in result.stderr
            else "isolated dependency import did not complete"
        )
        raise RuntimeError(f"Native dependency preflight failed: {reason} (exit={result.returncode})")


@contextmanager
def proposal_socket(legacy):
    with TemporaryDirectory(prefix="aao-stage42-", dir=SOCKET_PARENT) as directory:
        root = Path(directory)
        root.chmod(0o755)
        socket_dir = root / "agent"
        socket_dir.mkdir(mode=0o750)
        os.chown(socket_dir, legacy.CONTROL, legacy.PROPOSERS)
        yield socket_dir / "proposal.sock"


def run(*, selected=42):
    with ExitStack() as resources:
        try:
            prepare()
            sys.path.insert(0, str(STAGE))
            from scripts import stage4_native_acceptance as legacy

            legacy_paths = resources.enter_context(legacy.prepare_native())
            verify_dependency_runtime(legacy)
            path = resources.enter_context(proposal_socket(legacy))
            if selected >= 43:
                from scripts import stage43_native_acceptance

                managed_paths = resources.enter_context(stage43_native_acceptance.prepare(legacy))
            if selected >= 44:
                from scripts import stage44_native_acceptance

                protected_paths = resources.enter_context(stage44_native_acceptance.prepare(legacy))
            if selected >= 45:
                from scripts import stage45_native_acceptance

                dispatch_paths = resources.enter_context(stage45_native_acceptance.prepare(legacy))
            if selected >= 46:
                from scripts import stage46_native_acceptance

                effect_paths = resources.enter_context(stage46_native_acceptance.prepare(legacy))
            if selected >= 47:
                from scripts import stage47_native_acceptance

                hardened_paths = resources.enter_context(stage47_native_acceptance.prepare(legacy))
            if selected == 60:
                from scripts import stage6e_native_acceptance

                evidence_paths = resources.enter_context(stage6e_native_acceptance.prepare(legacy))
        except (RuntimeError, OSError, BadZipFile, UnicodeError, ImportError, subprocess.TimeoutExpired) as error:
            if isinstance(error, OSError):
                detail = f"setup filesystem or process operation failed (errno={error.errno})"
            elif isinstance(error, subprocess.TimeoutExpired):
                detail = "native dependency preflight exceeded its five-second timeout"
            elif isinstance(error, (BadZipFile, UnicodeError, ImportError)):
                detail = f"setup input rejected ({type(error).__name__})"
            else:
                detail = str(error)
            print(f"::error title=Stage 4.2 setup failure::{detail}", file=sys.stderr)
            return SETUP_FAILED

        print("START stage42 native security probes", flush=True)
        try:
            legacy.run_probes(*legacy_paths)
            check_live_session(legacy, path)
            check_denials(legacy, path)
            check_wrong_server(legacy, path)
            check_evidence_failure(legacy, path)
            if selected >= 43:
                stage43_native_acceptance.run(legacy, *managed_paths)
            if selected >= 44:
                stage44_native_acceptance.run(legacy, *protected_paths)
            if selected >= 45:
                stage45_native_acceptance.run(legacy, *dispatch_paths)
            if selected >= 46:
                stage46_native_acceptance.run(legacy, *effect_paths)
            if selected >= 47:
                stage47_native_acceptance.run(legacy, *hardened_paths)
            if selected == 60:
                stage6e_native_acceptance.run(legacy, *evidence_paths)
        except Exception:
            print_exc()
            return PROBE_FAILED
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "pins":
        print(*dependency_pins(Path(sys.argv[2])).values(), sep="\n")
    else:
        modes = {"run": 42, "run-stage43": 43, "run-stage44": 44, "run-stage45": 45, "run-stage46": 46, "run-stage47": 47,
                 "run-stage60": 60}
        if len(sys.argv) == 2 and sys.argv[1] in modes:
            sys.exit(run(selected=modes[sys.argv[1]]))
        sys.exit("Expected pins MANIFEST or a supported native run mode")
