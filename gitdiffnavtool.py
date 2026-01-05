#!/usr/bin/env python3
"""
gitdiffnavtool - regenerated scaffold (step 1 only)

This file is a minimal scaffold created by the regen plan. Subsequent
steps will fill in the concrete classes and behavior.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import subprocess
import traceback
import inspect
from typing import Optional
from datetime import datetime
from functools import wraps

# Optional pygit2 support — best-effort import to enable repo status checks
try:
    import pygit2  # type: ignore
except Exception as _no_logging:
    pygit2 = None

# Third-party UI and rendering imports
from rich.text import Text
from textual import events
from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.widgets import ListView, Label, ListItem, Footer, Header

# --- Constants -------------------------------------------------------------
# Highlight constants (defaults)
HIGHLIGHT_FILELIST_BG = "#f1c40f"

HIGHLIGHT_REPOLIST_BG = "#3333CC"

# Diff-list specific highlight background
HIGHLIGHT_DIFF_BG = "#2ecc71"

# Help-list specific highlight background
HIGHLIGHT_HELP_BG = "#95a5a6"

# Default highlight background used when a widget doesn't specify one
HIGHLIGHT_DEFAULT_BG = "light_gray"

# Status markers mapping used to render the left-most TAG for file rows.
# Keys correspond to computed repo statuses (strings used by preparatory APIs).
MARKERS = {
    "conflicted": "!",
    "staged": "A",
    "wt_deleted": "D",
    "ignored": "I",
    "modified": "M",
    "untracked": "U",
    "tracked_clean": " ",
}

# Inline CSS used by the Textual App (can be edited in-place)
INLINE_CSS = """
/* gitdiffnavtool inline CSS */

/* Title labels */
#left-file-title, #left-history-title, #right-history-title, #right-file-title, #diff-title, #help-title {
    padding: 0 1;
    background: $surface;
    color: $text;
}

.title.active {
    background: $accent-darken-1;
    color: white;
    text-style: bold;
}

/* Simple column spacing */
ListView {
    padding: 0 1;
}

/* Highlight active list item */
ListItem.active {
    background: $accent-darken-1;
    color: white;
}

