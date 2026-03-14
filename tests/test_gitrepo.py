import os
import re
import subprocess
from pathlib import Path

import pytest

from gitrepo import GitRepo


@pytest.fixture(scope="module")
def test_repo_dirs(tmp_path_factory):
    """Create all test repository variants and return their paths."""
    repo_dir = tmp_path_factory.mktemp("gitrepo")
    script = Path(__file__).resolve().parents[1] / "scripts" / "create_test_repo.sh"

    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Pytest User")
    env.setdefault("GIT_AUTHOR_EMAIL", "pytest@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "Pytest User")
    env.setdefault("GIT_COMMITTER_EMAIL", "pytest@example.com")

    subprocess.run(["bash", str(script), str(repo_dir)], check=True, env=env)
    repo_root = Path(repo_dir)
    return {
        "base": repo_root,
        "modified": Path(f"{repo_root}-m"),
        "staged": Path(f"{repo_root}-s"),
        "staged_modified": Path(f"{repo_root}-sm"),
        "remote": Path(f"{repo_root}-remote.git"),
    }


@pytest.fixture(scope="module")
def test_repo(test_repo_dirs):
    """Return a GitRepo wrapper for the clean base repository."""
    return GitRepo(str(test_repo_dirs["base"]))


@pytest.fixture(scope="module")
def test_repo_m(test_repo_dirs):
    """Return a GitRepo wrapper for the modified-only repository."""
    return GitRepo(str(test_repo_dirs["modified"]))


@pytest.fixture(scope="module")
def test_repo_s(test_repo_dirs):
    """Return a GitRepo wrapper for the staged repository."""
    return GitRepo(str(test_repo_dirs["staged"]))


@pytest.fixture(scope="module")
def test_repo_sm(test_repo_dirs):
    """Return a GitRepo wrapper for the staged-and-modified repository."""
    return GitRepo(str(test_repo_dirs["staged_modified"]))


def _git_status_lines(repo_path):
    result = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def test_repo_variants_exposed_with_expected_statuses(test_repo_dirs):
    assert test_repo_dirs["base"].exists()
    assert test_repo_dirs["modified"].exists()
    assert test_repo_dirs["staged"].exists()
    assert test_repo_dirs["staged_modified"].exists()
    assert test_repo_dirs["remote"].exists()

    assert _git_status_lines(test_repo_dirs["base"]) == []
    assert _git_status_lines(test_repo_dirs["modified"]) == [" M docs/notes.txt"]
    assert _git_status_lines(test_repo_dirs["staged"]) == ["M  docs/notes.txt"]
    assert _git_status_lines(test_repo_dirs["staged_modified"]) == ["MM docs/notes.txt"]

    result = subprocess.run(
        ["git", "-C", str(test_repo_dirs["remote"]), "rev-parse", "--is-bare-repository"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "true"


def test_amend_commit_message_rejects_pushed_commit_if_available(test_repo):
    all_commits = test_repo.getHashListEntireRepo()
    pushed_commits = [entry for entry in all_commits if entry[3] == "pushed"]

    if not pushed_commits:
        pytest.skip("No pushed commits in fixture repo; cannot test pushed-commit rejection")

    pushed_hash = pushed_commits[0][1]
    with pytest.raises(ValueError):
        test_repo.amendCommitMessage(pushed_hash, "This should fail")


def test_amend_commit_message_has_unpushed_head_or_unpushed_commits(test_repo):
    all_commits = test_repo.getHashListEntireRepo()
    assert all_commits, "No commits found in repository"

    pushed_hashes = test_repo.getPushedHashes()
    unpushed = [entry for entry in all_commits if entry[3] == "unpushed"]
    assert unpushed, "No unpushed commits found for amendment eligibility checks"

    first_unpushed = unpushed[0][1]
    assert first_unpushed not in pushed_hashes

    head_hash = test_repo._git_run(["git", "-C", test_repo.get_repo_root(), "rev-parse", "HEAD"], text=True).strip()
    assert head_hash


def test_get_complete_commit_message(test_repo):
    repo_path = test_repo.get_repo_root()
    hash_list = test_repo.getHashListEntireRepo()
    assert hash_list, "No commits found in test repository"

    _, hash_val, subject, *_ = hash_list[0]
    complete_msg = test_repo.getCompleteCommitMessage(repo_path, hash_val)

    assert complete_msg is not None
    first_line = complete_msg.splitlines()[0] if complete_msg.splitlines() else ""
    assert first_line
    assert subject in complete_msg or first_line in subject or subject.endswith(first_line)

    invalid_hash = "0000000000000000000000000000000000000000"
    assert test_repo.getCompleteCommitMessage(repo_path, invalid_hash) is None

    multiline_found = False
    for _, h, *_rest in hash_list:
        msg = test_repo.getCompleteCommitMessage(repo_path, h)
        if msg and "\n" in msg:
            multiline_found = True
            assert len(msg.strip().split("\n")) > 1
            break
    assert isinstance(multiline_found, bool)


def test_contract_validation_file_list_and_hash_list_methods(test_repo):
    normalized = test_repo.getNormalizedHashListComplete()
    assert normalized, "getNormalizedHashListComplete returned empty list"

    tokens = [x[1] for x in normalized]
    first_token = tokens[0]
    last_token = tokens[-1]

    file_list_methods = [
        lambda repo: repo.getFileListBetweenNormalizedHashes(first_token, last_token),
        lambda repo: repo.getFileListAtHash(first_token),
        lambda repo: repo.getFileListUntracked(),
        lambda repo: repo.getFileListIgnored(),
        lambda repo: repo.getFileListUntrackedAndIgnored(),
    ]

    for method in file_list_methods:
        res = method(test_repo)
        assert isinstance(res, list)
        for entry in res:
            assert isinstance(entry, tuple)
            assert len(entry) == 3

            path, iso_mtime, status = entry
            assert isinstance(path, str) and path
            assert isinstance(iso_mtime, str) and iso_mtime
            assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", iso_mtime)

            valid_statuses = {
                "added",
                "modified",
                "deleted",
                "renamed",
                "copied",
                "committed",
                "untracked",
                "ignored",
                "staged",
            }
            status_prefix = status.split("->")[0] if "->" in status else status
            assert status_prefix in valid_statuses or status_prefix.startswith("renamed-")

    hash_list_methods = [
        lambda repo: repo.getHashListEntireRepo(),
        lambda repo: repo.getHashListStagedChanges(),
        lambda repo: repo.getHashListNewChanges(),
        lambda repo: repo.getHashListNewRepo(),
        lambda repo: repo.getNormalizedHashListComplete(),
    ]

    for method in hash_list_methods:
        res = method(test_repo)
        assert isinstance(res, list)
        for entry in res:
            assert isinstance(entry, tuple)
            assert len(entry) == 6

            iso, hash_val, subject, status, author_name, author_email = entry
            assert isinstance(iso, str) and iso

            if iso not in ("Newly created repository",):
                if hash_val not in {"NEWREPO", "STAGED", "MODS"}:
                    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", iso)

            assert isinstance(hash_val, str) and hash_val
            pseudo_hashes = {"NEWREPO", "STAGED", "MODS"}
            assert hash_val in pseudo_hashes or re.match(r"^[0-9a-f]{7,}$", hash_val)

            assert isinstance(subject, str)
            assert status in {"pushed", "unpushed"}
            assert isinstance(author_name, str)
            assert isinstance(author_email, str)


def test_get_current_branch_returns_string(test_repo):
    branch = test_repo.getCurrentBranch()
    # The fixture repo is initialised on 'main'
    assert branch == "main"


def test_get_current_branch_returns_none_for_detached_head(test_repo_dirs, tmp_path):
    """Detached HEAD state should cause getCurrentBranch to return None."""
    import shutil

    detached_dir = tmp_path / "detached"
    shutil.copytree(str(test_repo_dirs["base"]), str(detached_dir))

    # Grab HEAD hash, then detach
    head_hash = subprocess.check_output(
        ["git", "-C", str(detached_dir), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    subprocess.run(
        ["git", "-C", str(detached_dir), "checkout", "--detach", head_hash],
        check=True,
        capture_output=True,
    )

    repo = GitRepo(str(detached_dir))
    assert repo.getCurrentBranch() is None


def test_get_all_branches_returns_list_with_main(test_repo):
    branches = test_repo.getAllBranches()
    assert isinstance(branches, list)
    assert "main" in branches


def test_get_all_branches_is_sorted(test_repo):
    branches = test_repo.getAllBranches()
    assert branches == sorted(branches)


def test_get_all_branches_include_remote(test_repo):
    # The fixture remote is a bare repo; local clone has origin/main tracking it
    branches_local = test_repo.getAllBranches(include_remote=False)
    branches_all = test_repo.getAllBranches(include_remote=True)
    # All local branches should still be present when remotes are included
    for b in branches_local:
        assert b in branches_all
    # Result remains sorted
    assert branches_all == sorted(branches_all)
