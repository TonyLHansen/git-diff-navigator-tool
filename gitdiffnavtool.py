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
from datetime import datetime, timezone, timedelta
from functools import wraps
import pprint
import difflib
import re
import hashlib
import codecs
import time
from subprocess import check_output, CalledProcessError

# Third-party UI and rendering imports
from rich.text import Text
from rich.markdown import Markdown
from textual import events
from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.widgets import ListView, Label, ListItem, Footer, Header
from textual.screen import ModalScreen

# Repository helpers (extracted): provide printException, AppException, GitRepo
from gitrepo import printException, AppException, GitRepo

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

# Number of characters to display for short hashes
HASH_LENGTH = 12


# --- Logging setup --------------------------------------------------------
# NOTE: logging is configured in `main()` when `--debug` is passed.

logger = logging.getLogger(__name__)

# Define a TRACE level lower than DEBUG and add a convenience `trace` method
# so callers can emit very-verbose trace messages when enabled.
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _logger_trace(self, msg, *args, **kwargs):
    """Logger method implementing TRACE-level logging.

    Attached to `logging.Logger` as `trace`; emits the message at the
    numeric TRACE level when enabled.
    """
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


class AppBase(AppException, ListView):
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

    def _run_git_lines(self, cmd: list[str], label: str | None = None) -> list[str]:
        """Run a git command and return non-empty output lines.

        Uses `_run_cmd_log` for consistent logging; returns an empty list
        on error and logs the exception via `printException`.
        """
        try:
            proc = self._run_cmd_log(cmd, label=label)
            out = proc.stdout or ""
            return [ln for ln in out.splitlines() if ln.strip()]
        except Exception as e:
            self.printException(e, f"_run_git_lines: {label or 'git'}")
            return []

    def _canonical_relpath(self, path: str, repo_root: str) -> str:
        """Return a canonical realpath for `path` using `repo_root` for
        repository-relative paths.

        Return a canonical repository-relative path.
        If `path` is absolute, convert it to a repo-relative path using
        `repo_root`. On error return the original `path`.
        """
        try:
            # Always return a repo-relative normalized path so callers
            # can compare canonical values without dealing with system
            # absolute paths.
            if not path:
                return ""
            # If `path` appears to be absolute (leading slash) convert to
            # a repo-relative normalized path; otherwise normalize as-is.
            if path and path.startswith(os.sep):
                try:
                    return os.path.relpath(os.path.normpath(path), repo_root)
                except Exception as e:
                    self.printException(e, "_canonical_relpath: realpath->relpath failed")
                    return os.path.normpath(path)
            # Normalize relative paths (collapse ./ and ../)
            return os.path.normpath(path)
        except Exception as e:
            self.printException(e, "_canonical_relpath failed")
            return path

    def _format_pseudo_summary(self, pseudo_entries: list[tuple[str, str]]) -> None:
        """Append pseudo-summary rows (e.g. MODS/STAGED/UNTRACKED) to this list.

        Centralized helper used by file- and repo-mode preparers to ensure
        consistent display formatting and metadata attachment.
        """
        try:
            for status, path in pseudo_entries:
                try:
                    display = f"{status} {path}"
                    item = ListItem(Label(Text(display)))
                    try:
                        repo_root_local = self.app.gitRepo.get_repo_root()
                        full = self._canonical_relpath(path, repo_root_local)
                        item._raw_text = full
                    except Exception as e:
                        self.printException(e, "_format_pseudo_summary: resolving full path failed")
                        item._raw_text = path
                    item._is_dir = False
                    self.append(item)
                except Exception as e:
                    self.printException(e, "_format_pseudo_summary append pseudo entry failed")
        except Exception as e:
            self.printException(e, "_format_pseudo_summary failed")

    def _append_file_row(self, display: str, full_path: str, is_dir: bool = False, status: str | None = None) -> None:
        """Append a file-list row with consistent marker and status styling.

        This centralizes file-row display so repo- and file-mode preparers
        use identical formatting. The left-most marker is chosen from
        `MARKERS` based on `status`. A style is applied using the project's
        `STYLE_*` constants. Metadata attached to the ListItem:
        - `_raw_text`: canonical full path
        - `_filename`: basename
        - `_is_dir`: bool
        - `_repo_status`: optional status string

        Exceptions are logged via `printException` so callers needn't
        handle failures.
        """
        try:
            try:
                repo_root_local = self.app.gitRepo.get_repo_root()
                canonical = self._canonical_relpath(full_path, repo_root_local) if full_path else full_path
            except Exception as _ex:
                self.printException(_ex, "_append_file_row: canonicalizing path failed")
                canonical = full_path

            # Determine marker and style from status
            try:
                marker = MARKERS.get(status, MARKERS.get("tracked_clean", " "))
            except Exception as _ex:
                self.printException(_ex, "_append_file_row: computing marker failed")
                marker = " "

            try:
                if status == "conflicted":
                    style = STYLE_CONFLICTED
                elif status == "staged":
                    style = STYLE_STAGED
                elif status == "wt_deleted":
                    style = STYLE_WT_DELETED
                elif status == "ignored":
                    style = STYLE_IGNORED
                elif status == "modified":
                    style = STYLE_MODIFIED
                elif status == "untracked":
                    style = STYLE_UNTRACKED
                else:
                    style = STYLE_DEFAULT
            except Exception as _ex:
                self.printException(_ex, "_append_file_row: selecting style failed")
                style = STYLE_DEFAULT

            # Compose display with left marker
            try:
                display_text = f"{marker} {display}" if marker else display
            except Exception as _ex:
                self.printException(_ex, "_append_file_row: composing display failed")
                display_text = display

            try:
                lbl = Label(Text(display_text, style=style))
                item = ListItem(lbl)
            except Exception as _ex:
                self.printException(_ex, "_append_file_row: building ListItem failed")
                item = ListItem(Label(display))

            try:
                item._raw_text = canonical
            except Exception as _ex:
                self.printException(_ex, "_append_file_row: setting _raw_text failed")
                try:
                    repo_root_local = self.app.gitRepo.get_repo_root()
                    item._raw_text = os.path.relpath(full_path, repo_root_local) if full_path else display
                except Exception as e:
                    self.printException(e, "_append_file_row: relpath fallback failed")
                    item._raw_text = display

            try:
                item._filename = (
                    os.path.basename(canonical)
                    if canonical
                    else (os.path.basename(full_path) if full_path else display)
                )
            except Exception as _ex:
                self.printException(_ex, "_append_file_row: setting _filename failed")
                item._filename = display

            try:
                item._is_dir = bool(is_dir)
            except Exception as _ex:
                self.printException(_ex, "_append_file_row: setting _is_dir failed")
                item._is_dir = False

            try:
                if status is not None:
                    item._repo_status = status
            except Exception as _ex:
                self.printException(_ex, "_append_file_row: setting _repo_status failed")

            try:
                self.append(item)
            except Exception as _ex:
                self.printException(_ex, "_append_file_row: append failed")
        except Exception as e:
            self.printException(e, "_append_file_row failed")

    def _parse_git_log_lines(self, lines: list[str]) -> list[tuple[datetime, str, str]]:
        """Parse lines produced by `git log --pretty=format:%H\t%aI\t%s`.

        Returns a list of tuples (datetime, hash, subject). On parse errors
        datetimes default to `datetime.min` so sorting remains robust.
        """
        out: list[tuple[datetime, str, str]] = []
        try:
            for ln in lines:
                try:
                    parts = ln.split("\t", 2)
                    h = parts[0] if parts else ""
                    date_s = parts[1] if len(parts) > 1 else ""
                    msg = parts[2] if len(parts) > 2 else ""
                    try:
                        dt = datetime.fromisoformat(date_s) if date_s else datetime.min
                    except Exception as _ex:
                        self.printException(_ex, f"_parse_git_log_lines failed ISO parse for '{date_s}'")
                        try:
                            dt = datetime.strptime(date_s, "%Y-%m-%d") if date_s else datetime.min
                        except Exception as _ex2:
                            self.printException(_ex2, f"_parse_git_log_lines failed parsing date '{date_s}'")
                            dt = datetime.min
                    out.append((dt, h, msg))
                except Exception as e:
                    self.printException(e, "_parse_git_log_lines line parse failed")
        except Exception as e:
            self.printException(e, "_parse_git_log_lines failed")
        return out

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

    def _date_key(self, t: tuple[str, str, str]):
        """Convert a (hash, date, msg) tuple's ISO date to a datetime for sorting.

        Returns `datetime.min` when the date is missing or unparsable so
        sorting remains robust.
        """
        try:
            ds = t[1] if len(t) > 1 else ""
            if ds:
                try:
                    # Prefer full ISO datetime parsing when available
                    dt_obj = datetime.fromisoformat(ds)
                except Exception as e:
                    self.printException(e, f"parsing ISO datetime '{ds}' failed, trying date-only")
                    try:
                        dt_obj = datetime.strptime(ds, "%Y-%m-%d")
                    except Exception as e2:
                        self.printException(e2, f"parsing date-only '{ds}' failed, using datetime.min")
                        dt_obj = datetime.min
            else:
                dt_obj = datetime.min
            # Return (datetime, hash) so sorting is deterministic; datetime
            # may include time when provided in ISO format.
            return (dt_obj, t[0] if len(t) > 0 else "")
        except Exception as _ex:
            self.printException(_ex, f"_date_key failed for tuple: {t}")
            return (datetime.min, "")

    def _compute_pseudo_timestamps(self, repo_root: str, mods: list[str], single_path: str) -> tuple[str, str]:
        """Compute timestamps for pseudo-summary rows.

        Returns (mods_ts, staged_ts) where each is an ISO-like timestamp
        string (no leading space) or empty string when unavailable.
        - When `mods` is provided, compute the latest mtime among those files.
        - When `single_path` is provided, compute the mtime for that file.
        `staged_ts` is computed from `.git/index` mtime when available.
        """
        mods_ts = ""
        try:
            if mods:
                latest_m = None
                for p in mods:
                    try:
                        full = os.path.join(repo_root, p)
                        if os.path.exists(full):
                            m = os.path.getmtime(full)
                            if latest_m is None or m > latest_m:
                                latest_m = m
                    except Exception as _ex:
                        self.printException(_ex, "_compute_pseudo_timestamps skipping file mtime due to error")
                        continue
                if latest_m is not None:
                    mods_ts = datetime.fromtimestamp(latest_m).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
            elif single_path:
                try:
                    if os.path.exists(single_path):
                        m = os.path.getmtime(single_path)
                        mods_ts = datetime.fromtimestamp(m).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
                except Exception as _ex:
                    self.printException(_ex, "_compute_pseudo_timestamps computing single_path mtime failed")
        except Exception as e:
            self.printException(e, "_compute_pseudo_timestamps failed computing mods_ts")

        staged_ts = ""
        try:
            idx_path = os.path.join(repo_root, ".git", "index")
            if os.path.exists(idx_path):
                m = os.path.getmtime(idx_path)
                staged_ts = datetime.fromtimestamp(m).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        except Exception as _ex:
            self.printException(_ex, "_compute_pseudo_timestamps computing staged timestamp failed")

        return (mods_ts, staged_ts)

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
                self.call_after_refresh(lambda: self._safe_set_index(new_index))
            except Exception as e:
                self.printException(e, "_activate_index: scheduling index set failed")
                logger.debug("_activate_index: falling back to direct index set -> %s", new_index)
                self.index = new_index
        except Exception as e:
            self.printException(e, "_activate_index failed")

    def watch_index(self, old: int | None, new: int | None) -> None:
        """Handle an index change: perform common highlight/scroll and
        dispatch to widget-specific hooks (`watch_filelist_index` or
        `watch_history_index`).
        """
        # Delegate to a helper that performs common highlight/scroll behavior
        try:
            node_new = self.watch_index_helper(old, new)
            # Dispatch to widget-specific hooks so subclass behavior is
            # implemented in `watch_filelist_index` / `watch_history_index`
            try:
                if self.is_history_list:
                    try:
                        self.watch_history_index(old, new, node_new)
                    except Exception as e:
                        self.printException(e, "watch_index: watch_history_index hook failed")
                elif self.is_file_list:
                    try:
                        self.watch_filelist_index(old, new, node_new)
                    except Exception as e:
                        self.printException(e, "watch_index: watch_filelist_index hook failed")
                else:
                    logger.debug("watch_index: no specific hook for widget %s", type(self).__name__)
            except Exception as e:
                self.printException(e, "watch_index: dispatch to hooks failed")
        except Exception as e:
            self.printException(e, "watch_index failed")

    def watch_index_helper(self, old: int | None, new: int | None):
        """Common highlight and scroll behavior extracted from previous watch_index.

        Returns the `node_new` (or None) so callers/hooks may inspect it.
        """
        try:
            nodes = self.nodes()
            if not nodes:
                return None
            highlight_bg = self.highlight_bg_style
            text_color = "white"
            node_old = None
            if old is not None and 0 <= old < len(nodes):
                try:
                    node_old = nodes[old]
                except Exception as e:
                    self.printException(e, "watch_index_helper: getting old node failed")
                    node_old = None

            logger.debug("watch_index_helper: old=%r new=%r nodes=%d", old, new, len(nodes))
            logger.debug(
                "watch_index_helper: preserved marked style for old index %s hash=%r",
                old,
                getattr(node_old, "_hash", None),
            )

            # Deactivate old
            if node_old is not None:
                try:
                    node_old.remove_class("active")
                except Exception as e:
                    self.printException(e, "watch_index_helper: remove_class failed")
                try:
                    if getattr(node_old, "_checked", False):
                        node_old.styles.background = "red"
                        node_old.styles.color = "white"
                        node_old.styles.text_style = "bold"
                    else:
                        node_old.styles.background = None
                        node_old.styles.color = None
                        node_old.styles.text_style = None
                        logger.debug("watch_index_helper: cleared styles for old index %s", old)
                except Exception as e:
                    self.printException(e, "watch_index_helper: clearing old styles failed")

            node_new = None
            if new is not None and 0 <= new < len(nodes):
                try:
                    node_new = nodes[new]
                    try:
                        node_new.add_class("active")
                    except Exception as e:
                        self.printException(e, "watch_index_helper: add_class failed")
                    try:
                        node_new.styles.background = highlight_bg
                        node_new.styles.color = text_color
                        node_new.styles.text_style = "bold"
                        logger.debug(
                            "watch_index_helper: applied highlight to new index %s text=%s",
                            new,
                            self.text_of(node_new),
                        )
                    except Exception as e:
                        self.printException(e, "watch_index_helper: applying new highlight failed")

                    try:
                        animate = False
                        try:
                            animate = bool(self._page_scroll)
                        except Exception as e:
                            self.printException(e, "watch_index_helper: reading _page_scroll failed")
                            animate = False
                        logger.debug("watch_index_helper: scroll animate=%s for index %s", animate, new)
                        if hasattr(self, "scroll_to_widget"):
                            try:
                                self.call_after_refresh(lambda: self._safe_scroll_to_widget(node_new, animate=animate))
                            except Exception as e:
                                self.printException(e, "watch_index_helper: scroll_to_widget(animate=) failed")
                                try:
                                    self.call_after_refresh(lambda: self._safe_scroll_to_widget(node_new))
                                except Exception as e2:
                                    self.printException(
                                        e2, "watch_index_helper: scroll_to_widget(node_new) fallback failed"
                                    )
                        else:
                            try:
                                logger.debug("watch_index_helper: scheduling node_new.scroll_visible for index %s", new)
                                self.call_after_refresh(lambda: self._safe_node_scroll_visible(node_new, True))
                            except Exception as e:
                                self.printException(e, "watch_index_helper: node_new.scroll_visible failed")
                        self._page_scroll = False
                    except Exception as e:
                        self.printException(e, "watch_index_helper: scrolling new node failed")
                except Exception as e:
                    self.printException(e, "watch_index_helper: finding new node failed")

            return node_new
        except Exception as e:
            self.printException(e, "watch_index_helper failed")
            return None

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
                    repo_root_local = self.app.gitRepo.get_repo_root()
                    match_full = self._canonical_relpath(match, repo_root_local)
                except Exception as e:
                    match_full = match
                    self.printException(e, "_highlight_match: normalizing match failed")

                for i, node in enumerate(nodes):
                    try:
                        raw = getattr(node, "_raw_text", None)
                        h = getattr(node, "_hash", None)
                        try:
                            if raw is not None:
                                repo_root_local = self.app.gitRepo.get_repo_root()
                                node_full = self._canonical_relpath(raw, repo_root_local)
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
                self.call_after_refresh(lambda: self._safe_activate_index(top))
            except Exception as e:
                self.printException(e, "_highlight_top: scheduling index set failed")
                # Fall back to direct activation if scheduling fails
                self._activate_index(top)
        except Exception as e:
            self.printException(e, "AppBase._highlight_top failed")

    # Consolidated safe-call helpers used for scheduling post-refresh actions.
    # These centralize try/except logic so lambdas passed to
    # `call_after_refresh` remain small and identical behavior isn't
    # duplicated across the codebase.
    def _safe_set_index(self, new_index: int) -> None:
        """Safely set the widget `index` attribute.

        Wraps the assignment in a try/except and forwards exceptions to
        `printException` so callers can schedule this to run after UI
        refresh without raising.
        """
        try:
            setattr(self, "index", new_index)
        except Exception as e:
            self.printException(e, "_safe_set_index failed")

    def _safe_activate_index(self, idx: int) -> None:
        """Invoke `_activate_index` and handle any exceptions.

        Intended to be called from lambdas passed to `call_after_refresh`.
        """
        try:
            self._activate_index(idx)
        except Exception as e:
            self.printException(e, "_safe_activate_index failed")

    def _safe_scroll_to_widget(self, node, animate: bool = False) -> None:
        """Scroll the given `node` into view (safe wrapper).

        Uses the framework `scroll_to_widget` API when available and logs
        exceptions instead of raising so UI callbacks remain stable.
        """
        try:
            # Prefer the framework-provided scroll, if present.
            self.scroll_to_widget(node, animate=animate)
        except Exception as e:
            self.printException(e, "_safe_scroll_to_widget failed")

    def _safe_node_scroll_visible(self, node, visible: bool = True) -> None:
        """Call a node's `scroll_visible` method safely.

        This is a non-fatal fallback used when the widget-level
        `scroll_to_widget` API is not available.
        """
        try:
            getattr(node, "scroll_visible", lambda *a, **k: None)(visible)
        except Exception as e:
            self.printException(e, "_safe_node_scroll_visible failed")

    def _safe_highlight_match(self, match: Optional[str]) -> None:
        """Safe wrapper around `_highlight_match` that logs failures.

        Useful for scheduling highlight operations after UI refresh.
        """
        try:
            self._highlight_match(match)
        except Exception as e:
            self.printException(e, "_safe_highlight_match failed")

    def _finalize_prep_common(
        self, curr_hash: str | None = None, prev_hash: str | None = None, path: str | None = None
    ) -> None:
        """Shared app-level sync used by all preparers.

        This function performs the conservative updates to the application
        state (`app.current_hash`, `app.previous_hash`, `app.rel_dir`/`app.rel_file`)
        and invokes `_compute_selected_pair` when appropriate. It does not
        perform widget-specific highlighting or marking.
        """
        try:
            if curr_hash is not None or prev_hash is not None:
                try:
                    self.app.current_hash = curr_hash
                    self.app.previous_hash = prev_hash
                except Exception as _ex:
                    self.printException(_ex, "_finalize_prep_common: updating app hashes failed")
                return

            # If no explicit hashes provided, attempt to compute the selected pair.
            if hasattr(self, "_compute_selected_pair"):
                try:
                    self._compute_selected_pair()
                except Exception as _ex:
                    self.printException(_ex, "_finalize_prep_common: _compute_selected_pair failed")

            # Normalize and store repo-relative path components when provided.
            if path is not None:
                try:
                    rel = os.path.normpath(path)
                    rd, rf = os.path.split(rel)
                    self.app.rel_dir = rd or ""
                    self.app.rel_file = rf or ""
                except Exception as _ex:
                    self.printException(_ex, "_finalize_prep_common: setting app.rel_dir/rel_file failed")

        except Exception as e:
            self.printException(e, "_finalize_prep_common: app state sync failed")

    # Key handlers: prefer `key_` methods on widgets instead of an `on_key` dispatcher.
    # Implement navigation handlers as `key_*` methods so subclasses may override
    # them individually and keep key logic co-located with widget state.

    def key_up(self, event: events.Key | None = None) -> None:
        """Move the selection up by one item, honoring `event.stop()` if provided."""
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
        """Move the selection down by one item, honoring `event.stop()` if provided."""
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
        """Scroll forward by approximately one page and activate the new index.

        When `recursive` is true this is an alias invocation and logging is
        suppressed to avoid duplicate messages.
        """
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
        """Alias for `key_page_up`; preserves event semantics and logging."""
        logger.debug("AppBase.key_pageup called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_up(event, recursive=True)

    def key_pagedown(self, event: events.Key | None = None) -> None:
        """Alias for `key_page_down`; preserves event semantics and logging."""
        logger.debug("AppBase.key_pagedown called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_down(event, recursive=True)

    def key_page_up(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Scroll backward by approximately one page and activate the new index.

        When `recursive` is true this is an alias invocation and logging is
        suppressed to avoid duplicate messages.
        """
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
        """Alias mapping for terminals that report PageUp as 'prior'."""
        logger.debug("AppBase.key_prior called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_up(event, recursive=True)

    def key_next(self, event: events.Key | None = None) -> None:
        """Alias mapping for terminals that report PageDown as 'next'."""
        logger.debug("AppBase.key_next called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_down(event, recursive=True)

    def key_home(self, event: events.Key | None = None) -> None:
        """Move selection to the first selectable index."""
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
        """Move selection to the last selectable index."""
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
        """Default left-key handler; subclasses may override to provide actions."""
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
        """Default right-key handler; subclasses may override to provide actions."""
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
        """Default Enter-key handler; subclasses may override to provide actions."""
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

    def key_s_helper(self, event: events.Key | None = None) -> None:
        """Common helper to prompt and save snapshot files for a visible widget.

        Pops a modal asking whether to save the older (previous_hash), newer
        (current_hash), or both versions of the current `app.rel_dir`/`app.rel_file`.
        The modal performs the actual file extraction and writing.
        """
        try:
            try:
                app = self.app
            except Exception as e:
                self.printException(e, "key_s_helper: accessing self.app failed")
                app = None
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_s_helper: event.stop failed")

            if app is None:
                logger.debug("key_s_helper: no app available")
                return

            # Build an absolute filepath from app rel_dir/rel_file when available
            filepath = None
            try:
                if getattr(app, "rel_file", None):
                    filepath = app.gitRepo.full_path_for(getattr(app, "rel_dir", "") or "", app.rel_file)
                elif getattr(app, "rel_dir", None) is not None:
                    filepath = app.gitRepo.full_path_for(app.rel_dir or "", None)
            except Exception as _ex:
                self.printException(_ex, "key_s_helper: building filepath from app.rel_dir/rel_file failed")
            prev_hash = getattr(app, "previous_hash", None)
            curr_hash = getattr(app, "current_hash", None)

            # If filepath appears to be a directory, keep it as-is
            if filepath and os.path.isdir(filepath):
                pass

            if not filepath:
                try:
                    # Inform user with a tiny modal
                    app.push_screen(SaveSnapshotModal("Unknown filename for save"))
                except Exception as e:
                    self.printException(e, "key_s_helper: push modal failed")
                return

            try:
                try:
                    repo_root_val = app.repo_root
                except Exception as e:
                    self.printException(e, "key_s_helper: reading app.repo_root failed")
                    repo_root_val = None
                msg = f"Create {os.path.basename(filepath)}.HASH. Do you wish to save the (o)lder file, the (n)ewer file, or (b)oth? (Any other key to cancel.)"
                app.push_screen(
                    SaveSnapshotModal(
                        msg, filepath=filepath, prev_hash=prev_hash, curr_hash=curr_hash, repo_root=repo_root_val
                    )
                )
            except Exception as e:
                self.printException(e, "key_s_helper: push SaveSnapshotModal failed")
        except Exception as e:
            self.printException(e, "key_s_helper failed")


class SaveSnapshotModal(AppException, ModalScreen):
    """Modal that prompts the user to save older/newer versions of a file.

    The modal handles the key press and writes the requested snapshots
    to files named '<filepath>.<hash>'. Supported keys: o/O (older),
    n/N (newer), b/B (both). Any other key cancels.
    """

    def __init__(
        self,
        message: str | None = None,
        filepath: str | None = None,
        prev_hash: str | None = None,
        curr_hash: str | None = None,
        repo_root: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.message = message or ""
        self.filepath = filepath
        self.prev_hash = prev_hash
        self.curr_hash = curr_hash
        self.repo_root = repo_root

    def compose(self):
        """Compose the modal contents (a single Label with the message)."""
        try:
            yield Label(Text(self.message, style="bold"))
        except Exception as e:
            # Best-effort: avoid modal failure — ensure we log the original
            self.printException(e, "SaveSnapshotModal.compose failed")

            try:
                yield Label(Text(self.message or "", style="bold"))
            except Exception as e2:
                # If even yielding fails, log and give up
                self.printException(e2, "SaveSnapshotModal.compose fallback failed")

    def on_key(self, event: events.Key) -> None:
        """Handle a single key press: o/O -> older, n/N -> newer, b/B -> both."""
        try:
            key = getattr(event, "key", "")
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "SaveSnapshotModal.on_key: event.stop failed")

            # Map keys to actions
            try:
                if key in ("o", "O"):
                    if self.prev_hash:
                        self._save(self.prev_hash)
                elif key in ("n", "N"):
                    if self.curr_hash:
                        self._save(self.curr_hash)
                elif key in ("b", "B"):
                    if self.prev_hash:
                        self._save(self.prev_hash)
                    if self.curr_hash:
                        self._save(self.curr_hash)
            except Exception as e:
                self.printException(e, "SaveSnapshotModal.on_key: _save failed")

        finally:
            try:
                self.app.pop_screen()
            except Exception as e:
                self.printException(e, "SaveSnapshotModal.on_key: pop_screen failed")

    def _save(self, hashval: str | None) -> None:
        """Save the file content for the given hash into a target snapshot file."""
        if not hashval or not self.filepath:
            return

        try:
            relpath = os.path.relpath(self.filepath, self.repo_root)
        except Exception as e:
            self.printException(e, "SaveSnapshotModal._save: computing relpath failed")
            relpath = os.path.basename(self.filepath)

        target_path = f"{self.filepath}.{hashval}"

        # Helper to write bytes to target
        def _write_bytes(bdata: bytes) -> None:
            try:
                ddir = os.path.dirname(target_path)
                if ddir and not os.path.exists(ddir):
                    try:
                        os.makedirs(ddir, exist_ok=True)
                    except Exception as e:
                        self.printException(e, "SaveSnapshotModal._save: makedirs failed")

                with open(target_path, "wb") as out:
                    out.write(bdata)
            except Exception as e:
                self.printException(e, f"SaveSnapshotModal._write failed for {target_path}")

        # Different strategies based on hash semantics
        if hashval == "MODS":
            # Working tree (unstaged) version
            try:
                with open(self.filepath, "rb") as f:
                    data = f.read()
                _write_bytes(data)
                return
            except Exception as e:
                self.printException(e, "SaveSnapshotModal._save read working-tree failed")
                return

        if hashval == "STAGED":
            # Read from index via git show :<relpath>
            try:
                gitrepo = self.app.gitRepo
                data = gitrepo.getFileContents("STAGED", relpath)
                if data is None:
                    raise Exception("git show STAGED failed")
                _write_bytes(data)
                return
            except Exception as e:
                self.printException(e, "SaveSnapshotModal._save STAGED failed")
                return

        # Otherwise treat as commit-ish hash: git show <hash>:<relpath>
        try:
            gitrepo = self.app.gitRepo
            data = gitrepo.getFileContents(hashval, relpath)
            if data is None:
                raise Exception("git show failed")
            _write_bytes(data)
        except Exception as e:
            self.printException(e, "SaveSnapshotModal._save commit show failed")


# Top-level modal so callers can push it via `self.app.push_screen(_TBDModal(...))`
class MessageModal(ModalScreen):
    """Simple modal that shows a message (default "") and closes on any key.

    Mirrors the helper from `gitdiffnavtool-old.py`.
    """

    def __init__(self, message: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.message = message or ""

    def compose(self):
        """Compose the modal contents (a single Label with bold text)."""
        try:
            yield Label(Text(self.message, style="bold"), id="msg-modal")
        except Exception as e:
            printException(e, "MessageModal.compose failed")

    def on_key(self, event: events.Key) -> None:
        """Close the modal on any key press."""
        try:
            try:
                event.stop()
            except Exception as _use_pass:
                pass
            try:
                self.app.pop_screen()
            except Exception as e:
                printException(e, "MessageModal.on_key: pop_screen failed")
        except Exception as e:
            printException(e, "MessageModal.on_key failed")


class FileListBase(AppBase):
    """Base for file list widgets.

    Provides safe focus handling, highlighting helpers, and small default
    implementations that concrete subclasses can override.
    """

    def on_focus(self) -> None:
        """Ensure the widget has a valid `index` when it receives focus."""
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
                    self.call_after_refresh(lambda: self._safe_scroll_to_widget(node, animate=False))
                    return
                except Exception as e:
                    self.printException(e, "_ensure_index_visible: scroll_to_widget failed")
            # Fallback to node.scroll_visible if provided
            try:
                self.call_after_refresh(lambda: self._safe_node_scroll_visible(node, True))
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
                    repo_root_local = self.app.gitRepo.get_repo_root()
                    match_full = self._canonical_relpath(filename, repo_root_local)
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
                            repo_root_local = self.app.gitRepo.get_repo_root()
                            node_full = self._canonical_relpath(raw, repo_root_local)
                        except Exception as e:
                            node_full = raw
                            self.printException(e, "_highlight_filename: computing node_full failed")
                        if node_full == match_full:
                            try:
                                self.call_after_refresh(lambda: self._safe_activate_index(i))
                            except Exception as e:
                                self.printException(e, "_highlight_filename: scheduling index set failed")
                                try:
                                    self._activate_index(i)
                                except Exception as e2:
                                    self.printException(e2, "setting index in _highlight_filename")
                            return
                    try:
                        text = self.text_of(node)
                    except Exception as e:
                        self.printException(e, "_highlight_filename: extracting text failed")
                        text = str(node)
                    if text == filename:
                        try:
                            self.call_after_refresh(lambda: self._safe_activate_index(i))
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

    def _finalize_filelist_prep(
        self, curr_hash: str | None = None, prev_hash: str | None = None, path: str | None = None
    ) -> None:
        """Finalize for file-list widgets: highlight by filename/path then sync common state."""
        try:
            try:
                if path is not None:
                    try:
                        # Prefer filename/path highlighting when a path is provided
                        self._highlight_filename(path)
                    except Exception as e:
                        self.printException(e, "FileListBase._finalize_filelist_prep: _highlight_filename failed")
                elif curr_hash:
                    try:
                        self._highlight_match(curr_hash)
                    except Exception as e:
                        self.printException(e, "FileListBase._finalize_filelist_prep: _highlight_match failed")
                else:
                    try:
                        self._highlight_top()
                    except Exception as e:
                        self.printException(e, "FileListBase._finalize_filelist_prep: _highlight_top failed")
            except Exception as e:
                self.printException(e, "FileListBase._finalize_filelist_prep: highlight step failed")

            try:
                self._finalize_prep_common(curr_hash=curr_hash, prev_hash=prev_hash, path=path)
            except Exception as e:
                self.printException(e, "FileListBase._finalize_filelist_prep: _finalize_prep_common failed")
        except Exception as e:
            self.printException(e, "FileListBase._finalize_filelist_prep failed")

    def watch_index(self, old, new) -> None:
        """Default index-change handler for file-list widgets.

        Delegates styling updates to `AppBase.watch_index` and logs the
        index transition. Subclasses may override for custom behavior.
        """
        try:
            # keep existing logging but delegate to base handler for styling
            try:
                super().watch_index(old, new)
            except Exception as e:
                self.printException(e, "FileListBase.watch_index: base watch failed")
            logger.debug("FileListBase index changed %r -> %r", old, new)
        except Exception as e:
            self.printException(e, "FileListBase.watch_index failed")

    def watch_filelist_index(self, old: int | None, new: int | None, node_new) -> None:
        """File-list specific post-highlight hook.

        Keeps `app.rel_dir` and `app.rel_file` in sync with the newly-highlighted
        row's `_raw_text` value where appropriate.
        """
        try:
            raw = getattr(node_new, "_raw_text", None)
            if raw:
                try:
                    # Raw values are repo-relative; normalize before storing
                    rel = os.path.normpath(raw)
                    try:
                        rd, rf = os.path.split(rel)
                        self.app.rel_dir = rd or ""
                        self.app.rel_file = rf or ""
                    except Exception as _ex:
                        self.printException(_ex, "watch_filelist_index: setting app.rel_dir/rel_file failed")
                except Exception as _ex:
                    self.printException(_ex, "watch_filelist_index: setting app.rel_dir/rel_file failed")
            logger.debug(
                "watch_filelist_index: set app.rel_dir/app.rel_file=%r/%r",
                self.app.rel_dir,
                self.app.rel_file,
            )
        except Exception as e:
            self.printException(e, "watch_filelist_index failed")

    def on_list_view_highlighted(self, event) -> None:
        """Hook invoked by Textual when the list view highlight changes.

        Default implementation logs the event; subclasses may override.
        """
        logger.debug("list view highlighted: %s", event)

    def _child_filename(self, node) -> str:
        """Return the filename or visible text for a child `node`.

        Safe wrapper around `text_of` that falls back to stringifying the
        node when extraction fails.
        """
        try:
            return self.text_of(node)
        except Exception as e:
            printException(e)
            return str(node)

    def _enter_directory(self, filename: str) -> None:
        """Handle a request to enter the directory named `filename`.

        Default implementation logs the request; subclasses may override to
        change the UI mode or update the widget to display the directory.
        """
        logger.debug("enter directory requested and ignored: %s", filename)

    def _list_directory(self, path: str) -> list[str]:
        """Return a sorted list of entries in `path`.

        Safe wrapper around `os.listdir` that logs and returns an empty
        list on error so callers don't need try/except every time.
        """
        try:
            entries = sorted(os.listdir(path))
            return entries
        except Exception as e:
            self.printException(e, f"_list_directory: reading {path} failed")
            return []

    def _render_parent_entry_if_needed(self, path: str) -> None:
        """Add a parent (`..`) entry when `path` is not the repo root.

        Creates a non-selectable ListItem with metadata `_filename='..'` and
        `_is_dir=True` and appends it to the list. Safe no-op on error.
        """
        try:
            parent = os.path.dirname(path)
            logger.debug("_render_parent_entry_if_needed: path=%s parent=%s", path, parent)
            try:
                parent_abs = os.path.normpath(parent) if parent else ""
                path_abs = os.path.normpath(path) if path else ""
                repo_root_abs = self.app.gitRepo.get_repo_root()
            except Exception as e:
                self.printException(e, "_render_parent_entry_if_needed: realpath lookup failed")
                parent_abs = parent
                path_abs = path
                try:
                    repo_root_abs = self.app.gitRepo.get_repo_root()
                except Exception as e:
                    self.printException(e, "_render_parent_entry_if_needed: getting repo_root failed")
                    repo_root_abs = None
            if parent and parent_abs != path_abs and path_abs != repo_root_abs:
                try:
                    parent_item = ListItem(Label(Text(f"← ..", style=STYLE_PARENT)))
                    try:
                        parent_item._filename = ".."
                        parent_item._is_dir = True
                        try:
                            # Store repo-relative raw path when possible
                            repo_root_local = self.app.gitRepo.get_repo_root()
                            parent_item._raw_text = (
                                os.path.relpath(parent_abs, repo_root_local) if parent_abs else parent
                            )
                        except Exception as e:
                            self.printException(e, "_render_parent_entry_if_needed: relpath failed")
                            parent_item._raw_text = parent
                    except Exception as e:
                        self.printException(e, "_render_parent_entry_if_needed: setting metadata failed")
                        parent_item._raw_text = parent
                        logger.debug(
                            "_render_parent_entry_if_needed: adding parent dir item for %s", parent_item._raw_text
                        )
                    except Exception as e:
                        self.printException(e, "_render_parent_entry_if_needed: setting parent item attributes failed")
                    try:
                        self.append(parent_item)
                    except Exception as e:
                        self.printException(e, "_render_parent_entry_if_needed: append parent failed")
                except Exception as e:
                    self.printException(e, "_render_parent_entry_if_needed: creating parent item failed")
        except Exception as e:
            self.printException(e, "_render_parent_entry_if_needed failed")

    def _render_hash_header(self, prev_hash: str | None, curr_hash: str | None) -> None:
        """Render the non-selectable hash header row for repo-mode file lists.

        The header is appended as a `ListItem` with `_hash_header=True` and
        `_selectable=False` so navigation logic can skip it.
        """
        try:

            def _short(h: str | None) -> str:
                if not h:
                    return "None"
                return h[:HASH_LENGTH] if len(h) > HASH_LENGTH else h

            hash_text = f"Hashes: prev={_short(prev_hash)}  curr={_short(curr_hash)}"
            try:
                hash_item = ListItem(Label(Text(hash_text, style=STYLE_FILELIST_KEY)))
                try:
                    hash_item._hash_header = True
                    hash_item._selectable = False
                    self.append(hash_item)
                except Exception as e:
                    self.printException(e, "_render_hash_header: appending hash header failed")
            except Exception as e:
                self.printException(e, "_render_hash_header: creating hash header failed")
        except Exception as e:
            self.printException(e, "_render_hash_header failed")

    def _schedule_highlight_and_visibility(self, highlight: str | None, base_path: str | None = None) -> None:
        """Schedule highlighting and ensure the selected node is visible.

        If `highlight` is provided, resolve it to an absolute candidate inside
        `base_path` (or this widget's `self.path`) when it's not already
        absolute, schedule a safe highlight match, and then schedule index
        visibility. When no `highlight` is provided schedule the logical
        top highlight.
        """
        try:
            if highlight:
                try:
                    candidate = highlight
                    # Always treat highlights as repo-relative and resolve
                    # them under `base_path` or the app's current path under
                    # `repo_root` before highlighting.
                    if base_path is None:
                        try:
                            rd = self.app.rel_dir
                            rf = self.app.rel_file
                            if rf:
                                bp_rel = os.path.join(rd or "", rf)
                            else:
                                bp_rel = rd or "."
                            try:
                                repo_root_local = self.app.gitRepo.get_repo_root()
                                bp = os.path.join(repo_root_local, bp_rel) if repo_root_local else bp_rel
                            except Exception as e:
                                self.printException(e, "_schedule_highlight_and_visibility: getting repo_root failed")
                                bp = bp_rel
                        except Exception as e:
                            self.printException(
                                e, "_schedule_highlight_and_visibility: reading app.rel_dir/rel_file failed"
                            )
                            bp = "."
                    else:
                        bp = base_path
                    candidate = os.path.join(bp, candidate)
                except Exception as e:
                    self.printException(e, "_schedule_highlight_and_visibility: candidate adjustment failed")
                    candidate = highlight

                try:
                    self.call_after_refresh(lambda: self._safe_highlight_match(candidate))
                except Exception as e:
                    self.printException(e, "_schedule_highlight_and_visibility: scheduling highlight failed")
                    try:
                        self._highlight_match(candidate)
                    except Exception as e2:
                        self.printException(e2, "_schedule_highlight_and_visibility: immediate highlight failed")

                try:
                    self.call_after_refresh(self._ensure_index_visible)
                except Exception as e:
                    self.printException(
                        e, "_schedule_highlight_and_visibility: scheduling _ensure_index_visible failed"
                    )
            else:
                try:
                    self.call_after_refresh(self._highlight_top)
                except Exception as e:
                    self.printException(e, "_schedule_highlight_and_visibility: scheduling _highlight_top failed")
                    try:
                        self._highlight_top()
                    except Exception as e2:
                        self.printException(
                            e2, "_schedule_highlight_and_visibility: immediate _highlight_top fallback failed"
                        )
        except Exception as e:
            self.printException(e, "_schedule_highlight_and_visibility failed")

    def _build_status_map(self, path: str) -> dict | None:
        """Build and return a porcelain `status_map` for `path` or None.

        Always prefer the git CLI based status map. Return None only on
        unexpected failures so callers may fall back if necessary.
        """
        try:
            # Build a map of repo-relative path -> two-char porcelain code
            # (index, worktree) by invoking `git status --porcelain` via
            # the shared GitRepo instance on the app. This centralizes git
            # command invocation and reuses GitRepo's helpers.
            gitrepo = self.app.gitRepo
            out = gitrepo._git_run(["git", "status", "--porcelain", "--", path], text=True) or ""
            if not out:
                return {}

            status_map: dict = {}
            for line in out.splitlines():
                if not line:
                    continue
                # porcelain format: XY SP PATH  (or '?? PATH')
                try:
                    # Prefer the common XY<space>path form
                    if len(line) >= 4 and line[2] == " ":
                        code = line[:2]
                        p = line[3:]
                    else:
                        # Fallback: take first two chars as code and the rest as path
                        code = (line + "  ")[:2]
                        p = line[2:].lstrip()
                    p = gitrepo._git_cli_decode_quoted_path(p.strip())
                    status_map[p] = code
                except Exception as _ex:
                    self.printException(_ex, "_build_status_map: parsing line failed")
                    continue
            return status_map
        except Exception as e:
            self.printException(e, "_build_status_map failed")
            return None

    def _populate_from_file_infos(self, file_infos: list[dict]) -> None:
        """Append ListItems for each dict in `file_infos`.

        Each dict is expected to have keys: `name`, `full`, `is_dir`, `raw`, `repo_status`.
        This centralizes the row-creation logic used by file-list preparers.
        """
        try:
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
                            try:
                                # Prefer storing repo-relative raw paths
                                repo_root_local = self.app.gitRepo.get_repo_root()
                                item._raw_text = os.path.relpath(full, repo_root_local) if full else name
                            except Exception as e:
                                self.printException(e, "_populate_from_file_infos: relpath failed for dir")
                                item._raw_text = name
                            item._filename = name
                            logger.debug("_populate_from_file_infos: adding dir item for %s", full)
                            self.append(item)
                        except Exception as e:
                            self.printException(e, "_populate_from_file_infos append dir failed")
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
                        try:
                            repo_root_local = self.app.gitRepo.get_repo_root()
                            item._raw_text = os.path.relpath(full, repo_root_local) if full else name
                        except Exception as e:
                            self.printException(e, "_populate_from_file_infos: relpath fallback failed")
                            item._raw_text = name
                        item._filename = name
                        self.append(item)
                    except Exception as e:
                        self.printException(e, f"_populate_from_file_infos appending {name} failed")
                        continue
                except Exception as e:
                    self.printException(e, f"_populate_from_file_infos processing entry failed")
                    continue
        except Exception as e:
            self.printException(e, "_populate_from_file_infos failed")

    def _to_display_rows(self, raw_filelist: list) -> list[dict]:
        """
        Convert raw file-list items from backends into display-ready dicts.

        Accepts backend outputs such as lists of `(path, status)` tuples
        (repo-relative or absolute paths) or dicts with `display`/`full`.
        Returns list of dicts with keys: `name`, `full`, `is_dir`, `raw`, `repo_status`.

        - Paths are resolved into absolute full paths using `self.app.repo_root`.
        """
        rows: list[dict] = []
        try:
            try:
                repo_root_local = self.app.gitRepo.get_repo_root()
            except Exception as e:
                self.printException(e, "_to_display_rows: getting repo_root failed")
                repo_root_local = None
            base = repo_root_local
            for entry in raw_filelist or []:
                try:
                    # If already a normalized dict use it as-is (but ensure keys)
                    if isinstance(entry, dict):
                        full = entry.get("full") or entry.get("display") or entry.get("path")
                        name = entry.get("name") or (os.path.basename(full) if full else entry.get("display"))
                        is_dir = bool(entry.get("is_dir", False))
                        raw = entry.get("raw", name)
                        repo_status = entry.get("repo_status")
                        # Resolve full to a normalized path when possible
                        try:
                            if full and base:
                                full = os.path.join(base, full)
                            # else leave `full` as provided (repo-relative or empty)
                        except Exception as e:
                            self.printException(e, "_to_display_rows: resolving full path failed")
                        try:
                            rel_raw = os.path.relpath(full, repo_root_local) if full and repo_root_local else full
                        except Exception as e:
                            self.printException(e, "_to_display_rows: rel_raw computation failed")
                            rel_raw = raw
                        rows.append(
                            {"name": name, "full": full, "is_dir": is_dir, "raw": rel_raw, "repo_status": repo_status}
                        )
                        continue

                    # Tuples of (path, status) are common from git diff helpers
                    if isinstance(entry, (list, tuple)) and len(entry) >= 1:
                        path = entry[0]
                        status = entry[1] if len(entry) > 1 else None
                        # Resolve full path
                        try:
                            # Treat `path` as repo-relative and form a path under `base`.
                            full = os.path.join(base, path) if base else path
                        except Exception as e:
                            self.printException(e, "_to_display_rows: resolving full path failed")
                            full = path

                        # Determine name and directory-ness
                        try:
                            is_dir = os.path.isdir(full)
                        except Exception as e:
                            self.printException(e, "_to_display_rows: isdir check failed")
                            is_dir = False
                        name = os.path.basename(full) if full else path
                        try:
                            raw = (
                                os.path.relpath(full, repo_root_local)
                                if full and repo_root_local
                                else (full if is_dir else name)
                            )
                        except Exception as e:
                            self.printException(e, "_to_display_rows: raw relpath failed")
                            raw = full if is_dir else name

                        # Conservative repo_status: leave None for diff-driven lists
                        repo_status = None
                        # However map a few well-known status tokens when present
                        try:
                            s = str(status) if status is not None else ""
                            s_up = s.upper()
                            if s_up in ("??", "UNTRACKED"):
                                repo_status = "untracked"
                            elif s_up in ("!!", "IGNORED"):
                                repo_status = "ignored"
                            elif "U" in s_up or "!" in s_up:
                                repo_status = "conflicted"
                            elif s_up == "D" or "DELETED" in s_up:
                                repo_status = "wt_deleted"
                            elif s_up == "A" or "ADDED" in s_up:
                                repo_status = "staged"
                            elif s_up == "M" or "MODIFIED" in s_up:
                                repo_status = "modified"
                        except Exception as e:
                            self.printException(e, "_to_display_rows: mapping status failed")
                            repo_status = None

                        rows.append(
                            {"name": name, "full": full, "is_dir": is_dir, "raw": raw, "repo_status": repo_status}
                        )
                        continue

                    # Fallback: stringify and append
                    s = str(entry)
                    rows.append(
                        {"name": os.path.basename(s), "full": s, "is_dir": False, "raw": s, "repo_status": None}
                    )
                except Exception as e:
                    self.printException(e, "_to_display_rows: processing entry failed")
                    continue
        except Exception as e:
            self.printException(e, "_to_display_rows failed")
        return rows


class FileModeFileList(FileListBase):
    """
    File-mode file list: shows files for a working tree path.

    For regen Step 3 this class provides a `prepFileModeFileList` stub and
    default `key_left`/`key_right` handlers.
    """

    def prepFileModeFileList(
        self,
        rel_dir: str,
        rel_path: str,
    ) -> None:
        """Populate this widget with the file list for `path`.

        `highlight_filename` if provided will be highlighted in the list; if
        `path` names a file the file's containing directory is listed and the
        filename is used as the highlight candidate.
        """
        try:
            # Minimal replacement: clear list, show headers, call GitRepo
            # helpers `getFileListUntrackedAndIgnored` and
            # `getFileListBetweenNewRepoAndMods`, and display their results.
            self.clear()
            self._add_filelist_key_header()

            gitrepo = self.app.gitRepo

            # Gather untracked and ignored entries separately: lists of (path, iso, status)
            try:
                untracked = gitrepo.getFileListUntracked()
            except Exception as e:
                self.printException(e, "prepFileModeFileList: getFileListUntracked failed")
                untracked = []

            try:
                ignored = gitrepo.getFileListIgnored()
            except Exception as e:
                self.printException(e, "prepFileModeFileList: getFileListIgnored failed")
                ignored = []

            # Gather working-tree modifications (MODS): list of (path,status)
            try:
                mods = gitrepo.getFileListBetweenNewRepoAndMods()
            except Exception as e:
                self.printException(e, "prepFileModeFileList: getFileListBetweenNewRepoAndMods failed")
                mods = []

            # Build an index mapping directories -> immediate child dirs and files
            # so we can quickly render a directory slice for `rel_dir`.
            # Data structure:
            # nodes_by_dir: dict[str, {'dirs': set[str], 'files': list[(name, status, iso)]}]
            # - key is repository-relative directory path ("" for repo root)
            # - 'dirs' contains immediate child directory names
            # - 'files' contains tuples for immediate files in that directory
            # Note: we intentionally omit storing absolute/full paths here to
            # reduce memory and because most UI actions operate on repo-relative
            # names; callers that need full paths can reconstruct them from the
            # repo root when necessary.
            nodes_by_dir: dict = {}

            def ensure_dir_node(d: str):
                if d not in nodes_by_dir:
                    nodes_by_dir[d] = {"dirs": set(), "files": []}

            def register_file(rel_path: str, status: str, iso: str | None):
                parent = os.path.dirname(rel_path)
                if parent == "":
                    parent = ""
                ensure_dir_node(parent)
                name = os.path.basename(rel_path)
                nodes_by_dir[parent]["files"].append((name, status, iso))

                # register parent as a child in its parent directory
                if parent:
                    grand = os.path.dirname(parent)
                    if grand == "":
                        grand = ""
                    ensure_dir_node(grand)
                    nodes_by_dir[grand]["dirs"].add(os.path.basename(parent))

            # Add untracked entries: (path, iso, status)
            for entry in untracked:
                try:
                    p = entry[0]
                    iso = entry[1] if len(entry) > 1 else None
                    status = entry[2] if len(entry) > 2 else "untracked"
                    register_file(p, status, iso)
                except Exception:
                    continue

            # Add ignored entries: (path, iso, status)
            for entry in ignored:
                try:
                    p = entry[0]
                    iso = entry[1] if len(entry) > 1 else None
                    status = entry[2] if len(entry) > 2 else "ignored"
                    register_file(p, status, iso)
                except Exception:
                    continue

            # Add mods entries: (path, status) - no iso provided
            for entry in mods:
                try:
                    # entry is (path, status)
                    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                        p, s = entry[0], entry[1]
                    else:
                        p, s = str(entry), "modified"
                    register_file(p, s, None)
                except Exception:
                    continue

            # Display only the requested directory slice
            # Normalize the requested rel_dir so callers may pass '' './' or '.'
            norm_rel_dir = rel_dir or ""
            if isinstance(norm_rel_dir, str) and norm_rel_dir.startswith("./"):
                norm_rel_dir = norm_rel_dir[2:]
            norm_rel_dir = os.path.normpath(norm_rel_dir) if norm_rel_dir else ""
            if norm_rel_dir == ".":
                norm_rel_dir = ""

            slice_node = nodes_by_dir.get(norm_rel_dir, {"dirs": set(), "files": []})

            try:
                self.append(ListItem(Label(Text(f"Directory: {rel_dir or '/'}", style=STYLE_HELP_BG))))
            except Exception:
                pass

            # Show directories first
            for dname in sorted(slice_node["dirs"]):
                try:
                    txt = f"dir/ {dname}"
                    self.append(ListItem(Label(Text(txt))))
                except Exception as e:
                    self.printException(e, "prepFileModeFileList: appending dir entry failed")

            # Then show files
            for name, status, iso in sorted(slice_node["files"], key=lambda x: x[0]):
                try:
                    ts = iso if iso is not None else ""
                    txt = f"{status} {name} {ts}".strip()
                    self.append(ListItem(Label(Text(txt))))
                except Exception as e:
                    self.printException(e, "prepFileModeFileList: appending file entry failed")

            # Finalize minimal population state
            try:
                self._populated = True
                nodes = self.nodes()
                self._min_index = 1 if len(nodes) > 1 else 0
            except Exception:
                pass

        except Exception as e:
            self.printException(e, "prepFileModeFileList failed")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.highlight_bg_style = HIGHLIGHT_FILELIST_BG

    def _nav_dir_if(self, test_fn) -> None:
        """If the currently-selected node is a directory and `test_fn` returns
        True for its name, navigate into that directory.

        `test_fn` is a callable that accepts the filename and returns a
        boolean indicating whether to enter the directory. This centralizes
        the enter-dir logic used by several key handlers.
        """
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
                    gitrepo = self.app.gitRepo
                    try:
                        root = gitrepo.get_repo_root()
                        rr = raw
                        if rr == root:
                            rel = ""
                        elif rr.startswith(root + os.sep):
                            rel = rr[len(root) + 1 :]
                        else:
                            rel = os.path.relpath(rr, root)
                        if os.path.isdir(rr):
                            rdir = rel
                            rpath = None
                        else:
                            rdir = os.path.dirname(rel) or ""
                            rpath = os.path.basename(rel)
                        self.prepFileModeFileList(rdir, rpath)
                    except Exception as _ex:
                        self.printException(_ex, "FileModeFileList._nav_dir_if prep failed")
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
        """Activate the selected node or open its history if it's a file.

        - If the selected node is a directory and `enter_dir_test_fn(name)`
          returns True, navigate into it.
        - If the selected node is a file and `allow_file_open` is True,
          open the file's history via `prepFileModeHistoryList` unless the
          file is untracked.
        The `event` (if provided) will be stopped to prevent further handling.
        """
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
                try:
                    if enter_dir_test_fn(name):
                        try:
                            gitrepo = self.app.gitRepo
                            try:
                                root = gitrepo.get_repo_root()
                                rr = raw
                                if rr == root:
                                    rel = ""
                                elif rr.startswith(root + os.sep):
                                    rel = rr[len(root) + 1 :]
                                else:
                                    rel = os.path.relpath(rr, root)
                                if os.path.isdir(rr):
                                    rdir = rel
                                    rpath = None
                                else:
                                    rdir = os.path.dirname(rel) or ""
                                    rpath = os.path.basename(rel)
                                self.prepFileModeFileList(rdir, rpath)
                            except Exception as _ex:
                                self.printException(_ex, "_activate_or_open: prepFileModeFileList failed")
                        except Exception as e:
                            self.printException(e, "_activate_or_open: prepFileModeFileList failed")
                except Exception as e:
                    self.printException(e, "_activate_or_open: enter_dir_test_fn failed")
                return

            # Not a directory — possibly open file in history view
            if not allow_file_open:
                return
            try:
                if repo_status in ("untracked", "ignored"):
                    # Opening untracked or ignored files doesn't make sense in history
                    # view; log and ignore.
                    logger.debug("_activate_or_open: skipping open for %s file %s", repo_status, raw)
                    return
            except Exception as e:
                self.printException(e, "_activate_or_open: repo_status check failed")

            try:
                # Default behavior: prepare the right-hand file-history widget
                # (the app composes a FileModeHistoryList on the right) and
                # invoke its preparer so the UI shows the file's history.
                self.app.file_mode_history_list.prepFileModeHistoryList(raw)
            except Exception as e:
                self.printException(e, "_activate_or_open: prepFileModeHistoryList failed")

            try:
                # Switch UI to file-history layout and focus
                self.app.change_state("file_history", f"#{RIGHT_HISTORY_LIST_ID}", RIGHT_HISTORY_FOOTER)
            except Exception as e:
                self.printException(e, "FileModeFileList._activate_or_open change_state failed")
        except Exception as e:
            self.printException(e, "FileModeFileList._activate_or_open failed")

    def key_left(self, event: events.Key | None = None) -> None:
        """Handle Left key in a file list: enter parent directory when selected.

        This delegates to `_activate_or_open` and prevents opening files.
        """
        logger.debug("FileModeFileList.key_left called: key=%r index=%r", getattr(event, "key", None), self.index)
        self._activate_or_open(event, enter_dir_test_fn=lambda name: name == "..", allow_file_open=False)

    def key_right(self, event: events.Key | None = None) -> None:
        """Handle Right key in a file list: enter directories or open files."""
        logger.debug("FileModeFileList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index)
        self._activate_or_open(event, enter_dir_test_fn=lambda name: (name is not None) and name != "..")

    def key_enter(self, event: events.Key | None = None) -> None:
        """Enter key: enter directories or open file history for tracked files."""
        logger.debug("FileModeFileList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        self._activate_or_open(event, enter_dir_test_fn=lambda name: True)


class RepoModeFileList(FileListBase):
    """Repo-mode file list: shows files changed between commits.

    Provides a `prepRepoModeFileList` stub and navigation handlers.
    """

    def prepRepoModeFileList(
        self, prev_hash: str | None, curr_hash: str | None, highlight_filename: str | None = None
    ) -> None:
        """Populate this widget with files changed between `prev_hash` and `curr_hash`.

        If either hash is a pseudo-name (e.g. 'MODS' or 'STAGED') the
        corresponding pseudo-entries are collected and rendered instead of
        delegating to `git diff`.
        """
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
                self._render_hash_header(prev_hash, curr_hash)
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
                # Collect pseudo-entries (working-tree/index) using the git CLI
                if prev_hash in pseudo_names:
                    pseudo_entries.extend(self._prepRepoModePseudo_from_git(prev_hash))
                if curr_hash in pseudo_names:
                    pseudo_entries.extend(self._prepRepoModePseudo_from_git(curr_hash))
                # If both prev and curr were pseudo names their collected
                # entries may overlap (a file can be both STAGED and MODS).
                # Prefer entries from `curr_hash` when duplicates appear by
                # removing earlier duplicates and keeping the last occurrence.
                try:
                    seen = set()
                    dedup = []
                    for status, path in reversed(pseudo_entries):
                        if path in seen:
                            continue
                        seen.add(path)
                        dedup.append((status, path))
                    dedup.reverse()
                    pseudo_entries = dedup
                except Exception as e:
                    self.printException(e, "prepRepoModeFileList deduplicating pseudo_entries failed")

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
                    self._format_pseudo_summary(pseudo_entries)
                except Exception as e:
                    self.printException(e, "prepRepoModeFileList rendering pseudo entries failed")
            else:
                # Delegate diff collection to helpers; each helper returns
                # a list of dicts with keys: display, full, is_dir.
                try:
                    entries = self._prepRepoModeFileList_from_git(prev_hash, curr_hash)

                    # Normalize entries and delegate row creation to shared helper
                    try:
                        file_infos: list[dict] = []
                        for entry in entries:
                            try:
                                if isinstance(entry, dict):
                                    display = entry.get("display")
                                    full = entry.get("full", display)
                                    is_dir = entry.get("is_dir", False)
                                else:
                                    display = str(entry)
                                    full = display
                                    is_dir = False
                                name = os.path.basename(full) if full else display
                                try:
                                    repo_root_local = self.app.gitRepo.get_repo_root()
                                    raw_val = self._canonical_relpath(full, repo_root_local) if full else name
                                except Exception as e:
                                    self.printException(e, "prepRepoModeFileList: canonicalizing entry failed")
                                    raw_val = name
                                file_infos.append(
                                    {"name": name, "full": full, "is_dir": is_dir, "raw": raw_val, "repo_status": None}
                                )
                            except Exception as _ex:
                                self.printException(_ex, "prepRepoModeFileList: normalizing entry failed")
                                continue
                        try:
                            self._populate_from_file_infos(file_infos)
                        except Exception as _ex:
                            self.printException(_ex, "prepRepoModeFileList: populating entries failed")
                    except Exception as e:
                        self.printException(e, "prepRepoModeFileList processing entries failed")
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
                # If caller requested a filename highlight, record it via
                # `rel_dir`/`rel_file` so other components can rely on a single source of truth.
                if highlight_filename:
                    try:
                        # Store repo-relative highlight paths; normalize input
                        rel = os.path.normpath(highlight_filename)
                        rd, rf = os.path.split(rel)
                        self.app.rel_dir = rd or ""
                        self.app.rel_file = rf or ""
                    except Exception as _ex:
                        self.printException(_ex, "prepRepoModeFileList: setting app.rel_dir/rel_file failed")
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

            # Run centralized finalization so UI/app state is kept consistent
            try:
                self._finalize_filelist_prep(
                    curr_hash=curr_hash, prev_hash=prev_hash, path=highlight_filename if highlight_filename else None
                )
            except Exception as e:
                self.printException(e, "prepRepoModeFileList: finalize failed")
        except Exception as e:
            self.printException(e, "prepRepoModeFileList failed")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.highlight_bg_style = HIGHLIGHT_FILELIST_BG

    def key_left(self, event: events.Key | None = None) -> None:
        """Handle Left key in repo-mode file list: switch to history fullscreen.

        Typically moves focus back to the left history column or toggles
        the paired layout; defensive with event.stop() handling.
        """
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
        """Open diff view for the selected file and switch to the file view.

        Delegates to `DiffList` to prepare the diff and records the
        app-level `path` for downstream helpers. Honors `recursive` when
        invoked as an alias.
        """
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
            try:
                rel = os.path.normpath(filename)
                rd, rf = os.path.split(rel)
                self.app.rel_dir = rd or ""
                self.app.rel_file = rf or ""
            except Exception as _ex:
                self.printException(_ex, "RepoModeFileList.key_right: setting app.rel_dir/rel_file failed")

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

    def key_s(self, event: events.Key | None = None) -> None:
        """Prompt to save snapshot files for the selected file (older/newer/both)."""
        logger.debug("RepoModeFileList.key_s called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "RepoModeFileList.key_s: event.stop failed")
        try:
            self.key_s_helper(event)
        except Exception as e:
            self.printException(e, "RepoModeFileList.key_s: helper failed")


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
        """Append a commit-row with `text` and attach `commit_hash` metadata."""
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

    def _format_commit_row(self, ts, h: str | None, msg: str) -> str:
        """Return a formatted commit row string for display.

        Centralized so formatting is consistent across preparers.
        """
        try:
            try:
                if hasattr(ts, "strftime"):
                    date_stamp = ts.strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    date_stamp = str(ts)
            except Exception as _ex:
                self.printException(_ex, f"_format_commit_row failed formatting date for {(ts,h,msg)}")
                date_stamp = str(ts)
            short_hash = (h or "")[:HASH_LENGTH]
            return f"{date_stamp} {short_hash} {msg}".strip()
        except Exception as e:
            self.printException(e, "_format_commit_row failed")
            return f"{h or ''} {msg}".strip()

    def _to_history_entries(self, raw_list: list) -> list[dict]:
        """Normalize various backend hash-list formats into HistoryEntry dicts.

        Accepts items produced by GitRepo helpers (tuples like
        `(iso, hash, subject)`) or raw strings/tuples and returns a list of
        dicts with keys: `iso`, `hash`, `subject`, `short_hash`, `meta`.
        This is defensive and will try to preserve as much information as
        possible when inputs vary.
        """
        out: list[dict] = []
        try:
            for item in raw_list or []:
                try:
                    iso = ""
                    h = None
                    subject = ""
                    meta = item

                    # Handle tuple/list forms: prefer (iso, hash, subject)
                    if isinstance(item, (list, tuple)):
                        if len(item) >= 3:
                            iso = item[0]
                            h = item[1]
                            subject = item[2] or ""
                        elif len(item) == 2:
                            iso = item[0]
                            h = item[1]
                            subject = ""
                        elif len(item) == 1:
                            # single-element tuple
                            iso = str(item[0])
                    elif isinstance(item, str):
                        # Try to parse strings like "<iso> <hash> <subject>"
                        parts = item.split(None, 2)
                        if parts:
                            if len(parts) >= 1:
                                iso = parts[0]
                            if len(parts) >= 2:
                                h = parts[1]
                            if len(parts) == 3:
                                subject = parts[2]
                    else:
                        # Fallback: stringify the item
                        iso = str(item)

                    # Normalize iso value (if it's numeric epoch convert to iso)
                    try:
                        if isinstance(iso, (int, float)):
                            iso = self._epoch_to_iso(int(iso))
                        else:
                            # leave as string; if object with strftime use that
                            if hasattr(iso, "strftime"):
                                try:
                                    iso = iso.strftime("%Y-%m-%dT%H:%M:%S")
                                except Exception as e:
                                    self.printException(e, "_to_history_entries: iso.strftime failed")
                                    iso = str(iso)
                            else:
                                iso = str(iso)
                    except Exception as e:
                        self.printException(e, "_to_history_entries: iso normalization failed")
                        iso = str(iso)

                    short_hash = (h or "")[:HASH_LENGTH] if h else ""

                    out.append({"iso": iso, "hash": h, "subject": subject, "short_hash": short_hash, "meta": meta})
                except Exception as e:
                    self.printException(e, "_to_history_entries: processing item failed")
                    continue
        except Exception as e:
            self.printException(e, "_to_history_entries failed")
        return out

    def toggle_check_current(self, idx: int | None = None) -> None:
        """Toggle a single-mark (checked) state on the selected history row.

        Enforces single-mark semantics: marking one row unmarks others.
        """
        try:
            if idx is None:
                idx = self.index or 0
            nodes = self.nodes()
            logger.debug(
                "toggle_check_current: called idx=%r node_count=%d", idx, len(nodes) if nodes is not None else 0
            )
            if not (0 <= idx < len(nodes)):
                return
            # Enforce single-mark semantics: mark the selected item (M ) and
            # unmark all others. If the selected item was already marked, clear it.
            try:
                selected_node = nodes[idx]
                was_marked = getattr(selected_node, "_checked", False)
                logger.debug(
                    "toggle_check_current: selected_idx=%d selected_hash=%r was_marked=%r",
                    idx,
                    getattr(selected_node, "_hash", None),
                    was_marked,
                )
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
                                try:
                                    # Also apply a persistent per-node background so
                                    # the marking remains visible even when
                                    # the widget highlight logic runs.
                                    node.styles.background = "red"
                                    node.styles.color = "white"
                                    node.styles.text_style = "bold"
                                    logger.debug(
                                        "toggle_check_current: applied styles idx=%d hash=%r",
                                        i,
                                        getattr(node, "_hash", None),
                                    )
                                except Exception as e:
                                    self.printException(e, "toggle_check_current: applying node styles failed")
                            else:
                                # Unmarked: two-space prefix (already applied during add), plain style
                                lbl.update(Text(f"  {raw}"))
                                try:
                                    node.styles.background = None
                                    node.styles.color = None
                                    node.styles.text_style = None
                                    logger.debug(
                                        "toggle_check_current: cleared styles idx=%d hash=%r",
                                        i,
                                        getattr(node, "_hash", None),
                                    )
                                except Exception as e:
                                    self.printException(e, "toggle_check_current: clearing node styles failed")
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
        """Alias for `key_m` used to support Shift-M bindings."""
        logger.debug("HistoryListBase.key_M called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_m(event, recursive=True)

    def compute_commit_pair_hashes(self, idx: int | None = None) -> tuple[str | None, str | None]:
        """Compute (prev_hash, curr_hash) pair from the history list selection.

        Returns (prev, curr) where `prev` is the older commit and `curr` is the
        currently-selected commit when available.
        """
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
        """Ensure the history widget has a valid `index` when focused."""
        try:
            if self.index is None:
                # Respect widget-specific minimum index when focusing
                self.index = self._min_index or 0
        except Exception as e:
            self.printException(e, "HistoryListBase.on_focus")

    def on_list_view_highlighted(self, event) -> None:
        """Hook invoked when the history list highlight changes; logs the event."""
        logger.debug("history highlighted: %s", event)

    def watch_history_index(self, old: int | None, new: int | None, node_new) -> None:
        """History-list specific post-highlight hook.

        Compute the selected commit pair and publish `app.current_hash` and
        `app.previous_hash` so other components (file preparers, diffs)
        can rely on them immediately.
        """
        try:
            try:
                self._compute_selected_pair()
            except Exception as e:
                self.printException(e, "watch_history_index: computing selected pair failed")
            logger.debug(
                "watch_history_index: updated app.current_hash=%r app.previous_hash=%r",
                self.app.current_hash,
                self.app.previous_hash,
            )
        except Exception as e:
            self.printException(e, "watch_history_index failed")

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
                    # update app-level hashes for other components
                    try:
                        self.app.current_hash = selected_hash
                        self.app.previous_hash = marked_hash
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

    def _finalize_historylist_prep(
        self, curr_hash: str | None = None, prev_hash: str | None = None, path: str | None = None
    ) -> None:
        """History-specific finalization then call shared common sync.

        This implements history-only behavior (e.g. marking a previously
        checked commit via `toggle_check_current`) and defers app-level
        state synchronization to `_finalize_prep_common`.
        """
        try:
            try:
                if curr_hash:
                    try:
                        self._highlight_match(curr_hash)
                    except Exception as e:
                        self.printException(e, "HistoryListBase._finalize_historylist_prep: _highlight_match failed")
                elif prev_hash:
                    try:
                        if hasattr(self, "toggle_check_current"):
                            for i, node in enumerate(self.nodes()):
                                try:
                                    if getattr(node, "_hash", None) == prev_hash:
                                        try:
                                            logger.debug(
                                                "prepRepoModeHistoryList: invoking toggle_check_current at index=%d for prev_hash=%r",
                                                i,
                                                prev_hash,
                                            )
                                            self.toggle_check_current(i)
                                        except Exception as e:
                                            self.printException(
                                                e,
                                                "HistoryListBase._finalize_historylist_prep: toggle_check_current failed",
                                            )
                                        break
                                except Exception as e:
                                    self.printException(
                                        e, "HistoryListBase._finalize_historylist_prep: checking node failed"
                                    )
                        else:
                            try:
                                self._highlight_top()
                            except Exception as e:
                                self.printException(
                                    e, "HistoryListBase._finalize_historylist_prep: _highlight_top failed"
                                )
                    except Exception as e:
                        self.printException(e, "HistoryListBase._finalize_historylist_prep: locating prev_hash failed")
                else:
                    try:
                        self._highlight_top()
                    except Exception as e:
                        self.printException(e, "HistoryListBase._finalize_historylist_prep: _highlight_top failed")
            except Exception as e:
                self.printException(e, "HistoryListBase._finalize_historylist_prep: highlight step failed")

            try:
                self._finalize_prep_common(curr_hash=curr_hash, prev_hash=prev_hash, path=path)
            except Exception as e:
                self.printException(e, "HistoryListBase._finalize_historylist_prep: _finalize_prep_common failed")
        except Exception as e:
            self.printException(e, "HistoryListBase._finalize_historylist_prep failed")


class FileModeHistoryList(HistoryListBase):
    """History list for a single file's history. Stubbed prep method."""

    def prepFileModeHistoryList(self, path: str, prev_hash: str | None = None, curr_hash: str | None = None) -> None:
        """Prepare the commit history listing for a single file at `path`.

        `prev_hash` and `curr_hash` may be provided to restrict the commit
        range; when omitted the full history is used.
        """
        try:
            logger.debug("prepFileModeHistoryList: path=%r prev_hash=%r curr_hash=%r", path, prev_hash, curr_hash)
            self.clear()
            # If repository available, collect pseudo-entries (MODS/STAGED)
            # and commit history via backend helpers.
            try:
                repo_root = self.app.gitRepo.get_repo_root()
            except Exception as e:
                self.printException(e, "prepFileModeHistoryList: getting repo_root failed")
                repo_root = None
            try:
                # Path inputs are repo-relative; normalize for helpers
                rel_path = os.path.normpath(path)
            except Exception as e:
                self.printException(e, "prepFileModeHistoryList: computing rel_path failed")
                rel_path = path

            pseudo_entries: list[tuple[str, str]] = []
            entries: list[tuple[str, str, str]] = []
            if repo_root:

                pseudo_entries, entries = self._prepFileModeHistoryList_for_git(repo_root, rel_path)

                # render pseudo entries first
                try:
                    # Attach timestamps for MODS/STAGED using centralized helper
                    try:
                        if repo_root and pseudo_entries:
                            full_path = os.path.join(repo_root or "", path)
                            mods_ts, staged_ts = self._compute_pseudo_timestamps(
                                repo_root, mods=[], single_path=full_path
                            )

                            # rewrite pseudo_entries in-place with date prefixes when appropriate
                            new_pseudo: list[tuple[str, str]] = []
                            for status, desc in pseudo_entries:
                                try:
                                    if status == "MODS":
                                        date_part = mods_ts or ""
                                        status_short = "MODS"
                                        raw = desc or "(modified, unstaged)"
                                        # Avoid duplicating the status word if desc already contains it
                                        if raw.upper().startswith(status):
                                            msg = raw[len(status) :].strip()
                                        else:
                                            msg = raw
                                        display = f"{date_part} {status_short[:HASH_LENGTH]} {msg}".strip()
                                        new_pseudo.append((status, display))
                                    elif status == "STAGED":
                                        date_part = staged_ts or ""
                                        status_short = "STAGED"
                                        raw = desc or "(staged, uncommitted)"
                                        if raw.upper().startswith(status):
                                            msg = raw[len(status) :].strip()
                                        else:
                                            msg = raw
                                        display = f"{date_part} {status_short[:HASH_LENGTH]} {msg}".strip()
                                        new_pseudo.append((status, display))
                                    else:
                                        new_pseudo.append((status, desc))
                                except Exception as _ex:
                                    self.printException(_ex, "prepFileModeHistoryList rewriting pseudo entry failed")
                            pseudo_entries = new_pseudo
                    except Exception as _ex:
                        self.printException(_ex, "prepFileModeHistoryList preparing pseudo timestamps failed")

                    for status, desc in pseudo_entries:
                        try:
                            self._add_row(desc, status)
                        except Exception as e:
                            self.printException(e, "prepFileModeHistoryList adding pseudo-row failed")
                except Exception as e:
                    self.printException(e, "prepFileModeHistoryList rendering pseudo entries failed")

                # then render real commit entries
                try:
                    for ts, h, msg in entries:
                        try:
                            # Use centralized formatter for commit rows
                            text = self._format_commit_row(ts, h, msg)
                            self._add_row(text, h)
                        except Exception as e:
                            self.printException(e, "prepFileModeHistoryList parse failed")
                except Exception as e:
                    self.printException(e, "prepFileModeHistoryList rendering commits failed")

            self._populated = True
            try:
                self._finalize_historylist_prep(curr_hash=curr_hash, prev_hash=prev_hash, path=path)
            except Exception as e:
                self.printException(e, "prepFileModeHistoryList: finalize failed")
        except Exception as e:
            self.printException(e, "prepFileModeHistoryList failed")

    def key_s(self, event: events.Key | None = None) -> None:
        """Prompt to save snapshot files for the current file history selection."""
        logger.debug("FileModeHistoryList.key_s called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "FileModeHistoryList.key_s: event.stop failed")
        try:
            self.key_s_helper(event)
        except Exception as e:
            self.printException(e, "FileModeHistoryList.key_s: helper failed")

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
            filename = self.app.rel_file or ""
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
        """Enter-key handler — same behavior as Right: open the file commit-pair diff."""
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
        self,
        repo_path: str | None = None,
        prev_hash: str | None = None,
        curr_hash: str | None = None,
    ) -> None:
        """Prepare the repository-wide commit history view.

        `repo_path` may narrow the view to a subpath; `prev_hash` and
        `curr_hash` may be used to constrain the commit range.
        """
        try:
            logger.debug(
                "prepRepoModeHistoryList: repo_path=%r prev_hash=%r curr_hash=%r",
                repo_path,
                prev_hash,
                curr_hash,
            )
            self.clear()

            # Use GitRepo to obtain a normalized list of hashes (including
            # pseudo-entries like MODS/STAGED). GitRepo centralizes git CLI
            # invocation and caching so prefer its helpers.
            try:
                entries = self.app.gitRepo.getNormalizedHashListComplete() or []
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: gitRepo.getNormalizedHashListComplete failed")
                entries = []

            try:
                # Entries are tuples (iso, hash, subject). Render each as a row.
                for ts_iso, h, subject in entries:
                    try:
                        text = f"{ts_iso} {h[:HASH_LENGTH]} {subject or ''}".strip()
                        self._add_row(text, h)
                    except Exception as e:
                        self.printException(e, "prepRepoModeHistoryList: adding commit row failed")
                        continue
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: rendering entries failed")

            # Mark populated and delegate final highlighting/selection logic
            # to the centralized finalizer which will honor provided hashes.
            try:
                self._populated = True
                try:
                    self._finalize_historylist_prep(curr_hash=curr_hash, prev_hash=prev_hash, path=repo_path)
                except Exception as e:
                    self.printException(e, "prepRepoModeHistoryList: finalize failed")
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: setting populated failed")
        except Exception as e:
            self.printException(e, "prepRepoModeHistoryList failed")

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
                # Prefer the canonical `current_path` for highlight comparisons
                # so repo-mode file rows (which store full paths) match deterministically.
                rd = self.app.rel_dir
                rf = self.app.rel_file
                hf = os.path.join(rd or "", rf) if rf else (rd or "")
                logger.debug(
                    "RepoModeHistoryList.key_right: prev=%r curr=%r highlight=%r",
                    prev_hash,
                    curr_hash,
                    hf,
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
        """Enter-key handler — same behavior as Right: open the commit-pair file list."""
        logger.debug("RepoModeHistoryList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_right(event, recursive=True)


class DiffList(AppBase):
    """List view for showing diffs.

    `prepDiffList` is a stub here; later steps will call `git diff` and
    colorize output. Key handlers toggle colorization and expose actions.
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
        """Prepare and display a diff for `filename` between `prev` and `curr`.

        This builds a diff command via `app.build_diff_cmd`, falls back to
        a metadata summary when no textual diff is present, and renders the
        output into the diff list. `variant_index` selects a diff variant
        from `app.diff_variants` and `go_back` records the return location.
        """
        try:
            logger.debug(
                "DiffList.prepDiffList: filename=%s prev=%s curr=%s variant=%s go_back=%s",
                filename,
                prev,
                curr,
                variant_index,
                go_back,
            )
            # Use the app-level `gitRepo` and build the selected variant
            try:
                gitrepo = self.app.gitRepo
                variant_arg = None
                try:
                    app = self.app
                    if app and hasattr(app, "diff_variants") and 0 <= variant_index < len(app.diff_variants):
                        variant_arg = app.diff_variants[variant_index]
                except Exception as _ex:
                    self.printException(_ex, "prepDiffList: retrieving app.diff_variants failed")
                    variant_arg = None
                variation = [variant_arg] if variant_arg else None
                out_lines = gitrepo.getDiff(filename, prev, curr, variation)
                out = "\n".join(out_lines) if out_lines else ""
            except Exception as e:
                self.printException(e, "prepDiffList: gitRepo.getDiff failed")

            # Save output lines on the object and render via helper
            # Prepend a human-readable header describing the diff context
            try:
                p_short = prev[:HASH_LENGTH] if prev else "None"
                c_short = curr[:HASH_LENGTH] if curr else "None"

                try:
                    variant_arg = None
                    try:
                        app = self.app
                        if app and hasattr(app, "diff_variants") and 0 <= variant_index < len(app.diff_variants):
                            variant_arg = app.diff_variants[variant_index]
                    except Exception as e:
                        self.printException(e, "prepDiffList: retrieving app.diff_variants failed")
                        variant_arg = None
                    vdisp = variant_arg if variant_arg else ""
                    vspace = " " if variant_arg else ""
                    header = f"'Diff{vspace}{vdisp}' for {filename} between {p_short} and {c_short}"
                except Exception as e:
                    self.printException(e, "prepDiffList: building header failed")
                    header = f"Diff for {filename} between {p_short} and {c_short}"
            except Exception as e:
                self.printException(e, "prepDiffList: header preparation failed")
                header = "Diff"
            self.output = [header] + (out.splitlines() if out else [])
            # Ensure the header line is not selectable by setting the
            # minimum selectable index to 1 so navigation skips it.
            try:
                self._min_index = 1
            except Exception as e:
                self.printException(e, "prepDiffList: setting _min_index failed")
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

            try:
                self._finalize_prep_common(curr_hash=curr, prev_hash=prev, path=filename)
            except Exception as e:
                self.printException(e, "prepDiffList: finalize failed")
        except Exception as e:
            self.printException(e, "prepDiffList failed")

    def key_c(self, event: events.Key | None = None) -> None:
        """Toggle colorization of the diff output and re-render."""
        logger.debug("DiffList.key_c called: key=%r index=%r", getattr(event, "key", None), self.index)
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
        logger.debug("DiffList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index)
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
        """Alias for `key_c` (Shift-C)."""
        logger.debug("DiffList.key_C called: key=%r", getattr(event, "key", None))
        return self.key_c(event, recursive=True)

    def _render_output(self) -> None:
        """Clear and render `self.output` honoring `self._colorized`."""
        try:
            self.clear()
            for i, ln in enumerate(self.output or []):
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
                        item = ListItem(Label(Text(ln, style=style)))
                    else:
                        item = ListItem(Label(Text(ln)))
                    # Make the first line (our diff header) unselectable so
                    # navigation/highlight skips it.
                    try:
                        if i == 0:
                            item._selectable = False
                            item._diff_header = True
                    except Exception as _ex:
                        self.printException(_ex, "_render_output: setting header metadata failed")
                    self.append(item)
                except Exception as e:
                    self.printException(e, "_render_output append failed")
        except Exception as e:
            self.printException(e, "_render_output failed")

    def key_d(self, event: events.Key | None = None) -> None:
        """Cycle to the next diff variant and re-run `prepDiffList`."""
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
                # Build repository-relative filename from rel_dir/rel_file
                try:
                    rd = self.app.rel_dir
                    rf = self.app.rel_file
                    filename = os.path.join(rd or "", rf) if rf else (rd or "")
                except Exception as e:
                    self.printException(e, "DiffList.key_d: computing filename failed")
                    filename = ""
                self.prepDiffList(
                    filename,
                    self.app.previous_hash,
                    self.app.current_hash,
                    new_variant,
                    self.go_back,
                )
            except Exception as e:
                self.printException(e, "DiffList.key_d: re-prep failed")
        except Exception as e:
            self.printException(e, "DiffList.key_d failed")

    def key_s(self, event: events.Key | None = None) -> None:
        """Prompt to save snapshot files for the diff's current file."""
        logger.debug("DiffList.key_s called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "DiffList.key_s: event.stop failed")
        try:
            self.key_s_helper(event)
        except Exception as e:
            self.printException(e, "DiffList.key_s: helper failed")

    def key_D(self, event: events.Key | None = None) -> None:
        """Alias for `key_d` (Shift-D)."""
        logger.debug("DiffList.key_D called: key=%r index=%r", getattr(event, "key", None), self.index)
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


HELP_TEXT = """
# gitdiffnavtool help

Overview:
- gitdiffnavtool is a terminal UI for exploring a Git repository: the
    left/right columns show file trees and per-file history, the central
    commit lists show repository history, and the diff column shows patches
    for a selected file/commit pair. It uses the `git` CLI for status and history operations.

Invocation:
- Run `gitdiffnavtool [path]` to open the app for `path` (directory or
    file).
- Run `gitdiffnavtool [-r/--repo-first [--repo-hash hash1] [--repo-hash hash2]] [path]` to open
    the app in repository mode, optionally comparing `hash1` and `hash2`.
- Use `--no-color` to disable colored diffs.

Basic navigation:
- Arrow keys: Up / Down / PageUp / PageDown / Home / End move
    the selection within the focused column.
- Right (or Enter): open/enter the selected row (enter directories,
    open file history or diff depending on focus).
- Left: go back / close / move focus to the previous column.
- `q` (or Ctrl-Q): quit the application.

Global actions:
- `h` or `?`: show this help screen.
- `r`: refresh the current view.
- `s`: prompt to save snapshot files for the current file/hash combination.

Column-specific information and commands:

Left File Column (Files):
- Shows directory tree or file list for the working tree path.
- Right on a directory: enter that directory.
- Right on a tracked file: open the file's history in the right-side
    history column.

Left History Column (File History for left pane):
- Shows commits affecting the file selected in the left file pane.
- Mark a row with `m` to select it as the `prev` commit; navigate to a
    second row and press Right to diff between the two marked rows.
- Right on a row: open the diff for that file between the selected
    commit pair.

Right History Column (Repository History):
- Shows repository-wide commits (newest first). Use this to pick commit
    ranges to inspect repository-wide changes.
- Press `m` on a row to mark it (acts as one side of a commit pair).
- With a commit pair selected, press Right to populate the Right File
    Column with the files changed between those commits.

Right File Column (Files for selected commit-pair or pseudo refs):
- When populated from a repo commit pair you will see per-file status
    markers (A/M/D/U/!) followed by the filename.
- Special pseudo-rows: `MODS` and `STAGED` appear at the top when the
    selected refs are the working tree/index. Expanding `MODS` shows the
    modified (unstaged) files; expanding `STAGED` shows staged changes.
- Right on a file row: open the file-level diff between the selected
    commit pair (or between index and working-tree when using `STAGED`/`MODS`).

Diff Column:
- Shows the textual patch for the current file/commit pair. The first
    line is a one-line header describing the file and the two refs being
    compared and is not selectable.
- Commands when focused:
    - `c`: toggle colorized diffs on/off.


Tips and behavior notes:
- Short commit hashes are shown using the app's `HASH_LENGTH` constant.
- `MODS` lists working-tree modifications (unstaged).
- `STAGED` lists index changes (files that were added (staged) but not committed).
- When diffing between `STAGED` and `MODS` the UI shows the comparison the user
    expects (index vs working-tree).
- The app uses the `git` CLI for its repository operations.
"""

#    - `toggle-color` / `c`: toggle colorized diff output.
#    - `cycle-diff-variant` / `d`: cycle to the next diff variant (e.g. ignore-space-change, patience).
# Command palette (^P):
# - Press Ctrl-P (Textual command palette) to run commands directly. Useful
#    commands to wire up include:
#    - `open-file`, `diff <file> [prev] <curr>`, `file-history <path>`,
#        `goto-commit <hash>`, `toggle-color`, `next-hunk`, `prev-hunk`,
#        `stage <path>`, `unstage <path>`, `refresh`.


class HelpList(AppBase):
    """Renders help text as list rows and allows restoring previous state."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.highlight_bg_style = HIGHLIGHT_HELP_BG

    def prepHelp(self) -> None:
        """Populate the help list with rendered Markdown blocks.

        Splits the help text into paragraph blocks and appends each as a
        separate `ListItem` so the ListView can provide natural scrolling.
        """
        try:
            logger.debug("prepHelp: invoked")
            self.clear()
            try:
                # Split help text into paragraph/block chunks so each block
                # is its own ListItem. This preserves Markdown formatting
                # while allowing the ListView to provide scrolling behavior.
                blocks = re.split(r"\n\s*\n", HELP_TEXT.strip())
                sep = None
                for i, blk in enumerate(blocks):
                    if not blk:
                        continue
                    try:
                        # Add a spacer row between each block to provide visual
                        # separation when rendered in the ListView.
                        if sep is not None:
                            self.append(ListItem(Label(Text(sep))))
                        else:
                            sep = ""
                        # Render each block using Markdown; allow the renderer
                        # to determine spacing/formatting (including H1).
                        self.append(ListItem(Label(Markdown(blk))))
                    except Exception as e:
                        self.printException(e, "prepHelp append failed for Markdown block")
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
        """Return from the help view to the previously-saved app state."""
        logger.debug("HelpList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
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


class GitHistoryNavTool(AppException, App):
    """Main Textual application wiring the lists together.

    It composes the previously defined widgets, mounts a header/footer,
    and provides simple state save/restore stubs.
    """

    CSS = INLINE_CSS

    def __init__(
        self,
        gitRepo: GitRepo,
        rel_dir: str | None,
        rel_file: str | None,
        repo_first: bool,
        repo_hashes: list,
        no_color: bool,
        **kwargs,
    ):
        """
        Create the textual app.

        Parameters contract:
        - `rel_dir`: a repository-relative directory path (relative to the
          repository root). May be an empty string or None to indicate the
          repository root itself. Must NOT be an absolute filesystem path.
        - `rel_file`: a filename relative to `rel_dir`. Must be a basename
            (no path separators or subdirectories). May be an empty string or
            None to indicate no file selection. Must NOT be an absolute
            filesystem path.

        The application and preparers expect only repository-relative paths in
        their state; absolute/full filesystem paths are constructed only when
        performing filesystem or git calls using `GitRepo.full_path_for()`.
        """
        # Accept CLI options here so the app can inspect them during mount
        super().__init__(**kwargs)
        self.gitRepo = gitRepo
        # Record rel_dir/rel_file and compute canonical self.path for
        # backward compatibility with existing code paths.
        # Normalize and validate inputs per the documented contract
        self.rel_dir = os.path.normpath(rel_dir) if rel_dir else ""
        # Normalize but reject any path separators in rel_file immediately.
        if rel_file:
            # Reject any rel_file that is not a basename (no subpath)
            if os.path.basename(rel_file) != rel_file:
                raise ValueError("GitHistoryNavTool.__init__: rel_file must be a basename (no subpath)")
            # Assign the validated basename directly (no normalization needed)
            self.rel_file = rel_file
        else:
            self.rel_file = ""

        # Normalize `.` to empty string for rel_dir
        if self.rel_dir == ".":
            self.rel_dir = ""

        # Application state uses only `rel_dir` and `rel_file`.
        # Do not maintain `self.path` to avoid multiple source-of-truth values.

        self.no_color = no_color
        self.repo_first = repo_first
        # optional repo hash initialization (list of 1 or 2 hashes)
        # Normalize repo_hashes to a list (avoid mutable default)
        self.repo_hashes = repo_hashes or []
        # placeholders for runtime state
        # `repo_root` is provided by main and should not be modified further.
        # Set the application title to include the repository path
        self.title = f"GitHistoryNavTool ({self.gitRepo.get_repo_root()})"
        self._saved_state = None
        self._current_layout = None
        # Track current focus selector for save/restore; initialize here
        self._current_focus = None
        # Track the currently-selected and previous commit hashes
        self.current_hash = None
        self.previous_hash = None

        # Optional diff variant arguments indexed by variant_index.
        # index 0 -> None (no extra arg), 1 -> ignore-space-change, 2 -> patience algorithm
        self.diff_variants: list[Optional[str]] = [None, "--ignore-space-change", "--diff-algorithm=patience"]

    def compose(self):
        """Yield the canonical six-column layout widgets for the app.

        The method composes header, six content columns (files/history/diff/help),
        and the footer label used by `change_footer`.
        """
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
        """Resolve widget references and perform initial preparatory actions.

        This should not perform repository discovery; `main()` handles that
        and passes `repo_root` into the app constructor.
        """
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

            # Ensure help content is prepared so help is immediately available
            if self.help_list is not None:
                try:
                    self.help_list.prepHelp()
                except Exception as e:
                    self.printException(e, "on_mount: prepHelp failed")

            # Populate the canonical left lists and set focus so key handlers
            # and highlight behavior work immediately in both modes.
            try:
                if not self.repo_first:
                    try:
                        # Compute an initial repository-relative `rel` for preparers
                        # using the canonical rel_dir/rel_file pair.
                        try:
                            if self.rel_file:
                                rel = os.path.join(self.rel_dir or "", self.rel_file)
                            else:
                                rel = self.rel_dir or ""
                        except Exception as e:
                            self.printException(e, "preparer: computing rel failed")
                            rel = self.rel_dir or ""

                        # Resolve a full filesystem path only to test whether
                        # the repo-relative path is a directory or not.
                        if rel:
                            full_candidate = self.gitRepo.full_path_for(os.path.dirname(rel), os.path.basename(rel))
                        else:
                            full_candidate = self.gitRepo.get_repo_root()

                        if os.path.isdir(full_candidate):
                            rdir = rel
                            rpath = None
                        else:
                            rdir = os.path.dirname(rel) or ""
                            rpath = os.path.basename(rel)
                        try:
                            self.file_mode_file_list.prepFileModeFileList(rdir, rpath)
                        except Exception as _ex:
                            self.printException(_ex, "on_mount: prepFileModeFileList failed")
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
                        # Normalize CLI-provided repo hashes (first -> curr, second -> prev)
                        rh = self.repo_hashes or []
                        prev = None
                        curr = None
                        if rh:
                            curr = rh[0]
                            if len(rh) > 1:
                                prev = rh[1]

                        # Call preparer once with any provided hashes so it may
                        # highlight/mark the requested commits during prep.
                        try:
                            # Compute repository-relative `rel` and only resolve
                            # an absolute path when needed for filesystem checks.
                            try:
                                if self.rel_file:
                                    rel = os.path.join(self.rel_dir or "", self.rel_file)
                                else:
                                    rel = self.rel_dir or ""
                            except Exception as e:
                                self.printException(e, "preparer: computing rel failed")
                                rel = self.rel_dir or ""

                            if rel:
                                ip = self.gitRepo.full_path_for(os.path.dirname(rel), os.path.basename(rel))
                            else:
                                ip = self.gitRepo.get_repo_root()

                            if os.path.isdir(ip):
                                rdir = rel
                                rpath = None
                            else:
                                rdir = os.path.dirname(rel) or ""
                                rpath = os.path.basename(rel)

                            try:
                                self.repo_mode_history_list.prepRepoModeHistoryList(
                                    repo_path=rel, prev_hash=prev, curr_hash=curr
                                )
                            except Exception as _ex:
                                self.printException(_ex, "on_mount: prepRepoModeHistoryList failed")
                        except Exception as e:
                            self.printException(e, "on_mount: prepRepoModeHistoryList failed")

                        # If starting in repo-first mode and hashes were given,
                        # populate the repo file list so the right column shows files.
                        if curr is not None or prev is not None:
                            try:
                                self.repo_mode_file_list.prepRepoModeFileList(prev, curr)
                            except Exception as e:
                                self.printException(e, "preparing repo mode file list failed")
                            # Centralize layout/focus/footer handling via change_state.
                            try:
                                self.change_state("history_file", f"#{RIGHT_FILE_LIST_ID}", RIGHT_FILE_FOOTER)
                            except Exception as e:
                                self.printException(e, "on_mount: change_state for history_fullscreen failed")
                        else:
                            # Centralize layout/focus/footer handling via change_state.
                            try:
                                self.change_state("history_fullscreen", f"#{LEFT_HISTORY_LIST_ID}", LEFT_HISTORY_FOOTER)
                            except Exception as e:
                                self.printException(e, "on_mount: change_state for history_fullscreen failed")
                    except Exception as e:
                        self.printException(e, "on_mount: repo-first initialization failed")

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
        """Alias for `key_h` (uppercase H)."""
        logger.debug("GitHistoryNavTool.key_H called: key=%r", getattr(event, "key", None))
        return self.key_h(event, recursive=True)

    def key_question(self, event: events.Key | None = None) -> None:
        """Handle terminal mappings where '?' is reported as 'question' by delegating to help."""
        logger.debug("GitHistoryNavTool.key_question called: key=%r", getattr(event, "key", None))
        return self.key_h(event, recursive=True)

    def key_r(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Global revert: re-run preparers for visible HistoryList and FileList widgets.

        This re-executes the prep methods for visible file/history widgets using
        the current app state (hashes and path) to restore highlights. It does
        not change which columns are visible or which widget has focus.
        """
        logger.debug("GitHistoryNavTool.key_r called: key=%r", getattr(event, "key", None))
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_r: event.stop failed")

            # Local references to avoid repeated attribute lookups
            app = self
            prev = app.previous_hash
            curr = app.current_hash
            # Build repo-relative path from rel_dir/rel_file; default to '.'
            try:
                if app.rel_file:
                    path = os.path.join(app.rel_dir or "", app.rel_file)
                else:
                    path = app.rel_dir or "."
            except Exception as e:
                self.printException(e, "key_r: computing path failed")
                path = "."

            # Helper to decide visibility (styles.display == "none" means hidden)
            def _is_visible(widget) -> bool:
                try:
                    disp = getattr(getattr(widget, "styles", None), "display", None)
                    return not (disp == "none")
                except Exception as e:
                    self.printException(e, "key_r: _is_visible failed")
                    return True

            # Re-run file-mode file list preparer if visible
            try:
                if hasattr(app, "file_mode_file_list") and _is_visible(app.file_mode_file_list):
                    try:
                        gitrepo = app.gitRepo
                        ip = path
                        root = gitrepo.get_repo_root()
                        if ip == root:
                            rel = ""
                        elif ip.startswith(root + os.sep):
                            rel = ip[len(root) + 1 :]
                        else:
                            rel = os.path.relpath(ip, root)
                        if os.path.isdir(ip):
                            rdir = rel
                            rpath = None
                        else:
                            rdir = os.path.dirname(rel) or ""
                            rpath = os.path.basename(rel)
                        app.file_mode_file_list.prepFileModeFileList(rdir, rpath)
                    except Exception as e:
                        self.printException(e, "key_r: prepFileModeFileList failed")
            except Exception as e:
                self.printException(e, "key_r: checking file_mode_file_list failed")

            # Re-run repo-mode file list preparer if visible
            try:
                if hasattr(app, "repo_mode_file_list") and _is_visible(app.repo_mode_file_list):
                    try:
                        app.repo_mode_file_list.prepRepoModeFileList(prev, curr)
                    except Exception as e:
                        self.printException(e, "key_r: prepRepoModeFileList failed")
            except Exception as e:
                self.printException(e, "key_r: checking repo_mode_file_list failed")

            # Re-run left repo history if visible
            try:
                if hasattr(app, "repo_mode_history_list") and _is_visible(app.repo_mode_history_list):
                    try:
                        app.repo_mode_history_list.prepRepoModeHistoryList(
                            repo_path=path, prev_hash=prev, curr_hash=curr
                        )
                    except Exception as e:
                        self.printException(e, "key_r: prepRepoModeHistoryList failed")
            except Exception as e:
                self.printException(e, "key_r: checking repo_mode_history_list failed")

            # Re-run right file history (file-mode history) if visible
            try:
                if hasattr(app, "file_mode_history_list") and _is_visible(app.file_mode_history_list):
                    try:
                        app.file_mode_history_list.prepFileModeHistoryList(path=path, prev_hash=prev, curr_hash=curr)
                    except Exception as e:
                        self.printException(e, "key_r: prepFileModeHistoryList failed")
            except Exception as e:
                self.printException(e, "key_r: checking file_mode_history_list failed")

        except Exception as e:
            self.printException(e, "key_r failed")

    def key_R(self, event: events.Key | None = None) -> None:
        """Alias for `key_r` (Shift-R)."""
        logger.debug("GitHistoryNavTool.key_R called: key=%r", getattr(event, "key", None))
        return self.key_r(event, recursive=True)

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
        """Delegate to the app-level `GitRepo` to construct a git diff argv list.

        Keeping `git` command construction inside `GitRepo` ensures all
        direct `git` invocations remain within that class.
        """
        return self.gitRepo.build_diff_cmd(filename, prev, curr, variant_index)

    def change_layout(self, newlayout: str) -> None:
        """Change column layout using a named layout."""
        try:
            logger.debug(f"change_layout: newlayout={newlayout}")
            if newlayout == "file_fullscreen":
                self._apply_column_layout(100, 0, 0, 0, 0, 0)
            elif newlayout == "history_fullscreen":
                self._apply_column_layout(0, 100, 0, 0, 0, 0)
            elif newlayout == "file_history":
                self._apply_column_layout(15, 0, 85, 0, 0, 0)
            elif newlayout == "history_file":
                self._apply_column_layout(0, 15, 0, 85, 0, 0)
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
                                    logger.debug(
                                        "change_focus:%d: forcing gray border for %s (before=%r)",
                                        inspect.currentframe().f_lineno,
                                        cname,
                                        before,
                                    )
                                    w.styles.border = ("solid", "gray")
                                    try:
                                        readback = getattr(w.styles, "border", None)
                                    except Exception as _ex:
                                        printException(_ex)
                                        readback = "<unavailable>"
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

                        try:
                            logger.debug(
                                "change_focus:%d: calling set_focus on widget=%r key=%r",
                                inspect.currentframe().f_lineno,
                                type(widget).__name__ if widget is not None else None,
                                key,
                            )
                            self.set_focus(widget)
                        except Exception as e:
                            self.printException(e, f"could not set focus to widget for {target}")
                            # Fallback: resolve widget by id and call set_focus
                            try:
                                logger.debug(
                                    "change_focus:%d: attempting widget.focus() fallback for key=%r",
                                    inspect.currentframe().f_lineno,
                                    key,
                                )
                                widget.focus()
                            except Exception as e:
                                self.printException(e, f"could not fallback focus to widget for {target}")

                        # Now set the new focused widget's border to white
                        try:
                            # read current focused widget border safely
                            try:
                                cur_border = getattr(widget.styles, "border", None)
                            except Exception as _ex:
                                printException(_ex)
                                cur_border = "<unavailable>"
                            logger.debug(
                                "change_focus:%d: focused widget before set border=%r key=%r",
                                inspect.currentframe().f_lineno,
                                cur_border,
                                key,
                            )
                            logger.debug(
                                "change_focus:%d: setting focused widget.styles.border -> %r for key=%r",
                                inspect.currentframe().f_lineno,
                                ("solid", "white"),
                                key,
                            )
                            widget.styles.border = ("solid", "white")
                            try:
                                readback = getattr(widget.styles, "border", None)
                            except Exception as _ex:
                                printException(_ex)
                                readback = "<unavailable>"
                            logger.debug(
                                "change_focus:%d: focused widget.styles.border readback=%r key=%r",
                                inspect.currentframe().f_lineno,
                                readback,
                                key,
                            )
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
                                    logger.debug(
                                        "change_focus:%d: setting fallback widget.styles.border -> %r for resolved id=%r",
                                        inspect.currentframe().f_lineno,
                                        ("solid", "white"),
                                        key,
                                    )
                                    w.styles.border = ("solid", "white")
                            except Exception as _ex:
                                printException(_ex)

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
                    "app state: rel_dir=<%r> rel_file=<%r> current_hash=<%r> previous_hash=<%r>",
                    self.rel_dir,
                    self.rel_file,
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

    def key_w(self, event: events.Key | None = None) -> None:
        """Toggle the paired layout for the current layout (invoked by 'w')."""
        logger.debug("GitHistoryNavTool.key_w called: key=%r", getattr(event, "key", None))
        return self.toggle(self._current_layout, event)

    def key_W(self, event: events.Key | None = None) -> None:
        """Alias for `key_w` (Shift-W)."""
        logger.debug("GitHistoryNavTool.key_W called: key=%r", getattr(event, "key", None))
        return self.key_w(event, recursive=True)

    # Per-layout toggle implementations. These prepare lists and switch
    # layouts in pairs so the `s` key toggles between related views.
    def toggle_file_fullscreen(self) -> None:
        """Toggle between file fullscreen and the paired history fullscreen view.

        Prepares the paired view content so the transition feels immediate.
        """
        # When toggling from file_fullscreen, populate the repo history
        # so the paired history_fullscreen view is ready.
        try:
            try:
                # Determine a repository-relative path to request from the preparer
                rd = self.rel_dir
                rf = self.rel_file
                if rf:
                    repo_path = os.path.join(rd or "", rf)
                else:
                    repo_path = rd or ""
            except Exception as e:
                self.printException(e, "toggle_file_fullscreen: computing repo_path failed")
                repo_path = ""
            try:
                self.repo_mode_history_list.prepRepoModeHistoryList(repo_path=repo_path)
            except Exception as e:
                self.printException(e, "toggle_file_fullscreen prepRepoModeHistoryList failed")
        except Exception as e:
            self.printException(e, "toggle_file_fullscreen unexpected failure")
        try:
            self.change_state("history_fullscreen", f"#{LEFT_HISTORY_LIST_ID}", LEFT_HISTORY_FOOTER)
        except Exception as e:
            self.printException(e, "toggle_file_fullscreen change_state failed")

    def toggle_history_fullscreen(self) -> None:
        """Toggle between history fullscreen and the paired file fullscreen view.

        Prepares the file list and sets focus/footers appropriately.
        """
        try:
            # Determine a highlight filename: prefer rel_file when present.
            # Use empty string to indicate no highlight rather than None.
            hl = self.rel_file or ""
            gitrepo = self.gitRepo
            init_path = os.path.join(gitrepo.get_repo_root(), self.rel_dir) if self.rel_dir else gitrepo.get_repo_root()
            try:
                root = gitrepo.get_repo_root()
                ip = init_path
                if ip == root:
                    rel = ""
                elif ip.startswith(root + os.sep):
                    rel = ip[len(root) + 1 :]
                else:
                    rel = os.path.relpath(ip, root)
                if os.path.isdir(ip):
                    rdir = rel
                    rpath = None
                else:
                    rdir = os.path.dirname(rel) or ""
                    rpath = os.path.basename(rel)
                self.file_mode_file_list.prepFileModeFileList(rdir, rpath)
            except Exception as _ex:
                self.printException(_ex, "toggle_history_fullscreen prepFileModeFileList failed")
        except Exception as e:
            self.printException(e, "toggle_history_fullscreen prepFileModeFileList failed")
        try:
            self.change_state("file_fullscreen", f"#{LEFT_FILE_LIST_ID}", LEFT_FILE_FOOTER)
        except Exception as e:
            self.printException(e, "toggle_history_fullscreen change_state failed")

    def toggle_file_history(self) -> None:
        """Switch to a history view for the current file and prepare paired file list.

        Reads authoritative commit hashes after preparing the repo history and
        then prepares the repo file list highlighting the canonical filename.
        """
        # Save transient values (use repo-relative rel_dir/rel_file)
        saved_path = os.path.join(self.rel_dir or "", self.rel_file) if self.rel_file else (self.rel_dir or "")
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
            try:
                rd = self.rel_dir
                rf = self.rel_file
                if rf:
                    repo_path = os.path.join(rd or "", rf)
                else:
                    repo_path = rd or ""
            except Exception as e:
                self.printException(e, "toggle_file_history: computing repo_path failed")
                repo_path = ""

            self.repo_mode_history_list.prepRepoModeHistoryList(
                repo_path=repo_path, prev_hash=self.previous_hash, curr_hash=self.current_hash
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

            # Pass the repo-relative highlight so matching uses repository-relative rows.
            hl = saved_path
            logger.debug("toggle_file_history: passing highlight=%r", hl)
            self.repo_mode_file_list.prepRepoModeFileList(use_prev, use_curr, highlight_filename=hl)
        except Exception as e:
            self.printException(e, "toggle_file_history preparing repo file list failed")
        try:
            self.change_state("history_file", f"#{RIGHT_FILE_LIST_ID}", RIGHT_FILE_FOOTER)
        except Exception as e:
            self.printException(e, "toggle_file_history change_state failed")

    def toggle_history_file(self) -> None:
        """Switch to file-history layout for the current file and prepare lists.

        Prepares the right file list and the file's history preparer, then
        switches the UI to the paired layout.
        """
        # Save transient values (use repo-relative rel_dir/rel_file)
        saved_path = os.path.join(self.rel_dir or "", self.rel_file) if self.rel_file else (self.rel_dir or "")
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
            gitrepo = self.gitRepo
            try:
                ip = saved_path or "."
                root = gitrepo.get_repo_root()
                if ip == root:
                    rel = ""
                elif ip.startswith(root + os.sep):
                    rel = ip[len(root) + 1 :]
                else:
                    rel = os.path.relpath(ip, root)
                if os.path.isdir(ip):
                    rdir = rel
                    rpath = None
                else:
                    rdir = os.path.dirname(rel) or ""
                    rpath = os.path.basename(rel)
                self.file_mode_file_list.prepFileModeFileList(rdir, rpath)
            except Exception as _ex:
                self.printException(_ex, "toggle_history_file prepFileModeFileList failed")
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
        """Toggle to a file-history diff in the right diff column.

        Prepares file-history state then shows the diff and updates `diff_list.go_back`.
        """
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
        """Toggle to a history-file diff view and set appropriate go-back state."""
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
        """If a saved diff layout exists, toggle back to it via recursive dispatch."""
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
    Discover the repository worktree root by deferring to `GitRepo.resolve_repo_top`.
    Exits the program with an error message if no repository is found.
    """
    try:
        start = os.path.abspath(start_path or os.getcwd())
    except Exception as _ex:
        printException(_ex)
        start = os.getcwd()
    topo, err = GitRepo.resolve_repo_top(start, raise_on_missing=False)
    if topo:
        try:
            return os.path.normpath(topo)
        except Exception as _ex:
            printException(_ex)
            return topo
    sys.exit(f"Not a git repository starting at {start}")


def main(argv: Optional[list[str]] = None) -> int:
    """Command-line entry point for gitdiffnavtool.

    Parses CLI arguments, locates the repository worktree, configures
    logging, and launches the `GitHistoryNavTool` Textual application.
    Returns process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(prog="gitdiffnavtool.py")
    parser.add_argument("path", nargs="+", help="one or more directories or files to open")
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
        "-R",
        "--repo-hash",
        dest="repo_hash",
        action="append",
        metavar="HASH",
        help="specify a repo commit hash; may be provided up to two times (implies --repo-first)",
    )
    args = parser.parse_args(argv)

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

        # Allocate the shared `GitRepo` instance here and compute the
        # repository-relative `relpath` for the provided path. The app will
        # receive the `gitRepo` instance so helpers can call into it.
        raw_path = args.path[0] if args.path else "."
        try:
            gitrepo = GitRepo(raw_path)
        except Exception as e:
            printException(e, f"repository discovery failed for {raw_path}")
            sys.exit(f"Not a git repository: {raw_path}")
        logger.debug("Discovered repository worktree root: %s", gitrepo.get_repo_root())

        # Compute repository-relative directory/file for the provided path.
        try:
            rel_dir, rel_file = gitrepo.cwd_plus_path_to_reldir_relfile(raw_path)
        except Exception as e:
            printException(e, f"Not a git repository or invalid path: {raw_path}")
            sys.exit(f"Not a git repository: {raw_path}")

        # Compute a validated absolute candidate path using repo-relative
        # components. Use `abs_path_for` which validates containment.
        try:
            if rel_dir == "" and rel_file == "":
                full_candidate = gitrepo.get_repo_root()
            else:
                full_candidate = gitrepo.abs_path_for(rel_dir, rel_file)
        except Exception as e:
            printException(e, f"Not a git repository or invalid path: {raw_path}")
            sys.exit(f"Not a git repository: {raw_path}")

        # Sanity-check the prepared rel_dir/rel_file before passing to the app
        if rel_dir and os.path.isabs(rel_dir):
            sys.exit(f"internal error: rel_dir must be repository-relative: {rel_dir}")
        if rel_file and os.path.isabs(rel_file):
            sys.exit(f"internal error: rel_file must be repository-relative: {rel_file}")

        logger.debug(
            "Starting GitHistoryNavTool; raw_path=%s repo_root=%s rel_dir=%r rel_file=%r",
            raw_path,
            gitrepo.get_repo_root(),
            rel_dir,
            rel_file,
        )
        app = GitHistoryNavTool(
            gitRepo=gitrepo,
            rel_dir=rel_dir,
            rel_file=rel_file,
            repo_first=args.repo_first,
            repo_hashes=repo_hashes,
            no_color=args.no_color,
        )
        # Run the textual app (blocks until exit)
        app.run()
        return 0
    except Exception as e:
        printException(e, "fatal error running GitHistoryNavTool")
        return 2


if __name__ == "__main__":
    sys.exit(main())