"""


# Canonical widget and label IDs (six canonical widgets)
LEFT_FILE_LIST_ID = "left-file-list"
LEFT_FILE_TITLE = "left-file-title"

LEFT_HISTORY_LIST_ID = "left-history-list"
LEFT_HISTORY_TITLE = "left-history-title"

RIGHT_FILE_LIST_ID = "right-file-list"
RIGHT_FILE_TITLE = "right-file-title"

RIGHT_HISTORY_LIST_ID = "right-history-list"
RIGHT_HISTORY_TITLE = "right-history-title"

DIFF_LIST_ID = "diff-list"
DIFF_TITLE = "diff-title"

HELP_LIST_ID = "help-list"
HELP_TITLE = "help-title"

# Footer text used when switching to file-history view
RIGHT_HISTORY_FOOTER = Text("File history: press Left to return")
# Footer text used when showing the left history pane
LEFT_HISTORY_FOOTER = Text("History: press Right to open file list")
# Footer text used when showing the left file list
LEFT_FILE_FOOTER = Text("Files: press Right to open file history")
# Footer text used when showing the right file list (file list view)
RIGHT_FILE_FOOTER = Text("Files: press Left to return")
# Footer text used for help screen
HELP_FOOTER = Text("Help: press Enter to return")
# Footer text used when showing the diff for a history/file selection
HISTORY_FILE_DIFF_FOOTER = Text("Diff: press Left to return to files")

# Common styles used across file/history preparers
STYLE_DIR = "white on blue"
STYLE_PARENT = STYLE_DIR
STYLE_WT_DELETED = "red"
STYLE_ERROR = "red"
STYLE_CONFLICTED = "magenta"
STYLE_STAGED = "cyan"
STYLE_IGNORED = "dim italic"
STYLE_MODIFIED = "yellow"
STYLE_UNTRACKED = "bold yellow"
STYLE_DEFAULT = "white"

STYLE_FILELIST_KEY = "dim"

# Header row text for file lists (unselectable)
FILELIST_KEY_ROW_TEXT = "Key:  ' ' tracked  U untracked  M modified  A staged  D deleted  I ignored  ! conflicted"


# --- Logging setup --------------------------------------------------------
# NOTE: logging is configured in `main()` when `--debug` is passed.

logger = logging.getLogger(__name__)

# Define a TRACE level lower than DEBUG and add a convenience `trace` method
# so callers can emit very-verbose trace messages when enabled.
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _logger_trace(self, msg, *args, **kwargs):
    if self.isEnabledFor(TRACE):
        self._log(TRACE, msg, args, **kwargs)


setattr(logging.Logger, "trace", _logger_trace)


def enable_trace_logging(enabled: bool) -> None:
    """Enable or disable TRACE-level logging across the root logger and handlers.

    When enabled this sets the root logger and all its handlers to the numeric
    TRACE level so `logger.trace(...)` messages are emitted. When disabled this
    does nothing (existing logging configuration remains).
    """
    try:
        root = logging.getLogger()
        if enabled:
            root.setLevel(TRACE)
            for h in root.handlers:
                h.setLevel(TRACE)

            logger.debug("Trace logging enabled")
    except Exception as e:
        printException(e, "enable_trace_logging failed")


def printException(e: Exception, msg: Optional[str] = None) -> None:
    """Module-level helper to log unexpected exceptions when `self` isn't available.

    This mirrors the widget-level `printException` helper used by widgets.
    """
    try:
        short_msg = msg or ""
        logger.warning("%s: %s", short_msg, e)
        logger.warning(traceback.format_exc())
    except Exception as e2:
        printException(e2)
        # Last-resort fallback to stderr — avoid recursive logging
        sys.stderr.write(f"printException fallback: {e}\n")
        sys.stderr.write(f"secondary exception: {e2}\n")


def run_cmd_log(cmd: list[str], label: str | None = None, text: bool = True, capture_output: bool = True):
    """Module-level wrapper for subprocess.run mirroring `AppBase._run_cmd_log`.

    Useful for top-level functions that don't have access to a widget `self`.
    Returns a CompletedProcess-like result; on exception returns a non-zero
    CompletedProcess with the exception text in `stderr`.
    """
    proc = subprocess.run(cmd, text=text, capture_output=capture_output)
    lab = label or "cmd"
    if proc.stderr:
        logger.warning("%s stderr (cmd=%s):\n%s", lab, " ".join(cmd), proc.stderr.strip())
    logger.trace("%s stdout (cmd=%s):\n%s", lab, " ".join(cmd), proc.stdout or "")
    return proc


class AppBase(ListView):
    """Base widget class for list-like components providing shared helpers.

    This is a minimal, safe implementation intended for Step 2 of the regen
    plan. It implements defensive defaults, exception logging, text
    extraction helpers, and basic navigation key handling.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Safe defaults so other code can access these attributes early
        self._min_index = 0
        self._populated = False
        self.current_prev_sha = None
        self.current_commit_sha = None
        self.current_diff_file = None
        # When True the next watch_index-triggered scroll should animate
        # (used by page up / page down handlers to make the jump more
        # visually noticeable).
        self._page_scroll = False
        # Flags to identify widget type without relying on isinstance checks
        # These are set to 0 by default and overridden by subclasses.
        self.is_history_list = 0
        self.is_file_list = 0
        # Ensure common attributes exist so code can access them directly
        # Rely on ListView to provide `children`, `_nodes`, `index`, and `app`.
        # Per-widget highlight background; subclasses override with specific backgrounds
        self.highlight_bg_style = HIGHLIGHT_DEFAULT_BG

    def printException(self, e: Exception, msg: Optional[str] = None) -> None:
        try:
            className = type(self).__name__
            funcName = sys._getframe(1).f_code.co_name
            short = msg or ""
            logger.warning(f"{className}.{funcName}: {short} - {e}")
            logger.warning(traceback.format_exc())
        except Exception as e_fallback:
            # Fall back to module-level printer
            printException(e, msg)
            printException(e_fallback, "AppBase.printException fallback")

    def _run_cmd_log(self, cmd: list[str], label: str | None = None, text: bool = True, capture_output: bool = True):
        """Run subprocess command, log stderr as warning and stdout at TRACE.

        Returns the CompletedProcess instance. Defensive: on exception returns
        a CompletedProcess with non-zero return code and the exception string
        in `stderr` so callers can continue to inspect `stdout`/`stderr` safely.
        """
        proc = subprocess.run(cmd, text=text, capture_output=capture_output)
        lab = label or "cmd"
        if proc.stderr:
            logger.warning("%s stderr (cmd=%s):\n%s", lab, " ".join(cmd), proc.stderr.strip())
        logger.trace("%s stdout (cmd=%s):\n%s", lab, " ".join(cmd), proc.stdout or "")
        return proc

    def text_of(self, node) -> str:
        """Extract visible text from a ListItem's Label or renderable."""
        try:
            raw = getattr(node, "_raw_text", None)
            if raw is not None:
                return raw
            lbl = node.query_one(Label)
            if hasattr(lbl, "text") and getattr(lbl, "text"):
                return lbl.text
            renderable = getattr(lbl, "renderable", None)
            if isinstance(renderable, Text):
                return renderable.plain
            if renderable is not None:
                return str(renderable)
            return str(lbl)
        except Exception as e:
            self.printException(e, "extracting text")
            return str(node)

    def _extract_label_text(self, lbl) -> str:
        """Safely extract visible text from a Label or its renderable."""
        try:
            renderable = getattr(lbl, "renderable", None)
            if isinstance(renderable, Text):
                return renderable.plain
            if renderable is not None:
                return str(renderable)
            if hasattr(lbl, "text"):
                return getattr(lbl, "text")
            return str(lbl)
        except Exception as e:
            self.printException(e, "extracting label text")
            return str(lbl)

    def nodes(self):
        """Return the underlying nodes list or an empty list if unset.

        Uses getattr to tolerate Textual internals not being present yet.
        """
        try:
            # Prefer the public `children` live view when available so callers
            # observe the current DOM without allocating a snapshot.
            n = self.children
            return n if n else []
        except Exception as e:
            printException(e)
            return []

    def _activate_index(self, new_index: int) -> None:
        """Set the active/selected index and update ListItem 'active' class.

        Deactivates the previously-active item, activates the new item,
        and schedules the index change with `call_after_refresh` when possible.
        """
        try:
            nodes = self.nodes()
            if not nodes:
                return
            old = self.index
            # per-widget highlight is provided via `self.highlight_bg_style`

            # Only set the index here; actual visual activation is performed
            # in `watch_index` which runs after Textual has processed the index
            # change so our styles/classes won't be clobbered.
            try:
                logger.debug(
                    "_activate_index: old=%r new=%r highlight_style=%s", old, new_index, self.highlight_bg_style
                )
                # Schedule the index change after the UI refresh.
                self.call_after_refresh(lambda: setattr(self, "index", new_index))
            except Exception as e:
                self.printException(e, "_activate_index: scheduling index set failed")
                logger.debug("_activate_index: falling back to direct index set -> %s", new_index)
                self.index = new_index
        except Exception as e:
            self.printException(e, "_activate_index failed")

    def watch_index(self, old: int | None, new: int | None) -> None:
        """Called when `index` changes — update visual highlighting here.

        This runs after Textual applies its own highlight, so applying
        inline styles here overrides the default selection background.
        """
        try:
            nodes = self.nodes()
            if not nodes:
                return
            # determine repo vs file highlight colors
            highlight_bg = self.highlight_bg_style
            text_color = "white"

            logger.debug("watch_index: old=%r new=%r nodes=%d", old, new, len(nodes))
            # remove style/class from old if it exists
            if old is not None and 0 <= old < len(nodes):
                try:
                    node_old = nodes[old]
                    try:
                        node_old.remove_class("active")
                    except Exception as e:
                        self.printException(e, "watch_index: remove_class failed")
                    try:
                        node_old.styles.background = None
                        node_old.styles.color = None
                        node_old.styles.text_style = None
                        logger.debug("watch_index: cleared styles for old index %s", old)
                    except Exception as e:
                        self.printException(e, "watch_index: clearing old styles failed")
                except Exception as e:
                    self.printException(e, "watch_index: deactivating old failed")

            # apply style/class to new
            if new is not None and 0 <= new < len(nodes):
                try:
                    node_new = nodes[new]
                    try:
                        node_new.add_class("active")
                    except Exception as e:
                        self.printException(e, "watch_index: add_class failed")
                    try:
                        node_new.styles.background = highlight_bg
                        node_new.styles.color = text_color
                        node_new.styles.text_style = "bold"
                        logger.debug(
                            "watch_index: applied highlight to new index %s text=%s", new, self.text_of(node_new)
                        )
                    except Exception as e:
                        self.printException(e, "watch_index: applying new highlight failed")
                    # Ensure the newly-highlighted node is scrolled into view.
                    try:
                        # Use a lambda so `call_after_refresh` calls a single-arg
                        # callable and we don't accidentally pass extra positional
                        # args that cause TypeError in scroll_to_widget.
                        animate = False
                        try:
                            animate = bool(self._page_scroll)
                        except Exception as e:
                            printException(e)
                            animate = False
                        logger.debug("watch_index: scroll animate=%s for index %s", animate, new)
                        if hasattr(self, "scroll_to_widget"):
                            try:
                                self.call_after_refresh(lambda: self.scroll_to_widget(node_new, animate=animate))
                            except Exception as e:
                                self.printException(e, "watch_index: scroll_to_widget(animate=) failed")
                                try:
                                    self.call_after_refresh(lambda: self.scroll_to_widget(node_new))
                                except Exception as e2:
                                    self.printException(e2, "watch_index: scroll_to_widget(node_new) fallback failed")
                        else:
                            try:
                                # Some nodes expose scroll_visible; attempt to call
                                # it (non-animated) as a fallback.
                                logger.debug("watch_index: scheduling node_new.scroll_visible for index %s", new)
                                self.call_after_refresh(
                                    lambda: getattr(node_new, "scroll_visible", lambda *a, **k: None)(True)
                                )
                            except Exception as e:
                                self.printException(e, "watch_index: node_new.scroll_visible failed")
                        # Reset the page_scroll flag after scheduling
                        self._page_scroll = False
                    except Exception as e:
                        printException(e, "watch_index: scrolling new node failed")
                except Exception as e:
                    self.printException(e, "watch_index: finding new node failed")
                # After highlighting, synchronize app-level state conservatively:
                # - If this widget is a history list, only update app hashes.
                # - If this widget is a file list, update app.path from the
                #   selected node's `_raw_text` (which is a filesystem path).
                try:
                    if isinstance(self, HistoryListBase):
                        try:
                            self._compute_selected_pair()
                        except Exception as e:
                            self.printException(e)
                        logger.debug(
                            "watch_index: history widget updated app.current_hash=%r app.previous_hash=%r",
                            self.app.current_hash,
                            self.app.previous_hash,
                        )

                    elif isinstance(self, FileListBase):
                        try:
                            raw = getattr(node_new, "_raw_text", None)
                            if raw:
                                try:
                                    # Preserve the raw value on `app.path` for
                                    # components that expect repo-relative names,
                                    # but also set the canonical full filesystem
                                    # path on `app.current_path` so other logic
                                    # can rely on a realpath.
                                    self.app.path = raw
                                except Exception as _ex:
                                    printException(_ex)
                                try:
                                    if os.path.isabs(raw):
                                        full = raw
                                    else:
                                        full = os.path.join(self.app.repo_root, raw)
                                    # Setter canonicalizes via realpath
                                    try:
                                        self.app.current_path = full
                                    except Exception as _ex:
                                        printException(_ex)
                                        # Best-effort fallback: store raw full
                                        try:
                                            self.app._current_path = os.path.realpath(full)
                                        except Exception as e:
                                            self.printException(e)
                                except Exception as e:
                                    self.printException(e)
                            logger.debug(
                                "watch_index: file widget set app.path=%r app.current_path=%r",
                                self.app.path,
                                self.app.current_path,
                            )
                        except Exception as e:
                            self.printException(e)
                    else:
                        logger.debug("watch_index: widget type %s - not updating app.path", type(self).__name__)
                except Exception as e:
                    self.printException(e, "watch_index: post-highlight app state sync failed")
        except Exception as e:
            self.printException(e, "watch_index failed")

    def _highlight_match(self, match: Optional[str]) -> None:
        """Highlight the first node whose raw text or _hash matches `match`.

        If `match` is None or no matching node is found, highlight the top item.
        Matching rules: exact match against `_raw_text`, exact match against
        `_hash`, or node text equality. For hashes allow prefix matching.
        """
        try:
            nodes = self.nodes()
            if not nodes:
                return
            if match:
                # Normalize match to canonical full path when possible so
                # comparisons against `_raw_text` (which we now store as
                # full paths for repo-mode rows) succeed.
                try:
                    if os.path.isabs(match):
                        match_full = os.path.realpath(match)
                    else:
                        match_full = os.path.realpath(os.path.join(self.app.repo_root, match))
                except Exception as e:
                    match_full = match
                    self.printException(e, "_highlight_match: normalizing match failed")

                for i, node in enumerate(nodes):
                    try:
                        raw = getattr(node, "_raw_text", None)
                        h = getattr(node, "_hash", None)
                        try:
                            if raw is not None:
                                node_full = (
                                    raw
                                    if os.path.isabs(raw)
                                    else os.path.realpath(os.path.join(self.app.repo_root, raw))
                                )
                            else:
                                node_full = None
                        except Exception as e:
                            node_full = raw
                            self.printException(e, "_highlight_match: computing node_full failed")

                        if node_full is not None and match_full is not None and node_full == match_full:
                            self._activate_index(i)
                            return

                        if h is not None and (h == match or str(h).startswith(match)):
                            self._activate_index(i)
                            return

                        # fallback to visible text equality
                        try:
                            txt = self.text_of(node)
                        except Exception as e:
                            self.printException(e, "_highlight_match: extracting text failed")
                            txt = str(node)
                        if txt == match:
                            self._activate_index(i)
                            return
                    except Exception as e:
                        self.printException(e, "_highlight_match: checking node failed")
            # No match found; highlight top
            self._highlight_top()
        except Exception as e:
            self.printException(e, "_highlight_match failed")

    def _highlight_top(self) -> None:
        """Schedule highlighting of the logical top item for this widget.

        Centralized implementation so subclasses don't need to duplicate
        the call_after_refresh/fallback pattern. Uses `self._min_index`
        when available.
        """
        try:
            top = self._min_index or 0
            try:
                self.call_after_refresh(lambda: self._activate_index(top))
            except Exception as e:
                self.printException(e, "_highlight_top: scheduling index set failed")
                # Fall back to direct activation if scheduling fails
                self._activate_index(top)
        except Exception as e:
            self.printException(e, "AppBase._highlight_top failed")

    # Key handlers: prefer `key_` methods on widgets instead of an `on_key` dispatcher.
    # Implement navigation handlers as `key_*` methods so subclasses may override
    # them individually and keep key logic co-located with widget state.

    def key_up(self, event: events.Key | None = None) -> None:
        logger.debug("AppBase.key_up called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_up: event.stop failed")
            min_idx = self._min_index or 0
            cur = self.index
            if cur is None:
                self._activate_index(min_idx)
                return
            if cur <= min_idx:
                return
            new_index = cur - 1
            self._activate_index(new_index)
        except Exception as e:
            self.printException(e, "key_up outer failure")

    def key_down(self, event: events.Key | None = None) -> None:
        logger.debug("AppBase.key_down called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_down: event.stop failed")
            cur = self.index or (self._min_index or 0)
            nodes = self.nodes()
            if not nodes:
                return
            new_index = min(len(nodes) - 1, cur + 1)
            self._activate_index(new_index)
        except Exception as e:
            self.printException(e, "key_down outer failure")

    def key_page_down(self, event: events.Key | None = None, recursive: bool = False) -> None:
        if not recursive:
            logger.debug("AppBase.key_pagedown called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_page_down: event.stop failed")
            nodes = self.nodes()
            if not nodes:
                return
            current_index = self.index or 0
            try:
                region = self.scrollable_content_region
                visible_height = int(getattr(region, "height", 10))
            except Exception as e:
                self.printException(e, "key_page_down: measuring region height failed")
                visible_height = 10
            page_size = max(1, visible_height // 2)
            new_index = min(current_index + page_size, len(nodes) - 1)
            try:
                try:
                    self._page_scroll = True
                except Exception as e:
                    self.printException(e, "key_page_down: setting _page_scroll failed")
                self._activate_index(new_index)
            except Exception as e:
                self.printException(e, "key_page_down: activate failed")
        except Exception as e:
            self.printException(e, "key_page_down failed")

    # Alias handlers: terminals/terminfo may report different key names for
    # page up / page down (e.g. 'pageup', 'pagedown', 'prior', 'next'). Provide
    # aliases that delegate to the canonical handlers so keys are handled.
    def key_pageup(self, event: events.Key | None = None) -> None:
        logger.debug("AppBase.key_pageup called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_up(event, recursive=True)

    def key_pagedown(self, event: events.Key | None = None) -> None:
        logger.debug("AppBase.key_pagedown called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_down(event, recursive=True)

    def key_page_up(self, event: events.Key | None = None, recursive: bool = False) -> None:
        if not recursive:
            logger.debug("AppBase.key_pageup called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_page_up: event.stop failed")
            nodes = self.nodes()
            if not nodes:
                return
            current_index = self.index or 0
            try:
                region = self.scrollable_content_region
                visible_height = int(getattr(region, "height", 10))
            except Exception as e:
                self.printException(e, "key_page_up: measuring region height failed")
                visible_height = 10
            page_size = max(1, visible_height // 2)
            min_idx = self._min_index or 0
            new_index = max(current_index - page_size, min_idx)
            # Use _activate_index which schedules the index change after refresh
            try:
                try:
                    self._page_scroll = True
                except Exception as e:
                    self.printException(e)
                self._activate_index(new_index)
            except Exception as e:
                self.printException(e, "key_page_up: activate failed")
        except Exception as e:
            self.printException(e, "key_page_up failed")

    def key_prior(self, event: events.Key | None = None) -> None:
        # 'prior' is sometimes used for PageUp
        logger.debug("AppBase.key_prior called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_up(event, recursive=True)

    def key_next(self, event: events.Key | None = None) -> None:
        # 'next' is sometimes used for PageDown
        logger.debug("AppBase.key_next called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_down(event, recursive=True)

    def key_home(self, event: events.Key | None = None) -> None:
        logger.debug("AppBase.key_home called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_home: event.stop failed")
            min_idx = self._min_index or 0
            self._activate_index(min_idx)
        except Exception as e:
            self.printException(e, "key_home failed")

    def key_end(self, event: events.Key | None = None) -> None:
        logger.debug("AppBase.key_end called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_end: event.stop failed")
            nodes = self._nodes
            if not nodes:
                return
            last_idx = max(0, len(nodes) - 1)
            self._activate_index(last_idx)
        except Exception as e:
            self.printException(e, "key_end failed")

    # Default stubs for left/right/enter — subclasses should override as needed
    def key_left(self, event: events.Key | None = None) -> None:
        logger.debug("AppBase.key_left called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "AppBase.key_left: event.stop failed")
        except Exception as e:
            self.printException(e, "AppBase.key_left failed")
        return None

    def key_right(self, event: events.Key | None = None) -> None:
        logger.debug("AppBase.key_right called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "AppBase.key_right: event.stop failed")
        except Exception as e:
            self.printException(e, "AppBase.key_right failed")
        return None

    def key_enter(self, event: events.Key | None = None) -> None:
        logger.debug("AppBase.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "AppBase.key_enter: event.stop failed")
        except Exception as e:
            self.printException(e, "AppBase.key_enter failed")
        return None


class FileListBase(AppBase):
    """Base for file list widgets.

    Provides safe focus handling, highlighting helpers, and small default
    implementations that concrete subclasses can override.
    """

    def on_focus(self) -> None:
        # When focused, ensure index is valid.
        try:
            if self.index is None:
                self.index = self._min_index or 0
        except Exception as e:
            self.printException(e, "FileListBase.on_focus")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Mark this base as a file-list so AppBase.watch_index can act
        # conservatively based on widget type flags instead of isinstance.
        self.is_file_list = 1

    def _ensure_index_visible(self) -> None:
        """Ensure the current `index` node is scrolled into view.

        Safe no-op when scrolling APIs are unavailable.
        """
        try:
            idx = self.index or 0
            nodes = self.nodes()
            if not nodes or idx is None:
                return
            if not (0 <= idx < len(nodes)):
                return
            node = nodes[idx]
            # Prefer Textual's scroll_to_widget when available
            if hasattr(self, "scroll_to_widget"):
                try:
                    self.call_after_refresh(lambda: self.scroll_to_widget(node, animate=False))
                    return
                except Exception as e:
                    self.printException(e, "_ensure_index_visible: scroll_to_widget failed")
            # Fallback to node.scroll_visible if provided
            try:
                self.call_after_refresh(lambda: getattr(node, "scroll_visible", lambda *a, **k: None)(True))
            except Exception as e:
                self.printException(e, "_ensure_index_visible: scroll_visible failed")
        except Exception as e:
            self.printException(e, "_ensure_index_visible failed")

    def _add_filelist_key_header(self) -> None:
        """Insert an unselectable key legend row at the top of the file list.

        This places the header at index 0 and sets `_min_index` to 1 so
        navigation skips the header.
        """
        try:
            # Create header row; use the configured style so it's visually distinct
            item = ListItem(Label(Text(FILELIST_KEY_ROW_TEXT, style=STYLE_FILELIST_KEY)))
            # Mark metadata so callers can recognize it and avoid selecting it
            try:
                item._filelist_key_header = True
                item._selectable = False
            except Exception as e:
                self.printException(e, "_add_filelist_key_header: setting metadata failed")
            try:
                # Append header to the list; callers will set `_min_index`
                # after populating rows to ensure it doesn't exceed the
                # available node count.
                self.append(item)
            except Exception as e:
                self.printException(e, "_add_filelist_key_header append failed")
        except Exception as e:
            self.printException(e, "_add_filelist_key_header failed")

    def _highlight_filename(self, filename: str) -> None:
        """Find the first node matching `filename` and move the index there."""
        try:
            nodes = self.nodes()
            # Normalize the provided filename to a canonical full path when
            # possible so comparisons match stored `_raw_text` values.
            try:
                match_full = None
                if filename:
                    if os.path.isabs(filename):
                        match_full = os.path.realpath(filename)
                    else:
                        match_full = os.path.realpath(os.path.join(self.app.repo_root, filename))
            except Exception as e:
                match_full = filename
                self.printException(e, "_highlight_filename: normalizing filename failed")

            for i, node in enumerate(nodes):
                try:
                    # Prefer matching against canonical `_raw_text` when
                    # available; fall back to visible text equality.
                    raw = getattr(node, "_raw_text", None)
                    if raw is not None and match_full is not None:
                        try:
                            node_full = (
                                raw
                                if os.path.isabs(raw)
                                else os.path.realpath(os.path.join(self.app.repo_root, raw))
                            )
                        except Exception as e:
                            node_full = raw
                            self.printException(e, "_highlight_filename: computing node_full failed")
                        if node_full == match_full:
                            try:
                                self.call_after_refresh(lambda: self._activate_index(i))
                            except Exception as e:
                                self.printException(e, "_highlight_filename: scheduling index set failed")
                                try:
                                    self._activate_index(i)
                                except Exception as e:
                                    self.printException(e, "setting index in _highlight_filename")
                            return
                    try:
                        text = self.text_of(node)
                    except Exception as e:
                        self.printException(e, "_highlight_filename: extracting text failed")
                        text = str(node)
                    if text == filename:
                        try:
                            self.call_after_refresh(lambda: self._activate_index(i))
                        except Exception as e:
                            self.printException(e, "_highlight_filename: scheduling index set failed")
                            try:
                                self._activate_index(i)
                            except Exception as e:
                                self.printException(e, "setting index in _highlight_filename")
                        return
                except Exception as e:
                    self.printException(e, "_highlight_filename: checking node failed")
        except Exception as e:
            self.printException(e, "_highlight_filename failed")

    def watch_index(self, old, new) -> None:
        # Placeholder watch — concrete subclasses may override
        try:
            # keep existing logging but delegate to base handler for styling
            try:
                super().watch_index(old, new)
            except Exception as e:
                self.printException(e, "FileListBase.watch_index: base watch failed")
            logger.debug("FileListBase index changed %r -> %r", old, new)
        except Exception as e:
            self.printException(e, "FileListBase.watch_index failed")

    def on_list_view_highlighted(self, event) -> None:
        # Textual-specific hook placeholder for when highlighting changes.
        logger.debug("list view highlighted: %s", event)

    def _child_filename(self, node) -> str:
        try:
            return self.text_of(node)
        except Exception as e:
            printException(e)
            return str(node)

    def _enter_directory(self, filename: str) -> None:
        # Default: log and do nothing. Subclasses should override to change mode.
        logger.debug("enter directory requested: %s", filename)


class FileModeFileList(FileListBase):
    """File-mode file list: shows files for a working tree path.

    For regen Step 3 this class provides a `prepFileModeFileList` stub and
    default `key_left`/`key_right` handlers.
    """

    def prepFileModeFileList(self, path: str, highlight_filename: str | None = None) -> None:
        try:
            logger.debug("prepFileModeFileList: path=%r highlight_filename=%r", path, highlight_filename)
            # Canonicalize path and allow callers to pass a file to highlight
            path = os.path.abspath(path)
            # `highlight_filename` (if provided) takes precedence. If not
            # provided and `path` points at a file, use that file's basename
            # as the highlight and list its containing directory.
            hl = highlight_filename
            if hl is None and os.path.isfile(path):
                hl = os.path.basename(path)
            if os.path.isfile(path):
                path = os.path.dirname(path) or "."

            # Canonicalize the directory path so comparisons (e.g. against
            # the repo root) use real paths and avoid /tmp vs /private/tmp
            # mismatches.
            try:
                path = os.path.realpath(path)
            except Exception as e:
                self.printException(e, "prepFileModeFileList: realpath failed")
                # If realpath fails for some reason, keep the original path

            # Record the list's path
            self.path = path
            # Keep the application's canonical current path in sync with
            # this widget's directory so other components can rely on it.
            self.app.current_path = self.path
            # Also record the app-level `path` so other components (diff,
            # toggles) can rely on the currently-visible directory.
            try:
                self.app.path = self.path
            except Exception as e:
                self.printException(e, "prepFileModeFileList: setting app.path failed")
            relpath = path[len(self.app.repo_root) + 1 :]
            logger.debug(f"prepFileModeFileList: path='{path}' relpath={relpath}")

            # Build a batched `status_map` once per directory when `pygit2` is
            # unavailable. This avoids per-file `git status` calls for large
            # directories. `status_map` maps repository-relative paths to the
            # two-char porcelain status code (e.g. ' M', 'A ', '??'). If the
            # repository isn't available or building the map fails, leave
            # `status_map` as None and fall back to per-file checks later.
            status_map = None
            try:
                if not (self.app.pygit2_repo and pygit2):
                    if path.startswith(self.app.repo_root):
                        prefix = os.path.relpath(path, self.app.repo_root)
                        if prefix == ".":
                            prefix = ""
                        else:
                            prefix = prefix + os.sep
                        try:
                            cmd = ["git", "-C", self.app.repo_root, "status", "--porcelain"]
                            proc = self._run_cmd_log(cmd, label="prepFileModeFileList git status")
                            out = proc.stdout or ""
                            m: dict[str, str] = {}
                            for ln in out.splitlines():
                                if not ln:
                                    continue
                                logger.debug("prepFileModeFileList: git status line: %s", ln)
                                code = ln[:2]
                                logger.debug("prepFileModeFileList: git status code: %s", code)
                                name = ln[3:].rstrip() if len(ln) > 3 else ""
                                if "->" in name:
                                    name = name.split("->")[-1].strip()
                                logger.debug("prepFileModeFileList: git status file: %s code=%s", name, code)
                                if prefix:
                                    if not name.startswith(prefix):
                                        logger.debug(
                                            "prepFileModeFileList: skipping file %s as it does not start with prefix %s",
                                            name,
                                            prefix,
                                        )
                                        continue
                                    rel = name[len(prefix) :]
                                else:
                                    rel = name
                                m[rel] = code
                            status_map = m
                        except Exception as e:
                            self.printException(e, "prepFileModeFileList: git status subprocess failed")
            except Exception as e:
                self.printException(e, "prepFileModeFileList: building status_map failed")
                status_map = None

            # clear and populate
            self.clear()

            # Insert the unselectable key legend header at the top
            try:
                self._add_filelist_key_header()
            except Exception as e:
                self.printException(e, "prepFileModeFileList: adding filelist key header failed")

            # List directory contents
            try:
                entries = sorted(os.listdir(path))
            except Exception as e:
                self.printException(e, f"Error reading {path}")
                try:
                    self.append(ListItem(Label(Text(f"Error reading {path}: {e}", style=STYLE_ERROR))))
                except Exception as e2:
                    self.printException(e2)
                return

            logger.debug(f"prepFileModeFileList: entries in {path}: {entries}")
            # Optionally add a parent entry when appropriate
            try:
                parent = os.path.dirname(path)
                logger.debug("prepFileModeFileList: path=%s parent=%s", path, parent)
                # Only add a parent entry when appropriate and when the
                # current path is not the repository root. Use the canonical
                # `app.repo_root` provided by the application (widgets are
                # expected to have this set by `GitHistoryNavTool`).
                # Only add parent entry when not at the repo root. Compare
                # directly to the app-provided `repo_root` per project axiom.
                if parent and parent != path and path != self.app.repo_root:
                    parent_item = ListItem(Label(Text(f"← ..", style=STYLE_PARENT)))
                    try:
                        parent_item._filename = ".."
                        parent_item._is_dir = True
                        parent_item._raw_text = parent
                        logger.debug("prepFileModeFileList: adding parent dir item for %s", parent_item._raw_text)
                    except Exception as e:
                        self.printException(e, "prepFileModeFileList: setting parent item attributes failed")
                    try:
                        self.append(parent_item)
                    except Exception as e:
                        self.printException(e, "prepFileModeFileList: append parent failed")
            except Exception as e:
                self.printException(e, "prepFileModeFileList: adding parent directory failed")
            try:
                file_infos: list[dict] = []
                if pygit2 and self.app.pygit2_repo:
                    file_infos = self._prepFileModeFileList_from_pygit2(path, relpath, status_map)
                else:
                    file_infos = self._prepFileModeFileList_from_git(path, relpath, status_map)

                for info in file_infos:
                    try:
                        name = info.get("name")
                        full = info.get("full")
                        is_dir = info.get("is_dir", False)
                        raw = info.get("raw", name)
                        repo_status = info.get("repo_status")

                        if is_dir:
                            tag = "→"
                            display_name = f"{name}/"
                            display = f"{tag} {display_name}"
                            style = STYLE_DIR
                            item = ListItem(Label(Text(display, style=style)))
                            try:
                                item._is_dir = True
                                item._repo_status = None
                                item._raw_text = full
                                item._filename = name
                                logger.debug("prepFileModeFileList: adding dir item for %s", full)
                                self.append(item)
                            except Exception as e:
                                self.printException(e, "prepFileModeFileList append dir failed")
                            continue

                        marker = MARKERS.get(repo_status, " ")
                        if repo_status == "conflicted":
                            style = STYLE_CONFLICTED
                        elif repo_status == "staged":
                            style = STYLE_STAGED
                        elif repo_status == "wt_deleted":
                            style = STYLE_WT_DELETED
                        elif repo_status == "ignored":
                            style = STYLE_IGNORED
                        elif repo_status == "modified":
                            style = STYLE_MODIFIED
                        elif repo_status == "untracked":
                            style = STYLE_UNTRACKED
                        else:
                            style = STYLE_DEFAULT

                        display = f"{marker} {name}"
                        try:
                            if style:
                                item = ListItem(Label(Text(display, style=style)))
                            else:
                                item = ListItem(Label(display))
                            item._repo_status = repo_status
                            item._is_dir = False
                            item._raw_text = full
                            item._filename = name
                            self.append(item)
                        except Exception as e:
                            self.printException(e, f"exception appending {name} in prepFileModeFileList")
                            continue
                    except Exception as e:
                        self.printException(e, f"exception processing entry {name}")
                        continue
            except Exception as e:
                self.printException(e, "prepFileModeFileList iteration failed")

            try:
                self._populated = True
                # Ensure navigation skips the header when rows exist
                try:
                    nodes = self.nodes()
                    if len(nodes) > 1:
                        self._min_index = 1
                    else:
                        self._min_index = 0
                except Exception as e:
                    self.printException(e, "prepFileModeFileList: determining min index failed")
                if hl:
                    try:
                        # If `hl` looks like a filename (not an absolute path), prefer
                        # highlighting by the canonical full path inside this list's
                        # directory so matches against `_raw_text` succeed when rows
                        # store absolute paths (common for file lists).
                        try:
                            candidate = hl
                            if not os.path.isabs(candidate):
                                candidate = os.path.join(self.path, candidate)
                        except Exception as e:
                            self.printException(e, "prepFileModeFileList: candidate path adjustment failed")
                            candidate = hl
                        self.call_after_refresh(lambda: self._highlight_match(candidate))
                        # After highlighting, ensure the selected node is visible
                        try:
                            self.call_after_refresh(self._ensure_index_visible)
                        except Exception as e:
                            self.printException(e, "prepFileModeFileList: scheduling _ensure_index_visible failed")
                    except Exception as e:
                        self.printException(e, "prepFileModeFileList: scheduling highlight failed")
                        try:
                            self._highlight_match(hl)
                        except Exception as e:
                            self.printException(e, "prepFileModeFileList: immediate highlight failed")
                else:
                    try:
                        self.call_after_refresh(self._highlight_top)
                    except Exception as e:
                        self.printException(e)
                        try:
                            self._highlight_top()
                        except Exception as e2:
                            self.printException(e2, "immediate _highlight_top fallback failed")
            except Exception as e:
                self.printException(e)
        except Exception as e:
            self.printException(e, "prepFileModeFileList failed")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.highlight_bg_style = HIGHLIGHT_FILELIST_BG


    def _prepFileModeFileList_from_git(self, path: str, relpath: str, status_map: dict | None) -> list[dict]:
        """Return a list of file info dicts for `path` using filesystem/git status.

        Each dict contains: name, full, is_dir, raw, repo_status
        """
        infos: list[dict] = []
        try:
            try:
                items = sorted(os.listdir(path))
            except Exception as _ex:
                self.printException(_ex, "_prepFileModeFileList_from_git: listing path failed")
                items = []
            for name in items:
                try:
                    if name == ".git":
                        continue
                    full = os.path.join(path, name)
                    is_dir = os.path.isdir(full)
                    raw = full if is_dir else name
                    repo_status = None
                    try:
                        if relpath:
                            rel = os.path.join(relpath, name) if relpath else name
                            rel = os.path.normpath(rel)
                        else:
                            rel = name
                        if status_map is not None:
                            code = status_map.get(rel)
                            if code is not None:
                                if code == "??":
                                    repo_status = "untracked"
                                elif code == "!!":
                                    repo_status = "ignored"
                                elif "U" in code:
                                    repo_status = "conflicted"
                                elif code[0] != " ":
                                    repo_status = "staged"
                                elif code[1] != " ":
                                    if code[1] == "D":
                                        repo_status = "wt_deleted"
                                    else:
                                        repo_status = "modified"
                                else:
                                    repo_status = "tracked_clean"
                        # leave repo_status None if unknown; caller may treat as tracked
                    except Exception as _ex:
                        self.printException(_ex, "_prepFileModeFileList_from_git: determining rel/status failed")
                        repo_status = None
                    infos.append({"name": name, "full": full, "is_dir": is_dir, "raw": raw, "repo_status": repo_status})
                except Exception as e:
                    self.printException(e)
                    continue
        except Exception as e:
            self.printException(e, "_prepFileModeFileList_from_git failed")
        return infos

    def _prepFileModeFileList_from_pygit2(self, path: str, relpath: str, status_map: dict | None) -> list[dict]:
        """Return a list of file info dicts using pygit2 when possible.

        For now this attempts to reuse the git-backed implementation for
        directory listing and augments repo_status via pygit2 if available.
        Each dict contains: name, full, is_dir, raw, repo_status
        """
        infos: list[dict] = []

        try:
            try:
                items = sorted(os.listdir(path))
            except Exception as _ex:
                self.printException(_ex, "_prepFileModeFileList_from_pygit2: listing path failed")
                items = []

            repo = self.app.pygit2_repo
            for name in items:
                try:
                    if name == ".git":
                        continue
                    full = os.path.join(path, name)
                    is_dir = os.path.isdir(full)
                    raw = full if is_dir else name
                    repo_status = None
                    if not is_dir:
                        try:
                            rel = os.path.join(relpath, name) if relpath else name
                            rel = os.path.normpath(rel)
                            flags = repo.status_file(rel)
                            if flags & pygit2.GIT_STATUS_IGNORED:
                                repo_status = "ignored"
                            elif flags & pygit2.GIT_STATUS_WT_NEW:
                                repo_status = "untracked"
                            elif flags & pygit2.GIT_STATUS_CONFLICTED:
                                repo_status = "conflicted"
                            elif flags & (
                                pygit2.GIT_STATUS_INDEX_NEW
                                | pygit2.GIT_STATUS_INDEX_MODIFIED
                                | pygit2.GIT_STATUS_INDEX_RENAMED
                                | pygit2.GIT_STATUS_INDEX_TYPECHANGE
                                | pygit2.GIT_STATUS_INDEX_DELETED
                            ):
                                repo_status = "staged"
                            elif flags & pygit2.GIT_STATUS_WT_DELETED:
                                repo_status = "wt_deleted"
                            elif flags & (
                                pygit2.GIT_STATUS_WT_MODIFIED
                                | pygit2.GIT_STATUS_WT_RENAMED
                                | pygit2.GIT_STATUS_WT_TYPECHANGE
                            ):
                                repo_status = "modified"
                            else:
                                repo_status = "tracked_clean"
                        except Exception as _ex:
                            self.printException(_ex, "_prepFileModeFileList_from_pygit2: status_file failed")
                            repo_status = None

                    infos.append({"name": name, "full": full, "is_dir": is_dir, "raw": raw, "repo_status": repo_status})
                except Exception as e:
                    self.printException(e)
                    continue
        except Exception as e:
            self.printException(e, "_prepFileModeFileList_from_pygit2 failed")
        return infos

    def _nav_dir_if(self, test_fn) -> None:
        try:
            idx = self.index or 0
            nodes = self.nodes()
            if not (0 <= idx < len(nodes)):
                return
            item = nodes[idx]
            if not getattr(item, "_is_dir", False):
                return
            name = getattr(item, "_filename", None)
            raw = getattr(item, "_raw_text", None)
            if test_fn(name) and raw:
                try:
                    self.prepFileModeFileList(raw)
                except Exception as e:
                    self.printException(e, "FileModeFileList._nav_dir_if prep failed")
        except Exception as e:
            self.printException(e, "FileModeFileList._nav_dir_if failed")

    def _activate_or_open(
        self,
        event: events.Key | None = None,
        enter_dir_test_fn=lambda name: True,
        allow_file_open: bool = True,
    ) -> None:
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "_activate_or_open: event.stop failed")

            idx = self.index or 0
            nodes = self.nodes()
            if not (0 <= idx < len(nodes)):
                return
            item = nodes[idx]
            is_dir = getattr(item, "_is_dir", False)
            name = getattr(item, "_filename", None)
            raw = getattr(item, "_raw_text", None)
            repo_status = getattr(item, "_repo_status", None)

            if is_dir:
                if enter_dir_test_fn(name) and raw:
                    try:
                        # If navigating up to the parent entry ('..'), pass the
                        # current directory basename as the highlight filename
                        # so the parent listing highlights the directory we
                        # came from.
                        hl = None
                        try:
                            if name == "..":
                                # Pass the full directory path so the highlight
                                # matcher can match the node `_raw_text` which
                                # stores full paths.
                                hl = self.path
                        except Exception as e:
                            printException(e)
                            self.printException(
                                e, "FileModeFileList._activate_or_open highlight filename fallback failed"
                            )

                        # Record the app-level path for downstream components
                        try:
                            self.app.path = raw
                        except Exception as e:
                            self.printException(e, "FileModeFileList._activate_or_open setting app.path failed")
                        self.prepFileModeFileList(raw, highlight_filename=hl)
                    except Exception as e:
                        self.printException(e, "FileModeFileList._activate_or_open prep failed")
                return

            # File selected: open repo view for tracked files only (when allowed)
            if raw and repo_status not in ("untracked", "ignored") and allow_file_open:
                try:
                    try:
                        self.app.path = raw
                    except Exception as e:
                        self.printException(e, "FileModeFileList._activate_or_open setting app.path failed")
                    self.app.file_mode_history_list.prepFileModeHistoryList(raw)
                    try:
                        # Switch UI to file-history layout and focus
                        self.app.change_state("file_history", f"#{RIGHT_HISTORY_LIST_ID}", RIGHT_HISTORY_FOOTER)
                    except Exception as e:
                        self.printException(e, "FileModeFileList._activate_or_open change_state failed")
                except Exception as e:
                    self.printException(e, "FileModeFileList._activate_or_open repo open failed")
        except Exception as e:
            self.printException(e, "FileModeFileList._activate_or_open failed")

    def key_left(self, event: events.Key | None = None) -> None:
        # Navigate up only when the selected directory is the parent entry ('..')
        # Use shared helper so event.stop() is honored and behavior is unified.
        # Do not open files when pressing left; only allow entering parent dir
        logger.debug("FileModeFileList.key_left called: key=%r index=%r", getattr(event, "key", None), self.index)
        self._activate_or_open(event, enter_dir_test_fn=lambda name: name == "..", allow_file_open=False)

    def key_right(self, event: events.Key | None = None) -> None:
        # Use shared helper to handle directory enter or file open.
        logger.debug("FileModeFileList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index)
        self._activate_or_open(event, enter_dir_test_fn=lambda name: (name is not None) and name != "..")

    def key_enter(self, event: events.Key | None = None) -> None:
        # Enter key: enter directories or open file history for tracked files.
        logger.debug("FileModeFileList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        self._activate_or_open(event, enter_dir_test_fn=lambda name: True)


class RepoModeFileList(FileListBase):
    """Repo-mode file list: shows files changed between commits.

    Provides a `prepRepoModeFileList` stub and navigation handlers.
    """

    def prepRepoModeFileList(
        self, prev_hash: str | None, curr_hash: str | None, highlight_filename: str | None = None
    ) -> None:
        try:
            logger.debug(
                "prepRepoModeFileList: prev_hash=%r curr_hash=%r highlight_filename=%r",
                prev_hash,
                curr_hash,
                highlight_filename,
            )
            self.clear()
            # Insert a hash header and the unselectable key legend header at the top
            try:
                try:

                    def _short(h: str | None) -> str:
                        if not h:
                            return "None"
                        return h[:12] if len(h) > 12 else h

                    hash_text = f"Hashes: prev={_short(prev_hash)}  curr={_short(curr_hash)}"
                    hash_item = ListItem(Label(Text(hash_text, style=STYLE_FILELIST_KEY)))
                    try:
                        hash_item._hash_header = True
                        hash_item._selectable = False
                        self.append(hash_item)
                    except Exception as e:
                        self.printException(e, "prepRepoModeFileList: appending hash header failed")
                except Exception as e:
                    self.printException(e, "prepRepoModeFileList: creating hash header failed")
                # Then append the existing key legend row so it appears below
                # the hashes header.
                try:
                    self._add_filelist_key_header()
                except Exception as e:
                    self.printException(e, "prepRepoModeFileList _add_filelist_key_header failed")
            except Exception as e:
                self.printException(e, "prepRepoModeFileList header setup failed")

            # If caller passed pseudo-hashes (MODS/STAGED) treat them
            # specially rather than passing them through to `git diff`.
            # Collect file lists for the pseudo refs and render them.
            pseudo_names = ("MODS", "STAGED")
            pseudo_entries = []
            try:

                def _collect_for(pseudo: str) -> list[tuple[str, str]]:
                    out = ""
                    items: list[tuple[str, str]] = []
                    try:
                        if pseudo == "MODS":
                            cmd = ["git", "-C", self.app.repo_root, "diff", "--name-status"]
                        elif pseudo == "STAGED":
                            cmd = ["git", "-C", self.app.repo_root, "diff", "--name-status", "--cached"]
                        else:
                            cmd = []
                        if cmd:
                            proc = self._run_cmd_log(cmd, label="prepRepoModeFileList pseudo diff")
                            out = proc.stdout or ""
                        logger.debug(
                            "prepRepoModeFileList: pseudo %s diff (cmd=%s) output: %s", pseudo, " ".join(cmd), out
                        )
                        for ln in out.splitlines():
                            if not ln:
                                continue
                            parts = ln.split("\t", 1)
                            status = parts[0]
                            path = parts[1] if len(parts) > 1 else parts[0]
                            items.append((status, path))
                    except subprocess.CalledProcessError as _ex:
                        printException(_ex)
                        return []
                    except Exception as e:
                        self.printException(e, f"collecting pseudo entries for {pseudo} failed")
                    return items

                if prev_hash in pseudo_names:
                    pseudo_entries.extend(_collect_for(prev_hash))
                if curr_hash in pseudo_names:
                    pseudo_entries.extend(_collect_for(curr_hash))
                logger.debug(
                    "prepRepoModeFileList: prev_hash=%r curr_hash=%r pseudo_entries=%r",
                    prev_hash,
                    curr_hash,
                    pseudo_entries,
                )
            except Exception as e:
                self.printException(e, "prepRepoModeFileList collecting pseudo entries failed")

            if pseudo_entries:
                # Render collected pseudo entries and skip the git-diff-with-refs path
                try:
                    for status, path in pseudo_entries:
                        try:
                            display = f"{status} {path}"
                            item = ListItem(Label(Text(display)))
                            try:
                                if not os.path.isabs(path):
                                    full = os.path.realpath(os.path.join(self.app.repo_root, path))
                                else:
                                    full = os.path.realpath(path)
                                item._raw_text = full
                            except Exception as e:
                                self.printException(e, "prepRepoModeFileList: resolving full path failed")
                                item._raw_text = path
                            item._is_dir = False
                            self.append(item)
                        except Exception as e:
                            self.printException(e, "prepRepoModeFileList append pseudo entry failed")
                except Exception as e:
                    self.printException(e, "prepRepoModeFileList rendering pseudo entries failed")
            else:
                # Delegate diff collection to helpers so alternate backends
                # (pygit2 vs git CLI) can provide entries. Each helper returns
                # a list of dicts with keys: display, full, is_dir.
                try:
                    if pygit2 and self.app.pygit2_repo:
                        entries = self._prepRepoModeFileList_from_pygit2(prev_hash, curr_hash)
                    else:
                        entries = self._prepRepoModeFileList_from_git(prev_hash, curr_hash)
                    for entry in entries:
                        try:
                            display = entry.get("display") if isinstance(entry, dict) else str(entry)
                            item = ListItem(Label(Text(display)))
                            try:
                                item._raw_text = entry.get("full", display)
                            except Exception as e:
                                self.printException(e, "prepRepoModeFileList: resolving full path failed")
                                item._raw_text = entry.get("full", display)
                            item._is_dir = entry.get("is_dir", False) if isinstance(entry, dict) else False
                            self.append(item)
                        except Exception as e:
                            self.printException(e, "prepRepoModeFileList append entry failed")
                except Exception as e:
                    self.printException(e, "prepRepoModeFileList git diff failed")

            self._populated = True
            # Highlight based on provided hashes (prefer curr_hash) or
            # by filename when `highlight_filename` is provided. Ensure
            # navigation skips the header when rows exist
            try:
                nodes = self.nodes()
                header_count = 2
                if len(nodes) > header_count:
                    self._min_index = header_count
                else:
                    self._min_index = 0
            except Exception as e:
                self.printException(e, "prepRepoModeFileList: setting _min_index failed")
            # Immediately record the repo-level commit pair so other
            # components can access the selected refs.
            try:
                self.app.previous_hash = prev_hash
                self.app.current_hash = curr_hash
                # If caller requested a filename highlight, record it as
                # `app.path` and set `app.current_path` to the canonical
                # full path so other components can rely on it.
                if highlight_filename:
                    self.app.path = highlight_filename
                    self.app.current_path = highlight_filename
                    # Prefer setting current_path which canonicalizes.
                    self.app.current_path = highlight_filename
            except Exception as e:
                self.printException(e, "prepRepoModeFileList: recording app-level state failed")
            try:
                # If a filename highlight was requested prefer it over
                # commit-based highlighting.
                if highlight_filename:
                    self._highlight_filename(highlight_filename)
                else:
                    self._highlight_top()
            except Exception as e:
                self.printException(e, "prepRepoModeFileList: highlight failed")
        except Exception as e:
            self.printException(e, "prepRepoModeFileList failed")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.highlight_bg_style = HIGHLIGHT_FILELIST_BG

    def key_left(self, event: events.Key | None = None) -> None:
        # Move to previous view or update state in main app (stub)
        logger.debug("RepoModeFileList.key_left called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "RepoModeFileList.key_left: event.stop failed")
        try:
            # Switch layout back to left-side history fullscreen
            self.app.change_state("history_fullscreen", f"#{LEFT_HISTORY_LIST_ID}", LEFT_HISTORY_FOOTER)
        except Exception as e:
            self.printException(e, "RepoModeFileList.key_left change_state failed")

    def key_right(self, event: events.Key | None = None, recursive: bool = False) -> None:
        # Open diff view for selected file: delegate to DiffList and switch layout.
        if not recursive:
            logger.debug("RepoModeFileList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "RepoModeFileList.key_right: event.stop failed")
        try:
            idx = self.index or 0
            nodes = self.nodes()
            if not (0 <= idx < len(nodes)):
                return
            node = nodes[idx]
            filename = getattr(node, "_raw_text", None) or self._child_filename(node)

            # Pass through the app-level commit pair unchanged; variant is fixed for now
            variant_index = 0
            self.app.path = filename

            try:
                # When opening from the repo-file list, we want DiffList.key_left
                # to return to the repo file list view.
                self.app.diff_list.prepDiffList(
                    filename,
                    self.app.previous_hash,
                    self.app.current_hash,
                    variant_index,
                    ("history_file", RIGHT_FILE_LIST_ID, RIGHT_FILE_FOOTER),
                )
            except Exception as e:
                self.printException(e, "RepoModeFileList.key_right: prepDiffList failed")

            try:
                self.app.change_state("history_file_diff", f"#{DIFF_LIST_ID}", HISTORY_FILE_DIFF_FOOTER)
            except Exception as e:
                self.printException(e, "RepoModeFileList.key_right change_state failed")
        except Exception as e:
            self.printException(e, "RepoModeFileList.key_right failed")

    def key_enter(self, event: events.Key | None = None) -> None:
        """Same behavior as Right: open the diff for the selected file."""
        logger.debug("RepoModeFileList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_right(event, recursive=True)

    def _prepRepoModeFileList_from_git(self, prev_hash: str | None, curr_hash: str | None) -> list[dict]:
        """Return a list of dicts for the repo-mode file list using git CLI.

        Each dict contains: display, full, is_dir
        """
        entries: list[dict] = []
        try:
            if prev_hash and curr_hash:
                cmd = ["git", "-C", self.app.repo_root, "diff", "--name-status", prev_hash, curr_hash]
            elif curr_hash:
                cmd = ["git", "-C", self.app.repo_root, "show", "--name-status", "--pretty=format:", curr_hash]
            else:
                cmd = ["git", "-C", self.app.repo_root, "diff", "--name-status"]
            proc = self._run_cmd_log(cmd, label="prepRepoModeFileList git diff")
            out = proc.stdout or ""
            logger.debug("_prepRepoModeFileList_from_git: cmd=%s output=%s", " ".join(cmd), out)
            for ln in out.splitlines():
                if not ln:
                    continue
                try:
                    parts = ln.split("\t", 1)
                    status = parts[0]
                    path = parts[1] if len(parts) > 1 else parts[0]
                    display = f"{status} {path}"
                    try:
                        if not os.path.isabs(path):
                            full = os.path.realpath(os.path.join(self.app.repo_root, path))
                        else:
                            full = os.path.realpath(path)
                    except Exception as e:
                        self.printException(e, "_prepRepoModeFileList_from_git: resolving full path failed")
                        full = path
                    entries.append({"display": display, "full": full, "is_dir": False})
                except Exception as e:
                    self.printException(e, "_prepRepoModeFileList_from_git parsing line failed")
        except Exception as e:
            self.printException(e, "_prepRepoModeFileList_from_git failed")
        return entries

    def _prepRepoModeFileList_from_pygit2(self, prev_hash: str | None, curr_hash: str | None) -> list[dict]:
        """Return a list of dicts using pygit2 diffs when available.

        Attempts to compute a diff via pygit2 and map deltas to simple
        status letters. Falls back to an empty list on error.
        """
        entries: list[dict] = []
        try:
            try:
                has_repo = self.app.pygit2_repo
            except Exception as _ex:
                self.printException(_ex, "_prepRepoModeFileList_from_pygit2: accessing pygit2_repo failed")
                has_repo = None
            if not (pygit2 and has_repo):
                return entries
            repo = has_repo
            diff = None
            try:
                if prev_hash and curr_hash:
                    a = repo.revparse_single(prev_hash)
                    b = repo.revparse_single(curr_hash)
                    a_tree = a.tree if hasattr(a, "tree") else a
                    b_tree = b.tree if hasattr(b, "tree") else b
                    diff = repo.diff(a_tree, b_tree)
                elif curr_hash:
                    c = repo.revparse_single(curr_hash)
                    parents = list(c.parents)
                    if parents:
                        diff = repo.diff(parents[0].tree, c.tree)
                    else:
                        diff = repo.diff(None, c.tree)
                else:
                    diff = repo.diff()
            except Exception as e:
                self.printException(e, "_prepRepoModeFileList_from_pygit2: building diff failed")
                diff = None

            if diff is None:
                return entries

            for delta in diff.deltas:
                try:
                    path = (delta.new_file.path or delta.old_file.path) if hasattr(delta, "new_file") else None
                    if not path:
                        continue
                    st = "?"
                    try:
                        if delta.status == pygit2.GIT_DELTA_ADDED:
                            st = "A"
                        elif delta.status == pygit2.GIT_DELTA_MODIFIED:
                            st = "M"
                        elif delta.status == pygit2.GIT_DELTA_DELETED:
                            st = "D"
                        elif delta.status == pygit2.GIT_DELTA_RENAMED:
                            st = "R"
                        elif delta.status == pygit2.GIT_DELTA_COPIED:
                            st = "C"
                        elif delta.status == pygit2.GIT_DELTA_CONFLICTED:
                            st = "!"
                    except Exception as _ex:
                        self.printException(_ex, "_prepRepoModeFileList_from_pygit2: mapping delta.status failed")
                        st = "?"
                    display = f"{st} {path}"
                    try:
                        if not os.path.isabs(path):
                            full = os.path.realpath(os.path.join(self.app.repo_root, path))
                        else:
                            full = os.path.realpath(path)
                    except Exception as e:
                        self.printException(e, "_prepRepoModeFileList_from_pygit2: resolving full path failed")
                        full = path
                    entries.append({"display": display, "full": full, "is_dir": False})
                except Exception as e:
                    self.printException(e, "_prepRepoModeFileList_from_pygit2 iter delta failed")
        except Exception as e:
            self.printException(e, "_prepRepoModeFileList_from_pygit2 failed")
        return entries

    def _prepRepoModePseudo_from_git(self, pseudo: str) -> list[tuple[str, str]]:
        """Collect (status, path) tuples for pseudo refs using git CLI.

        `pseudo` is one of 'MODS' or 'STAGED'. Returns list of (status, path).
        """
        out = ""
        items: list[tuple[str, str]] = []
        try:
            if pseudo == "MODS":
                cmd = ["git", "-C", self.app.repo_root, "diff", "--name-status"]
            elif pseudo == "STAGED":
                cmd = ["git", "-C", self.app.repo_root, "diff", "--name-status", "--cached"]
            else:
                return []
            proc = self._run_cmd_log(cmd, label="prepRepoModeFileList pseudo diff")
            out = proc.stdout or ""
            for ln in out.splitlines():
                if not ln:
                    continue
                parts = ln.split("\t", 1)
                status = parts[0]
                path = parts[1] if len(parts) > 1 else parts[0]
                items.append((status, path))
        except subprocess.CalledProcessError as _ex:
            printException(_ex)
            return []
        except Exception as e:
            self.printException(e, f"_prepRepoModePseudo_from_git failed for {pseudo}")
            return []
        return items

    def _prepRepoModePseudo_from_pygit2(self, pseudo: str) -> list[tuple[str, str]]:
        """Collect (status, path) tuples for pseudo refs using pygit2.

        Map pygit2 status flags to single-letter status codes similar to
        git's `--name-status` output. Returns list of (status, path).
        """
        items: list[tuple[str, str]] = []
        try:
            try:
                repo = self.app.pygit2_repo
            except Exception as _ex:
                self.printException(_ex, "_prepRepoModePseudo_from_pygit2: accessing pygit2_repo failed")
                return []
            try:
                status_map = repo.status()
            except Exception as e:
                self.printException(e, "_prepRepoModePseudo_from_pygit2: repo.status() failed")
                return []
            for path, flags in status_map.items():
                try:
                    # Determine inclusion based on pseudo type
                    include = False
                    if pseudo == "MODS":
                        # include working-tree changes and untracked
                        if flags & (
                            pygit2.GIT_STATUS_WT_NEW
                            | pygit2.GIT_STATUS_WT_MODIFIED
                            | pygit2.GIT_STATUS_WT_RENAMED
                            | pygit2.GIT_STATUS_WT_TYPECHANGE
                            | pygit2.GIT_STATUS_WT_DELETED
                        ):
                            include = True
                    elif pseudo == "STAGED":
                        if flags & (
                            pygit2.GIT_STATUS_INDEX_NEW
                            | pygit2.GIT_STATUS_INDEX_MODIFIED
                            | pygit2.GIT_STATUS_INDEX_RENAMED
                            | pygit2.GIT_STATUS_INDEX_TYPECHANGE
                            | pygit2.GIT_STATUS_INDEX_DELETED
                        ):
                            include = True
                    if not include:
                        continue

                    # Map flags to status letter
                    st = "?"
                    try:
                        if flags & pygit2.GIT_STATUS_INDEX_NEW:
                            st = "A"
                        elif flags & pygit2.GIT_STATUS_INDEX_MODIFIED:
                            st = "M"
                        elif flags & pygit2.GIT_STATUS_INDEX_DELETED:
                            st = "D"
                        elif flags & pygit2.GIT_STATUS_WT_NEW:
                            st = "U"
                        elif flags & pygit2.GIT_STATUS_WT_MODIFIED:
                            st = "M"
                        elif flags & pygit2.GIT_STATUS_WT_DELETED:
                            st = "D"
                        elif flags & pygit2.GIT_STATUS_CONFLICTED:
                            st = "!"
                    except Exception as _ex:
                        self.printException(_ex, "_prepRepoModePseudo_from_pygit2: mapping flags failed")
                        st = "?"

                    items.append((st, path))
                except Exception as e:
                    self.printException(e, "_prepRepoModePseudo_from_pygit2 iter failed")
        except Exception as e:
            self.printException(e, "_prepRepoModePseudo_from_pygit2 failed")
        return items


class HistoryListBase(AppBase):
    """Base for history (commit) lists.

    Provides helpers to attach metadata to rows and compute commit-pair hashes.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # History lists should use repository highlight backgrounds
        self.highlight_bg_style = HIGHLIGHT_REPOLIST_BG
        # Mark as history list for flag-based checks in AppBase.watch_index
        self.is_history_list = 1

    def _add_row(self, text: str, commit_hash: str | None) -> None:
        try:
            # Visible rows are prefixed with two spaces for alignment; keep
            # `_raw_text` as the original value for metadata and matching.
            display_text = f"  {text}"
            item = ListItem(Label(Text(display_text)))
            # Attach helpful metadata for later lookup
            setattr(item, "_hash", commit_hash)
            setattr(item, "_raw_text", text)
            try:
                self.append(item)
            except Exception as e:
                self.printException(e, "HistoryListBase._add_row append failed")
        except Exception as e:
            self.printException(e, "HistoryListBase._add_row failed")

    def toggle_check_current(self, idx: int | None = None) -> None:
        try:
            if idx is None:
                idx = self.index or 0
            nodes = self.nodes()
            if not (0 <= idx < len(nodes)):
                return
            # Enforce single-mark semantics: mark the selected item (M ) and
            # unmark all others. If the selected item was already marked, clear it.
            try:
                selected_node = nodes[idx]
                was_marked = getattr(selected_node, "_checked", False)
                # If it was marked, unmark everything; otherwise mark selected and unmark others
                for i, node in enumerate(nodes):
                    try:
                        is_selected = i == idx
                        if was_marked:
                            setattr(node, "_checked", False)
                        else:
                            setattr(node, "_checked", is_selected)

                        # Update label renderable
                        try:
                            lbl = node.query_one(Label)
                            raw = getattr(node, "_raw_text", "")
                            if getattr(node, "_checked", False):
                                # Marked: prefix with 'M ' and apply contrasting style
                                marked_txt = Text(f"M {raw}", style="bold white on red")
                                lbl.update(marked_txt)
                            else:
                                # Unmarked: two-space prefix (already applied during add), plain style
                                lbl.update(Text(f"  {raw}"))
                        except Exception as e:
                            self.printException(e, "updating label renderable failed")
                    except Exception as e:
                        self.printException(e, "updating _checked attribute failed")
            except Exception as e:
                self.printException(e, "HistoryListBase.toggle_check_current update failed")
        except Exception as e:
            self.printException(e, "toggle_check_current failed")

    def key_m(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Toggle the 'marked' state for the currently-selected history row."""
        if not recursive:
            logger.debug("HistoryListBase.key_m called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "HistoryListBase.key_m: event.stop failed")
            try:
                self.toggle_check_current()
            except Exception as e:
                self.printException(e, "HistoryListBase.key_m toggle failed")
        except Exception as e:
            self.printException(e, "HistoryListBase.key_m failed")

    def key_M(self, event: events.Key | None = None) -> None:
        logger.debug("HistoryListBase.key_M called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_m(event, recursive=True)

    def compute_commit_pair_hashes(self, idx: int | None = None) -> tuple[str | None, str | None]:
        try:
            if idx is None:
                idx = self.index or 0
            nodes = self._nodes or []
            if not nodes:
                return (None, None)
            # Current commit is at idx; previous is idx+1 (newer->older ordering varies)
            curr = getattr(nodes[idx], "_hash", None) if 0 <= idx < len(nodes) else None
            prev = getattr(nodes[idx + 1], "_hash", None) if 0 <= (idx + 1) < len(nodes) else None
            return (prev, curr)
        except Exception as e:
            self.printException(e, "compute_commit_pair_hashes failed")
            return (None, None)

    def on_focus(self) -> None:
        try:
            if self.index is None:
                # Respect widget-specific minimum index when focusing
                self.index = self._min_index or 0
        except Exception as e:
            self.printException(e, "HistoryListBase.on_focus")

    def on_list_view_highlighted(self, event) -> None:
        logger.debug("history highlighted: %s", event)

    def _compute_selected_pair(self) -> tuple[str | None, str | None]:
        """Return (prev_hash, curr_hash) where prev is older and curr is newer.

        If a row is marked (single-mark semantics) use the marked row and the
        currently-selected row as the pair. Otherwise compute the pair as the
        currently-selected row and the following row.
        """
        try:
            idx = self.index or 0
            nodes = self.nodes()
            if not nodes:
                return (None, None)
            selected_hash = getattr(nodes[idx], "_hash", None)
            marked_idx = None
            for i, node in enumerate(nodes):
                if getattr(node, "_checked", False):
                    marked_idx = i
                    break
            if marked_idx is not None:
                marked_hash = getattr(nodes[marked_idx], "_hash", None)
                # History ordering: lower index == newer, higher index == older
                if marked_idx > idx:
                    # marked is older
                    try:
                        # update app-level hashes for other components
                        try:
                            self.app.current_hash = selected_hash
                            self.app.previous_hash = marked_hash
                        except Exception as e:
                            self.printException(e, "updating app-level hashes failed")
                    except Exception as e:
                        self.printException(e, "updating app-level hashes failed")
                    return (marked_hash, selected_hash)
                else:
                    try:
                        self.app.current_hash = marked_hash
                        self.app.previous_hash = selected_hash
                    except Exception as e:
                        self.printException(e, "updating app-level hashes failed")
                    return (selected_hash, marked_hash)

            # No marked row — fall back to adjacent pair computation
            prev, curr = self.compute_commit_pair_hashes(idx)
            try:
                self.app.current_hash = curr
                self.app.previous_hash = prev
            except Exception as e:
                self.printException(e, "updating app-level hashes failed")
            return (prev, curr)
        except Exception as e:
            self.printException(e, "_compute_selected_pair failed")
            return (None, None)


class FileModeHistoryList(HistoryListBase):
    """History list for a single file's history. Stubbed prep method."""

    def prepFileModeHistoryList(self, path: str, prev_hash: str | None = None, curr_hash: str | None = None) -> None:
        try:
            logger.debug("prepFileModeHistoryList: path=%r prev_hash=%r curr_hash=%r", path, prev_hash, curr_hash)
            self.clear()
            # If repository available, collect pseudo-entries (MODS/STAGED)
            # and commit history via backend helpers.
            repo_root = self.app.repo_root
            try:
                try:
                    # Normalize to repo-relative path for backend helpers
                    rel_path = path if not os.path.isabs(path) else os.path.relpath(path, repo_root)
                except Exception as e:
                    self.printException(e, "prepFileModeHistoryList: computing rel_path failed")
                    rel_path = path

                pseudo_entries: list[tuple[str, str]] = []
                entries: list[tuple[str, str, str]] = []
                if repo_root:
                    try:
                        if pygit2 and getattr(self.app, "pygit2_repo", None):
                            pseudo_entries, entries = self._prepFileModeHistoryList_for_pygit2(repo_root, rel_path)
                        else:
                            pseudo_entries, entries = self._prepFileModeHistoryList_for_git(repo_root, rel_path)
                    except Exception as e:
                        self.printException(e, "prepFileModeHistoryList collecting history failed")

                # render pseudo entries first
                try:
                    for status, desc in pseudo_entries:
                        try:
                            self._add_row(desc, status)
                        except Exception as e:
                            self.printException(e, "prepFileModeHistoryList adding pseudo-row failed")
                except Exception as e:
                    self.printException(e, "prepFileModeHistoryList rendering pseudo entries failed")

                # then render real commit entries
                try:
                    for h, date_stamp, msg in entries:
                        try:
                            short_hash = h[:12] if h else ""
                            text = f"{date_stamp} {short_hash} {msg}".strip()
                            self._add_row(text, h)
                        except Exception as e:
                            self.printException(e, "prepFileModeHistoryList parse failed")
                except Exception as e:
                    self.printException(e, "prepFileModeHistoryList rendering commits failed")

            self._populated = True
            # Highlight requested commits when provided. Prefer `curr_hash`
            # and mark `prev_hash` if present; otherwise highlight top.
            try:
                if curr_hash:
                    self._highlight_match(curr_hash)
                elif prev_hash:
                    for i, node in enumerate(self.nodes()):
                        if getattr(node, "_hash", None) == prev_hash:
                            try:
                                self.toggle_check_current(i)
                            except Exception as e:
                                self.printException(e, "prepFileModeHistoryList toggle_check_current failed")
                            break
                else:
                    self._highlight_top()
            except Exception as e:
                self.printException(e, "prepFileModeHistoryList: highlight failed")
            # Ensure app-level hashes/path are updated immediately after prep
            try:
                if curr_hash is not None or prev_hash is not None:
                    self.app.current_hash = curr_hash
                    self.app.previous_hash = prev_hash
                else:
                    # compute and record selected pair based on current index
                    self._compute_selected_pair()
                    # record the file associated with this history list
                    self.app.current_path = path
            except Exception as e:
                self.printException(e, "prepFileModeHistoryList: highlight failed")
        except Exception as e:
            self.printException(e, "prepFileModeHistoryList failed")

    def _prepFileModeHistoryList_commits_from_git(self, repo_root: str, rel_path: str) -> list[tuple[str, str, str]]:
        """Return a list of (hash, date, subject) using the git CLI."""
        entries: list[tuple[str, str, str]] = []
        try:
            cmd = [
                "git",
                "-C",
                repo_root,
                "log",
                "--follow",
                "--pretty=format:%H\t%ad\t%s",
                "--date=short",
                "--",
                rel_path,
            ]
            proc = self._run_cmd_log(cmd, label="prepFileModeHistoryList git log")
            for ln in (proc.stdout or "").splitlines():
                try:
                    parts = ln.split("\t", 2)
                    h = parts[0]
                    date_stamp = parts[1] if len(parts) > 1 else ""
                    msg = parts[2] if len(parts) > 2 else ""
                    entries.append((h, date_stamp, msg))
                except Exception as e:
                    self.printException(e, "_prepFileModeHistoryList_commits_from_git parse failed")
        except Exception as e:
            self.printException(e, "_prepFileModeHistoryList_commits_from_git failed")
        return entries

    def _prepFileModeHistoryList_commits_from_pygit2(self, repo_root: str, rel_path: str) -> list[tuple[str, str, str]]:
        """Return a list of (hash, date, subject) using pygit2.

        Note: this implementation does not fully implement `--follow` (rename
        tracking). It walks commits reachable from HEAD and records commits
        whose diffs touch `rel_path`.
        """
        entries: list[tuple[str, str, str]] = []
        try:
            repo = pygit2.Repository(repo_root)
            try:
                head = repo.head.target
            except Exception as _ex:
                printException(_ex)
                head = None
            if head is None:
                return entries
            walker = repo.walk(head, pygit2.GIT_SORT_TIME)
            # Walk commits from HEAD backwards (time order). Maintain a
            # `search_path` that is updated when we observe renames so we can
            # follow the file history across rename operations (approx `--follow`).
            search_path = rel_path
            for commit in walker:
                try:
                    parents = commit.parents
                    parent = parents[0] if parents else None
                    if parent is None:
                        # Initial commit: no parent. Check whether the path
                        # exists in the commit tree as a proxy for being
                        # introduced/modified in this commit.
                        try:
                            # Will raise KeyError if not present
                            _ = commit.tree[search_path]
                            touched = True
                        except Exception as _no_logging:
                            touched = False
                        diff = None
                    else:
                        diff = repo.diff(parent.tree, commit.tree)
                    touched = False
                    if diff is not None:
                        for delta in diff.deltas:
                            try:
                                oldp = getattr(delta.old_file, "path", None)
                                newp = getattr(delta.new_file, "path", None)
                                # Match against the current search_path which may
                                # be updated when a rename is discovered.
                                if oldp == search_path or newp == search_path:
                                    touched = True
                                    # If this delta represents a rename/copy where
                                    # the new path equals our search target, update
                                    # the search_path to the old path so older
                                    # commits are checked against the previous name.
                                    try:
                                        if getattr(delta, "status", None) in (
                                            pygit2.GIT_DELTA_RENAMED,
                                            pygit2.GIT_DELTA_COPIED,
                                        ):
                                            if newp == search_path and oldp and oldp != newp:
                                                search_path = oldp
                                    except Exception as _ex:
                                        printException(_ex)
                                    break
                            except Exception as _ex:
                                printException(_ex)
                                continue
                    if touched:
                        try:
                            h = str(commit.id)
                            date_stamp = datetime.fromtimestamp(commit.author.time).strftime("%Y-%m-%d")
                            msg = (commit.message or "").splitlines()[0].strip()
                            entries.append((h, date_stamp, msg))
                        except Exception as e:
                            self.printException(e, "_prepFileModeHistoryList_commits_from_pygit2 entry build failed")
                except Exception as _ex:
                    printException(_ex)
                    continue
        except Exception as e:
            self.printException(e, "_prepFileModeHistoryList_commits_from_pygit2 failed")
        return entries

    
    def _prepFileModeHistoryList_for_git(self, repo_root: str, rel_path: str) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        """Collect pseudo entries and commits for a single file using git CLI.

        Returns (pseudo_entries, commits) where pseudo_entries is a list of
        (status, desc) tuples and commits is a list of (hash, date, msg).
        """
        pseudo_entries: list[tuple[str, str]] = []
        commits: list[tuple[str, str, str]] = []
        try:
            try:
                cmd = ["git", "-C", repo_root, "diff", "--name-only", "--", rel_path]
                proc = self._run_cmd_log(cmd, label="prepFileModeHistoryList diff --name-only")
                mods = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_git getting modified file failed")
                mods = []
            try:
                cmd = ["git", "-C", repo_root, "diff", "--name-only", "--cached", "--", rel_path]
                proc = self._run_cmd_log(cmd, label="prepFileModeHistoryList diff --name-only --cached")
                staged = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_git getting staged file failed")
                staged = []

            try:
                if mods:
                    pseudo_entries.append(("MODS", "MODS (modified, unstaged)"))
                if staged:
                    pseudo_entries.append(("STAGED", "STAGED (staged, uncommitted)"))
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_git adding pseudo summaries failed")

            try:
                commits = self._prepFileModeHistoryList_commits_from_git(repo_root, rel_path)
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_git collecting commits failed")
        except Exception as e:
            self.printException(e, "_prepFileModeHistoryList_for_git failed")
        return (pseudo_entries, commits)

    def _prepFileModeHistoryList_for_pygit2(self, repo_root: str, rel_path: str) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        """Collect pseudo entries and commits for a single file using pygit2.

        Returns (pseudo_entries, commits) similar to the git helper.
        """
        pseudo_entries: list[tuple[str, str]] = []
        commits: list[tuple[str, str, str]] = []
        try:
            try:
                repo = self.app.pygit2_repo
            except Exception as _ex:
                self.printException(_ex, "_prepFileModeHistoryList_for_pygit2: accessing pygit2_repo failed")
                return (pseudo_entries, commits)

            try:
                status = repo.status_file(rel_path)
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_pygit2: repo.status_file failed")
                status = 0

            try:
                mods_flags = (
                    pygit2.GIT_STATUS_WT_NEW
                    | pygit2.GIT_STATUS_WT_MODIFIED
                    | pygit2.GIT_STATUS_WT_RENAMED
                    | pygit2.GIT_STATUS_WT_TYPECHANGE
                    | pygit2.GIT_STATUS_WT_DELETED
                )
                index_flags = (
                    pygit2.GIT_STATUS_INDEX_NEW
                    | pygit2.GIT_STATUS_INDEX_MODIFIED
                    | pygit2.GIT_STATUS_INDEX_RENAMED
                    | pygit2.GIT_STATUS_INDEX_TYPECHANGE
                    | pygit2.GIT_STATUS_INDEX_DELETED
                )
                if status & mods_flags:
                    pseudo_entries.append(("MODS", "MODS (modified, unstaged)"))
                if status & index_flags:
                    pseudo_entries.append(("STAGED", "STAGED (staged, uncommitted)"))
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_pygit2 adding pseudo summaries failed")

            try:
                commits = self._prepFileModeHistoryList_commits_from_pygit2(repo_root, rel_path)
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_pygit2 collecting commits failed")
        except Exception as e:
            self.printException(e, "_prepFileModeHistoryList_for_pygit2 failed")
        return (pseudo_entries, commits)


    def key_right(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Open the diff for the selected file commit-pair.

        Compute the current and previous hashes (using marked rows if present),
        determine the filename from the app-level `path`, call
        `self.app.diff_list.prepDiffList(filename, prev, curr)` and switch the
        UI to the file-history-diff layout.
        """
        if not recursive:
            logger.debug(
                "FileModeHistoryList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index
            )
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "FileModeHistoryList.key_right: event.stop failed")

        prev_hash, curr_hash = self._compute_selected_pair()
        try:
            filename = self.app.path
            # Ask the diff list to prepare the diff for this file and pair
            try:
                # When opening from a file's history, ensure left returns to
                # the file-history view on the right history column.
                self.app.diff_list.prepDiffList(
                    filename,
                    prev_hash,
                    curr_hash,
                    0,
                    ("file_history", RIGHT_HISTORY_LIST_ID, RIGHT_HISTORY_FOOTER),
                )
            except Exception as e:
                self.printException(e, "FileModeHistoryList.key_right: prepDiffList failed")

            # Switch to the file-history-diff layout and focus diff list
            try:
                self.app.change_state("file_history_diff", f"#{DIFF_LIST_ID}", HISTORY_FILE_DIFF_FOOTER)
            except Exception as e:
                self.printException(e, "FileModeHistoryList.key_right change_state failed")
        except Exception as e:
            self.printException(e, "FileModeHistoryList.key_right prep failed")

    def key_enter(self, event: events.Key | None = None) -> None:
        # Same behavior as Right: open the file commit-pair diff
        logger.debug("FileModeHistoryList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_right(event, recursive=True)

    def key_left(self, event: events.Key | None = None) -> None:
        """Return to file fullscreen and focus the left file list."""
        logger.debug("FileModeHistoryList.key_left called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "FileModeHistoryList.key_left: event.stop failed")
        try:
            app = self.app
            app.change_state("file_fullscreen", f"#{LEFT_FILE_LIST_ID}", LEFT_FILE_FOOTER)
        except Exception as e:
            self.printException(e, "FileModeHistoryList.key_left change_state failed")


class RepoModeHistoryList(HistoryListBase):
    """History list for repository-wide commits. Stubbed prep method."""

    def prepRepoModeHistoryList(
        self, repo_path: str | None = None, prev_hash: str | None = None, curr_hash: str | None = None
    ) -> None:
        try:
            logger.debug(
                "prepRepoModeHistoryList: repo_path=%r prev_hash=%r curr_hash=%r", repo_path, prev_hash, curr_hash
            )
            self.clear()
            # Collect pseudo-entries and commit rows via backend helpers
            try:
                try:
                    has_repo = self.app.pygit2_repo
                except Exception as _ex:
                    self.printException(_ex, "prepRepoModeHistoryList: accessing pygit2_repo failed")
                    has_repo = None
                if pygit2 and has_repo:
                    pseudo_entries, commits = self._prepRepoModeHistoryList_for_pygit2(repo_path, prev_hash, curr_hash)
                else:
                    pseudo_entries, commits = self._prepRepoModeHistoryList_for_git(repo_path, prev_hash, curr_hash)

                # Insert MODS then STAGED at the top if present
                try:
                    if pseudo_entries:
                        # pseudo_entries contains tuples like (status, path) and may include MODS/STAGED labels
                        for status, path in pseudo_entries:
                            # status may be 'MODS' or 'STAGED' summary rows represented specially
                            if status in ("MODS", "STAGED"):
                                # path carries the count/caption
                                self._add_row(path, status)
                                continue
                            # otherwise these are file tuples (status, path)
                            display = f"{status} {path}"
                            item = ListItem(Label(Text(display)))
                            try:
                                if not os.path.isabs(path):
                                    full = os.path.realpath(os.path.join(self.app.repo_root, path))
                                else:
                                    full = os.path.realpath(path)
                                item._raw_text = full
                            except Exception as e:
                                self.printException(e, "prepRepoModeHistoryList: resolving full path failed")
                                item._raw_text = path
                            item._is_dir = False
                            self.append(item)
                except Exception as e:
                    self.printException(e, "prepRepoModeHistoryList adding pseudo-rows failed")

                # Render commit rows
                try:
                    for commit_hash, date_stamp, msg in commits:
                        try:
                            short_hash = commit_hash[:12] if commit_hash else ""
                            text = f"{date_stamp} {short_hash} {msg}"
                            self._add_row(text, commit_hash)
                        except Exception as e:
                            self.printException(e, "prepRepoModeHistoryList parse failed")
                except Exception as e:
                    self.printException(e, "prepRepoModeHistoryList commit rendering failed")
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList backend collection failed")

            self._populated = True
            # Highlight requested commits when provided. Prefer highlighting
            # the `curr_hash` then marking `prev_hash` as the checked row.
            try:
                if curr_hash:
                    self._highlight_match(curr_hash)
                elif prev_hash:
                    # find and mark the previous commit row
                    for i, node in enumerate(self.nodes()):
                        if getattr(node, "_hash", None) == prev_hash:
                            try:
                                self.toggle_check_current(i)
                            except Exception as e:
                                self.printException(e, "prepRepoModeHistoryList toggle_check_current failed")
                            break
                else:
                    self._highlight_top()
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: highlight failed")
            # Ensure app-level commit pair state reflects the newly-highlighted
            # selection so callers (e.g. repo file preparer) can rely on
            # `app.current_hash` and `app.previous_hash` immediately.
            try:
                # Compute the selected pair after the UI refresh so any
                # scheduled index activation (from `_highlight_match`) has
                # taken effect. Calling `_compute_selected_pair` immediately
                # can observe the old index (e.g. top/pseudo rows) which
                # results in incorrect pseudo-hash selection.
                def _compute_and_log() -> None:
                    try:
                        self._compute_selected_pair()
                        logger.debug(
                            "prepRepoModeHistoryList: after compute_selected_pair app.previous_hash=%r app.current_hash=%r",
                            self.app.previous_hash,
                            self.app.current_hash,
                        )
                    except Exception as e:
                        self.printException(e, "prepRepoModeHistoryList: computing selected pair failed")

                try:
                    self.call_after_refresh(_compute_and_log)
                except Exception as e:
                    self.printException(e, "prepRepoModeHistoryList: scheduling compute_selected_pair failed")
                    # Fallback: run immediately if scheduling isn't available
                    _compute_and_log()
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: scheduling compute_selected_pair failed")
        except Exception as e:
            self.printException(e, "prepRepoModeHistoryList failed")

    def _prepRepoModeHistoryList_for_git(
        self, repo_path: str | None, prev_hash: str | None, curr_hash: str | None
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        """Collect pseudo entries and commits using git CLI.

        Returns (pseudo_entries, commits) where pseudo_entries is a list of
        (status, path) tuples and commits is a list of (hash, date, msg).
        """
        pseudo_entries: list[tuple[str, str]] = []
        commits: list[tuple[str, str, str]] = []
        try:
            try:
                cmd = ["git", "-C", self.app.repo_root, "diff", "--name-only"]
                proc = self._run_cmd_log(cmd, label="prepRepoModeHistoryList diff --name-only")
                mods = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_git getting modified files failed")
                mods = []
            try:
                cmd = ["git", "-C", self.app.repo_root, "diff", "--name-only", "--cached"]
                proc = self._run_cmd_log(cmd, label="prepRepoModeHistoryList diff --name-only --cached")
                staged = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_git getting staged files failed")
                staged = []

            # add summary pseudo rows first if present
            try:
                if mods:
                    pseudo_entries.append(("MODS", f"MODS ({len(mods)} modified file{'s' if len(mods) != 1 else ''})"))
                if staged:
                    pseudo_entries.append(("STAGED", f"STAGED ({len(staged)} staged file{'s' if len(staged) != 1 else ''})"))
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_git adding pseudo summaries failed")

            # then include the individual file entries for visibility
            for p in mods:
                pseudo_entries.append(("M", p))
            for p in staged:
                pseudo_entries.append(("S", p))

            # collect commits
            try:
                if self.app.repo_root:
                    cmd = [
                        "git",
                        "-C",
                        self.app.repo_root,
                        "log",
                        "--pretty=format:%H\t%ad\t%s",
                        "--date=short",
                        "-n",
                        "200",
                    ]
                    proc = self._run_cmd_log(cmd, label="prepRepoModeHistoryList git log")
                    for ln in (proc.stdout or "").splitlines():
                        if not ln:
                            continue
                        try:
                            parts = ln.split("\t", 2)
                            commit_hash = parts[0]
                            date_stamp = parts[1] if len(parts) > 1 else ""
                            msg = parts[2] if len(parts) > 2 else ""
                            commits.append((commit_hash, date_stamp, msg))
                        except Exception as e:
                            self.printException(e, "_prepRepoModeHistoryList_for_git parse failed")
            except subprocess.CalledProcessError as _ex:
                printException(_ex)
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_git git log failed")
        except Exception as e:
            self.printException(e, "_prepRepoModeHistoryList_for_git failed")
        return (pseudo_entries, commits)

    def _prepRepoModeHistoryList_for_pygit2(
        self, repo_path: str | None, prev_hash: str | None, curr_hash: str | None
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        """Collect pseudo entries and commits using pygit2.

        Returns (pseudo_entries, commits) similar to the git helper.
        """
        pseudo_entries: list[tuple[str, str]] = []
        commits: list[tuple[str, str, str]] = []
        try:
            try:
                repo = self.app.pygit2_repo
            except Exception as _ex:
                self.printException(_ex, "_prepRepoModeHistoryList_for_pygit2: accessing pygit2_repo failed")
                return (pseudo_entries, commits)

            # status map: repo.status() -> dict[path] = flags
            try:
                status_map = repo.status()
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_pygit2: repo.status() failed")
                status_map = {}

            mods = [p for p, f in status_map.items() if f & (
                pygit2.GIT_STATUS_WT_NEW
                | pygit2.GIT_STATUS_WT_MODIFIED
                | pygit2.GIT_STATUS_WT_RENAMED
                | pygit2.GIT_STATUS_WT_TYPECHANGE
                | pygit2.GIT_STATUS_WT_DELETED
            )]
            staged = [p for p, f in status_map.items() if f & (
                pygit2.GIT_STATUS_INDEX_NEW
                | pygit2.GIT_STATUS_INDEX_MODIFIED
                | pygit2.GIT_STATUS_INDEX_RENAMED
                | pygit2.GIT_STATUS_INDEX_TYPECHANGE
                | pygit2.GIT_STATUS_INDEX_DELETED
            )]

            try:
                if mods:
                    pseudo_entries.append(("MODS", f"MODS ({len(mods)} modified file{'s' if len(mods) != 1 else ''})"))
                if staged:
                    pseudo_entries.append(("STAGED", f"STAGED ({len(staged)} staged file{'s' if len(staged) != 1 else ''})"))
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_pygit2 adding pseudo summaries failed")

            for p in mods:
                pseudo_entries.append(("M", p))
            for p in staged:
                pseudo_entries.append(("S", p))

            # Walk commits
            try:
                head = None
                try:
                    head = repo.head.target if repo.head_is_unborn is False else None
                except Exception as _ex:
                    self.printException(_ex, "_prepRepoModeHistoryList_for_pygit2: detecting head failed")
                    head = None
                if head:
                    walker = repo.walk(head, pygit2.GIT_SORT_TIME)
                    count = 0
                    for c in walker:
                        try:
                            commit_hash = c.id.hex
                            # use author time for date
                            t = getattr(c.author, "time", None)
                            if t:
                                date_stamp = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
                            else:
                                date_stamp = ""
                            msg = (c.message or "").splitlines()[0]
                            commits.append((commit_hash, date_stamp, msg))
                            count += 1
                            if count >= 200:
                                break
                        except Exception as e:
                            self.printException(e, "_prepRepoModeHistoryList_for_pygit2 walk parse failed")
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_pygit2 commit walk failed")
        except Exception as e:
            self.printException(e, "_prepRepoModeHistoryList_for_pygit2 failed")
        return (pseudo_entries, commits)

    def key_right(self, event: events.Key | None = None) -> None:
        """Open the selected/marked commit-pair in the repo file list preparer.

        This method lives on the repo-mode history widget because the action
        it performs (populate the repo file list and switch to the files
        column) is meaningful only for repository-wide history views.
        """
        logger.debug("RepoModeHistoryList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "RepoModeHistoryList.key_right: event.stop failed")
            prev_hash, curr_hash = self._compute_selected_pair()
            try:
                # Delegate to the repo-mode file list preparer. The preparer
                # understands pseudo-hashes like MODS/STAGED. Pass the
                # currently-selected filename (app.path) as a highlight so
                # the file list highlights the expected file.
                # Prefer the canonical current_path (full path) for highlight
                # comparisons so repo-mode file rows (which store full paths)
                # match deterministically.
                hf = self.app.current_path or self.app.path
                logger.debug(
                    "RepoModeHistoryList.key_right: prev=%r curr=%r app.current_path=%r app.path=%r",
                    prev_hash,
                    curr_hash,
                    self.app.current_path,
                    self.app.path,
                )
                self.app.repo_mode_file_list.prepRepoModeFileList(prev_hash, curr_hash, highlight_filename=hf)
                try:
                    # Switch to the right-file list view and update footer
                    self.app.change_state("history_file", f"#{RIGHT_FILE_LIST_ID}", RIGHT_FILE_FOOTER)
                except Exception as e:
                    self.printException(e, "RepoModeHistoryList.key_right change_state failed")
            except Exception as e:
                self.printException(e, "RepoModeHistoryList.key_right prep failed")
        except Exception as e:
            self.printException(e, "RepoModeHistoryList.key_right failed")

    def key_enter(self, event: events.Key | None = None) -> None:
        # Same behavior as Right: open the commit-pair file list
        logger.debug("RepoModeHistoryList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_right(event, recursive=True)


HELP_TEXT = """
gitdiffnavtool help

- Navigation: up/down/pageup/pagedown/home/end
- Open/enter: right
- Back/close: left
- Diff color toggle: c
- Save diff: d
- Find in diff: f
"""


class DiffList(AppBase):
    """List view for showing diffs.

    `prepDiffList` is a stub here; later steps will call `git diff` or pygit2
    and colorize output. Key handlers toggle colorization and expose actions.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._colorized = True
        self.highlight_bg_style = HIGHLIGHT_DIFF_BG
        # Stored output lines from the last diff command
        self.output: list[str] = []
        # Current diff variant index used when re-prepping the diff
        self.variant: int = 0
        # Where to return when leaving the diff view: (state_name, widget_id, footer)
        # Always initialized to a non-None default so callers can rely on it.
        self.go_back: tuple = ("history_file", RIGHT_FILE_LIST_ID, RIGHT_FILE_FOOTER)
        # Saved layout for fullscreen diff toggles. When the user presses
        # Right from a history_file_diff or file_history_diff layout we save
        # that layout so `key_left` can restore it when exiting fullscreen.
        self._saved_layout: str | None = None

    def prepDiffList(self, filename: str, prev: str, curr: str, variant_index: int, go_back: tuple) -> None:
        try:
            logger.debug(
                "DiffList.prepDiffList: filename=%s prev=%s curr=%s variant=%s go_back=%s",
                filename,
                prev,
                curr,
                variant_index,
                go_back,
            )
            # Prefer the canonicalized `current_path` on the app when available
            out = ""
            try:
                cmd = self.app.build_diff_cmd(filename, prev, curr, variant_index)
                proc = self._run_cmd_log(cmd, label="prepDiffList diff")
                # Prefer stdout when available; keep empty string on failure
                out = proc.stdout or ""
            except Exception as e:
                self.printException(e, "prepDiffList: running git diff failed")
                out = ""

            # If the textual diff is empty, attempt to collect metadata (renames,
            # mode changes, summary) so the UI can indicate non-textual changes.
            if not (out and out.strip()):
                try:
                    repo_root = self.app.repo_root
                    pseudo_names = ("MODS", "STAGED")
                    use_cached = prev == "STAGED" or curr == "STAGED"
                    meta_cmd = ["git", "-C", repo_root, "diff"]
                    if use_cached:
                        meta_cmd.append("--cached")
                    meta_cmd += ["--name-status", "--summary"]
                    # Include explicit refs when provided (avoid pseudo names)
                    if prev in pseudo_names or curr in pseudo_names:
                        if prev and prev not in pseudo_names and curr and curr not in pseudo_names:
                            meta_cmd += [prev, curr]
                        elif curr and curr not in pseudo_names:
                            meta_cmd.append(curr)
                        elif prev and prev not in pseudo_names:
                            meta_cmd.append(prev)
                    else:
                        if prev and curr:
                            meta_cmd += [prev, curr]
                        elif curr and not prev:
                            meta_cmd.append(curr)
                    if filename:
                        meta_cmd += ["--", filename]
                    proc_meta = self._run_cmd_log(meta_cmd, label="prepDiffList metadata diff")
                    meta_out = proc_meta.stdout or ""
                    if meta_out.strip():
                        out = meta_out
                    else:
                        out = "(no textual changes for this file)"
                except Exception as e:
                    self.printException(e, "prepDiffList: metadata diff failed")

            # Save output lines on the object and render via helper
            self.output = out.splitlines() if out else []
            # Record the active variant for future re-renders
            self.variant = variant_index
            # Update go-back state only.
            self.go_back = go_back

            try:
                self._render_output()
            except Exception as e:
                self.printException(e, "prepDiffList: render failed")
            try:
                self._populated = True
                self._highlight_top()
            except Exception as e:
                self.printException(e, "prepDiffList: highlight failed")
        except Exception as e:
            self.printException(e, "prepDiffList failed")

    def key_c(self, event: events.Key | None = None) -> None:
        logger.debug(
            "DiffList.key_c called: key=%r index=%r", getattr(event, "key", None), self.index
        )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "DiffList.key_c: event.stop failed")
            self._colorized = not self._colorized
            logger.debug("DiffList colorized=%s", self._colorized)
            try:
                self._render_output()
            except Exception as e:
                self.printException(e, "DiffList.key_c re-render failed")
        except Exception as e:
            self.printException(e, "DiffList.key_c failed")

    def key_right(self, event: events.Key | None = None) -> None:
        """When in a history-file diff layout, promote the diff to fullscreen.

        If the current app layout is one of the file-history diff layouts,
        save it and switch to the `diff_fullscreen` layout. Otherwise noop.
        """
        logger.debug(
            "DiffList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index
        )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "DiffList.key_right: event.stop failed")
            current = self.app._current_layout
            if current in ("history_file_diff", "file_history_diff"):
                try:
                    # save then switch to fullscreen diff
                    self._saved_layout = current
                    self.app.change_layout("diff_fullscreen")
                except Exception as e:
                    self.printException(e, "DiffList.key_right change_layout failed")
        except Exception as e:
            self.printException(e, "DiffList.key_right failed")

    def key_C(self, event: events.Key | None = None) -> None:
        logger.debug("DiffList.key_C called: key=%r", getattr(event, "key", None))
        return self.key_c(event, recursive=True)

    def _render_output(self) -> None:
        """Clear and render `self.output` honoring `self._colorized`."""
        try:
            self.clear()
            for ln in self.output or []:
                try:
                    style = None
                    if self._colorized:
                        if ln.startswith("+") and not ln.startswith("+++"):
                            style = "green"
                        elif ln.startswith("-") and not ln.startswith("---"):
                            style = "red"
                        elif ln.startswith("@@"):
                            style = "magenta"
                        elif ln.startswith("diff --git") or ln.startswith("index "):
                            style = "bold white"
                    if style:
                        self.append(ListItem(Label(Text(ln, style=style))))
                    else:
                        self.append(ListItem(Label(Text(ln))))
                except Exception as e:
                    self.printException(e, "_render_output append failed")
        except Exception as e:
            self.printException(e, "_render_output failed")

    def key_d(self, event: events.Key | None = None) -> None:
        logger.debug(
            "DiffList.key_d called: key=%r variant=%r index=%r",
            getattr(event, "key", None),
            self.variant,
            self.index,
        )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "DiffList.key_d: event.stop failed")
            # Rotate to the next diff variant and re-run the diff preparer.
            try:
                total = len(self.app.diff_variants or [None])
                new_variant = (int(self.variant or 0) + 1) % max(1, total)
            except Exception as e:
                self.printException(e, "DiffList.key_d: computing new variant failed")
                new_variant = 0
            logger.debug("DiffList.key_d: switching to variant %s from %s", new_variant, self.variant)
            try:
                # Use the app-level path and selected commit pair when re-prepping
                # Preserve the current go_back state when re-prepping.
                self.prepDiffList(
                    self.app.path,
                    self.app.previous_hash,
                    self.app.current_hash,
                    new_variant,
                    self.go_back,
                )
            except Exception as e:
                self.printException(e, "DiffList.key_d: re-prep failed")
        except Exception as e:
            self.printException(e, "DiffList.key_d failed")

    def key_D(self, event: events.Key | None = None) -> None:
        logger.debug(
            "DiffList.key_D called: key=%r index=%r", getattr(event, "key", None), self.index
        )
        return self.key_d(event, recursive=True)

    def key_left(self, event: events.Key | None = None) -> None:
        """Return from diff view to the right file list."""
        logger.debug("DiffList.key_left called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "DiffList.key_left: event.stop failed")
            try:
                # If we're in fullscreen diff, restore the saved layout.
                current = self.app._current_layout
                if current == "diff_fullscreen":
                    try:
                        target = self._saved_layout or "history_file_diff"
                        # restore layout
                        self.app.change_layout(target)
                        # clear saved layout
                        self._saved_layout = None
                        return
                    except Exception as e:
                        self.printException(e, "DiffList.key_left restore layout failed")

                # Otherwise fall back to the recorded go-back tuple.
                state_name, widget_id, footer = self.go_back
                self.app.change_state(state_name, f"#{widget_id}", footer)
            except Exception as e:
                self.printException(e, "DiffList.key_left change_state failed")
        except Exception as e:
            self.printException(e, "DiffList.key_left failed")

    def key_enter(self, event: events.Key | None = None) -> None:
        """If fullscreen, act like Left (close); otherwise act like Right.

        This mirrors the behavior of using Enter to toggle fullscreen/back.
        """
        logger.debug("DiffList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            current = self.app._current_layout
            if current == "diff_fullscreen":
                return self.key_left(event, recursive=True)
            else:
                return self.key_right(event, recursive=True)
        except Exception as e:
            self.printException(e, "DiffList.key_enter failed")


class HelpList(AppBase):
    """Renders help text as list rows and allows restoring previous state."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.highlight_bg_style = HIGHLIGHT_HELP_BG

    def prepHelp(self) -> None:
        try:
            logger.debug("prepHelp: invoked")
            self.clear()
            for ln in HELP_TEXT.strip().splitlines():
                try:
                    self.append(ListItem(Label(Text(ln))))
                except Exception as e:
                    self.printException(e, "prepHelp append failed")
            self._populated = True
            try:
                self._highlight_top()
            except Exception as e:
                self.printException(e, "prepHelp: highlight failed")
        except Exception as e:
            self.printException(e, "prepHelp failed")

    def key_enter(self, event: events.Key | None = None) -> None:
        logger.debug(
            "HelpList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index
        )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "HelpList.key_enter: event.stop failed")
            app = self.app
            try:
                app.restore_state()
            except Exception as e:
                self.printException(e, "HelpList.restore_state failed")
        except Exception as e:
            self.printException(e, "HelpList.key_enter failed")


class GitHistoryNavTool(App):
    """Main Textual application wiring the lists together.

    It composes the previously defined widgets, mounts a header/footer,
    and provides simple state save/restore stubs.
    """

    CSS = INLINE_CSS

    def __init__(
        self,
        path: str,
        no_color: bool,
        repo_first: bool,
        repo_hashes: list,
        repo_root: str,
        **kwargs,
    ):
        # Accept CLI options here so the app can inspect them during mount
        super().__init__(**kwargs)
        try:
            self.path = path
            self.no_color = no_color
            self.repo_first = repo_first
            # optional repo hash initialization (list of 1 or 2 hashes)
            # Normalize repo_hashes to a list (avoid mutable default)
            self.repo_hashes = repo_hashes or []
            # placeholders for runtime state
            # `repo_root` is provided by main and should not be modified further.
            self.repo_root = repo_root
            # Set the application title to include the repository path
            self.title = f"GitHistoryNavTool ({self.repo_root or '.'})"
            self._saved_state = None
            self._current_layout = None
            # Track current focus selector for save/restore; initialize here
            self._current_focus = None
            # Track the currently-selected and previous commit hashes
            self.current_hash = None
            self.previous_hash = None
            # Best-effort: cache a pygit2 Repository object to avoid
            # constructing it per-file. If pygit2 isn't available set
            # `pygit2_repo` to None so callers can fall back to CLI.
            self.pygit2_repo = None
            if pygit2:
                try:
                    self.pygit2_repo = pygit2.Repository(self.repo_root)
                except Exception as e:
                    printException(e, "GitHistoryNavTool.__init__: pygit2.Repository init failed")

            # Optional diff variant arguments indexed by variant_index.
            # index 0 -> None (no extra arg), 1 -> ignore-space-change, 2 -> patience algorithm
            self.diff_variants: list[Optional[str]] = [None, "--ignore-space-change", "--diff-algorithm=patience"]

            # Initialize `_current_path` to either the provided path when
            # it's a directory, or the dirname when `path` is a file.
            # Use the property setter so the value is canonicalized.
            self._current_path = self.path if os.path.isdir(self.path) else os.path.dirname(self.path)
        except Exception as e:
            printException(e, "GitHistoryNavTool.__init__ failed")

    @property
    def current_path(self) -> str | None:
        """The current working path for the app, always stored as a realpath.

        External code should set `app.current_path = some_path` and the
        property will canonicalize it via `os.path.realpath`. A None value
        is preserved as None.
        """
        return self._current_path

    @current_path.setter
    def current_path(self, value: str) -> None:
        try:
            # Treat empty/false as '.' and always store realpath
            p = value if value else "."
            self._current_path = os.path.realpath(p)
        except Exception as e:
            # Fall back to storing raw value and log
            self.printException(e, "setting current_path failed")
            self._current_path = value

    def printException(self, e: Exception, msg: Optional[str] = None) -> None:
        """Instance-level exception logger for the App to mirror widget helper.

        Keeps behavior consistent with `AppBase.printException`.
        """
        try:
            className = type(self).__name__
            funcName = sys._getframe(1).f_code.co_name
            short = msg or ""
            logger.warning(f"{className}.{funcName}: {short} - {e}")
            logger.warning(traceback.format_exc())
        except Exception as e_fallback:
            # Fall back to module-level printer
            printException(e, msg)
            printException(e_fallback, "GitHistoryNavTool.printException fallback")

    def compose(self):
        # Compose the canonical six-column layout using Vertical columns
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="left-file-column"):
                yield Label(Text("Files"), id=LEFT_FILE_TITLE)
                yield FileModeFileList(id=LEFT_FILE_LIST_ID)
            with Vertical(id="left-history-column"):
                yield Label(Text("History"), id=LEFT_HISTORY_TITLE)
                yield RepoModeHistoryList(id=LEFT_HISTORY_LIST_ID)
            with Vertical(id="right-history-column"):
                yield Label(Text("History"), id=RIGHT_HISTORY_TITLE)
                yield FileModeHistoryList(id=RIGHT_HISTORY_LIST_ID)
            with Vertical(id="right-file-column"):
                yield Label(Text("Files"), id=RIGHT_FILE_TITLE)
                yield RepoModeFileList(id=RIGHT_FILE_LIST_ID)
            with Vertical(id="diff-column"):
                yield Label(Text("Diff"), id=DIFF_TITLE)
                yield DiffList(id=DIFF_LIST_ID)
            with Vertical(id="help-column"):
                yield Label(Text("Help"), id=HELP_TITLE)
                yield HelpList(id=HELP_LIST_ID)

        # Use a Label with id="footer" so `change_footer` can update it.
        # Placing it outside the `Horizontal` ensures it always sits below
        # the columns and remains visible regardless of layout changes.
        yield Label(Text(""), id="footer")

    async def on_mount(self) -> None:
        try:
            # Repo discovery is handled by `main()` and passed into the app;
            # do not perform any repo scans here.
            # Resolve and store references to the six canonical widgets
            try:
                self.file_mode_file_list = self.query_one(f"#{LEFT_FILE_LIST_ID}", FileModeFileList)
                self.repo_mode_history_list = self.query_one(f"#{LEFT_HISTORY_LIST_ID}", RepoModeHistoryList)
                self.file_mode_history_list = self.query_one(f"#{RIGHT_HISTORY_LIST_ID}", FileModeHistoryList)
                self.repo_mode_file_list = self.query_one(f"#{RIGHT_FILE_LIST_ID}", RepoModeFileList)
                self.diff_list = self.query_one(f"#{DIFF_LIST_ID}", DiffList)
                self.help_list = self.query_one(f"#{HELP_LIST_ID}", HelpList)
            except Exception as e:
                printException(e)
                # composition must match expected ids
                raise RuntimeError(f"widget resolution failed in on_mount: {e}") from e

            # Populate the canonical left lists and set focus so key handlers
            # and highlight behavior work immediately in both modes.
            try:
                if not self.repo_first:
                    try:
                        self.file_mode_file_list.prepFileModeFileList(path=self.path or ".")
                        # Centralize layout/focus/footer handling via change_state.
                        try:
                            self.change_state("file_fullscreen", f"#{LEFT_FILE_LIST_ID}", LEFT_FILE_FOOTER)
                        except Exception as e:
                            self.printException(e, "on_mount: change_state for file_fullscreen failed")
                    except Exception as e:
                        self.printException(e, "on_mount: prepFileModeFileList failed")
                else:
                    # If starting in repo-first mode, pre-populate the left
                    # repository-history widget so the UI shows commits immediately.
                    try:
                        self.repo_mode_history_list.prepRepoModeHistoryList(repo_path=self.path or ".")
                        # Centralize layout/focus/footer handling via change_state.
                        try:
                            self.change_state("history_fullscreen", f"#{LEFT_HISTORY_LIST_ID}", LEFT_HISTORY_FOOTER)
                        except Exception as e:
                            self.printException(e, "on_mount: change_state for history_fullscreen failed")
                    except Exception as e:
                        self.printException(e, "on_mount: prepRepoModeHistoryList failed")
                    # If repo hashes were provided on the command line, use them
                    try:
                        rh = self.repo_hashes or []
                        if rh:
                            # Normalize to up to two values: previous, current
                            prev = None
                            curr = None
                            if len(rh) == 1:
                                curr = rh[0]
                            else:
                                prev = rh[0]
                                curr = rh[1]
                            try:
                                self.current_prev_sha = prev
                                self.current_commit_sha = curr
                            except Exception as e:
                                self.printException(e, "setting current_prev_sha and current_commit_sha failed")
                            try:
                                # If starting in repo-first mode, populate the repo file list
                                # with the specified hashes so the UI reflects them immediately.
                                if self.repo_first:
                                    try:
                                        self.repo_mode_file_list.prepRepoModeFileList(prev, curr)
                                    except Exception as e:
                                        self.printException(e, "preparing repo mode file list failed")
                            except Exception as e:
                                self.printException(e, "on_mount: initializing repo hashes failed")
                    except Exception as e:
                        self.printException(e, "on_mount: initializing repo hashes failed")

                    # Ensure help content is prepared so help is immediately available
                    try:
                        if self.help_list is not None:
                            try:
                                self.help_list.prepHelp()
                            except Exception as e:
                                self.printException(e, "on_mount: prepHelp failed")
                    except Exception as e:
                        self.printException(e, "on_mount: prepHelp outer failure")
            except Exception as e:
                self.printException(e, "on_mount: initial prep failed")
        except Exception as e:
            printException(e, "on_mount failed")

    def key_q(self, event: events.Key | None = None) -> None:
        """Quit the application on `q` keypress (synonym for ^Q)."""
        logger.debug("GitHistoryNavTool.key_q called: key=%r", getattr(event, "key", None))
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_q: event.stop failed")
            logger.debug("key_q invoked; exiting app")
            try:
                # App.exit() is the Textual API to stop the app.
                self.exit()
            except Exception as e:
                self.printException(e, "key_q: app.exit failed")
                # Fallback to raising SystemExit
                raise SystemExit(0)
        except Exception as e:
            self.printException(e, "key_q failed")

    def key_Q(self, event: events.Key | None = None) -> None:
        """Uppercase Q also quits."""
        logger.debug("GitHistoryNavTool.key_Q called: key=%r", getattr(event, "key", None))
        return self.key_q(event, recursive=True)

    def key_h(self, event: events.Key | None = None) -> None:
        """Show help: save state, prepare help, then display help fullscreen.

        This records the single-slot state, ensures help content is prepared,
        and switches layout/focus/footer to the help configuration.
        """
        logger.debug("GitHistoryNavTool.key_h called: key=%r", getattr(event, "key", None))
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_h: event.stop failed")
            logger.debug("key_h invoked: saving state and showing help")
            try:
                self.save_state()
            except Exception as e:
                self.printException(e, "key_h: save_state failed")
            # Help text is static and prepopulated during on_mount; no need
            # to call prepHelp() again here.

            try:
                self.change_state("help_fullscreen", f"#{HELP_LIST_ID}", HELP_FOOTER)
            except Exception as e:
                self.printException(e, "key_h: change_state failed")
        except Exception as e:
            self.printException(e, "key_h outer failure")

    def key_H(self, event: events.Key | None = None) -> None:
        logger.debug("GitHistoryNavTool.key_H called: key=%r", getattr(event, "key", None))
        return self.key_h(event, recursive=True)

    def key_question(self, event: events.Key | None = None) -> None:
        # Some terminals map '?' to 'question'
        logger.debug("GitHistoryNavTool.key_question called: key=%r", getattr(event, "key", None))
        return self.key_h(event, recursive=True)

    def _apply_column_layout(
        self,
        left_file_w: int,
        left_history_w: int,
        right_history_w: int,
        right_file_w: int,
        diff_w: int,
        help_w: int,
    ) -> None:
        """Set column widths and visibility for the six canonical columns.

        If a width is zero the column is hidden (styles.display set to "none").
        Otherwise `styles.width` is set to "{width}%" and `styles.display` is cleared.
        """
        show = None
        hide = "none"
        try:
            # set container widths
            try:
                c = self.query_one("#left-file-column")
                c.styles.width = f"{left_file_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set left-file-column")
            try:
                c = self.query_one("#left-history-column")
                c.styles.width = f"{left_history_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set left-history-column")
            try:
                c = self.query_one("#right-history-column")
                c.styles.width = f"{right_history_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set right-history-column")
            try:
                c = self.query_one("#right-file-column")
                c.styles.width = f"{right_file_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set right-file-column")
            try:
                c = self.query_one("#diff-column")
                c.styles.width = f"{diff_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set diff-column")
            try:
                c = self.query_one("#help-column")
                c.styles.width = f"{help_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set help-column")

            # set widget visibility based on widths
            try:
                self.file_mode_file_list.styles.display = show if left_file_w else hide
            except Exception as e:
                self.printException(e, "could not set left-file-list display")
            try:
                self.repo_mode_history_list.styles.display = show if left_history_w else hide
            except Exception as e:
                self.printException(e, "could not set left-history-list display")
            try:
                self.file_mode_history_list.styles.display = show if right_history_w else hide
            except Exception as e:
                self.printException(e, "could not set right-history-list display")
            try:
                self.repo_mode_file_list.styles.display = show if right_file_w else hide
            except Exception as e:
                self.printException(e, "could not set right-file-list display")
            try:
                self.diff_list.styles.display = show if diff_w else hide
            except Exception as e:
                self.printException(e, "could not set diff-list display")
            try:
                self.help_list.styles.display = show if help_w else hide
            except Exception as e:
                self.printException(e, "could not set help-list display")
        except Exception as e:
            self.printException(e, "error applying column layout")

    def build_diff_cmd(self, filename: str, prev: str, curr: str, variant_index: int = 0) -> list[str]:
        """Return a git diff command list for the given filenames and commit-ish pair.

        This is a small helper used by `DiffList.prepDiffList` to centralize
        how diffs are constructed. It returns an argv list suitable for
        `subprocess.check_output`.
        """
        try:
            repo_root = self.repo_root
            pseudo_names = ("MODS", "STAGED")

            # Determine the optional variant argument (insert after 'diff')
            variant_arg = None
            try:
                if 0 <= variant_index < len(self.diff_variants):
                    variant_arg = self.diff_variants[variant_index]
            except Exception as e:
                self.printException(e, "determining variant_arg in build_diff_cmd")
                variant_arg = None

            # Helper to create base diff command (without refs/filename yet)
            def _base_diff(use_cached: bool = False) -> list[str]:
                base = ["git", "-C", self.repo_root, "diff"]
                # insert variant arg immediately after 'diff' when present
                if variant_arg:
                    try:
                        idx = base.index("diff")
                        base.insert(idx + 1, variant_arg)
                    except Exception as e:
                        self.printException(e, "inserting variant_arg in base diff command")
                        # best-effort: append if lookup fails
                        base.append(variant_arg)
                if use_cached:
                    base.append("--cached")
                return base

            # Build command considering pseudo-names
            if prev in pseudo_names or curr in pseudo_names:
                use_cached = prev == "STAGED" or curr == "STAGED"
                cmd = _base_diff(use_cached=use_cached)
                # If a concrete ref is provided (not pseudo) include it
                if prev and prev not in pseudo_names and curr and curr not in pseudo_names:
                    cmd += [prev, curr]
                elif curr and curr not in pseudo_names:
                    cmd.append(curr)
                elif prev and prev not in pseudo_names:
                    cmd.append(prev)
            else:
                # When only a single commit is provided (no `prev`), prefer
                # `git show <commit> -- <file>` which produces a patch against
                # /dev/null for new files (initial commit) and generally
                # represents the commit's changes. `git diff <commit> -- <file>`
                # compares the commit to the working tree and can yield empty
                # output for initial commits.
                if curr and not prev:
                    cmd = ["git", "-C", self.repo_root, "show", "--pretty=format:", curr]
                else:
                    cmd = _base_diff(use_cached=False)
                    if prev and curr:
                        cmd += [prev, curr]

            if filename:
                cmd += ["--", filename]

            logger.debug(
                f"build_diff_cmd: filename={filename} prev={prev} curr={curr} variant_index={variant_index} -> cmd={' '.join(cmd)}"
            )
            return cmd
        except Exception as e:
            self.printException(e, "build_diff_cmd failed")
            return ["git", "diff"]

    def change_layout(self, newlayout: str) -> None:
        """Change column layout using a named layout."""
        try:
            logger.debug(f"change_layout: newlayout={newlayout}")
            if newlayout == "file_fullscreen":
                self._apply_column_layout(100, 0, 0, 0, 0, 0)
            elif newlayout == "history_fullscreen":
                self._apply_column_layout(0, 100, 0, 0, 0, 0)
            elif newlayout == "file_history":
                self._apply_column_layout(25, 0, 75, 0, 0, 0)
            elif newlayout == "history_file":
                self._apply_column_layout(0, 25, 0, 75, 0, 0)
            elif newlayout == "file_history_diff":
                self._apply_column_layout(5, 0, 20, 0, 75, 0)
            elif newlayout == "history_file_diff":
                self._apply_column_layout(0, 5, 0, 20, 75, 0)
            elif newlayout == "diff_fullscreen":
                self._apply_column_layout(0, 0, 0, 0, 100, 0)
            elif newlayout == "help_fullscreen":
                self._apply_column_layout(0, 0, 0, 0, 0, 100)
            else:
                raise ValueError(f"unknown layout: {newlayout}")
            try:
                self._current_layout = newlayout
            except Exception as e:
                self.printException(e, "setting _current_layout in change_layout")
        except Exception as e:
            self.printException(e, f"change_layout {newlayout}")

    def change_state(
        self, layout: Optional[str] = None, focus: Optional[str] = None, footer: Optional[Text | str] = None
    ) -> None:
        """Change to the provided layout/focus/footer immediately.

        This applies the requested layout, focus, and footer using existing
        helpers and records the current values for save/restore semantics.
        """
        try:
            logger.debug(f"change_state(layout={layout}, focus={focus}, footer={footer}) - applying requested changes")

            if layout is not None:
                try:
                    self.change_layout(layout)
                except Exception as e:
                    self.printException(e, "change_state.change_layout failed")
            if focus is not None:
                try:
                    self.change_focus(focus)
                except Exception as e:
                    self.printException(e, "change_state.change_focus failed")
            if footer is not None:
                try:
                    self.change_footer(footer)
                except Exception as e:
                    self.printException(e, "change_state.change_footer failed")

            # change_layout/change_focus/change_footer are responsible for
            # recording their own current values; do not duplicate here.

        except Exception as e:
            self.printException(e, "change_state outer failure")

    def save_state(self) -> None:
        """Save the current single-value state (layout, focus, footer).

        This is a single-slot save; calling multiple times overwrites the slot.
        """
        try:
            self._saved_state = (
                self._current_layout,
                self._current_focus,
                self._current_footer,
            )
            logger.debug(f"save_state: saved={self._saved_state}")
        except Exception as e:
            self.printException(e, "save_state failed")

    def restore_state(self) -> None:
        """Restore the state saved by `save_state`.

        Raises RuntimeError if no saved state exists.
        """
        try:
            s = self._saved_state
            if s is None:
                raise RuntimeError("restore_state called without a prior save_state")

            layout, focus, footer = s

            logger.debug(f"restore_state: restoring layout={layout} focus={focus} footer={footer}")
            try:
                self.change_state(layout, focus, footer)
            except Exception as e:
                self.printException(e, "restore_state.change_state failed")

            # clear saved slot after restore
            try:
                self._saved_state = None
            except Exception as e:
                self.printException(e, "restore_state clearing saved state failed")
        except Exception as e:
            self.printException(e, "restore_state failed")

    def change_focus(self, target: str) -> None:
        """Change focus to the given widget id (safely).

        Records the desired focus id for save/restore semantics.
        """
        try:

            def _do():
                sel = str(target)
                # normalize selector to a bare id (without leading '#')
                if sel.startswith("#"):
                    key = sel[1:]
                else:
                    key = sel

                widget = None
                label_name = None

                # Reset title label classes
                try:
                    title_ids = [
                        LEFT_FILE_TITLE,
                        LEFT_HISTORY_TITLE,
                        RIGHT_HISTORY_TITLE,
                        RIGHT_FILE_TITLE,
                        DIFF_TITLE,
                        HELP_TITLE,
                    ]
                    for tid in title_ids:
                        try:
                            lbl = self.query_one(f"#{tid}", Label)
                            try:
                                lbl.set_class(False, "active")
                            except Exception as e:
                                self.printException(e, "change_focus resetting title label class failed")
                                try:
                                    lbl.remove_class("active")
                                except Exception as e:
                                    self.printException(e, "change_focus removing title label class failed")
                        except Exception as e:
                            self.printException(e, "change_focus querying title label failed")
                except Exception as e:
                    self.printException(e, "change_focus resetting title label classes failed")

                if key == LEFT_FILE_LIST_ID:
                    widget = self.file_mode_file_list
                    label_name = LEFT_FILE_TITLE
                elif key == LEFT_HISTORY_LIST_ID:
                    widget = self.repo_mode_history_list
                    label_name = LEFT_HISTORY_TITLE
                elif key == RIGHT_FILE_LIST_ID:
                    widget = self.repo_mode_file_list
                    label_name = RIGHT_FILE_TITLE
                elif key == RIGHT_HISTORY_LIST_ID:
                    widget = self.file_mode_history_list
                    label_name = RIGHT_HISTORY_TITLE
                elif key == DIFF_LIST_ID:
                    widget = self.diff_list
                    label_name = DIFF_TITLE
                elif key == HELP_LIST_ID:
                    widget = self.help_list
                    label_name = HELP_TITLE
                else:
                    logger.warning(
                        "change_focus:%d: unknown canonical focus target %r",
                        inspect.currentframe().f_lineno,
                        target,
                    )
                    return

                try:
                    if widget is not None:
                        # If there is an existing focused column, set its border to gray
                        try:
                            # Force all canonical candidate widgets to gray borders,
                            # then we'll set the chosen widget to white below.
                            try:
                                candidates = [
                                    ("left_file_list", self.file_mode_file_list),
                                    ("left_history_list", self.repo_mode_history_list),
                                    ("right_file_list", self.repo_mode_file_list),
                                    ("right_history_list", self.file_mode_history_list),
                                    ("diff_list", self.diff_list),
                                    ("help_list", self.help_list),
                                ]
                                for cname, w in candidates:
                                    try:
                                        if w is None:
                                            continue
                                        try:
                                            before = getattr(w.styles, "border", None)
                                        except Exception as _ex:
                                            printException(_ex)
                                            before = "<unavailable>"
                                        try:
                                            logger.debug(
                                                "change_focus:%d: forcing gray border for %s (before=%r)",
                                                inspect.currentframe().f_lineno,
                                                cname,
                                                before,
                                            )
                                        except Exception as _ex:
                                            printException(_ex)
                                        w.styles.border = ("solid", "gray")
                                        try:
                                            readback = getattr(w.styles, "border", None)
                                        except Exception as _ex:
                                            printException(_ex)
                                            readback = "<unavailable>"
                                        try:
                                            logger.debug(
                                                "change_focus:%d: forced gray border readback=%r for %s",
                                                inspect.currentframe().f_lineno,
                                                readback,
                                                cname,
                                            )
                                        except Exception as _ex:
                                            printException(_ex)
                                    except Exception as _ex:
                                        printException(_ex)
                            except Exception as _ex:
                                printException(_ex)
                        except Exception as _ex:
                            printException(_ex)

                        try:
                            try:
                                logger.debug(
                                    "change_focus:%d: calling set_focus on widget=%r key=%r",
                                    inspect.currentframe().f_lineno,
                                    type(widget).__name__ if widget is not None else None,
                                    key,
                                )
                            except Exception as _ex:
                                printException(_ex)
                            self.set_focus(widget)
                        except Exception as e:
                            self.printException(e, f"could not set focus to widget for {target}")
                            # Fallback: resolve widget by id and call set_focus
                            try:
                                try:
                                    logger.debug(
                                        "change_focus:%d: attempting widget.focus() fallback for key=%r",
                                        inspect.currentframe().f_lineno,
                                        key,
                                    )
                                except Exception as _ex:
                                    printException(_ex)
                                widget.focus()
                            except Exception as e:
                                self.printException(e, f"could not fallback focus to widget for {target}")

                        # Now set the new focused widget's border to white
                        try:
                            try:
                                # read current focused widget border safely
                                try:
                                    cur_border = getattr(widget.styles, "border", None)
                                except Exception as _ex:
                                    printException(_ex)
                                    cur_border = "<unavailable>"
                                try:
                                    logger.debug(
                                        "change_focus:%d: focused widget before set border=%r key=%r",
                                        inspect.currentframe().f_lineno,
                                        cur_border,
                                        key,
                                    )
                                except Exception as _ex:
                                    printException(_ex)
                                try:
                                    logger.debug(
                                        "change_focus:%d: setting focused widget.styles.border -> %r for key=%r",
                                        inspect.currentframe().f_lineno,
                                        ("solid", "white"),
                                        key,
                                    )
                                except Exception as _ex:
                                    printException(_ex)
                                widget.styles.border = ("solid", "white")
                                try:
                                    readback = getattr(widget.styles, "border", None)
                                except Exception as _ex:
                                    printException(_ex)
                                    readback = "<unavailable>"
                                try:
                                    logger.debug(
                                        "change_focus:%d: focused widget.styles.border readback=%r key=%r",
                                        inspect.currentframe().f_lineno,
                                        readback,
                                        key,
                                    )
                                except Exception as _ex:
                                    printException(_ex)
                            except Exception as _ex:
                                printException(_ex)
                                # Attempt to resolve by id and set border
                                try:
                                    w = None
                                    try:
                                        w = self.query_one(f"#{key}")
                                    except Exception as _ex:
                                        printException(_ex)
                                        w = None
                                    if w is not None:
                                        try:
                                            logger.debug(
                                                "change_focus:%d: setting fallback widget.styles.border -> %r for resolved id=%r",
                                                inspect.currentframe().f_lineno,
                                                ("solid", "white"),
                                                key,
                                            )
                                        except Exception as _ex:
                                            printException(_ex)
                                        w.styles.border = ("solid", "white")
                                except Exception as _ex:
                                    printException(_ex)
                        except Exception as e:
                            self.printException(e, "change_focus: setting focused widget border failed")
                        # best-effort normalize index/scroll for file lists
                        try:
                            if hasattr(widget, "index"):
                                idx = getattr(widget, "index", None)
                                if idx is None:
                                    widget.index = getattr(widget, "_min_index", 0) or 0
                        except Exception as e:
                            self.printException(e, f"could not normalize index/scroll for widget {target}")

                except Exception as e:
                    self.printException(e, f"could not focus resolved widget for {target}")

                # Update title label
                try:
                    if label_name:
                        try:
                            title_lbl = self.query_one(f"#{label_name}", Label)
                            try:
                                title_lbl.set_class(True, "active")
                            except Exception as e:
                                self.printException(e, "change_focus setting title label class failed")
                                try:
                                    title_lbl.add_class("active")
                                except Exception as e:
                                    self.printException(e, "change_focus adding title label class failed")
                        except Exception as e:
                            self.printException(e, f"could not update title label {label_name}")
                except Exception as e:
                    self.printException(e, "change_focus: updating title label failed")

            try:
                self.call_after_refresh(_do)
            except Exception as e:
                self.printException(e, "change_focus.call_after_refresh failed")
                _do()

            # record desired focus target for save/restore
            try:
                sel = str(target)
                if sel.startswith("#"):
                    key = sel
                else:
                    key = f"#{sel}"
                self._current_focus = key
            except Exception as e:
                self.printException(e, "change_focus recording _current_focus failed")
        except Exception as e:
            self.printException(e, "change_focus outer failure")

    def change_footer(self, value: Text | str) -> None:
        """Set the footer to `value` (Text or str) immediately and record it."""
        try:
            txt = value if isinstance(value, Text) else Text(str(value))
            try:
                footer = None
                try:
                    footer = self.query_one("#footer", Label)
                except Exception as e:
                    self.printException(e, "change_footer querying footer label failed")
                    footer = None
                if footer is not None:
                    try:
                        footer.update(txt)
                    except Exception as e:
                        self.printException(e, "change_footer updating footer label failed")
            except Exception as e:
                self.printException(e, "could not update footer in change_footer")
            try:
                self._current_footer = txt
            except Exception as e:
                self.printException(e, "change_footer recording _current_footer failed")
        except Exception as e:
            self.printException(e, "change_footer outer failure")

    # Layout toggle helpers -------------------------------------------------
    def toggle(self, layout: str, event: events.Key | None = None) -> None:
        """Dispatch to a per-layout toggle_* handler for `layout`.

        If the layout is `help_fullscreen` this is a no-op. Otherwise stop
        the event (if provided) and call the corresponding `toggle_<layout>`
        method if it exists.
        """
        try:
            if layout == "help_fullscreen":
                return

            # Log app and focused-widget state to help debug path/hash swapping
            try:
                logger.trace(
                    "toggle_%s invoked: _current_layout=%s _current_focus=%s",
                    layout,
                    self._current_layout,
                    self._current_focus,
                )
                logger.trace(
                    "app state: path=<%r> current_path=<%r> current_hash=<%r> previous_hash=<%r>",
                    self.path,
                    self.current_path,
                    self.current_hash,
                    self.previous_hash,
                )

                focused_info = None
                try:
                    fsel = self._current_focus
                    if fsel:
                        fid = fsel[1:] if str(fsel).startswith("#") else str(fsel)
                        try:
                            widget = self.query_one(f"#{fid}")
                        except Exception as e:
                            self.printException(e, f"toggle: querying focused widget #{fid} failed")
                            widget = None
                        if widget is not None:
                            wtype = type(widget).__name__
                            wpath = getattr(widget, "path", None)
                            # If history list, compute selected commit pair
                            pair = None
                            try:
                                if isinstance(widget, HistoryListBase):
                                    pair = widget.compute_commit_pair_hashes()
                            except Exception as e:
                                self.printException(e, "toggle: computing commit pair hashes failed")
                                pair = None
                            # If file list, try to get selected node raw text
                            selected_raw = None
                            try:
                                nodes = widget.nodes()
                                idx = widget.index or getattr(widget, "_min_index", 0) or 0
                                if 0 <= idx < len(nodes):
                                    selected_raw = getattr(nodes[idx], "_raw_text", None)
                            except Exception as e:
                                self.printException(e, "toggle: getting selected node raw text failed")
                                selected_raw = None
                            focused_info = (wtype, wpath, pair, selected_raw)
                except Exception as e:
                    self.printException(e, "toggle: getting focused widget info failed")
                    focused_info = None
                logger.debug("focused widget info: %r", focused_info)
            except Exception as e:
                self.printException(e, "toggle outer failure")

            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "toggle: event.stop failed")
            handler = getattr(self, f"toggle_{layout}", None)
            if callable(handler):
                try:
                    handler()
                except Exception as e:
                    self.printException(e, f"toggle_{layout} failed")
            else:
                logger.debug("toggle: no handler for layout %s", layout)
        except Exception as e:
            self.printException(e, "toggle outer failure")

    def key_s(self, event: events.Key | None = None) -> None:
        logger.debug("GitHistoryNavTool.key_s called: key=%r", getattr(event, "key", None))
        return self.toggle(self._current_layout, event)

    def key_S(self, event: events.Key | None = None) -> None:
        logger.debug("GitHistoryNavTool.key_S called: key=%r", getattr(event, "key", None))
        return self.key_s(event, recursive=True)

    # Per-layout toggle implementations. These prepare lists and switch
    # layouts in pairs so the `s` key toggles between related views.
    def toggle_file_fullscreen(self) -> None:
        # When toggling from file_fullscreen, populate the repo history
        # so the paired history_fullscreen view is ready.
        try:
            self.repo_mode_history_list.prepRepoModeHistoryList(repo_path=self.path or ".")
        except Exception as e:
            self.printException(e, "toggle_file_fullscreen prepRepoModeHistoryList failed")
        try:
            self.change_state("history_fullscreen", f"#{LEFT_HISTORY_LIST_ID}", LEFT_HISTORY_FOOTER)
        except Exception as e:
            self.printException(e, "toggle_file_fullscreen change_state failed")

    def toggle_history_fullscreen(self) -> None:
        # When toggling from history_fullscreen, prepare the left file list
        # and highlight the current filename when available.
        try:
            hl = os.path.basename(self.path) if self.path else None
            self.file_mode_file_list.prepFileModeFileList(self.path or ".", hl)
        except Exception as e:
            self.printException(e, "toggle_history_fullscreen prepFileModeFileList failed")
        try:
            self.change_state("file_fullscreen", f"#{LEFT_FILE_LIST_ID}", LEFT_FILE_FOOTER)
        except Exception as e:
            self.printException(e, "toggle_history_fullscreen change_state failed")

    def toggle_file_history(self) -> None:
        # Save transient values
        saved_path = self.current_path
        try:
            logger.debug(
                    "toggle_file_history: before prepRepoModeHistoryList app.previous_hash=%r app.current_hash=%r saved_path=%r",
                    self.previous_hash,
                    self.current_hash,
                    saved_path,
                )
            # Prepare repo history and request that preparer highlight and
            # mark the provided commit hashes when present.
            # Use the current app-level hashes as the initial request; the
            # preparer will update app-level state to reflect the highlighted
            # selection and we will read back the authoritative values.
            self.repo_mode_history_list.prepRepoModeHistoryList(
                repo_path=self.path or ".", prev_hash=self.previous_hash, curr_hash=self.current_hash
            )
        except Exception as e:
            self.printException(e, "toggle_file_history preparing repo history failed")
        try:
            logger.debug(
                "toggle_file_history: after prepRepoModeHistoryList app.previous_hash=%r app.current_hash=%r",
                self.previous_hash,
                self.current_hash,
            )
            # After the history preparer runs it will have updated
            # `app.current_hash`/`app.previous_hash` to match the highlighted
            # selection. Read those authoritative values and pass them to the
            # file preparer so it lists the correct commit-pair.
            use_prev = self.previous_hash
            use_curr = self.current_hash
            # Compute a repo-relative highlight filename so it matches the
            # `_raw_text` values attached to repo-mode file list rows (these
            # are repository-relative paths like 'docs/notes.txt'). Prefer a
            # normalized relative path when `saved_path` is inside the repo.

            # Pass the canonical full path through as the highlight so
            # matching is performed against absolute paths.
            hl = saved_path
            logger.debug("toggle_file_history: passing fullpath highlight=%r", hl)
            self.repo_mode_file_list.prepRepoModeFileList(use_prev, use_curr, highlight_filename=hl)
        except Exception as e:
            self.printException(e, "toggle_file_history preparing repo file list failed")
        try:
            self.change_state("history_file", f"#{RIGHT_FILE_LIST_ID}", RIGHT_FILE_FOOTER)
        except Exception as e:
            self.printException(e, "toggle_file_history change_state failed")

    def toggle_history_file(self) -> None:
        # Save transient values
        saved_path = self.current_path
        saved_curr = self.current_hash
        saved_prev = self.previous_hash
        logger.debug(
            "toggle_history_file: before prepFileModeFileList app.previous_hash=%r app.current_hash=%r saved_path=%r",
            self.previous_hash,
            self.current_hash,
            saved_path,
        )
        try:
            # Prepare the right file list (file pane on right) showing files
            # Use the full path as the highlight so matching is
            # performed against canonical full paths instead of basenames.
            hl = saved_path
            logger.debug("toggle_history_file: saved_path=%r computed_highlight=%r", saved_path, hl)
            self.file_mode_file_list.prepFileModeFileList(saved_path or ".", hl)
        except Exception as e:
            self.printException(e, "toggle_history_file prepFileModeFileList failed")
        try:
            # Prepare the right history list for the current file and request
            # the preparer highlight/mark the provided commit hashes.
            self.file_mode_history_list.prepFileModeHistoryList(
                saved_path or ".", prev_hash=saved_prev, curr_hash=saved_curr
            )
        except Exception as e:
            self.printException(e, "toggle_history_file prepping file history failed")
        try:
            self.change_state("file_history", f"#{RIGHT_HISTORY_LIST_ID}", RIGHT_HISTORY_FOOTER)
        except Exception as e:
            self.printException(e, "toggle_history_file change_state failed")

    def toggle_file_history_diff(self) -> None:
        try:
            self.toggle_file_history()
        except Exception as e:
            self.printException(e, "toggle_file_history_diff: toggle_file_history failed")
        try:
            # show diff in the right diff column and set go_back
            self.change_state("history_file_diff", f"#{DIFF_LIST_ID}", HISTORY_FILE_DIFF_FOOTER)
            try:
                self.diff_list.go_back = ("history_file", RIGHT_FILE_LIST_ID, RIGHT_FILE_FOOTER)
            except Exception as e:
                self.printException(e, "toggle_file_history_diff setting diff_list.go_back failed")
        except Exception as e:
            self.printException(e, "toggle_file_history_diff change_state failed")

    def toggle_history_file_diff(self) -> None:
        try:
            self.toggle_history_file()
        except Exception as e:
            self.printException(e, "toggle_history_file_diff: toggle_history_file failed")
        try:
            self.change_state("file_history_diff", f"#{DIFF_LIST_ID}", HISTORY_FILE_DIFF_FOOTER)
            try:
                self.diff_list.go_back = ("file_history", RIGHT_HISTORY_LIST_ID, RIGHT_HISTORY_FOOTER)
            except Exception as e:
                self.printException(e, "toggle_history_file_diff setting diff_list.go_back failed")
        except Exception as e:
            self.printException(e, "toggle_history_file_diff change_state failed")

    def toggle_diff_fullscreen(self) -> None:
        # If the diff has a saved layout, toggle back to it via recursive dispatch
        try:
            saved = self.diff_list._saved_layout
            if saved:
                try:
                    self.toggle(saved)
                except Exception as e:
                    self.printException(e, "toggle_diff_fullscreen dispatch failed")
        except Exception as e:
            self.printException(e, "toggle_diff_fullscreen retrieving saved layout failed")


def discover_repo_worktree(start_path: str | None) -> str:
    """Discover the repository worktree root starting at `start_path`.

    Uses `pygit2.discover_repository` when available; otherwise uses
    git -C <start_path> rev-parse --show-toplevel to find the worktree root.
    If no repository is found this function exits the program with an error message.
    """
    try:
        start = os.path.abspath(start_path or os.getcwd())
    except Exception as _ex:
        printException(_ex)
        start = os.getcwd()

    # Try pygit2 discovery first
    if pygit2:
        try:
            gitdir = pygit2.discover_repository(start)
            logger.debug("discover_repo_worktree: pygit2 discovered gitdir=%s", gitdir)
            if gitdir:
                try:
                    gitdir = os.fspath(gitdir)
                    logger.debug("discover_repo_worktree: pygit2 gitdir fspath=%s", gitdir)
                except Exception as e:
                    printException(e, "discover_repo_worktree: converting gitdir to fspath failed")
                gitdir_real = os.path.realpath(gitdir)
                logger.debug(f"discover_repo_worktree: pygit2 gitdir realpath={gitdir_real}")
                # Worktree root is parent of the .git directory
                worktree = os.path.realpath(os.path.dirname(gitdir_real))
                logger.debug("discover_repo_worktree: pygit2 discovered gitdir=%s worktree=%s", gitdir_real, worktree)
                return worktree
        except Exception as e:
            printException(e, "discover_repo_worktree: pygit2 discovery failed, falling back to git CLI")

    # Next try git CLI discovery using `git -C <start> rev-parse --show-toplevel`.
    try:
        cmd = ["git", "-C", start or ".", "rev-parse", "--show-toplevel"]
        proc = run_cmd_log(cmd, label="discover_repo_worktree rev-parse")
        topo = (proc.stdout or "").strip() if proc.returncode == 0 else ""
        if topo:
            try:
                worktree = os.path.realpath(topo)
            except Exception as _ex:
                printException(_ex)
                logger.debug("discover_repo_worktree: realpath failed for topo=%s", topo)
                worktree = topo
            logger.debug("discover_repo_worktree: git rev-parse -> %s (worktree=%s)", topo, worktree)
            return worktree
    except Exception as e:
        printException(e, "discover_repo_worktree: git rev-parse failed, falling back to directory walk")

    # If pygit2 and git discovery both fail, fail fast — no filesystem walk.
    sys.exit(f"Not a git repository (pygit2 and git discovery failed) starting at {start}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="gitdiffnavtool.py")
    parser.add_argument("path", nargs="?", default=".", help="directory or file to open")
    parser.add_argument(
        "-C", "--no-color", dest="no_color", action="store_true", help="start with diff colorization off"
    )
    parser.add_argument("-r", "--repo-first", dest="repo_first", action="store_true", help="start in repo-first mode")
    parser.add_argument(
        "-d", "--debug", dest="debug", metavar="FILE", help="write debug log to FILE (enables debug logging)"
    )
    parser.add_argument(
        "-D",
        "--debug-tracing",
        dest="debug_tracing",
        action="store_true",
        help="enable TRACE-level (very verbose) logging",
    )
    parser.add_argument(
        "-P", "--no-pygit2", dest="no_pygit2", action="store_true", help="disable pygit2 usage even if installed"
    )
    parser.add_argument(
        "-R",
        "--repo-hash",
        dest="repo_hash",
        action="append",
        metavar="HASH",
        help="specify a repo commit hash; may be provided up to two times (implies --repo-first)",
    )
    args = parser.parse_args(argv)

    if args.no_pygit2:
        global pygit2  # pylint: disable=global-statement
        pygit2 = None

    # Configure logging if debug file requested
    try:
        if args.debug:
            try:
                os.makedirs(os.path.dirname(args.debug) or "", exist_ok=True)
            except Exception as e:
                printException(e, "could not create directories for debug log file")
            logging.basicConfig(
                filename=args.debug,
                level=logging.DEBUG,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            )
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug logging enabled -> %s", args.debug)

        # Enable TRACE-level logging if requested (applies to root and handlers)
        enable_trace_logging(bool(args.debug_tracing))

        # If repo-hash provided, validate count and imply repo-first
        repo_hashes = None
        if args.repo_hash:
            repo_hashes = args.repo_hash
            if len(repo_hashes) > 2:
                printException(ValueError("--repo-hash may be specified at most twice"), "argument error")
                return 2
            # imply repo-first when -R used
            args.repo_first = True

        # Determine repository worktree root once and store on the app.
        repo_root = discover_repo_worktree(args.path)
        logger.debug("Discovered repository worktree root: %s", repo_root)

        # Wire CLI into the Textual app and run it.
        logger.debug("Starting GitHistoryNavTool; args=%s repo_root=%s", args, repo_root)
        app = GitHistoryNavTool(
            path=args.path,
            no_color=args.no_color,
            repo_first=args.repo_first,
            repo_hashes=repo_hashes,
            repo_root=repo_root,
        )
        # Run the textual app (blocks until exit)
        app.run()
        return 0
    except Exception as e:
        printException(e, "fatal error running GitHistoryNavTool")
        return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        printException(e, "fatal error in gitdiffnavtool")
        raise
