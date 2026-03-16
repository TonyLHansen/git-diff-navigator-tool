import types

from gitdiffnavtool import AppBase, RepoModeFileList, RepoModeHistoryList, SaveSnapshotModal
from gitrepo import GitRepo


class FakeEvent:
    def __init__(self, key=None, character=None):
        self.key = key
        self.character = character
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeGitRepoForSnapshots:
    def __init__(self):
        self.hashes_between_calls = []

    def abs_path_for(self, rel_dir, rel_file):
        base = "/tmp/repo"
        if rel_dir:
            return f"{base}/{rel_dir}/{rel_file}"
        return f"{base}/{rel_file}"

    def getHashesBetween(self, file_name, prev_hash, curr_hash, ignorecache=False):
        self.hashes_between_calls.append((file_name, prev_hash, curr_hash, ignorecache))
        return ["selected_curr", "mid_1", "mid_2", "selected_prev"]

    def getCommitTimestamp(self, hashval):
        return None

    def getIndexMtime(self):
        return None


class HarnessForKeyWHelper:
    def __init__(self):
        self.errors = []
        self.pushed = []
        self.app = types.SimpleNamespace(
            NO_DIR="",
            NO_FILE="",
            rel_dir="",
            rel_file="README.md",
            previous_hash="top_prev",
            current_hash="top_curr",
            output_directory=None,
            write_adds_timestamps=False,
            write_hash_length=12,
            write_uses_mtime=True,
            gitRepo=_FakeGitRepoForSnapshots(),
            push_screen=self._push_screen,
        )

    def _push_screen(self, modal):
        self.pushed.append(modal)

    def printException(self, exc, context=None):
        self.errors.append((exc, context))

    def _compute_selected_pair(self):
        # Simulate a non-top current selection in history mode.
        self.app.previous_hash = "selected_prev"
        self.app.current_hash = "selected_curr"
        return ("selected_prev", "selected_curr")


def test_key_w_helper_uses_selected_pair_hashes_when_available():
    h = HarnessForKeyWHelper()

    AppBase.key_w_helper(h, None)

    assert len(h.pushed) == 1
    modal = h.pushed[0]
    assert isinstance(modal, SaveSnapshotModal)
    assert modal.prev_hash == "selected_prev"
    assert modal.curr_hash == "selected_curr"
    assert modal.all_hashes == ["selected_curr", "mid_1", "mid_2", "selected_prev"]
    assert "(a)ll 4 versions" in modal.message
    assert h.app.gitRepo.hashes_between_calls == [("README.md", "selected_prev", "selected_curr", False)]


def test_getHashesBetween_uses_file_history_slice_only():
    repo = GitRepo.__new__(GitRepo)
    calls = []

    def fake_get_history(file_name, ignorecache=False, limit=0):
        calls.append((file_name, ignorecache, limit))
        return [
            ("2026-03-11T10:00:00", "0f0ca1075fb9", "newest", "unpushed", "", ""),
            ("2026-03-11T09:00:00", "55cbd38079f2", "mid1", "unpushed", "", ""),
            ("2026-03-11T08:00:00", "6d9ffe6e662e", "mid2", "unpushed", "", ""),
            ("2026-03-11T07:00:00", "3e0be488de9f", "oldest", "unpushed", "", ""),
        ]

    repo.getNormalizedHashListFromFileName = fake_get_history
    repo.printException = lambda *_args, **_kwargs: None

    hashes = GitRepo.getHashesBetween(repo, "gitdiffnavtool.py", "3e0be488de9f", "0f0ca1075fb9")

    assert hashes == ["0f0ca1075fb9", "55cbd38079f2", "6d9ffe6e662e", "3e0be488de9f"]
    assert calls == [("gitdiffnavtool.py", False, 0)]


class HarnessForRepoModeKeyW:
    def __init__(self):
        self.index = 0
        self.errors = []
        self.helper_calls = 0
        self.app = types.SimpleNamespace()

    def printException(self, exc, context=None):
        self.errors.append((exc, context))

    def key_w_helper(self, _event=None):
        self.helper_calls += 1

    def _log_visible_items(self, _msg):
        pass


def test_repo_mode_key_w_always_calls_helper():
    h = HarnessForRepoModeKeyW()
    event = FakeEvent(key="w", character="w")

    RepoModeFileList.key_w(h, event)

    assert event.stopped is True
    assert h.helper_calls == 1


class HarnessForRepoModeHistoryKeyA:
    def __init__(self, rel_dir="", rel_file="notes.txt"):
        self.index = 0
        self.errors = []
        self.messages = []
        self.commit_calls = []
        self.refresh_calls = []

        git_repo = types.SimpleNamespace(commitFile=self._commit_file)
        repo_file_list = types.SimpleNamespace(prepRepoModeFileList=self._refresh)
        self.app = types.SimpleNamespace(
            rel_dir=rel_dir,
            rel_file=rel_file,
            NO_DIR="",
            NO_FILE="",
            previous_hash="prev",
            current_hash="curr",
            gitRepo=git_repo,
            repo_mode_file_list=repo_file_list,
        )

    def _commit_file(self, rel_path):
        self.commit_calls.append(rel_path)

    def _refresh(self, prev_hash, curr_hash, ignorecache=False):
        self.refresh_calls.append((prev_hash, curr_hash, ignorecache))

    def error_message(self, message: str):
        self.messages.append(message)

    def printException(self, exc, context=None):
        self.errors.append((exc, context))

    def key_a(self, event=None, recursive=False):
        return RepoModeHistoryList.key_a(self, event, recursive=recursive)


