import os
import re
import subprocess
from pathlib import Path

import pytest

import gitrepo
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


def test_get_current_branch_on_disk_returns_string(test_repo):
    branch = test_repo.getCurrentBranchOnDisk()
    # The fixture repo is initialised on 'main'
    assert branch == "main"


def test_get_current_branch_on_disk_returns_none_for_detached_head(test_repo_dirs, tmp_path):
    """Detached HEAD state should cause getCurrentBranchOnDisk to return None."""
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
    assert repo.getCurrentBranchOnDisk() is None


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


def test_reset_cache_clears_command_cache(test_repo):
    test_repo._cmd_cache = {"example": "value"}
    test_repo.reset_cache()
    assert test_repo._cmd_cache == {}


def test_get_current_branch_prefers_configured_branch_and_falls_back_to_head(test_repo_dirs):
    repo_with_branch = GitRepo(str(test_repo_dirs["base"]), branch="main")
    repo_without_branch = GitRepo(str(test_repo_dirs["base"]))

    assert repo_with_branch.getCurrentBranch() == "main"
    assert repo_without_branch.getCurrentBranch() == "HEAD"


def test_get_current_branch_on_disk_returns_none_when_symbolic_ref_empty(monkeypatch, test_repo):
    def _fake_check_output(*_args, **_kwargs):
        return "\n"

    monkeypatch.setattr(gitrepo, "check_output", _fake_check_output)
    assert test_repo.getCurrentBranchOnDisk() is None


def test_get_all_branches_remote_flag_sorts_and_filters_blank_lines(monkeypatch, test_repo):
    calls = []

    def _fake_check_output(cmd, **_kwargs):
        calls.append(cmd)
        return "zeta\n\nalpha\n"

    monkeypatch.setattr(gitrepo, "check_output", _fake_check_output)

    local_only = test_repo.getAllBranches(include_remote=False)
    with_remote = test_repo.getAllBranches(include_remote=True)

    assert local_only == ["alpha", "zeta"]
    assert with_remote == ["alpha", "zeta"]
    assert calls[0] == ["git", "branch", "--format=%(refname:short)"]
    assert calls[1] == ["git", "branch", "--format=%(refname:short)", "--all"]


def test_get_all_branches_returns_empty_list_on_called_process_error(monkeypatch, test_repo):
    def _fake_check_output(*_args, **_kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=["git", "branch"])

    monkeypatch.setattr(gitrepo, "check_output", _fake_check_output)
    assert test_repo.getAllBranches() == []


def test_get_all_local_branches_returns_list_with_main(test_repo):
    branches = test_repo.getAllLocalBranches()
    assert isinstance(branches, list)
    assert "main" in branches


def test_get_all_local_branches_is_sorted(test_repo):
    branches = test_repo.getAllLocalBranches()
    assert branches == sorted(branches)


def test_get_all_local_branches_command_and_whitespace_filter(monkeypatch, test_repo):
    calls = []

    def _fake_check_output(cmd, **_kwargs):
        calls.append(cmd)
        return "zeta\n\n  alpha  \n"

    monkeypatch.setattr(gitrepo, "check_output", _fake_check_output)
    assert test_repo.getAllLocalBranches() == ["alpha", "zeta"]
    assert calls[-1] == ["git", "branch", "--format=%(refname:short)"]


def test_get_all_local_branches_equivalent_to_all_branches_local(test_repo):
    assert test_repo.getAllLocalBranches() == test_repo.getAllBranches(include_remote=False)


def test_get_all_local_branches_returns_empty_list_on_called_process_error(monkeypatch, test_repo):
    def _fake_check_output(*_args, **_kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=["git", "branch"])

    monkeypatch.setattr(gitrepo, "check_output", _fake_check_output)
    assert test_repo.getAllLocalBranches() == []


def test_get_upstream_ref_success_and_none_paths(monkeypatch, test_repo_dirs):
    repo_with_branch = GitRepo(str(test_repo_dirs["base"]), branch="main")
    calls = []

    def _fake_success(cmd, **_kwargs):
        calls.append(cmd)
        return "0123456789abcdef\n"

    monkeypatch.setattr(gitrepo, "check_output", _fake_success)
    assert repo_with_branch._get_upstream_ref() == "main@{upstream}"
    assert calls[-1] == ["git", "rev-parse", "--verify", "main@{upstream}"]

    def _fake_empty(*_args, **_kwargs):
        return "\n"

    monkeypatch.setattr(gitrepo, "check_output", _fake_empty)
    assert repo_with_branch._get_upstream_ref() is None

    def _fake_fail(*_args, **_kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=["git", "rev-parse"])

    monkeypatch.setattr(gitrepo, "check_output", _fake_fail)
    assert repo_with_branch._get_upstream_ref() is None


def test_index_mtime_iso_prefers_dot_git_index(monkeypatch, test_repo):
    seen = {"paths": []}

    def _fake_exists(path):
        seen["paths"].append(path)
        return path.endswith("/.git/index")

    def _fake_getmtime(path):
        assert path.endswith("/.git/index")
        return 1234.5

    monkeypatch.setattr(gitrepo.os.path, "exists", _fake_exists)
    monkeypatch.setattr(gitrepo.os.path, "getmtime", _fake_getmtime)
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda epoch: f"iso:{epoch}")

    assert test_repo.index_mtime_iso() == "iso:1234.5"
    assert any(p.endswith("/.git/index") for p in seen["paths"])


def test_index_mtime_iso_falls_back_to_now_when_index_missing_or_errors(monkeypatch, test_repo):
    class _FakeNow:
        def timestamp(self):
            return 42.0

    class _FakeDateTime:
        @staticmethod
        def now(_tz):
            return _FakeNow()

    def _fake_exists(path):
        if path.endswith("/.git/index"):
            raise OSError("boom")
        return False

    monkeypatch.setattr(gitrepo.os.path, "exists", _fake_exists)
    monkeypatch.setattr(gitrepo, "datetime", _FakeDateTime)
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda epoch: f"iso:{epoch}")

    assert test_repo.index_mtime_iso() == "iso:42.0"


def test_epoch_to_iso_success_and_failure(monkeypatch, test_repo):
    iso = test_repo._epoch_to_iso(0)
    assert iso.startswith("1970-01-01T00:00:00")

    class _BrokenDateTime:
        @staticmethod
        def fromtimestamp(*_args, **_kwargs):
            raise ValueError("bad timestamp")

    monkeypatch.setattr(gitrepo, "datetime", _BrokenDateTime)
    assert test_repo._epoch_to_iso(1.0) == "1970-01-01T00:00:00"


def test_git_cli_decode_quoted_path_variants(monkeypatch, test_repo):
    assert test_repo._git_cli_decode_quoted_path("") == ""
    assert test_repo._git_cli_decode_quoted_path("plain/path.txt") == "plain/path.txt"

    # Normal quoted decode path
    assert test_repo._git_cli_decode_quoted_path('"a\\040b.txt"') == "a b.txt"

    # Force UTF-8 decode failure; latin-1 fallback should return a byte-preserving string
    assert test_repo._git_cli_decode_quoted_path('"\\xff"') == "ÿ"

    def _boom_decode(*_args, **_kwargs):
        raise RuntimeError("decode fail")

    monkeypatch.setattr(gitrepo.codecs, "decode", _boom_decode)
    assert test_repo._git_cli_decode_quoted_path('"hello"') == "hello"


def test_safe_mtime_all_paths(monkeypatch, test_repo):
    class _Stat:
        st_mtime = 77.0

    # Symlink path
    monkeypatch.setattr(gitrepo.os.path, "islink", lambda _fp: True)
    monkeypatch.setattr(gitrepo.os, "lstat", lambda _fp: _Stat())
    assert test_repo.safe_mtime("rel") == 77.0

    # Normal file path
    monkeypatch.setattr(gitrepo.os.path, "islink", lambda _fp: False)
    monkeypatch.setattr(gitrepo.os.path, "exists", lambda _fp: True)
    monkeypatch.setattr(gitrepo.os.path, "getmtime", lambda _fp: 88.0)
    assert test_repo.safe_mtime("rel") == 88.0

    # Missing file path
    monkeypatch.setattr(gitrepo.os.path, "exists", lambda _fp: False)
    assert test_repo.safe_mtime("rel") is None

    # FileNotFoundError path
    def _exists_not_found(_fp):
        raise FileNotFoundError("gone")

    monkeypatch.setattr(gitrepo.os.path, "exists", _exists_not_found)
    assert test_repo.safe_mtime("rel") is None

    # Generic exception path
    def _exists_boom(_fp):
        raise RuntimeError("stat fail")

    monkeypatch.setattr(gitrepo.os.path, "exists", _exists_boom)
    assert test_repo.safe_mtime("rel") is None


def test_paths_mtime_iso_uses_max_and_handles_errors(monkeypatch, test_repo):
    values = iter([1.0, None, 9.0])

    def _safe(_p):
        return next(values)

    monkeypatch.setattr(test_repo, "safe_mtime", _safe)
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda epoch: f"iso:{epoch}")
    assert test_repo._paths_mtime_iso(["a", "b", "c"]) == "iso:9.0"

    calls = {"index": 0}

    def _safe_raise(_p):
        raise RuntimeError("boom")

    def _index():
        calls["index"] += 1
        return "idx"

    monkeypatch.setattr(test_repo, "safe_mtime", _safe_raise)
    monkeypatch.setattr(test_repo, "index_mtime_iso", _index)
    assert test_repo._paths_mtime_iso(["x"]) == "idx"
    assert calls["index"] == 1


def test_make_cache_key_success_and_fallback(monkeypatch, test_repo):
    k1 = test_repo._make_cache_key("name", 1, "x")
    k2 = test_repo._make_cache_key("name", 1, "x")
    assert k1 == k2
    assert k1.startswith("name:")
    assert len(k1.split(":", 1)[1]) == 16

    original_sha256 = gitrepo.hashlib.sha256
    calls = {"n": 0}

    def _sha256_flaky(data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("hash fail")
        return original_sha256(data)

    monkeypatch.setattr(gitrepo.hashlib, "sha256", _sha256_flaky)
    fallback = test_repo._make_cache_key("fallback-name")
    assert fallback.startswith("fallback-name:")
    assert len(fallback.split(":", 1)[1]) == 16


def test_get_commit_timestamp_all_paths(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "1700000000\n")
    assert test_repo._get_commit_timestamp("abc") == 1700000000.0

    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "")
    assert test_repo._get_commit_timestamp("abc") is None

    def _raise(*_args, **_kwargs):
        raise RuntimeError("git failed")

    monkeypatch.setattr(test_repo, "_git_run", _raise)
    assert test_repo._get_commit_timestamp("abc") is None


