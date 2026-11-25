"""Check the DevLoop documentation and local worktree without changing either."""

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit


ARCHITECTURE = "docs/architecture/stage4-local.md"
ADR = "docs/decisions/ADR-0001-stage4-identity-boundaries.md"
LOOP = "docs/developer-loop.md"
CAMPAIGN = "docs/campaigns/stage4-autonomous-completion.md"
LEDGER = "docs/campaigns/stage4-progress.md"
STAGE41 = "docs/stage4-native-acceptance.md"
STAGE42 = "docs/stage42-native-acceptance.md"
STAGE43 = "docs/stage43-native-acceptance.md"
STAGE44 = "docs/stage44-native-acceptance.md"
STAGE45 = "docs/stage45-native-acceptance.md"
CONTRACT42 = "docs/tasks/stage4/4.2-scoped-delegation-contract.md"
CONTRACT43 = "docs/tasks/stage4/4.3-operator-delegation-contract.md"
CONTRACT44 = "docs/tasks/stage4/4.4-protected-controller-state-contract.md"
CONTRACT45 = "docs/tasks/stage4/4.5-os-bound-dispatch-contract.md"
APPROVED_SOURCE = "4cbbc0f325ad1cfc427008c6b55d314355b19550"
ADR_BLOB = "b8a5653a32b95c966d47d79f05907f4aa3fb4f46"
REPOSITORY = "Iliya-Tsvibel/adaptive-agentops"
NATIVE_RUN = f"https://github.com/{REPOSITORY}/actions/runs/45403991684"
SIGN_OFF = f"https://github.com/{REPOSITORY}/pull/11#issuecomment-7133007767"
STAGE41_COMMIT = "de5d9dbfb8db9ccb7a546892f4046010dfc3b9da"
REQUIRED_LINKS = {
    "AGENTS.md": {"SECURITY.md", LOOP, ARCHITECTURE, CONTRACT42, CAMPAIGN, LEDGER},
    "SECURITY.md": {ARCHITECTURE, LOOP, CONTRACT42, "docs/stage2-security.md"},
    LOOP: {"AGENTS.md", "SECURITY.md", ARCHITECTURE, STAGE41, CONTRACT42, CAMPAIGN, LEDGER},
    CAMPAIGN: {ADR, ARCHITECTURE, LEDGER, LOOP, STAGE43, "SECURITY.md"},
    LEDGER: {CAMPAIGN, ADR, STAGE41, CONTRACT42, STAGE43, CONTRACT44, STAGE44, CONTRACT45, STAGE45},
    ARCHITECTURE: {ADR, STAGE41, STAGE42, CONTRACT42, "docs/stage2-security.md",
                   "docs/stage3-acceptance.md"},
    ADR: {ARCHITECTURE, STAGE41, CONTRACT42},
    STAGE41: {ADR, STAGE42, NATIVE_RUN, SIGN_OFF},
    STAGE43: {CONTRACT43, STAGE42},
    CONTRACT43: {ADR, CONTRACT42},
    STAGE44: {CONTRACT44, STAGE43},
    CONTRACT44: {ADR, CONTRACT43, CAMPAIGN},
    STAGE45: {CONTRACT45, STAGE44},
    CONTRACT45: {ADR, CAMPAIGN},
}
DOCS = sorted(set(REQUIRED_LINKS) | {
    STAGE42, CONTRACT42, "docs/stage2-security.md", "docs/stage3-acceptance.md",
})
PRECEDENCE = (
    "Approved architecture, security and task contracts",
    "Repository code and tests",
    "Verified project evidence",
    "Historical material",
)
COMMIT = re.compile(r"[0-9a-f]{40}")
LINK = re.compile(r"\[[^\]\n]+\]\(([^\s()]+)\)")


def regular_source(root, path):
    """Only inspect paths whose existing components stay inside the checkout."""
    return path.is_relative_to(root) and path.resolve(strict=True) == path and path.is_file()


def prose_lines(text):
    fence = None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in ("```", "~~~"):
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
        elif fence is None:
            yield number, line


def heading_anchors(text):
    anchors = set()
    for _, line in prose_lines(text):
        match = re.match(r"^#{1,6}\s+(.+)$", line)
        if match:
            stem = re.sub(r"[^\w -]", "", match[1].lower().strip()).replace(" ", "-")
            anchor = stem
            count = 1
            while anchor in anchors:
                anchor = f"{stem}-{count}"
                count += 1
            anchors.add(anchor)
    return anchors


