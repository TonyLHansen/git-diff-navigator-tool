import types
import unittest.mock as mock

from gitdiffnavtool import AppBase, FileModeFileList, RepoModeFileList, SaveSnapshotModal, MARKERS, STYLE_STAGED
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


class HarnessForFileModeKeyA:
    def __init__(self, rel_dir="", rel_file="notes.txt", repo_status="modified", is_dir=False):
        self.index = 0
        self.errors = []
        self.messages = []
        self.commit_calls = []
        self.refresh_calls = []
        raw_text = f"{rel_dir}/{rel_file}" if rel_dir and rel_file else (rel_file or rel_dir)
        self._nodes = [
            types.SimpleNamespace(
                _is_dir=is_dir,
                _repo_status=repo_status,
                _raw_text=raw_text,
                _filename=rel_file or raw_text,
            )
        ]

        git_repo = types.SimpleNamespace(commitFile=self._commit_file)
        self.app = types.SimpleNamespace(
            rel_dir=rel_dir,
            rel_file=rel_file,
            NO_DIR="",
            NO_FILE="",
            gitRepo=git_repo,
        )

    def _commit_file(self, rel_path):
        self.commit_calls.append(rel_path)

    def prepFileModeFileList(self, highlight=None):
        self.refresh_calls.append(highlight)

    def nodes(self):
        return self._nodes

    def error_message(self, message: str):
        self.messages.append(message)

    def printException(self, exc, context=None):
        self.errors.append((exc, context))

    def key_a(self, event=None, recursive=False):
        return FileModeFileList.key_a(self, event, recursive=recursive)


class HarnessForCollectFileModeNodes:
    def __init__(self, mods=None):
        self.errors = []
        _mods = mods if mods is not None else [("both.txt", "iso-mod", "modified")]
        self.app = types.SimpleNamespace(
            no_untracked=False,
            no_ignored=False,
            gitRepo=types.SimpleNamespace(
                getFileListAtHash=lambda _hash: [("tracked.txt", "iso-head", "committed")],
                getFileListBetweenHashAndStaged=lambda _hash: [
                    ("staged.txt", "iso-staged", "added"),
                    ("both.txt", "iso-staged-both", "modified"),
                ],
                getFileListUntracked=lambda: [("untracked.txt", "iso-u", "untracked")],
                getFileListIgnored=lambda: [("ignored.txt", "iso-i", "ignored")],
                getFileListBetweenNormalizedHashes=lambda _prev, _curr: _mods,
            ),
        )

    def printException(self, exc, context=None):
        self.errors.append((exc, context))


def test_collect_filemode_nodes_includes_staged_and_mods_override_staged():
    h = HarnessForCollectFileModeNodes()

    FileModeFileList._collect_filemode_nodes(h)

    files = sorted(h._nodes_by_dir[""]["files"])
    assert ("tracked.txt", "tracked_clean", None) in files
    assert ("staged.txt", "staged", "iso-staged") in files
    assert ("untracked.txt", "untracked", "iso-u") in files
    assert ("ignored.txt", "ignored", "iso-i") in files
    assert ("both.txt", "modified", "iso-mod") in files


def test_collect_filemode_nodes_staged_new_not_overridden_by_mods_added():
    # Simulate a staged-new file that also appears in git diff HEAD as "added".
    # The mods pass must NOT override the staged status in this case.
    h = HarnessForCollectFileModeNodes(
        mods=[
            ("staged.txt", "iso-mod-staged", "added"),   # git diff HEAD sees staged-new as "added"
            ("both.txt", "iso-mod", "modified"),          # MM file: mods still win
        ]
    )

    FileModeFileList._collect_filemode_nodes(h)

    files = {name: (status, iso) for name, status, iso in h._nodes_by_dir[""]["files"]}
    # staged.txt must keep "staged" - not be overridden by mods "added"
    assert files["staged.txt"] == ("staged", "iso-staged"), (
        f"Expected staged.txt to stay 'staged', got {files['staged.txt']}"
    )
    # both.txt: staged pass gave "staged", mods pass gives "modified" -> mods wins
    assert files["both.txt"][0] == "modified", (
        f"Expected both.txt to be 'modified', got {files['both.txt'][0]}"
    )


def test_file_mode_key_a_stages_selected_file_and_refreshes():
    h = HarnessForFileModeKeyA(rel_dir="docs", rel_file="notes.txt", repo_status="modified")
    event = FakeEvent(key="a", character="a")

    FileModeFileList.key_a(h, event)

    assert event.stopped is True
    assert h.commit_calls == ["docs/notes.txt"]
    assert h.refresh_calls == [None]
    assert h.messages == []
    assert h.app.rel_dir == "docs"
    assert h.app.rel_file == "notes.txt"


def test_file_mode_key_a_shows_error_when_no_file_selected():
    h = HarnessForFileModeKeyA(rel_dir="docs", rel_file="", repo_status="modified", is_dir=True)
    event = FakeEvent(key="a", character="a")

    FileModeFileList.key_a(h, event)

    assert event.stopped is True
    assert h.commit_calls == []
    assert h.refresh_calls == []
    assert h.messages == ["No file selected for staging"]


