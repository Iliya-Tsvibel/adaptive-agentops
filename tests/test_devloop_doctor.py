import os
from pathlib import Path
from shutil import copyfile, which
import subprocess

import pytest

from scripts import devloop_doctor as doctor


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sources(tmp_path):
    relative_paths = [*doctor.DOCS, "evaluations/stage3_results.json"]
    for relative in relative_paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / relative, target)
    return tmp_path


def test_published_governance_sources_are_consistent():
    assert doctor.check_documents(ROOT) == []


@pytest.mark.parametrize(("file", "before", "after", "diagnostic"), [
    ("AGENTS.md", "4. **Historical material.", "3. **Historical material.", "source precedence"),
    (doctor.ADR, "Status: ACCEPTED", "Status: DRAFT", "status metadata"),
    (doctor.ARCHITECTURE, "| D10 |", "| D11 |", "accepted D01–D10 set"),
    (doctor.ARCHITECTURE, doctor.APPROVED_SOURCE, "0" * 40, "approved source commit"),
    (doctor.STAGE41, doctor.SIGN_OFF, "https://example.com", "required source or evidence links"),
    (doctor.STAGE41, doctor.STAGE41_COMMIT, "0" * 40, "Stage 4.1 acceptance metadata"),
    ("SECURITY.md", "docs/developer-loop.md)", "docs/missing.md)", "unresolved or unsafe link"),
    (doctor.STAGE42, "Pending a separately authorized native run", "Already accepted",
     "Stage 4.2 native acceptance metadata"),
])
def test_conflicting_or_missing_source_information_is_blocked(sources, file, before, after, diagnostic):
    target = sources / file
    original = target.read_text(encoding="utf-8")
    assert before in original
    target.write_text(original.replace(before, after), encoding="utf-8")
    assert any(diagnostic in problem for problem in doctor.check_documents(sources))


@pytest.mark.parametrize("file", [doctor.CAMPAIGN, doctor.LEDGER])
def test_missing_campaign_document_is_blocked(sources, file):
    (sources / file).unlink()
    assert f"{file} is missing, unsafe, or unreadable" in doctor.check_documents(sources)


@pytest.mark.parametrize("file", [doctor.CAMPAIGN, doctor.LEDGER])
def test_governance_requires_campaign_links(sources, file):
    agents = sources / "AGENTS.md"
    original = agents.read_text(encoding="utf-8")
    assert f"({file})" in original
    agents.write_text(original.replace(f"({file})", "(SECURITY.md)"), encoding="utf-8")
    assert doctor.check_documents(sources) == ["AGENTS.md is missing required source or evidence links"]


def test_duplicate_adr_is_blocked(sources):
    original = sources / doctor.ADR
    copyfile(original, original.with_name("ADR-0001-duplicate.md"))
    assert any("conflicting canonical paths" in problem for problem in doctor.check_documents(sources))


def test_links_reject_symlinks_and_credentials_without_exposing_paths(sources):
    document = sources / "AGENTS.md"
    (sources / "alias.md").symlink_to("SECURITY.md")
    with document.open("a", encoding="utf-8") as stream:
        stream.write("\n[Symlink](alias.md) [Unsafe](https://fake-token@github.com/example)\n")
    problems = doctor.check_documents(sources)
    assert sum("unresolved or unsafe link" in problem for problem in problems) == 2
    assert not any("fake-token" in problem for problem in problems)


@pytest.fixture
def worktree(tmp_path, monkeypatch):
    git = which("git", path=os.defpath)
    if git is None:
        pytest.skip("system Git unavailable")
    config_home = tmp_path / "config"
    config_home.mkdir()
    monkeypatch.setenv("HOME", str(config_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    env = {"HOME": str(config_home), "PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1"}
    source = tmp_path / "source"
    linked = tmp_path / "linked"
    source.mkdir()

    def run(where, *args):
        return subprocess.check_output(
            [git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "core.hooksPath=/dev/null", *args], cwd=where, env=env,
        ).decode().strip()

    run(source, "init", "-q", "-b", "main", "--template=")
    (source / "fixture").write_text("base\n")
    run(source, "add", "fixture")
    run(source, "commit", "-qm", "test base")
    base = run(source, "rev-parse", "HEAD")
    run(source, "remote", "add", "origin", f"https://github.com/{doctor.REPOSITORY}.git")
    run(source, "update-ref", "refs/remotes/origin/main", base)
    run(source, "worktree", "add", "-qb", "test/doctor", str(linked), base)
    return source, linked, base, run


def test_clean_matching_linked_worktree_passes_without_writing_index(worktree, monkeypatch):
    source, linked, base, run = worktree
    index = Path(run(linked, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    before = index.read_bytes(), index.stat().st_mtime_ns
    monkeypatch.setenv("GIT_DIR", str(source / ".git"))
    assert doctor.check_worktree(linked, base) == []
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before


def test_wrong_base_primary_checkout_and_untracked_changes_are_blocked(worktree):
    source, linked, base, _ = worktree
    assert any("linked worktree" in problem for problem in doctor.check_worktree(source, base))
    assert any("approved base" in problem for problem in doctor.check_worktree(linked, "0" * 40))
    (linked / "untracked").write_text("fixture")
    assert any("untracked files" in problem for problem in doctor.check_worktree(linked, base))


def test_external_filters_block_status_without_invoking_them(worktree):
    _, linked, base, run = worktree
    (linked / ".gitattributes").write_text("fixture filter=unapproved\n")
    (linked / "fixture").write_text("modified text\n")
    run(linked, "config", "filter.unapproved.clean", "touch filter-ran")
    assert any("Configured filters" in problem for problem in doctor.check_worktree(linked, base))
    assert not (linked / "filter-ran").exists()


def test_unsafe_origin_and_hidden_index_changes_are_blocked(worktree):
    _, linked, base, run = worktree
    run(linked, "remote", "set-url", "--push", "origin", "https://fake-token@example.invalid/other")
    run(linked, "update-index", "--assume-unchanged", "fixture")
    problems = doctor.check_worktree(linked, base)
    assert any("approved GitHub" in problem for problem in problems)
    assert any("special index flags" in problem for problem in problems)
    assert not any("fake-token" in problem for problem in problems)


def test_object_replacement_cannot_substitute_the_approved_tree(worktree):
    source, linked, base, run = worktree
    (source / "fixture").write_text("altered tree\n")
    run(source, "commit", "-qam", "different tree")
    run(source, "replace", base, run(source, "rev-parse", "HEAD"))
    assert doctor.check_worktree(linked, base) == [
        "Git object replacements prevent verification of the approved tree",
    ]


@pytest.mark.parametrize("setting", ["remote.origin.promisor", "remote.origin.partialclonefilter"])
def test_partial_clone_is_blocked_before_checkout_status(worktree, setting):
    _, linked, base, run = worktree
    run(linked, "config", setting, "true" if setting.endswith("promisor") else "blob:none")
    assert doctor.check_worktree(linked, base) == [
        "Partial or promisor clones prevent an offline worktree check",
    ]