def link_target(root, source, href):
    link = urlsplit(href)
    if link.scheme or link.netloc:
        if link.scheme != "https" or not link.hostname or link.username or link.password:
            raise ValueError("external links must use HTTPS without credentials")
        return href
    if link.query:
        raise ValueError("local links cannot have a query")
    path = root / source
    if link.path:
        # Normalize relative parent components before checking for symlinks.
        path = Path(os.path.abspath(path.parent / unquote(link.path)))
    if not regular_source(root, path):
        raise ValueError("local link must resolve to a regular in-repository source")
    if link.fragment and unquote(link.fragment) not in heading_anchors(path.read_text(encoding="utf-8")):
        raise ValueError("local heading link does not resolve")
    return path.relative_to(root).as_posix()


def check_documents(root):
    problems = []
    sources = {}
    for source in DOCS:
        path = root / source
        try:
            if not regular_source(root, path):
                raise ValueError
            sources[source] = path.read_text(encoding="utf-8")
        except (OSError, ValueError, RuntimeError):
            problems.append(f"{source} is missing, unsafe, or unreadable")

    for source, text in sources.items():
        targets = set()
        for number, line in prose_lines(text):
            for href in LINK.findall(line):
                try:
                    targets.add(link_target(root, source, href))
                except (OSError, ValueError, RuntimeError):
                    problems.append(f"{source}:{number} has an unresolved or unsafe link")
        if REQUIRED_LINKS.get(source, set()) - targets:
            problems.append(f"{source} is missing required source or evidence links")

    prose = {source: "\n".join(line for _, line in prose_lines(text)) for source, text in sources.items()}
    agents = prose.get("AGENTS.md", "")
    section = agents.partition("\n## Source of truth\n")[2].split("\n## ", 1)[0]
    levels = re.findall(r"^(\d+)\. (.+)$", section, re.MULTILINE)
    if agents.count("\n## Source of truth\n") != 1 or len(levels) != len(PRECEDENCE) or any(
        number != str(index) or not entry.startswith(f"**{label}.** ")
        for index, ((number, entry), label) in enumerate(zip(levels, PRECEDENCE), start=1)
    ):
        problems.append("AGENTS.md source precedence differs from the canonical hierarchy")

    metadata = {
        ADR: {
            "Status": "ACCEPTED — LOCAL STAGE 4 ONLY",
            "Approval date": "2026-11-18",
            "Approved source commit": f"`{APPROVED_SOURCE}`",
            "Approved source blob": f"`{ADR_BLOB}`",
        },
        ARCHITECTURE: {
            "Status": "ACCEPTED DECISIONS — LOCAL STAGE 4 ONLY",
            "Approved source commit": f"`{APPROVED_SOURCE}`",
        },
    }
    for source, fields in metadata.items():
        for name, value in fields.items():
            values = re.findall(rf"^{re.escape(name)}: (.*)$", prose.get(source, ""), re.MULTILINE)
            if values != [value]:
                problems.append(f"{source} has missing or conflicting {name.lower()} metadata")

    groups = re.findall(r"^- (D\d{2}(?:–D\d{2})?): ", prose.get(ADR, ""), re.MULTILINE)
    decisions = re.findall(r"^\| (D\d{2}) \|", prose.get(ARCHITECTURE, ""), re.MULTILINE)
    if groups != ["D01–D03", *(f"D{n:02}" for n in range(4, 11))] or decisions != [
        f"D{n:02}" for n in range(1, 11)
    ]:
        problems.append("The ADR and architecture must both record the accepted D01–D10 set")
    if ADR not in sources or {path.name for path in (root / "docs/decisions").glob("ADR-0001*.md")} != {Path(ADR).name}:
        problems.append("ADR-0001 has missing or conflicting canonical paths")

    stage41 = prose.get(STAGE41, "")
    status = re.findall(r"^\*\*Status: ([^*]+)\*\*$", stage41, re.MULTILINE)
    if status != ["ACCEPTED — Stage 4.1 isolated native Linux IPC only."] or f"`{STAGE41_COMMIT}`" not in stage41:
        problems.append("Stage 4.1 acceptance metadata does not match the approved scope")
    stage42 = prose.get(STAGE42, "")
    pending = "**Pending a separately authorized native run and owner acceptance.**"
    if not stage42.partition("\n\n")[2].startswith(pending) or re.search(
        r"^(?:\*\*)?Status: ", stage42, re.MULTILINE,
    ):
        problems.append("Stage 4.2 native acceptance metadata does not match the pending gate")
    return problems


class GitReadError(RuntimeError):
    pass


