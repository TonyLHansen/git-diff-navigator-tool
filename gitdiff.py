#!/usr/bin/env python3
"""
Git Diff Navigator TUI
"""
from __future__ import annotations

import argparse
import datetime
import logging
import os
import re
import subprocess
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
# Uncomment the basicConfig line below to enable logging to /tmp/gitdiff_debug.log
DOLOGGING = True
if DOLOGGING:
    logging.basicConfig(
        filename='tmp/gitdiff_debug.log',
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
logger = logging.getLogger(__name__)


class FileList(ListView):
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
            logger.debug(f"FileList.set_path: exception setting displayed_path: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            entries = sorted(os.listdir(path))
        except Exception as exc:
            self.clear()
            self.append(ListItem(Label(Text(f"Error reading {path}: {exc}", style="red"))))
            return

        self.clear()
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
                            logger.debug(f"FileList.set_path: exception getting relpath: {e}")
                            logger.debug(traceback.format_exc())
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
                                logger.debug(f"FileList.set_path: exception processing pygit2 flags: {e}")
                                logger.debug(traceback.format_exc())
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
                    logger.debug(f"FileList.set_path: exception getting repo status: {e}")
                    logger.debug(traceback.format_exc())
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
                    logger.debug(f"FileList.set_path: exception setting _repo_status: {e}")
                    logger.debug(traceback.format_exc())
                    pass
            # attach filename to the ListItem for reliable lookup later
            try:
                li._filename = name
            except Exception as e:
                logger.debug(f"FileList.set_path: exception setting _filename: {e}")
                logger.debug(traceback.format_exc())
                pass
            self.append(li)

        # After populating, ensure the top entry is highlighted.
        try:
            self.call_after_refresh(self._highlight_top)
        except Exception as e:
            logger.debug(f"FileList.set_path: exception calling _highlight_top: {e}")
            logger.debug(traceback.format_exc())
            try:
                self.index = 0
            except Exception as e:
                logger.debug(f"FileList.set_path: exception setting index to 0: {e}")
                logger.debug(traceback.format_exc())
                pass

    def on_focus(self, event: events.Focus) -> None:
        """When Files column receives focus, make it full-width and hide others."""
        try:
            # Make the left column (the whole left vertical) full width,
            # and collapse the two right columns so titles/later columns
            # don't reserve space.
            try:
                self.app.query_one("#left-column").styles.width = "100%"
                self.app.query_one("#left-column").styles.flex = 0
            except Exception as e:
                logger.debug(f"FileList.on_focus: exception setting left column: {e}")
                logger.debug(traceback.format_exc())
                pass
            try:
                self.app.query_one("#right1-column").styles.width = "0%"
                self.app.query_one("#right1-column").styles.flex = 0
            except Exception as e:
                logger.debug(f"FileList.on_focus: exception setting right1 column: {e}")
                logger.debug(traceback.format_exc())
                pass
            try:
                self.app.query_one("#right2-column").styles.width = "0%"
                self.app.query_one("#right2-column").styles.flex = 0
            except Exception as e:
                logger.debug(f"FileList.on_focus: exception setting right2 column: {e}")
                logger.debug(traceback.format_exc())
                pass
            # Ensure inner left list fills its column
            self.styles.width = "100%"
            self.styles.flex = 0
        except Exception as e:
            logger.debug(f"FileList.on_focus: exception setting column widths: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            right1 = self.app.query_one("#right1", HistoryList)
            right1.styles.display = "none"
        except Exception as e:
            logger.debug(f"FileList.on_focus: exception hiding right1: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            right2 = self.app.query_one("#right2", ListView)
            right2.styles.display = "none"
        except Exception as e:
            logger.debug(f"FileList.on_focus: exception hiding right2: {e}")
            logger.debug(traceback.format_exc())
            pass

        # show/hide titles for columns: left visible, others hidden
        try:
            lbl = self.app.query_one("#left-title", Label)
            lbl.update(Text("Files", style="bold"))  # Restore full name
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            logger.debug(f"FileList.on_focus: exception updating left title: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            lbl = self.query_one("#right1-title", Label)
            lbl.styles.display = "none"
            lbl.styles.height = 0
            lbl.styles.width = 0
        except Exception as e:
            logger.debug(f"FileList.on_focus: exception hiding right1 title: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            lbl = self.query_one("#right2-title", Label)
            lbl.styles.display = "none"
            lbl.styles.height = 0
            lbl.styles.width = 0
        except Exception as e:
            logger.debug(f"FileList.on_focus: exception hiding right2 title: {e}")
            logger.debug(traceback.format_exc())
            pass

        # FileList footer
        try:
            footer = self.app.query_one("#footer", Label)
            footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓ →", style="bold"))
        except Exception as e:
            logger.debug(f"FileList.on_focus: exception updating footer: {e}")
            logger.debug(traceback.format_exc())
            pass

    def on_key(self, event: events.Key) -> None:
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
            self.action_cursor_up()
        elif key == "down":
            event.stop()
            self.action_cursor_down()
        elif key == "right":
            event.stop()
            # If the highlighted entry is a directory (and not ".."), enter it.
            child = self.highlighted_child
            if child is None:
                return
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
                        logger.debug(f"FileList.on_key: exception showing modal fallback: {e}")
                        logger.debug(traceback.format_exc())
                        # Last-resort fallback
                        self.app.push_screen(_TBDModal())
                    return

            if item_name != "..":
                full = os.path.join(self.path, item_name)
                if os.path.isdir(full):
                    # switch the listing to the selected directory
                    self.set_path(full)
                    # ensure highlight resets to first item
                    try:
                        self.index = 0
                    except Exception as e:
                        logger.debug(f"FileList.on_key: exception resetting index: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                    # update app-level current path as well
                    try:
                        self.app.path = os.path.abspath(full)
                    except Exception as e:
                        logger.debug(f"FileList.on_key: exception updating app.path: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                    # focus back on this list
                    self.focus()
                    return

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
                            logger.debug(f"FileList.on_key: exception clearing history: {e}")
                            logger.debug(traceback.format_exc())
                            pass

                        # remember which file this history is for
                        try:
                            hist._filename = item_name
                        except Exception as e:
                            logger.debug(f"FileList.on_key: exception setting hist._filename: {e}")
                            logger.debug(traceback.format_exc())
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
                                        logger.debug(f"FileList.on_key: exception getting relpath for pseudo entries: {e}")
                                        logger.debug(traceback.format_exc())
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
                                logger.debug(f"FileList.on_key: exception building pseudo entries: {e}")
                                logger.debug(traceback.format_exc())
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
                                                logger.debug(f"FileList.on_key: exception getting relpath for STAGED: {e}")
                                                logger.debug(traceback.format_exc())
                                                rel = None
                                            mtime = None
                                            if rel:
                                                try:
                                                    mtime = app.repo_index_mtime_map.get(rel)
                                                except Exception as e:
                                                    logger.debug(f"FileList.on_key: exception getting index mtime: {e}")
                                                    logger.debug(traceback.format_exc())
                                                    mtime = None
                                            # fallback to index file mtime if per-file not available
                                            if not mtime:
                                                try:
                                                    index_path = os.path.join(app.repo_root, ".git", "index")
                                                    mtime = os.path.getmtime(index_path)
                                                except Exception as e:
                                                    logger.debug(f"FileList.on_key: exception getting index file mtime: {e}")
                                                    logger.debug(traceback.format_exc())
                                                    mtime = None
                                            if mtime:
                                                display_pseudo = f"{datetime.datetime.fromtimestamp(float(mtime)).strftime('%Y-%m-%d')} STAGED"
                                    except Exception as e:
                                        logger.debug(f"FileList.on_key: exception building STAGED display: {e}")
                                        logger.debug(traceback.format_exc())
                                        display_pseudo = "STAGED"
                                elif pseudo == "MODS":
                                    try:
                                        # use working-tree file mtime for MODS
                                        try:
                                            fp = os.path.join(self.path, item_name)
                                            mtime = os.path.getmtime(fp)
                                        except Exception as e:
                                            logger.debug(f"FileList.on_key: exception getting MODS file mtime: {e}")
                                            logger.debug(traceback.format_exc())
                                            mtime = None
                                        if mtime:
                                            display_pseudo = f"{datetime.datetime.fromtimestamp(float(mtime)).strftime('%Y-%m-%d')} MODS"
                                        else:
                                            display_pseudo = "MODS"
                                    except Exception as e:
                                        logger.debug(f"FileList.on_key: exception building MODS display: {e}")
                                        logger.debug(traceback.format_exc())
                                        display_pseudo = "MODS"

                                pli = ListItem(Label(Text(" " + display_pseudo)))
                                try:
                                    pli._hash = pseudo
                                except Exception as e:
                                    logger.debug(f"FileList.on_key: exception setting pli._hash: {e}")
                                    logger.debug(traceback.format_exc())
                                    pass
                                try:
                                    pli._raw_text = display_pseudo
                                except Exception as e:
                                    logger.debug(f"FileList.on_key: exception setting pli._raw_text: {e}")
                                    logger.debug(traceback.format_exc())
                                    pass
                                hist.append(pli)

                            for line in out.splitlines():
                                li = ListItem(Label(Text(" " + line)))
                                try:
                                    m = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", line)
                                    if m:
                                        li._hash = m.group(2)
                                except Exception as e:
                                    logger.debug(f"FileList.on_key: exception parsing hash from line: {e}")
                                    logger.debug(traceback.format_exc())
                                    pass
                                try:
                                    li._raw_text = line
                                except Exception as e:
                                    logger.debug(f"FileList.on_key: exception setting li._raw_text: {e}")
                                    logger.debug(traceback.format_exc())
                                    pass
                                hist.append(li)
                        else:
                            hist.append(ListItem(Label(Text(" " + f"No git history for {item_name}"))))

                        # highlight and focus the top entry
                        try:
                            hist.index = 0
                        except Exception as e:
                            logger.debug(f"FileList.on_key: exception setting hist.index: {e}")
                            logger.debug(traceback.format_exc())
                            pass
                        try:
                                hist.focus()
                        except Exception as e:
                            logger.debug(f"FileList.on_key: exception focusing history: {e}")
                            logger.debug(traceback.format_exc())
                            pass
                    except Exception as e:
                        logger.debug(f"FileList.on_key: exception updating history view: {e}")
                        logger.debug(traceback.format_exc())
                        # If unable to update, show modal with output or message
                        msg = out or f"No git history for {item_name}"
                        try:
                            self.app.push_screen(_TBDModal(msg))
                        except Exception as e:
                            logger.debug(f"FileList.on_key: exception showing history error modal: {e}")
                            logger.debug(traceback.format_exc())
                            pass
                except Exception as exc:
                    try:
                        self.app.push_screen(_TBDModal(str(exc)))
                    except Exception as e:
                        logger.debug(f"FileList.on_key: exception showing outer error modal: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                return

            # Not a directory we can enter — show TBD for now
            self.app.push_screen(_TBDModal())
        elif key == "left":
            event.stop()
            # If left pressed on the parent entry, go up a directory and
            # highlight the directory we came from.
            child = self.highlighted_child
            if child is None:
                return
            item_name = getattr(child, "_filename", None)
            if item_name is None:
                try:
                    label = child.query_one(Label)
                    item_name = label.text if hasattr(label, "text") else str(label)
                except Exception as exc:
                    try:
                        self.app.push_screen(_TBDModal(str(exc)))
                    except Exception as e:
                        logger.debug(f"FileList.on_key: exception showing left key error modal: {e}")
                        logger.debug(traceback.format_exc())
                        self.app.push_screen(_TBDModal())
                    return

            if item_name == "..":
                prev_basename = os.path.basename(self.path)
                parent = os.path.dirname(self.path)
                if parent == self.path or not parent:
                    # already at filesystem root
                    self.app.push_screen(_TBDModal("Already at root"))
                    return

                # change to parent directory
                self.set_path(parent)

                # After the DOM refresh, highlight the directory we came from.
                try:
                    self.call_after_refresh(self._highlight_filename, prev_basename)
                except Exception as e:
                    logger.debug(f"FileList.on_key: exception calling _highlight_filename: {e}")
                    logger.debug(traceback.format_exc())
                    try:
                        # Fallback: set to first item
                        self.index = 0
                    except Exception as e:
                        logger.debug(f"FileList.on_key: exception setting index fallback: {e}")
                        logger.debug(traceback.format_exc())
                        pass

                # update app-level path info
                try:
                    self.app.path = os.path.abspath(parent)
                    self.app.displayed_path = self.path
                except Exception as e:
                    logger.debug(f"FileList.on_key: exception updating app path info: {e}")
                    logger.debug(traceback.format_exc())
                    pass

                self.focus()
                return

            # Left on non-parent: ignore (do nothing)
            return
        else:
            # For keys we don't explicitly handle here, allow them to bubble
            # to higher-level handlers (e.g. app-level `on_key`) so global
            # shortcuts like `h` / `?` and `Q` are still processed.
            return

    def _highlight_filename(self, name: str) -> None:
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
                        logger.debug(f"FileList._highlight_filename: exception setting index: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                    return
            # not found: default to first
            try:
                self.index = 0
            except Exception as e:
                logger.debug(f"FileList._highlight_filename: exception setting index to 0: {e}")
                logger.debug(traceback.format_exc())
                pass
        except Exception as e:
            logger.debug(f"FileList._highlight_filename: exception in outer block: {e}")
            logger.debug(traceback.format_exc())
            return

    def _highlight_top(self) -> None:
        """Highlight the first entry in the list after a refresh."""
        try:
            # If there are nodes, set index to 0; otherwise leave unset.
            nodes = getattr(self, "_nodes", [])
            if nodes:
                self.index = 0
        except Exception as e:
            logger.debug(f"FileList._highlight_top: exception: {e}")
            logger.debug(traceback.format_exc())
            return



class HistoryList(ListView):
    """ListView used for the History column. Left arrow moves focus back to Files."""

    def toggle_check_current(self) -> None:
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
                        logger.debug(f"HistoryList.toggle_check_current._label_text_and_style: exception getting style: {e}")
                        logger.debug(traceback.format_exc())
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
                    logger.debug(f"HistoryList.toggle_check_current._label_text_and_style: exception: {e}")
                    logger.debug(traceback.format_exc())
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
                    logger.debug(f"HistoryList.toggle_check_current: exception unchecking target: {e}")
                    logger.debug(traceback.format_exc())
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
                    logger.debug(f"HistoryList.toggle_check_current: exception clearing previous check: {e}")
                    logger.debug(traceback.format_exc())
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
                logger.debug(f"HistoryList.toggle_check_current: exception setting check on target: {e}")
                logger.debug(traceback.format_exc())
                pass
        except Exception as e:
            logger.debug(f"HistoryList.toggle_check_current: exception in outer block: {e}")
            logger.debug(traceback.format_exc())
            pass


    def on_focus(self, event: events.Focus) -> None:
        """When the HistoryList receives focus, ensure the first item is highlighted."""
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
                        logger.debug(f"HistoryList._apply: clearing index: exception: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                    try:
                        self.index = target
                    except Exception as e:
                        logger.debug(f"HistoryList._apply: restoring index target: exception: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                except Exception as e:
                    logger.debug(f"HistoryList._apply: nodes processing: exception: {e}")
                    logger.debug(traceback.format_exc())
                    return

            try:
                self.call_after_refresh(_apply)
            except Exception as e:
                logger.debug(f"HistoryList.on_focus: call_after_refresh exception: {e}")
                logger.debug(traceback.format_exc())
                _apply()
        except Exception as e:
            logger.debug(f"HistoryList.on_focus: _apply setup: exception: {e}")
            logger.debug(traceback.format_exc())
            pass

        # When History receives focus, make Files/History split 50/50 and hide Diff
        try:
            left = self.app.query_one("#left", FileList)
            right2 = self.app.query_one("#right2", ListView)
            # set widths to 50/50
            try:
                # Adjust outer columns so left/right1 split the screen 25/75
                try:
                    self.app.query_one("#left-column").styles.width = "25%"
                    self.app.query_one("#left-column").styles.flex = 0
                except Exception as e:
                    logger.debug(f"HistoryList.on_focus: setting left-column width: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                try:
                    self.app.query_one("#right1-column").styles.width = "75%"
                    self.app.query_one("#right1-column").styles.flex = 0
                except Exception as e:
                    logger.debug(f"HistoryList.on_focus: setting right1-column width: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                # inner lists should fill their outer column
                left.styles.width = "100%"
                left.styles.flex = 0
            except Exception as e:
                logger.debug(f"HistoryList.on_focus: setting left list styles: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            try:
                self.styles.width = "100%"
                self.styles.display = None
                self.styles.flex = 0
            except Exception as e:
                logger.debug(f"HistoryList.on_focus: setting history styles: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            # hide diff list and shrink its outer column to zero so the
            # title doesn't consume space
            try:
                right2.styles.display = "none"
            except Exception as e:
                logger.debug(f"HistoryList.on_focus: hiding diff list: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            try:
                self.app.query_one("#right2-column").styles.width = "0%"
                self.app.query_one("#right2-column").styles.flex = 0
            except Exception as e:
                logger.debug(f"HistoryList.on_focus: setting right2-column width: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
        except Exception as e:
            logger.debug(f"HistoryList.on_focus: layout adjustment: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        # Titles: show left and history, hide diff
        try:
            lbl = self.app.query_one("#left-title", Label)
            lbl.update(Text("Files", style="bold"))  # Restore full name
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            logger.debug(f"HistoryList.on_focus: updating left-title label: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            lbl = self.app.query_one("#right1-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            logger.debug(f"HistoryList.on_focus: updating right1-title label: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            lbl = self.app.query_one("#right2-title", Label)
            lbl.styles.display = "none"
            lbl.styles.height = 0
            lbl.styles.width = 0
        except Exception as e:
            logger.debug(f"HistoryList.on_focus: updating right2 title: exception: {e}")
            logger.debug(traceback.format_exc())
            pass

        # HistoryList footer
        try:
            footer = self.app.query_one("#footer", Label)
            footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓ →   m(ark)", style="bold"))
        except Exception as e:
            logger.debug(f"HistoryList.on_focus: updating footer: exception: {e}")
            logger.debug(traceback.format_exc())
            pass

    def on_key(self, event: events.Key) -> None:
        """Handle left/right keys to move between columns or show diffs.

        Left moves focus back to the Files column. Right computes hashes for
        the selected history entry pair and populates the Diff column.
        """
        key = event.key
        logger.debug(f"HistoryList.on_key: key={key}")
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
                logger.debug(f"HistoryList.on_key: toggle check: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            return
        if key == "left":
            event.stop()
            try:
                files = self.app.query_one("#left", FileList)
                files.focus()
            except Exception as e:
                logger.debug(f"HistoryList.on_key: focusing files on left: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            return
        if key == "right":
            event.stop()
            # need at least one other item to diff against (either checked or next)
            idx = getattr(self, "index", None)
            nodes = getattr(self, "_nodes", [])
            if idx is None or idx < 0 or not nodes:
                try:
                    self.app.push_screen(_TBDModal("No commit to diff with"))
                except Exception as e:
                    logger.debug(f"HistoryList.on_key: showing no commit modal: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                return

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
                    logger.debug(f"HistoryList.on_key._text_of: extracting text: exception: {e}")
                    logger.debug(traceback.format_exc())
                    return str(node)

            # Determine the pair of indices to diff: default is current vs next
            if checked_idx is None or checked_idx == idx:
                # behave as before: need a next item
                if idx >= len(nodes) - 1:
                    try:
                        self.app.push_screen(_TBDModal("No earlier commit to diff with"))
                    except Exception as e:
                        logger.debug(f"HistoryList.on_key: showing no earlier commit modal: exception: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                    return
                i_newer = idx
                i_older = idx + 1
            else:
                # If there is a checked item and it's not the current one,
                # diff between the current item and the checked item.
                # Order: lower item in the list (larger index) is prev, higher (smaller index) is curr.
                i1 = idx
                i2 = checked_idx
                if i1 == i2:
                    return
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
                        raise ValueError(
                            f"Lines not in expected format:\n{current_line!r}\n{previous_line!r}"
                        )
                    current_hash = m1.group(2)
                    previous_hash = m2.group(2)
                except Exception as exc:
                    try:
                        self.app.push_screen(_TBDModal(f"Could not parse hashes: {exc}"))
                    except Exception as e:
                        logger.debug(f"HistoryList.on_key: showing hash parse error modal: exception: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                    return

            # determine filename for the history (attached when populated)
            filename = getattr(self, "_filename", None)
            if not filename:
                try:
                    self.app.push_screen(_TBDModal("Unknown filename for history"))
                except Exception as e:
                    logger.debug(f"HistoryList.on_key: showing unknown filename modal: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                return

            # run git diff for a variety of hash/pseudo-hash combinations
            def _is_pseudo(h: str | None) -> bool:
                return h in ("STAGED", "MODS")

            def _build_diff_cmd(prev: str | None, curr: str | None, fname: str) -> list[str]:
                # prev = older, curr = newer
                # MODS = working tree; STAGED = index
                try:
                    if _is_pseudo(prev) or _is_pseudo(curr):
                        # working vs staged
                        if (prev == "STAGED" and curr == "MODS") or (prev == "MODS" and curr == "STAGED"):
                            return ["git", "diff", "--", fname]

                        # working vs commit: use `git diff <commit> -- <file>`
                        if curr == "MODS" and prev and not _is_pseudo(prev):
                            return ["git", "diff", prev, "--", fname]
                        if prev == "MODS" and curr and not _is_pseudo(curr):
                            return ["git", "diff", curr, "--", fname]

                        # staged vs commit: use --cached <commit>
                        if curr == "STAGED" and prev and not _is_pseudo(prev):
                            return ["git", "diff", "--cached", prev, "--", fname]
                        if prev == "STAGED" and curr and not _is_pseudo(curr):
                            return ["git", "diff", "--cached", curr, "--", fname]

                        # fallback: if one side is STAGED or MODS with no commit on the other
                        if curr == "STAGED" and prev is None:
                            return ["git", "diff", "--cached", "--", fname]
                        if curr == "MODS" and prev is None:
                            return ["git", "diff", "--", fname]

                    # default: two real commits/hashes
                    if prev and curr:
                        return ["git", "diff", prev, curr, "--", fname]
                except Exception as e:
                    logger.debug(f"HistoryList.on_key._build_diff_cmd: building command: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                # ultimate fallback: show working-tree diff
                return ["git", "diff", "--", fname]

            # Store current diff info for potential re-render
            self.app.current_commit_sha = current_hash
            self.app.current_prev_sha = previous_hash
            self.app.current_diff_file = filename

            try:
                cmd = _build_diff_cmd(previous_hash, current_hash, filename)
                proc = subprocess.run(cmd, cwd=self.app.path, capture_output=True, text=True)
                diff_out = proc.stdout or proc.stderr or ""
            except Exception as exc:
                try:
                    self.app.push_screen(_TBDModal(str(exc)))
                except Exception as e:
                    logger.debug(f"HistoryList.on_key: showing subprocess error modal: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                return

            # show the Diff column and populate it
            try:
                diff_view = self.app.query_one("#right2", ListView)
                try:
                    diff_view.clear()
                except Exception as e:
                    logger.debug(f"HistoryList.on_key: clearing diff view: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass

                # Header indicating which two hashes are being compared
                try:
                    header = ListItem(Label(Text(f"Comparing: {previous_hash}..{current_hash}", style="bold")))
                    diff_view.append(header)
                except Exception as e:
                    logger.debug(f"HistoryList.on_key: appending diff header: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass

                if diff_out:
                    for line in diff_out.splitlines():
                        # Colorize diff lines like git does (if enabled)
                        if self.app.colorize_diff:
                            if line.startswith('+++') or line.startswith('---'):
                                # File headers in bold white
                                styled_text = Text(line, style="bold white")
                            elif line.startswith('+'):
                                # Additions in green
                                styled_text = Text(line, style="green")
                            elif line.startswith('-'):
                                # Deletions in red
                                styled_text = Text(line, style="red")
                            elif line.startswith('@@'):
                                # Hunk headers in cyan
                                styled_text = Text(line, style="cyan")
                            elif line.startswith('diff --git') or line.startswith('index '):
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
                    diff_view.styles.display = None
                except Exception as e:
                    logger.debug(f"HistoryList.on_key: making diff column visible: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass

                try:
                    diff_view.index = 0
                except Exception as e:
                    logger.debug(f"HistoryList.on_key: setting diff view index: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                try:
                    diff_view.focus()
                except Exception as e:
                    logger.debug(f"HistoryList.on_key: focusing diff view: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
            except Exception as exc:
                try:
                    self.app.push_screen(_TBDModal(str(exc)))
                except Exception as e:
                    logger.debug(f"HistoryList.on_key: showing diff error modal: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
            return

        # Other keys: let default handling run by not stopping the event.
        return


class DiffList(ListView):
    """ListView used for the Diff column. Left arrow moves focus back to History."""

    def on_key(self, event: events.Key) -> None:
        """Handle left key to move focus back to History; handle PgUp/PgDn with visible selection; handle c/C to toggle colorization."""
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
                    except Exception:
                        pass
                else:
                    # Only enter fullscreen when diff is visible (columnated)
                    try:
                        # require right2 to be visible
                        if self.app.query_one("#right2", ListView).styles.display != "none":
                            self.app.enter_diff_fullscreen()
                            try:
                                self.focus()
                            except Exception:
                                pass
                    except Exception:
                        # best-effort: enter anyway
                        try:
                            self.app.enter_diff_fullscreen()
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"DiffList: exception toggling fullscreen f/F: {e}")
                logger.debug(traceback.format_exc())
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
                logger.debug(f"DiffList: current_commit_sha={getattr(self.app, 'current_commit_sha', None)}, current_prev_sha={getattr(self.app, 'current_prev_sha', None)}, current_diff_file={getattr(self.app, 'current_diff_file', None)}")
                if (getattr(self.app, 'current_commit_sha', None) and 
                    getattr(self.app, 'current_prev_sha', None) and 
                    getattr(self.app, 'current_diff_file', None)):
                    
                    logger.debug("DiffList: re-rendering diff with new colorization")
                    # Save current scroll position and selection
                    saved_scroll_y = self.scroll_y
                    saved_index = self.index
                    
                    # Directly re-run the diff command
                    previous_hash = self.app.current_prev_sha
                    current_hash = self.app.current_commit_sha
                    filename = self.app.current_diff_file
                    
                    # Same diff command building logic
                    def _build_diff_cmd(prev, curr, fname):
                        try:
                            if prev == "STAGED" and curr == "MODS":
                                return ["git", "diff", "HEAD", "--", fname]
                            if curr == "MODS" and prev is not None:
                                return ["git", "diff", prev, "--", fname]
                            if curr == "STAGED" and prev is not None:
                                return ["git", "diff", "--cached", prev, "--", fname]
                            if curr == "STAGED" and prev is None:
                                return ["git", "diff", "--cached", "--", fname]
                            if curr == "MODS" and prev is None:
                                return ["git", "diff", "--", fname]
                        except Exception as e:
                            logger.debug(f"[DiffList._build_diff_cmd]: exception: {e}")
                            logger.debug(traceback.format_exc())
                            pass
                        if prev and curr:
                            return ["git", "diff", prev, curr, "--", fname]
                        return ["git", "diff", "--", fname]
                    
                    cmd = _build_diff_cmd(previous_hash, current_hash, filename)
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
                                if line.startswith('+++') or line.startswith('---'):
                                    styled_text = Text(line, style="bold white")
                                elif line.startswith('+'):
                                    styled_text = Text(line, style="green")
                                elif line.startswith('-'):
                                    styled_text = Text(line, style="red")
                                elif line.startswith('@@'):
                                    styled_text = Text(line, style="cyan")
                                elif line.startswith('diff --git') or line.startswith('index '):
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
                            logger.debug(f"[DiffList.restore_state]: exception: {e}")
                            logger.debug(traceback.format_exc())
                            pass
                    
                    self.call_after_refresh(restore_state)
                    logger.debug("DiffList: diff re-rendered successfully")
                else:
                    logger.debug("DiffList: no current diff info available")
                        
            except Exception as e:
                logger.debug(f"DiffList: exception in c/C handler: {e}")
                logger.debug(traceback.format_exc())
            return
        
        if key and key.lower() == "q":
            try:
                event.key = key.lower()
            except Exception as e:
                logger.debug(f"DiffList: exception in q handler: {e}")
                logger.debug(traceback.format_exc())
            return
        if key == "left":
            event.stop()
            try:
                # If we're in fullscreen, left arrow exits fullscreen
                if getattr(self.app, "diff_fullscreen", False):
                    try:
                        self.app.exit_diff_fullscreen()
                    except Exception as e:
                        logger.debug(f"DiffList: exception exiting fullscreen on left: {e}")
                        logger.debug(traceback.format_exc())
                    return
                # otherwise move focus back to History
                hist = self.app.query_one("#right1", HistoryList)
                hist.focus()
            except Exception as e:
                logger.debug(f"DiffList: exception in left arrow handler: {e}")
                logger.debug(traceback.format_exc())
            return

        if key == "right":
            # In columnated mode, pressing right expands Diff to fullscreen.
            try:
                event.stop()
                if getattr(self.app, "diff_fullscreen", False):
                    # already fullscreen; right arrow does nothing
                    return
                # If diff is visible and not fullscreen, enter fullscreen
                try:
                    right1_display = self.app.query_one("#right1").styles.display
                    right2_display = self.app.query_one("#right2").styles.display
                    if right1_display != "none" and right2_display != "none":
                        self.app.enter_diff_fullscreen()
                        try:
                            self.focus()
                        except Exception:
                            pass
                        return
                except Exception:
                    # best-effort enter
                    try:
                        self.app.enter_diff_fullscreen()
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"DiffList: exception handling right key: {e}")
                logger.debug(traceback.format_exc())
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
                
                logger.debug(f"DiffList: {key} - current_index={current_index}, page_size={page_size}, visible_height={visible_height}, nodes={len(nodes)}")
                
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

                        logger.debug(f"DiffList: {key} - before scroll: scroll_y={self.scroll_y}, setting to {target_scroll}, max_scroll={max_scroll}")
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
                                            logger.debug(f"DiffList: exception managing highlight classes after animate: {e}")
                                            logger.debug(traceback.format_exc())
                                    except Exception as e:
                                        logger.debug(f"DiffList: exception finalizing highlight after animate: {e}")
                                        logger.debug(traceback.format_exc())
                                try:
                                    # schedule after animation completes
                                    self.set_timer(anim_duration + 0.02, _finalize_highlight)
                                except Exception:
                                    # fallback to call_after_refresh if set_timer not available
                                    self.call_after_refresh(_finalize_highlight)
                            except Exception:
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
                                        logger.debug(f"DiffList: exception managing highlight classes after instant scroll: {e}")
                                        logger.debug(traceback.format_exc())
                                except Exception as e:
                                    logger.debug(f"DiffList: exception setting index after instant scroll: {e}")
                                    logger.debug(traceback.format_exc())
                        except Exception as e:
                            logger.debug(f"DiffList: exception in scroll_to_position: {e}")
                            logger.debug(traceback.format_exc())
                    except Exception as e:
                        logger.debug(f"DiffList: exception in scroll_to_position: {e}")
                        logger.debug(traceback.format_exc())

                # Schedule the scroll -> highlight sequence
                self.call_after_refresh(scroll_to_position)
                
            except Exception as e:
                logger.debug(f"DiffList: exception in {key} handler: {e}")
                logger.debug(traceback.format_exc())
            return
        # let other keys be handled by default (up/down handled by ListView)

    def on_focus(self, event: events.Focus) -> None:
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
                        logger.debug(f"[DiffList._apply.set_index_none]: exception: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                    try:
                        self.index = target
                    except Exception as e:
                        logger.debug(f"[DiffList._apply.set_index_target]: exception: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                except Exception as e:
                    logger.debug(f"[DiffList._apply]: exception: {e}")
                    logger.debug(traceback.format_exc())
                    return

            try:
                self.call_after_refresh(_apply)
            except Exception as e:
                logger.debug(f"[DiffList.call_after_refresh]: exception: {e}")
                logger.debug(traceback.format_exc())
                _apply()
        except Exception as e:
            logger.debug(f"[DiffList.on_focus]: exception: {e}")
            logger.debug(traceback.format_exc())
            pass

        # When Diff receives focus, show all columns and set widths: Files 5%, History 20%, Diff 75%
        try:
            left = self.app.query_one("#left", FileList)
            hist = self.app.query_one("#right1", HistoryList)
            diff = self.app.query_one("#right2", ListView)
            try:
                # adjust outer columns to the target proportions
                try:
                    self.app.query_one("#left-column").styles.width = "5%"
                    self.app.query_one("#left-column").styles.flex = 0
                except Exception as e:
                    logger.debug(f"[DiffList.on_focus.set_left_column_width]: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                try:
                    self.app.query_one("#right1-column").styles.width = "15%"
                    self.app.query_one("#right1-column").styles.flex = 0
                except Exception as e:
                    logger.debug(f"[DiffList.on_focus.set_right1_column_width]: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                try:
                    self.app.query_one("#right2-column").styles.width = "80%"
                    self.app.query_one("#right2-column").styles.flex = 0
                except Exception as e:
                    logger.debug(f"[DiffList.on_focus.set_right2_column_width]: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                left.styles.width = "100%"
                left.styles.flex = 0
            except Exception as e:
                logger.debug(f"[DiffList.on_focus.set_left_styles]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            try:
                hist.styles.width = "100%"
                hist.styles.flex = 0
                hist.styles.display = None
            except Exception as e:
                logger.debug(f"[DiffList.on_focus.set_hist_styles]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            try:
                diff.styles.width = "100%"
                diff.styles.display = None
                diff.styles.flex = 0
            except Exception as e:
                logger.debug(f"[DiffList.on_focus.set_diff_styles]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
        except Exception as e:
            logger.debug(f"[DiffList.on_focus.set_widget_styles]: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        # Show all titles when diff active
        try:
            lbl = self.app.query_one("#left-title", Label)
            lbl.update(Text("Fi", style="bold"))  # Shorten to save space
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            logger.debug(f"[DiffList.on_focus.set_left_title]: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            lbl = self.app.query_one("#right1-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            logger.debug(f"[DiffList.on_focus.set_right1_title]: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            lbl = self.app.query_one("#right2-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            logger.debug(f"[DiffList.on_focus.set_right2_title]: exception: {e}")
            logger.debug(traceback.format_exc())
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
            logger.debug(f"[DiffList.on_focus.update_footer]: exception: {e}")
            logger.debug(traceback.format_exc())
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
Git History Navigator (gitdiff)
================================

Overview
--------
The Git History Navigator is a terminal Textual TUI that provides a three-column view for

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

Running
-------
Run the application as follows:

`gitdiff.py {path}`

`{path}` is optional — it defaults to the current working directory. If a filename is provided, the app will open its directory and populate the History column for that file on startup.
"""


class HelpList(ListView):
    """Help column showing usage and short docs.

    The contents are a plain listing derived from the README.
    """

    def on_mount(self) -> None:
        """Populate help content."""
        # Split help text into lines and add as list items
        lines = HELP_TEXT.split("\n")
        linelen = len(lines)
        for i, line in enumerate(lines):
            if i < linelen-1:
                if lines[i+1].startswith("=="):
                    lines[i] = "<title>" + lines[i].strip()
                    lines[i+1] = ""
                if lines[i+1].startswith("--"):
                    lines[i] = "<heading>" + lines[i].strip()
                    lines[i+1] = ""
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
        bullets = ["◉","○","♦","◊","—"]
        
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
                    logger.debug(f"Error focusing {focus_target}: {e}")
            
            self.app.call_after_refresh(restore_focus)
            
            # HelpList footer
            footer = self.app.query_one("#footer", Label)
            footer.update(Text("q(uit)  ?h/(elp)  ← ↑ ↓ →", style="bold"))
        except Exception as e:
            logger.debug(f"Error restoring state: {e}")


class GitHistoryTool(App):
    """Main Textual application providing the three-column git navigator.

    The app composes three columns: `Files`, `History`, and `Diff`. It builds a
    repository cache (using `pygit2`) and handles keyboard
    navigation and git operations to populate history and diffs.
    """
    TITLE = "Git History Navigator"
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

    def __init__(self, path: Optional[str] = None, **kwargs) -> None:
        """Initialize the app state.

        If `path` names a file, treat its directory as the working path and
        remember the filename to open its history on mount.
        """
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
        self.colorize_diff = True
        self.current_commit_sha: Optional[str] = None
        self.current_prev_sha: Optional[str] = None
        self.current_diff_file: Optional[str] = None
        # Diff fullscreen flag: when True, Diff column occupies 100% width
        self.diff_fullscreen: bool = False

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
                logger.debug(f"[GitDiffApp.build_repo_cache.index_set]: exception: {e}")
                logger.debug(traceback.format_exc())
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
                                logger.debug(f"[GitDiffApp.build_repo_cache.get_mtime]: exception: {e}")
                                logger.debug(traceback.format_exc())
                                mtime_val = None
                        if mtime_val:
                            mmap[entry.path] = float(mtime_val)
                    except Exception as e:
                        logger.debug(f"[GitDiffApp.build_repo_cache.process_entry_mtime]: exception: {e}")
                        logger.debug(traceback.format_exc())
                        continue
                self.repo_index_mtime_map = mmap
            except Exception as e:
                logger.debug(f"[GitDiffApp.build_repo_cache.mtime_map]: exception: {e}")
                logger.debug(traceback.format_exc())
                self.repo_index_mtime_map = {}

            # status: mapping path -> flags
            try:
                status_map = repo.status()
                # keys are paths relative to repo root
                self.repo_status_map = {k: int(v) for k, v in status_map.items()}
            except Exception as e:
                logger.debug(f"[GitDiffApp.build_repo_cache.status_map]: exception: {e}")
                logger.debug(traceback.format_exc())
                self.repo_status_map = {}

            self.repo_available = True
        except Exception as e:
            logger.debug(f"[GitDiffApp.build_repo_cache]: exception: {e}")
            logger.debug(traceback.format_exc())
            # leave as not available
            self.repo_available = False

    def compose(self) -> ComposeResult:
        """Compose the app UI: title, four-column layout, and footer hints."""
        with Vertical(id="root"):
            yield Label(Text(self.TITLE, style="bold"), id="title")
            with Horizontal(id="main"):
                # left column: flex so it takes remaining space
                with Vertical(id="left-column"):
                    yield Label(Text("Files", style="bold"), id="left-title")
                    yield FileList(id="left")
                # three minimal right columns
                with Vertical(id="right1-column"):
                    yield Label(Text("History", style="bold"), id="right1-title")
                    yield HistoryList(id="right1")
                with Vertical(id="right2-column"):
                    yield Label(Text("Diff", style="bold"), id="right2-title")
                    yield DiffList(id="right2")
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
        left = self.query_one("#left", FileList)
        right1 = self.query_one("#right1", HistoryList)
        right2 = self.query_one("#right2", ListView)
        right3 = self.query_one("#right3", HelpList)
        # Ensure the main horizontal fills remaining space so the title remains visible
        try:
            # ensure root fills the app and main flexes so footer remains visible
            root = self.query_one("#root")
            root.styles.height = "100%"
            root.styles.flex = 1
        except Exception as e:
            logger.debug(f"[GitDiffApp.on_mount.set_root_styles]: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            main = self.query_one("#main")
            main.styles.flex = 1
            # do not force 100% height here; allow footer to occupy its line
            main.styles.height = None
        except Exception as e:
            logger.debug(f"[GitDiffApp.on_mount.set_main_styles]: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        # build repository cache (pygit2-based) before populating file list
        try:
            self.build_repo_cache()
        except Exception as e:
            logger.debug(f"[GitDiffApp.on_mount.build_repo_cache]: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        # Start with Files column full-width, other columns hidden
        try:
            left.styles.width = "100%"
            left.styles.flex = 0
        except Exception as e:
            logger.debug(f"[GitDiffApp.on_mount.set_left_width]: exception: {e}")
            logger.debug(traceback.format_exc())
            try:
                left.styles.flex = 1
            except Exception as e:
                logger.debug(f"[GitDiffApp.on_mount.set_left_flex]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass

        try:
            right1.styles.display = "none"
        except Exception as e:
            logger.debug(f"[GitDiffApp.on_mount.hide_right1]: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            right2.styles.display = "none"
        except Exception as e:
            logger.debug(f"[GitDiffApp.on_mount.hide_right2]: exception: {e}")
            logger.debug(traceback.format_exc())
            pass
        try:
            right3.styles.display = "none"
        except Exception as e:
            logger.debug(f"[GitDiffApp.on_mount.hide_right3]: exception: {e}")
            logger.debug(traceback.format_exc())
            pass

        left.set_path(self.path)

        # If launched with a filename, populate and focus its history immediately
        try:
            if getattr(self, "initial_file", None):
                try:
                    self._open_history_for_file(self.initial_file)
                except Exception as e:
                    logger.debug(f"[GitDiffApp.on_mount.open_history]: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
        except Exception as e:
            logger.debug(f"[GitDiffApp.on_mount.check_initial_file]: exception: {e}")
            logger.debug(traceback.format_exc())
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
                logger.debug(f"[GitDiffApp._open_history_for_file.clear_hist]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass

            try:
                hist._filename = item_name
            except Exception as e:
                logger.debug(f"[GitDiffApp._open_history_for_file.set_filename]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass

            if out:
                for line in out.splitlines():
                    li = ListItem(Label(Text(" " + line)))
                    try:
                        m = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", line)
                        if m:
                            li._hash = m.group(2)
                    except Exception as e:
                        logger.debug(f"[GitDiffApp._open_history_for_file.parse_hash]: exception: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                    try:
                        li._raw_text = line
                    except Exception as e:
                        logger.debug(f"[GitDiffApp._open_history_for_file.set_raw_text]: exception: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                    hist.append(li)
            else:
                hist.append(ListItem(Label(Text(" " + f"No git history for {item_name}"))))

            # Make History column visible and try to apply focus/layout
            try:
                hist.styles.display = None
            except Exception as e:
                logger.debug(f"[GitDiffApp._open_history_for_file.show_hist]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            # Highlight the file in the Files column after the DOM refresh
            try:
                left = self.query_one("#left", FileList)
                try:
                    left.call_after_refresh(left._highlight_filename, item_name)
                except Exception as e:
                    logger.debug(f"[GitDiffApp._open_history_for_file.highlight_filename]: exception: {e}")
                    logger.debug(traceback.format_exc())
                    try:
                        left.index = 0
                    except Exception as e:
                        logger.debug(f"[GitDiffApp._open_history_for_file.set_left_index]: exception: {e}")
                        logger.debug(traceback.format_exc())
                        pass
            except Exception as e:
                logger.debug(f"[GitDiffApp._open_history_for_file.query_left]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            try:
                self.query_one("#left-column").styles.width = "25%"
                self.query_one("#left-column").styles.flex = 0
                self.query_one("#right1-column").styles.width = "75%"
                self.query_one("#right1-column").styles.flex = 0
            except Exception as e:
                logger.debug(f"[GitDiffApp._open_history_for_file.set_column_widths]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            try:
                hist.index = 0
            except Exception as e:
                logger.debug(f"[GitDiffApp._open_history_for_file.set_hist_index]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            try:
                hist.focus()
            except Exception as e:
                logger.debug(f"[GitDiffApp._open_history_for_file.focus_hist]: exception: {e}")
                logger.debug(traceback.format_exc())
                pass
            # ensure we are not in diff-fullscreen when opening history
            try:
                self.diff_fullscreen = False
            except Exception:
                pass
        except Exception as exc:
            try:
                self.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                logger.debug(f"[GitDiffApp._open_history_for_file.push_modal]: exception: {e}")
                logger.debug(traceback.format_exc())
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
                    logger.debug(f"[GitDiffApp.on_key.quit_event_stop]: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                try:
                    self.action_quit()
                except Exception as e:
                    logger.debug(f"[GitDiffApp.on_key.action_quit]: exception: {e}")
                    logger.debug(traceback.format_exc())
                    try:
                        self.exit()
                    except Exception as e:
                        logger.debug(f"[GitDiffApp.on_key.exit]: exception: {e}")
                        logger.debug(traceback.format_exc())
                        pass
                return
            # Help: show help column on h / H / ?
            if key in ("h", "H", "?", "question_mark"):
                logger.debug(f"Help key detected: {key}")
                try:
                    event.stop()
                except Exception as e:
                    logger.debug(f"[GitDiffApp.on_key.help_event_stop]: exception: {e}")
                    logger.debug(traceback.format_exc())
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
                    logger.debug(f"[GitDiffApp.on_key.show_help]: exception: {e}")
                    logger.debug(traceback.format_exc())
                    pass
                return

        except Exception as e:
            logger.debug(f"[GitDiffApp.on_key]: exception: {e}")
            logger.debug(traceback.format_exc())
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
            except Exception:
                logger.debug("enter_diff_fullscreen: could not collapse left-column")
            try:
                self.query_one("#right1-column").styles.width = "0%"
                self.query_one("#right1-column").styles.flex = 0
                self.query_one("#right1").styles.display = "none"
            except Exception:
                logger.debug("enter_diff_fullscreen: could not collapse right1-column")
            try:
                self.query_one("#right2-column").styles.width = "100%"
                self.query_one("#right2-column").styles.flex = 0
                self.query_one("#right2").styles.display = None
            except Exception:
                logger.debug("enter_diff_fullscreen: could not expand right2-column")
            # mark state and update footer
            try:
                self.diff_fullscreen = True
            except Exception:
                pass
            try:
                footer = self.query_one("#footer", Label)
                footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓   ←/f(ull)", style="bold"))
            except Exception:
                logger.debug("enter_diff_fullscreen: could not update footer")
        except Exception as e:
            logger.debug(f"enter_diff_fullscreen: exception: {e}")
            logger.debug(traceback.format_exc())

    def exit_diff_fullscreen(self) -> None:
        """Restore the standard three-column layout (columnated mode)."""
        try:
            if not getattr(self, "diff_fullscreen", False):
                return
            # restore a sensible columnated layout
            try:
                self.query_one("#left-column").styles.width = "5%"
                self.query_one("#left-column").styles.flex = 0
            except Exception:
                logger.debug("exit_diff_fullscreen: could not restore left-column")
            try:
                self.query_one("#right1-column").styles.width = "15%"
                self.query_one("#right1-column").styles.flex = 0
                self.query_one("#right1").styles.display = None
            except Exception:
                logger.debug("exit_diff_fullscreen: could not restore right1-column")
            try:
                self.query_one("#right2-column").styles.width = "80%"
                self.query_one("#right2-column").styles.flex = 0
                self.query_one("#right2").styles.display = None
            except Exception:
                logger.debug("exit_diff_fullscreen: could not restore right2-column")
            try:
                self.diff_fullscreen = False
            except Exception:
                pass
            try:
                footer = self.query_one("#footer", Label)
                footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓   PgUp/PgDn  c(olor)  →/f(ull)", style="bold"))
            except Exception:
                logger.debug("exit_diff_fullscreen: could not update footer")
        except Exception as e:
            logger.debug(f"exit_diff_fullscreen: exception: {e}")
            logger.debug(traceback.format_exc())
            pass


def main() -> None:
    """Entry point: parse CLI args and run the Textual app."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="Directory/file to list", default=os.getcwd())
    args = parser.parse_args()

    app = GitHistoryTool(args.path)
    app.run()


if __name__ == "__main__":
    main()