def test_file_mode_key_a_rejects_non_modified_or_untracked_selection():
    h = HarnessForFileModeKeyA(rel_dir="docs", rel_file="notes.txt", repo_status="staged")
    event = FakeEvent(key="a", character="a")

    FileModeFileList.key_a(h, event)

    assert event.stopped is True
    assert h.commit_calls == []
    assert h.refresh_calls == []
    assert h.messages == ["Selected file is not modified or untracked"]


def test_file_mode_key_a_surfaces_commit_file_validation_errors():
    h = HarnessForFileModeKeyA(rel_dir="docs", rel_file="notes.txt", repo_status="modified")

    def _raise_validation(_rel_path):
        raise ValueError("commitFile: file 'docs/notes.txt' is not modified or untracked")

    h.app.gitRepo.commitFile = _raise_validation
    event = FakeEvent(key="a", character="a")

    FileModeFileList.key_a(h, event)

    assert event.stopped is True
    assert h.refresh_calls == []
    assert h.messages == ["commitFile: file 'docs/notes.txt' is not modified or untracked"]


def test_file_mode_key_A_alias_calls_key_a_path():
    h = HarnessForFileModeKeyA(rel_dir="docs", rel_file="notes.txt", repo_status="untracked")
    event = FakeEvent(key="A", character="A")

    FileModeFileList.key_A(h, event)

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


# ---------------------------------------------------------------------------
# Helpers for _render_filemode_display marker tests
# ---------------------------------------------------------------------------

class _FakeLabel:
    def __init__(self, renderable):
        self.renderable = renderable


class _FakeListItem:
    def __init__(self, label):
        self._label = label
        self._repo_status = None
        self._is_dir = False
        self._raw_text = ""
        self._filename = ""

    def set_class(self, *_args, **_kwargs):
        pass

    def add_class(self, *_args, **_kwargs):
        pass


class HarnessForRenderFilemodeDisplay:
    def __init__(self):
        self._render_filemode_in_progress = False
        self._populated = False
        self._min_index = 0
        self._preselected_filename = None
        self._highlight_history = []
        self._highlight_pos = -1
        self.index = 0
        self.children = []
        self.items = []
        self.errors = []
        self.app = None  # skip header-label updates

    def clear(self):
        self.items.clear()
        self.children.clear()

    def append(self, item):
        self.items.append(item)
        self.children.append(item)

    def call_after_refresh(self, fn):
        pass

    def printException(self, exc, context=None):
        self.errors.append((exc, context))


def test_markers_staged_constant_is_A():
    assert MARKERS["staged"] == "A"
    assert STYLE_STAGED == "cyan"


def test_render_filemode_display_staged_file_uses_A_marker():
    h = HarnessForRenderFilemodeDisplay()
    nodes_by_dir = {"":{"dirs": set(), "files": [("staged.txt", "staged", "2026-01-01T00:00:00")]}}

    with mock.patch("gitdiffnavtool.ListItem", _FakeListItem), mock.patch("gitdiffnavtool.Label", _FakeLabel):
        FileModeFileList._render_filemode_display(h, nodes_by_dir, "", "")

    staged = [it for it in h.items if getattr(it, "_repo_status", None) == "staged"]
    assert staged, f"No staged item rendered; statuses={[getattr(it, '_repo_status', None) for it in h.items]}"
    txt = staged[0]._label.renderable
    assert txt.plain.startswith("A "), f"Expected 'A ' prefix, got {txt.plain!r}"


def test_render_filemode_display_staged_file_uses_staged_style():
    h = HarnessForRenderFilemodeDisplay()
    nodes_by_dir = {"":{"dirs": set(), "files": [("staged.txt", "staged", None)]}}

    with mock.patch("gitdiffnavtool.ListItem", _FakeListItem), mock.patch("gitdiffnavtool.Label", _FakeLabel):
        FileModeFileList._render_filemode_display(h, nodes_by_dir, "", "")

    staged = [it for it in h.items if getattr(it, "_repo_status", None) == "staged"]
    assert staged
    txt = staged[0]._label.renderable
    assert str(txt.style) == STYLE_STAGED, f"Expected style={STYLE_STAGED!r}, got {str(txt.style)!r}"


def test_render_filemode_display_staged_vs_modified_markers():
    h = HarnessForRenderFilemodeDisplay()
    nodes_by_dir = {
        "": {
            "dirs": set(),
            "files": [
                ("m.txt", "modified", None),
                ("s.txt", "staged", None),
            ],
        }
    }

    with mock.patch("gitdiffnavtool.ListItem", _FakeListItem), mock.patch("gitdiffnavtool.Label", _FakeLabel):
        FileModeFileList._render_filemode_display(h, nodes_by_dir, "", "")

    by_status = {it._repo_status: it for it in h.items}
    assert by_status["staged"]._label.renderable.plain.startswith("A "), "staged must use 'A' marker"
    assert by_status["modified"]._label.renderable.plain.startswith("M "), "modified must use 'M' marker"
