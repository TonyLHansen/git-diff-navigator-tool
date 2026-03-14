import types

from gitdiffnavtool import AppBase


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


class HarnessForHighlightMatch:
    def __init__(self):
        self._nodes = [
            types.SimpleNamespace(_raw_text="src/main.py", _hash=None),
            types.SimpleNamespace(_raw_text="docs/readme.md", _hash=None),
        ]
        self.activated = []
        self.highlighted_top = False
        self.errors = []
        # Intentionally no app.gitRepo on this harness. _highlight_match
        # should not require get_repo_root when _raw_text is repo-relative.
        self.app = types.SimpleNamespace()

    def nodes(self):
        return self._nodes

    def _activate_index(self, idx):
        self.activated.append(idx)

    def _highlight_top(self):
        self.highlighted_top = True

    def text_of(self, node):
        return getattr(node, "_raw_text", "")

    def printException(self, exc, context=None):
        self.errors.append((exc, context))


def test_highlight_match_uses_repo_relative_raw_text_without_repo_root():
    h = HarnessForHighlightMatch()

    AppBase._highlight_match(h, "docs/readme.md")

    assert h.activated == [1]
    assert h.highlighted_top is False
    assert h.errors == []