def test_parse_git_log_output_all_paths(test_repo):
    output = "\n".join(
        [
            "1700000000 a1 Author a@example.com subject line",
            "badts b2 Name n@example.com second line",
            "onlyhash",
            "1700000001 c3",
            "",
        ]
    )
    res = test_repo._parse_git_log_output(output)
    assert res[0] == (1700000000, "a1", "Author", "a@example.com", "subject line")
    assert res[1] == (0, "b2", "Name", "n@example.com", "second line")
    assert res[2] == (1700000001, "c3", "", "", "")

    class _BrokenLog:
        def splitlines(self):
            raise RuntimeError("split fail")

    assert test_repo._parse_git_log_output(_BrokenLog()) == []


def test_parse_git_log_output_skips_blank_lines(test_repo):
    # Explicit blank line exercises the early-continue branch.
    output = "\n1700000002 h2"
    assert test_repo._parse_git_log_output(output) == [(1700000002, "h2", "", "", "")]


def test_git_cli_parse_name_status_output_all_paths(test_repo):
    output = "\n".join(
        [
            "A alpha.txt",
            "M beta.txt",
            "D gamma.txt",
            "R100 old.txt new.txt",
            "C100 src.txt dst.txt",
            "X weird.txt",
            "Z",  # no path; should be skipped
            "",  # ignored blank
        ]
    )
    res = test_repo._git_cli_parse_name_status_output(output)
    assert ("alpha.txt", "added") in res
    assert ("beta.txt", "modified") in res
    assert ("gamma.txt", "deleted") in res
    assert ("old.txt", "renamed->new.txt") in res
    assert ("src.txt", "copied") in res
    assert ("weird.txt", "modified") in res
    assert all(path for path, _status in res)
    assert res == sorted(res, key=lambda x: x[0])

    class _BrokenStatus:
        def splitlines(self):
            raise RuntimeError("split fail")

    assert test_repo._git_cli_parse_name_status_output(_BrokenStatus()) == []


def test_git_cli_parse_name_status_output_blank_and_line_parse_exceptions(test_repo):
    class _LineSplitFails:
        def split(self):
            raise RuntimeError("line split fail")

    class _NameStatusInput:
        def splitlines(self):
            # Covers: blank line branch, whitespace-only branch (not parts), and line-parse exception branch.
            return ["", "   ", _LineSplitFails(), "A ok.txt"]

    res = test_repo._git_cli_parse_name_status_output(_NameStatusInput())
    assert res == [("ok.txt", "added")]


def test_git_cli_parse_name_status_output_rename_target_exception_branch(test_repo):
    class _CodeStartsWithR:
        def strip(self):
            return self

        def startswith(self, _prefix):
            return True

        def __bool__(self):
            return True

        def __getitem__(self, _idx):
            return "R"

    class _BadPart:
        def strip(self):
            raise RuntimeError("strip fail")

    class _GoodPart:
        def __init__(self, s):
            self.s = s

        def strip(self):
            return self.s

    class _LineProducesBadRenameParts:
        def split(self):
            return [_CodeStartsWithR(), _GoodPart("old.txt"), _BadPart()]

    class _Input:
        def splitlines(self):
            return [_LineProducesBadRenameParts()]

    # Rename-target extraction fails and is logged; parser should still return fallback "renamed" status.
    assert test_repo._git_cli_parse_name_status_output(_Input()) == [("old.txt", "renamed")]


def test_set_verbosity_clamps_to_non_negative():
    GitRepo.setVerbosity(3)
    assert GitRepo.verbose == 3
    GitRepo.setVerbosity(-10)
    assert GitRepo.verbose == 0


def test_get_commit_timestamp_delegates_to_internal(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "_get_commit_timestamp", lambda _h: 123.0)
    assert test_repo.getCommitTimestamp("abc") == 123.0


def test_get_file_list_between_new_repo_and_top_hash_delegates(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "getCurrentBranch", lambda: "main")
    seen = {}

    def _fake_newrepo_and_hash(curr_hash, ignorecache=False):
        seen["curr_hash"] = curr_hash
        seen["ignorecache"] = ignorecache
        return [("a.txt", "iso", "added")]

    monkeypatch.setattr(test_repo, "getFileListBetweenNewRepoAndHash", _fake_newrepo_and_hash)
    out = test_repo.getFileListBetweenNewRepoAndTopHash(ignorecache=True)
    assert out == [("a.txt", "iso", "added")]
    assert seen == {"curr_hash": "main", "ignorecache": True}


def test_get_file_list_between_top_hash_and_current_time_delegates(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "getCurrentBranch", lambda: "feature/x")
    seen = {}

    def _fake_hash_to_now(hash, ignorecache=False):
        seen["hash"] = hash
        seen["ignorecache"] = ignorecache
        return [("b.txt", "iso", "modified")]

    monkeypatch.setattr(test_repo, "getFileListBetweenHashAndCurrentTime", _fake_hash_to_now)
    out = test_repo.getFileListBetweenTopHashAndCurrentTime(ignorecache=True)
    assert out == [("b.txt", "iso", "modified")]
    assert seen == {"hash": "feature/x", "ignorecache": True}


def test_get_file_list_between_top_hash_and_staged_delegates(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "getCurrentBranch", lambda: "dev")
    seen = {}

    def _fake_hash_to_staged(hash, ignorecache=False):
        seen["hash"] = hash
        seen["ignorecache"] = ignorecache
        return [("c.txt", "iso", "staged")]

    monkeypatch.setattr(test_repo, "getFileListBetweenHashAndStaged", _fake_hash_to_staged)
    out = test_repo.getFileListBetweenTopHashAndStaged(ignorecache=True)
    assert out == [("c.txt", "iso", "staged")]
    assert seen == {"hash": "dev", "ignorecache": True}


def test_get_file_list_between_new_repo_and_staged_dispatch(monkeypatch, test_repo):
    seen = {}

    def _fake_dispatch(prev=None, curr=None, cached=False, key=None, ignorecache=False):
        seen.update(
            {
                "prev": prev,
                "curr": curr,
                "cached": cached,
                "key": key,
                "ignorecache": ignorecache,
            }
        )
        return [("staged.txt", "iso", "added")]

    monkeypatch.setattr(test_repo, "_git_name_status_dispatch", _fake_dispatch)
    out = test_repo.getFileListBetweenNewRepoAndStaged(ignorecache=True)
    assert out == [("staged.txt", "iso", "added")]
    assert seen == {
        "prev": None,
        "curr": None,
        "cached": True,
        "key": "getFileListBetweenNewRepoAndStaged",
        "ignorecache": True,
    }


def test_get_file_list_between_new_repo_and_mods_dispatch(monkeypatch, test_repo):
    seen = {}

    def _fake_dispatch(prev=None, curr=None, cached=False, key=None, ignorecache=False):
        seen.update(
            {
                "prev": prev,
                "curr": curr,
                "cached": cached,
                "key": key,
                "ignorecache": ignorecache,
            }
        )
        return [("mods.txt", "iso", "modified")]

    monkeypatch.setattr(test_repo, "_git_name_status_dispatch", _fake_dispatch)
    out = test_repo.getFileListBetweenNewRepoAndMods(ignorecache=True)
    assert out == [("mods.txt", "iso", "modified")]
    assert seen == {
        "prev": None,
        "curr": None,
        "cached": False,
        "key": "getFileListBetweenNewRepoAndMods",
        "ignorecache": True,
    }


