#!/usr/bin/env python3
"""
Git Diff Navigator Tool TUI
"""
from __future__ import annotations

import argparse
import datetime
import logging
import os
import re
import subprocess
import sys
import traceback
from typing import Optional
from rich.text import Text
from rich.align import Align

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

# Set up logging to help debug key event issues (currently disabled)
# Uncomment the basicConfig line below to enable logging to tmp/gitdiff_debug.log
DOLOGGING = True
if DOLOGGING:
    logging.basicConfig(
        filename="tmp/gitdiff_debug.log",
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
logger = logging.getLogger(__name__)


class AppBase(ListView):
    """A base class for all of our other application Base classes.
    It provides common functionality that everyone needs.
    """

    def printException(self, e, msg=None):
        """Print a message, the error information and a stacktrace"""
        className = type(self).__name__
        funcName = sys._getframe(1).f_code.co_name
        msg = msg if msg else "???"
        logger.warning(f"WARNING: {className}.{funcName}: {msg}")
        logger.warning(traceback.format_exc())


class FileListBase(AppBase):
    """A ListView showing directory contents. Directories have a blue background.

    Navigation: arrow keys (up/down) move selection automatically because ListView
    handles keyboard navigation. The app focuses this widget on mount.
    """

    def set_path(self, path: str) -> None:
        """Set the directory to display and populate the list view.

        This updates `self.path`, clears the current ListView contents, and
        appends directory and file entries. Files are annotated with
        `_filename` and `_repo_status` metadata when available.
        """
        path = os.path.abspath(path)
        self.path = path
        # keep the app aware of the full path currently displayed
        try:
            if hasattr(self, "app"):
                self.app.displayed_path = self.path
        except Exception as e:
            self.printException(e, "exception setting displayed_path")
            pass

        # Refresh repository cache when changing path so status markers
        # (e.g. untracked WT_NEW -> 'U') stay up-to-date even if files
        # were created/removed since the app mounted.
        try:
            app = getattr(self, "app", None)
            if app:
                try:
                    app.build_repo_cache()
                except Exception as e:
                    self.printException(e, "exception refreshing repo cache")
                    pass
        except Exception as e:
            self.printException(e)
            pass

        try:
            entries = sorted(os.listdir(path))
        except Exception as e:
            self.printException(e, f"Error reading {path}")
            self.clear()
            self.append(ListItem(Label(Text(f"Error reading {path}: {exc}", style="red"))))
            return

        self.clear()
        # Insert a short key/legend explaining the repo-status markers shown
        try:
            key_text = Text("Key:  ' ' tracked  U untracked  M modified  A staged  D deleted  I ignored  ! conflicted", style="bold")
            self.append(ListItem(Label(key_text)))
            try:
                # Prevent cursor from moving into the legend row
                self._min_index = 1
            except Exception as e:
                self.printException(e)
                pass
        except Exception as e:
            logger.debug(f"FileList.set_path: exception adding key legend: {e}")
            logger.debug(traceback.format_exc())
            pass
        # Parent entry — omit when this directory contains a .git subdirectory
        if not os.path.isdir(os.path.join(path, ".git")):
            parent_item = ListItem(Label(Text("..", style="white on blue")))
            parent_item._filename = ".."
            self.append(parent_item)

        for name in entries:
            full = os.path.join(path, name)
            # Do not display the .git subdirectory
            if name == ".git":
                continue
            if os.path.isdir(full):
                li = ListItem(Label(Text(name, style="white on blue")))
            else:
                # determine repo status (tracked/modified/untracked/conflicted/etc.) if available
                style = None
                repo_status = None
                try:
                    app = getattr(self, "app", None)
                    if app and getattr(app, "repo_available", False) and app.repo_root:
                        # path relative to repo root
                        try:
                            rel = os.path.relpath(full, app.repo_root)
                        except Exception as e:
                            self.printException(e, f"exception getting relpath")
                            rel = None
                        if rel and not rel.startswith(".."):
                            flags = app.repo_status_map.get(rel, 0)
                            # Map pygit2 status flags to styles
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
                                # ignored
                                elif flags & getattr(pygit2, "GIT_STATUS_IGNORED", 0):
                                    style = "dim italic"
                                    repo_status = "ignored"
                                # modified in worktree or index-modified
                                elif flags & (
                                    getattr(pygit2, "GIT_STATUS_WT_MODIFIED", 0)
                                    | getattr(pygit2, "GIT_STATUS_INDEX_MODIFIED", 0)
                                ):
                                    style = "yellow"
                                    repo_status = "modified"
                                # untracked / new in worktree
                                elif flags & getattr(pygit2, "GIT_STATUS_WT_NEW", 0):
                                    style = "bold yellow"
                                    repo_status = "untracked"
                                else:
                                    # tracked and clean — display in bright white
                                    style = "white"
                                    repo_status = "tracked_clean"
                            except Exception as e:
                                self.printException(e, "exception processing pygit2 flags")
                                style = None
                                repo_status = None
                        else:
                            # outside repo tree -> untracked/not-in-repo
                            style = "bold yellow"
                            repo_status = "untracked"
                    else:
                        # no repo available -> treat as untracked
                        style = "bold yellow"
                        repo_status = "untracked"
                except Exception as e:
                    self.printException(e, f"exception getting repo status")
                    style = None
                    repo_status = None

                # Prefer a short marker to make status visually obvious
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
                try:
                    li._repo_status = repo_status
                except Exception as e:
                    self.printException(e, f"exception setting _repo_status")
                    pass
            # attach filename to the ListItem for reliable lookup later
            try:
                li._filename = name
            except Exception as e:
                self.printException(e, f"exception setting _filename")
                pass
            self.append(li)

        # After populating, ensure the top entry is highlighted.
        try:
            self.call_after_refresh(self._highlight_top)
        except Exception as e:
            self.printException(e, f"exception calling _highlight_top")
            try:
                # If setting via _highlight_top fails, fall back to the
                # configured minimum selectable index so the legend row
                # isn't selected on initial focus.
                self.index = getattr(self, "_min_index", 0) or 0
            except Exception as e:
                self.printException(e, f"exception setting index to 0")
                pass

    def on_focus(self, event: events.Focus) -> None:
        """When Files column receives focus, make it full-width and hide others."""
        try:
            # Adjust columns depending on which FileList got focus. If the
            # left files column is focused, make it full-width and hide the
            # right columns. If the right1 files column is focused (common
            # when starting in log-first mode), show left/history at 25%
            # and right1/files at 75% so the files view remains visible.
            fid = getattr(self, "id", "left")
            if fid == "left":
                try:
                    self.app.query_one("#left-column").styles.width = "100%"
                    self.app.query_one("#left-column").styles.flex = 0
                except Exception as e:
                    self.printException(e, f"exception setting left column")
                    pass
                try:
                    self.app.query_one("#right1-column").styles.width = "0%"
                    self.app.query_one("#right1-column").styles.flex = 0
                except Exception as e:
                    self.printException(e, f"exception setting right1 column")
                    pass
                try:
                    self.app.query_one("#right2-column").styles.width = "0%"
                    self.app.query_one("#right2-column").styles.flex = 0
                except Exception as e:
                    self.printException(e, f"exception setting right2 column")
                    pass
                # Ensure inner left list fills its column
                self.styles.width = "100%"
                self.styles.flex = 0
            else:
                try:
                    self.app.query_one("#left-column").styles.width = "25%"
                    self.app.query_one("#left-column").styles.flex = 0
                except Exception as e:
                    self.printException(e, f"exception setting left column for right1 focus")
                    pass
                try:
                    self.app.query_one("#right1-column").styles.width = "75%"
                    self.app.query_one("#right1-column").styles.flex = 0
                except Exception as e:
                    self.printException(e, f"exception setting right1 column for right1 focus")
                    pass
                # Ensure inner right1 list fills its column
                self.styles.width = "100%"
                self.styles.flex = 0
        except Exception as e:
            self.printException(e, f"exception setting column widths")
            pass
        try:
            # Accept any widget at `#right1` (HistoryListBase or FileList).
            # If this FileList instance *is* the widget at `#right1`, do
            # not hide it — avoid toggling off the very list that just
            # received focus (this was hiding appended items).
            right1 = self.app.query_one("#right1")
            if right1 is not self:
                right1.styles.display = "none"
        except Exception as e:
            self.printException(e, f"exception hiding right1")
            pass
        try:
            right2 = self.app.query_one("#right2", ListView)
            right2.styles.display = "none"
        except Exception as e:
            self.printException(e, f"exception hiding right2")
            pass

        # Ensure selection is at or below the minimum selectable index
        # (e.g. skip the Key legend row). Use call_after_refresh so the
        # DOM is stable before mutating the selection.
        try:
            min_idx = getattr(self, "_min_index", 0) or 0
            cur = getattr(self, "index", None)
            if cur is None or cur < min_idx:
                try:
                    # Prefer to set after refresh to avoid race with mount
                    self.call_after_refresh(lambda: setattr(self, "index", min_idx))
                except Exception:
                    try:
                        self.index = min_idx
                    except Exception as e:
                        self.printException(e, "exception enforcing min index on focus")
                        pass
        except Exception as e:
            self.printException(e, "exception checking/enforcing _min_index on focus")
            pass

        # show/hide titles for columns: left visible, others hidden
        try:
            # Determine which column holds Files vs History depending on
            # whether the app was started in log-first mode and which
            # widget currently has focus. If this FileList is the focused
            # files widget, show its column title as 'Files' and hide the
            # other history title.
            files_id = getattr(self, "id", "left")
            if files_id == "left":
                left_text = "Files"
                right1_text = "History"
            else:
                left_text = "History"
                right1_text = "Files"

            lbl = self.app.query_one("#left-title", Label)
            lbl.update(Text(left_text, style="bold"))
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e, f"exception updating left title")
            pass
        try:
            lbl = self.app.query_one("#right1-title", Label)
            lbl.update(Text(right1_text, style="bold"))
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e, f"exception updating right1 title")
            pass
        try:
            lbl = self.app.query_one("#right2-title", Label)
            lbl.styles.display = "none"
            lbl.styles.height = 0
            lbl.styles.width = 0
        except Exception as e:
            self.printException(e, f"exception hiding right2 title")
            pass

        # FileList footer
        try:
            footer = self.app.query_one("#footer", Label)
            footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓ →", style="bold"))
        except Exception as e:
            self.printException(e, f"exception updating footer")
            pass

    def on_key(self, event: events.Key) -> None:  # FileListBase
        """Only allow up/down/left/right in this column.

        - Up: move to previous entry
        - Down: move to next entry
        - Left/Right: show a temporary "TBD" modal
        - Other keys: ignore
        """
        key = event.key
        logger.debug(f"FileList.on_key: key={key}")
        if key and key.lower() == "q":
            # Allow global quit (q/Q) to bubble to the app. Ensure the
            # event.key is normalized to lowercase so the app-level handler
            # which checks for 'q' will receive a lower-case key.
            event.key = key.lower()
            return
        if key == "up":
            event.stop()
            try:
                min_idx = getattr(self, "_min_index", 0) or 0
            except Exception as e:
                self.printException(e)
                min_idx = 0
            cur = getattr(self, "index", None)
            # If index is unset, initialize to min_idx
            if cur is None:
                try:
                    self.index = min_idx
                except Exception as e:
                    self.printException(e)
                    pass
                return
            # If already at or above minimum, do not move up past it
            try:
                if cur <= min_idx:
                    return
            except Exception as e:
                self.printException(e)
                pass
            self.action_cursor_up()
        elif key == "down":
            event.stop()
            self.action_cursor_down()
        elif key == "right":
            event.stop()
            try:
                if self.key_right():
                    return
            except Exception as e:
                self.printException(e, f"key_right exception")
            return
        elif key == "left":
            event.stop()
            try:
                if self.key_left():
                    return
            except Exception as e:
                self.printException(e, f"key_left exception")
            return
        else:
            # For keys we don't explicitly handle here, allow them to bubble
            # to higher-level handlers (e.g. app-level `on_key`) so global
            # shortcuts like `h` / `?` and `Q` are still processed.
            return

    def _highlight_filename(self, name: str) -> None:  # FileListBase
        """Highlight the ListItem whose attached `_filename` equals `name`.

        This is intended to be called via `call_after_refresh` after the
        DOM has been updated by `set_path`.
        """
        try:
            nodes = getattr(self, "_nodes", [])
            for idx, node in enumerate(nodes):
                if getattr(node, "_filename", None) == name:
                    try:
                        self.index = idx
                    except Exception as e:
                        self.printException(e, f"exception setting index")
                        pass
                    return
            # not found: default to minimum selectable index (skip legend)
            try:
                self.index = getattr(self, "_min_index", 0) or 0
            except Exception as e:
                self.printException(e, f"exception setting index to 0")
                pass
        except Exception as e:
            self.printException(e, f"exception in outer block")
            return

    def key_left(self) -> bool:  # FileListBase
        """Handle left key behavior for FileListBase.

        Returns True when the key was handled/consumed.
        """
        return False

    def _highlight_top(self) -> None:  # FileListBase
        """Highlight the first entry in the list after a refresh."""
        try:
            # If there are nodes, set index to 0; otherwise leave unset.
            nodes = getattr(self, "_nodes", [])
            if nodes:
                # Respect a minimum selectable index (e.g. skip Key legend)
                min_idx = getattr(self, "_min_index", 0) or 0
                try:
                    self.index = min_idx if min_idx < len(nodes) else 0
                except Exception as e:
                    self.printException(e)
                    self.index = 0
        except Exception as e:
            self.printException(e, f"exception")
            return