def git_reader(root):
    executable = shutil.which("git", path=os.defpath)
    if executable is None:
        raise GitReadError("Git is unavailable on the system path")
    environment = {
        key: os.environ[key] for key in ("HOME", "XDG_CONFIG_HOME") if key in os.environ
    }
    environment.update(
        PATH=os.defpath, LC_ALL="C", GIT_TERMINAL_PROMPT="0",
        GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="",
    )
    prefix = (
        executable, "--no-pager", "--no-optional-locks",
        "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", "core.hooksPath=/dev/null",
    )

    def read(*args, allow_absent=False):
        try:
            result = subprocess.run((*prefix, *args), cwd=root, env=environment,
                                    capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitReadError("A local Git check could not complete") from error
        if result.returncode != 0 and not (allow_absent and result.returncode == 1):
            raise GitReadError(f"Git could not verify {args[0]}")
        return result.stdout

    return read


def check_worktree(root, expected_base):
    problems = []
    try:
        git = git_reader(root)
        top, git_dir, common_dir = git(
            "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir",
        ).splitlines()
        if Path(os.fsdecode(top)).resolve() != root:
            return ["The doctor sources and Git checkout root differ"]
        if git("for-each-ref", "--format=%(refname)", "refs/replace/"):
            return ["Git object replacements prevent verification of the approved tree"]
        if git("config", "--name-only", "--get-regexp",
               r"^(extensions\.partialclone|remote\..*\.(promisor|partialclonefilter))$", allow_absent=True):
            return ["Partial or promisor clones prevent an offline worktree check"]
        if git_dir == common_dir:
            problems.append("Use a dedicated linked worktree for the task")
        if git("symbolic-ref", "--quiet", "--short", "HEAD", allow_absent=True).strip() == b"main":
            problems.append("Use a task branch or detached worktree instead of main")
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge",
                       "rebase-apply", "sequencer", "BISECT_START"):
            path = git("rev-parse", "--path-format=absolute", "--git-path", marker).rstrip(b"\n")
            if os.path.lexists(os.fsdecode(path)):
                problems.append("An unfinished Git operation is present")
                break

        accepted_origins = {
            f"{prefix}{REPOSITORY}{suffix}".lower().encode()
            for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/")
            for suffix in ("", ".git")
        }
        for option in ((), ("--push",)):
            urls = git("remote", "get-url", *option, "--all", "origin").splitlines()
            if len(urls) != 1 or urls[0].lower() not in accepted_origins:
                problems.append("Origin must have one approved GitHub fetch and push destination")
                break

        if expected_base is None:
            problems.append("Supply --expected-base with the owner-approved full commit SHA")
        else:
            head = git("rev-parse", "--verify", "HEAD").strip()
            remote = git("rev-parse", "--verify", "--quiet", "refs/remotes/origin/main",
                         allow_absent=True).strip()
            if head != expected_base.encode() or remote != expected_base.encode():
                problems.append("HEAD and cached origin/main must match the approved base")

        # Avoid Git's status machinery when local configuration could invoke
        # an external clean filter or hide changes through index flags.
        filters = git("config", "--name-only", "--get-regexp", r"^filter\.", allow_absent=True)
        index = git("ls-files", "--stage", "-z").split(b"\0")
        index_flags = git("ls-files", "-v", "-z").split(b"\0")
        if filters or any(entry.startswith(b"160000 ") for entry in index) or any(
            entry and not entry.startswith(b"H ") for entry in index_flags
        ):
            problems.append("Configured filters, submodules, or special index flags prevent a safe status check")
        elif git("status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=all"):
            problems.append("Worktree has staged, modified, or untracked files")
    except (GitReadError, OSError, ValueError) as error:
        problems.append(str(error) if isinstance(error, GitReadError) else "Local Git layout could not be verified")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-only", action="store_true", help="skip all Git checks")
    parser.add_argument("--expected-base", metavar="SHA", help="owner-approved 40-character base commit")
    args = parser.parse_args()
    if args.expected_base is not None and not COMMIT.fullmatch(args.expected_base):
        parser.error("--expected-base requires a full lowercase 40-character commit SHA")
    if args.docs_only and args.expected_base is not None:
        parser.error("--expected-base applies only to the full check")

    root = Path(__file__).resolve().parents[1]
    problems = check_documents(root)
    if not args.docs_only:
        problems.extend(check_worktree(root, args.expected_base))
    for problem in problems:
        print(f"BLOCKED {problem}")
    if problems:
        return 1
    if args.docs_only:
        print(f"PASS source links and metadata in {len(DOCS)} documents (docs only)")
    else:
        print("PASS source links, metadata, and local worktree checks; verify live origin/main separately")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