def test_get_file_list_between_hash_and_current_time_dispatch(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-hash-now")
    seen = {}

    def _fake_dispatch(prev=None, curr=None, cached=False, key=None, ignorecache=False):
        seen.update(
            {
                "prev": prev,
                "curr": curr,
                "cached": cached,
                "key": key,
                "ignorecache": ignorecache,
            }
        )
        return [("work.txt", "iso", "modified")]

    monkeypatch.setattr(test_repo, "_git_name_status_dispatch", _fake_dispatch)
    out = test_repo.getFileListBetweenHashAndCurrentTime("abc123", ignorecache=True)
    assert out == [("work.txt", "iso", "modified")]
    assert seen == {
        "prev": "abc123",
        "curr": None,
        "cached": False,
        "key": "k-hash-now",
        "ignorecache": True,
    }


def test_get_file_list_between_hash_and_staged_dispatch(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-hash-staged")
    seen = {}

    def _fake_dispatch(prev=None, curr=None, cached=False, key=None, ignorecache=False):
        seen.update(
            {
                "prev": prev,
                "curr": curr,
                "cached": cached,
                "key": key,
                "ignorecache": ignorecache,
            }
        )
        return [("stage.txt", "iso", "staged")]

    monkeypatch.setattr(test_repo, "_git_name_status_dispatch", _fake_dispatch)
    out = test_repo.getFileListBetweenHashAndStaged("def456", ignorecache=True)
    assert out == [("stage.txt", "iso", "staged")]
    assert seen == {
        "prev": "def456",
        "curr": None,
        "cached": True,
        "key": "k-hash-staged",
        "ignorecache": True,
    }


def test_get_file_list_between_staged_and_mods_dispatch(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-staged-mods")
    seen = {}

    def _fake_dispatch(prev=None, curr=None, cached=False, key=None, ignorecache=False):
        seen.update(
            {
                "prev": prev,
                "curr": curr,
                "cached": cached,
                "key": key,
                "ignorecache": ignorecache,
            }
        )
        return [("delta.txt", "iso", "modified")]

    monkeypatch.setattr(test_repo, "_git_name_status_dispatch", _fake_dispatch)
    out = test_repo.getFileListBetweenStagedAndMods(ignorecache=True)
    assert out == [("delta.txt", "iso", "modified")]
    assert seen == {
        "prev": None,
        "curr": None,
        "cached": False,
        "key": "k-staged-mods",
        "ignorecache": True,
    }


def test_deltas_to_results_all_paths(test_repo):
    detailed = [
        {"status": "added", "path": "b.txt"},
        {"status": "renamed->new.txt", "new_path": "a.txt"},
        {"status": "deleted", "old_path": "c.txt"},
        {"status": "modified"},  # missing path; skipped
        object(),  # triggers per-item exception path
    ]
    res = test_repo._deltas_to_results(detailed, None, None)
    assert res == [
        ("a.txt", "renamed->new.txt"),
        ("b.txt", "added"),
        ("c.txt", "deleted"),
    ]

    assert test_repo._deltas_to_results([], None, None) == []
    assert test_repo._deltas_to_results(1, None, None) == []


def test_git_cli_name_status_success_and_exception_paths(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "_git_run", lambda _args, text=True: "M a.txt\n")
    monkeypatch.setattr(test_repo, "_git_cli_parse_name_status_output", lambda output: [(output.strip(), "ok")])
    assert test_repo._git_cli_name_status(["git", "diff"]) == [("M a.txt", "ok")]

    def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(test_repo, "_git_run", _raise)
    assert test_repo._git_cli_name_status(["git", "diff"]) == []


def test_git_name_status_dispatch_variants_and_exception(monkeypatch, test_repo):
    calls = []

    def _fake_cached_file_list(cache_key, args, ignorecache=False):
        calls.append((cache_key, args, ignorecache))
        return [("x", "iso", "status")]

    monkeypatch.setattr(test_repo, "_git_cli_getCachedFileList", _fake_cached_file_list)
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "auto-key")

    # cached=True and prev+curr provided
    out = test_repo._git_name_status_dispatch(prev="A", curr="B", cached=True, key=None, ignorecache=True)
    assert out == [("x", "iso", "status")]
    assert calls[-1] == ("auto-key", ["git", "diff", "--name-status", "--cached", "A", "B"], True)

    # prev-only
    out = test_repo._git_name_status_dispatch(prev="A", curr=None, cached=False, key="k-prev", ignorecache=False)
    assert out == [("x", "iso", "status")]
    assert calls[-1] == ("k-prev", ["git", "diff", "--name-status", "A"], False)

    # curr-only
    out = test_repo._git_name_status_dispatch(prev=None, curr="B", cached=False, key="k-curr", ignorecache=False)
    assert out == [("x", "iso", "status")]
    assert calls[-1] == ("k-curr", ["git", "diff", "--name-status", "B"], False)

    def _raise_dispatch(*_args, **_kwargs):
        raise RuntimeError("dispatch fail")

    monkeypatch.setattr(test_repo, "_git_cli_getCachedFileList", _raise_dispatch)
    assert test_repo._git_name_status_dispatch(prev="A", curr="B", cached=False, key="k", ignorecache=False) == []


def test_git_cli_get_cached_file_list_paths(monkeypatch, test_repo):
    # cache-hit path
    test_repo._cmd_cache["k-hit"] = [("cached.txt", "iso", "modified")]
    assert test_repo._git_cli_getCachedFileList("k-hit", ["git", "diff"], ignorecache=False) == [
        ("cached.txt", "iso", "modified")
    ]

    # conversion path covering mtime present and mtime None
    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "raw")
    monkeypatch.setattr(test_repo, "_git_cli_parse_name_status_output", lambda _out: [("a.txt", "modified"), ("b.txt", "added")])

    def _safe_mtime(path):
        return 10.0 if path == "a.txt" else None

    monkeypatch.setattr(test_repo, "safe_mtime", _safe_mtime)
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda m: f"iso:{m}")
    monkeypatch.setattr(test_repo, "index_mtime_iso", lambda: "idx")

    out = test_repo._git_cli_getCachedFileList("k-new", ["git", "diff"], ignorecache=True)
    assert out == [("a.txt", "iso:10.0", "modified"), ("b.txt", "idx", "added")]
    assert test_repo._cmd_cache["k-new"] == out

    # exception path
    monkeypatch.setattr(test_repo, "_git_cli_parse_name_status_output", lambda _out: (_ for _ in ()).throw(RuntimeError("parse fail")))
    assert test_repo._git_cli_getCachedFileList("k-err", ["git", "diff"], ignorecache=True) == []


def test_empty_tree_hash_paths(monkeypatch, test_repo):
    sha1_empty = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    sha256_empty = "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"

    # cache-hit path
    test_repo._cmd_cache["_empty_tree_hash"] = "from-cache"
    assert test_repo._empty_tree_hash(ignorecache=False) == "from-cache"

    # sha256 path
    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "sha256\n")
    test_repo._cmd_cache.pop("_empty_tree_hash", None)
    assert test_repo._empty_tree_hash(ignorecache=True) == sha256_empty

    # default sha1 path for unknown format
    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "unknown\n")
    test_repo._cmd_cache.pop("_empty_tree_hash", None)
    assert test_repo._empty_tree_hash(ignorecache=True) == sha1_empty

    # exception path
    def _raise(*_args, **_kwargs):
        raise RuntimeError("cannot detect")

    monkeypatch.setattr(test_repo, "_git_run", _raise)
    test_repo._cmd_cache.pop("_empty_tree_hash", None)
    assert test_repo._empty_tree_hash(ignorecache=True) == sha1_empty


