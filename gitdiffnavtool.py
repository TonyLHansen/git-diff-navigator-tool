#!/usr/bin/env python3
"""
Git Diff Navigator Tool TUI
"""
# pylint: disable=too-many-lines

from __future__ import annotations

import argparse
import datetime
import logging
import os
import re
import subprocess
import sys
import traceback
import time
from typing import Optional
from rich.text import Text
from rich.align import Align
from rich.markdown import Markdown
import inspect

import pygit2

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual import events
from textual.widgets import (
    Static,
    Header,
    Footer,
    ListView,
    ListItem,
    Label,
)
from textual.css.query import NoMatches

# Set up logging to help debug key event issues (currently disabled)
# Uncomment the basicConfig line below to enable logging to tmp/gitdiff_debug.log
DOLOGGING = True
if DOLOGGING:
    logging.basicConfig(
        filename="tmp/debug.log",
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    # Reduce verbosity from markdown-it and submodules (noisy in debug logs)
    try:
        logging.getLogger("markdown_it").setLevel(logging.WARNING)
    except Exception:
        # Best-effort: don't fail if logger config isn't available
        pass
logger = logging.getLogger(__name__)


def get_caller_short(limit: int = 6, maxlen: int = 400) -> str:
    """
    Return a short single-line caller stack suitable for debug logging.

    Collapses newlines to ' | ' and truncates to `maxlen`. Returns an
    empty string on any error to avoid interfering with normal execution.
    """
    try:
        s = "".join(traceback.format_stack(limit=limit))
        return s.replace("\n", " | ")[:maxlen]
    except Exception:
        return ""


class AppBase(ListView):
    """
    A base class for all of our other application Base classes.
    It provides common functionality that everyone needs.
    """

    def printException(self, e, msg=None):  # AppBase
        """Print a message, the error information and a stacktrace"""
        className = type(self).__name__
        funcName = sys._getframe(1).f_code.co_name
        msg = msg if msg else "???"
        logger.warning(f"WARNING: {className}.{funcName} ({str(e)}): {msg}")
        logger.warning(traceback.format_exc())
        try:
            # Prefer formatting the traceback attached to the exception instance
            tb = "".join(traceback.format_exception(type(e), e, getattr(e, "__traceback__", None)))
            logger.warning(tb)
        except Exception:
            pass

    def __init__(self, *args, **kwargs):
        """
        Initialize common fallback attributes so direct access is safe.

        We set small defaults for private attributes this code frequently
        reads via `getattr` so callers can use direct attribute access
        without risking AttributeError during early lifecycle phases.
        """
        super().__init__(*args, **kwargs)
        self._min_index = 0
        self._populated = False
        # Common attributes that may be referenced before framework wiring
        # ensures they're present. Setting them here lets callers use
        # direct attribute access aggressively without AttributeError.
        # Note: do NOT assign to `self.app` — Textual provides a read-only
        # `app` property on widgets. The framework will supply it at runtime.
        self._filename = None
        self.current_prev_sha = None
        self.current_commit_sha = None
        self.current_diff_file = None


    def text_of(self, node) -> str:  # AppBase
        """
        Extract visible text from a ListItem node's Label/renderable.

        This centralizes the logic used by history lists to parse the
        display text for commits so both FileMode and RepoMode history
        handlers can reuse it.
        """
        try:
            raw = getattr(node, "_raw_text", None)
            if raw is not None:
                return raw
            lbl = node.query_one(Label)
            if hasattr(lbl, "text"):
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

    def on_key(self, event: events.Key) -> bool:  # AppBase
        """
        Handle common navigation keys for ListView-based widgets.

        Returns True when the key was handled and should not be processed
        further by subclass handlers.
        """
        stop_called = False

        try:
            key = event.key
            logger.debug("AppBase.on_key: key=%s event_id=%s", key, id(event))

            # Normalize quit key to lowercase so app-level handler can see it
            if key and key.lower() == "q":
                try:
                    event.key = key.lower()
                except Exception as e:
                    self.printException(e)

                return True

            if key == "up":
                try:
                    try:
                        event.stop()
                        stop_called = True
                    except Exception as e:
                        self.printException(e)
                    min_idx = self._min_index or 0
                except Exception as e:
                    self.printException(e)
                    min_idx = 0
                cur = self.index
                if cur is None:
                    try:
                        self.index = min_idx
                    except Exception as e:
                        self.printException(e)
                    return True
                try:
                    if cur <= min_idx:
                        return True
                    self.action_cursor_up()
                except Exception as e:
                    self.printException(e)
                return True

            if key == "down":
                try:
                    try:
                        event.stop()
                        stop_called = True
                    except Exception as e:
                        self.printException(e)

                    self.action_cursor_down()
                except Exception as e:
                    self.printException(e)
                return True

            # Handle PageUp/PageDown for ListView-based widgets
            if key in ("pageup", "pagedown"):
                try:
                    try:
                        try:
                            event.stop()
                            stop_called = True
                        except Exception as e:
                            self.printException(e)
                    except Exception as e:
                        self.printException(e)

                    nodes = self._nodes
                    if not nodes:
                        return True

                    current_index = self.index if self.index is not None else 0
                    visible_height = 0
                    try:
                        region = self.scrollable_content_region
                        if region is None:
                            raise AttributeError("no scrollable_content_region")
                        visible_height = int(getattr(region, "height", 10))
                    except Exception as e:
                        self.printException(e)
                        # fallback to a reasonable page size when not available
                        visible_height = 10
                    page_size = max(1, visible_height // 2)

                    # Pagedown: move forward but do not exceed last index
                    if key == "pagedown":
                        new_index = min(current_index + page_size, len(nodes) - 1)

                    # Pageup: move backward but do not go above the minimum selectable index
                    elif key == "pageup":
                        min_idx = self._min_index or 0
                        new_index = max(current_index - page_size, min_idx)

                    try:
                        self.call_after_refresh(lambda: setattr(self, "index", new_index))
                    except Exception as e:
                        self.printException(e)
                        try:
                            self.index = new_index
                        except Exception as e:
                            self.printException(e, "setting index for pageup")
                    return True
                except Exception as e:
                    self.printException(e, "AppBase.page navigation failure")
                return True

            # Handle Home/End to jump to first/last selectable item
            if key == "home":
                try:
                    try:
                        event.stop()
                        stop_called = True
                    except Exception as e:
                        self.printException(e)
                except Exception as e:
                    self.printException(e)
                nodes = self._nodes
                if not nodes:
                    return True

                min_idx = self._min_index or 0
                try:
                    self.call_after_refresh(lambda: setattr(self, "index", min_idx))
                except Exception as e:
                    self.printException(e)
                    try:
                        self.index = min_idx
                    except Exception as e:
                        self.printException(e, "setting index for home key")
                return True

            if key == "end":
                try:
                    try:
                        event.stop()
                        stop_called = True
                    except Exception as e:
                        self.printException(e)
                except Exception as e:
                    self.printException(e)
                nodes = self._nodes
                if not nodes:
                    return True

                last_idx = max(0, len(nodes) - 1)
                try:
                    self.call_after_refresh(lambda: setattr(self, "index", last_idx))
                except Exception as e:
                    self.printException(e)
                    try:
                        self.index = last_idx
                    except Exception as e:
                        self.printException(e, "setting index for end key")
                return True

            # these keys are handled at the App level because we have
            # key_left/right/enter in the subclasses
            if key in ("left", "right", "enter"):
                try:
                    try:
                        event.stop()
                        stop_called = True
                    except Exception as e:
                        self.printException(e)
                except Exception as e:
                    self.printException(e)
                return True

            # Not handled here: offer subclass a chance to handle additional keys
            handled = False
            try:
                handled = self.more_keys(event)
            except Exception as e:
                self.printException(e)
                handled = False
            if handled:
                return True
            return False

        except Exception as e:
            self.printException(e, "AppBase.on_key outer failure")
            return False
        finally:
            logger.debug(
                "AppBase.on_key EXIT key=%s event_id=%s stop_called=%s",
                locals().get("key", None),
                id(event) if "event" in locals() else None,
                stop_called,
            )

    def more_keys(self, event: events.Key) -> bool:  # AppBase
        """Per-mode file list key hook.
        Return True when the key was handled, False otherwise.
        """
        return True

    def prep_and_show_diff(
        self,
        filename: str,
        prev: Optional[str],
        curr: Optional[str],
        diff_widget: str,
        layout: str,
    ) -> None:  # AppBase
        """
        Populate the shared Diff column and make it visible using `layout`.

        Caller MUST pass the diff widget instance and the explicit
        layout name to use (e.g. 'file_history_diff' or 'history_file_diff').
        """
        try:
            try:
                diff_widget.prepDiffList(filename, prev, curr)
            except Exception as exc:
                try:
                    self.app.push_screen(_TBDModal(str(exc)))
                except Exception as e:
                    self.printException(e, "could not push TBDModal for diff-fullscreen error")

            # Use the explicit layout provided by the caller.
            try:
                logger.debug(
                    f">>>> change_state({layout}) - AppBase.prep_and_show_diff: for diff widget id={diff_widget.id}"
                )
                self.app.change_state(layout, f"#{diff_widget.id}", self.app.footer_diff3)
            except Exception as e:
                self.printException(e, "error ensuring layout/focus for diff")
        except Exception as e:
            self.printException(e)


class FileListBase(AppBase):
    """
    A ListView showing directory contents. Directories have a blue background.

    Navigation: arrow keys (up/down) move selection automatically because ListView
    handles keyboard navigation. The app focuses this widget on mount.
    """

    # NOTE: `set_path` intentionally removed from FileListBase. Subclasses
    # should implement `set_path` or an equivalent preparatory method such
    # as `prepFileModeFileList` so that different modes (file vs repo) can
    # populate lists with mode-specific behavior.

    def on_focus(self, event: events.Focus) -> None:  # FileListBase
        """When Files column receives focus, do selection/index handling"""

        # Ensure selection is at or below the minimum selectable index
        # (e.g. skip the Key legend row). Use call_after_refresh so the
        # DOM is stable before mutating the selection.
        try:
            min_idx = self._min_index or 0
            cur = self.index
            if cur is None or cur < min_idx:
                # No valid current index: schedule setting to the minimum selectable index
                self.call_after_refresh(lambda: setattr(self, "index", min_idx))
            else:
                # Keep existing valid selection; do not overwrite a valid index
                pass
        except Exception as e:
            self.printException(e, "exception scheduling index set after refresh")
            try:
                self.index = min_idx
            except Exception as e2:
                self.printException(e2, "exception enforcing min index on focus")

        except Exception as e:
            self.printException(e, "exception checking/enforcing _min_index on focus")

    def _highlight_filename(self, name: str) -> None:  # FileListBase
        """
        Highlight the ListItem whose attached `_filename` equals `name`.

        This is intended to be called via `call_after_refresh` after the
        DOM has been updated by `set_path`.
        """
        try:
            nodes = self._nodes
            for idx, node in enumerate(nodes):
                if getattr(node, "_filename", None) == name:
                    self.index = idx
                    logger.debug("FileListBase._highlight_filename: setting index=%s for %s (match %s)", idx, getattr(self, 'id', None), name)
                # Removed the inner try-except as it was not necessary here

                    return
            # not found: default to minimum selectable index (skip legend)
            try:
                logger.debug("setting index=%s for %s (min_index fallback)", getattr(self, '_min_index', None) or 0, getattr(self, 'id', None))
                self.index = self._min_index or 0
            except Exception as e:
                self.printException(e, "exception setting index to 0")

        except Exception as e:
            self.printException(e, "exception in outer block")
            return

    def _highlight_top(self) -> None:  # FileListBase
        """Highlight the first entry in the list after a refresh."""
        try:
            # If there are nodes, set index to 0; otherwise leave unset.
            nodes = self._nodes
            if nodes:
                min_idx = self._min_index or 0
                target_idx = min_idx if min_idx < len(nodes) else 0
                self.index = target_idx
                logger.debug("FileListBase._highlight_top: setting index=%s for %s", target_idx, getattr(self, 'id', None))
            else:
                self.index = 0  # Fallback to setting index to 0 if no nodes
        except Exception as e:
            self.printException(e, "exception")
            return

    def _child_filename(self, child) -> Optional[str]:
        """
        Extract a filename/text value from a ListItem `child`.

        This consolidates repeated logic used by multiple `key_right`
        handlers: prefer an attached `_filename` attribute, then look
        for a `Label` with `text` or a renderable `Text`.
        Returns the extracted string, or `None` if extraction failed
        (and a modal has been shown when possible).
        """
        try:
            name = getattr(child, "_filename", None)
            if name is not None:
                return name
            lbl = child.query_one(Label)
            if hasattr(lbl, "text"):
                return lbl.text
            renderable = getattr(lbl, "renderable", None)
            if isinstance(renderable, Text):
                return renderable.plain
            if renderable is not None:
                return str(renderable)
            return str(lbl)
        except Exception as exc:
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e, "exception showing modal fallback")
                try:
                    self.app.push_screen(_TBDModal())
                except Exception as e2:
                    self.printException(e2)
            return None

    def _enter_directory(self, new_path: str, highlight_name: Optional[str] = None) -> None:
        """
        Change this FileList to show `new_path` and update app state.

        Calls the preparatory method when available (`prepFileModeFileList`),
        resets selection, optionally highlights `highlight_name` after refresh,
        updates `self.app.path` and `self.app.displayed_path`, and restores
        focus to this list.
        """
        try:
            # Use preparatory API when available
            if hasattr(self, "prepFileModeFileList"):
                self.prepFileModeFileList(new_path)
            else:
                super().set_path(new_path)

            # After prep, set selection/indices appropriately
            try:
                if highlight_name:
                    try:
                        self.call_after_refresh(self._highlight_filename, highlight_name)
                    except Exception as e:
                        self.printException(e, "exception scheduling highlight in helper")
                else:
                    try:
                        self.index = self._min_index or 0
                    except Exception as e:
                        self.printException(e, "exception resetting index in helper")
            except Exception as e:
                self.printException(e)

            # update app-level path info
            try:
                self.app.path = os.path.abspath(new_path)
                self.app.displayed_path = self.path
            except Exception as e:
                self.printException(e, "exception updating app path info in helper")

            # restore focus to this list
            try:
                try:
                    self.app.change_focus(f"#{self.id}")
                except Exception as e:
                    self.printException(e)
            except Exception as e:
                self.printException(e)

        except Exception as e:
            self.printException(e, "_enter_directory outer failure")


class FileModeFileList(FileListBase):
    """FileList for FileMode."""

    key = (
        "Key:  [yellow]'[yellow on white]\u00a0[/yellow on white]'[/yellow] tracked  U untracked  "
        "M modified  A staged  D deleted  I ignored  ! conflicted"
    )

    def prepFileModeFileList(self, path: str) -> None:  # FileModeFileList
        """
        Prepare and populate this `FileModeFileList` for `path`.

        Extracted from the previous helper so this instance can populate
        itself when requested.
        """
        try:
            path = os.path.abspath(path)
            self.path = path
            # keep the app aware of the full path currently displayed
            try:
                if hasattr(self, "app"):
                    self.app.displayed_path = self.path
            except Exception as e:
                self.printException(e, "exception setting displayed_path in prep")

            # Refresh repository cache when changing path
            try:
                app = self.app
                if app:
                    try:
                        app.build_repo_cache()
                    except Exception as e:
                        self.printException(e, "exception refreshing repo cache in prep")

            except Exception as e:
                self.printException(e)

            try:
                entries = sorted(os.listdir(path))
            except Exception as e:
                self.printException(e, f"Error reading {path}")
                self.clear()
                try:
                    self.append(ListItem(Label(Text(f"Error reading {path}: {e}", style="red"))))
                except Exception as e:
                    self.printException(e)

                return

            self.clear()
            # Insert legend key
            try:
                key_text = Text.from_markup(
                    FileModeFileList.key,
                    style="bold white on blue",
                )
                self.append(ListItem(Label(key_text)))
                self._min_index = 1

            except Exception as e:
                logger.debug(f"prepFileModeFileList: exception adding key legend: {e}")
                self.printException(e)

            # Parent entry
            if not os.path.isdir(os.path.join(path, ".git")):
                parent_item = ListItem(Label(Text("..", style="white on blue")))
                try:
                    parent_item._filename = ".."
                except Exception as e:
                    self.printException(e)

                self.append(parent_item)

            for name in entries:
                full = os.path.join(path, name)
                if name == ".git":
                    continue
                try:
                    if os.path.isdir(full):
                        li = ListItem(Label(Text(name, style="white on blue")))
                    else:
                        style = None
                        repo_status = None
                        try:
                            app = self.app
                            if app and getattr(app, "repo_available", False) and app.repo_root:
                                try:
                                    rel = os.path.relpath(full, app.repo_root)
                                except Exception as e:
                                    self.printException(e, "exception getting relpath in prep")
                                    rel = None
                                if rel and not rel.startswith(".."):
                                    flags = app.repo_status_map.get(rel, 0)
                                    try:
                                        # conflicted
                                        if flags & getattr(pygit2, "GIT_STATUS_CONFLICTED", 0):
                                            style = "magenta"
                                            repo_status = "conflicted"
                                        # staged (index changes)
                                        elif flags & (
                                            getattr(pygit2, "GIT_STATUS_INDEX_NEW", 0)
                                            | getattr(pygit2, "GIT_STATUS_INDEX_MODIFIED", 0)
                                            | getattr(pygit2, "GIT_STATUS_INDEX_DELETED", 0)
                                        ):
                                            style = "cyan"
                                            repo_status = "staged"
                                        # deleted in working tree
                                        elif flags & getattr(pygit2, "GIT_STATUS_WT_DELETED", 0):
                                            style = "red"
                                            repo_status = "wt_deleted"
                                        elif flags & getattr(pygit2, "GIT_STATUS_IGNORED", 0):
                                            style = "dim italic"
                                            repo_status = "ignored"
                                        elif flags & (
                                            getattr(pygit2, "GIT_STATUS_WT_MODIFIED", 0)
                                            | getattr(pygit2, "GIT_STATUS_INDEX_MODIFIED", 0)
                                        ):
                                            style = "yellow"
                                            repo_status = "modified"
                                        elif flags & getattr(pygit2, "GIT_STATUS_WT_NEW", 0):
                                            style = "bold yellow"
                                            repo_status = "untracked"
                                        else:
                                            style = "white"
                                            repo_status = "tracked_clean"
                                    except Exception as e:
                                        self.printException(e, "exception processing pygit2 flags in prep")
                                        style = None
                                        repo_status = None
                                else:
                                    style = "bold yellow"
                                    repo_status = "untracked"
                            else:
                                style = "bold yellow"
                                repo_status = "untracked"
                        except Exception as e:
                            self.printException(e, "exception getting repo status in prep")
                            style = None
                            repo_status = None

                        markers = {
                            "conflicted": "!",
                            "staged": "A",
                            "wt_deleted": "D",
                            "ignored": "I",
                            "modified": "M",
                            "untracked": "U",
                            "tracked_clean": " ",
                        }
                        marker = markers.get(repo_status, " ")
                        display = f"{marker} {name}"
                        if style:
                            li = ListItem(Label(Text(display, style=style)))
                        else:
                            li = ListItem(Label(display))
                        li._repo_status = repo_status

                    li._filename = name
                    self.append(li)
                except Exception as e:
                    self.printException(e, f"exception appending {name} in prepFileModeFileList")
                    continue

            try:
                self.call_after_refresh(self._highlight_top)
            except Exception as e:
                self.printException(e)
                try:
                    logger.debug("setting index=%s in change_focus fallback for %s", getattr(self, "_min_index", 0) or 0, getattr(self, 'id', None))
                    self.index = getattr(self, "_min_index", 0) or 0
                except Exception as e:
                    self.printException(e)

        except Exception as e:
            self.printException(e)

    def key_left(self, event: events.Key) -> bool:  # FileModeFileList
        """
        Handle left key behavior for FileModeFileList
        Returns True when the key was handled/consumed.
        """
        # If left pressed on the parent entry, go up a directory and
        # highlight the directory we came from.
        child = self.highlighted_child
        if child is None:
            return True
        item_name = getattr(child, "_filename", None)
        if item_name is None:
            try:
                label = child.query_one(Label)
                item_name = label.text if hasattr(label, "text") else str(label)
            except Exception as exc:
                try:
                    self.app.push_screen(_TBDModal(str(exc)))
                except Exception as e:
                    self.printException(e, "exception showing left key error modal")
                    try:
                        self.app.push_screen(_TBDModal())
                    except Exception as e:
                        self.printException(e)

                return True

        if item_name == "..":
            prev_basename = os.path.basename(self.path)
            parent = os.path.dirname(self.path)
            if parent == self.path or not parent:
                # already at filesystem root
                try:
                    self.app.push_screen(_TBDModal("Already at root"))
                except Exception as e:
                    self.printException(e)

                return True

            # change to parent directory using shared helper
            try:
                self._enter_directory(parent, highlight_name=prev_basename)
            except Exception as e:
                self.printException(e, "changing to parent via helper")

            return True

        # Left on non-parent: ignore (do nothing)
        return False

    def key_right(self, event: events.Key) -> bool:  # FileModeFileList
        """
        Handle right key behavior for FileModeFileList
            Either
                1) ignore "..", or
                2) enter a directory, or
                3) show file history in the History column.
        Returns True when the key was handled/consumed.
        """
        # If the highlighted entry is a directory (and not ".."), enter it.
        child = self.highlighted_child
        if child is None:
            return True
        # Extract the item name using shared helper
        item_name = self._child_filename(child)
        if item_name is None:
            return True

        if item_name != "..":
            full = os.path.join(self.path, item_name)
            if os.path.isdir(full):
                # switch the listing to the selected directory using helper
                try:
                    self._enter_directory(full)
                except Exception as e:
                    self.printException(e, "changing directory via helper in key_right")

                return True

            # Delegate history population to the FileModeHistoryList preparatory API
            try:
                # Use the canonical file-mode history widget only (do not mix modes)
                hist = self.app.file_mode_history_list
                hist.prepFileModeHistoryList(item_name)
                # Show history and focus the populated widget using its id
                try:
                    tgt = f"#{hist.id}"
                    logger.debug(
                        f">>>> change_state(file_history) - FileModeFileList.key_right: for history widget id={hist.id}"
                    )
                    self.app.change_state("file_history", tgt, self.app.footer_history)
                except Exception as e:
                    self.printException(e, "focusing history after prep failed")

                # Ensure the current Files list highlights the selected filename
                try:
                    self.call_after_refresh(self._highlight_filename, item_name)
                except Exception as e:
                    self.printException(e, "exception calling _highlight_filename")
            except Exception as exc:
                try:
                    self.app.push_screen(_TBDModal(str(exc)))
                except Exception as e:
                    self.printException(e, "exception showing outer error modal")

            return True

        # Not a directory we can enter — show TBD for now
        try:
            self.app.push_screen(_TBDModal())
        except Exception as e:
            self.printException(e)

        return False


class RepoModeFileList(FileListBase):
    """File list for repo-mode / log-first mode."""

    key = (
        "Key: [yellow]'[yellow on white]\u00a0[/yellow on white]'[/yellow] tracked  U untracked  "
        "M modified  R renamed  A staged  D deleted  I ignored  ! conflicted"
    )

    def prepRepoModeFileList(
        self, previous_hash: Optional[str], current_hash: Optional[str]
    ) -> None:  # RepoModeFileList
        """
        Populate this RepoModeFileList with files changed between two commits.

        `previous_hash` and `current_hash` are commit-ish identifiers (short
        or full) used to compute the tree diff via pygit2. This method builds
        ListItem entries from the diff and appends them after refresh to avoid
        DuplicateId mount races.
        """
        try:
            self.clear()
        except Exception as e:
            self.printException(e, "clearing repo-mode file list")
        # Reset populated flag so repeated preps will re-append items
        try:
            self._populated = False
        except Exception as e:
            self.printException(e)
        try:
            repo = None
            try:
                # Prefer the app-provided repo discovery/cache
                app = getattr(self, "app", None)
                if app and getattr(app, "repo_root", None):
                    gitdir = pygit2.discover_repository(app.repo_root)
                    if gitdir:
                        repo = pygit2.Repository(gitdir)
            except Exception as e:
                self.printException(e, "discovering repo for repo-mode file list")

            if repo is None:
                try:
                    self.append(ListItem(Label(Text(" No repository available to compute diff"))))
                except Exception as e:
                    self.printException(e, "appending no repository item")
                return

            # If either side references pseudo tokens (STAGED/MODS), build items from working/index state
            if (previous_hash in ("STAGED", "MODS")) or (current_hash in ("STAGED", "MODS")):
                try:
                    items_buffer: list = []
                    # Determine repo root for git commands
                    repo_root = repo.workdir or getattr(self.app, "repo_root", None)

                    # Gather staged and unstaged files
                    try:
                        staged_proc = subprocess.run(
                            ["git", "diff", "--name-only", "--cached"], cwd=repo_root, capture_output=True, text=True
                        )
                        staged_files = [s for s in staged_proc.stdout.splitlines() if s.strip()]
                    except Exception as e:
                        self.printException(e, "getting staged files")
                        staged_files = []
                    try:
                        unstaged_proc = subprocess.run(["git", "diff", "--name-only"], cwd=repo_root, capture_output=True, text=True)
                        unstaged_files = [s for s in unstaged_proc.stdout.splitlines() if s.strip()]
                    except Exception as e:
                        self.printException(e, "getting unstaged files")
                        unstaged_files = []

                    # Compute MODS per user rules
                    try:
                        if staged_files:
                            mods_files = [f for f in staged_files if f in unstaged_files]
                        else:
                            # fall back to modified files since top commit
                            try:
                                top_proc = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True)
                                top_files = [s for s in top_proc.stdout.splitlines() if s.strip()]
                            except Exception as e:
                                self.printException(e, "getting top commit files")
                                top_files = []
                            try:
                                changed_proc = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True)
                                changed_files = [s for s in changed_proc.stdout.splitlines() if s.strip()]
                            except Exception as e:
                                self.printException(e, "getting changed files vs HEAD")
                                changed_files = []
                            mods_files = [f for f in top_files if f in changed_files]
                    except Exception as e:
                        self.printException(e, "computing mods files")
                        mods_files = []

                    files_set = set()
                    if previous_hash == "STAGED" or current_hash == "STAGED":
                        files_set.update(staged_files)
                    if previous_hash == "MODS" or current_hash == "MODS":
                        files_set.update(mods_files)

                    if not files_set:
                        items_buffer.append(ListItem(Label(Text(" No changed files"))))
                    else:
                        for display_path in sorted(files_set):
                            try:
                                text = f"  {display_path}"
                                li = ListItem(Label(Text(text)))
                                li._filename = display_path
                                li._old_filename = None
                                li._new_filename = None
                                li._hash_prev = previous_hash
                                li._hash_curr = current_hash
                                items_buffer.append(li)
                            except Exception as e:
                                self.printException(e, "building pseudo file list item")
                                continue
                except Exception as exc:
                    self.printException(exc, "building pseudo-mode file list")
                    try:
                        self.append(ListItem(Label(Text(f" Error building file list: {exc}"))))
                    except Exception as e:
                        self.printException(e)
                    return
            else:
                # Resolve commit-ish to objects when possible
                try:
                    curr_obj = None
                    prev_obj = None
                    if current_hash:
                        try:
                            curr_obj = repo.get(current_hash)
                        except Exception as e:
                            self.printException
                            try:
                                curr_obj = repo.revparse_single(current_hash)
                            except Exception as e:
                                self.printException(e, "resolving current_hash in repo-mode prep")
                                curr_obj = None
                    if previous_hash:
                        try:
                            prev_obj = repo.get(previous_hash)
                        except Exception as e:
                            self.printException(e, "resolving previous_hash in repo-mode prep")
                            try:
                                prev_obj = repo.revparse_single(previous_hash)
                            except Exception as e:
                                self.printException(e, "resolving previous_hash in repo-mode prep")
                                prev_obj = None

                    curr_tree = getattr(curr_obj, "tree", None)
                    prev_tree = getattr(prev_obj, "tree", None)

                    if prev_tree is None and curr_tree is not None:
                        diff = repo.diff(None, curr_tree)
                    elif curr_tree is None and prev_tree is not None:
                        diff = repo.diff(prev_tree, None)
                    else:
                        diff = repo.diff(prev_tree, curr_tree)
                except Exception as exc:
                    self.printException(exc, "computing repo diff for repo-mode file list")
                    try:
                        self.append(ListItem(Label(Text(f" Error computing diff: {exc}"))))
                    except Exception as e:
                        self.printException(e, "appending error computing diff item")
                    return

            # Build items buffer from diff deltas (skip if already built for pseudo tokens)
            if "items_buffer" not in locals():
                try:
                    delta_map = {
                        getattr(pygit2, "GIT_DELTA_ADDED", 0): "A",
                        getattr(pygit2, "GIT_DELTA_MODIFIED", 0): "M",
                        getattr(pygit2, "GIT_DELTA_DELETED", 0): "D",
                        getattr(pygit2, "GIT_DELTA_RENAMED", 0): "R",
                        getattr(pygit2, "GIT_DELTA_TYPECHANGE", 0): "T",
                    }
                    items_buffer: list = []
                    for d in getattr(diff, "deltas", diff):
                        try:
                            status = getattr(d, "status", None)
                            marker = delta_map.get(status, " ")
                            # Safely extract file paths from delta old_file/new_file
                            old_file = getattr(d, "old_file", None)
                            new_file = getattr(d, "new_file", None)
                            old_path = getattr(old_file, "path", None)
                            new_path = getattr(new_file, "path", None)
                            if not old_path and not new_path:
                                logger.warning(
                                    "delta with no paths",
                                    extra={"delta": repr(d), "status": status, "old_path": old_path, "new_path": new_path},
                                )
                            display_path = new_path or old_path or ""
                            text = f"{marker} {display_path}"
                            li = ListItem(Label(Text(" " + text)))
                            li._change_type = marker
                            li._filename = display_path
                            li._old_filename = old_path
                            li._new_filename = new_path
                            li._hash_prev = previous_hash
                            li._hash_curr = current_hash
                            items_buffer.append(li)
                        except Exception as e:
                            self.printException(e, "building repo-mode list item")
                            continue
                    if not items_buffer:
                        items_buffer.append(ListItem(Label(Text(" No changed files between selected commits"))))
                except Exception as e:
                    self.printException(e, "building items buffer for repo-mode file list")
                    try:
                        self.append(ListItem(Label(Text(f" Error building file list: {e}"))))
                    except Exception as e2:
                        self.printException(e2, "appending error building file list item")
                    return

            # Append buffer after refresh to avoid mount races
            try:
                def _append_buffer():
                    try:
                        logger.debug(
                            "RepoModeFileList._append_buffer ENTER id=%s populated=%s items_buffer_len=%s",
                            id(self),
                            getattr(self, "_populated", None),
                            len(items_buffer),
                        )
                        # capture a short caller stack for where the append was scheduled from
                        if self._populated:
                            return
                        try:
                            key_li = ListItem(
                                Label(
                                    Text.from_markup(
                                        RepoModeFileList.key,
                                        style="bold white on blue",
                                    )
                                )
                            )
                            try:
                                self.append(key_li)
                                self._min_index = 1
                            except Exception as e:
                                self.printException(e, "appending key list item")
                        except Exception as e:
                            self.printException(e, "building key list item")
                        for it in items_buffer:
                            try:
                                self.append(it)
                            except Exception as e:
                                self.printException(e, "appending repo-mode file item")
                                continue

                        target_idx = self._min_index or 0
                        logger.debug("RepoModeFileList._append_buffer: setting index=%s for %s", target_idx, getattr(self, 'id', None))
                        self.index = target_idx
                        try:
                            self.refresh()
                        except Exception as e:
                            self.printException(e, "could not refresh repo-mode file list")

                        try:
                            def _set_index_and_scroll():
                                try:
                                    target_idx2 = self._min_index or 0
                                    logger.debug("RepoModeFileList._append_buffer._set_index_and_scroll: setting index=%s for %s", target_idx2, getattr(self, 'id', None))
                                    logger.debug("RepoModeFileList._append_buffer._set_index_and_scroll: setting index=%s for %s", target_idx2, getattr(self, 'id', None))
                                    self.index = target_idx2
                                    try:
                                        self.scroll_y = 0
                                    except Exception as e:
                                        self.printException(e, "RepoModeFileList._set_index_and_scroll: setting scroll_y failed")
                                except Exception as e:
                                    self.printException(e, "setting index/scroll in _append_buffer")

                            self.call_after_refresh(_set_index_and_scroll)
                        except Exception as e:
                            self.printException(e, "scheduling index reset after refresh")
                            try:
                                logger.debug("FileModeFileList helper: resetting index to %s for %s", getattr(self, '_min_index', None) or 0, getattr(self, 'id', None))
                                self.index = self._min_index or 0
                                try:
                                    self.scroll_y = 0
                                except Exception as e2:
                                    self.printException(e2, "RepoModeFileList helper: resetting scroll_y failed")

                            except Exception as e:
                                self.printException(e, "setting index to _min_index fallback")
                        self._populated = True
                        logger.debug(
                            "RepoModeFileList._append_buffer EXIT id=%s populated=%s node_count=%s",
                            id(self),
                            getattr(self, "_populated", None),
                            len(getattr(self, "_nodes", [])) if getattr(self, "_nodes", None) is not None else None,
                        )
                        # If the app has a currently-selected diff file, ensure it's highlighted
                        try:
                            fname = getattr(self.app, "current_diff_file", None)
                            if fname:
                                logger.debug("RepoModeFileList._append_buffer: scheduling highlight for %s", fname)
                                try:
                                    self.call_after_refresh(lambda: (logger.debug("RepoModeFileList.call_after_refresh highlighting %s", fname), self._highlight_filename(fname)))
                                except Exception as _e:
                                    logger.debug("RepoModeFileList._append_buffer: call_after_refresh failed: %s", _e)
                                    try:
                                        self._highlight_filename(fname)
                                        logger.debug("RepoModeFileList._append_buffer: immediate highlight succeeded for %s", fname)
                                    except Exception as _e2:
                                        logger.debug("RepoModeFileList._append_buffer: immediate highlight failed: %s", _e2)
                                        self.printException(_e2, "RepoModeFileList immediate highlight failed")
                        except Exception as _e:
                            logger.debug("RepoModeFileList._append_buffer: highlight scheduling outer exception: %s", _e)
                            self.printException(_e, "RepoModeFileList highlight scheduling outer exception")

                        items_buffer.clear()
                    except Exception as e:
                        self.printException(e, "error appending repo-mode buffer")
                        return

                try:
                    try:
                        self.call_after_refresh(_append_buffer)
                    except Exception as e:
                        self.printException(e, "scheduling repo-mode append buffer failed")
                        try:
                            # fallback to app-level scheduling
                            if self.app:
                                self.app.call_after_refresh(_append_buffer)
                            else:
                                _append_buffer()
                        except Exception as e:
                            self.printException(e, "scheduling repo-mode append buffer failed")
                            _append_buffer()
                except Exception as e:
                    self.printException(e, "scheduling append buffer in repo-mode prep")
            except Exception as e:
                self.printException(e, "finalizing repo-mode prep")
        except Exception as e:
            self.printException(e, "prepRepoModeFileList outer failure")

    def key_left(self, event: events.Key) -> bool:  # RepoModeFileList
        """
        When Left is pressed in the repo-mode Files column, close
        the Files column and restore the History column to full-width.

        Returns True to indicate the key was handled.
        """
        try:
            # Hide the right1 (Files) column and restore left (History)
            try:
                # Restore previous full state (layout + focus + footer)
                logger.debug(">>>> change_state(): RepoModeFileList.key_left: restoring history_fullscreen layout")
                try:
                    self.app.change_state(
                        "history_fullscreen",
                        f"#{self.app.repo_mode_history_list.id}",
                        self.app.footer_history,
                    )
                except Exception as e:
                    self.printException(e, "exception changing state for left-only restore")
            except Exception as e:
                self.printException(e, "exception popping state for left-only restore")

        except Exception as e:
            self.printException(e)
        return True

    def key_right(self, event: events.Key) -> bool:  # RepoModeFileList
        """
        When Right is pressed on a file in repo-mode, show its diff between
        the two commits represented by the file list (or per-item hashes).

        Returns True when handled.
        """
        child = self.highlighted_child
        if child is None:
            return True

        # Extract filename/value from the selected child using shared helper
        filename = self._child_filename(child)
        if filename is None:
            return True

        # Determine commit hashes: prefer per-item hashes then file_list attrs then app-wide
        try:
            previous_hash = getattr(child, "_hash_prev", None) or self.current_prev_sha or self.app.current_prev_sha
            current_hash = getattr(child, "_hash_curr", None) or self.current_commit_sha or self.app.current_commit_sha
        except Exception as e:
            self.printException(e, "exception getting commit hashes")
            previous_hash = None
            current_hash = None

        if not filename:
            try:
                self.app.push_screen(_TBDModal("Unknown filename for diff"))
            except Exception as e:
                self.printException(e, "could not push TBDModal for unknown filename in FileModeFileList")

            return True

        # Store current diff info on the app for re-rendering/variant toggles
        try:
            self.app.current_commit_sha = current_hash
            self.app.current_prev_sha = previous_hash
            self.app.current_diff_file = filename
        except Exception as e:
            self.printException(e, "exception setting app diff info")

        try:
            # Delegate to centralized helper
            try:
                self.prep_and_show_diff(filename, previous_hash, current_hash, self.app.diff_list, "history_file_diff")
            except Exception as e:
                self.printException(e, "prep_and_show_diff failed")
        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e, "could not push TBDModal for diff-fullscreen error")

        return False


