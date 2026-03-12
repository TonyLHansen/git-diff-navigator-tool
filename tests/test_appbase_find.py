import types

from gitdiffnavtool import AppBase, RepoModeFileList, SaveSnapshotModal
from gitrepo import GitRepo


class FakeEvent:
    def __init__(self, key=None, character=None):
        self.key = key
        self.character = character
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeInput:
    def __init__(self):
        self.focused = False

    def focus(self):
        self.focused = True


class FakeScreen:
    def __init__(self, input_widget=None):
        self._input_widget = input_widget

    def query_one(self, selector, _type=None):
        if selector == "#find-input" and self._input_widget is not None:
            return self._input_widget
        raise LookupError(selector)


class HarnessForOnKey:
    def __init__(self):
        self._last_search = "needle"
        self.errors = []
        self.find_calls = []
        self.show_calls = []

        input_widget = FakeInput()
        self._input_widget = input_widget
        self.app = types.SimpleNamespace(
            _find_overlay_widget=None,
            show_find_overlay=self._show_find_overlay,
            call_later=lambda cb: cb(),
            screen=FakeScreen(input_widget=input_widget),
            query_one=lambda _sel, _typ=None: input_widget,
        )

    def printException(self, exc, context=None):
        self.errors.append((exc, context))

    def _find_and_activate(self, value, forward):
        self.find_calls.append((value, forward))
        return True

    def _show_find_overlay(self, initial_text, title, callback):
        self.show_calls.append((initial_text, title, callback))


def test_on_key_opens_find_overlay_forward():
    h = HarnessForOnKey()
    event = FakeEvent(key="greater_than_sign", character=">")

    AppBase.on_key(h, event)

    assert event.stopped is True
    assert len(h.show_calls) == 1
    initial_text, title, callback = h.show_calls[0]
    assert initial_text == "needle"
    assert title == "Find (forward)"

    callback("abc")
    assert h.find_calls == [("abc", True)]


def test_on_key_reuses_existing_overlay_and_refocuses():
    h = HarnessForOnKey()
    h.app._find_overlay_widget = object()
    event = FakeEvent(key="greater_than_sign", character=">")

    AppBase.on_key(h, event)

    assert event.stopped is True
    assert h.show_calls == []
    assert h._input_widget.focused is True


def test_on_key_ignores_non_find_keys():
    h = HarnessForOnKey()
    event = FakeEvent(key="a", character="a")

    AppBase.on_key(h, event)

    assert event.stopped is False
    assert h.show_calls == []


class HarnessForFind:
    def __init__(self, texts, start_index=0):
        self._nodes = [types.SimpleNamespace(_search_text=t) for t in texts]
        self.index = start_index
        self._last_search = None
        self.activated = []

    def nodes(self):
        return self._nodes

    def text_of(self, node):
        return getattr(node, "_search_text", "")

    def _activate_index(self, idx):
        self.index = idx
        self.activated.append(idx)

    def printException(self, _exc, _context=None):
        pass


def test_find_and_activate_forward_wraps():
    h = HarnessForFind(["one", "two", "three"], start_index=2)

    found = AppBase._find_and_activate(h, "o", forward=True)

    assert found is True
    assert h.activated[-1] == 0
    assert h._last_search == "o"


def test_find_and_activate_backward_wraps():
    h = HarnessForFind(["alpha", "beta", "gamma"], start_index=0)

    found = AppBase._find_and_activate(h, "ga", forward=False)

    assert found is True
    assert h.activated[-1] == 2
    assert h._last_search == "ga"


def test_find_and_activate_returns_false_when_not_found():
    h = HarnessForFind(["alpha", "beta"], start_index=0)

    found = AppBase._find_and_activate(h, "zzz", forward=True)

    assert found is False
    assert h.activated == []
    assert h._last_search == "zzz"


class _FakeGitRepoForSnapshots:
    def __init__(self):
        self.hashes_between_calls = []

    def abs_path_for(self, rel_dir, rel_file):
        base = "/tmp/repo"
        if rel_dir:
            return f"{base}/{rel_dir}/{rel_file}"
        return f"{base}/{rel_file}"

    def get_repo_root(self):
        return "/tmp/repo"

    def get_hashes_between(self, file_name, prev_hash, curr_hash, ignorecache=False):
        self.hashes_between_calls.append((file_name, prev_hash, curr_hash, ignorecache))
        return ["selected_curr", "mid_1", "mid_2", "selected_prev"]


class HarnessForKeyWHelper:
    def __init__(self):
        self.errors = []
        self.pushed = []
        self.app = types.SimpleNamespace(
            rel_dir="",
            rel_file="README.md",
            previous_hash="top_prev",
            current_hash="top_curr",
            output_directory=None,
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


def test_get_hashes_between_uses_file_history_slice_only():
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

    hashes = GitRepo.get_hashes_between(repo, "gitdiffnavtool.py", "3e0be488de9f", "0f0ca1075fb9")

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