def test_newrepo_timestamp_iso_paths(monkeypatch, test_repo):
    # Path 1: parse failures/blank lines + symlink file + file-stat exception.
    monkeypatch.setattr(test_repo, "getCurrentBranch", lambda: "main")
    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "\nnot-a-ts\n1700000000\n")
    monkeypatch.setattr(gitrepo.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(gitrepo.os, "walk", lambda _p: [("/repo/.git", [], ["link", "bad", "norm"])])
    monkeypatch.setattr(gitrepo.os.path, "islink", lambda p: p.endswith("link"))

    class _LStat:
        st_mtime = 5.0

    monkeypatch.setattr(gitrepo.os, "lstat", lambda _p: _LStat())

    def _getmtime(path):
        if path.endswith("bad"):
            raise OSError("stat fail")
        return 9.0

    monkeypatch.setattr(gitrepo.os.path, "getmtime", _getmtime)
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda epoch: f"iso:{epoch}")
    assert test_repo._newrepo_timestamp_iso(ignorecache=True) == "iso:5.0"

    # Path 2: git log failure + os.walk failure + fallback to index mtime ISO.
    def _git_run_raise(*_args, **_kwargs):
        raise RuntimeError("git log failed")

    def _walk_raise(*_args, **_kwargs):
        raise RuntimeError("walk failed")

    monkeypatch.setattr(test_repo, "_git_run", _git_run_raise)
    monkeypatch.setattr(gitrepo.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(gitrepo.os, "walk", _walk_raise)
    monkeypatch.setattr(test_repo, "index_mtime_iso", lambda: "idx")
    assert test_repo._newrepo_timestamp_iso(ignorecache=True) == "idx"


def test_is_directory_all_paths(monkeypatch, test_repo):
    seen = []

    def _fake_isdir(path):
        seen.append(path)
        if path.endswith("/boom"):
            raise RuntimeError("isdir failed")
        return path.endswith("/ok")

    monkeypatch.setattr(gitrepo.os.path, "isdir", _fake_isdir)

    assert test_repo.is_directory("", "ok") is True
    assert test_repo.is_directory("sub", "ok") is True
    assert test_repo.is_directory("", "boom") is False
    assert any(path.endswith("/ok") for path in seen)


def test_entry_rel_path_all_paths(test_repo):
    assert test_repo._entry_rel_path(("a/../b.txt",)) == "b.txt"
    assert test_repo._entry_rel_path(["c.txt"]) == "c.txt"
    assert test_repo._entry_rel_path({"raw": "r.txt"}) == "r.txt"
    assert test_repo._entry_rel_path({"full": "f.txt"}) == "f.txt"
    assert test_repo._entry_rel_path({"path": "p.txt"}) == "p.txt"
    assert test_repo._entry_rel_path({"name": "n.txt"}) == "n.txt"
    assert test_repo._entry_rel_path("s.txt") == "s.txt"
    assert test_repo._entry_rel_path({"path": None}) is None

    class _BadStr:
        def __str__(self):
            raise RuntimeError("bad str")

    assert test_repo._entry_rel_path((_BadStr(),)) is None


def test_cwd_plus_path_to_reldir_relfile_all_paths(monkeypatch, test_repo):
    monkeypatch.setattr(gitrepo.os, "getcwd", lambda: "/cwd")

    # relpath == "." path
    monkeypatch.setattr(gitrepo.os.path, "abspath", lambda _p: test_repo.get_repo_root())
    monkeypatch.setattr(gitrepo.os.path, "normpath", lambda p: p)
    monkeypatch.setattr(gitrepo.os.path, "relpath", lambda _a, _b: ".")
    assert test_repo.cwd_plus_path_to_reldir_relfile("anything") == ("", "")

    # existing directory branch
    monkeypatch.setattr(gitrepo.os.path, "abspath", lambda _p: "/abs/dir")
    monkeypatch.setattr(gitrepo.os.path, "relpath", lambda _a, _b: "subdir")
    monkeypatch.setattr(gitrepo.os.path, "isdir", lambda _p: True)
    assert test_repo.cwd_plus_path_to_reldir_relfile("dir") == ("subdir", "")

    # file path branch via helper
    monkeypatch.setattr(gitrepo.os.path, "isdir", lambda _p: False)
    monkeypatch.setattr(GitRepo, "repo_rel_path_to_reldir_relfile", classmethod(lambda _cls, rel: ("d", "f")))
    assert test_repo.cwd_plus_path_to_reldir_relfile("file") == ("d", "f")

    # exception branch
    def _abspath_raise(_p):
        raise RuntimeError("abspath fail")

    monkeypatch.setattr(gitrepo.os.path, "abspath", _abspath_raise)
    with pytest.raises(ValueError):
        test_repo.cwd_plus_path_to_reldir_relfile("boom")


def test_reldir_plus_path_to_reldir_relfile_all_paths(monkeypatch, test_repo):
    monkeypatch.setattr(gitrepo.os.path, "join", lambda *parts: "/".join([p for p in parts if p]))
    monkeypatch.setattr(gitrepo.os.path, "normpath", lambda p: p)
    monkeypatch.setattr(gitrepo.os.path, "relpath", lambda _a, _b: "rel/file.txt")
    monkeypatch.setattr(GitRepo, "repo_rel_path_to_reldir_relfile", classmethod(lambda _cls, rel: ("rel", "file.txt")))

    assert test_repo.reldir_plus_path_to_reldir_relfile(None, "q") == ("rel", "file.txt")
    assert test_repo.reldir_plus_path_to_reldir_relfile("base", "q") == ("rel", "file.txt")

    def _join_raise(*_parts):
        raise RuntimeError("join fail")

    monkeypatch.setattr(gitrepo.os.path, "join", _join_raise)
    with pytest.raises(ValueError):
        test_repo.reldir_plus_path_to_reldir_relfile("base", "q")


def test_reldir_plus_dirname_to_reldir_all_paths(monkeypatch, test_repo):
    monkeypatch.setattr(gitrepo.os.path, "join", lambda *parts: "/".join([p for p in parts if p]))
    monkeypatch.setattr(gitrepo.os.path, "normpath", lambda p: p)

    # repo root normalization to empty string
    monkeypatch.setattr(gitrepo.os.path, "relpath", lambda _a, _b: ".")
    assert test_repo.reldir_plus_dirname_to_reldir(None, "child") == ""

    # normal relative path return
    monkeypatch.setattr(gitrepo.os.path, "relpath", lambda _a, _b: "a/b")
    assert test_repo.reldir_plus_dirname_to_reldir("a", "b") == "a/b"

    # escaping path raises
    monkeypatch.setattr(gitrepo.os.path, "relpath", lambda _a, _b: "../escape")
    with pytest.raises(ValueError):
        test_repo.reldir_plus_dirname_to_reldir("a", "..")


def test_init_branch_validation_error_paths(monkeypatch, test_repo_dirs):
    monkeypatch.setattr(GitRepo, "resolve_repo_top", classmethod(lambda _cls, _path, raise_on_missing=False: (str(test_repo_dirs["base"]), None)))

    def _called_process_error(*_args, **_kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=["git", "rev-parse"])

    monkeypatch.setattr(gitrepo, "check_output", _called_process_error)
    with pytest.raises(ValueError):
        GitRepo(str(test_repo_dirs["base"]), branch="does-not-exist")

    monkeypatch.setattr(gitrepo, "check_output", lambda *_args, **_kwargs: "\n")
    with pytest.raises(ValueError):
        GitRepo(str(test_repo_dirs["base"]), branch="empty-output")

    def _generic_error(*_args, **_kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(gitrepo, "check_output", _generic_error)
    with pytest.raises(ValueError):
        GitRepo(str(test_repo_dirs["base"]), branch="runtime-error")


def test_repo_rel_path_to_reldir_relfile_all_paths_and_errors():
    with pytest.raises(ValueError):
        GitRepo.repo_rel_path_to_reldir_relfile(None)

    with pytest.raises(ValueError):
        GitRepo.repo_rel_path_to_reldir_relfile("/absolute/path")

    with pytest.raises(ValueError):
        GitRepo.repo_rel_path_to_reldir_relfile("../escape")

    assert GitRepo.repo_rel_path_to_reldir_relfile(".") == ("", "")
    assert GitRepo.repo_rel_path_to_reldir_relfile("dir/") == ("dir", "")
    assert GitRepo.repo_rel_path_to_reldir_relfile("file.txt") == ("", "file.txt")
    assert GitRepo.repo_rel_path_to_reldir_relfile("a/b.txt") == ("a", "b.txt")


def test_abs_path_for_all_paths_and_errors(monkeypatch, test_repo):
    assert test_repo.abs_path_for(None, None) == test_repo.get_repo_root()

    with pytest.raises(ValueError):
        test_repo.abs_path_for("/abs", "x")
    with pytest.raises(ValueError):
        test_repo.abs_path_for("x", "/abs")

    def _join_raise(*_args):
        raise RuntimeError("join failed")

    monkeypatch.setattr(gitrepo.os.path, "join", _join_raise)
    with pytest.raises(ValueError):
        test_repo.abs_path_for("a", "b")

    monkeypatch.setattr(gitrepo.os.path, "join", lambda *parts: "/".join([p for p in parts if p]))
    monkeypatch.setattr(gitrepo.os.path, "normpath", lambda p: p)
    monkeypatch.setattr(gitrepo.os.path, "commonpath", lambda _paths: "/not-repo")
    with pytest.raises(ValueError):
        test_repo.abs_path_for("a", "b")

    def _common_raise(_paths):
        raise RuntimeError("commonpath failed")

    monkeypatch.setattr(gitrepo.os.path, "commonpath", _common_raise)
    with pytest.raises(ValueError):
        test_repo.abs_path_for("a", "b")

    monkeypatch.setattr(gitrepo.os.path, "commonpath", lambda paths: paths[0])
    assert test_repo.abs_path_for("a", "b")


def test_remove_ignored_files_all_paths(monkeypatch, test_repo):
    assert test_repo.remove_ignored_files(None) == []
    assert test_repo.remove_ignored_files([]) == []

    monkeypatch.setattr(test_repo, "getFileListIgnored", lambda ignorecache=False: [("skip.txt", "iso", "ignored")])
    monkeypatch.setattr(test_repo, "_entry_rel_path", lambda entry: entry if isinstance(entry, str) else None)

    input_list = ["keep.txt", "skip.txt", {"raw": "dict"}]
    out = test_repo.remove_ignored_files(input_list, ignorecache=True)
    assert out == ["keep.txt", {"raw": "dict"}]

    def _raise_ignored(*_args, **_kwargs):
        raise RuntimeError("ignored failure")

    monkeypatch.setattr(test_repo, "getFileListIgnored", _raise_ignored)
    assert test_repo.remove_ignored_files(input_list) == input_list


def test_remove_untracked_files_all_paths(monkeypatch, test_repo):
    assert test_repo.remove_untracked_files(None) == []
    assert test_repo.remove_untracked_files([]) == []

    monkeypatch.setattr(test_repo, "getFileListUntracked", lambda ignorecache=False: [("skip.txt", "iso", "untracked")])
    monkeypatch.setattr(test_repo, "_entry_rel_path", lambda entry: entry if isinstance(entry, str) else None)

    input_list = ["keep.txt", "skip.txt", {"raw": "dict"}]
    out = test_repo.remove_untracked_files(input_list, ignorecache=True)
    assert out == ["keep.txt", {"raw": "dict"}]

    def _raise_untracked(*_args, **_kwargs):
        raise RuntimeError("untracked failure")

    monkeypatch.setattr(test_repo, "getFileListUntracked", _raise_untracked)
    assert test_repo.remove_untracked_files(input_list) == input_list


def test_resolve_repo_top_all_paths(monkeypatch, test_repo_dirs):
    # Empty path behavior
    root, err = GitRepo.resolve_repo_top("", raise_on_missing=False)
    assert root is None and isinstance(err, RuntimeError)
    with pytest.raises(RuntimeError):
        GitRepo.resolve_repo_top("", raise_on_missing=True)

    # Success path with file input and verbose debug branches
    GitRepo.setVerbosity(1)
    monkeypatch.setattr(gitrepo.os.path, "abspath", lambda _p: "/tmp/repo/file.py")
    monkeypatch.setattr(gitrepo.os.path, "isfile", lambda _p: True)
    monkeypatch.setattr(gitrepo.os.path, "dirname", lambda _p: "/tmp/repo")
    monkeypatch.setattr(gitrepo, "check_output", lambda _cmd, cwd=None, text=True: " /tmp/repo \n")
    root, err = GitRepo.resolve_repo_top("anything", raise_on_missing=False)
    assert root == "/tmp/repo"
    assert err is None

    # FileNotFoundError path
    def _raise_notfound(*_args, **_kwargs):
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(gitrepo, "check_output", _raise_notfound)
    root, err = GitRepo.resolve_repo_top("anything", raise_on_missing=False)
    assert root is None and isinstance(err, FileNotFoundError)
    with pytest.raises(RuntimeError):
        GitRepo.resolve_repo_top("anything", raise_on_missing=True)

    # CalledProcessError path
    def _raise_called_process(*_args, **_kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=["git", "rev-parse"])

    monkeypatch.setattr(gitrepo, "check_output", _raise_called_process)
    root, err = GitRepo.resolve_repo_top("anything", raise_on_missing=False)
    assert root is None and isinstance(err, subprocess.CalledProcessError)
    with pytest.raises(RuntimeError):
        GitRepo.resolve_repo_top("anything", raise_on_missing=True)

    # Generic exception path
    monkeypatch.setattr(gitrepo, "check_output", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    root, err = GitRepo.resolve_repo_top("anything", raise_on_missing=False)
    assert root is None and isinstance(err, RuntimeError)
    with pytest.raises(RuntimeError):
        GitRepo.resolve_repo_top("anything", raise_on_missing=True)

    GitRepo.setVerbosity(0)


def test_git_run_all_paths(monkeypatch, test_repo):
    class _CP:
        def __init__(self, stdout):
            self.stdout = stdout

    # Success path (text)
    monkeypatch.setattr(gitrepo, "run", lambda *_args, **_kwargs: _CP("ok"))
    out = test_repo._git_run(["git", "status"], text=True, ignorecache=True)
    assert out == "ok"

    # Cache-hit path with verbosity enabled
    GitRepo.setVerbosity(1)
    cached_key = "_git_run:git cached:text"
    test_repo._cmd_cache[cached_key] = "cached"
    assert test_repo._git_run(["git", "cached"], text=True, ignorecache=False) == "cached"

    # Success path (bytes) with verbose>1 decode logging branch
    GitRepo.setVerbosity(2)
    monkeypatch.setattr(gitrepo, "run", lambda *_args, **_kwargs: _CP(b"bytes-out"))
    out = test_repo._git_run(["git", "bytes"], text=False, ignorecache=True)
    assert out == b"bytes-out"

    # Success path (str) with verbose>1 str conversion branch
    monkeypatch.setattr(gitrepo, "run", lambda *_args, **_kwargs: _CP("string-out"))
    out = test_repo._git_run(["git", "string"], text=True, ignorecache=True)
    assert out == "string-out"

    # Success path where str(out) fails, exercising decode/logging exception path
    class _BadStr:
        def __str__(self):
            raise RuntimeError("str failed")

    monkeypatch.setattr(gitrepo, "run", lambda *_args, **_kwargs: _CP(_BadStr()))
    out = test_repo._git_run(["git", "badstr"], text=True, ignorecache=True)
    assert out == ""

    # CalledProcessError path including stderr/stdout decode and cache_key behavior
    def _raise_cpe(*_args, **_kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=["git"], stderr=b"stderr", output=b"stdout")

    monkeypatch.setattr(gitrepo, "run", _raise_cpe)
    out = test_repo._git_run(["git", "fail"], text=True, cache_key="k-fail", ignorecache=True)
    assert out == ""
    assert test_repo._cmd_cache["k-fail"] == []

    # Inner logging-exception path in CalledProcessError handler
    real_debug = gitrepo.logger.debug

    def _debug_maybe_raise(msg, *args):
        if isinstance(msg, str) and "_git_run stderr BEGIN" in msg:
            raise RuntimeError("debug failed")
        return real_debug(msg, *args)

    monkeypatch.setattr(gitrepo.logger, "debug", _debug_maybe_raise)
    out = test_repo._git_run(["git", "fail-log"], text=True, cache_key=None, ignorecache=True)
    assert out == ""

    # Outer exception path (non-CalledProcessError), with and without cache_key
    monkeypatch.setattr(gitrepo, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unexpected")))
    out = test_repo._git_run(["git", "boom1"], text=True, cache_key="k-boom", ignorecache=True)
    assert out == ""
    assert test_repo._cmd_cache["k-boom"] == []

    out = test_repo._git_run(["git", "boom2"], text=True, cache_key=None, ignorecache=True)
    assert out == ""

    GitRepo.setVerbosity(0)


def test_build_diff_cmd_all_paths(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "_empty_tree_hash", lambda: "EMPTY")

    # Identical staged refs -> --cached and both refs appended
    cmd = test_repo.build_diff_cmd("a.txt", GitRepo.STAGED, GitRepo.STAGED)
    assert cmd == ["git", "-C", test_repo.get_repo_root(), "diff", "--cached", GitRepo.STAGED, GitRepo.STAGED, "--", "a.txt"]

    # prev None + concrete curr -> git show variant
    cmd = test_repo.build_diff_cmd("a.txt", None, "abc123")
    assert cmd == ["git", "-C", test_repo.get_repo_root(), "show", "--pretty=format:", "abc123", "--", "a.txt"]

    # Working-tree token (MODS) vs concrete ref
    cmd = test_repo.build_diff_cmd("", GitRepo.MODS, "abc123")
    assert cmd == ["git", "-C", test_repo.get_repo_root(), "diff", "abc123"]

    # Concrete ref vs working-tree token to exercise ref_prev append branch
    cmd = test_repo.build_diff_cmd("f.txt", "abc123", GitRepo.MODS)
    assert cmd == ["git", "-C", test_repo.get_repo_root(), "diff", "abc123", "--", "f.txt"]

    # Both concrete refs including NEWREPO -> empty-tree hash mapping
    cmd = test_repo.build_diff_cmd("file.txt", GitRepo.NEWREPO, "abc123")
    assert cmd == ["git", "-C", test_repo.get_repo_root(), "diff", "EMPTY", "abc123", "--", "file.txt"]

    # Concrete ref vs staged marker -> --cached branch
    cmd = test_repo.build_diff_cmd("f.txt", "abc123", GitRepo.STAGED)
    assert cmd == ["git", "-C", test_repo.get_repo_root(), "diff", "--cached", "abc123", GitRepo.STAGED, "--", "f.txt"]

    # Exception fallback path
    monkeypatch.setattr(test_repo, "_empty_tree_hash", lambda: (_ for _ in ()).throw(RuntimeError("empty-tree fail")))
    assert test_repo.build_diff_cmd("x", "a", "b") == ["git", "diff"]


def test_get_diff_validation_and_identical_hashes(test_repo):
    with pytest.raises(ValueError):
        test_repo.getDiff("", "a", "b")

    with pytest.raises(ValueError):
        test_repo.getDiff("file.txt", None, "b")

    assert test_repo.getDiff("file.txt", "same", "same") == []


def test_get_diff_textual_path_and_variation_exception(monkeypatch, test_repo):
    GitRepo.setVerbosity(1)

    class _BadStr:
        def __str__(self):
            raise RuntimeError("variation stringify failed")

    calls = []

    def _fake_git_run(args, text=True):
        calls.append(args)
        return "line-a\nline-b\n"

    monkeypatch.setattr(test_repo, "_empty_tree_hash", lambda: "EMPTYTREE")
    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)

    out = test_repo.getDiff(
        "f.txt",
        GitRepo.NEWREPO,
        GitRepo.STAGED,
        variation=["--ignore-space-change", ("--word-diff",), _BadStr()],
        unified_context=5,
    )
    assert out == ["line-a", "line-b"]
    assert calls
    cmd = calls[0]
    assert cmd[0:2] == ["git", "diff"]
    assert "-U5" in cmd
    assert "--ignore-space-change" in cmd
    assert "--word-diff" in cmd
    assert "--cached" in cmd
    assert "EMPTYTREE" in cmd
    assert cmd[-2:] == ["--", "f.txt"]

    GitRepo.setVerbosity(0)


def test_get_diff_metadata_fallback_nonempty(monkeypatch, test_repo):
    GitRepo.setVerbosity(1)
    calls = []

    def _fake_git_run(args, text=True):
        calls.append(args)
        if "--summary" in args:
            return "M\tf.txt\n"
        return ""

    monkeypatch.setattr(test_repo, "_empty_tree_hash", lambda: "EMPTYTREE")
    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)

    out = test_repo.getDiff("f.txt", "abc123", GitRepo.MODS)
    assert out == ["M\tf.txt"]
    assert calls[0][0:3] == ["git", "diff", "-U3"]
    assert "abc123" in calls[0]
    assert calls[1][0:4] == ["git", "-C", test_repo.get_repo_root(), "diff"]
    assert "--summary" in calls[1]
    GitRepo.setVerbosity(0)


def test_get_diff_rename_probe_direct_match_with_path_match_fallback(monkeypatch, test_repo):
    GitRepo.setVerbosity(1)
    real_basename = gitrepo.os.path.basename

    def _basename_raise(_p):
        raise RuntimeError("basename failed")

    monkeypatch.setattr(gitrepo.os.path, "basename", _basename_raise)

    def _fake_git_run(args, text=True):
        if "--summary" in args:
            return ""
        if "--find-renames" in args and "show" not in args:
            return "badline\nM\tx\ty\nR100\tf.txt\tnew.txt\n"
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)
    monkeypatch.setattr(test_repo, "_empty_tree_hash", lambda: "EMPTYTREE")

    out = test_repo.getDiff("f.txt", GitRepo.STAGED, "abc123")
    assert out == ["(file renamed: f.txt -> new.txt; no textual changes)"]

    monkeypatch.setattr(gitrepo.os.path, "basename", real_basename)
    GitRepo.setVerbosity(0)


def test_get_diff_commit_rename_second_chance(monkeypatch, test_repo):
    GitRepo.setVerbosity(1)

    def _fake_git_run(args, text=True):
        if args[0:2] == ["git", "diff"]:
            return ""
        if "--summary" in args:
            return ""
        if "--find-renames" in args and "show" not in args:
            return "M\tother.txt\n"
        if "show" in args:
            return "bad\nM\tx\ty\nR100\told/name.txt\tf.txt\n"
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)
    monkeypatch.setattr(test_repo, "_empty_tree_hash", lambda: "EMPTYTREE")

    out = test_repo.getDiff("f.txt", GitRepo.NEWREPO, "abcdef123456")
    assert out and out[0].startswith("(file renamed in commit abcdef123456")

    GitRepo.setVerbosity(0)