class HistoryListBase(AppBase):
    """ListView used for the History column."""

    def toggle_check_current(self) -> None:  # HistoryListBase
        """
        Toggle a single checkmark on the currently selected history item.

        Only one history ListItem may be checked at a time. The checkmark is
        shown as a leading "✓ " replacing the leading space. If the current
        item is already checked it will be unchecked. Any other checked item
        will be cleared.
        """
        try:
            nodes = self._nodes
            if not nodes:
                return
            idx = self.index or 0
            if idx < 0 or idx >= len(nodes):
                return

            # helper to get label and style
            def _label_text_and_style(node):
                try:
                    lbl = node.query_one(Label)
                    style = None
                    try:
                        style = getattr(getattr(lbl, "renderable", None), "style", None)
                    except Exception as e:
                        self.printException(e, "exception getting style")
                        style = None
                    # derive visible text from stored raw_text when available
                    raw = getattr(node, "_raw_text", None)
                    if raw is not None:
                        return raw, style, lbl
                    # fallback to reading renderable
                    if hasattr(lbl, "text"):
                        text = lbl.text
                    else:
                        renderable = getattr(lbl, "renderable", None)
                        if isinstance(renderable, Text):
                            text = renderable.plain
                        elif renderable is not None:
                            text = str(renderable)
                        else:
                            text = str(lbl)
                    return text, style, lbl
                except Exception as e:
                    self.printException(e)
                    return str(node), None, None

            # Find previously checked node and clear it (unless it's the same)
            prev_checked = None
            for node in nodes:
                if getattr(node, "_checked", False):
                    prev_checked = node
                    break

            target = nodes[idx]

            # If target is already checked, uncheck it and restore leading space
            if prev_checked is target:
                try:
                    text, style, lbl = _label_text_and_style(target)
                    # remove any leading check and whitespace
                    stripped = text.lstrip("✓ ").lstrip()
                    new_text = " " + stripped
                    if lbl is not None:
                        if style:
                            lbl.update(Text(new_text, style=style))
                        else:
                            lbl.update(Text(new_text))
                    target._checked = False
                except Exception as e:
                    self.printException(e, "exception unchecking target")

                return

            # Clear previous checked if different
            if prev_checked is not None and prev_checked is not target:
                try:
                    text, style, lbl = _label_text_and_style(prev_checked)
                    stripped = text.lstrip("✓ ").lstrip()
                    new_text = " " + stripped
                    if lbl is not None:
                        if style:
                            lbl.update(Text(new_text, style=style))
                        else:
                            lbl.update(Text(new_text))
                    prev_checked._checked = False
                except Exception as e:
                    self.printException(e, "exception clearing previous check")

            # Set check on target
            try:
                text, style, lbl = _label_text_and_style(target)
                stripped = text.lstrip("✓").lstrip()
                new_text = "✓" + stripped
                if lbl is not None:
                    if style:
                        lbl.update(Text(new_text, style=style))
                    else:
                        lbl.update(Text(new_text))
                target._checked = True
            except Exception as e:
                self.printException(e, "exception setting check on target")

        except Exception as e:
            self.printException(e, "exception in outer block")

    def on_focus(self, event: events.Focus) -> None:  # HistoryListBase
        """When the HistoryListBase receives focus, ensure the first item is highlighted."""
        try:
            # Force a re-apply of the highlight after focus; sometimes the
            # ListView won't re-highlight if the index hasn't changed.
            def _apply() -> None:
                try:
                    nodes = self._nodes
                    if not nodes:
                        return
                    target = self.index if self.index is not None else 0
                    # clear then restore to force watch_index
                    try:
                        self.index = None
                    except Exception as e:
                        self.printException(e, "clearing index")
                    try:
                        self.index = target
                    except Exception as e:
                        self.printException(e, "restoring index target")

                except Exception as e:
                    self.printException(e, "nodes processing")
                    return

            try:
                self.call_after_refresh(_apply)
            except Exception as e:
                self.printException(e, "call_after_refresh")
                _apply()
        except Exception as e:
            self.printException(e, "_apply setup")


    def more_keys(self, event: events.Key) -> bool:  # HistoryListBase
        """
        Handle history-specific keys.
        Return True when the key was handled (e.g. `m`), False otherwise.
        """
        try:
            key = event.key
            logger.debug(f"HistoryListBase.more_keys: key={key}")

            # Mark/unmark the file referenced by this history view
            if key and key.lower() == "":
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e)

                try:
                    self.toggle_check_current()
                except Exception as e:
                    self.printException(e, "toggle check")
                return True

            # Other keys: not handled here
            return False
        except Exception as e:
            self.printException(e)
            return False

    def compute_commit_pair_hashes(self):  # HistoryListBase
        """
        Compute the pair of commit hashes for a history diff.

        Returns a tuple `(current_hash, previous_hash, current_line, previous_line, i_newer, i_older)`
        or `(None, None, None, None, None, None)` on failure. Callers should
        decide how to present errors to the user.
        """
        try:
            nodes = self._nodes
            idx = self.index
            if idx is None or idx < 0 or not nodes or idx >= len(nodes):
                return (None, None, None, None, None, None)

            # Find any checked item
            checked_idx = None
            for i, node in enumerate(nodes):
                if getattr(node, "_checked", False):
                    checked_idx = i
                    break

            if checked_idx is None or checked_idx == idx:
                # default to current vs next (older)
                if idx >= len(nodes) - 1:
                    return (None, None, None, None, None, None)
                i_newer = idx
                i_older = idx + 1
            else:
                i1 = idx
                i2 = checked_idx
                if i1 == i2:
                    return (None, None, None, None, None, None)
                if i1 < i2:
                    i_newer, i_older = i1, i2
                else:
                    i_newer, i_older = i2, i1

            current_line = self.text_of(nodes[i_newer])
            previous_line = self.text_of(nodes[i_older])

            current_hash = getattr(nodes[i_newer], "_hash", None)
            previous_hash = getattr(nodes[i_older], "_hash", None)
            if not current_hash or not previous_hash:
                try:
                    m1 = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", current_line)
                    m2 = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", previous_line)
                    if not m1 or not m2:
                        return (None, None, None, None, None, None)
                    current_hash = m1.group(2)
                    previous_hash = m2.group(2)
                except Exception as e:
                    self.printException(e)
                    return (None, None, None, None, None, None)

            return (current_hash, previous_hash, current_line, previous_line, i_newer, i_older)
        except Exception as e:
            self.printException(e)
            return (None, None, None, None, None, None)
        except Exception as e:
            self.printException(e)
            return False