class FileModeFileList(FileListBase):
    """Compatibility subclass; use `FileListBase` for shared logic."""

    def key_left(self) -> bool:  # FileModeFileList
        """Handle left key behavior for FileModeFileList

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
                    self.printException(e, f"exception showing left key error modal")
                    try:
                        self.app.push_screen(_TBDModal())
                    except Exception as e:
                        self.printException(e)
                        pass
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
                    pass
                return True

            # change to parent directory
            self.set_path(parent)

            # After the DOM refresh, highlight the directory we came from.
            try:
                self.call_after_refresh(self._highlight_filename, prev_basename)
            except Exception as e:
                self.printException(e, "exception calling _highlight_filename")
                try:
                    # Fallback: set to minimum selectable index (skip legend)
                    self.index = getattr(self, "_min_index", 0) or 0
                except Exception as e:
                    self.printException(e, "exception setting index fallback")
                    pass

            # update app-level path info
            try:
                self.app.path = os.path.abspath(parent)
                self.app.displayed_path = self.path
            except Exception as e:
                self.printException(e, "exception updating app path info")
                pass

            try:
                self.focus()
            except Exception as e:
                self.printException(e)
                pass
            return True

        # Left on non-parent: ignore (do nothing)
        return True

    def key_right(self) -> bool:  # FileModeFileList
        """Handle right key behavior for FileModeFileList

        Returns True when the key was handled/consumed.
        """
        # If the highlighted entry is a directory (and not ".."), enter it.
        child = self.highlighted_child
        if child is None:
            return True
        # Prefer filename attached to the ListItem (set in set_path)
        item_name = getattr(child, "_filename", None)
        if item_name is None:
            try:
                label = child.query_one(Label)
                # Label implementations vary: prefer `text`, then `renderable`.
                if hasattr(label, "text"):
                    item_name = label.text
                else:
                    renderable = getattr(label, "renderable", None)
                    if isinstance(renderable, Text):
                        item_name = renderable.plain
                    elif renderable is not None:
                        item_name = str(renderable)
                    else:
                        item_name = str(label)
            except Exception as exc:
                # Fallback: show the exception message in the modal
                try:
                    self.app.push_screen(_TBDModal(str(exc)))
                except Exception as e:
                    self.printException(e, "exception showing modal fallback")
                    try:
                        # Last-resort fallback
                        self.app.push_screen(_TBDModal())
                    except Exception as e:
                        self.printException(e)
                        pass
                return True

        if item_name != "..":
            full = os.path.join(self.path, item_name)
            if os.path.isdir(full):
                # switch the listing to the selected directory
                self.set_path(full)
                # ensure highlight resets to the minimum selectable item
                try:
                    self.index = getattr(self, "_min_index", 0) or 0
                except Exception as e:
                    self.printException(e, "exception resetting index")
                    pass
                # update app-level current path as well
                try:
                    self.app.path = os.path.abspath(full)
                except Exception as e:
                    self.printException(e, "exception updating app.path")
                    pass
                # focus back on this list
                try:
                    self.focus()
                except Exception as e:
                    self.printException(e)
                    pass
                return True

            # If it's a file, run `git log` and show output in History column
            try:
                # Run git log in the current directory for the filename
                proc = subprocess.run(
                    [
                        "git",
                        "log",
                        "--follow",
                        "--date=short",
                        "--pretty=format:%ad %h %s",
                        "--",
                        item_name,
                    ],
                    cwd=self.path,
                    capture_output=True,
                    text=True,
                )
                out = proc.stdout.strip()
                # update the History column (right1)
                try:
                    hist = self.app.query_one("#right1", ListView)
                    # populate the history ListView with lines from git output
                    try:
                        # clear existing items
                        hist.clear()
                    except Exception as e:
                        self.printException(e, "exception clearing history")
                        pass

                    # remember which file this history is for
                    try:
                        hist._filename = item_name
                    except Exception as e:
                        self.printException(e, "exception setting hist._filename")
                        pass

                    if out:
                        # Before appending real commits, optionally insert
                        # pseudo-log lines for staged/modified working tree.
                        try:
                            app = getattr(self, "app", None)
                            pseudo_entries: list[str] = []
                            if app and getattr(app, "repo_available", False) and app.repo_root:
                                try:
                                    rel = os.path.relpath(os.path.join(self.app.path, item_name), app.repo_root)
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
                                    # If both staged and further modifications exist,
                                    # show MODS (working) first, then STAGED.
                                    if has_wt and has_index:
                                        pseudo_entries = ["MODS", "STAGED"]
                                    elif has_index:
                                        pseudo_entries = ["STAGED"]
                                    elif has_wt:
                                        pseudo_entries = ["MODS"]
                            # If no repo available but file looks modified in FS,
                            # fall back to showing MODS when `git diff` would
                            # have non-empty output vs HEAD. We keep simple
                            # behavior and only use repo flags when available.
                        except Exception as e:
                            self.printException(e, "exception building pseudo entries")
                            pseudo_entries = []

                        for pseudo in pseudo_entries:
                            # Attach a best-effort timestamp for STAGED entries.
                            display_pseudo = pseudo
                            if pseudo == "STAGED":
                                try:
                                    app = getattr(self, "app", None)
                                    display_pseudo = "STAGED"
                                    if app and getattr(app, "repo_root", None):
                                        try:
                                            rel = os.path.relpath(os.path.join(self.path, item_name), app.repo_root)
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
                                        # fallback to index file mtime if per-file not available
                                        if not mtime:
                                            try:
                                                index_path = os.path.join(app.repo_root, ".git", "index")
                                                mtime = os.path.getmtime(index_path)
                                            except Exception as e:
                                                self.printException(e, "exception getting index file mtime")
                                                mtime = None
                                        if mtime:
                                            display_pseudo = (
                                                f"{datetime.datetime.fromtimestamp(float(mtime)).strftime('%Y-%m-%d')} "
                                                "STAGED"
                                            )
                                except Exception as e:
                                    self.printException(e, "exception building STAGED display")
                                    display_pseudo = "STAGED"
                            elif pseudo == "MODS":
                                try:
                                    # use working-tree file mtime for MODS
                                    try:
                                        fp = os.path.join(self.path, item_name)
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
                            except Exception as e:
                                self.printException(e, "exception setting pli._hash")
                                pass
                            try:
                                pli._raw_text = display_pseudo
                            except Exception as e:
                                self.printException(e, "exception setting pli._raw_text")
                                pass
                            hist.append(pli)

                        for line in out.splitlines():
                            li = ListItem(Label(Text(" " + line)))
                            try:
                                m = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", line)
                                if m:
                                    li._hash = m.group(2)
                            except Exception as e:
                                self.printException(e, "exception parsing hash from line")
                                pass
                            try:
                                li._raw_text = line
                            except Exception as e:
                                self.printException(e, "exception setting li._raw_text")
                                pass
                            hist.append(li)
                    else:
                        hist.append(ListItem(Label(Text(" " + f"No git history for {item_name}"))))

                    # highlight and focus the top entry
                    try:
                        hist.index = 0
                    except Exception as e:
                        self.printException(e, "exception setting hist.index")
                        pass
                    try:
                        hist.focus()
                    except Exception as e:
                        self.printException(e, "exception focusing history")
                        pass
                except Exception as e:
                    self.printException(e, "exception updating history view")
                    # If unable to update, show modal with output or message
                    msg = out or f"No git history for {item_name}"
                    try:
                        self.app.push_screen(_TBDModal(msg))
                    except Exception as e:
                        self.printException(e, "exception showing history error modal")
                        pass
            except Exception as exc:
                try:
                    self.app.push_screen(_TBDModal(str(exc)))
                except Exception as e:
                    self.printException(e, "exception showing outer error modal")
                    pass
            return True

        # Not a directory we can enter — show TBD for now
        try:
            self.app.push_screen(_TBDModal())
        except Exception as e:
            self.printException(e)
            pass
        return True


class RepoModeFileList(FileListBase):
    """Placeholder File list for repo-first / log-first mode.

    Currently a no-op subclass; behavior will be added in follow-up edits.
    """
    def key_left(self) -> bool:  # RepoModeFileList
        """When Left is pressed in the repo-mode Files column, close
        the Files column and restore the History column to full-width.

        Returns True to indicate the key was handled.
        """
        try:
            # Hide the right1 (Files) column and restore left (History)
            try:
                # If the files widget itself is at #right1, hide it
                right1 = self.app.query_one("#right1")
                try:
                    right1.styles.display = "none"
                except Exception as e:
                    self.printException(e, "exception hiding right1 widget")
            except Exception as e:
                self.printException(e, "exception querying #right1")
                pass

            try:
                self.app.query_one("#left-column").styles.width = "100%"
                self.app.query_one("#left-column").styles.flex = 0
            except Exception as e:
                self.printException(e, "exception restoring left-column width")
                pass
            try:
                self.app.query_one("#right1-column").styles.width = "0%"
                self.app.query_one("#right1-column").styles.flex = 0
            except Exception as e:
                self.printException(e, "exception shrinking right1-column")
                pass

            # Update titles so left shows 'History' and right1 hidden
            try:
                lbl = self.app.query_one("#left-title", Label)
                lbl.update(Text("History", style="bold"))
            except Exception as e:
                self.printException(e, "exception updating left-title")
                pass
            try:
                lbl = self.app.query_one("#right1-title", Label)
                lbl.styles.display = "none"
            except Exception as e:
                self.printException(e, "exception hiding right1-title")
                pass

            # Focus the History column (left)
            try:
                left = self.app.query_one("#left")
                left.focus()
            except Exception as e:
                self.printException(e, "exception focusing left history")
                pass
        except Exception as e:
            self.printException(e)
        return True

    def key_right(self) -> bool:  # RepoModeFileList
        """When Right is pressed on a file in repo-mode, show its diff between
        the two commits represented by the file list (or per-item hashes).

        Returns True when handled.
        """
        child = self.highlighted_child
        if child is None:
            return True

        # Prefer attached filename
        filename = getattr(child, "_filename", None)
        if filename is None:
            try:
                lbl = child.query_one(Label)
                if hasattr(lbl, "text"):
                    filename = lbl.text
                else:
                    renderable = getattr(lbl, "renderable", None)
                    if isinstance(renderable, Text):
                        filename = renderable.plain
                    elif renderable is not None:
                        filename = str(renderable)
                    else:
                        filename = str(lbl)
            except Exception as e:
                try:
                    self.app.push_screen(_TBDModal(str(e)))
                except Exception:
                    pass
                return True

        # Determine commit hashes: prefer per-item hashes then file_list attrs then app-wide
        try:
            prev = getattr(child, "_hash_prev", None) or getattr(self, "current_prev_sha", None) or getattr(self.app, "current_prev_sha", None)
            curr = getattr(child, "_hash_curr", None) or getattr(self, "current_commit_sha", None) or getattr(self.app, "current_commit_sha", None)
        except Exception as e:
            self.printException(e, "exception getting commit hashes")
            prev = None
            curr = None

        if not filename:
            try:
                self.app.push_screen(_TBDModal("Unknown filename for diff"))
            except Exception:
                pass
            return True

        # Store current diff info on the app for re-rendering/variant toggles
        try:
            self.app.current_commit_sha = curr
            self.app.current_prev_sha = prev
            self.app.current_diff_file = filename
        except Exception as e:
            self.printException(e, "exception setting app diff info")
            pass

        try:
            cmd = self.app.build_diff_cmd(prev, curr, filename)
            proc = subprocess.run(cmd, cwd=self.app.path, capture_output=True, text=True)
            diff_out = proc.stdout or proc.stderr or ""
        except Exception as exc:
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception:
                pass
            return True

        # show the Diff column and populate it
        try:
            diff_view = self.app.query_one("#right2", ListView)
            try:
                diff_view.clear()
            except Exception as e:
                self.printException(e, "clearing diff view")
                pass

            try:
                header = ListItem(Label(Text(f"Comparing: {prev}..{curr}", style="bold")))
                diff_view.append(header)
            except Exception as e:
                self.printException(e, "appending diff header")
                pass

            if diff_out:
                for line in diff_out.splitlines():
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
                    diff_view.append(ListItem(Label(styled_text)))
            else:
                diff_view.append(ListItem(Label(Text(f"No diff between {prev}..{curr}"))))

            try:
                v = getattr(self.app, "diff_variants", [None])[getattr(self.app, "diff_cmd_index", 0)]
                title_lbl = self.app.query_one("#right2-title", Label)
                title_lbl.update(Text("Diff" if not v else f"Diff {v}", style="bold"))
            except Exception as e:
                self.printException(e, "updating right2 title")
                pass
            try:
                diff_view.styles.display = None
            except Exception as e:
                self.printException(e, "making diff column visible")
                pass

            try:
                diff_view.index = 0
            except Exception as e:
                self.printException(e, "setting diff view index")
                pass
            try:
                diff_view.focus()
            except Exception as e:
                self.printException(e, "focusing diff view")
                pass
        except Exception as exc:
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception:
                pass
        return True

class HistoryListBase(AppBase):
    """ListView used for the History column."""

    def toggle_check_current(self) -> None:  # HistoryListBase
        """Toggle a single checkmark on the currently selected history item.

        Only one history ListItem may be checked at a time. The checkmark is
        shown as a leading "✓ " replacing the leading space. If the current
        item is already checked it will be unchecked. Any other checked item
        will be cleared.
        """
        try:
            nodes = getattr(self, "_nodes", [])
            if not nodes:
                return
            idx = getattr(self, "index", 0) or 0
            if idx < 0 or idx >= len(nodes):
                return

            # helper to get label and style
            def _label_text_and_style(node):
                try:
                    lbl = node.query_one(Label)
                    style = None
                    try:
                        style = getattr(lbl.renderable, "style", None)
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
                    pass
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
                    pass

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
                pass
        except Exception as e:
            self.printException(e, "exception in outer block")
            pass

    def on_focus(self, event: events.Focus) -> None:  # HistoryListBase
        """When the HistoryListBase receives focus, ensure the first item is highlighted."""
        try:
            # Force a re-apply of the highlight after focus; sometimes the
            # ListView won't re-highlight if the index hasn't changed.
            def _apply() -> None:
                try:
                    nodes = getattr(self, "_nodes", [])
                    if not nodes:
                        return
                    target = self.index if self.index is not None else 0
                    # clear then restore to force watch_index
                    try:
                        self.index = None
                    except Exception as e:
                        self.printException(e, "clearing index")
                        pass
                    try:
                        self.index = target
                    except Exception as e:
                        self.printException(e, "restoring index target")
                        pass
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
            pass

        # When History receives focus, make Files/History split 50/50 and hide Diff
        try:
            # In log-first mode the Files widget may be at #right1 instead of #left
            try:
                left = self.app.query_one("#left")
                if not isinstance(left, FileModeFileList):
                    try:
                        left = self.app.query_one("#right1")
                    except Exception as e:
                        self.printException(e)
                        left = None
            except Exception as e:
                self.printException(e)
                try:
                    left = self.app.query_one("#right1")
                except Exception as e:
                    self.printException(e)
                    left = None
            right2 = self.app.query_one("#right2", ListView)
            # set widths to 50/50
            try:
                # Adjust outer columns so left/right1 split the screen 25/75
                try:
                    self.app.query_one("#left-column").styles.width = "25%"
                    self.app.query_one("#left-column").styles.flex = 0
                except Exception as e:
                    self.printException(e, "setting left-column width")
                    pass
                try:
                    self.app.query_one("#right1-column").styles.width = "75%"
                    self.app.query_one("#right1-column").styles.flex = 0
                except Exception as e:
                    self.printException(e, "setting right1-column width")
                    pass
                # inner lists should fill their outer column
                left.styles.width = "100%"
                left.styles.flex = 0
            except Exception as e:
                self.printException(e, "setting left list styles")
                pass
            try:
                self.styles.width = "100%"
                self.styles.display = None
                self.styles.flex = 0
            except Exception as e:
                self.printException(e, "setting history styles")
                pass
            # hide diff list and shrink its outer column to zero so the
            # title doesn't consume space
            try:
                right2.styles.display = "none"
            except Exception as e:
                self.printException(e, "hiding diff list")
                pass
            try:
                self.app.query_one("#right2-column").styles.width = "0%"
                self.app.query_one("#right2-column").styles.flex = 0
            except Exception as e:
                self.printException(e, "setting right2-column width")
                pass
        except Exception as e:
            self.printException(e, "layout adjustment")
            pass
        # Titles: show left and history, hide diff
        try:
            # When History is focused, show the History title in the
            # focused column and label the other column as Files. The
            # focused history widget may be at `#left` (default layout)
            # or `#right1` when `log_first` is active; derive titles from
            # the widget id to ensure correctness.
            hist_id = getattr(self, "id", "left")
            if hist_id == "left":
                left_text = "History"
                right1_text = "Files"
            else:
                left_text = "Files"
                right1_text = "History"

            lbl = self.app.query_one("#left-title", Label)
            lbl.update(Text(left_text, style="bold"))
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e, "updating left-title label")
            pass
        try:
            lbl = self.app.query_one("#right1-title", Label)
            lbl.update(Text(right1_text, style="bold"))
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e, "updating right1-title label")
            pass
        try:
            lbl = self.app.query_one("#right2-title", Label)
            lbl.styles.display = "none"
            lbl.styles.height = 0
            lbl.styles.width = 0
        except Exception as e:
            self.printException(e, "updating right2 title")
            pass

        # istBaseBase footer
        try:
            footer = self.app.query_one("#footer", Label)
            footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓ →   m(ark)", style="bold"))
        except Exception as e:
            self.printException(e, "updating footer")
            pass

    def on_key(self, event: events.Key) -> None:  # HistoryListBase
        """Handle left/right keys to move between columns or show diffs.

        Left moves focus back to the Files column. Right computes hashes for
        the selected history entry pair and populates the Diff column.
        """
        key = event.key
        logger.debug(f"HistoryListBaseBase.on_key: key={key}")
        if key and key.lower() == "q":
            event.key = key.lower()
            return
        # Mark/unmark the file referenced by this history view
        if key and key.lower() == "m":
            event.stop()
            # Toggle checkmark on the current history item (in this column)
            try:
                self.toggle_check_current()
            except Exception as e:
                self.printException(e, "toggle check")
                pass
            return
        if key == "left":
            event.stop()
            try:
                if self.key_left():
                    return
            except Exception as e:
                self.printException(e, "key_left exception")
            return
        if key == "right":
            event.stop()
            try:
                if self.key_right():
                    return
            except Exception as e:
                self.printException(e, "key_right exception")
            return

        # Other keys: let default handling run by not stopping the event.
        return

    def key_left(self) -> bool:  # HistoryListBase
        """Handle left key behavior for HistoryListBase.

        Returns True when the key was handled/consumed.
        """
        return False

    def key_right(self) -> bool:  # HistoryListBase
        """Handle right key behavior for HistoryListBase.

        Returns True when the key was handled/consumed.
        """
        return False


class FileModeHistoryList(HistoryListBase):
    """subclass for FileMode HistoryList functionality; see `HistoryListBase` for shared logic."""

    def populate(self, repo_path: Optional[str] = None) -> None:  # FileModeHistoryList
        """Populate this History list with the commit history for a single file.

        This method populates the `ListView` with commits for a single file by
        running `git log --follow` (via `subprocess`) in `self.app.path`. The
        widget prefers an attached `_filename` attribute; if not present it will
        use the optional `repo_path` argument as the filename. Each appended
        `ListItem` will have `_raw_text` set to the visible log line and, when
        possible, `_hash` set to the commit short-hash parsed from the line.

        After populating the list the method makes the widget visible, sets
        the selection to the first item, and focuses the widget so the user can
        immediately navigate the history.
        """
        try:
            # File-mode history should show commits for a specific file.
            # Determine filename: prefer attached `_filename` on the widget,
            # otherwise accept the `repo_path` parameter as the filename.
            filename = getattr(self, "_filename", None) or repo_path
            if not filename:
                # Nothing to populate
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
                cwd=getattr(self.app, "path", None),
                capture_output=True,
                text=True,
            )
            out = proc.stdout.strip()

            try:
                self.clear()
            except Exception as e:
                self.printException(e)
                pass

            try:
                self._filename = filename
            except Exception as e:
                self.printException(e)
                pass

            if out:
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
                    pass

            try:
                self.styles.display = None
            except Exception as e:
                self.printException(e)
                pass
            try:
                self.index = 0
            except Exception as e:
                self.printException(e)
                pass
            try:
                self.focus()
            except Exception as e:
                self.printException(e)
                pass
        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)
                pass

    def key_left(self) -> bool:  # FileModeHistoryList
        """Handle left key behavior for FileModeHistoryList.

        Returns True when the key was handled/consumed.
        """
        try:
            # Find the Files widget whether it's at #left or #right1 (log-first layout)
            try:
                files = self.app.query_one("#left")
                if not isinstance(files, FileModeFileList):
                    try:
                        files = self.app.query_one("#right1")
                    except Exception as e:
                        self.printException(e)
                        files = None
            except Exception as e:
                self.printException(e)
                try:
                    files = self.app.query_one("#right1")
                except Exception as e:
                    self.printException(e)
                    files = None
            files.focus()
        except Exception as e:
            self.printException(e, "focusing files on left")
            return True
        return True

    def key_right(self) -> bool:  # FileModeHistoryList
        """Handle right key behavior for FileModeHistoryList.

        Returns True when the key was handled/consumed.
        """
        # need at least one other item to diff against (either checked or next)
        idx = getattr(self, "index", None)
        nodes = getattr(self, "_nodes", [])
        if idx is None or idx < 0 or not nodes:
            try:
                self.app.push_screen(_TBDModal("No commit to diff with"))
            except Exception as e:
                self.printException(e, "showing no commit modal")
                pass
            return True

        # Find any checked item in the history
        checked_idx = None
        for i, node in enumerate(nodes):
            if getattr(node, "_checked", False):
                checked_idx = i
                break

        # helper to extract the text of a ListItem label
        def _text_of(node) -> str:
            try:
                # prefer stored raw text
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

        # Determine the pair of indices to diff: default is current vs next
        if checked_idx is None or checked_idx == idx:
            # behave as before: need a next item
            if idx >= len(nodes) - 1:
                try:
                    self.app.push_screen(_TBDModal("No earlier commit to diff with"))
                except Exception as e:
                    self.printException(e, "showing no earlier commit modal")
                    pass
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

        current_line = _text_of(nodes[i_newer])
        previous_line = _text_of(nodes[i_older])

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
                    pass
                return True

        # determine filename for the history (attached when populated)
        filename = getattr(self, "_filename", None)
        if not filename:
            try:
                self.app.push_screen(_TBDModal("Unknown filename for history"))
            except Exception as e:
                self.printException(e, "showing unknown filename modal")
                pass
            return True

        # Use centralized diff command builder on the app (handles variants)

        # Store current diff info for potential re-render
        self.app.current_commit_sha = current_hash
        self.app.current_prev_sha = previous_hash
        self.app.current_diff_file = filename

        try:
            cmd = self.app.build_diff_cmd(previous_hash, current_hash, filename)
            proc = subprocess.run(cmd, cwd=self.app.path, capture_output=True, text=True)
            diff_out = proc.stdout or proc.stderr or ""
        except Exception as exc:
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e, "showing subprocess error modal")
                pass
            return True

        # show the Diff column and populate it
        try:
            diff_view = self.app.query_one("#right2", ListView)
            try:
                diff_view.clear()
            except Exception as e:
                self.printException(e, "clearing diff view")
                pass

            # Header indicating which two hashes are being compared
            try:
                header = ListItem(Label(Text(f"Comparing: {previous_hash}..{current_hash}", style="bold")))
                diff_view.append(header)
            except Exception as e:
                self.printException(e, "appending diff header")
                pass

            if diff_out:
                for line in diff_out.splitlines():
                    # Colorize diff lines like git does (if enabled)
                    if self.app.colorize_diff:
                        if line.startswith("+++") or line.startswith("---"):
                            # File headers in bold white
                            styled_text = Text(line, style="bold white")
                        elif line.startswith("+"):
                            # Additions in green
                            styled_text = Text(line, style="green")
                        elif line.startswith("-"):
                            # Deletions in red
                            styled_text = Text(line, style="red")
                        elif line.startswith("@@"):
                            # Hunk headers in cyan
                            styled_text = Text(line, style="cyan")
                        elif line.startswith("diff --git") or line.startswith("index "):
                            # Diff metadata in bold
                            styled_text = Text(line, style="bold")
                        else:
                            # Context lines in default color
                            styled_text = Text(line)
                    else:
                        # No colorization
                        styled_text = Text(line)
                    diff_view.append(ListItem(Label(styled_text)))
            else:
                diff_view.append(ListItem(Label(Text(f"No diff between {previous_hash}..{current_hash}"))))

            # make sure Diff column is visible
            try:
                # Update the Diff column title to reflect selected variant
                try:
                    v = getattr(self.app, "diff_variants", [None])[getattr(self.app, "diff_cmd_index", 0)]
                    title_lbl = self.app.query_one("#right2-title", Label)
                    title_lbl.update(Text("Diff" if not v else f"Diff {v}", style="bold"))
                except Exception as e:
                    self.printException(e, "updating right2 title")
                    pass
                diff_view.styles.display = None
            except Exception as e:
                self.printException(e, "making diff column visible")
                pass

            try:
                diff_view.index = 0
            except Exception as e:
                self.printException(e, "setting diff view index")
                pass
            try:
                diff_view.focus()
            except Exception as e:
                self.printException(e, "focusing diff view")
                pass
        except Exception as exc:
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e, "showing diff error modal")
                pass
        return True


class RepoModeHistoryList(HistoryListBase):
    """RepoMode History list used when `-l/--log-first`"""

    def populate(self, repo_path: Optional[str] = None) -> None:  # RepoModeHistoryList
        """Populate this History list with a repository-wide commit log using pygit2.

        This method walks commits (by time) and appends ListItem entries with
        the format: "YYYY-MM-DD <short-hash> <subject>".
        """
        try:
            repo_path = repo_path or getattr(self.app, "path", None)
            if not repo_path:
                return
            gitdir = pygit2.discover_repository(repo_path)
            if not gitdir:
                return
            repo = pygit2.Repository(gitdir)

            # clear existing items
            try:
                self.clear()
            except Exception as e:
                self.printException(e)
                pass

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
                    pass

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
                    pass
                return

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
                pass

            try:
                self.index = 0
            except Exception as e:
                self.printException(e)
                pass
            try:
                self.focus()
            except Exception as e:
                self.printException(e)
                pass

        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)
                pass

    def key_right(self) -> bool:  # RepoModeHistoryList
        """Handle Right key: open a `RepoModeFileList` populated with changes between two commits.

        Determines the current and comparison commit (checked item or next item),
        computes the tree diff via pygit2, mounts a `RepoModeFileList` at the files
        column, and records the old/new commit hashes on that widget for later
        diff rendering.
        """
        try:
            nodes = getattr(self, "_nodes", [])
            if not nodes:
                try:
                    self.app.push_screen(_TBDModal("No commit selected"))
                except Exception as e:
                    self.printException(e)
                    pass
                return True
            idx = getattr(self, "index", None)
            if idx is None or idx < 0 or idx >= len(nodes):
                try:
                    self.app.push_screen(_TBDModal("No commit selected"))
                except Exception as e:
                    self.printException(e)
                    pass
                return True

            # Find checked item to diff against, if any
            checked_idx = None
            for i, node in enumerate(nodes):
                if getattr(node, "_checked", False):
                    checked_idx = i
                    break

            if checked_idx is None or checked_idx == idx:
                # default to current vs next (older)
                if idx >= len(nodes) - 1:
                    try:
                        self.app.push_screen(_TBDModal("No earlier commit to diff with"))
                    except Exception as e:
                        self.printException(e)
                        pass
                    return True
                i_newer = idx
                i_older = idx + 1
            else:
                i1 = idx
                i2 = checked_idx
                if i1 == i2:
                    try:
                        self.app.push_screen(_TBDModal("No commit to diff with"))
                    except Exception as e:
                        self.printException(e)
                        pass
                    return True
                if i1 < i2:
                    i_newer, i_older = i1, i2
                else:
                    i_newer, i_older = i2, i1

            def _text_of(node) -> str:
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
                    self.printException(e)
                    return str(node)

            current_line = _text_of(nodes[i_newer])
            previous_line = _text_of(nodes[i_older])

            current_hash = getattr(nodes[i_newer], "_hash", None)
            previous_hash = getattr(nodes[i_older], "_hash", None)
            if not current_hash or not previous_hash:
                try:
                    m1 = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", current_line)
                    m2 = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", previous_line)
                    if not m1 or not m2:
                        raise ValueError("Could not parse commit lines for hashes")
                    current_hash = m1.group(2)
                    previous_hash = m2.group(2)
                except Exception as exc:
                    try:
                        self.app.push_screen(_TBDModal(f"Could not parse hashes: {exc}"))
                    except Exception as e:
                        self.printException(e)
                        pass
                    return True

            # Compute diff between trees using pygit2
            try:
                repo_path = getattr(self.app, "path", None)
                gitdir = pygit2.discover_repository(repo_path)
                if not gitdir:
                    raise RuntimeError("Repository not found")
                repo = pygit2.Repository(gitdir)

                try:
                    curr_obj = repo.get(current_hash)
                except Exception as e:
                    self.printException(e)
                    curr_obj = None
                try:
                    prev_obj = repo.get(previous_hash)
                except Exception as e:
                    self.printException(e)
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
                try:
                    self.app.push_screen(_TBDModal(str(exc)))
                except Exception as e:
                    self.printException(e)
                    pass
                return True

            # Pre-build ListItem buffer from diff deltas so we can mount
            # the desired widget later and append items atomically. This
            # avoids mount-time races when replacing widgets with the
            # same id.
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
                        old_path = getattr(getattr(d, "old_file", None), "path", None)
                        new_path = getattr(getattr(d, "new_file", None), "path", None)
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
                        self.printException(e)
                        continue
                if not items_buffer:
                    items_buffer.append(ListItem(Label(Text(" No changed files between selected commits"))))
            except Exception as exc:
                self.printException(exc, "error building items buffer")

            # Mount RepoModeFileList in middle column (#right1)
            try:
                parent = self.app.query_one("#right1-column")
            except Exception as e:
                self.printException(e)
                parent = None

            try:
                # Reuse existing widget if present to avoid DuplicateIds
                try:
                    # Find any existing widgets with id 'right1'. Use `query` to
                    # tolerate multiple matches (which can throw otherwise).
                    file_list = None
                    try:
                        matches = list(self.app.query("#right1"))
                    except Exception as e:
                        self.printException(e)
                        matches = []

                    if matches:
                        # If there are multiple, remove all but the first to
                        # restore a sane state.
                        if len(matches) > 1:
                            for dup in matches[1:]:
                                try:
                                    dup.remove()
                                except Exception as e:
                                    self.printException(e, "removing duplicate right1")
                        existing = matches[0]
                        # If the existing widget is already the repo-mode
                        # specialized list, reuse it. Otherwise, remove the
                        # existing generic FileModeFileList and replace it
                        # with a RepoModeFileList so repo-specific key
                        # handlers work correctly.
                        if isinstance(existing, RepoModeFileList):
                            file_list = existing
                            try:
                                file_list.clear()
                            except Exception as e:
                                self.printException(e, "clearing existing right1")
                        else:
                            try:
                                # Instead of removing and mounting (which can
                                # trigger DuplicateIds races), convert the
                                # existing widget in-place to the repo-mode
                                # class by updating its __class__. This is a
                                # safe operation because both classes share the
                                # same base (`FileListBase`). Then clear it and
                                # reuse it.
                                try:
                                    existing.__class__ = RepoModeFileList
                                except Exception as e:
                                    self.printException(e, "exception converting existing right1 to RepoModeFileList")
                                file_list = existing
                                try:
                                    file_list.clear()
                                except Exception as e:
                                    self.printException(e, "clearing converted right1")
                            except Exception as e:
                                self.printException(e, "replacing right1 with repo-mode list")
                    else:
                        # No existing widget: create and mount one.
                        file_list = RepoModeFileList(id="right1")
                        try:
                            if parent is not None:
                                parent.mount(file_list)
                            else:
                                self.app.mount(file_list)
                        except Exception as e:
                            self.printException(e, "mounting right1")
                except Exception as e:
                    self.printException(e, "mount/create error")
                    try:
                        self.app.push_screen(_TBDModal("Could not show files for commit diff"))
                    except Exception as e:
                        self.printException(e)
                        pass
                    return True
            except Exception as exc:
                self.printException(exc, "unexpected mount error")
                try:
                    self.app.push_screen(_TBDModal("Could not show files for commit diff"))
                except Exception as e:
                    self.printException(e)
                    pass
                return True

            # Ensure the file_list is actually mounted. If our attempt to
            # create/mount a new widget failed (e.g. DuplicateIds), try to
            # locate the mounted `#right1` widget and use that instead. If
            # none is mounted, abort gracefully.
            try:
                mounted_ok = bool(getattr(file_list, "app", None))
            except Exception as e:
                self.printException(e)
                mounted_ok = False
            if not mounted_ok:
                try:
                    existing_mounted = None
                    try:
                        existing_mounted = self.app.query_one("#right1")
                    except Exception as e:
                        self.printException(e)
                        existing_mounted = None
                    if existing_mounted is not None and getattr(existing_mounted, "app", None):
                        file_list = existing_mounted
                    else:
                        try:
                            self.app.push_screen(_TBDModal("Could not mount file list for commit diff"))
                        except Exception as e:
                            self.printException(e)
                        return True
                except Exception as e:
                    self.printException(e, "verifying mounted right1")
                    return True

            # store commit hashes for later diff rendering
            try:
                file_list.current_commit_sha = current_hash
                file_list.current_prev_sha = previous_hash
            except Exception as e:
                self.printException(e)
                pass

            # Populate file_list from diff deltas
            try:
                delta_map = {
                    getattr(pygit2, "GIT_DELTA_ADDED", 0): "A",
                    getattr(pygit2, "GIT_DELTA_MODIFIED", 0): "M",
                    getattr(pygit2, "GIT_DELTA_DELETED", 0): "D",
                    getattr(pygit2, "GIT_DELTA_RENAMED", 0): "R",
                    getattr(pygit2, "GIT_DELTA_TYPECHANGE", 0): "T",
                }
                appended = 0
                items_buffer: list = []
                try:
                    # Attempting to set `styles.border` at runtime can raise
                    # a StyleValueError depending on Textual version; avoid
                    # setting it here and rely on CSS instead. Log for
                    # diagnostics so we know the widget we're populating.
                    logger.debug(
                        f"RepoModeHistoryList.key_right: populating file_list={file_list!r} mounted={bool(getattr(file_list,'app',None))}"
                    )
                except Exception as e:
                    self.printException(e)
                    pass
                # `diff.deltas` is an iterable; don't call it. If not present,
                # iterate `diff` directly which is also iterable in pygit2.
                for d in getattr(diff, "deltas", diff):
                    try:
                        status = getattr(d, "status", None)
                        marker = delta_map.get(status, " ")
                        old_path = getattr(getattr(d, "old_file", None), "path", None)
                        new_path = getattr(getattr(d, "new_file", None), "path", None)
                        display_path = new_path or old_path or ""
                        text = f"{marker} {display_path}"
                        li = ListItem(Label(Text(" " + text)))
                        li._change_type = marker
                        li._filename = display_path
                        li._old_filename = old_path
                        li._new_filename = new_path
                        li._hash_prev = previous_hash
                        li._hash_curr = current_hash
                        try:
                            items_buffer.append(li)
                            appended += 1
                            logger.debug(f"RepoModeHistoryList.key_right: buffered: {li._filename!r}")
                        except Exception as e:
                            self.printException(e)
                            continue
                    except Exception as e:
                        self.printException(e)
                        continue

                logger.debug(f"RepoModeHistoryList.key_right: total appended={appended}")
                if appended == 0:
                    try:
                        items_buffer.append(ListItem(Label(Text(" No changed files between selected commits"))))
                    except Exception as e:
                        self.printException(e)
                        pass
                # Append buffered items after the next refresh so mounts succeed
                try:
                    def _append_buffer():
                        try:
                            # Prevent double-appending if this callback runs more than once
                            if getattr(file_list, "_populated", False):
                                logger.debug("RepoModeHistoryList._append_buffer: already populated, skipping")
                                return
                            # Insert legend at top for repo-populated file lists
                            try:
                                key_li = ListItem(Label(Text("Key: ' ' tracked  U untracked  M modified  A staged  D deleted  I ignored  ! conflicted", style="bold")))
                                try:
                                    file_list.append(key_li)
                                    try:
                                        file_list._min_index = 1
                                    except Exception as e:
                                        self.printException(e)
                                        pass
                                except Exception as e:
                                    self.printException(e)
                                    pass
                            except Exception as e:
                                self.printException(e)
                                pass

                            for it in items_buffer:
                                try:
                                    file_list.append(it)
                                    logger.debug(f"RepoModeHistoryList._append_buffer: appended item to file_list: {getattr(it,'_filename',None)!r}")
                                except Exception as e:
                                    self.printException(e)
                                    continue
                            try:
                                file_list.index = getattr(file_list, "_min_index", 0) or 0
                            except Exception as e:
                                self.printException(e)
                                pass
                            try:
                                file_list.focus()
                            except Exception as e:
                                self.printException(e)
                                pass
                            try:
                                # Ensure the widget is refreshed so appended
                                # items are rendered immediately.
                                try:
                                    file_list.refresh()
                                except Exception as e:
                                    self.printException(e)
                                    pass
                                try:
                                    # After refresh and any focus side-effects,
                                    # enforce the minimum selectable index so
                                    # the legend row isn't selected.
                                    try:
                                        file_list.call_after_refresh(
                                            lambda: setattr(file_list, "index", getattr(file_list, "_min_index", 0) or 0)
                                        )
                                    except Exception:
                                        try:
                                            file_list.index = getattr(file_list, "_min_index", 0) or 0
                                        except Exception as e:
                                            self.printException(e, "exception enforcing min index after refresh")
                                            pass
                                except Exception as e:
                                    self.printException(e)
                                    pass
                                # Diagnostic: log node filenames and display/style state
                                try:
                                    nodes = getattr(file_list, "_nodes", [])
                                    names = [getattr(n, "_filename", None) for n in nodes]
                                    logger.debug(
                                        f"RepoModeHistoryList._append_buffer: file_list._nodes length={len(nodes)} names={names} display={getattr(file_list.styles, 'display', None)}"
                                    )
                                except Exception as e:
                                    self.printException(e)
                            except Exception as e:
                                self.printException(e)
                                pass
                            # Mark as populated and clear buffer so retries are harmless
                            try:
                                file_list._populated = True
                            except Exception as e:
                                self.printException(e)
                                pass
                            try:
                                items_buffer.clear()
                            except Exception as e:
                                self.printException(e)
                                pass
                        except Exception as e:
                            self.printException(e, "error appending buffer")
                            return

                    try:
                        # Prefer to schedule appends after refresh to ensure
                        # the ListView is ready to mount items. Use the
                        # widget's `call_after_refresh` when available; if
                        # not, fall back to the app's `call_after_refresh`.
                        try:
                            file_list.call_after_refresh(_append_buffer)
                        except Exception as e:
                            self.printException(e, "file_list.call_after_refresh failed")
                            try:
                                # Fallback to app-level scheduling
                                self.app.call_after_refresh(_append_buffer)
                            except Exception as e:
                                self.printException(e, "app.call_after_refresh failed")
                                # Last resort: immediate append
                                _append_buffer()
                    except Exception as e:
                        self.printException(e, "scheduling append buffer")
                except Exception as e:
                    self.printException(e, "scheduling append buffer")
            except Exception as exc:
                self.printException(exc, "error populating file list")

            logger.debug(f"RepoModeHistoryList.key_right: finished populating file_list mounted={bool(getattr(file_list,'app',None))} nodes={len(getattr(file_list,'_nodes',[]))}")

            try:
                file_list.styles.display = None
            except Exception as e:
                self.printException(e)
                pass
            try:
                file_list.index = getattr(file_list, "_min_index", 0) or 0
            except Exception as e:
                self.printException(e)
                pass
            try:
                # Make the Files column visible and adjust column widths so
                # History (left) and Files (right1) split the screen.
                try:
                    self.app.query_one("#left-column").styles.width = "25%"
                    self.app.query_one("#left-column").styles.flex = 0
                except Exception as e:
                    self.printException(e, "setting left-column width for files view")
                try:
                    self.app.query_one("#right1-column").styles.width = "75%"
                    self.app.query_one("#right1-column").styles.flex = 0
                except Exception as e:
                    self.printException(e, "setting right1-column width for files view")
                try:
                    # Update titles for log-first layout: left remains History
                    lbl = self.app.query_one("#left-title", Label)
                    lbl.update(Text("History", style="bold"))
                except Exception as e:
                    self.printException(e, "updating left-title for files view")
                try:
                    lbl = self.app.query_one("#right1-title", Label)
                    lbl.update(Text("Files", style="bold"))
                except Exception as e:
                    self.printException(e, "updating right1-title for files view")

                file_list.focus()
            except Exception as e:
                self.printException(e)
                pass
            try:
                # Diagnostic: log the column styles and file_list state
                try:
                    left_col = self.app.query_one("#left-column")
                    right1_col = self.app.query_one("#right1-column")
                    logger.debug(
                        f"RepoModeHistoryList.key_right: columns left={getattr(left_col.styles,'width',None)} right1={getattr(right1_col.styles,'width',None)}"
                    )
                except Exception as e:
                    self.printException(e)
                    pass
                try:
                    logger.debug(
                        f"RepoModeHistoryList.key_right: file_list.display={getattr(file_list.styles,'display',None)} nodes={len(getattr(file_list,'_nodes',[]))}"
                    )
                except Exception as e:
                    self.printException(e)
                    pass
            except Exception as e:
                self.printException(e)
                pass

            try:
                self.app.current_commit_sha = current_hash
                self.app.current_prev_sha = previous_hash
            except Exception as e:
                self.printException(e)
                pass

        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)
                pass
        return True


class DiffListBase(AppBase):
    """ListView used for the Diff column. Left arrow moves focus back to History.

    Renamed to `DiffListBase` to allow a thin `DiffList` subclass for
    backwards compatibility and easier future refactors.
    """

    def on_key(self, event: events.Key) -> None:  # DiffListBase
        """
        Handle left key to move focus back to History;
        handle PgUp/PgDn with visible selection; handle c/C to toggle colorization.
        """
        key = event.key
        logger.debug(f"DiffList.on_key: key={key}")

        # Handle f/F to toggle fullscreen diff
        if key and key.lower() == "f":
            event.stop()
            try:
                if getattr(self.app, "diff_fullscreen", False):
                    self.app.exit_diff_fullscreen()
                    # keep focus on diff after restoring
                    try:
                        self.focus()
                    except Exception as e:
                        self.printException(e, "focus() exception")
                        pass
                else:
                    # Only enter fullscreen when diff is visible (columnated)
                    try:
                        # require right2 to be visible
                        if self.app.query_one("#right2", ListView).styles.display != "none":
                            self.app.enter_diff_fullscreen()
                            try:
                                self.focus()
                            except Exception as e:
                                self.printException(e, "focus() after enter_diff_fullscreen exception")
                                pass
                    except Exception as e:
                        self.printException(e, "enter fullscreen check exception")
                        # best-effort: enter anyway
                        try:
                            self.app.enter_diff_fullscreen()
                        except Exception as e:
                            self.printException(e, "fallback enter_diff_fullscreen exception")
                            pass
            except Exception as e:
                self.printException(e, "exception toggling fullscreen f/F")
            return

        # Handle c/C to toggle colorization
        if key and key.lower() == "c":
            event.stop()
            logger.debug(f"DiffList: c/C pressed, colorize_diff={getattr(self.app, 'colorize_diff', None)}")
            try:
                # Toggle the colorization flag
                self.app.colorize_diff = not self.app.colorize_diff
                logger.debug(f"DiffList: toggled to colorize_diff={self.app.colorize_diff}")

                # Re-render the diff if we have current diff info
                logger.debug(
                    f"DiffList: current_commit_sha={getattr(self.app, 'current_commit_sha', None)}, current_prev_sha={getattr(self.app, 'current_prev_sha', None)}, current_diff_file={getattr(self.app, 'current_diff_file', None)}"
                )
                if (
                    getattr(self.app, "current_commit_sha", None)
                    and getattr(self.app, "current_prev_sha", None)
                    and getattr(self.app, "current_diff_file", None)
                ):

                    logger.debug("DiffList: re-rendering diff with new colorization")
                    # Save current scroll position and selection
                    saved_scroll_y = self.scroll_y
                    saved_index = self.index

                    # Directly re-run the diff command
                    previous_hash = self.app.current_prev_sha
                    current_hash = self.app.current_commit_sha
                    filename = self.app.current_diff_file

                    # Use app-level builder so the selected diff variant is applied
                    cmd = self.app.build_diff_cmd(previous_hash, current_hash, filename)
                    proc = subprocess.run(cmd, cwd=self.app.path, capture_output=True, text=True)
                    diff_out = proc.stdout or proc.stderr or ""

                    # Clear and repopulate
                    self.clear()

                    # Header
                    header = ListItem(Label(Text(f"Comparing: {previous_hash}..{current_hash}", style="bold")))
                    self.append(header)

                    if diff_out:
                        for line in diff_out.splitlines():
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
                    else:
                        self.append(ListItem(Label(Text(f"No diff between {previous_hash}..{current_hash}"))))

                    # Restore scroll position and selection
                    def restore_state():
                        try:
                            self.scroll_y = saved_scroll_y
                            if saved_index is not None:
                                self.index = saved_index
                        except Exception as e:
                            self.printException(e)
                            pass

                    self.call_after_refresh(restore_state)
                    logger.debug("DiffList: diff re-rendered successfully")
                else:
                    logger.debug("DiffList: no current diff info available")

            except Exception as e:
                self.printException(e, "exception in c/C handler")
            return
        # Handle d/D to rotate diff command variant
        if key and key.lower() == "d":
            event.stop()
            try:
                variants = getattr(
                    self.app, "diff_variants", [None, "--ignore-space-change", "--diff-algorithm=patience"]
                )
                cur = getattr(self.app, "diff_cmd_index", 0)
                cur = (cur + 1) % max(1, len(variants))
                self.app.diff_cmd_index = cur
                logger.debug(f"DiffList: rotated diff_cmd_index to {cur}, variant={variants[cur]}")
                # Update footer to show current variant briefly
                try:
                    footer = self.app.query_one("#footer", Label)
                    v = variants[cur]
                    vlabel = v if v else "default"
                    if getattr(self.app, "diff_fullscreen", False):
                        footer.update(Text(f"q(uit)  ?/h(elp)  ↑ ↓   ←/f(ull)  d:{vlabel}", style="bold"))
                    else:
                        footer.update(
                            Text(f"q(uit)  ?/h(elp)  ← ↑ ↓   PgUp/PgDn  c(olor)  →/f(ull)  d:{vlabel}", style="bold")
                        )
                except Exception as e:
                    self.printException(e, "could not schedule timer or call_after_refresh")
                    pass
                # Update Diff column title to show current variant
                try:
                    title_lbl = self.app.query_one("#right2-title", Label)
                    v = variants[cur]
                    title_text = "Diff" if not v else f"Diff {v}"
                    title_lbl.update(Text(title_text, style="bold"))
                except Exception as e:
                    self.printException(e, "updating right2 title exception")
                    pass

                # Re-render current diff if available
                if (
                    getattr(self.app, "current_commit_sha", None)
                    and getattr(self.app, "current_prev_sha", None)
                    and getattr(self.app, "current_diff_file", None)
                ):

                    previous_hash = self.app.current_prev_sha
                    current_hash = self.app.current_commit_sha
                    filename = self.app.current_diff_file

                    saved_scroll_y = self.scroll_y
                    saved_index = self.index

                    cmd = self.app.build_diff_cmd(previous_hash, current_hash, filename)
                    proc = subprocess.run(cmd, cwd=self.app.path, capture_output=True, text=True)
                    diff_out = proc.stdout or proc.stderr or ""

                    # Clear and repopulate
                    self.clear()
                    header = ListItem(Label(Text(f"Comparing: {previous_hash}..{current_hash}", style="bold")))
                    self.append(header)

                    if diff_out:
                        for line in diff_out.splitlines():
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
                    else:
                        self.append(ListItem(Label(Text(f"No diff between {previous_hash}..{current_hash}"))))

                    def restore_state():
                        try:
                            self.scroll_y = saved_scroll_y
                            if saved_index is not None:
                                self.index = saved_index
                        except Exception as e:
                            self.printException(e)
                            pass

                    self.call_after_refresh(restore_state)
                    logger.debug("DiffList: diff re-rendered after rotating variant")
            except Exception as e:
                self.printException(e, "exception rotating diff variant")
            return

        if key and key.lower() == "q":
            try:
                event.key = key.lower()
            except Exception as e:
                self.printException(e, "exception in q handler")
            return
        if key == "left":
            event.stop()
            try:
                if self.key_left():
                    return
            except Exception as e:
                self.printException(e, "key_left exception")
            return

        if key == "right":
            event.stop()
            try:
                if self.key_right():
                    return
            except Exception as e:
                self.printException(e, "key_right exception")
            return

        # Handle PageUp/PageDown - move selection by page and scroll to position it appropriately
        if key in ("pageup", "pagedown"):
            logger.debug(f"DiffList: {key} pressed")
            event.stop()
            try:
                nodes = getattr(self, "_nodes", [])
                if not nodes:
                    logger.debug(f"DiffList: WARNING - _nodes is empty for {key}")
                    return

                current_index = self.index if self.index is not None else 0
                visible_height = self.scrollable_content_region.height
                page_size = max(1, visible_height // 2)  # Half screen at a time like built-in behavior

                logger.debug(
                    f"DiffList: {key} - current_index={current_index}, page_size={page_size}, visible_height={visible_height}, nodes={len(nodes)}"
                )

                # Calculate new index
                if key == "pagedown":
                    new_index = min(current_index + page_size, len(nodes) - 1)
                else:  # pageup
                    new_index = max(current_index - page_size, 0)

                logger.debug(f"DiffList: {key} - moving from index {current_index} to {new_index}")

                # We want to change the viewport first, then highlight the new line.
                # Schedule a scroll callback that sets scroll_y, then set the index
                # after the scroll has been applied so highlighting is stable.
                def scroll_to_position():
                    try:
                        # Assume each line is ~1 unit tall, so scroll position ≈ line index
                        if key == "pagedown":
                            target_scroll = float(new_index)
                        else:  # pageup
                            target_scroll = float(max(0, new_index - visible_height + 1))

                        # Clamp to valid scroll range
                        max_scroll = float(max(0, len(nodes) - visible_height))
                        target_scroll = max(0.0, min(target_scroll, max_scroll))

                        logger.debug(
                            f"DiffList: {key} - before scroll: scroll_y={self.scroll_y}, setting to {target_scroll}, max_scroll={max_scroll}"
                        )
                        # Try to animate the scroll for smooth movement. If animate
                        # is supported, schedule the highlight after the animation
                        # duration. Otherwise fall back to instant set.
                        anim_duration = 0.12
                        try:
                            # Some Textual versions support Widget.animate
                            try:
                                self.animate("scroll_y", target_scroll, duration=anim_duration)
                                logger.debug(f"DiffList: {key} - started animate(scroll_y -> {target_scroll})")

                                def _finalize_highlight():
                                    try:
                                        old_index = self.index
                                        self.index = None
                                        self.index = new_index
                                        try:
                                            if old_index is not None and old_index < len(nodes):
                                                nodes[old_index].remove_class("-highlight")
                                            if new_index < len(nodes):
                                                nodes[new_index].add_class("-highlight")
                                        except Exception as e:
                                            self.printException(e, "exception managing highlight classes after animate")
                                    except Exception as e:
                                        self.printException(e, "exception finalizing highlight after animate")

                                try:
                                    # schedule after animation completes
                                    self.set_timer(anim_duration + 0.02, _finalize_highlight)
                                except Exception as e:
                                    self.printException(e, "set_timer not available, falling back")
                                    # fallback to call_after_refresh if set_timer not available
                                    self.call_after_refresh(_finalize_highlight)
                            except Exception as e:
                                self.printException(e, "animate not available, falling back")
                                # If animate not available, fall back to instant scroll
                                self.scroll_y = target_scroll
                                logger.debug(f"DiffList: {key} - after instant scroll: scroll_y={self.scroll_y}")
                                try:
                                    old_index = self.index
                                    self.index = None
                                    self.index = new_index
                                    try:
                                        if old_index is not None and old_index < len(nodes):
                                            nodes[old_index].remove_class("-highlight")
                                        if new_index < len(nodes):
                                            nodes[new_index].add_class("-highlight")
                                    except Exception as e:
                                        self.printException(
                                            e, "exception managing highlight classes after instant scroll"
                                        )
                                except Exception as e:
                                    self.printException(e, "exception setting index after instant scroll")
                        except Exception as e:
                            self.printException(e, "exception in scroll_to_position")
                    except Exception as e:
                        self.printException(e, "exception in scroll_to_position")

                # Schedule the scroll -> highlight sequence
                self.call_after_refresh(scroll_to_position)

            except Exception as e:
                self.printException(e, "exception in {key} handler")
            return

    # let other keys be handled by default (up/down handled by ListView)

    def key_left(self) -> bool:  # DiffListBase
        """Handle left key behavior for DiffListBase.

        Returns True when the key was handled/consumed.
        """
        return False

    def key_right(self) -> bool:  # DiffListBase
        """Handle right key behavior for DiffListBase.

        Returns True when the key was handled/consumed.
        """
        return False

    def on_focus(self, event: events.Focus) -> None:  # DiffListBase
        """When the DiffList receives focus, ensure the first item is highlighted."""
        try:

            def _apply() -> None:
                try:
                    nodes = getattr(self, "_nodes", [])
                    if not nodes:
                        return
                    target = self.index if self.index is not None else 0
                    try:
                        self.index = None
                    except Exception as e:
                        self.printException(e)
                        pass
                    try:
                        self.index = target
                    except Exception as e:
                        self.printException(e)
                        pass
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
            pass

        # When Diff receives focus, show all columns and set widths: Files 5%, History 20%, Diff 75%
        try:
            try:
                left = self.app.query_one("#left")
                if not isinstance(left, FileModeFileList):
                    try:
                        left = self.app.query_one("#right1")
                    except Exception as e:
                        self.printException(e)
                        left = None
            except Exception as e:
                self.printException(e)
                try:
                    left = self.app.query_one("#right1")
                except Exception as e:
                    self.printException(e)
                    left = None
            try:
                hist = self.app.query_one("#right1", FileModeHistoryList)
            except Exception:
                try:
                    hist = self.app.query_one("#right1")
                except Exception:
                    hist = None
            diff = self.app.query_one("#right2", ListView)
            try:
                # adjust outer columns to the target proportions
                try:
                    self.app.query_one("#left-column").styles.width = "5%"
                    self.app.query_one("#left-column").styles.flex = 0
                except Exception as e:
                    self.printException(e)
                    pass
                try:
                    self.app.query_one("#right1-column").styles.width = "15%"
                    self.app.query_one("#right1-column").styles.flex = 0
                except Exception as e:
                    self.printException(e)
                    pass
                try:
                    self.app.query_one("#right2-column").styles.width = "80%"
                    self.app.query_one("#right2-column").styles.flex = 0
                except Exception as e:
                    self.printException(e)
                    pass
                left.styles.width = "100%"
                left.styles.flex = 0
            except Exception as e:
                self.printException(e)
                pass
            try:
                if hist is not None:
                    try:
                        hist.styles.width = "100%"
                        hist.styles.flex = 0
                        hist.styles.display = None
                    except Exception as e:
                        self.printException(e)
                        pass
            except Exception as e:
                self.printException(e)
                pass
            try:
                diff.styles.width = "100%"
                diff.styles.display = None
                diff.styles.flex = 0
            except Exception as e:
                self.printException(e)
                pass
        except Exception as e:
            self.printException(e)
            pass
        # Show all titles when diff active
        try:
            lbl = self.app.query_one("#left-title", Label)
            # Prefer to show full "Files" when there's enough room; otherwise shorten
            try:
                left = self.app.query_one("#left")
                width = None
                # prefer computed region if available
                if hasattr(left, "region") and left.region is not None:
                    width = left.region.width
                elif hasattr(left, "size") and left.size is not None:
                    width = left.size.width
                else:
                    # fallback to container percent width if set
                    col = self.app.query_one("#left-column")
                    s = getattr(col.styles, "width", None)
                    if isinstance(s, str) and s.endswith("%") and getattr(self, "size", None):
                        try:
                            percent = float(s[:-1]) / 100.0
                            width = int(self.size.width * percent)
                        except Exception as e:
                            self.printException(e)
                            width = None
                if width is not None and width >= 8:
                    lbl.update(Text("Files", style="bold"))
                else:
                    lbl.update(Text("Fi", style="bold"))
            except Exception as e:
                self.printException(e)
                # If any lookup fails, use the shortened title to be safe
                lbl.update(Text("Fi", style="bold"))
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e)
            pass
        try:
            lbl = self.app.query_one("#right1-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e)
            pass
        try:
            lbl = self.app.query_one("#right2-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e)
            pass

        # DiffList footer
        try:
            footer = self.app.query_one("#footer", Label)
            # Show fullscreen hint depending on current fullscreen state
            if getattr(self.app, "diff_fullscreen", False):
                footer.update(Text("q(uit)  ?/h(elp)  ↑ ↓   ←/f(ull)", style="bold"))
            else:
                footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓   PgUp/PgDn  c(olor)  →/f(ull)", style="bold"))
        except Exception as e:
            self.printException(e)
            pass


class FileModeDiffList(DiffListBase):
    """FileMode DiffList subclass; see `DiffListBase` for shared logic."""

    def key_left(self) -> bool:  # FileModeDiffList
        """Handle left key behavior

        Returns True when the key was handled/consumed.
        """
        try:
            # If we're in fullscreen, left arrow exits fullscreen
            if getattr(self.app, "diff_fullscreen", False):
                try:
                    self.app.exit_diff_fullscreen()
                except Exception as e:
                    self.printException(e, "exception exiting fullscreen on left")
                return True
            # otherwise move focus back to History
            try:
                try:
                    hist = self.app.query_one("#right1", FileModeHistoryList)
                except Exception:
                    try:
                        hist = self.app.query_one("#right1")
                    except Exception:
                        hist = None
                if hist is not None:
                    hist.focus()
            except Exception as e:
                self.printException(e, "exception focusing history")
                return True
            return True
        except Exception as e:
            self.printException(e, "unexpected exception")
            return True
        return True

    def key_right(self) -> bool:  # FileModeDiffList
        """Handle right key behavior

        Returns True when the key was handled/consumed.
        """
        try:
            # In columnated mode, pressing right expands Diff to fullscreen.
            if getattr(self.app, "diff_fullscreen", False):
                # already fullscreen; right arrow does nothing
                return True
            # If diff is visible and not fullscreen, enter fullscreen
            try:
                right1_display = self.app.query_one("#right1").styles.display
                right2_display = self.app.query_one("#right2").styles.display
                if right1_display != "none" and right2_display != "none":
                    try:
                        self.app.enter_diff_fullscreen()
                    except Exception as e:
                        self.printException(e, "enter_diff_fullscreen exception")
                    try:
                        self.focus()
                    except Exception as e:
                        self.printException(e)
                        pass
                    return True
            except Exception as e:
                self.printException(e, "checking displays for fullscreen failed")
                # best-effort enter
                try:
                    self.app.enter_diff_fullscreen()
                except Exception as e:
                    self.printException(e, "fallback enter_diff_fullscreen exception")
                    pass
            return True
        except Exception as e:
            self.printException(e, "unexpected exception")
            return True
        return True


class RepoModeDiffList(DiffListBase):
    """Placeholder Diff list for repo-first / log-first mode.

    Currently a no-op subclass; behavior will be added in follow-up edits.
    """

    pass


class _TBDModal(ModalScreen):
    """Simple modal that shows a message (default "TBD") and closes on any key."""

    def __init__(self, message: str | None = None, **kwargs) -> None:
        """Create the modal with an optional `message` to display."""
        super().__init__(**kwargs)
        self.message = message or "TBD"

    def compose(self) -> ComposeResult:
        """Compose the modal contents (a single Static message)."""
        yield Static(Text(self.message, style="bold"), id="tbd-msg")

    def on_key(self, event: events.Key) -> None:
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
    - ` ` (space) tracked & clean — bright white
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

`gitdiffnavtool.py [--no-color] [{path}]`

If `--no-color` is provided, the diff output will not be colorized.
`{path}` is optional — it defaults to the current working directory. If a filename is provided, the app will open its directory and populate the History column for that file on startup.
"""