def test_repo_mode_history_key_a_stages_selected_file_and_refreshes():
    h = HarnessForRepoModeHistoryKeyA(rel_dir="docs", rel_file="notes.txt")
    event = FakeEvent(key="a", character="a")

    RepoModeHistoryList.key_a(h, event)

    assert event.stopped is True
    assert h.commit_calls == ["docs/notes.txt"]
    assert h.refresh_calls == [("prev", "curr", True)]
    assert h.messages == []


def test_repo_mode_history_key_a_shows_error_when_no_file_selected():
    h = HarnessForRepoModeHistoryKeyA(rel_dir="docs", rel_file="")
    event = FakeEvent(key="a", character="a")

    RepoModeHistoryList.key_a(h, event)

    assert event.stopped is True
    assert h.commit_calls == []
    assert h.refresh_calls == []
    assert h.messages == ["No file selected for staging"]


def test_repo_mode_history_key_a_surfaces_commit_file_validation_errors():
    h = HarnessForRepoModeHistoryKeyA(rel_dir="docs", rel_file="notes.txt")

    def _raise_validation(_rel_path):
        raise ValueError("commitFile: file 'docs/notes.txt' is not modified or untracked")

    h.app.gitRepo.commitFile = _raise_validation
    event = FakeEvent(key="a", character="a")

    RepoModeHistoryList.key_a(h, event)

    assert event.stopped is True
    assert h.refresh_calls == []
    assert h.messages == ["commitFile: file 'docs/notes.txt' is not modified or untracked"]


def test_repo_mode_history_key_A_alias_calls_key_a_path():
    h = HarnessForRepoModeHistoryKeyA(rel_dir="docs", rel_file="notes.txt")
    event = FakeEvent(key="A", character="A")

    RepoModeHistoryList.key_A(h, event)

    assert event.stopped is True
    assert h.commit_calls == ["docs/notes.txt"]


class _FakeGitRepoForSave:
    def repo_rel_path_to_reldir_relfile(self, relpath):
        return ("", relpath)

    def getFileContents(self, hashval, reldir, relfile):
        return b"snapshot-bytes"

    def getCommitTimestamp(self, _hashval):
        # Return timestamp for test: March 12, 2026, 10:30:45 UTC
        return 1778049045.0

    def getIndexMtime(self):
        return 1778049045.0


class _FakeModalForSave:
    def __init__(self, filepath: str, source_relpath: str, write_hash_length: int):
        self.filepath = filepath
        self.source_relpath = source_relpath
        self.write_adds_timestamps = False
        self.write_hash_length = write_hash_length
        self.write_uses_mtime = True
        self.app = types.SimpleNamespace(output_directory=None, gitRepo=_FakeGitRepoForSave())

    def printException(self, *_args, **_kwargs):
        return None


def test_save_uses_truncated_hash_in_output_name(tmp_path):
    src = tmp_path / "f.txt"
    src.write_bytes(b"hello")
    full_hash = "0123456789abcdef0123456789abcdef01234567"

    modal = _FakeModalForSave(filepath=str(src), source_relpath="f.txt", write_hash_length=12)

    out_path, err = SaveSnapshotModal._save(modal, full_hash)

    assert err is None
    assert out_path is not None
    assert out_path.endswith(".0123456789ab")


def test_save_uses_full_hash_when_write_hash_length_zero(tmp_path):
    src = tmp_path / "f.txt"
    src.write_bytes(b"hello")
    full_hash = "fedcba9876543210fedcba9876543210fedcba98"

    modal = _FakeModalForSave(filepath=str(src), source_relpath="f.txt", write_hash_length=0)

    out_path, err = SaveSnapshotModal._save(modal, full_hash)

    assert err is None
    assert out_path is not None
    assert out_path.endswith("." + full_hash)


def test_save_sets_mtime_from_hash_when_enabled(tmp_path):
    src = tmp_path / "f.txt"
    src.write_bytes(b"hello")

    modal = _FakeModalForSave(filepath=str(src), source_relpath="f.txt", write_hash_length=12)
    modal.write_uses_mtime = True

    out_path, err = SaveSnapshotModal._save(modal, "0123456789abcdef0123456789abcdef01234567")

    assert err is None
    assert out_path is not None
    # Fake repo timestamp is 1778049045.0
    assert abs((tmp_path / (src.name + ".0123456789ab")).stat().st_mtime - 1778049045.0) < 1.0


def test_save_does_not_set_mtime_when_disabled(tmp_path):
    src = tmp_path / "f.txt"
    src.write_bytes(b"hello")

    modal = _FakeModalForSave(filepath=str(src), source_relpath="f.txt", write_hash_length=12)
    modal.write_uses_mtime = False

    out_path, err = SaveSnapshotModal._save(modal, "fedcba9876543210fedcba9876543210fedcba98")

    assert err is None
    assert out_path is not None
    # When disabled, mtime should remain around current time, not fake repo timestamp.
    # Use a generous threshold to avoid flaky timing checks.
    assert abs((tmp_path / (src.name + ".fedcba987654")).stat().st_mtime - 1778049045.0) > 60.0