class FileModeHistoryList(HistoryListBase):
    """subclass for FileMode HistoryList functionality; see `HistoryListBase` for shared logic."""

    def prepFileModeHistoryList(self, file_path: str) -> None:  # FileModeHistoryList
        """
        Populate this History list with the commit history for a single file.

        Accepts a file path (filename relative to `self.app.path`) and
        populates the widget by running `git log --follow` in the current
        working directory. Appends ListItem entries and focuses the widget.
        """
        try:
            filename = file_path or self._filename
            if not filename:
                return

            proc = subprocess.run(
                [
                    "git",
                    "log",
                    "--follow",
                    "--date=short",
                    "--pretty=format:%ad %h %s",
                    "--",
                    filename,
                ],
                cwd=self.app.path,
                capture_output=True,
                text=True,
            )
            out = proc.stdout.strip()

            try:
                self.clear()
            except Exception as e:
                self.printException(e)

            try:
                self._filename = filename
            except Exception as e:
                self.printException(e)

            # Optionally insert pseudo entries (STAGED/MODS) then append real commits
            if out:
                try:
                    app = self.app
                    pseudo_entries: list[str] = []
                    if app and getattr(app, "repo_available", False) and app.repo_root:
                        try:
                            rel = os.path.relpath(os.path.join(self.app.path, filename), app.repo_root)
                        except Exception as e:
                            self.printException(e, "exception getting relpath for pseudo entries")
                            rel = None
                        if rel and not rel.startswith(".."):
                            flags = app.repo_status_map.get(rel, 0)
                            idx_flags = (
                                getattr(pygit2, "GIT_STATUS_INDEX_NEW", 0)
                                | getattr(pygit2, "GIT_STATUS_INDEX_MODIFIED", 0)
                                | getattr(pygit2, "GIT_STATUS_INDEX_DELETED", 0)
                            )
                            wt_flags = (
                                getattr(pygit2, "GIT_STATUS_WT_NEW", 0)
                                | getattr(pygit2, "GIT_STATUS_WT_MODIFIED", 0)
                                | getattr(pygit2, "GIT_STATUS_WT_DELETED", 0)
                            )
                            has_index = bool(flags & idx_flags)
                            has_wt = bool(flags & wt_flags)
                            if has_wt and has_index:
                                pseudo_entries = ["MODS", "STAGED"]
                            elif has_index:
                                pseudo_entries = ["STAGED"]
                            elif has_wt:
                                pseudo_entries = ["MODS"]
                except Exception as e:
                    self.printException(e, "exception building pseudo entries")
                    pseudo_entries = []

                logger.debug(
                    f"prepFileModeHistoryList: populated history for {filename} items={len(self._nodes) if hasattr(self,'_nodes') else 'unknown'}"
                )

                for pseudo in pseudo_entries:
                    display_pseudo = pseudo
                    if pseudo == "STAGED":
                        try:
                            app = self.app
                            display_pseudo = "STAGED"
                            if app and getattr(app, "repo_root", None):
                                try:
                                    base_path = getattr(app, "path", None)
                                    rel = os.path.relpath(os.path.join(base_path or "", filename), app.repo_root)
                                except Exception as e:
                                    self.printException(e, "exception getting relpath for STAGED")
                                    rel = None
                                mtime = None
                                if rel:
                                    try:
                                        mtime = app.repo_index_mtime_map.get(rel)
                                    except Exception as e:
                                        self.printException(e, "exception getting index mtime")
                                        mtime = None
                                if not mtime:
                                    try:
                                        index_path = os.path.join(app.repo_root, ".git", "index")
                                        mtime = os.path.getmtime(index_path)
                                    except Exception as e:
                                        self.printException(e, "exception getting index file mtime")
                                        mtime = None
                                if mtime:
                                    display_pseudo = (
                                        f"{datetime.datetime.fromtimestamp(float(mtime)).strftime('%Y-%m-%d')} STAGED"
                                    )
                        except Exception as e:
                            self.printException(e, "exception building STAGED display")
                            display_pseudo = "STAGED"

                    elif pseudo == "MODS":
                        try:
                            try:
                                base_path = getattr(app, "path", None)
                                fp = os.path.join(base_path or "", filename)
                                mtime = os.path.getmtime(fp)
                            except Exception as e:
                                self.printException(e, "exception getting MODS file mtime")
                                mtime = None
                            if mtime:
                                display_pseudo = (
                                    f"{datetime.datetime.fromtimestamp(float(mtime)).strftime('%Y-%m-%d')} MODS"
                                )
                            else:
                                display_pseudo = "MODS"
                        except Exception as e:
                            self.printException(e, "exception building MODS display")
                            display_pseudo = "MODS"

                    pli = ListItem(Label(Text(" " + display_pseudo)))
                    try:
                        pli._hash = pseudo
                        pli._raw_text = display_pseudo
                        self.append(pli)
                    except Exception as e:
                        self.printException(e)

                for line in out.splitlines():
                    try:
                        li = ListItem(Label(Text(" " + line)))
                        m = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", line)
                        if m:
                            li._hash = m.group(2)
                        li._raw_text = line
                        self.append(li)
                    except Exception as e:
                        self.printException(e)
                        continue
            else:
                try:
                    self.append(ListItem(Label(Text(" " + f"No git history for {filename}"))))
                except Exception as e:
                    self.printException(e)

            try:
                self.styles.display = None
            except Exception as e:
                self.printException(e)

            try:
                logger.debug("setting index=0 in prepFileModeHistoryList for %s", getattr(self, 'id', None))
                self.index = 0
            except Exception as e:
                self.printException(e)

        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)

    def key_left(self, event: events.Key) -> bool:  # FileModeHistoryList
        """
        Handle left key behavior for FileModeHistoryList.

        Returns True when the key was handled/consumed.
        """
        try:
            # Restore focus first, then restore the layout to left_fullscreen
            logger.debug(">>>> change_state(): FileModeHistoryList.key_left: restoring left fullscreen layout")
            try:
                self.app.change_state("file_fullscreen", f"#{self.app.file_mode_file_list.id}", self.app.footer_file)
            except Exception as e:
                self.printException(e, "exception changing state to file_fullscreen from history")

            # After restoring the left files view, ensure the left file list highlights the
            # currently selected diff file (if any). Use a retry loop that waits until
            # the left file list has been populated and contains the filename, then
            # perform the highlight. This avoids races where other focus handlers set
            # the index after a single scheduled call.
            try:
                fname = getattr(self.app, "current_diff_file", None)
                if fname:
                    logger.debug("FileModeHistoryList.key_left: scheduling highlight for left-file-list %s", fname)
                    try:
                        fl = getattr(self.app, "file_mode_file_list", None)
                        if fl is None:
                            logger.debug("FileModeHistoryList.key_left: no file_mode_file_list available to highlight")
                        else:
                            try:
                                # Schedule a single highlight after the DOM refresh; no retry loop needed
                                self.app.call_after_refresh(lambda: fl._highlight_filename(fname))
                            except Exception as e:
                                # Fallback to direct call if scheduling fails
                                try:
                                    fl._highlight_filename(fname)
                                except Exception as _e:
                                    self.printException(_e, "FileModeHistoryList highlight failed")
                    except Exception as _e:
                        logger.debug("FileModeHistoryList.key_left: scheduling highlight outer exception: %s", _e)
                        self.printException(_e, "FileModeHistoryList scheduling highlight outer exception")
            except Exception as _e:
                logger.debug("FileModeHistoryList.key_left: scheduling highlight outer exception: %s", _e)
                self.printException(_e, "FileModeHistoryList scheduling highlight outer exception")
        except Exception as e:
            self.printException(e, "exception popping state to files on left from history")
        return True

    def key_right(self, event: events.Key) -> bool:  # FileModeHistoryList
        """
        Handle right key behavior for FileModeHistoryList.

        Returns True when the key was handled/consumed.
        """
        # need at least one other item to diff against (either checked or next)
        idx = self.index
        nodes = self._nodes
        if idx is None or idx < 0 or not nodes:
            try:
                self.app.push_screen(_TBDModal("No commit to diff with"))
            except Exception as e:
                self.printException(e, "showing no commit modal")

            return True

        # Find any checked item in the history
        checked_idx = None
        for i, node in enumerate(nodes):
            if getattr(node, "_checked", False):
                checked_idx = i
                break

        # Determine the pair of indices to diff: default is current vs next
        if checked_idx is None or checked_idx == idx:
            # behave as before: need a next item
            if idx >= len(nodes) - 1:
                try:
                    self.app.push_screen(_TBDModal("No earlier commit to diff with"))
                except Exception as e:
                    self.printException(e, "showing no earlier commit modal")

                return True
            i_newer = idx
            i_older = idx + 1
        else:
            # If there is a checked item and it's not the current one,
            # diff between the current item and the checked item.
            # Order: lower item in the list (larger index) is prev, higher (smaller index) is curr.
            i1 = idx
            i2 = checked_idx
            if i1 == i2:
                return True
            i_older = max(i1, i2)
            i_newer = min(i1, i2)

        current_line = self.text_of(nodes[i_newer])
        previous_line = self.text_of(nodes[i_older])

        # Prefer attached _hash on ListItems; fallback to regex parsing
        current_hash = getattr(nodes[i_newer], "_hash", None)
        previous_hash = getattr(nodes[i_older], "_hash", None)
        if not current_hash or not previous_hash:
            try:
                m1 = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", current_line)
                m2 = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", previous_line)
                if not m1 or not m2:
                    raise ValueError(f"Lines not in expected format:\n{current_line!r}\n{previous_line!r}")
                current_hash = m1.group(2)
                previous_hash = m2.group(2)
            except Exception as exc:
                try:
                    self.app.push_screen(_TBDModal(f"Could not parse hashes: {exc}"))
                except Exception as e:
                    self.printException(e, "showing hash parse error modal")

                return True

        # determine filename for the history (attached when populated)
        filename = self._filename
        if not filename:
            try:
                self.app.push_screen(_TBDModal("Unknown filename for history"))
            except Exception as e:
                self.printException(e, "showing unknown filename modal")

            return True

        # Use centralized diff command builder on the app (handles variants)

        # Store current diff info for potential re-render
        self.app.current_commit_sha = current_hash
        self.app.current_prev_sha = previous_hash
        self.app.current_diff_file = filename

        try:
            # Delegate to centralized helper
            try:
                logger.debug(
                    f"FileModeHistoryList.key_right: preparing diff for file={filename} prev={previous_hash} curr={current_hash}"
                )
                self.prep_and_show_diff(filename, previous_hash, current_hash, self.app.diff_list, "file_history_diff")
            except Exception as e:
                self.printException(e, "prep_and_show_diff failed")

        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)

        return False