class HelpList(AppBase):
    """Help column showing usage and short docs.

    The contents are a plain listing derived from the README.
    """

    def on_mount(self) -> None:
        """Populate help content."""
        # Split help text into lines and add as list items
        lines = HELP_TEXT.split("\n")
        linelen = len(lines)
        for i, line in enumerate(lines):
            if i < linelen - 1:
                if lines[i + 1].startswith("=="):
                    lines[i] = "<title>" + lines[i].strip()
                    lines[i + 1] = ""
                if lines[i + 1].startswith("--"):
                    lines[i] = "<heading>" + lines[i].strip()
                    lines[i + 1] = ""
            lline = line.lstrip()
            if lline.startswith("-") or lline.startswith("*"):
                n = len(line) - len(lline)
                lines[i] = f"<bullet{n//2}>" + lline[1:].strip()
            elif line and line[0].isspace():
                n = len(line) - len(lline)
                lines[i] = f"<indent{n}>" + lline

        # change two ore more consecutive blank lines to a single blank line
        newlines = []
        prev_blank = False
        for line in lines:
            if line.strip() == "":
                if not prev_blank:
                    newlines.append(line)
                    prev_blank = True
            else:
                newlines.append(line)
                prev_blank = False

        lines = newlines

        # various bullet styles
        bullets = ["◉", "○", "♦", "◊", "—"]

        for i, line in enumerate(lines):
            indent = 0
            al = "left"
            style = ""
            if line.startswith("<title>"):
                # present as a centered bold string; append and skip parsing
                line = line[7:]
                style = "bold"
                lbl = Label(Align(Text(line, style=style), align="center"))
                lbl.styles.width = "100%"
                self.append(ListItem(lbl))
                continue
            elif line.startswith("<heading>"):
                line = line[9:]
                # present as a left-justified underlined bold string
                style = "bold underline"
            elif line.startswith("<bullet"):
                # determine the number following "<bullet"
                # present as an indentation of 2*n spaces followed by bullets[n]
                m = re.match(r"<bullet(\d+)>(.*)", line)
                if m:
                    n = int(m.group(1))
                    indent = 2 * n
                    line = (" " * indent) + bullets[n % len(bullets)] + " " + m.group(2).lstrip()
            elif line.startswith("<indent"):
                # determine the number following "<indent"
                # present as an indentation of 2*n spaces
                m = re.match(r"<indent(\d+)>(.*)", line)
                if m:
                    n = int(m.group(1))
                    indent = 2 * n
                    line = (" " * indent) + m.group(2).lstrip()

            # create a fresh Text for this line so styles never carry over
            text = Text()
            inFixed = False
            inBold = False
            inItalics = False
            modes = ["", "", ""]
            # instead of using split(), cycle through the string one
            # character at a time and look for both "`", "*" and "_".
            # When we see a "`", toggle inFixed state.
            # When we see "*" or "_", toggle bold/italic state.
            nline = ""
            for c in line:
                modeChanged = False
                # capture previous modes so we flush preceding text with
                # the styles that were active before the toggle.
                prev_modes = list(modes)
                if c == "`":
                    inFixed = not inFixed
                    modeChanged = True
                    if inFixed:
                        modes[0] = "white on bright_blue"
                    else:
                        modes[0] = ""
                elif c == "*":
                    inBold = not inBold
                    modeChanged = True
                    if inBold:
                        modes[1] = "bold"
                    else:
                        modes[1] = ""
                elif c == "_":
                    inItalics = not inItalics
                    modeChanged = True
                    if inItalics:
                        modes[2] = "italic"
                    else:
                        modes[2] = ""
                if modeChanged:
                    # flush nline with the previous modes (pre-toggle)
                    if nline != "":
                        parts = [style] + [m for m in prev_modes if m]
                        nstyle = " ".join([p for p in parts if p]) or None
                        text.append(nline, style=nstyle)
                        nline = ""
                else:
                    nline += c

            # flush any remaining nline
            if nline != "":
                parts = [style] + [m for m in modes if m]
                nstyle = " ".join([p for p in parts if p]) or None
                text.append(nline, style=nstyle)

            if al == "center":
                lbl = Label(Align(text, align="center"))
                lbl.styles.width = "100%"
                self.append(ListItem(lbl))
            else:
                self.append(ListItem(Label(text)))

    def on_key(self, event: events.Key) -> None:
        """Handle keys - go back to files view on any key except arrows/quit."""
        key = event.key
        logger.debug(f"HelpList.on_key: key={key}")

        # Allow arrow keys for scrolling, quit for quitting
        if key in ("up", "down", "pageup", "pagedown", "q", "Q"):
            return
        # Any other key: return to previous view
        try:
            event.stop()
            logger.debug(f"Restoring column state: {self.app.saved_column_state}")
            # Hide help column
            self.app.query_one("#right3-column").styles.width = "0%"
            self.app.query_one("#right3-column").styles.flex = 0

            # Determine which widget to focus based on saved state
            focus_target = "#left"  # default

            # Restore saved column state if available
            if self.app.saved_column_state:
                state = self.app.saved_column_state
                # Restore left column
                self.app.query_one("#left-column").styles.width = state["left"]["width"]
                self.app.query_one("#left-column").styles.flex = state["left"]["flex"]
                # Restore right1 column
                self.app.query_one("#right1-column").styles.width = state["right1"]["width"]
                self.app.query_one("#right1-column").styles.flex = state["right1"]["flex"]
                self.app.query_one("#right1").styles.display = state["right1"]["display"]
                # Restore right2 column
                self.app.query_one("#right2-column").styles.width = state["right2"]["width"]
                self.app.query_one("#right2-column").styles.flex = state["right2"]["flex"]
                self.app.query_one("#right2").styles.display = state["right2"]["display"]

                # Determine focus target: rightmost visible column
                if state["right2"]["display"] != "none":
                    focus_target = "#right2"
                elif state["right1"]["display"] != "none":
                    focus_target = "#right1"

                logger.debug("Column state restored")
            else:
                logger.debug("No saved state, showing only files column")
                # Fallback: just show files column
                self.app.query_one("#left-column").styles.width = "100%"
                self.app.query_one("#left-column").styles.flex = 0
                self.app.query_one("#right1-column").styles.width = "0%"
                self.app.query_one("#right1-column").styles.flex = 0
                self.app.query_one("#right2-column").styles.width = "0%"
                self.app.query_one("#right2-column").styles.flex = 0

            # Focus on the appropriate widget
            # Use call_after_refresh to avoid triggering on_focus during layout restore
            def restore_focus():
                try:
                    self.app.query_one(focus_target).focus()
                except Exception as e:
                    self.printException(e)

            self.app.call_after_refresh(restore_focus)

            # HelpList footer
            footer = self.app.query_one("#footer", Label)
            footer.update(Text("q(uit)  ?h/(elp)  ← ↑ ↓ →", style="bold"))
        except Exception as e:
            self.printException(e)