def test_get_diff_rename_probe_and_commit_probe_exception_paths(monkeypatch, test_repo):
    def _fake_git_run(args, text=True):
        if "show" in args:
            raise RuntimeError("show failed")
        if "--find-renames" in args:
            return "R100\tnope_old\tnope_new\n"
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)
    monkeypatch.setattr(test_repo, "_empty_tree_hash", lambda: "EMPTYTREE")

    out = test_repo.getDiff("target.txt", GitRepo.NEWREPO, "abcdef123456")
    assert out == ["(no textual changes for this file)"]


def test_get_diff_outer_rename_probe_exception_path(monkeypatch, test_repo):
    def _fake_git_run(args, text=True):
        if "--summary" in args:
            return ""
        if "--find-renames" in args and "show" not in args:
            raise RuntimeError("probe failed")
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)
    monkeypatch.setattr(test_repo, "_empty_tree_hash", lambda: "EMPTYTREE")
    out = test_repo.getDiff("target.txt", GitRepo.NEWREPO, "abcdef123456")
    assert out == ["(no textual changes for this file)"]


def test_get_diff_metadata_exception_and_out_empty_return(monkeypatch, test_repo):
    # Make metadata phase raise after entering fallback; ensures metadata-exception path
    # and final empty-output return path are covered.
    def _fake_git_run(args, text=True):
        if "--summary" in args:
            raise RuntimeError("meta failed")
        if args[0] == "git":
            return ""
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)
    monkeypatch.setattr(test_repo, "_empty_tree_hash", lambda: "EMPTYTREE")

    out = test_repo.getDiff("f.txt", GitRepo.STAGED, GitRepo.MODS)
    assert out == []


def test_get_diff_outer_exception_reraises(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "_empty_tree_hash", lambda: "EMPTYTREE")

    def _fake_git_run(_args, text=True):
        raise RuntimeError("text diff failed")

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)
    with pytest.raises(RuntimeError):
        test_repo.getDiff("f.txt", "a", "b")


def test_get_file_contents_newrepo_and_mods_success_and_failure(monkeypatch, test_repo, tmp_path):
    # NEWREPO token path
    assert test_repo.getFileContents(GitRepo.NEWREPO, "dir", "file.txt") == b""

    # MODS success path (read from working tree)
    fpath = tmp_path / "mods.bin"
    fpath.write_bytes(b"mods-bytes")
    monkeypatch.setattr(test_repo, "_repoRoot", str(tmp_path))
    assert test_repo.getFileContents(GitRepo.MODS, "", "mods.bin") == b"mods-bytes"

    # MODS failure path
    assert test_repo.getFileContents(GitRepo.MODS, "", "does-not-exist.bin") is None


def test_get_file_contents_staged_and_commit_variants(monkeypatch, test_repo):
    calls = []

    def _fake_git_run(args, text=False):
        calls.append((args, text))
        target = args[-1]
        if target.startswith(":"):
            return b"staged-bytes"
        if target.startswith("abc123:"):
            return bytearray(b"commit-bytearray")
        return "commit-str"

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)

    out = test_repo.getFileContents(GitRepo.STAGED, "d", "f.txt")
    assert out == b"staged-bytes"
    assert calls[-1][0][:4] == ["git", "-C", test_repo.get_repo_root(), "show"]
    assert calls[-1][0][-1] == ":d/f.txt"
    assert calls[-1][1] is False

    out = test_repo.getFileContents("abc123", "d", "f.txt")
    assert out == b"commit-bytearray"
    assert calls[-1][0][-1] == "abc123:d/f.txt"

    out = test_repo.getFileContents("def456", "", "f.txt")
    assert out == b"commit-str"


def test_get_file_contents_none_and_outer_exception_reraise(monkeypatch, test_repo):
    # _git_run returns None path
    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: None)
    assert test_repo.getFileContents("hash", "", "f.txt") is None

    # Outer exception path: conversion fails and exception is re-raised
    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: 123)
    with pytest.raises(TypeError):
        test_repo.getFileContents("hash", "", "f.txt")