class RepoModeHistoryList(HistoryListBase):
    """RepoMode History list used when `-l/--log-first`"""

    def prepRepoModeHistoryList(self) -> None:  # RepoModeHistoryList
        """
        Populate this RepoModeHistoryList using the current repository.

        This method discovers the repository from the app state (prefer
        `self.app.repo_root` then `self.app.path`) and populates the widget
        with a repository-wide commit log using pygit2. Appends ListItem
        entries with the format: "YYYY-MM-DD <short-hash> <subject>".
        """
        try:
            app = self.app
            repo_root = None
            if app:
                repo_root = getattr(app, "repo_root", None) or getattr(app, "path", None)
            if not repo_root:
                return
            gitdir = pygit2.discover_repository(repo_root)
            if not gitdir:
                return
            repo = pygit2.Repository(gitdir)

            # clear existing items
            try:
                self.clear()
            except Exception as e:
                self.printException(e)

            seen: set[str] = set()
            commits: list[pygit2.Commit] = []

            # Prefer HEAD if available
            try:
                if repo.head_is_unborn:
                    start_oids = []
                else:
                    try:
                        start_oids = [repo.head.target]
                    except Exception as e:
                        self.printException(e)
                        start_oids = []
            except Exception as e:
                self.printException(e)
                start_oids = []

            # If no HEAD, fall back to all branch refs
            if not start_oids:
                try:
                    for ref_name in repo.references:
                        if ref_name.startswith("refs/heads/") or ref_name.startswith("refs/tags/"):
                            try:
                                ref = repo.lookup_reference(ref_name)
                                if ref and getattr(ref, "target", None):
                                    start_oids.append(ref.target)
                            except Exception as e:
                                self.printException(e)
                                continue
                except Exception as e:
                    self.printException(e)

            # Walk commits from each start oid, collect unique commits
            for oid in start_oids:
                try:
                    walker = repo.walk(oid, pygit2.GIT_SORT_TIME)
                    for c in walker:
                        cid = str(c.id)
                        if cid in seen:
                            continue
                        seen.add(cid)
                        commits.append(c)
                except Exception as e:
                    self.printException(e)
                    continue

            # If still no commits (empty repo), bail out
            if not commits:
                try:
                    self.append(ListItem(Label(Text(" " + "No git history for repository"))))
                except Exception as e:
                    self.printException(e)

                return

            # Optionally insert pseudo entries (STAGED/MODS) at the top
            try:
                pseudo_entries: list[str] = []
                try:
                    staged_proc = subprocess.run(
                        ["git", "diff", "--name-only", "--cached"], cwd=repo.workdir, capture_output=True, text=True
                    )
                    staged_files = [s for s in staged_proc.stdout.splitlines() if s.strip()]
                    unstaged_proc = subprocess.run(["git", "diff", "--name-only"], cwd=repo.workdir, capture_output=True, text=True)
                    unstaged_files = [s for s in unstaged_proc.stdout.splitlines() if s.strip()]
                    has_staged = bool(staged_files)
                    has_unstaged = bool(unstaged_files)
                    if has_unstaged and has_staged:
                        pseudo_entries = ["MODS", "STAGED"]
                    elif has_staged:
                        pseudo_entries = ["STAGED"]
                    elif has_unstaged:
                        pseudo_entries = ["MODS"]
                except Exception as e:
                    self.printException(e, "exception detecting staged/unstaged for pseudo entries")
                    pseudo_entries = []

                for pseudo in pseudo_entries:
                    try:
                        display_pseudo = pseudo
                        pli = ListItem(Label(Text(" " + display_pseudo)))
                        pli._hash = pseudo
                        pli._raw_text = display_pseudo
                        self.append(pli)
                    except Exception as e:
                        self.printException(e, "appending pseudo history entry")
            except Exception as e:
                self.printException(e)

            # Append commits in order (walker yields by time desc)
            for c in commits:
                try:
                    when = datetime.datetime.fromtimestamp(int(c.author.time)).strftime("%Y-%m-%d")
                except Exception as e:
                    self.printException(e)
                    when = "????-??-??"
                try:
                    short = str(c.id)[:7]
                except Exception as e:
                    self.printException(e)
                    short = "???????"
                try:
                    subj = (c.message or "").splitlines()[0]
                except Exception as e:
                    self.printException(e)
                    subj = "(no message)"
                line = f"{when} {short} {subj}"
                try:
                    li = ListItem(Label(Text(" " + line)))
                    li._hash = short
                    li._raw_text = line
                    self.append(li)
                except Exception as e:
                    self.printException(e)
                    continue

            try:
                self.styles.display = None
            except Exception as e:
                self.printException(e)

            try:
                self.index = 0
            except Exception as e:
                self.printException(e)

        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)

    def key_right(self, event: events.Key) -> bool:  # RepoModeHistoryList
        """
        Handle Right key: open a `RepoModeFileList` populated with changes between two commits.

        Determines the current and comparison commit (checked item or next item),
        computes the tree diff via pygit2, mounts a `RepoModeFileList` at the files
        column, and records the old/new commit hashes on that widget for later
        diff rendering.
        """
        try:
            # Use centralized helper to compute the two commit hashes/lines
            current_hash, previous_hash, _, _, _, _ = self.compute_commit_pair_hashes()
            if not current_hash or not previous_hash:
                try:
                    self.app.push_screen(_TBDModal("No commits available to diff"))
                except Exception as e:
                    self.printException(e)
                return True

            try:
                self.app.current_commit_sha = current_hash
                self.app.current_prev_sha = previous_hash
            except Exception as e:
                self.printException(e)

            # Ensure a RepoModeFileList instance is available via app attribute and mount it
            try:
                # Use the already-composed repo_mode_file_list (no remounting).
                try:
                    file_list = self.app.repo_mode_file_list
                    if file_list is None:
                        try:
                            self.app.push_screen(_TBDModal("Could not show files for commit diff"))
                        except Exception as e:
                            self.printException(e)
                        return True

                    try:
                        logger.debug(
                            "RepoModeHistoryList.key_right: calling prepRepoModeFileList on file_list id=%s populated=%s",
                            id(file_list),
                            getattr(file_list, "_populated", None),
                        )
                        file_list.prepRepoModeFileList(previous_hash, current_hash)
                        logger.debug(
                            "RepoModeHistoryList.key_right: returned from prepRepoModeFileList id=%s populated=%s node_count=%s",
                            id(file_list),
                            getattr(file_list, "_populated", None),
                            (
                                len(getattr(file_list, "_nodes", []))
                                if getattr(file_list, "_nodes", None) is not None
                                else None
                            ),
                        )
                    except Exception as e:
                        self.printException(e, "prepRepoModeFileList failed")

                    try:
                        file_list.index = getattr(file_list, "_min_index", 0) or 0
                    except Exception as e:
                        self.printException(e)

                    try:
                        logger.debug(
                            f">>>> change_state(history_file): RepoModeHistoryList.key_right: mounting RepoModeFileList id={getattr(file_list,'id',None)} file_list_id={id(file_list)}"
                        )
                        self.app.change_state(
                            "history_file",
                            f"#{getattr(file_list, 'id', file_list.id if file_list else 'right-file-list')}",
                            self.app.footer_file,
                        )
                        try:
                            def _set_file_index_and_scroll():
                                try:
                                    file_list.index = getattr(file_list, "_min_index", 0) or 0
                                    try:
                                        file_list.scroll_y = 0
                                    except Exception as e2:
                                        self.printException(e2, "setting file_list scroll_y after change_state")
                                except Exception as e:
                                    self.printException(e, "setting file_list index/scroll after change_state")

                            file_list.call_after_refresh(_set_file_index_and_scroll)
                        except Exception as e:
                            self.printException(e, "scheduling file_list index reset after change_state")

                        try:
                            def _app_set_file_index_and_scroll():
                                try:
                                    file_list.index = getattr(file_list, "_min_index", 0) or 0
                                    try:
                                        file_list.scroll_y = 0
                                    except Exception as e2:
                                        self.printException(e2, "setting file_list scroll_y after change_state")
                                except Exception as e:
                                    self.printException(e, "app-level setting file_list index/scroll after change_state")

                            self.app.call_after_refresh(_app_set_file_index_and_scroll)
                        except Exception as e:
                            self.printException(e, "scheduling app-level file_list index reset after change_state")
                        logger.debug(
                            "RepoModeHistoryList.key_right AFTER change_state: _current_layout=%s _current_focus=%s _current_footer=%s",
                            getattr(self.app, "_current_layout", None),
                            getattr(self.app, "_current_focus", None),
                            getattr(self.app, "_current_footer", None),
                        )
                    except Exception as e:
                        self.printException(e)
                except Exception as e:
                    self.printException(e, "unexpected error in key_right")
                    return True
            except Exception as e:
                self.printException(e, "unexpected error in key_right")
                return True

            try:
                logger.debug(
                    "RepoModeHistoryList.key_right EXIT id=%s widget_id=%s", id(self), getattr(self, "id", None)
                )
            except Exception as e:
                self.printException(e)
            return True

        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)

        return True