class GitHistoryTool(App):
    """Main Textual application providing the three-column git navigator.

    The app composes three columns: `Files`, `History`, and `Diff`. It builds a
    repository cache (using `pygit2`) and handles keyboard
    navigation and git operations to populate history and diffs.
    """

    TITLE = "Git Diff History Navigator Tool"
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
#left {
    border: solid white;
    scrollbar-size-vertical: 1;
}
#right1 {
    border: heavy #555555;
    scrollbar-size-vertical: 1;
}
#right2 {
    border: heavy #555555;
    scrollbar-size-vertical: 1;
}
#right3 {
    border: heavy #555555;
    scrollbar-size-vertical: 1;
}
/* footer area: show quit and navigation hints */
#footer {
    height: 1;
    padding: 0 1;
    text-align: left;
}
"""

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(
        self, path: Optional[str] = None, colorize_diff: bool = True, log_first: bool = False, **kwargs
    ) -> None:
        """Initialize the app state.

        If `path` names a file, treat its directory as the working path and
        remember the filename to open its history on mount.
        """
        # Ensure `log_first` is available before `compose` runs in the
        # Textual `App` initialization so the UI can be composed with the
        # correct column ordering when starting in log-first mode.
        self.log_first: bool = bool(log_first)
        super().__init__(**kwargs)
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
        # column state for restoring after help
        self.saved_column_state: Optional[dict] = None
        # colorization state and current diff info
        self.colorize_diff = colorize_diff
        self.current_commit_sha: Optional[str] = None
        self.current_prev_sha: Optional[str] = None
        self.current_diff_file: Optional[str] = None
        # Diff fullscreen flag: when True, Diff column occupies 100% width
        self.diff_fullscreen: bool = False
        # Diff command variants and current selection index
        # None = default `git diff`; other entries are flags inserted after `git diff`
        self.diff_variants: list[Optional[str]] = [None, "--ignore-space-change", "--diff-algorithm=patience"]
        self.diff_cmd_index: int = 0
        # start the app showing repository-wide commit log first when True

    def build_diff_cmd(self, prev: str | None, curr: str | None, fname: str) -> list[str]:
        """Construct the git diff command honoring the currently selected variant.

        The variant (if not None) is inserted right after `git diff` so that
        options like `--ignore-space-change` and `--diff-algorithm=patience`
        are applied to the invoked command.
        """

        def _is_pseudo(h: str | None) -> bool:
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

    def build_repo_cache(self) -> None:
        """
        Discover repository (if any) and build in-memory index/status maps.
        """
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

    def compose(self) -> ComposeResult:
        """Compose the app UI: title, four-column layout, and footer hints."""
        with Vertical(id="root"):
            yield Label(Text(self.TITLE, style="bold"), id="title")
            with Horizontal(id="main"):
                # allow alternate column ordering when starting in log-first mode
                if getattr(self, "log_first", False):
                    # History on the left, Files in the middle, Diff on the right
                    with Vertical(id="left-column"):
                        yield Label(Text("History", style="bold"), id="left-title")
                        yield RepoModeHistoryList(id="left")
                    with Vertical(id="right1-column"):
                        yield Label(Text("Files", style="bold"), id="right1-title")
                        yield FileModeFileList(id="right1")
                    with Vertical(id="right2-column"):
                        yield Label(Text("Diff", style="bold"), id="right2-title")
                        yield FileModeDiffList(id="right2")
                    with Vertical(id="right3-column"):
                        yield Label(Text("Help", style="bold"), id="right3-title")
                        yield HelpList(id="right3")
                else:
                    # default: Files on the left, History middle, Diff right
                    with Vertical(id="left-column"):
                        yield Label(Text("Files", style="bold"), id="left-title")
                        yield FileModeFileList(id="left")
                    # three minimal right columns
                    with Vertical(id="right1-column"):
                        yield Label(Text("History", style="bold"), id="right1-title")
                        yield FileModeHistoryList(id="right1")
                    with Vertical(id="right2-column"):
                        yield Label(Text("Diff", style="bold"), id="right2-title")
                        yield FileModeDiffList(id="right2")
                    with Vertical(id="right3-column"):
                        yield Label(Text("Help", style="bold"), id="right3-title")
                        yield HelpList(id="right3")

            # GitHistoryTool footer
            yield Label(Text("q(uit)  ?/h(elp)  ← ↑ ↓ →", style="bold"), id="footer")

    async def on_mount(self) -> None:
        """Mount-time initialization: build repo cache and populate Files.

        This method configures initial layout sizes, builds the repository
        cache, and sets the initial path listing. If the app was launched with
        a filename, it will also open that file's history.
        """
        # Query columns generically — types differ when `log_first` is active
        try:
            left = self.query_one("#left")
        except Exception as e:
            self.printException(e)
            left = None
        try:
            right1 = self.query_one("#right1")
        except Exception as e:
            self.printException(e)
            right1 = None
        try:
            right2 = self.query_one("#right2")
        except Exception as e:
            self.printException(e)
            right2 = None
        try:
            right3 = self.query_one("#right3")
        except Exception as e:
            self.printException(e)
            right3 = None
        # Ensure the main horizontal fills remaining space so the title remains visible
        try:
            # ensure root fills the app and main flexes so footer remains visible
            root = self.query_one("#root")
            root.styles.height = "100%"
            root.styles.flex = 1
        except Exception as e:
            self.printException(e)
            pass
        try:
            main = self.query_one("#main")
            main.styles.flex = 1
            # do not force 100% height here; allow footer to occupy its line
            main.styles.height = None
        except Exception as e:
            self.printException(e)
            pass
        # build repository cache (pygit2-based) before populating file list
        try:
            self.build_repo_cache()
        except Exception as e:
            self.printException(e)
            pass
        # Start with Files column full-width, other columns hidden
        try:
            left.styles.width = "100%"
            left.styles.flex = 0
        except Exception as e:
            self.printException(e)
            try:
                left.styles.flex = 1
            except Exception as e:
                self.printException(e)
                pass

        try:
            right1.styles.display = "none"
        except Exception as e:
            self.printException(e)
            pass
        try:
            right2.styles.display = "none"
        except Exception as e:
            self.printException(e)
            pass
        try:
            right3.styles.display = "none"
        except Exception as e:
            self.printException(e)
            pass

        # Populate the Files column only when present and not in log-first startup
        try:
            if left is not None and not getattr(self, "log_first", False):
                if hasattr(left, "set_path") and callable(getattr(left, "set_path")):
                    left.set_path(self.path)
        except Exception as e:
            self.printException(e)
            pass

        # If started in log-first (repo-first) mode, populate repo-wide history
        try:
            if getattr(self, "log_first", False):
                try:
                    self._open_repo_history()
                except Exception as e:
                    self.printException(e)
                    pass
        except Exception as e:
            self.printException(e)
            pass
        # If launched with a filename, populate and focus its history immediately
        try:
            if getattr(self, "initial_file", None):
                try:
                    self._open_history_for_file(self.initial_file)
                except Exception as e:
                    self.printException(e)
                    pass
        except Exception as e:
            self.printException(e)
            pass

    def _open_history_for_file(self, item_name: str) -> None:
        """Populate the History column for `item_name` and focus it.

        Mirrors the behavior used when pressing Right on a file in the Files column.
        """
        try:
            proc = subprocess.run(
                [
                    "git",
                    "log",
                    "--follow",
                    "--date=short",
                    "--pretty=format:%ad %h %s",
                    "--",
                    item_name,
                ],
                cwd=self.path,
                capture_output=True,
                text=True,
            )
            out = proc.stdout.strip()

            hist = self.query_one("#right1", ListView)
            try:
                hist.clear()
            except Exception as e:
                self.printException(e)
                pass

            try:
                hist._filename = item_name
            except Exception as e:
                self.printException(e)
                pass

            if out:
                for line in out.splitlines():
                    li = ListItem(Label(Text(" " + line)))
                    try:
                        m = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", line)
                        if m:
                            li._hash = m.group(2)
                    except Exception as e:
                        self.printException(e)
                        pass
                    try:
                        li._raw_text = line
                    except Exception as e:
                        self.printException(e)
                        pass
                    hist.append(li)
            else:
                hist.append(ListItem(Label(Text(" " + f"No git history for {item_name}"))))

            # Make History column visible and try to apply focus/layout
            try:
                hist.styles.display = None
            except Exception as e:
                self.printException(e)
                pass
            # Highlight the file in the Files column after the DOM refresh
            try:
                try:
                    left = self.query_one("#left")
                    if not isinstance(left, FileModeFileList):
                        try:
                            left = self.query_one("#right1")
                        except Exception as e:
                            self.printException(e)
                            left = None
                except Exception as e:
                    self.printException(e)
                    try:
                        left = self.query_one("#right1")
                    except Exception as e:
                        self.printException(e)
                        left = None
                try:
                    left.call_after_refresh(left._highlight_filename, item_name)
                except Exception as e:
                    self.printException(e)
                    try:
                        left.index = getattr(left, "_min_index", 0) or 0
                    except Exception as e:
                        self.printException(e)
                        pass
            except Exception as e:
                self.printException(e)
                pass
            try:
                self.query_one("#left-column").styles.width = "25%"
                self.query_one("#left-column").styles.flex = 0
                self.query_one("#right1-column").styles.width = "75%"
                self.query_one("#right1-column").styles.flex = 0
            except Exception as e:
                self.printException(e)
                pass
            try:
                hist.index = 0
            except Exception as e:
                self.printException(e)
                pass
            try:
                hist.focus()
            except Exception as e:
                self.printException(e)
                pass
            # ensure we are not in diff-fullscreen when opening history
            try:
                self.diff_fullscreen = False
            except Exception as e:
                self.printException(e)
                pass
        except Exception as exc:
            try:
                self.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)
                pass

    def _open_repo_history(self) -> None:
        """Populate the History column with repository-wide commits and focus it."""
        try:
            # If we started in log-first mode, the left column already hosts
            # a `RepoModeHistoryList`; populate it rather than mounting a new widget.
            if getattr(self, "log_first", False):
                try:
                    hist = self.query_one("#left")
                except Exception as e:
                    self.printException(e)
                    hist = None

                if hist is not None and hasattr(hist, "populate"):
                    try:
                        hist.populate(self.path)
                    except Exception as e:
                        self.printException(e)
                        pass

                    try:
                        # Make left column full-width and hide others
                        self.query_one("#left-column").styles.width = "100%"
                        self.query_one("#left-column").styles.flex = 0
                        self.query_one("#right1-column").styles.width = "0%"
                        self.query_one("#right1-column").styles.flex = 0
                        self.query_one("#right2-column").styles.width = "0%"
                        self.query_one("#right2-column").styles.flex = 0
                        self.query_one("#right3-column").styles.width = "0%"
                        self.query_one("#right3-column").styles.flex = 0
                    except Exception as e:
                        self.printException(e)
                        pass

                    try:
                        hist.index = 0
                    except Exception as e:
                        self.printException(e)
                        pass
                    try:
                        hist.focus()
                    except Exception as e:
                        self.printException(e)
                        pass
                    try:
                        self.diff_fullscreen = False
                    except Exception as e:
                        self.printException(e)
                        pass
                    return

            # Replace the existing History widget with a dedicated RepoModeHistoryList
            try:
                parent = self.query_one("#right1-column")
                try:
                    old = self.query_one("#right1")
                    try:
                        old.remove()
                    except Exception as e:
                        self.printException(e)
                        pass
                except Exception as e:
                    self.printException(e)
                    old = None

                # Mount a repository-backed history list with the same id
                try:
                    repo_hist = RepoModeHistoryList(id="right1")
                    parent.mount(repo_hist)
                except Exception as e:
                    self.printException(e)
                    repo_hist = None

                if repo_hist:
                    try:
                        repo_hist.populate(self.path)
                    except Exception as e:
                        self.printException(e)

                    # Adjust layout: hide files and other right columns, show history full-width
                    try:
                        self.query_one("#left-column").styles.width = "0%"
                        self.query_one("#left-column").styles.flex = 0
                        self.query_one("#right1-column").styles.width = "100%"
                        self.query_one("#right1-column").styles.flex = 0
                        self.query_one("#right2-column").styles.width = "0%"
                        self.query_one("#right2-column").styles.flex = 0
                        self.query_one("#right3-column").styles.width = "0%"
                        self.query_one("#right3-column").styles.flex = 0
                    except Exception as e:
                        self.printException(e)
                        pass

                    try:
                        repo_hist.index = 0
                    except Exception as e:
                        self.printException(e)
                        pass
                    try:
                        repo_hist.focus()
                    except Exception as e:
                        self.printException(e)
                        pass
                    try:
                        self.diff_fullscreen = False
                    except Exception as e:
                        self.printException(e)
                        pass
                    return
            except Exception as e:
                self.printException(e)
                logger.debug("_open_repo_history: failed to replace History widget; falling back")

            # Fallback to subprocess-based behavior if replacement fails
            try:
                proc = subprocess.run(
                    ["git", "log", "--date=short", "--pretty=format:%ad %h %s"],
                    cwd=self.path,
                    capture_output=True,
                    text=True,
                )
                out = proc.stdout.strip()
            except Exception as e:
                self.printException(e)
                out = ""

            try:
                hist = self.query_one("#right1", ListView)
            except Exception as e:
                self.printException(e)
                hist = None

            if hist:
                try:
                    hist.clear()
                except Exception as e:
                    self.printException(e)
                    pass
                try:
                    hist._filename = None
                except Exception as e:
                    self.printException(e)
                    pass

                if out:
                    for line in out.splitlines():
                        li = ListItem(Label(Text(" " + line)))
                        try:
                            m = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", line)
                            if m:
                                li._hash = m.group(2)
                        except Exception as e:
                            self.printException(e)
                            pass
                        try:
                            li._raw_text = line
                        except Exception as e:
                            self.printException(e)
                            pass
                        try:
                            hist.append(li)
                        except Exception as e:
                            self.printException(e)
                            pass
                else:
                    try:
                        hist.append(ListItem(Label(Text(" " + "No git history for repository"))))
                    except Exception as e:
                        self.printException(e)
                        pass

                try:
                    hist.styles.display = None
                except Exception as e:
                    self.printException(e)
                    pass

                try:
                    hist.index = 0
                except Exception as e:
                    self.printException(e)
                    pass
                try:
                    hist.focus()
                except Exception as e:
                    self.printException(e)
                    pass

            return
        except Exception as exc:
            try:
                self.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)
                pass

    def on_key(self, event: events.Key) -> None:
        """Global key handler.

        - Block the Ctrl+P palette shortcut.
        - Accept uppercase `Q` as a quit key in addition to lowercase `q`.
        """
        logger.debug(f"GitHistoryTool.on_key: key={event.key}")
        try:
            key = event.key
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
                    pass
                try:
                    self.action_quit()
                except Exception as e:
                    self.printException(e)
                    try:
                        self.exit()
                    except Exception as e:
                        self.printException(e)
                        pass
                return
            # Help: show help column on h / H / ?
            if key in ("h", "H", "?", "question_mark"):
                logger.debug(f"Help key detected: {key}")
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e)
                    pass
                try:
                    # Save current column state (save actual values, not strings)
                    left_col = self.query_one("#left-column")
                    right1_col = self.query_one("#right1-column")
                    right2_col = self.query_one("#right2-column")
                    right1_widget = self.query_one("#right1")
                    right2_widget = self.query_one("#right2")

                    self.saved_column_state = {
                        "left": {
                            "width": left_col.styles.width,
                            "flex": left_col.styles.flex,
                        },
                        "right1": {
                            "width": right1_col.styles.width,
                            "flex": right1_col.styles.flex,
                            "display": right1_widget.styles.display,
                        },
                        "right2": {
                            "width": right2_col.styles.width,
                            "flex": right2_col.styles.flex,
                            "display": right2_widget.styles.display,
                        },
                    }
                    logger.debug(f"Saved column state: {self.saved_column_state}")

                    # Show only the help column, hide others
                    self.query_one("#left-column").styles.width = "0%"
                    self.query_one("#left-column").styles.flex = 0
                    self.query_one("#right1-column").styles.width = "0%"
                    self.query_one("#right1-column").styles.flex = 0
                    self.query_one("#right2-column").styles.width = "0%"
                    self.query_one("#right2-column").styles.flex = 0
                    self.query_one("#right3-column").styles.width = "100%"
                    self.query_one("#right3-column").styles.flex = 0
                    self.query_one("#right3").styles.display = "block"
                    self.query_one("#right3").focus()
                    # Update footer
                    footer = self.query_one("#footer", Label)
                    footer.update(Text("q(uit)  ↑ ↓  Press any key to return", style="bold"))
                except Exception as e:
                    self.printException(e)
                    pass
                return

        except Exception as e:
            self.printException(e)
            pass

    def enter_diff_fullscreen(self) -> None:
        """Make the Diff column full-screen (hide other columns) and update footer."""
        try:
            # save whether we were fullscreen already
            if getattr(self, "diff_fullscreen", False):
                return
            # collapse left and history columns
            try:
                self.query_one("#left-column").styles.width = "0%"
                self.query_one("#left-column").styles.flex = 0
            except Exception as e:
                self.printException(e, "could not collapse left-column")
            try:
                self.query_one("#right1-column").styles.width = "0%"
                self.query_one("#right1-column").styles.flex = 0
                self.query_one("#right1").styles.display = "none"
            except Exception as e:
                self.printException(e, "could not collapse right1-column")
            try:
                self.query_one("#right2-column").styles.width = "100%"
                self.query_one("#right2-column").styles.flex = 0
                self.query_one("#right2").styles.display = None
            except Exception as e:
                self.printException(e, "could not expand right2-column")
            # mark state and update footer
            try:
                self.diff_fullscreen = True
            except Exception as e:
                self.printException(e, "could not set diff_fullscreen flag")
                pass
            try:
                footer = self.query_one("#footer", Label)
                footer.update(Text("q(uit)  ?/h(elp)  ↑ ↓   ←/f(ull)", style="bold"))
            except Exception as e:
                self.printException(e, "could not update footer")
        except Exception as e:
            self.printException(e)

    def exit_diff_fullscreen(self) -> None:
        """Restore the standard three-column layout (columnated mode)."""
        try:
            if not getattr(self, "diff_fullscreen", False):
                return
            # restore a sensible columnated layout
            try:
                self.query_one("#left-column").styles.width = "5%"
                self.query_one("#left-column").styles.flex = 0
            except Exception as e:
                self.printException(e, "could not restore left-column")
            try:
                self.query_one("#right1-column").styles.width = "15%"
                self.query_one("#right1-column").styles.flex = 0
                self.query_one("#right1").styles.display = None
            except Exception as e:
                self.printException(e, "could not restore right1-column")
            try:
                self.query_one("#right2-column").styles.width = "80%"
                self.query_one("#right2-column").styles.flex = 0
                self.query_one("#right2").styles.display = None
            except Exception as e:
                self.printException(e, "could not restore right2-column")
            try:
                self.diff_fullscreen = False
            except Exception as e:
                self.printException(e, "could not clear diff_fullscreen flag")
                pass
            try:
                footer = self.query_one("#footer", Label)
                footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓   PgUp/PgDn  c(olor)  →/f(ull)", style="bold"))
            except Exception as e:
                self.printException(e, "could not update footer")
        except Exception as e:
            self.printException(e)
            pass


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

    app = GitHistoryTool(args.path, colorize_diff=(not args.no_color), log_first=args.log_first)
    app.run()


if __name__ == "__main__":
    main()