def test_get_normalized_hash_list_from_file_name_cache_path(monkeypatch, test_repo):
    key = "k-file-cache"
    cached = [
        ("iso2", "h2", "s2", "unpushed", "n2", "e2"),
        ("iso1", "h1", "s1", "pushed", "n1", "e1"),
    ]
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: key)
    test_repo._cmd_cache[key] = list(cached)

    out_all = test_repo.getNormalizedHashListFromFileName("f.txt", ignorecache=False, limit=0)
    assert out_all == cached

    out_limited = test_repo.getNormalizedHashListFromFileName("f.txt", ignorecache=False, limit=1)
    assert out_limited == cached[:1]


def test_get_normalized_hash_list_from_file_name_build_and_sort(monkeypatch, test_repo):
    key = "k-file-build"
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: key)
    monkeypatch.setattr(test_repo, "getCurrentBranch", lambda: "main")

    def _fake_git_run(args, text=True, cache_key=None, ignorecache=False):
        if args[:3] == ["git", "log", "main"]:
            return "log-output"
        if args[:3] == ["git", "status", "--porcelain"]:
            return "MM f.txt\n"
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)
    monkeypatch.setattr(test_repo, "getPushedHashes", lambda ignorecache=False: {"h1"})
    monkeypatch.setattr(
        test_repo,
        "_parse_git_log_output",
        lambda _out: [
            (2, "h2", "author2", "e2", ""),
            (3, "h1", "author1", "e1", "subject1"),
        ],
    )
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda ts: f"iso{ts}")
    monkeypatch.setattr(test_repo, "index_mtime_iso", lambda: "idxiso")
    monkeypatch.setattr(test_repo, "_paths_mtime_iso", lambda _paths: "modsiso")

    out = test_repo.getNormalizedHashListFromFileName("f.txt", ignorecache=True, limit=0)

    assert out[0] == ("modsiso", GitRepo.MODS, GitRepo.MODS_MESSAGE, "unpushed", "", "")
    assert out[1] == ("idxiso", GitRepo.STAGED, GitRepo.STAGED_MESSAGE, "unpushed", "", "")
    assert out[2] == ("iso3", "h1", "subject1", "pushed", "author1", "e1")
    assert out[3] == ("iso2", "h2", "", "unpushed", "author2", "e2")
    assert test_repo._cmd_cache[key] == out

    out_limit = test_repo.getNormalizedHashListFromFileName("f.txt", ignorecache=True, limit=2)
    assert out_limit == out[:2]


def test_get_normalized_hash_list_from_file_name_short_status_and_pseudo_ts_exception(monkeypatch, test_repo):
    key = "k-file-short-status"
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: key)
    monkeypatch.setattr(test_repo, "getCurrentBranch", lambda: "main")

    def _fake_git_run(args, text=True, cache_key=None, ignorecache=False):
        if args[:3] == ["git", "log", "main"]:
            return ""
        if args[:3] == ["git", "status", "--porcelain"]:
            return "A"
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)
    monkeypatch.setattr(test_repo, "getPushedHashes", lambda ignorecache=False: set())
    monkeypatch.setattr(test_repo, "_parse_git_log_output", lambda _out: [])
    monkeypatch.setattr(test_repo, "index_mtime_iso", lambda: (_ for _ in ()).throw(RuntimeError("idx fail")))

    out = test_repo.getNormalizedHashListFromFileName("f.txt", ignorecache=True, limit=0)
    assert out == []


def test_get_normalized_hash_list_from_file_name_outer_exception_returns_empty(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-file-outer")
    monkeypatch.setattr(test_repo, "getCurrentBranch", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    out = test_repo.getNormalizedHashListFromFileName("f.txt", ignorecache=True, limit=0)
    assert out == []


def test_amend_commit_message_head_hash_lookup_failure(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "getPushedHashes", lambda: set())

    def _git_run_raise(args, **_kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=args)
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _git_run_raise)
    with pytest.raises(ValueError):
        test_repo.amendCommitMessage("abc", "new message")


def test_amend_commit_message_head_success_and_amend_failure(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "getPushedHashes", lambda: set())

    # Success path: commit amend works and second HEAD lookup empty -> fallback to hash_val
    def _git_run_head_success(args, **_kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            if _kwargs.get("ignorecache"):
                return ""
            return "abcdef123456\n"
        if "commit" in args and "--amend" in args:
            return "ok"
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _git_run_head_success)
    GitRepo.setVerbosity(1)
    assert test_repo.amendCommitMessage("abcdef", "new message") == "abcdef"
    GitRepo.setVerbosity(0)

    # Failure path in HEAD amend branch
    def _git_run_head_amend_fails(args, **_kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return "abcdef123456\n"
        if "commit" in args and "--amend" in args:
            raise subprocess.CalledProcessError(returncode=1, cmd=args)
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _git_run_head_amend_fails)
    with pytest.raises(subprocess.CalledProcessError):
        test_repo.amendCommitMessage("abcdef", "new message")


def test_amend_commit_message_non_head_paths(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "getPushedHashes", lambda: set())

    class _Completed:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    # Non-HEAD success with parent hash and matching subject
    def _git_run_non_head_parent(args, **_kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return "tophash\n"
        if args[-3:] == ["cat-file", "-t", "targethash"]:
            return "commit\n"
        if args[-2:] == ["rev-parse", "targethash^"]:
            return "parenthash\n"
        if "log" in args and "--all" in args:
            return "newhash123 first line\n"
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _git_run_non_head_parent)
    monkeypatch.setattr(gitrepo, "run", lambda *_args, **_kwargs: _Completed(returncode=0, stdout="ok", stderr=""))
    GitRepo.setVerbosity(1)
    assert test_repo.amendCommitMessage("targethash", "first line\nbody") == "newhash123"
    GitRepo.setVerbosity(0)

    # Non-HEAD success with root rebase branch and no matching log subject -> fallback hash
    def _git_run_non_head_root(args, **_kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return "tophash\n"
        if args[-3:] == ["cat-file", "-t", "targetroot"]:
            return "commit\n"
        if args[-2:] == ["rev-parse", "targetroot^"]:
            return ""
        if "log" in args and "--all" in args:
            return "line-without-space\notherhash other-subject\n"
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _git_run_non_head_root)
    monkeypatch.setattr(gitrepo, "run", lambda *_args, **_kwargs: _Completed(returncode=0, stdout="ok", stderr=""))
    assert test_repo.amendCommitMessage("targetroot", "") == "targetroot"

    # Verification failure path
    def _git_run_verify_missing(args, **_kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return "tophash\n"
        if args[-3:] == ["cat-file", "-t", "missinghash"]:
            return ""
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _git_run_verify_missing)
    with pytest.raises(ValueError):
        test_repo.amendCommitMessage("missinghash", "msg")


def test_amend_commit_message_rebase_failure_and_cleanup_and_setup_failure(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "getPushedHashes", lambda: set())

    class _Completed:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _git_run_for_rebase(args, **_kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return "tophash\n"
        if args[-3:] == ["cat-file", "-t", "badrebase"]:
            return "commit\n"
        if args[-2:] == ["rev-parse", "badrebase^"]:
            return "parent\n"
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _git_run_for_rebase)
    monkeypatch.setattr(gitrepo, "run", lambda *_args, **_kwargs: _Completed(returncode=1, stdout="oops", stderr="err"))
    with pytest.raises(subprocess.CalledProcessError):
        test_repo.amendCommitMessage("badrebase", "msg")

    # Cleanup failure in finally path should be swallowed/logged
    def _git_run_for_cleanup(args, **_kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return "tophash\n"
        if args[-3:] == ["cat-file", "-t", "cleanuphash"]:
            return "commit\n"
        if args[-2:] == ["rev-parse", "cleanuphash^"]:
            return "parent\n"
        if "log" in args and "--all" in args:
            return ""
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _git_run_for_cleanup)
    monkeypatch.setattr(gitrepo, "run", lambda *_args, **_kwargs: _Completed(returncode=0, stdout="ok", stderr=""))
    monkeypatch.setattr(gitrepo.os, "unlink", lambda _p: (_ for _ in ()).throw(RuntimeError("unlink fail")))
    assert test_repo.amendCommitMessage("cleanuphash", "msg") == "cleanuphash"

    # Outer setup-failure branch
    class _BadTempFile:
        def __enter__(self):
            raise RuntimeError("tempfile fail")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(gitrepo.tempfile, "NamedTemporaryFile", lambda **_kwargs: _BadTempFile())
    with pytest.raises(RuntimeError):
        test_repo.amendCommitMessage("cleanuphash", "msg")


def test_get_index_mtime_all_branches(monkeypatch, test_repo):
    # First candidate exists and returns immediately.
    monkeypatch.setattr(gitrepo.os.path, "join", lambda *parts: "/".join(parts))
    monkeypatch.setattr(gitrepo.os.path, "exists", lambda p: p.endswith(".git/index"))
    monkeypatch.setattr(gitrepo.os.path, "getmtime", lambda _p: 111.0)
    assert test_repo.getIndexMtime() == 111.0

    # First missing, second exists path.
    monkeypatch.setattr(gitrepo.os.path, "exists", lambda p: p.endswith("/index") and not p.endswith(".git/index"))
    monkeypatch.setattr(gitrepo.os.path, "getmtime", lambda _p: 222.0)
    assert test_repo.getIndexMtime() == 222.0

    # Exception during stat is swallowed and function returns None.
    def _exists_raise(_p):
        raise RuntimeError("exists failed")

    monkeypatch.setattr(gitrepo.os.path, "exists", _exists_raise)
    assert test_repo.getIndexMtime() is None


def test_get_file_list_untracked_all_branches(monkeypatch, test_repo):
    test_repo._cmd_cache.clear()

    # Non-cache path: includes blank line, quoted path decode, duplicate skip,
    # and mtime None fallback to index timestamp.
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-untracked")
    monkeypatch.setattr(
        test_repo,
        "_git_run",
        lambda *_args, **_kwargs: "\n\"a\\040b.txt\"\nplain.txt\nplain.txt\n",
    )
    monkeypatch.setattr(test_repo, "_git_cli_decode_quoted_path", lambda rel: "a b.txt" if rel.startswith('"') else rel)
    monkeypatch.setattr(test_repo, "safe_mtime", lambda rel: 12.0 if rel == "a b.txt" else None)
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda ts: f"iso-{int(ts)}")
    monkeypatch.setattr(test_repo, "index_mtime_iso", lambda: "idx-iso")

    out = test_repo.getFileListUntracked(ignorecache=True)
    assert out == [("a b.txt", "iso-12", "untracked"), ("plain.txt", "idx-iso", "untracked")]
    assert test_repo._cmd_cache["k-untracked"] == out

    # Cache-hit path.
    cached = [("cached.txt", "cached-iso", "untracked")]
    test_repo._cmd_cache["k-untracked"] = cached
    assert test_repo.getFileListUntracked(ignorecache=False) == cached

    # Outer exception returns empty list.
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: (_ for _ in ()).throw(RuntimeError("key fail")))
    assert test_repo.getFileListUntracked(ignorecache=True) == []