class DiffList(AppBase):
    """
    ListView used for the Diff column.
    """

    def prepDiffList(  # DiffList
        self,
        filename: str,
        previous_hash: Optional[str],
        current_hash: Optional[str],
        variant_index: Optional[int] = None,
    ) -> None:
        """
        Populate this Diff list for `filename` between two commit hashes.

        `variant_index` may be provided to select a diff variant from
        `self.app.diff_variants`. This method runs the diff command built
        by `self.app.build_diff_cmd`, clears the list, appends a header
        and the diff lines, applying colorization if enabled.
        """
        try:
            # Optionally select a diff variant
            if variant_index is not None:
                try:
                    self.app.diff_cmd_index = int(variant_index)
                except Exception as e:
                    self.printException(e)

            # Record current diff context for potential re-renders
            self.app.current_commit_sha = current_hash
            self.app.current_prev_sha = previous_hash
            self.app.current_diff_file = filename

            # Build and run the diff command
            try:
                cmd = self.app.build_diff_cmd(previous_hash, current_hash, filename)
                proc = subprocess.run(cmd, cwd=self.app.path, capture_output=True, text=True)
                diff_out = proc.stdout or proc.stderr or ""
            except Exception as exc:
                self.printException(exc, "running diff command")
                return

            # Clear and populate this ListView
            self.clear()
            header = ListItem(Label(Text(f"Comparing: {previous_hash}..{current_hash}", style="bold")))
            self.append(header)

            if diff_out:
                for line in diff_out.splitlines():
                    try:
                        if self.app.colorize_diff:
                            if line.startswith("+++") or line.startswith("---"):
                                styled_text = Text(line, style="bold white")
                            elif line.startswith("+"):
                                styled_text = Text(line, style="green")
                            elif line.startswith("-"):
                                styled_text = Text(line, style="red")
                            elif line.startswith("@@"):
                                styled_text = Text(line, style="cyan")
                            elif line.startswith("diff --git") or line.startswith("index "):
                                styled_text = Text(line, style="bold")
                            else:
                                styled_text = Text(line)
                        else:
                            styled_text = Text(line)
                        self.append(ListItem(Label(styled_text)))
                    except Exception as e:
                        self.printException(e)
                        continue
            else:
                try:
                    self.append(ListItem(Label(Text(f"No diff between {previous_hash}..{current_hash}"))))
                except Exception as e:
                    self.printException(e)

            self.styles.display = None
            self.index = 0

        except Exception as exc:
            self.printException(exc, "prepDiffList outer failure")

    def more_keys(self, event: events.Key) -> bool:  # DiffList
        """
        Handle left key to move focus back to History;
        handle PgUp/PgDn with visible selection; handle c/C to toggle colorization.

        Return True when the key was handled, False otherwise.
        """
        try:
            key = event.key
            logger.debug(f"DiffList.more_keys: key={key}")

            # Handle f/F: toggle fullscreen by delegating to left/right handlers
            if key and key.lower() == "f":
                try:
                    event.stop()
                    if self.app.is_diff_fullscreen():
                        # when fullscreen, left behavior exits fullscreen
                        self.key_left(event)
                    else:
                        # when not fullscreen, right behavior enters fullscreen
                        self.key_right(event)
                except Exception as e:
                    self.printException(e, "exception toggling fullscreen f/F")
                return True

            # Handle c/C to toggle colorization
            if key and key.lower() == "c":
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e)

                logger.debug(f"DiffList: c/C pressed, colorize_diff={self.app.colorize_diff}")
                try:
                    self.app.colorize_diff = not self.app.colorize_diff
                    logger.debug(f"DiffList: toggled to colorize_diff={self.app.colorize_diff}")

                    if self.app.current_commit_sha and self.app.current_prev_sha and self.app.current_diff_file:

                        logger.debug("DiffList: re-rendering diff with new colorization")
                        saved_scroll_y = self.scroll_y
                        saved_index = self.index

                        previous_hash = self.app.current_prev_sha
                        current_hash = self.app.current_commit_sha
                        filename = self.app.current_diff_file

                        try:
                            self.prepDiffList(filename, previous_hash, current_hash)
                        except Exception as e:
                            self.printException(e, "prepDiffList failed in c/C handler")

                        def restore_state():
                            try:
                                self.scroll_y = saved_scroll_y
                                if saved_index is not None:
                                    self.index = saved_index
                            except Exception as e:
                                self.printException(e)

                        self.call_after_refresh(restore_state)
                        logger.debug("DiffList: diff re-rendered successfully")
                    else:
                        logger.debug("DiffList: no current diff info available")

                except Exception as e:
                    self.printException(e, "exception in c/C handler")
                return True

            # Handle d/D to rotate diff command variant
            if key and key.lower() == "d":
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e)

                try:
                    variants = self.app.diff_variants
                    cur = self.app.diff_cmd_index
                    cur = (cur + 1) % max(1, len(variants))
                    self.app.diff_cmd_index = cur
                    logger.debug(f"DiffList: rotated diff_cmd_index to {cur}, variant={variants[cur]}")

                    if self.app.current_commit_sha and self.app.current_prev_sha and self.app.current_diff_file:

                        previous_hash = self.app.current_prev_sha
                        current_hash = self.app.current_commit_sha
                        filename = self.app.current_diff_file

                        saved_scroll_y = self.scroll_y
                        saved_index = self.index

                        try:
                            self.prepDiffList(filename, previous_hash, current_hash)
                        except Exception as e:
                            self.printException(e, "prepDiffList failed after rotating variant")

                        def restore_state():
                            try:
                                self.scroll_y = saved_scroll_y
                                if saved_index is not None:
                                    self.index = saved_index
                            except Exception as e:
                                self.printException(e)

                        self.call_after_refresh(restore_state)
                        logger.debug("DiffList: diff re-rendered after rotating variant")
                except Exception as e:
                    self.printException(e, "exception rotating diff variant")
                return True

        except Exception as e:
            self.printException(e)
        # let other keys be handled by default (up/down handled by ListView)
        return False

    # let other keys be handled by default (up/down handled by ListView)

    def key_left(self, event: events.Key) -> bool:  # DiffList
        """
        Handle left key behavior for DiffListBase.

        Returns True when the key was handled/consumed.
        """
        try:
            logger.debug(">>>> DiffList.key_left(): determining state to restore from diff")
            try:
                # If the Diff is fullscreen, exit fullscreen to the appropriate
                # history layout depending on whether the app started in
                # log-first mode. Otherwise, when not fullscreen, also choose
                # the correct history layout by `log_first`.
                if self.app.is_diff_fullscreen():
                    # if full screen, returning to smaller diff_list view,
                    # either history_file_diff or file_history_diff
                    if self.app.log_first:
                        layout = "history_file_diff"
                        focus = f"#{self.app.diff_list.id}"
                        footer = self.app.footer_diff3
                    else:
                        layout = "file_history_diff"
                        focus = f"#{self.app.diff_list.id}"
                        footer = self.app.footer_diff3
                else:
                    # not full screen, so returning to either history_file or file_history
                    if self.app.log_first:
                        layout = "history_file"
                        focus = f"#{self.app.repo_mode_file_list.id}"
                        footer = self.app.footer_file
                    else:
                        layout = "file_history"
                        focus = f"#{self.app.file_mode_history_list.id}"
                        footer = self.app.footer_history
                logger.debug(f">>>> DiffList.key_left(): restoring layout={layout} focus={focus} footer={footer}")
                self.app.change_state(layout, focus, footer)
            except Exception as e:
                self.printException(e, "error restoring state from diff")
        except Exception as e:
            self.printException(e, "unexpected exception in DiffListBase.key_left")
        return True

    def key_right(self, event: events.Key) -> bool:  # DiffList
        """
        Handle right key behavior for DiffListBase.
        Returns True when the key was handled/consumed.
        """
        try:
            # In columnated mode, pressing right expands Diff to fullscreen.
            if self.app.is_diff_fullscreen():
                # already fullscreen; right arrow does nothing
                return True
            else:
                # If diff is visible and not fullscreen, enter fullscreen
                logger.debug(">>>> change_state(diff_fullscreen): DiffList.key_right: entering diff fullscreen layout")
                self.app.change_state(
                    "diff_fullscreen",
                    "#diff-list",
                    self.app.footer_diff_full,
                )

        except Exception as e:
            self.printException(e, "unexpected exception")
        return True

    def on_focus(self, event: events.Focus) -> None:  # DiffList
        """When the DiffList receives focus, ensure the first item is highlighted."""
        try:

            def _apply() -> None:
                try:
                    nodes = self._nodes
                    if not nodes:
                        return
                    target = self.index if self.index is not None else 0
                    try:
                        self.index = None
                    except Exception as e:
                        self.printException(e)

                    try:
                        self.index = target
                    except Exception as e:
                        self.printException(e)

                except Exception as e:
                    self.printException(e)
                    return

            try:
                self.call_after_refresh(_apply)
            except Exception as e:
                self.printException(e)
                _apply()
        except Exception as e:
            self.printException(e)


class _TBDModal(ModalScreen):
    """Simple modal that shows a message (default "TBD") and closes on any key."""

    def __init__(self, message: str | None = None, **kwargs) -> None:  # TBDModal
        """Create the modal with an optional `message` to display."""
        super().__init__(**kwargs)
        self.message = message or "TBD"

    def compose(self) -> ComposeResult:  # TBDModal
        """Compose the modal contents (a single Static message)."""
        yield Static(Text(self.message, style="bold"), id="tbd-msg")

    def on_key(self, event: events.Key) -> None:  # TBDModal
        """Close the modal on any key press."""
        event.stop()
        self.app.pop_screen()


HELP_TEXT = """
Git Diff History Navigator Tool (gitdiffnavtool)
================================================

History
-------

I was doing a lot of "software archeology" on a repository to see what had changed
in various different commits into the code base. This tool was designed to help
in doing that.

Overview
--------
The Git Diff History Navigator Tool is a terminal Textual TUI that provides a three-column view for

* browsing a filesystem tree,
* viewing the git history for a selected file, and
* exploring the diffs between different versions.

The three columns are titled: Files (left), History (middle), Diff (right).

Type `q` or `Q` to exit the program.

Key features
------------

Arrow keys move up and down the various columns.
Left and Right arrow keys perform differently in each column.
Other keys have specific functions as described below.

- Files column: navigable directory listing; directories highlighted with a blue background.
  - Status markers & colors: files are prefixed with a short marker and colored by status:
    - `'\u00a0'` (space) tracked & clean — bright white
    - `U` untracked — bold yellow
    - `M` modified — yellow
    - `A` staged (index changes) — cyan
    - `D` deleted in working tree — red
    - `I` ignored — dim italic
    - `!` conflicted — magenta

  - A Right Arrow will
    - (for files) open the History column for the current filename
    - (for directories) navigates to the current directory name.

  - A Left Arrow on the directory ".." will navigate to the parent directory.

- History column:
  - Lines are populated from `git log --follow`.
  - Pseudo-log entries `STAGED` and `MODS` are inserted at the top when the file has been staged, and when there are uncommitted/unstaged modifications, respectively.
  - Press `m` (or `M`) to _mark_ the current log row with a leading `✓`.
    - Only one history row may be checked at a time — toggling a new row clears any prior checkmark.
  - A Right Arrow will
    - open the Diff column for the currently highlighted log entry against the checkmarked entry (if there is a checkmarked entry) or the next entry in the list.
  - A Left Arrow will close the History column.

- Diff column:
  - Lines are populated using `git diff` between the two hashes (or pseudo-hashes for staged and modified unstaged versions).
  - A header line indicates the two hashes being compared, e.g.:
    `Comparing: <old_hash>..<new_hash>`.
  - The order is always the lower list item vs the higher item, so diffs read `older..newer`.
  - A Left Arrow will close the Diff column.
  - Press `d` (or `D`) while focused in the Diff column to rotate the diff command variant. The variants cycle through:
    - `git diff` (default),
    - `git diff --ignore-space-change`, and
    - `git diff --diff-algorithm=patience`.

Running
-------
Run the application as follows:

`gitdiffnavtool.py [--no-color] [--log-first] [path]`

If `--no-color` is provided, the diff output will not be colorized.

`[path]` is optional — it defaults to the current working directory. 
If a filename is provided, the app will open its directory and populate the History column for that file on startup.

If `--log-first` is provided, the app starts with a repository-wide commit log in the History column.
- Right arrow from the History column opens a Files column showing the changed files for the selected commit.
"""


class HelpList(AppBase):
    """Help column showing usage and short docs.

    The contents are a plain listing derived from the README.
    """

    def on_mount(self) -> None:  # HelpList
        """Populate help content."""
        try:
            # Render HELP_TEXT using Rich's Markdown and add each paragraph as
            # a separate ListItem so AppBase navigation (pageup/pagedown)
            # continues to work on the ListView.
            blocks = [b.strip() for b in re.split(r"\n\s*\n", HELP_TEXT.strip()) if b.strip()]
            for block in blocks:
                md = Markdown(block)
                lbl = Label(md)
                self.append(ListItem(lbl))
        except Exception as e:
            self.printException(e, "HelpList.on_mount failed to render Markdown")

    def more_keys(self, event: events.Key) -> bool:  # HelpList
        """
        Handle keys - go back to files view on any key.
        Navigation (up/down/page)keys are handled by AppBase.

        Return True when the key is handled here and should not be processed
        further; otherwise return False to allow default handling.
        """
        try:
            key = event.key
            logger.debug(f"HelpList.more_keys: key={key}")

            # Any key dismisses help: pop the help layout and restore previous focus
            try:
                try:
                    event.stop()
                    logger.debug(
                        ">>>> HelpList.more_keys: dismissing help, restoring prior layout and focus using restore_state()"
                    )
                    try:
                        self.app.restore_state()
                    except Exception as e:
                        # If no saved state, fall back to a sensible default
                        self.printException(
                            e, "restore_state failed in HelpList.more_keys; falling back to file_fullscreen"
                        )
                        try:
                            self.app.change_state(
                                "file_fullscreen", f"#{self.app.file_mode_file_list.id}", self.app.footer_file
                            )
                        except Exception as e:
                            self.printException(e, "fallback change_state failed in HelpList.more_keys")
                except Exception as e:
                    self.printException(e, "could not restore state when dismissing help")
                return True
            except Exception as e:
                self.printException(e)
                return False
        except Exception as e:
            self.printException(e)
            return False