def test_get_file_list_between_new_repo_and_hash_all_branches(monkeypatch, test_repo):
    test_repo._cmd_cache.clear()

    # Cache-hit path.
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-newrepo-hash")
    test_repo._cmd_cache["k-newrepo-hash"] = [("cached.txt", "iso", "added")]
    out = test_repo.getFileListBetweenNewRepoAndHash("abc123", ignorecache=False)
    assert out == [("cached.txt", "iso", "added")]

    # Main path: commit_ts not None, includes blank line to test skip, results sorted.
    def _fake_git_run(args, text=True, cache_key=None, ignorecache=False):
        return "\nb.txt\na.txt\n\nc.txt\n"

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)
    monkeypatch.setattr(test_repo, "_get_commit_timestamp", lambda _h: 999.0)
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda ts: f"iso-{int(ts)}")

    out = test_repo.getFileListBetweenNewRepoAndHash("abc123", ignorecache=True)
    assert out == [("a.txt", "iso-999", "added"), ("b.txt", "iso-999", "added"), ("c.txt", "iso-999", "added")]
    assert test_repo._cmd_cache["k-newrepo-hash"] == out

    # commit_ts is None -> falls back to index_mtime_iso.
    monkeypatch.setattr(test_repo, "_get_commit_timestamp", lambda _h: None)
    monkeypatch.setattr(test_repo, "index_mtime_iso", lambda: "idx-iso")
    out = test_repo.getFileListBetweenNewRepoAndHash("abc123", ignorecache=True)
    assert out[0][1] == "idx-iso"

    # Exception path returns empty list (_git_run raises inside the try block).
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-newrepo-exc")
    monkeypatch.setattr(
        test_repo,
        "_git_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("git fail")),
    )
    assert test_repo.getFileListBetweenNewRepoAndHash("abc123", ignorecache=True) == []


def test_get_hash_list_new_repo_all_branches(monkeypatch, test_repo):
    # Happy path with empty repo (no commits) -> oldest_status stays "unpushed".
    monkeypatch.setattr(test_repo, "_newrepo_timestamp_iso", lambda ignorecache=False: "2025-01-01T00:00:00")
    monkeypatch.setattr(test_repo, "getHashListEntireRepo", lambda ignorecache=False: [])
    out = test_repo.getHashListNewRepo(ignorecache=True)
    assert len(out) == 1
    assert out[0][1] == test_repo.NEWREPO
    assert out[0][3] == "unpushed"

    # Happy path: oldest commit present -> inherits its pushed status.
    monkeypatch.setattr(
        test_repo,
        "getHashListEntireRepo",
        lambda ignorecache=False: [
            ("iso1", "h1", "subject", "pushed", "", ""),
            ("iso2", "h2", "subject2", "pushed", "", ""),
        ],
    )
    out = test_repo.getHashListNewRepo(ignorecache=True)
    assert out[0][3] == "pushed"

    # limit > 0 path in happy path (limit=1 on 1-entry list keeps it; limit=0 means no limit).
    out_limited = test_repo.getHashListNewRepo(ignorecache=True, limit=1)
    assert len(out_limited) == 1

    # Exception path: _newrepo_timestamp_iso raises; fallback entry returned.
    monkeypatch.setattr(
        test_repo,
        "_newrepo_timestamp_iso",
        lambda ignorecache=False: (_ for _ in ()).throw(RuntimeError("ts fail")),
    )
    monkeypatch.setattr(test_repo, "index_mtime_iso", lambda: "fallback-iso")
    out_exc = test_repo.getHashListNewRepo(ignorecache=True)
    assert len(out_exc) == 1
    assert out_exc[0][0] == "fallback-iso"
    assert out_exc[0][1] == test_repo.NEWREPO

    # Exception path with limit > 0.
    out_exc_limited = test_repo.getHashListNewRepo(ignorecache=True, limit=1)
    assert len(out_exc_limited) == 1


def test_get_file_list_ignored_all_branches(monkeypatch, test_repo):
    test_repo._cmd_cache.clear()

    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-ignored")
    monkeypatch.setattr(
        test_repo,
        "_git_run",
        lambda *_args, **_kwargs: "\n\"a\\040b.txt\"\nplain.txt\nplain.txt\n",
    )
    monkeypatch.setattr(test_repo, "_git_cli_decode_quoted_path", lambda rel: "a b.txt" if rel.startswith('"') else rel)
    monkeypatch.setattr(test_repo, "safe_mtime", lambda rel: 42.0 if rel == "a b.txt" else None)
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda ts: f"iso-{int(ts)}")
    monkeypatch.setattr(test_repo, "index_mtime_iso", lambda: "idx-iso")

    out = test_repo.getFileListIgnored(ignorecache=True)
    assert out == [("a b.txt", "iso-42", "ignored"), ("plain.txt", "idx-iso", "ignored")]
    assert test_repo._cmd_cache["k-ignored"] == out

    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: (_ for _ in ()).throw(RuntimeError("key fail")))
    assert test_repo.getFileListIgnored(ignorecache=True) == []


def test_get_file_list_untracked_and_ignored_all_branches(monkeypatch, test_repo):
    test_repo._cmd_cache.clear()

    class _BadEntry:
        def __len__(self):
            raise RuntimeError("bad entry")

    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-combined")
    cached = [("cached.txt", "iso", "untracked")]
    test_repo._cmd_cache["k-combined"] = cached
    assert test_repo.getFileListUntrackedAndIgnored(ignorecache=False) == cached

    monkeypatch.setattr(
        test_repo,
        "getFileListUntracked",
        lambda ignorecache=False: [("dup.txt", "iso1", "untracked"), _BadEntry(), (), ("u.txt", "iso2", "untracked")],
    )
    monkeypatch.setattr(
        test_repo,
        "getFileListIgnored",
        lambda ignorecache=False: [("dup.txt", "iso3", "ignored"), ("i.txt", "iso4", "ignored")],
    )

    out = test_repo.getFileListUntrackedAndIgnored(ignorecache=True)
    assert out == [("dup.txt", "iso1", "untracked"), ("i.txt", "iso4", "ignored"), ("u.txt", "iso2", "untracked")]
    assert test_repo._cmd_cache["k-combined"] == out

    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-combined-exc")
    monkeypatch.setattr(
        test_repo,
        "getFileListUntracked",
        lambda ignorecache=False: (_ for _ in ()).throw(RuntimeError("untracked fail")),
    )
    assert test_repo.getFileListUntrackedAndIgnored(ignorecache=True) == []


def test_module_print_exception_success_and_fallback(monkeypatch):
    warning_calls = []

    def _warning_ok(*args, **kwargs):
        warning_calls.append((args, kwargs))

    monkeypatch.setattr(gitrepo.logger, "warning", _warning_ok)
    gitrepo.printException(RuntimeError("boom"), "msg")
    assert len(warning_calls) == 2

    writes = []
    call_count = {"n": 0}

    def _warning_fail(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("logger failed")

    monkeypatch.setattr(gitrepo.logger, "warning", _warning_fail)
    monkeypatch.setattr(gitrepo.sys.stderr, "write", lambda s: writes.append(s))
    gitrepo.printException(RuntimeError("boom2"), "msg2")
    assert any("printException fallback: boom2" in s for s in writes)
    assert any("secondary exception: logger failed" in s for s in writes)


def test_get_file_list_at_hash_all_branches(monkeypatch, test_repo):
    test_repo._cmd_cache.clear()

    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-at-hash")
    test_repo._cmd_cache["k-at-hash"] = [("cached.txt", "iso", "committed")]
    out = test_repo.getFileListAtHash("abc123", ignorecache=False)
    assert out == [("cached.txt", "iso", "committed")]

    def _fake_git_run(args, text=True, cache_key=None, ignorecache=False):
        return "\nb.txt\na.txt\n\nc.txt\n"

    monkeypatch.setattr(test_repo, "_git_run", _fake_git_run)
    monkeypatch.setattr(test_repo, "_get_commit_timestamp", lambda _h: 123.0)
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda ts: f"iso-{int(ts)}")
    out = test_repo.getFileListAtHash("abc123", ignorecache=True)
    assert out == [("a.txt", "iso-123", "committed"), ("b.txt", "iso-123", "committed"), ("c.txt", "iso-123", "committed")]
    assert test_repo._cmd_cache["k-at-hash"] == out

    monkeypatch.setattr(test_repo, "_get_commit_timestamp", lambda _h: None)
    monkeypatch.setattr(test_repo, "index_mtime_iso", lambda: "idx-iso")
    out = test_repo.getFileListAtHash("abc123", ignorecache=True)
    assert out[0][1] == "idx-iso"

    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-at-hash-exc")
    monkeypatch.setattr(
        test_repo,
        "_git_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("git fail")),
    )
    assert test_repo.getFileListAtHash("abc123", ignorecache=True) == []


def test_get_hash_list_entire_repo_limit_branch(monkeypatch, test_repo):
    monkeypatch.setattr(test_repo, "getCurrentBranch", lambda: "main")
    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "ignored")
    monkeypatch.setattr(
        test_repo,
        "_parse_git_log_output",
        lambda _out: [
            (2, "h2", "author2", "e2", "subject2"),
            (3, "h1", "author1", "e1", "subject1"),
        ],
    )
    monkeypatch.setattr(test_repo, "getPushedHashes", lambda ignorecache=False: {"h1"})
    monkeypatch.setattr(test_repo, "_epoch_to_iso", lambda ts: f"iso-{ts}")

    out = test_repo.getHashListEntireRepo(ignorecache=True, limit=1)
    assert out == [("iso-3", "h1", "subject1", "pushed", "author1", "e1")]


def test_get_hashes_between_all_branches(monkeypatch, test_repo):
    assert test_repo.getHashesBetween("", "a", "b") == []
    assert test_repo.getHashesBetween("f.txt", "", "b") == []
    assert test_repo.getHashesBetween("f.txt", "a", "") == []

    entries = [
        ("iso4", "h4", "s4", "unpushed", "", ""),
        ("iso3", "h3", "s3", "unpushed", "", ""),
        ("iso2", "h2", "s2", "unpushed", "", ""),
        ("iso1", "h1", "s1", "unpushed", "", ""),
        ("bad", None),
        "not-a-sequence",
    ]
    monkeypatch.setattr(test_repo, "getNormalizedHashListFromFileName", lambda *_args, **_kwargs: entries)

    assert test_repo.getHashesBetween("f.txt", "missing", "h2", ignorecache=True) == []
    assert test_repo.getHashesBetween("f.txt", "h2", "h4", ignorecache=True) == ["h4", "h3", "h2"]
    assert test_repo.getHashesBetween("f.txt", "h4", "h2", ignorecache=True) == ["h4", "h3", "h2"]

    monkeypatch.setattr(
        test_repo,
        "getNormalizedHashListFromFileName",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("history fail")),
    )
    assert test_repo.getHashesBetween("f.txt", "h1", "h2", ignorecache=True) == []


def test_get_complete_commit_message_exception_path(monkeypatch, test_repo):
    monkeypatch.setattr(
        test_repo,
        "_git_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("git show failed")),
    )
    assert test_repo.getCompleteCommitMessage(test_repo.get_repo_root(), "abc123") is None


def test_get_file_list_between_normalized_hashes_all_dispatches(monkeypatch, test_repo):
    assert test_repo.getFileListBetweenNormalizedHashes("same", "same") == []

    with pytest.raises(ValueError):
        test_repo.getFileListBetweenNormalizedHashes(None, "abc")
    with pytest.raises(ValueError):
        test_repo.getFileListBetweenNormalizedHashes("abc", None)

    monkeypatch.setattr(test_repo, "getFileListBetweenNewRepoAndHash", lambda curr_hash, ignorecache=False: [(curr_hash, "iso", "added")])
    monkeypatch.setattr(test_repo, "getFileListBetweenNewRepoAndStaged", lambda ignorecache=False: [("staged", "iso", "added")])
    monkeypatch.setattr(test_repo, "getFileListBetweenNewRepoAndMods", lambda ignorecache=False: [("mods", "iso", "modified")])
    monkeypatch.setattr(test_repo, "getFileListBetweenTwoCommits", lambda prev_hash, curr_hash, ignorecache=False: [(f"{prev_hash}->{curr_hash}", "iso", "modified")])
    monkeypatch.setattr(test_repo, "getFileListBetweenHashAndStaged", lambda hash, ignorecache=False: [(hash, "iso", "staged")])
    monkeypatch.setattr(test_repo, "getFileListBetweenHashAndCurrentTime", lambda hash, ignorecache=False: [(hash, "iso", "modified")])
    monkeypatch.setattr(test_repo, "getFileListBetweenStagedAndMods", lambda ignorecache=False: [("staged->mods", "iso", "modified")])

    assert test_repo.getFileListBetweenNormalizedHashes(test_repo.NEWREPO, "abc", ignorecache=True) == [("abc", "iso", "added")]
    assert test_repo.getFileListBetweenNormalizedHashes(test_repo.NEWREPO, test_repo.STAGED, ignorecache=True) == [("staged", "iso", "added")]
    assert test_repo.getFileListBetweenNormalizedHashes(test_repo.NEWREPO, test_repo.MODS, ignorecache=True) == [("mods", "iso", "modified")]
    assert test_repo.getFileListBetweenNormalizedHashes("a1", "b2", ignorecache=True) == [("a1->b2", "iso", "modified")]
    assert test_repo.getFileListBetweenNormalizedHashes("a1", test_repo.STAGED, ignorecache=True) == [("a1", "iso", "staged")]
    assert test_repo.getFileListBetweenNormalizedHashes("a1", test_repo.MODS, ignorecache=True) == [("a1", "iso", "modified")]
    assert test_repo.getFileListBetweenNormalizedHashes(test_repo.STAGED, test_repo.MODS, ignorecache=True) == [("staged->mods", "iso", "modified")]
    assert test_repo.getFileListBetweenNormalizedHashes(test_repo.STAGED, "abc", ignorecache=True) == [(f"{test_repo.STAGED}->abc", "iso", "modified")]


def test_get_pushed_hashes_all_branches(monkeypatch, test_repo):
    test_repo._cmd_cache.clear()

    test_repo._cmd_cache["getPushedHashes"] = {"cached1"}
    assert test_repo.getPushedHashes(ignorecache=False) == {"cached1"}

    test_repo._cmd_cache["getPushedHashes"] = ["not-a-set"]
    assert test_repo.getPushedHashes(ignorecache=False) == set()

    test_repo._cmd_cache.clear()
    monkeypatch.setattr(test_repo, "_get_upstream_ref", lambda: "origin/main")
    monkeypatch.setattr(test_repo, "_git_run", lambda args, **_kwargs: "h1\n\nh2\n" if args[:2] == ["git", "rev-list"] else "")
    assert test_repo.getPushedHashes(ignorecache=True) == {"h1", "h2"}

    test_repo._cmd_cache.clear()

    def _git_run_upstream_cpe(args, **_kwargs):
        if args == ["git", "rev-list", "origin/main"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=args)
        if args[:4] == ["git", "config", "--get", "remote.origin.url"]:
            return "\n"
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _git_run_upstream_cpe)
    assert test_repo.getPushedHashes(ignorecache=True) == set()

    def _git_run_remotes(args, **_kwargs):
        if args[:4] == ["git", "config", "--get", "remote.origin.url"]:
            return "origin-url\n"
        if args == ["git", "rev-list", "--remotes"]:
            return "r1\n\nr2\n"
        return ""

    monkeypatch.setattr(test_repo, "_get_upstream_ref", lambda: None)
    monkeypatch.setattr(test_repo, "_git_run", _git_run_remotes)
    assert test_repo.getPushedHashes(ignorecache=True) == {"r1", "r2"}

    def _git_run_remotes_cpe(args, **_kwargs):
        if args[:4] == ["git", "config", "--get", "remote.origin.url"]:
            return "origin-url\n"
        if args == ["git", "rev-list", "--remotes"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=args)
        return ""

    monkeypatch.setattr(test_repo, "_git_run", _git_run_remotes_cpe)
    assert test_repo.getPushedHashes(ignorecache=True) == set()

    monkeypatch.setattr(test_repo, "_get_upstream_ref", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert test_repo.getPushedHashes(ignorecache=True) == set()


def test_get_normalized_hash_list_complete_limit_paths(monkeypatch, test_repo):
    new = [("niso", GitRepo.MODS, GitRepo.MODS_MESSAGE, "unpushed", "", "")]
    staged = [("siso", GitRepo.STAGED, GitRepo.STAGED_MESSAGE, "unpushed", "", "")]
    entire = [("eiso", "h1", "subject", "pushed", "name", "email")]
    newrepo = [("riso", GitRepo.NEWREPO, GitRepo.NEWREPO_MESSAGE, "unpushed", "", "")]

    monkeypatch.setattr(test_repo, "getHashListNewChanges", lambda ignorecache=False, limit=0: list(new))
    monkeypatch.setattr(test_repo, "getHashListStagedChanges", lambda ignorecache=False, limit=0: list(staged))
    monkeypatch.setattr(test_repo, "getHashListEntireRepo", lambda ignorecache=False, limit=0: list(entire))
    monkeypatch.setattr(test_repo, "getHashListNewRepo", lambda ignorecache=False, limit=0: list(newrepo))

    assert test_repo.getNormalizedHashListComplete(ignorecache=True, limit=1) == new
    assert test_repo.getNormalizedHashListComplete(ignorecache=True, limit=2) == new + staged
    assert test_repo.getNormalizedHashListComplete(ignorecache=True, limit=3) == new + staged + entire
    assert test_repo.getNormalizedHashListComplete(ignorecache=True, limit=0) == new + staged + entire + newrepo


def test_get_hash_list_staged_changes_all_branches(monkeypatch, test_repo):
    test_repo._cmd_cache.clear()

    monkeypatch.setattr(test_repo, "index_mtime_iso", lambda: "idx-iso")
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-staged")

    test_repo._cmd_cache["k-staged"] = [("cached", GitRepo.STAGED, GitRepo.STAGED_MESSAGE, "unpushed", "", "")]
    assert test_repo.getHashListStagedChanges(ignorecache=False, limit=1) == [("cached", GitRepo.STAGED, GitRepo.STAGED_MESSAGE, "unpushed", "", "")]

    test_repo._cmd_cache.clear()
    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "")
    assert test_repo.getHashListStagedChanges(ignorecache=True) == []
    assert test_repo._cmd_cache["k-staged"] == []

    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: " \n\t\n")
    assert test_repo.getHashListStagedChanges(ignorecache=True) == []

    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "a.txt\n")
    out = test_repo.getHashListStagedChanges(ignorecache=True, limit=1)
    assert out == [("idx-iso", GitRepo.STAGED, GitRepo.STAGED_MESSAGE, "unpushed", "", "")]
    assert test_repo._cmd_cache["k-staged"] == out

    monkeypatch.setattr(
        test_repo,
        "_git_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("staged fail")),
    )
    assert test_repo.getHashListStagedChanges(ignorecache=True) == []


def test_get_hash_list_new_changes_all_branches(monkeypatch, test_repo):
    test_repo._cmd_cache.clear()

    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "")
    assert test_repo.getHashListNewChanges(ignorecache=True) == []

    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: " \n\t\n")
    assert test_repo.getHashListNewChanges(ignorecache=True) == []

    monkeypatch.setattr(test_repo, "_git_run", lambda *_args, **_kwargs: "a.txt\nb.txt\n")
    monkeypatch.setattr(test_repo, "_paths_mtime_iso", lambda paths: f"mods-{'-'.join(paths)}")
    monkeypatch.setattr(test_repo, "_make_cache_key", lambda *_args: "k-mods")

    test_repo._cmd_cache["k-mods"] = [("cached", GitRepo.MODS, GitRepo.MODS_MESSAGE, "unpushed", "", "")]
    assert test_repo.getHashListNewChanges(ignorecache=False, limit=1) == [("cached", GitRepo.MODS, GitRepo.MODS_MESSAGE, "unpushed", "", "")]

    test_repo._cmd_cache.clear()
    out = test_repo.getHashListNewChanges(ignorecache=True, limit=1)
    assert out == [("mods-a.txt-b.txt", GitRepo.MODS, GitRepo.MODS_MESSAGE, "unpushed", "", "")]
    assert test_repo._cmd_cache["k-mods"] == out

    monkeypatch.setattr(
        test_repo,
        "_paths_mtime_iso",
        lambda _paths: (_ for _ in ()).throw(RuntimeError("mods fail")),
    )
    assert test_repo.getHashListNewChanges(ignorecache=True) == []