class GitHistoryTool(App):
    """
    Main Textual application providing the three-column git navigator.

    The app composes three columns: `Files`, `History`, and `Diff`. It builds a
    repository cache (using `pygit2`) and handles keyboard
    navigation and git operations to populate history and diffs.
    """

    TITLE = "Git Diff History Navigator Tool"

    # Block the Ctrl+P palette shortcut.
    ENABLE_COMMAND_PALETTE = False

    # CSS: reserve one line for `#title` and let the main Horizontal flex to fill rest
    CSS = """
        /* Disable scrolling on the app itself - only columns should scroll */
        App {
            overflow: hidden;
            scrollbar-size: 0 0;
        }
        /* Reserve a one-line title bar for the app name */
        #title {
            height: 1;
            padding: 0 1;
            width: 100%;
            text-align: center;
        }
        /* Let the layout determine main area height so footer remains visible */
        /* Column widget ids */
        #left-file-list {
            border: solid white;
            scrollbar-size-vertical: 1;
        }
        #left-history-list {
            border: solid white;
            scrollbar-size-vertical: 1;
        }
        #right-history-list {
            border: heavy #555555;
            scrollbar-size-vertical: 1;
        }
        #right-file-list {
            border: heavy #555555;
            scrollbar-size-vertical: 1;
        }
        #diff-list {
            border: heavy #555555;
            scrollbar-size-vertical: 1;
        }
        #help-list {
            border: heavy #555555;
            scrollbar-size-vertical: 1;
        }
        /* footer area: show quit and navigation hints */
        #footer {
            height: 1;
            padding: 0 1;
            text-align: left;
        }

        /* Active title styling: highlighted focused column */
        #left-file-title.active,
        #left-history-title.active,
        #right-history-title.active,
        #right-file-title.active,
        #diff-title.active,
        #help-title.active {
            text-style: bold reverse;
        }
        """

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(  # GitHistoryTool
        self, path: Optional[str] = None, colorize_diff: bool = True, log_first: bool = False, **kwargs
    ) -> None:
        """
        Initialize the app state.

        If `path` names a file, treat its directory as the working path and
        remember the filename to open its history on mount.
        """
        # Ensure `log_first` is available before `compose` runs in the
        # Textual `App` initialization so the UI can be composed with the
        # correct column ordering when starting in log-first mode.
        logger.debug("GitHistoryTool.__init__ starts")
        self.log_first: bool = bool(log_first)
        super().__init__(**kwargs)
        logger.debug("GitHistoryTool.__init__ continues after super().__init()")
        # If the provided path is a file, treat its directory as the app path
        # and remember the filename so we can immediately open its history.
        given = path or os.getcwd()
        if os.path.isfile(given):
            self.initial_file = os.path.basename(given)
            self.path = os.path.abspath(os.path.dirname(given) or os.getcwd())
        else:
            self.initial_file = None
            self.path = os.path.abspath(given)
        # store the full path that will be displayed at startup
        self.displayed_path = self.path
        # repository cache populated at mount
        self.repo_available = False
        self.repo_root: Optional[str] = None
        self.repo_index_set: set[str] = set()
        self.repo_status_map: dict[str, int] = {}
        # per-file index mtime map (path -> mtime seconds)
        self.repo_index_mtime_map: dict[str, float] = {}
        # Current layout/focus/footer tracking for single-value save/restore
        self._current_layout: Optional[str] = None
        self._current_focus: Optional[str] = None
        self._current_footer: Optional[Text] = None
        # single-slot saved state used by save_state/restore_state
        self._saved_state: Optional[tuple[Optional[str], Optional[str], Optional[Text]]] = None
        # colorization state and current diff info
        self.colorize_diff = colorize_diff
        self.current_commit_sha: Optional[str] = None
        self.current_prev_sha: Optional[str] = None
        self.current_diff_file: Optional[str] = None
        # Diff fullscreen flag: when True, Diff column occupies 100% width
        # removed: `diff_fullscreen` flag is now derived from `layout_stack`
        # Diff command variants and current selection index
        # None = default `git diff`; other entries are flags inserted after `git diff`
        self.diff_variants: list[Optional[str]] = [None, "--ignore-space-change", "--diff-algorithm=patience"]
        self.diff_cmd_index: int = 0
        # Standard footer texts used throughout the app (one per column/type)
        self.footer_file: Text = Text("File: q(uit)  s(wap)  ?/h(elp)  ← ↑/↓/PgUp/PgDn/Begin/End", style="bold")
        self.footer_history: Text = Text(
            "History: q(uit)  s(wap)  ?/h(elp)  ← ↑/↓/ PgUp/PgDn/Begin/End  →  m(ark)", style="bold"
        )
        self.footer_diff3: Text = Text(
            "Diff: q(uit)  ?/h(elp)  ← ↑/↓/PgUp/PgDn/Begin/End →/f(ull) c(olor) d(iff-type)", style="bold"
        )
        self.footer_diff_full: Text = Text(
            "Diff: q(uit)  ?/h(elp)  ←/f(ull) ↑/↓/PgUp/PgDn/Begin/End c(olor) d(iff-type)", style="bold"
        )
        self.footer_help: Text = Text("Help: q(uit)  ↑/↓/PgUp/PgDn/Begin/End  Press any key to return", style="bold")
        # start the app showing repository-wide commit log first when True
        logger.debug("GitHistoryTool.__init__ ends")

    def printException(self, e, msg=None):  # GitHistoryTool
        """Log an exception from the app context (mirrors AppBase.printException)."""
        className = type(self).__name__
        funcName = sys._getframe(1).f_code.co_name
        msg = msg if msg else "???"
        logger.warning(f"WARNING: {className}.{funcName} ({str(e)}): {msg}")
        logger.warning(traceback.format_exc())
        try:
            tb = "".join(traceback.format_exception(type(e), e, getattr(e, "__traceback__", None)))
            logger.warning(tb)
        except Exception:
            pass

    # Layout helpers on the App so widgets can call `self.app.layout_*`.
    def _apply_column_layout(  # GitHistoryTool
        self,
        left_file_w: int,
        left_history_w: int,
        right_history_w: int,
        right_file_w: int,
        diff_w: int,
        help_w: int,
    ) -> None:
        # Maintainable visibility tokens:
        # `show` clears an override (lets the CSS decide),
        # `hide` forces display:none
        show = None
        hide = "none"
        logger.debug(
            f"GitHistoryTool._apply_column_layout widths: lf={left_file_w}, lh={left_history_w}, "
            f"rh={right_history_w}, rf={right_file_w}, d={diff_w}, h={help_w}"
        )

        try:
            try:
                c1 = self.query_one("#left-file-column")
                c1.styles.width = f"{left_file_w}%"
                c1.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set left-file-column")
            try:
                c2 = self.query_one("#left-history-column")
                c2.styles.width = f"{left_history_w}%"
                c2.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set left-history-column")
            try:
                c3 = self.query_one("#right-history-column")
                c3.styles.width = f"{right_history_w}%"
                c3.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set right-history-column")
            try:
                c4 = self.query_one("#right-file-column")
                c4.styles.width = f"{right_file_w}%"
                c4.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set right-file-column")
            try:
                c5 = self.query_one("#diff-column")
                c5.styles.width = f"{diff_w}%"
                c5.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set diff-column")
            try:
                c6 = self.query_one("#help-column")
                c6.styles.width = f"{help_w}%"
                c6.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set help-column")

            try:
                # Directly set displays on the canonical, already-resolved widgets.
                try:
                    self.file_mode_file_list.styles.display = show if left_file_w else hide
                    logger.debug(
                        "_apply_column_layout: left-file-list id=%s parent=%s display=%r",
                        getattr(self.file_mode_file_list, "id", None),
                        getattr(getattr(self.file_mode_file_list, "parent", None), "id", None),
                        getattr(self.file_mode_file_list.styles, "display", None),
                    )
                except Exception as e:
                    self.printException(e, "could not set left-file-list display in _apply_column_layout")
                try:
                    self.repo_mode_history_list.styles.display = show if left_history_w else hide
                    logger.debug(
                        "_apply_column_layout: left-history-list id=%s parent=%s display=%r",
                        getattr(self.repo_mode_history_list, "id", None),
                        getattr(getattr(self.repo_mode_history_list, "parent", None), "id", None),
                        getattr(self.repo_mode_history_list.styles, "display", None),
                    )
                except Exception as e:
                    self.printException(e, "could not set left-history-list display in _apply_column_layout")
                try:
                    self.file_mode_history_list.styles.display = show if right_history_w else hide
                    logger.debug(
                        "_apply_column_layout: right-history-list id=%s parent=%s display=%r",
                        getattr(self.file_mode_history_list, "id", None),
                        getattr(getattr(self.file_mode_history_list, "parent", None), "id", None),
                        getattr(self.file_mode_history_list.styles, "display", None),
                    )
                except Exception as e:
                    self.printException(e, "could not set right-history-list display in _apply_column_layout")
                try:
                    self.repo_mode_file_list.styles.display = show if right_file_w else hide
                    logger.debug(
                        "_apply_column_layout: right-file-list id=%s parent=%s display=%r",
                        getattr(self.repo_mode_file_list, "id", None),
                        getattr(getattr(self.repo_mode_file_list, "parent", None), "id", None),
                        getattr(self.repo_mode_file_list.styles, "display", None),
                    )
                except Exception as e:
                    self.printException(e, "could not set right-file-list display in _apply_column_layout")
                try:
                    # single canonical diff widget
                    self.diff_list.styles.display = show if diff_w else hide
                    logger.debug(
                        "_apply_column_layout: diff-list id=%s parent=%s display=%r",
                        getattr(self.diff_list, "id", None),
                        getattr(getattr(self.diff_list, "parent", None), "id", None),
                        getattr(self.diff_list.styles, "display", None),
                    )
                except Exception as e:
                    self.printException(e, "could not set diff-list display in _apply_column_layout")
                try:
                    # help-list must exist after allocation
                    self.help_list.styles.display = show if help_w else hide
                    logger.debug(
                        "_apply_column_layout: help-list id=%s parent=%s display=%r",
                        getattr(self.help_list, "id", None),
                        getattr(getattr(self.help_list, "parent", None), "id", None),
                        getattr(self.help_list.styles, "display", None),
                    )
                except Exception as e:
                    self.printException(e, "could not set help-list display in _apply_column_layout")
            except Exception as e:
                self.printException(e, "could not assign displays in _apply_column_layout")

        except Exception as e:
            self.printException(e, "error applying column layout")

    def change_layout(self, newlayout: str) -> None:  # GitHistoryTool
        """
        Change column layout using a named layout.

        Valid names: "left_fullscreen", "file_history", "history_file",
        "file_history_diff", "history_file_diff", "diff_fullscreen", "help_fullscreen".
        """
        try:
            logger.debug(f"change_layout: newlayout={newlayout}")
            # Maintainable visibility tokens:
            # `show` clears an override (lets the CSS decide),
            # `hide` forces display:none
            show = None
            hide = "none"
            if newlayout == "file_fullscreen":
                # show left-file-list only
                self._apply_column_layout(100, 0, 0, 0, 0, 0)
            elif newlayout == "history_fullscreen":
                # show left-history-list only
                self._apply_column_layout(0, 100, 0, 0, 0, 0)
            elif newlayout == "file_history":
                # left-file-list (25%), right-history-list (75%), others hidden
                self._apply_column_layout(25, 0, 75, 0, 0, 0)
            elif newlayout == "history_file":
                # left-history-list then right-file-list
                self._apply_column_layout(0, 25, 0, 75, 0, 0)
            elif newlayout == "file_history_diff":
                # show left-file, left-history, diff
                self._apply_column_layout(5, 0, 20, 0, 75, 0)
            elif newlayout == "history_file_diff":
                # show left-history, right-file, diff
                self._apply_column_layout(0, 5, 0, 20, 75, 0)
            elif newlayout == "diff_fullscreen":
                self._apply_column_layout(0, 0, 0, 0, 100, 0)
            elif newlayout == "help_fullscreen":
                # Show only the Help column
                self._apply_column_layout(0, 0, 0, 0, 0, 100)
            else:
                raise ValueError(f"unknown layout: {newlayout}")
            # record current layout for save/restore semantics
            try:
                self._current_layout = newlayout
            except Exception as e:
                self.printException(e, "setting _current_layout in change_layout")
        except Exception as e:
            self.printException(e, f"change_layout {newlayout}")

    def is_diff_fullscreen(self) -> bool:  # GitHistoryTool
        """
        Return True when the current layout is `diff_fullscreen`.

        Uses the tracked `_current_layout` value instead of a stack.
        """
        try:
            ret = bool(self._current_layout == "diff_fullscreen")
            logger.debug(f"is_diff_fullscreen: returning {ret} for current_layout={self._current_layout}")
            return ret
        except Exception as e:
            self.printException(e)
            return False

    def change_state(
        self, layout: Optional[str] = None, focus: Optional[str] = None, footer: Optional[Text | str] = None
    ) -> None:
        """Change to the provided layout/focus/footer immediately.

        This replaces the previous push_state behavior by directly applying
        the requested layout, focus, and footer using existing helpers.
        """
        try:
            logger.debug(f"change_state(layout={layout}, focus={focus}, footer={footer}) - applying requested changes")

            # Always apply requested changes unconditionally. Deduplication of
            # duplicate key events is handled at the key-handler layer using
            # the event.time value.
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
        except Exception as e:
            self.printException(e, "change_state outer failure")

    def save_state(self) -> None:
        """Save the current single-value state (layout, focus, footer).

        This is a single-slot save; calling multiple times overwrites the slot.
        """
        try:
            self._saved_state = (self._current_layout, self._current_focus, self._current_footer)
            logger.debug(f"save_state: saved={self._saved_state}")
        except Exception as e:
            self.printException(e, "save_state failed")

    def restore_state(self) -> None:
        """Restore the state saved by `save_state`.

        Raises RuntimeError if no saved state exists.
        """
        try:
            if self._saved_state is None:
                raise RuntimeError("restore_state called without a prior save_state")
            layout, focus, footer = self._saved_state
            logger.debug(f"restore_state: restoring layout={layout} focus={focus} footer={footer}")
            try:
                self.change_state(layout, focus, footer)
            except Exception as e:
                self.printException(e, "restore_state.change_state failed")
            # clear saved slot after restore
            self._saved_state = None
        except Exception as e:
            self.printException(e, "restore_state failed")

    def change_focus(self, target: str) -> None:  # GitHistoryTool
        """Change focus to the given widget id (safely)."""
        try:
            logger.debug(f"change_focus: target={target}")

            def _do():
                sel = str(target)
                if sel.startswith("#"):
                    key = sel[1:]
                else:
                    key = sel

                widget = None
                label_name = None

                # Reset the known title labels to a neutral bold style so
                # we can highlight the focused column title afterwards.
                try:
                    title_ids = [
                        "left-file-title",
                        "left-history-title",
                        "right-history-title",
                        "right-file-title",
                        "diff-title",
                        "help-title",
                    ]
                    for tid in title_ids:
                        try:
                            lbl = self.query_one(f"#{tid}", Label)
                            try:
                                lbl.set_class(False, "active")
                            except Exception:
                                try:
                                    # fallback older API
                                    lbl.remove_class("active")
                                except Exception:
                                    pass
                        except Exception:
                            # missing title label: ignore and continue
                            pass
                except Exception as e:
                    self.printException(e, "change_focus resetting title label classes failed")

                # Accept canonical ids (no '#') and capture the title id
                if key == "left-file-list":
                    widget = self.file_mode_file_list
                    label_name = "left-file-title"
                elif key == "left-history-list":
                    widget = self.repo_mode_history_list
                    label_name = "left-history-title"
                elif key == "right-file-list":
                    widget = self.repo_mode_file_list
                    label_name = "right-file-title"
                elif key == "right-history-list":
                    widget = self.file_mode_history_list
                    label_name = "right-history-title"
                elif key == "diff-list":
                    widget = self.diff_list
                    label_name = "diff-title"
                elif key == "help-list":
                    widget = self.help_list
                    label_name = "help-title"
                else:
                    logger.warning(f"change_focus: unknown canonical focus target {target}")
                    return

                try:
                    widget.focus()
                    logger.debug(
                        f"change_focus: focused resolved id={getattr(widget,'id',None)} type={type(widget)!r}"
                    )

                    # When focusing a files column, prefer to highlight the
                    # currently-selected diff file (if any). Fall back to the
                    # minimum selectable index otherwise. Use call_after_refresh
                    # so the DOM is stable before mutating selection.
                    try:
                        if widget in (getattr(self, "file_mode_file_list", None), getattr(self, "repo_mode_file_list", None)):
                            # If the widget already has a valid selection, preserve it.
                            try:
                                cur_idx = getattr(widget, "index", None)
                            except Exception:
                                cur_idx = None
                            min_idx = getattr(widget, "_min_index", 0) or 0
                            # Only set/reset index if missing or below the minimum selectable index.
                            if cur_idx is None or cur_idx < min_idx:
                                fname = getattr(self, "current_diff_file", None)
                                if fname:
                                    logger.debug("change_focus: scheduling highlight of %s in %s (no existing valid index)", fname, getattr(widget, 'id', None))
                                    try:
                                        widget.call_after_refresh(lambda: widget._highlight_filename(fname))
                                    except Exception as e:
                                        try:
                                            self.call_after_refresh(lambda: widget._highlight_filename(fname))
                                        except Exception as e2:
                                            try:
                                                widget._highlight_filename(fname)
                                            except Exception as e3:
                                                self.printException(e3, "change_focus: immediate highlight failed")
                                else:
                                    logger.debug("change_focus: setting index to min_idx=%s for %s (no existing valid index)", min_idx, getattr(widget, 'id', None))
                                    try:
                                        widget.index = min_idx
                                    except Exception as e:
                                        self.printException(e, "change_focus: setting widget.index failed")
                            # Always try to reset scroll to top to make selection visible.
                            try:
                                widget.scroll_y = 0
                            except Exception as e:
                                self.printException(e, "change_focus: setting scroll_y failed")
                    except Exception as e:
                        self.printException(e, "change_focus: error handling file widget selection")

                    try:
                        if hasattr(widget, "index") and (
                            getattr(widget, "index", None) is None or getattr(widget, "index") < 0
                        ):
                            widget.index = 0
                    except Exception as e:
                        self.printException(e, "change_focus: normalizing widget.index failed")
                    # After focusing, update the corresponding title label
                    try:
                        if label_name:
                            try:
                                title_lbl = self.query_one(f"#{label_name}", Label)
                                try:
                                    title_lbl.set_class(True, "active")
                                except Exception as e:
                                    try:
                                        title_lbl.add_class("active")
                                    except Exception as e2:
                                        self.printException(e2, f"could not add_class on title label {label_name}")
                            except Exception as e:
                                self.printException(e, f"could not update title label {label_name}")
                    except Exception as e:
                        self.printException(e, "change_focus: updating title label failed")

                    return
                except Exception as e:
                    self.printException(e, f"could not focus resolved widget for {target}")

                logger.warning(f"change_focus: no matching focus target for {target}")

            try:
                self.call_after_refresh(_do)
            except Exception as e:
                self.printException(e)
                _do()
        except Exception as e:
            self.printException(e, "change_focus outer failure")
        try:
            # record the desired focus target for save/restore semantics
            sel = str(target)
            if sel.startswith("#"):
                key = sel[1:]
            else:
                key = sel
            try:
                self._current_focus = f"#{key}"
            except Exception as e:
                self.printException(e, "setting _current_focus in change_focus")
        except Exception as e2:
            self.printException(e2, "outer failure in change_focus setting _current_focus")

    def _normalize_footer(self, value: Text | str) -> Text:
        try:
            if isinstance(value, Text):
                return value
            return Text(str(value))
        except Exception as e:
            self.printException(e)
            return Text(str(value))

    def change_footer(self, value: Text | str) -> None:  # GitHistoryTool
        """Set the footer to `value` (Text or str) immediately."""
        try:
            logger.debug(f"change_footer({value})")
            txt = self._normalize_footer(value)
            try:
                # Prefer attribute-backed footer update helper; update label if present
                footer = None
                try:
                    footer = self.query_one("#footer", Label)
                except Exception as e:
                    self.printException(e)
                    footer = None
                if footer is not None:
                    footer.update(txt)
                else:
                    logger.debug("change_footer: footer label not found")
            except Exception as e:
                self.printException(e, "could not update footer in change_footer")
            try:
                # record current footer value for save/restore semantics
                self._current_footer = txt
            except Exception as e2:
                self.printException(e2, "setting _current_footer in change_footer")
        except Exception as e:
            self.printException(e, "change_footer outer failure")

    def _choose_hash_in_history(self, history_widget: HistoryListBase, cur_hash: Optional[str], prev_hash: Optional[str]) -> None:
        """
        Select or check the appropriate commit in a HistoryList widget.

        Prefer selecting the `cur_hash` if found; otherwise check `prev_hash`.
        Matching is done via startswith so short/long hashes both work.
        """
        try:
            if history_widget is None:
                return
            nodes = getattr(history_widget, "_nodes", None) or []
            if not nodes:
                return
            cur_idx = None
            prev_idx = None
            for i, node in enumerate(nodes):
                try:
                    h = getattr(node, "_hash", None)
                    if not h:
                        # try to parse from text
                        t = history_widget.text_of(node)
                        m = re.match(r"^\s*\S+\s+([0-9a-fA-F]+)\b", t)
                        if m:
                            h = m.group(1)
                    if h and cur_hash and (h.startswith(cur_hash) or cur_hash.startswith(h)):
                        cur_idx = i
                        break
                    if h and prev_hash and (h.startswith(prev_hash) or prev_hash.startswith(h)):
                        prev_idx = i
                except Exception as e:
                    self.printException(e, "_choose_hash_in_history iterating nodes")
                    continue

            try:
                if cur_idx is not None:
                    history_widget.index = cur_idx
                    # ensure not checked
                    try:
                        nodes[cur_idx]._checked = False
                    except Exception as e:
                        self.printException(e, "_choose_hash_in_history setting _checked False")
                elif prev_idx is not None:
                    history_widget.index = prev_idx
                    try:
                        # mark previous as checked
                        nodes[prev_idx]._checked = True
                    except Exception as e:
                        self.printException(e, "_choose_hash_in_history setting _checked True")
                else:
                    history_widget.index = 0
            except Exception as e2:
                self.printException(e2, "_choose_hash_in_history setting index")
        except Exception as e:
            self.printException(e, "_choose_hash_in_history")

    def _highlight_filename_in_filelist(self, filelist_widget: FileListBase, filename: Optional[str]) -> None:
        try:
            if not filelist_widget or not filename:
                return
            try:
                filelist_widget.call_after_refresh(lambda: filelist_widget._highlight_filename(filename))
            except Exception as e:
                self
                try:
                    filelist_widget._highlight_filename(filename)
                except Exception as e2:
                    self.printException(e2, "_highlight_filename_in_filelist inner failure")
        except Exception as e:
            self.printException(e, "_highlight_filename_in_filelist")

    def build_diff_cmd(self, prev: str | None, curr: str | None, fname: str) -> list[str]:  # GitHistoryTool
        """
        Construct the git diff command honoring the currently selected variant.

        The variant (if not None) is inserted right after `git diff` so that
        options like `--ignore-space-change` and `--diff-algorithm=patience`
        are applied to the invoked command.
        """

        def _is_pseudo(h: str | None) -> bool:  # GitHistoryTool
            return h in ("STAGED", "MODS")

        try:
            flag = self.diff_variants[self.diff_cmd_index] if self.diff_variants else None
            # Handle staged/modified pseudo-entries first
            if _is_pseudo(prev) or _is_pseudo(curr):
                if (prev == "STAGED" and curr == "MODS") or (prev == "MODS" and curr == "STAGED"):
                    cmd = ["git", "diff", "--", fname]
                    if flag:
                        cmd.insert(2, flag)
                    return cmd

                if curr == "MODS" and prev and not _is_pseudo(prev):
                    cmd = ["git", "diff", prev, "--", fname]
                    if flag:
                        cmd.insert(2, flag)
                    return cmd
                if prev == "MODS" and curr and not _is_pseudo(curr):
                    cmd = ["git", "diff", curr, "--", fname]
                    if flag:
                        cmd.insert(2, flag)
                    return cmd

                if curr == "STAGED" and prev and not _is_pseudo(prev):
                    cmd = ["git", "diff", "--cached", prev, "--", fname]
                    if flag:
                        cmd.insert(2, flag)
                    return cmd
                if prev == "STAGED" and curr and not _is_pseudo(curr):
                    cmd = ["git", "diff", "--cached", curr, "--", fname]
                    if flag:
                        cmd.insert(2, flag)
                    return cmd

                if curr == "STAGED" and prev is None:
                    cmd = ["git", "diff", "--cached", "--", fname]
                    if flag:
                        cmd.insert(2, flag)
                    return cmd
                if curr == "MODS" and prev is None:
                    cmd = ["git", "diff", "--", fname]
                    if flag:
                        cmd.insert(2, flag)
                    return cmd

            # Default: two real commits/hashes
            if prev and curr:
                cmd = ["git", "diff", prev, curr, "--", fname]
                if flag:
                    cmd.insert(2, flag)
                return cmd
        except Exception as e:
            self.printException(e, "exception building command")
        # Fallback
        return ["git", "diff", "--", fname]

    def build_repo_cache(self) -> None:  # GitHistoryTool
        """
        Discover repository (if any) and build in-memory index/status maps.
        """
        logger.debug("build_repo_cache()")
        self.repo_available = False
        self.repo_root = None
        self.repo_index_set = set()
        self.repo_status_map = {}
        self.repo_index_mtime_map = {}

        try:
            # discover repo from current path
            gitdir = pygit2.discover_repository(self.path)
            if not gitdir:
                return
            repo = pygit2.Repository(gitdir)
            workdir = repo.workdir
            if not workdir:
                return
            self.repo_root = os.path.abspath(workdir)
            # index: tracked files
            try:
                idx = repo.index
                self.repo_index_set = {entry.path for entry in idx}
            except Exception as e:
                self.printException(e)
                self.repo_index_set = set()
            # per-index-entry mtime map (best-effort)
            try:
                idx = repo.index
                mmap: dict[str, float] = {}
                for entry in idx:
                    try:
                        mtime_val = None
                        # pygit2 index entry may expose mtime directly or as a tuple/object
                        if hasattr(entry, "mtime"):
                            mtime_val = getattr(entry, "mtime")
                        # normalize common shapes
                        if isinstance(mtime_val, tuple) and len(mtime_val) >= 1:
                            mtime_val = mtime_val[0]
                        elif hasattr(mtime_val, "seconds"):
                            mtime_val = getattr(mtime_val, "seconds")
                        elif hasattr(mtime_val, "tv_sec"):
                            mtime_val = getattr(mtime_val, "tv_sec")
                        # fallback: stat the working copy file
                        if not mtime_val:
                            try:
                                p = os.path.join(self.repo_root, entry.path)
                                mtime_val = os.path.getmtime(p)
                            except Exception as e:
                                self.printException(e)
                                mtime_val = None
                        if mtime_val:
                            mmap[entry.path] = float(mtime_val)
                    except Exception as e:
                        self.printException(e)
                        continue
                self.repo_index_mtime_map = mmap
            except Exception as e:
                self.printException(e)
                self.repo_index_mtime_map = {}

            # status: mapping path -> flags
            try:
                status_map = repo.status()
                # keys are paths relative to repo root
                self.repo_status_map = {k: int(v) for k, v in status_map.items()}
            except Exception as e:
                self.printException(e)
                self.repo_status_map = {}

            self.repo_available = True
        except Exception as e:
            self.printException(e)
            # leave as not available
            self.repo_available = False

    def compose(self) -> ComposeResult:  # GitHistoryTool
        """Compose the app UI: title, four-column layout, and footer hints."""
        logger.debug("compose: start composing UI")
        with Vertical(id="root"):
            yield Label(Text(self.TITLE, style="bold"), id="title")
            with Horizontal(id="main"):
                # Six dedicated columns in fixed order:
                # left-file-list, left-history-list, right-history-list,
                # right-file-list, diff-list, help-list
                with Vertical(id="left-file-column"):
                    yield Label(Text("Files", style="bold"), id="left-file-title")
                    yield FileModeFileList(id="left-file-list")
                with Vertical(id="left-history-column"):
                    yield Label(Text("History", style="bold"), id="left-history-title")
                    yield RepoModeHistoryList(id="left-history-list")
                with Vertical(id="right-history-column"):
                    yield Label(Text("History", style="bold"), id="right-history-title")
                    yield FileModeHistoryList(id="right-history-list")
                with Vertical(id="right-file-column"):
                    yield Label(Text("Files", style="bold"), id="right-file-title")
                    yield RepoModeFileList(id="right-file-list")
                with Vertical(id="diff-column"):
                    yield Label(Text("Diff", style="bold"), id="diff-title")
                    yield DiffList(id="diff-list")
                with Vertical(id="help-column"):
                    yield Label(Text("Help", style="bold"), id="help-title")
                    yield HelpList(id="help-list")

            # GitHistoryTool footer (placed outside  so it always sits below columns)
        yield Label(self.footer_file, id="footer")
        try:
            logger.debug("compose: finished composing UI")
        except Exception as e:
            self.printException(e)
            pass

    async def on_mount(self) -> None:  # GitHistoryTool
        """
        Mount-time initialization: build repo cache and populate Files.

        This method configures initial layout sizes, builds the repository
        cache, and sets the initial path listing. If the app was launched with
        a filename, it will also open that file's history.
        """
        # Resolve references to the six canonical widgets composed in `compose()`
        logger.debug("GitHistoryTool.on_mount()")
        try:
            # Resolve the six canonical widgets composed in `compose()`.
            # If any composed widget is missing or the query fails, abort
            # by allowing the exception to propagate to the outer handler
            # which converts it to a RuntimeError. This avoids creating
            # stray unmounted fallback widgets that are not in the DOM.
            self.file_mode_file_list = self.query_one("#left-file-list", FileListBase)
            logger.debug(
                f"on_mount: found composed file_mode_file_list id={getattr(self.file_mode_file_list,'id',None)}"
            )

            # left-history-column should be the repository-wide history view
            self.repo_mode_history_list = self.query_one("#left-history-list", RepoModeHistoryList)
            logger.debug(
                f"on_mount: found composed repo_mode_history_list id={getattr(self.repo_mode_history_list,'id',None)}"
            )

            # right-history-column is the file-scoped history view
            self.file_mode_history_list = self.query_one("#right-history-list", FileModeHistoryList)
            logger.debug(
                f"on_mount: found composed file_mode_history_list id={getattr(self.file_mode_history_list,'id',None)}"
            )

            self.repo_mode_file_list = self.query_one("#right-file-list", FileListBase)
            logger.debug(
                f"on_mount: found composed repo_mode_file_list id={getattr(self.repo_mode_file_list,'id',None)}"
            )

            self.diff_list = self.query_one("#diff-list", DiffList)
            logger.debug(f"on_mount: found composed diff_list id={getattr(self.diff_list,'id',None)}")

            self.help_list = self.query_one("#help-list", HelpList)
            logger.debug(f"on_mount: found composed help_list id={getattr(self.help_list,'id',None)}")
        except Exception as e:
            # Fail fast: composition did not produce the expected widgets.
            raise RuntimeError(f"Critical widget allocation/resolution failure: {e}") from e
        # Eager queries for right1/right2/right3 removed — query these widgets on demand.
        try:
            logger.debug("on_mount: finished initial widget resolution")
        except Exception as e:
            self.printException(e)
            pass
        # Ensure the main horizontal fills remaining space so the title remains visible
        try:
            # ensure root flexes so footer remains visible (do not force 100% height)
            root = self.query_one("#root")
            root.styles.height = None
            root.styles.flex = 1
        except Exception as e:
            self.printException(e)

        try:
            main = self.query_one("#main")
            main.styles.flex = 1
            # do not force 100% height here; allow footer to occupy its line
            main.styles.height = None
        except Exception as e:
            self.printException(e)

        # build repository cache (pygit2-based) before populating file list
        try:
            self.build_repo_cache()
        except Exception as e:
            self.printException(e)

        if self.log_first:
            try:
                self.repo_mode_history_list.prepRepoModeHistoryList()
                # Resolve the left target id from attributes (direct access)
                left_widget = self.repo_mode_history_list if self.log_first else self.file_mode_file_list
                left_target = f"#{left_widget.id}" if left_widget is not None else "#left-file-list"
                logger.debug(
                    f"on_mount: setting initial history_fullscreen state (log_first) left_target={left_target}"
                )
                self.change_state(
                    "history_fullscreen",
                    left_target,
                    self.footer_history,
                )
            except Exception as e3:
                self.printException(e3, "outer failure after push_state in log_first")
        else:
            try:
                self.file_mode_file_list.prepFileModeFileList(self.path)
                # initialize current state to file_fullscreen
                self.change_state("file_fullscreen", f"#{self.file_mode_file_list.id}", self.footer_file)
                if self.initial_file:
                    self.file_mode_history_list.prepFileModeHistoryList(self.initial_file)
                    self.change_state("file_history", f"#{self.file_mode_history_list.id}", self.footer_history)

            except Exception as e:
                self.printException(e)

    def on_key(self, event: events.Key) -> None:  # GitHistoryTool
        """
        Global key handler.

        - Accept uppercase `Q` as a quit key in addition to lowercase `q`.
        """
        logger.debug(f"GitHistoryTool.on_key: key={event.key}")
        try:
            key = event.key
            try:
                # remember the last user key so focus handlers can make
                # decisions about whether the layout change was user-driven
                # or programmatic. Cleared by the focus handler after use.
                self._last_user_key = key
            except Exception as e:
                self.printException(e)

            logger.debug(f"GitHistoryTool.on_key: key={key}")
            if key and key.lower() == "ctrl+p":
                event.stop()
                return

            if key in ("q", "Q"):
                # Ensure quitting works for uppercase Q as well.
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e)

                try:
                    self.action_quit()
                except Exception as e:
                    self.printException(e)
                    try:
                        self.exit()
                    except Exception as e:
                        self.printException(e)

                return

            # Help: show help column on h / H / ?
            if key in ("h", "H", "?", "question_mark"):
                logger.debug(f"Help key detected: {key}")
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e)

                try:
                    # Save current state and show the help fullscreen layout
                    try:
                        self.save_state()
                    except Exception as e:
                        self.printException(e, "save_state failed before showing help")
                    try:
                        self.change_state(
                            "help_fullscreen",
                            "#help-list",
                            Text("q(uit)  ↑/↓/PgUp/PgDn  Press any key to return", style="bold"),
                        )
                    except Exception as e:
                        self.printException(e, "change_state failed showing help")
                except Exception as e:
                    self.printException(e)

                return

            # Mode toggle: switch between file-first and log-first modes
            if key in ("s", "S"):
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e)
                try:
                    # Decide which side to toggle based on current focus
                    cur = getattr(self, "_current_focus", None) or "#left-file-list"
                    if isinstance(cur, str) and cur.startswith("#"):
                        cur_key = cur[1:]
                    else:
                        cur_key = str(cur)

                    # Toggle global flag
                    try:
                        self.log_first = not bool(self.log_first)
                    except Exception as e2:
                        self.printException(e2, "toggling log_first flag")
                        self.log_first = not getattr(self, "log_first", False)

                    # Left-side toggle
                    if cur_key == "left-file-list":
                        try:
                            self._toggle_left_file_to_history()
                        except Exception as e:
                            self.printException(e, "switching to left-history-list")

                    elif cur_key == "left-history-list":
                        try:
                            self._toggle_left_history_to_file()
                        except Exception as e:
                            self.printException(e, "switching to left-file-list")

                    # Right-side toggle
                    # going from right-history -> right-file
                    elif cur_key == "right-history-list":
                        try:
                            self._toggle_right_history_to_file()
                        except Exception as e:
                            self.printException(e, "switching right-history -> right-file")

                    elif cur_key == "right-file-list":
                        try:
                            self._toggle_right_file_to_history()
                        except Exception as e:
                            self.printException(e, "switching right-file -> right-history")

                    else:
                        # Default: toggle overall startup mode and set global layout
                        try:
                            if self.log_first:
                                self.repo_mode_history_list.prepRepoModeHistoryList()
                                self.change_state("history_fullscreen", f"#{self.repo_mode_history_list.id}", self.footer_history)
                            else:
                                self.file_mode_file_list.prepFileModeFileList(self.path)
                                self.change_state("file_fullscreen", f"#{self.file_mode_file_list.id}", self.footer_file)
                        except Exception as e:
                            self.printException(e, "toggling global mode")
                except Exception as e:
                    self.printException(e, "mode toggle failed")

                return

        except Exception as e:
            self.printException(e)

    def _toggle_left_file_to_history(self) -> None:
        """Switch from left-file-list to left-history-list (extracted helper)."""
        try:
            try:
                # ensure history is populated
                try:
                    self.repo_mode_history_list.prepRepoModeHistoryList()
                except Exception as e:
                    self.printException(e, "toggle: prepRepoModeHistoryList failed")

                self.change_state("history_fullscreen", f"#{self.repo_mode_history_list.id}", self.footer_history)

                try:
                    # ensure top item visible
                    self.repo_mode_history_list.index = 0
                except Exception as e:
                    self.printException(e, "toggle: setting repo_mode_history_list.index failed")
            except Exception as e:
                self.printException(e, "switching to left-history-list")
        except Exception as e:
            self.printException(e, "_toggle_left_file_to_history outer failure")

    def _toggle_left_history_to_file(self) -> None:
        """Switch from left-history-list to left-file-list (extracted helper)."""
        try:
            try:
                # Ensure the left file list is populated for the currently
                # displayed path so highlighting/indexing can be applied.
                try:
                    prep_path = getattr(self, "displayed_path", None) or getattr(self, "path", None) or "."
                    logger.debug("toggle(left): prepping left-file-list for path=%s", prep_path)
                    self.file_mode_file_list.prepFileModeFileList(prep_path)
                    logger.debug(
                        "toggle(left): prepFileModeFileList returned; _populated=%s",
                        getattr(self.file_mode_file_list, "_populated", None),
                    )
                except Exception as _e:
                    self.printException(_e, "toggle(left): prepping left-file-list failed")

                # Switch to the file fullscreen layout and focus the left file list
                self.change_state("file_fullscreen", f"#{self.file_mode_file_list.id}", self.footer_file)

                try:
                    fname = getattr(self, "current_diff_file", None)
                    if fname:
                        try:
                            # Keep app-level current_diff_file in sync
                            try:
                                self.current_diff_file = fname
                                try:
                                    self.app.current_diff_file = fname
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        except Exception:
                            pass

                        logger.debug("toggle(left): scheduling highlight for left-file-list %s", fname)
                        try:
                            # Use centralized helper which already handles call_after_refresh
                            self._highlight_filename_in_filelist(self.file_mode_file_list, fname)
                        except Exception as e3:
                            self.printException(e3, "toggle(left): highlight failed")
                    else:
                        # No current filename: ensure a selectable index and reset scroll
                        try:
                            self.file_mode_file_list.index = getattr(self.file_mode_file_list, "_min_index", 0) or 0
                        except Exception as e:
                            self.printException(e, "toggle(left): setting index failed")
                        try:
                            self.file_mode_file_list.scroll_y = 0
                        except Exception as e:
                            self.printException(e, "toggle(left): setting scroll_y failed")
                except Exception as e:
                    self.printException(e, "switching to left-file-list")
            except Exception as e:
                self.printException(e, "switching to left-file-list")
        except Exception as e:
            self.printException(e, "_toggle_left_history_to_file outer failure")

    def _toggle_right_history_to_file(self) -> None:
        """Switch from right-history-list to right-file-list."""
        try:
            try:
                curr_hash, prev_hash, *_ = self.file_mode_history_list.compute_commit_pair_hashes()
            except Exception as e:
                self.printException(e, "toggle: compute_commit_pair_hashes failed")
                curr_hash = getattr(self, "current_commit_sha", None)
                prev_hash = getattr(self, "current_prev_sha", None)

            try:
                logger.debug("toggle: prepping repo-wide history (curr=%s prev=%s)", curr_hash, prev_hash)
                self.repo_mode_history_list.prepRepoModeHistoryList()
                logger.debug("toggle: prepRepoModeHistoryList returned (len=%s)", len(getattr(self.repo_mode_history_list, "_nodes", []) or []))
            except Exception as _e:
                logger.debug("toggle: prepRepoModeHistoryList raised: %s", _e)
                self.printException(_e, "toggle: prepRepoModeHistoryList raised")

            try:
                logger.debug("toggle: choosing hashes in repo history curr=%s prev=%s", curr_hash, prev_hash)
                self._choose_hash_in_history(self.repo_mode_history_list, curr_hash, prev_hash)
                logger.debug("toggle: _choose_hash_in_history completed")
            except Exception as _e:
                logger.debug("toggle: _choose_hash_in_history raised: %s", _e)
                self.printException(_e, "toggle: _choose_hash_in_history raised")

            try:
                logger.debug("toggle: prepping repo file list prev=%s curr=%s", prev_hash, curr_hash)
                self.repo_mode_file_list.prepRepoModeFileList(prev_hash, curr_hash)
                logger.debug("toggle: prepRepoModeFileList invoked; _populated=%s", getattr(self.repo_mode_file_list, "_populated", None))
            except Exception as _e:
                logger.debug("toggle: prepRepoModeFileList raised: %s", _e)
                self.printException(_e, "toggle: prepRepoModeFileList raised")

            try:
                logger.debug("toggle: changing state to history_file focus=#%s", self.repo_mode_file_list.id)
                self.change_state("history_file", f"#{self.repo_mode_file_list.id}", self.footer_history)
                logger.debug("toggle: change_state completed, current_focus=%s", getattr(self, "_current_focus", None))
            except Exception as _e:
                logger.debug("toggle: change_state raised: %s", _e)
                self.printException(_e, "toggle: change_state raised")

            try:
                fname = getattr(self, "current_diff_file", None)
                if fname:
                    def _wait_and_highlight():
                        try:
                            logger.debug(
                                "toggle: _wait_and_highlight checking _populated=%s",
                                getattr(self.repo_mode_file_list, "_populated", None),
                            )
                            if getattr(self.repo_mode_file_list, "_populated", False):
                                try:
                                    logger.debug("toggle: scheduling call_after_refresh to highlight %s", fname)
                                    self.repo_mode_file_list.call_after_refresh(
                                        lambda: (
                                            logger.debug("toggle: inside call_after_refresh highlighting %s", fname),
                                            self.repo_mode_file_list._highlight_filename(fname),
                                        )
                                    )
                                except Exception as _e:
                                    logger.debug("toggle: call_after_refresh highlight failed: %s", _e)
                                    self.printException(_e, "toggle: call_after_refresh highlight failed")
                                    try:
                                        self.repo_mode_file_list._highlight_filename(fname)
                                        logger.debug("toggle: fallback immediate highlight succeeded for %s", fname)
                                    except Exception as _e2:
                                        logger.debug("toggle: fallback immediate highlight failed: %s", _e2)
                                        self.printException(_e2, "toggle: fallback immediate highlight failed")
                                return

                            try:
                                logger.debug("toggle: _populated False, re-scheduling _wait_and_highlight")
                                self.call_after_refresh(_wait_and_highlight)
                            except Exception as _e:
                                logger.debug("toggle: re-schedule failed, sleeping then retrying: %s", _e)
                                time.sleep(0.02)
                                _wait_and_highlight()
                        except Exception as _e:
                            logger.debug("toggle: _wait_and_highlight outer exception: %s", _e)
                            self.printException(_e, "toggle: _wait_and_highlight outer exception")

                    try:
                        self.call_after_refresh(_wait_and_highlight)
                    except Exception as _e:
                        self.printException(_e, "toggle: scheduling _wait_and_highlight failed, falling back to immediate highlight")
                        try:
                            self.repo_mode_file_list._highlight_filename(fname)
                        except Exception as _e2:
                            self.printException(_e2, "toggle: immediate highlight failed in fallback")
            except Exception as e:
                self.printException(e, "switching right-history -> right-file")
        except Exception as e:
            self.printException(e, "_toggle_right_history_to_file outer failure")

    def _toggle_right_file_to_history(self) -> None:
        """Switch from right-file-list to right-history-list."""
        try:
            try:
                fname = getattr(self, "current_diff_file", None)
                # If no app-level current file, try to derive it from the
                # currently-selected entry in the repo-mode file list.
                if not fname:
                    try:
                        idx = getattr(self.repo_mode_file_list, "index", None)
                        if idx is None or idx < getattr(self.repo_mode_file_list, "_min_index", 0) or idx >= len(getattr(self.repo_mode_file_list, "_nodes", []) or []):
                            idx = getattr(self.repo_mode_file_list, "_min_index", 0) or 0
                        nodes = getattr(self.repo_mode_file_list, "_nodes", []) or []
                        if nodes and 0 <= idx < len(nodes):
                            node = nodes[idx]
                            try:
                                fname = self.repo_mode_file_list._child_filename(node)
                            except Exception as _e:
                                self.printException(_e, "toggle: extracting filename from repo_mode_file_list node failed")
                    except Exception as _e:
                        self.printException(_e, "toggle: deriving filename from repo_mode_file_list failed")

                if fname:
                    try:
                        self.current_diff_file = fname
                        try:
                            self.app.current_diff_file = fname
                        except Exception:
                            pass
                    except Exception:
                        pass
                    logger.debug("toggle: prepping file-mode history for fname=%s", fname)
                    self.file_mode_history_list.prepFileModeHistoryList(fname)
                    logger.debug("toggle: prepFileModeHistoryList returned (len=%s)", len(getattr(self.file_mode_history_list, "_nodes", []) or []))
                else:
                    logger.debug("toggle: no filename available to prep file-mode history; file-mode history will be empty")
            except Exception as _e:
                self.printException(_e, "toggle: prepFileModeHistoryList raised")

            try:
                if hasattr(self, "file_mode_file_list"):
                    try:
                        prep_path = getattr(self, "displayed_path", None) or getattr(self, "path", None) or "."
                        logger.debug("toggle: prepping left file list for file_history using path=%s", prep_path)
                        self.file_mode_file_list.prepFileModeFileList(prep_path)
                    except Exception as _e:
                        self.printException(_e, "toggle: prepping left-file-list failed")
            except Exception as e:
                self.printException(e, "toggle: ensuring left-file-list prep failed")

            try:
                self.change_state("file_history", f"#{self.file_mode_history_list.id}", self.footer_history)
            except Exception as e2:
                self.printException(e2, "toggle: change_state to file_history failed")

            try:
                if getattr(self, "current_diff_file", None):
                    self.file_mode_file_list.call_after_refresh(lambda: self.file_mode_file_list._highlight_filename(getattr(self, "current_diff_file", None)))
            except Exception as e2:
                self.printException(e2, "toggle: highlighting current file in left-file-list failed")
        except Exception as e:
            self.printException(e, "switching right-file -> right-history")



def main() -> None:
    """Entry point: parse CLI args and run the Textual app."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="Directory/file to list", default=os.getcwd())
    parser.add_argument(
        "-C", "--no-color", dest="no_color", action="store_true", help="Start with diff colorization turned off"
    )
    parser.add_argument(
        "-l",
        "--log-first",
        dest="log_first",
        action="store_true",
        help="Start with repository commit log (history) shown first",
    )
    args = parser.parse_args()

    # Ensure logging is cleanly shut down on normal exit or when receiving
    # termination signals (SIGTERM / SIGINT). Register atexit as a fallback
    # and signal handlers to call logging.shutdown() before exiting.
    try:
        import signal
        import atexit

        def _term_handler(signum, frame):
            logger.info(f"Received signal {signum}; shutting down")

            try:
                atexit.register(logging.shutdown)
            except Exception as e:
                logger.warning(f"atexit.register failed: {e}")
            try:
                logging.shutdown()
            except Exception:
                pass
            sys.exit(0)

        # Register both TERM and INT so Ctrl-C and `kill` behave the same.
        signal.signal(signal.SIGTERM, _term_handler)
        signal.signal(signal.SIGINT, _term_handler)

    except Exception as e:
        # If signals/atexit can't be configured, continue without them.
        logger.warning(f"Signal failed: {e}")
        pass

    try:
        import atexit as _atexit

        _atexit.register(logging.shutdown)
    except Exception as e:
        logger.warning(f"could not register logging.shutdown: {e}")

    logger.debug(f"invoking GitHistoryTool(path={args.path}, color={not args.no_color}, log_first={args.log_first})")
    app = GitHistoryTool(args.path, colorize_diff=(not args.no_color), log_first=args.log_first)
    logger.debug("Calling app.run()")
    app.run()


if __name__ == "__main__":
    main()
