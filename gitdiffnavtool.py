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

    def printException(self, e, msg=None): # AppBase
        """Print a message, the error information and a stacktrace"""
        className = type(self).__name__
        funcName = sys._getframe(1).f_code.co_name
        msg = msg if msg else "???"
        logger.warning(f"WARNING: {className}.{funcName} ({str(e)}): {msg}")
        logger.warning(traceback.format_exc())

    # Layout helpers: centralize column width/display management
    # pylint: disable=too-many-positional-arguments
    def _apply_column_layout( # AppBase
        self, left_w: str, right1_w: str, right2_w: str, left_display=None, right1_display=None, right2_display=None
    ) -> None:
        """Set outer column widths and optional widget display flags."""
        try:
            try:
                lc = self.query_one("#left-column")
                lc.styles.width = left_w
                lc.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set left-column")
            try:
                r1c = self.query_one("#right1-column")
                r1c.styles.width = right1_w
                r1c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set right1-column")
            try:
                r2c = self.query_one("#right2-column")
                r2c.styles.width = right2_w
                r2c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set right2-column")

            # Optionally set widget display styles
            try:
                if left_display is not None:
                    self.query_one("#left").styles.display = left_display
            except Exception as e:
                self.printException(e, "could not set left display style")

            try:
                if right1_display is not None:
                    self.query_one("#right1").styles.display = right1_display
            except Exception as e:
                self.printException(e, "could not set right1 display style")

            try:
                if right2_display is not None:
                    self.query_one("#right2").styles.display = right2_display
            except Exception as e:
                self.printException(e, "could not set right2 display style")

        except Exception as e:
            self.printException(e, "error applying column layout")

    def layout_left_only(self) -> None: # AppBase
        """Show only the left (History) column full-width."""
        self._apply_column_layout("100%", "0%", "0%", left_display=None, right1_display="none", right2_display="none")

    def layout_left_right_split(self) -> None: # AppBase
        """Show left/history and files split 25%/75%."""
        self._apply_column_layout("25%", "75%", "0%", left_display=None, right1_display=None, right2_display="none")

    def layout_three_columns(self) -> None: # AppBase
        """Show three-column layout 5%/15%/80%."""
        self._apply_column_layout("5%", "15%", "80%", left_display=None, right1_display=None, right2_display=None)

    def layout_diff_fullscreen(self) -> None: # AppBase
        """Make the diff column fullscreen (hide left and right1)."""
        self._apply_column_layout("0%", "0%", "100%", left_display="none", right1_display="none", right2_display=None)

    def text_of(self, node) -> str: # AppBase
        """Extract visible text from a ListItem node's Label/renderable.

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

    def on_key(self, event: events.Key) -> bool: # AppBase
        """Handle common navigation keys for ListView-based widgets.

        Returns True when the key was handled and should not be processed
        further by subclass handlers.
        """
        try:
            key = event.key
            logger.debug(f"AppBase.on_key: key={key}")

            # Normalize quit key to lowercase so app-level handler can see it
            if key and key.lower() == "q":
                try:
                    event.key = key.lower()
                except Exception:
                    pass
                return True

            if key == "up":
                try:
                    event.stop()
                except Exception:
                    pass
                try:
                    min_idx = getattr(self, "_min_index", 0) or 0
                except Exception:
                    min_idx = 0
                cur = getattr(self, "index", None)
                if cur is None:
                    try:
                        self.index = min_idx
                    except Exception as e:
                        self.printException(e)
                    return True
                try:
                    if cur <= min_idx:
                        return True
                except Exception as e:
                    self.printException(e)
                    return True

                try:
                    self.action_cursor_up()
                except Exception as e:
                    self.printException(e)
                return True

            if key == "down":
                try:
                    event.stop()
                except Exception:
                    pass
                try:
                    self.action_cursor_down()
                except Exception as e:
                    self.printException(e)
                return True

            # Handle PageUp/PageDown generically for ListView-based widgets
            if key in ("pageup", "pagedown"):
                try:
                    try:
                        event.stop()
                    except Exception:
                        pass
                    nodes = getattr(self, "_nodes", [])
                    if not nodes:
                        return True

                    current_index = self.index if self.index is not None else 0
                    visible_height = 0
                    try:
                        visible_height = int(getattr(self, "scrollable_content_region").height)
                    except Exception:
                        # fallback to a reasonable page size when not available
                        visible_height = 10
                    page_size = max(1, visible_height // 2)

                    if key == "pagedown":
                        new_index = min(current_index + page_size, len(nodes) - 1)
                    else:
                        new_index = max(current_index - page_size, 0)

                    try:
                        # schedule index change after refresh for stability
                        self.call_after_refresh(lambda: setattr(self, "index", new_index))
                    except Exception:
                        try:
                            self.index = new_index
                        except Exception as e:
                            self.printException(e, "setting index for page navigation")
                except Exception as e:
                    self.printException(e, "AppBase.page navigation failure")
                return True

            if key == "left":
                try:
                    event.stop()
                except Exception:
                    pass
                try:
                    if hasattr(self, "key_left"):
                        try:
                            self.key_left()
                        except Exception as e:
                            self.printException(e, "key_left exception")
                except Exception as e:
                    self.printException(e)
                return True

            if key == "right":
                try:
                    event.stop()
                except Exception:
                    pass
                try:
                    if hasattr(self, "key_right"):
                        try:
                            self.key_right()
                        except Exception as e:
                            self.printException(e, "key_right exception")
                except Exception as e:
                    self.printException(e)
                return True

            # Not handled here
            return False
        except Exception as e:
            self.printException(e, "AppBase.on_key outer failure")
            return False


    def more_keys(self, event: events.Key) -> bool:  # FileListBase
        """Per-mode file list key hook.
        Return True when the key was handled, False otherwise.
        """
        try:
            return False
        except Exception as e:
            self.printException(e)
            return False



class FileListBase(AppBase):
    """A ListView showing directory contents. Directories have a blue background.

    Navigation: arrow keys (up/down) move selection automatically because ListView
    handles keyboard navigation. The app focuses this widget on mount.
    """

    # NOTE: `set_path` intentionally removed from FileListBase. Subclasses
    # should implement `set_path` or an equivalent preparatory method such
    # as `prepFileModeFileList` so that different modes (file vs repo) can
    # populate lists with mode-specific behavior.

    def on_focus(self, event: events.Focus) -> None: # FileListBase
        """When Files column receives focus, make it full-width and hide others."""
        try:
            # Allow callers to temporarily suppress automatic layout changes
            if getattr(self.app, "_suppress_focus_layout", False):
                logger.debug(f"FileList.on_focus: suppressed layout change for {getattr(self,'id',None)}")
                return

            # Only apply layout/title changes when focus was caused by a
            # recent user navigation key. Programmatic focus calls should
            # not trigger layout mutations. The `GitHistoryTool.on_key`
            # stores the most recent user key in `self._last_user_key`.
            last_key = getattr(self.app, "_last_user_key", None)
            nav_keys = ("left", "right", "up", "down")
            apply_layout = last_key in nav_keys
            if apply_layout:
                fid = getattr(self, "id", "left")
                try:
                    if fid == "left":
                        try:
                            self.app.layout_left_only()
                        except Exception as e:
                            self.printException(e, "exception setting left column")

                        # Ensure inner left list fills its column
                        self.styles.width = "100%"
                        self.styles.flex = 0
                    else:
                        try:
                            self.app.layout_left_right_split()
                        except Exception as e:
                            self.printException(e, "exception setting left/right split for right1 focus")

                        # Ensure inner right1 list fills its column
                        self.styles.width = "100%"
                        self.styles.flex = 0
                finally:
                    # Clear the last user key so subsequent programmatic
                    # focus calls don't re-trigger layout.
                    try:
                        self.app._last_user_key = None
                    except Exception as e:
                        self.printException(e)

            # Hide other columns only if we applied layout above.
            try:
                if apply_layout:
                    try:
                        # Accept any widget at `#right1` (HistoryListBase or FileList).
                        # If this FileList instance *is* the widget at `#right1`, do
                        # not hide it — avoid toggling off the very list that just
                        # received focus (this was hiding appended items).
                        right1 = self.app.query_one("#right1")
                        if not getattr(self.app, "_suppress_focus_layout", False):
                            if right1 is not self:
                                right1.styles.display = "none"
                    except Exception as e:
                        self.printException(e, "exception hiding right1")

                    try:
                        right2 = self.app.query_one("#right2", ListView)
                        right2.styles.display = "none"
                    except Exception as e:
                        self.printException(e, "exception hiding right2")

                else:
                    # When not applying layout, do not mutate other columns.
                    pass
            except Exception as e:
                self.printException(e, "exception managing other columns on focus")

        except Exception as e:
            self.printException(e, "exception checking/enforcing _min_index on focus")

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
                except Exception as e:
                    self.printException(e, "exception scheduling index set after refresh")
                    try:
                        self.index = min_idx
                    except Exception as e2:
                        self.printException(e2, "exception enforcing min index on focus")

        except Exception as e:
            self.printException(e, "exception checking/enforcing _min_index on focus")

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
            self.printException(e, "exception updating left title")

        try:
            lbl = self.app.query_one("#right1-title", Label)
            lbl.update(Text(right1_text, style="bold"))
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e, "exception updating right1 title")

        try:
            lbl = self.app.query_one("#right2-title", Label)
            lbl.styles.display = "none"
            lbl.styles.height = 0
            lbl.styles.width = 0
        except Exception as e:
            self.printException(e, "exception hiding right2 title")

        # FileList footer
        try:
            footer = self.app.query_one("#footer", Label)
            footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓ →", style="bold"))
        except Exception as e:
            self.printException(e, "exception updating footer")

    
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
                        self.printException(e, "exception setting index")

                    return
            # not found: default to minimum selectable index (skip legend)
            try:
                self.index = getattr(self, "_min_index", 0) or 0
            except Exception as e:
                self.printException(e, "exception setting index to 0")

        except Exception as e:
            self.printException(e, "exception in outer block")
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
            self.printException(e, "exception")
            return


    def more_keys(self, event: events.Key) -> bool:  # FileListBase
        """Per-mode file list key hook.
        Return True when the key was handled, False otherwise.
        """
        try:
            return False
        except Exception as e:
            self.printException(e)
            return False




class FileModeFileList(FileListBase):
    """Compatibility subclass; use `FileListBase` for shared logic."""

    def prepFileModeFileList(self, path: str) -> None: # FileModeFileList
        """Prepare and populate this `FileModeFileList` for `path`.

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
                app = getattr(self, "app", None)
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
                key_text = Text(
                    "Key:  ' ' tracked  U untracked  M modified  A staged  D deleted  I ignored  ! conflicted",
                    style="bold white on blue",
                )
                self.append(ListItem(Label(key_text)))
                try:
                    self._min_index = 1
                except Exception as e:
                    self.printException(e)

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
                            app = getattr(self, "app", None)
                            if app and getattr(app, "repo_available", False) and app.repo_root:
                                try:
                                    rel = os.path.relpath(full, app.repo_root)
                                except Exception as e:
                                    self.printException(e, "exception getting relpath in prep")
                                    rel = None
                                if rel and not rel.startswith(".."):
                                    flags = app.repo_status_map.get(rel, 0)
                                    try:
                                        if flags & getattr(pygit2, "GIT_STATUS_CONFLICTED", 0):
                                            style = "magenta"
                                            repo_status = "conflicted"
                                        elif flags & (
                                            getattr(pygit2, "GIT_STATUS_INDEX_NEW", 0)
                                            | getattr(pygit2, "GIT_STATUS_INDEX_MODIFIED", 0)
                                            | getattr(pygit2, "GIT_STATUS_INDEX_DELETED", 0)
                                        ):
                                            style = "cyan"
                                            repo_status = "staged"
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
                    self.index = getattr(self, "_min_index", 0) or 0
                except Exception as e:
                    self.printException(e)

        except Exception as e:
            self.printException(e)

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

            # change to parent directory
            try:
                # Use the preparatory method rather than a removed generic set_path
                if hasattr(self, "prepFileModeFileList"):
                    self.prepFileModeFileList(parent)
                else:
                    # defensive fallback
                    super().set_path(parent)
            except Exception as e:
                self.printException(e, "changing to parent in key_left")

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

            # update app-level path info
            try:
                self.app.path = os.path.abspath(parent)
                self.app.displayed_path = self.path
            except Exception as e:
                self.printException(e, "exception updating app path info")

            try:
                self.focus()
            except Exception as e:
                self.printException(e)

            return True

        # Left on non-parent: ignore (do nothing)
        return False

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

                return True

        if item_name != "..":
            full = os.path.join(self.path, item_name)
            if os.path.isdir(full):
                # switch the listing to the selected directory
                try:
                    if hasattr(self, "prepFileModeFileList"):
                        self.prepFileModeFileList(full)
                    else:
                        super().set_path(full)
                except Exception as e:
                    self.printException(e, "changing directory in key_right")
                # ensure highlight resets to the minimum selectable item
                try:
                    self.index = getattr(self, "_min_index", 0) or 0
                except Exception as e:
                    self.printException(e, "exception resetting index")

                # update app-level current path as well
                try:
                    self.app.path = os.path.abspath(full)
                except Exception as e:
                    self.printException(e, "exception updating app.path")

                # focus back on this list
                try:
                    self.focus()
                except Exception as e:
                    self.printException(e)

                return True

            # Delegate history population to the FileModeHistoryList preparatory API
            try:
                hist = None
                try:
                    hist = self.app.query_one("#right1")
                except Exception as e:
                    self.printException(e)
                    hist = None

                # If the right1 widget doesn't expose the preparatory API,
                # try the alternate left location (log-first layouts).
                if hist is None or not hasattr(hist, "prepListModeHistoryList"):
                    try:
                        hist = self.app.query_one("#left")
                    except Exception as e:
                        self.printException(e)
                        hist = None

                if hist is not None and hasattr(hist, "prepListModeHistoryList"):
                    try:
                        hist.prepListModeHistoryList(item_name)
                    except Exception as e:
                        self.printException(e, "prepListModeHistoryList failed")
                        try:
                            # fallback: ask app to open history for file
                            if hasattr(self.app, "_open_history_for_file"):
                                self.app._open_history_for_file(item_name)
                        except Exception as e:
                            self.printException(e, "fallback open history failed")
                else:
                    # No preparatory API available; fallback to app helper
                    try:
                        if hasattr(self.app, "_open_history_for_file"):
                            self.app._open_history_for_file(item_name)
                    except Exception as e:
                        self.printException(e, "could not open history for file")

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
    """File list for repo-first / log-first mode."""

    key = "Key: ' ' tracked  U untracked  M modified  R renamed  A staged  D deleted  I ignored  ! conflicted"

    def prepRepoModeFileList(self, previous_hash: Optional[str], current_hash: Optional[str]) -> None: # RepoModeFileList
        """Populate this RepoModeFileList with files changed between two commits.

        `previous_hash` and `current_hash` are commit-ish identifiers (short
        or full) used to compute the tree diff via pygit2. This method builds
        ListItem entries from the diff and appends them after refresh to avoid
        DuplicateId mount races.
        """
        try:
            self.clear()
        except Exception as e:
            self.printException(e, "clearing repo-mode file list")
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

            # Build items buffer from diff deltas
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
                        if getattr(self, "_populated", False):
                            return
                        try:
                            key_li = ListItem(
                                Label(
                                    Text(
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

                        self.index = getattr(self, "_min_index", 0) or 0
                        self.focus()
                        self.refresh()

                        try:
                            self.call_after_refresh(lambda: setattr(self, "index", getattr(self, "_min_index", 0) or 0))
                        except Exception as e:
                            self.printException(e, "scheduling index reset after refresh")
                            try:
                                self.index = getattr(self, "_min_index", 0) or 0
                            except Exception as e:
                                self.printException(e, "setting index to _min_index fallback")
                        self._populated = True
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
                            if getattr(self, "app", None):
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

    def key_left(self) -> bool:  # RepoModeFileList
        """When Left is pressed in the repo-mode Files column, close
        the Files column and restore the History column to full-width.

        Returns True to indicate the key was handled.
        """
        try:
            # Hide the right1 (Files) column and restore left (History)
            try:
                # Restore single-column history layout using helper
                self.app.layout_left_only()
            except Exception as e:
                self.printException(e, "exception restoring left-only layout")
            # Additionally enforce container widths/display to be robust across Textual versions
            try:
                try:
                    right1_col = self.app.query_one("#right1-column")
                    right1_col.styles.width = "0%"
                    right1_col.styles.flex = 0
                except Exception as e:
                    self.printException(e)

                try:
                    right1 = self.app.query_one("#right1")
                    right1.styles.display = "none"
                except Exception as e:
                    self.printException(e, "could not hide right1 in RepoModeFileList.key_left")
            except Exception as e:
                self.printException(e, "could not enforce right1 hide")

            # Update titles so left shows 'History' and right1 hidden
            try:
                lbl = self.app.query_one("#left-title", Label)
                lbl.update(Text("History", style="bold"))
            except Exception as e:
                self.printException(e, "exception updating left-title")

            try:
                lbl = self.app.query_one("#right1-title", Label)
                lbl.styles.display = "none"
            except Exception as e:
                self.printException(e, "exception hiding right1-title")

            # Focus the History column (left)
            try:
                left = None
                try:
                    left = self.app.query_one("#left")
                except Exception as e:
                    self.printException(e)
                    left = None
                if left is not None:
                    left.focus()
            except Exception as e:
                self.printException(e, "exception focusing left history")

        except Exception as e:
            self.printException(e)
        return False

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
                except Exception as e:
                    self.printException(e, "could not push TBDModal for filename exception")

                return True

        # Determine commit hashes: prefer per-item hashes then file_list attrs then app-wide
        try:
            prev = (
                getattr(child, "_hash_prev", None)
                or getattr(self, "current_prev_sha", None)
                or getattr(self.app, "current_prev_sha", None)
            )
            curr = (
                getattr(child, "_hash_curr", None)
                or getattr(self, "current_commit_sha", None)
                or getattr(self.app, "current_commit_sha", None)
            )
        except Exception as e:
            self.printException(e, "exception getting commit hashes")
            prev = None
            curr = None

        if not filename:
            try:
                self.app.push_screen(_TBDModal("Unknown filename for diff"))
            except Exception as e:
                self.printException(e, "could not push TBDModal for unknown filename in FileModeFileList")

            return True

        # Store current diff info on the app for re-rendering/variant toggles
        try:
            self.app.current_commit_sha = curr
            self.app.current_prev_sha = prev
            self.app.current_diff_file = filename
        except Exception as e:
            self.printException(e, "exception setting app diff info")

        try:
            # Delegate diff population to the shared DiffList preparer
            diff_widget = None
            try:
                diff_widget = self.app.query_one("#right2")
            except Exception as e:
                self.printException(e, "locating right2 diff widget")

            if diff_widget is not None and hasattr(diff_widget, "prepDiffListBase"):
                try:
                    diff_widget.prepDiffListBase(filename, prev, curr)
                except Exception as exc:
                    try:
                        self.app.push_screen(_TBDModal(str(exc)))
                    except Exception as e:
                        self.printException(e, "could not push TBDModal for diff-fullscreen error")
            else:
                try:
                    self.app.push_screen(_TBDModal("Could not show diff (no diff widget)"))
                except Exception as e:
                    self.printException(e)

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
                    nodes = getattr(self, "_nodes", [])
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
            # Use centralized layout helper to set left/right split and hide diff
            try:
                try:
                    self.app.layout_left_right_split()
                except Exception as e:
                    self.printException(e, "layout_left_right_split")
                # inner lists should fill their outer column
                left.styles.width = "100%"
                left.styles.flex = 0
            except Exception as e:
                self.printException(e, "setting left list styles")

            try:
                self.styles.width = "100%"
                self.styles.display = None
                self.styles.flex = 0
            except Exception as e:
                self.printException(e, "setting history styles")

            # explicitly hide the diff list (outer column already shrunk by helper)
            try:
                right2.styles.display = "none"
            except Exception as e:
                self.printException(e, "hiding diff list")

        except Exception as e:
            self.printException(e, "layout adjustment")

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

        try:
            lbl = self.app.query_one("#right1-title", Label)
            lbl.update(Text(right1_text, style="bold"))
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e, "updating right1-title label")

        try:
            lbl = self.app.query_one("#right2-title", Label)
            lbl.styles.display = "none"
            lbl.styles.height = 0
            lbl.styles.width = 0
        except Exception as e:
            self.printException(e, "updating right2 title")

        # HistoryListBase footer
        try:
            footer = self.app.query_one("#footer", Label)
            footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓ →   m(ark)", style="bold"))
        except Exception as e:
            self.printException(e, "updating footer")

    def more_keys(self, event: events.Key) -> bool:  # HistoryListBase
        """Handle history-specific keys.
        Return True when the key was handled (e.g. `m`), False otherwise.
        """
        try:
            key = event.key
            logger.debug(f"HistoryListBase.more_keys: key={key}")

            # Mark/unmark the file referenced by this history view
            if key and key.lower() == "m":
                try:
                    event.stop()
                except Exception:
                    pass
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

    def key_right(self) -> bool:  # HistoryListBase
        """Handle right key behavior for HistoryListBase.

        Returns True when the key was handled/consumed.
        """
        return False


class FileModeHistoryList(HistoryListBase):
    """subclass for FileMode HistoryList functionality; see `HistoryListBase` for shared logic."""

    def prepListModeHistoryList(self, file_path: str) -> None: # FileModeHistoryList
        """Populate this History list with the commit history for a single file.

        Accepts a file path (filename relative to `self.app.path`) and
        populates the widget by running `git log --follow` in the current
        working directory. Appends ListItem entries and focuses the widget.
        """
        try:
            filename = file_path or getattr(self, "_filename", None)
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
                cwd=getattr(self.app, "path", None),
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
                    app = getattr(self, "app", None)
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

                for pseudo in pseudo_entries:
                    display_pseudo = pseudo
                    if pseudo == "STAGED":
                        try:
                            app = getattr(self, "app", None)
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
                self.index = 0
            except Exception as e:
                self.printException(e)

            try:
                self.focus()
            except Exception as e:
                self.printException(e)

        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)

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
        return False

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
        filename = getattr(self, "_filename", None)
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
            diff_widget = None
            try:
                diff_widget = self.app.query_one("#right2")
            except Exception as e:
                self.printException(e, "locating right2 diff widget")

            if diff_widget is not None and hasattr(diff_widget, "prepDiffListBase"):
                try:
                    diff_widget.prepDiffListBase(filename, previous_hash, current_hash)
                except Exception as exc:
                    try:
                        self.app.push_screen(_TBDModal(str(exc)))
                    except Exception as e:
                        self.printException(e, "showing diff error modal")
            else:
                try:
                    self.app.push_screen(_TBDModal("Could not show diff (no diff widget)"))
                except Exception as e:
                    self.printException(e)

        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)

        return False


class RepoModeHistoryList(HistoryListBase):
    """RepoMode History list used when `-l/--log-first`"""

    def prepRepoModeHistoryList(self) -> None: # RepoModeHistoryList
        """Populate this RepoModeHistoryList using the current repository.

        This method discovers the repository from the app state (prefer
        `self.app.repo_root` then `self.app.path`) and populates the widget
        with a repository-wide commit log using pygit2. Appends ListItem
        entries with the format: "YYYY-MM-DD <short-hash> <subject>".
        """
        try:
            app = getattr(self, "app", None)
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

            try:
                self.focus()
            except Exception as e:
                self.printException(e)

        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)

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

                return True
            idx = getattr(self, "index", None)
            if idx is None or idx < 0 or idx >= len(nodes):
                try:
                    self.app.push_screen(_TBDModal("No commit selected"))
                except Exception as e:
                    self.printException(e)

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

                    return True
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
                        raise ValueError("Could not parse commit lines for hashes")
                    current_hash = m1.group(2)
                    previous_hash = m2.group(2)
                except Exception as exc:
                    try:
                        self.app.push_screen(_TBDModal(f"Could not parse hashes: {exc}"))
                    except Exception as e:
                        self.printException(e)

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

                    return True
            except Exception as exc:
                self.printException(exc, "unexpected mount error")
                try:
                    self.app.push_screen(_TBDModal("Could not show files for commit diff"))
                except Exception as e:
                    self.printException(e)

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

            # Delegate population to the RepoModeFileList instance so it can
            # assemble and append its items in a safe, mount-friendly way.
            try:
                try:
                    file_list.current_commit_sha = current_hash
                    file_list.current_prev_sha = previous_hash
                except Exception as e:
                    self.printException(e, "setting commit hashes on file_list")
                try:
                    file_list.prepRepoModeFileList(previous_hash, current_hash)
                except Exception as e:
                    self.printException(e, "prepRepoModeFileList failed")
            except Exception as e:
                self.printException(e, "delegating repo-mode file list population failed")

            try:
                file_list.styles.display = None
            except Exception as e:
                self.printException(e)

            try:
                file_list.index = getattr(file_list, "_min_index", 0) or 0
            except Exception as e:
                self.printException(e)

                try:
                    # Make the Files column visible and use central layout helper
                    self.app.layout_left_right_split()
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
                except Exception as e:
                    self.printException(e, "setting files column layout")

                file_list.focus()
            except Exception as e:
                self.printException(e)

            try:
                # Diagnostic: log the column styles and file_list state
                try:
                    left_col = self.app.query_one("#left-column")
                    right1_col = self.app.query_one("#right1-column")
                    logger.debug(
                        f"RepoModeHistoryList.key_right: columns left={getattr(left_col.styles,'width',None)} "
                        f"right1={getattr(right1_col.styles,'width',None)}"
                    )
                except Exception as e:
                    self.printException(e)

                try:
                    logger.debug(
                        f"RepoModeHistoryList.key_right: file_list.display={getattr(file_list.styles,'display',None)}"
                        f"nodes={len(getattr(file_list,'_nodes',[]))}"
                    )
                except Exception as e:
                    self.printException(e)

            except Exception as e:
                self.printException(e)

            try:
                self.app.current_commit_sha = current_hash
                self.app.current_prev_sha = previous_hash
            except Exception as e:
                self.printException(e)

        except Exception as exc:
            self.printException(exc)
            try:
                self.app.push_screen(_TBDModal(str(exc)))
            except Exception as e:
                self.printException(e)

        return True


class DiffListBase(AppBase):
    """
    ListView used for the Diff columns.
    """

    def prepDiffListBase( # DiffListBase
        self,
        filename: str,
        previous_hash: Optional[str],
        current_hash: Optional[str],
        variant_index: Optional[int] = None,
    ) -> None:
        """Populate this Diff list for `filename` between two commit hashes.

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
                except Exception:
                    pass

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
                        if getattr(self.app, "colorize_diff", False):
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
            self.focus()

        except Exception as exc:
            self.printException(exc, "prepDiffListBase outer failure")

    def more_keys(self, event: events.Key) -> bool:  # DiffListBase
        """
        Handle left key to move focus back to History;
        handle PgUp/PgDn with visible selection; handle c/C to toggle colorization.

        Return True when the key was handled, False otherwise.
        """
        try:
            key = event.key
            logger.debug(f"DiffList.more_keys: key={key}")

            # Handle f/F to toggle fullscreen diff
            if key and key.lower() == "f":
                try:
                    event.stop()
                except Exception:
                    pass
                try:
                    if getattr(self.app, "diff_fullscreen", False):
                        self.app.exit_diff_fullscreen()
                        # keep focus on diff after restoring
                        try:
                            self.focus()
                        except Exception as e:
                            self.printException(e, "focus() exception")

                    else:
                        try:
                            # require right2 to be visible
                            if self.app.query_one("#right2", ListView).styles.display != "none":
                                self.app.enter_diff_fullscreen()
                                try:
                                    self.focus()
                                except Exception as e:
                                    self.printException(e, "focus() after enter_diff_fullscreen exception")

                        except Exception as e:
                            self.printException(e, "enter fullscreen check exception")
                            try:
                                self.app.enter_diff_fullscreen()
                            except Exception as e:
                                self.printException(e, "fallback enter_diff_fullscreen exception")

                except Exception as e:
                    self.printException(e, "exception toggling fullscreen f/F")
                return True

            # Handle c/C to toggle colorization
            if key and key.lower() == "c":
                try:
                    event.stop()
                except Exception:
                    pass
                logger.debug(f"DiffList: c/C pressed, colorize_diff={getattr(self.app, 'colorize_diff', None)}")
                try:
                    self.app.colorize_diff = not self.app.colorize_diff
                    logger.debug(f"DiffList: toggled to colorize_diff={self.app.colorize_diff}")

                    if (
                        getattr(self.app, "current_commit_sha", None)
                        and getattr(self.app, "current_prev_sha", None)
                        and getattr(self.app, "current_diff_file", None)
                    ):

                        logger.debug("DiffList: re-rendering diff with new colorization")
                        saved_scroll_y = self.scroll_y
                        saved_index = self.index

                        previous_hash = self.app.current_prev_sha
                        current_hash = self.app.current_commit_sha
                        filename = self.app.current_diff_file

                        try:
                            self.prepDiffListBase(filename, previous_hash, current_hash)
                        except Exception as e:
                            self.printException(e, "prepDiffListBase failed in c/C handler")

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
                except Exception:
                    pass
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

                    try:
                        title_lbl = self.app.query_one("#right2-title", Label)
                        v = variants[cur]
                        title_text = "Diff" if not v else f"Diff {v}"
                        title_lbl.update(Text(title_text, style="bold"))
                    except Exception as e:
                        self.printException(e, "updating right2 title exception")

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

                        try:
                            self.prepDiffListBase(filename, previous_hash, current_hash)
                        except Exception as e:
                            self.printException(e, "prepDiffListBase failed after rotating variant")

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
                # Query untyped and verify instance to avoid WrongType exceptions
                try:
                    hist = self.app.query_one("#right1")
                except Exception as e:
                    self.printException(e, "could not query #right1")
                    hist = None
                if hist is not None and not isinstance(hist, FileModeHistoryList):
                    hist = None
            except Exception as e:
                self.printException(e, "could not query #right1 in DiffList.on_focus")
                hist = None
            diff = self.app.query_one("#right2", ListView)
            try:
                # adjust outer columns to the target proportions
                try:
                    self.app.layout_three_columns()
                except Exception as e:
                    self.printException(e)

                left.styles.width = "100%"
                left.styles.flex = 0
            except Exception as e:
                self.printException(e)

            try:
                if hist is not None:
                    try:
                        hist.styles.width = "100%"
                        hist.styles.flex = 0
                        hist.styles.display = None
                    except Exception as e:
                        self.printException(e)

            except Exception as e:
                self.printException(e)

            try:
                diff.styles.width = "100%"
                diff.styles.display = None
                diff.styles.flex = 0
            except Exception as e:
                self.printException(e)

        except Exception as e:
            self.printException(e)

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

        try:
            lbl = self.app.query_one("#right1-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e)

        try:
            lbl = self.app.query_one("#right2-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception as e:
            self.printException(e)

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


class FileModeDiffList(DiffListBase):
    """
    FileMode DiffList subclass; see `DiffListBase` for shared logic.
    Left arrow moves focus back to History.
    """

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
                try:
                    # Enforce expected column sizes after exiting fullscreen
                    try:
                        self.app.layout_three_columns()
                    except Exception as e:
                        self.printException(e, "layout_three_columns after exit fullscreen")
                    logger.debug("FileModeDiffList.key_left: enforced three-column layout after exit")
                except Exception as e:
                    self.printException(e, "could not enforce columns after exit fullscreen")
                return True
            # otherwise move focus back to History
            try:
                try:
                    # Try to locate the History widget in either #right1 or #left
                    hist = None
                    try:
                        cand = self.app.query_one("#right1")
                    except Exception as e:
                        self.printException(e)
                        cand = None
                    if cand is not None and isinstance(cand, FileModeHistoryList):
                        hist = cand
                    else:
                        try:
                            cand2 = self.app.query_one("#left")
                        except Exception as e:
                            self.printException(e)
                            cand2 = None
                        if cand2 is not None and isinstance(cand2, FileModeHistoryList):
                            hist = cand2
                except Exception as e:
                    self.printException(e, "could not locate history widget in FileModeDiffList.key_left")
                    hist = None
                # Ensure the Files/History split is restored to 25/75 before focusing
                try:
                    self.app.layout_left_right_split()
                except Exception as e:
                    self.printException(e, "could not set left/right split before focus")
                try:
                    if hist is not None:
                        hist.focus()

                        # After focus, enforce the desired 25/75 split again
                        def _enforce_25_75():
                            try:
                                try:
                                    self.app.layout_left_right_split()
                                except Exception as e:
                                    self.printException(e, "post-focus layout_left_right_split")
                                try:
                                    r1 = self.app.query_one("#right1")
                                    r1.styles.display = None
                                except Exception as e:
                                    self.printException(e, "could not unhide #right1 after focus")

                            except Exception as e:
                                self.printException(e, "post-focus enforce 25/75")

                        try:
                            self.app.call_after_refresh(_enforce_25_75)
                        except Exception as e:
                            self.printException(e, "could not schedule _enforce_25_75 via call_after_refresh")
                            _enforce_25_75()
                except Exception as e:
                    self.printException(e, "exception focusing history")
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

                    return True
            except Exception as e:
                self.printException(e, "checking displays for fullscreen failed")
                # best-effort enter
                try:
                    self.app.enter_diff_fullscreen()
                except Exception as e:
                    self.printException(e, "fallback enter_diff_fullscreen exception")

            return True
        except Exception as e:
            self.printException(e, "unexpected exception")
            return True
        return True


class RepoModeDiffList(DiffListBase):
    """
    Diff list for repo-first / log-first mode.
    """

    def key_left(self) -> bool:  # FileModeDiffList
        """Handle left key behavior

        Returns True when the key was handled/consumed.
        """
        return False

    def key_right(self) -> bool:  # FileModeDiffList
        """Handle left key behavior

        Returns True when the key was handled/consumed.
        """
        return False

class _TBDModal(ModalScreen):
    """Simple modal that shows a message (default "TBD") and closes on any key."""

    def __init__(self, message: str | None = None, **kwargs) -> None: # TBDModal
        """Create the modal with an optional `message` to display."""
        super().__init__(**kwargs)
        self.message = message or "TBD"

    def compose(self) -> ComposeResult: # TBDModal
        """Compose the modal contents (a single Static message)."""
        yield Static(Text(self.message, style="bold"), id="tbd-msg")

    def on_key(self, event: events.Key) -> None: # TBDModal
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

    def on_mount(self) -> None: # HelpList
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

    def on_key(self, event: events.Key) -> None: # HelpList
        """Handle keys - go back to files view on any key except arrows/quit."""
        try:
            handled = False
            try:
                handled = super().on_key(event)
            except Exception as e:
                self.printException(e)
                handled = False
            if handled:
                return
        except Exception as e:
            self.printException(e)

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
                try:
                    # Use centralized helper to restore widths and displays
                    self.app._apply_column_layout(
                        state["left"]["width"],
                        state["right1"]["width"],
                        state["right2"]["width"],
                        left_display=None,
                        right1_display=state["right1"].get("display"),
                        right2_display=state["right2"].get("display"),
                    )
                except Exception as e:
                    self.printException(e, "restoring saved column state via helper")

                # Determine focus target: rightmost visible column
                try:
                    if state["right2"].get("display") != "none":
                        focus_target = "#right2"
                    elif state["right1"].get("display") != "none":
                        focus_target = "#right1"
                except Exception as e:
                    self.printException(e, "could not determine focus target from saved state")

                logger.debug("Column state restored")
            else:
                logger.debug("No saved state, showing only files column")
                # Fallback: just show files column
                try:
                    self.app.layout_left_only()
                except Exception as e:
                    self.printException(e, "layout_left_only fallback")

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

    def __init__( # GitHistoryTool
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

    def printException(self, e, msg=None): # GitHistoryTool
        """Log an exception from the app context (mirrors AppBase.printException)."""
        className = type(self).__name__
        funcName = sys._getframe(1).f_code.co_name
        msg = msg if msg else "???"
        logger.warning(f"WARNING: {className}.{funcName}: {msg}")
        logger.warning(traceback.format_exc())

    # Layout helpers on the App so widgets can call `self.app.layout_*`.
    def _apply_column_layout( # GitHistoryTool
        self, left_w: str, right1_w: str, right2_w: str, left_display=None, right1_display=None, right2_display=None
    ) -> None:
        try:
            try:
                lc = self.query_one("#left-column")
                lc.styles.width = left_w
                lc.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set left-column")
            try:
                r1c = self.query_one("#right1-column")
                r1c.styles.width = right1_w
                r1c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set right1-column")
            try:
                r2c = self.query_one("#right2-column")
                r2c.styles.width = right2_w
                r2c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set right2-column")

            try:
                if left_display is not None:
                    self.query_one("#left").styles.display = left_display
            except Exception as e:
                self.printException(e, "could not set left display in _apply_column_layout")

            try:
                if right1_display is not None:
                    self.query_one("#right1").styles.display = right1_display
            except Exception as e:
                self.printException(e, "could not set right1 display in _apply_column_layout")

            try:
                if right2_display is not None:
                    self.query_one("#right2").styles.display = right2_display
            except Exception as e:
                self.printException(e, "could not set right2 display in _apply_column_layout")

        except Exception as e:
            self.printException(e, "error applying column layout")

    def layout_left_only(self) -> None: # GitHistoryTool
        """Show only the left (History) column full-width."""
        self._apply_column_layout("100%", "0%", "0%", left_display=None, right1_display="none", right2_display="none")

    def layout_left_right_split(self) -> None: # GitHistoryTool
        """Show left/history and files split 25%/75%."""
        self._apply_column_layout("25%", "75%", "0%", left_display=None, right1_display=None, right2_display="none")

    def layout_three_columns(self) -> None: # GitHistoryTool
        """Show three-column layout 5%/15%/80%."""
        self._apply_column_layout("5%", "15%", "80%", left_display=None, right1_display=None, right2_display=None)

    def layout_diff_fullscreen(self) -> None: # GitHistoryTool
        """Make the diff column fullscreen (hide left and right1)."""
        self._apply_column_layout("0%", "0%", "100%", left_display="none", right1_display="none", right2_display=None)

    def build_diff_cmd(self, prev: str | None, curr: str | None, fname: str) -> list[str]: # GitHistoryTool
        """Construct the git diff command honoring the currently selected variant.

        The variant (if not None) is inserted right after `git diff` so that
        options like `--ignore-space-change` and `--diff-algorithm=patience`
        are applied to the invoked command.
        """

        def _is_pseudo(h: str | None) -> bool: # GitHistoryTool
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

    def build_repo_cache(self) -> None: # GitHistoryTool
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

    def compose(self) -> ComposeResult: # GitHistoryTool
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

    async def on_mount(self) -> None: # GitHistoryTool
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

        try:
            right1.styles.display = "none"
        except Exception as e:
            self.printException(e)

        try:
            right2.styles.display = "none"
        except Exception as e:
            self.printException(e)

        try:
            right3.styles.display = "none"
        except Exception as e:
            self.printException(e)

        # Populate the Files column only when present and not in log-first startup
        try:
            if left is not None and not getattr(self, "log_first", False):
                # For FileModeFileList instances, call their preparatory method
                # to populate the listing. Repo-mode variants implement other
                # population paths.
                if isinstance(left, FileModeFileList) and hasattr(left, "prepFileModeFileList"):
                    left.prepFileModeFileList(self.path)
        except Exception as e:
            self.printException(e)

        # If started in log-first (repo-first) mode, populate repo-wide history
        try:
            if getattr(self, "log_first", False):
                try:
                    self._open_repo_history()
                except Exception as e:
                    self.printException(e)

        except Exception as e:
            self.printException(e)

        # If launched with a filename, populate and focus its history immediately
        try:
            if getattr(self, "initial_file", None):
                try:
                    self._open_history_for_file(self.initial_file)
                except Exception as e:
                    self.printException(e)

        except Exception as e:
            self.printException(e)

    def _open_history_for_file(self, item_name: str) -> None: # GitHistoryTool
        """Populate the History column for `item_name` and focus it.

        Mirrors the behavior used when pressing Right on a file in the Files column.
        """
        try:
            hist = self.query_one("#right1", ListView)
            # Prefer the new preparatory API when available on the history widget
            hist.prepListModeHistoryList(item_name)
            self.layout_left_right_split()
            hist.index = 0
            hist.focus()
            # ensure we are not in diff-fullscreen when opening history
            self.diff_fullscreen = False
        except Exception as e:
            try:
                self.push_screen(_TBDModal(str(e)))
            except Exception as e:
                self.printException(e)

    def _open_repo_history(self) -> None: # GitHistoryTool
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

                if hist is not None:
                    try:
                        hist.prepRepoModeHistoryList()

                        # Make left column full-width and hide others
                        self.layout_left_only()
                        self.query_one("#right3-column").styles.width = "0%"
                        self.query_one("#right3-column").styles.flex = 0

                        hist.index = 0
                        hist.focus()
                        self.diff_fullscreen = False

                    except Exception as e:
                        self.printException(e)

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
                        # RepoModeHistoryList implements prepRepoModeHistoryList()
                        repo_hist.prepRepoModeHistoryList()
                    except Exception as e:
                        self.printException(e)

                    # Adjust layout: hide files and other right columns, show history full-width
                    try:
                        self._apply_column_layout(
                            "0%", "100%", "0%", left_display="none", right1_display=None, right2_display="none"
                        )
                        self.query_one("#right3-column").styles.width = "0%"
                        self.query_one("#right3-column").styles.flex = 0
                        repo_hist.index = 0
                        repo_hist.focus()
                        self.diff_fullscreen = False
                    except Exception as e:
                        self.printException(e)
                    return

            except Exception as e:
                self.printException(e)
                self.push_screen(_TBDModal(str(e)))

        except Exception as e:
            try:
                self.push_screen(_TBDModal(str(e)))
            except Exception as e2:
                self.printException(e2)

    def on_key(self, event: events.Key) -> None: # GitHistoryTool
        """Global key handler.

        - Block the Ctrl+P palette shortcut.
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
                    # Save current column state, normalizing widths to percent strings
                    def _norm_width(w):
                        try:
                            if w is None:
                                return None
                            # Textual may store a Scalar-like object with a `value` attr
                            if hasattr(w, "value"):
                                val = int(getattr(w, "value", 0))
                                return f"{val}%"
                            # If it's already a string that accidentally contains Unit names,
                            # extract leading number and use percent.
                            if isinstance(w, str) and "Unit.WIDTH" in w:
                                m = re.match(r"^(\d+)", w)
                                if m:
                                    return f"{m.group(1)}%"
                            # Already a usable string/number
                            return str(w)
                        except Exception as e:
                            self.printException(e, "error normalizing width in _norm_width")
                            return str(w)

                    left_col = self.query_one("#left-column")
                    right1_col = self.query_one("#right1-column")
                    right2_col = self.query_one("#right2-column")
                    right1_widget = self.query_one("#right1")
                    right2_widget = self.query_one("#right2")

                    self.saved_column_state = {
                        "left": {
                            "width": _norm_width(left_col.styles.width),
                            "flex": left_col.styles.flex,
                            "display": getattr(self.query_one("#left"), "styles", None)
                            and getattr(self.query_one("#left"), "styles").display,
                        },
                        "right1": {
                            "width": _norm_width(right1_col.styles.width),
                            "flex": right1_col.styles.flex,
                            "display": right1_widget.styles.display,
                        },
                        "right2": {
                            "width": _norm_width(right2_col.styles.width),
                            "flex": right2_col.styles.flex,
                            "display": right2_widget.styles.display,
                        },
                    }
                    logger.debug(f"Saved column state: {self.saved_column_state}")

                    # Show only the help column, hide others (use helper for main columns)
                    try:
                        self._apply_column_layout(
                            "0%", "0%", "0%", left_display="none", right1_display="none", right2_display="none"
                        )
                    except Exception as e:
                        self.printException(e, "could not apply helper for help view")
                    try:
                        # Make right3 the visible/help column
                        self.query_one("#right3-column").styles.width = "100%"
                        self.query_one("#right3-column").styles.flex = 0
                        self.query_one("#right3").styles.display = "block"
                        self.query_one("#right3").focus()
                    except Exception as e:
                        self.printException(e, "could not show/focus right3 help column")

                    # Update footer
                    footer = self.query_one("#footer", Label)
                    footer.update(Text("q(uit)  ↑ ↓  Press any key to return", style="bold"))
                except Exception as e:
                    self.printException(e)

                return

        except Exception as e:
            self.printException(e)

    def enter_diff_fullscreen(self) -> None: # GitHistoryTool
        """Make the Diff column full-screen (hide other columns) and update footer."""
        try:
            # save whether we were fullscreen already
            if getattr(self, "diff_fullscreen", False):
                return
            # Save current column state so we can restore it on exit.
            try:
                # Normalize saved widths to percent strings so restoring applies expected values
                def _norm_width(w):
                    try:
                        if w is None:
                            return None
                        if hasattr(w, "value"):
                            val = int(getattr(w, "value", 0))
                            return f"{val}%"
                        if isinstance(w, str) and "Unit.WIDTH" in w:
                            m = re.match(r"^(\d+)", w)
                            if m:
                                return f"{m.group(1)}%"
                        return str(w)
                    except Exception as e:
                        self.printException(e, "error normalizing width in enter_diff_fullscreen._norm_width")
                        return str(w)

                left_col = self.query_one("#left-column")
                right1_col = self.query_one("#right1-column")
                right2_col = self.query_one("#right2-column")
                left_widget = self.query_one("#left")
                right1_widget = self.query_one("#right1")
                right2_widget = self.query_one("#right2")
                self.saved_column_state = {
                    "left": {
                        "width": _norm_width(left_col.styles.width),
                        "flex": left_col.styles.flex,
                        "display": getattr(left_widget.styles, "display", None),
                    },
                    "right1": {
                        "width": _norm_width(right1_col.styles.width),
                        "flex": right1_col.styles.flex,
                        "display": getattr(right1_widget.styles, "display", None),
                    },
                    "right2": {
                        "width": _norm_width(right2_col.styles.width),
                        "flex": right2_col.styles.flex,
                        "display": getattr(right2_widget.styles, "display", None),
                    },
                }
                logger.debug(f"enter_diff_fullscreen: saved_column_state={self.saved_column_state}")
            except Exception as e:
                self.printException(e, "could not save column state before fullscreen")
            # collapse left/history and expand diff column via helper
            try:
                self.layout_diff_fullscreen()
                # ensure right1 is hidden (helper should handle, but enforce)
                try:
                    self.query_one("#right1").styles.display = "none"
                except Exception as e:
                    self.printException(e, "could not hide #right1 when entering fullscreen")

            except Exception as e:
                self.printException(e, "could not adjust columns for fullscreen")
            # mark state and update footer
            try:
                self.diff_fullscreen = True
            except Exception as e:
                self.printException(e, "could not set diff_fullscreen flag")

            try:
                footer = self.query_one("#footer", Label)
                footer.update(Text("q(uit)  ?/h(elp)  ↑ ↓   ←/f(ull)", style="bold"))
            except Exception as e:
                self.printException(e, "could not update footer")
        except Exception as e:
            self.printException(e)

    def exit_diff_fullscreen(self) -> None: # GitHistoryTool
        """Restore the standard three-column layout (columnated mode)."""
        try:
            if not getattr(self, "diff_fullscreen", False):
                return
            # If we previously saved a column state, restore it; otherwise
            # fall back to the default 5/15/80 layout. This ensures that
            # entering/exiting fullscreen preserves the layout active when
            # fullscreen was requested (e.g. log-first 25/75 vs default 5/15).
            try:
                if getattr(self, "saved_column_state", None):
                    s = self.saved_column_state
                    logger.debug(f"exit_diff_fullscreen: restoring saved_column_state={s}")

                    def _apply_saved(col_id, saved, default_width):
                        try:
                            col = self.query_one(col_id)
                            width = saved.get("width", default_width)
                            # If width is a scalar-like object stored earlier, convert to string
                            try:
                                if hasattr(width, "value"):
                                    width = f"{int(getattr(width, 'value', 0))}%"
                            except Exception as e:
                                self.printException(e, f"could not normalize saved width for {col_id}")

                            col.styles.width = width
                            col.styles.flex = saved.get("flex", 0)
                        except Exception as e:
                            self.printException(e, f"could not restore {col_id} from saved state")

                    _apply_saved("#left-column", s.get("left", {}), "5%")
                    try:
                        left_widget = self.query_one("#left")
                        left_widget.styles.display = s.get("left", {}).get("display", None)
                    except Exception as e:
                        self.printException(e, "could not restore left widget display")

                    _apply_saved("#right1-column", s.get("right1", {}), "15%")
                    try:
                        right1_widget = self.query_one("#right1")
                        right1_widget.styles.display = s.get("right1", {}).get("display", None)
                    except Exception as e:
                        self.printException(e, "could not restore right1 widget display")

                    _apply_saved("#right2-column", s.get("right2", {}), "80%")
                    try:
                        right2_widget = self.query_one("#right2")
                        right2_widget.styles.display = s.get("right2", {}).get("display", None)
                    except Exception as e:
                        self.printException(e, "could not restore right2 widget display")

                    # While we restore, suppress file-list focus handlers from
                    # overriding the applied column sizes.
                    try:
                        self._suppress_focus_layout = True
                        logger.debug("exit_diff_fullscreen: set _suppress_focus_layout flag")
                    except Exception as e:
                        self.printException(e, "could not set _suppress_focus_layout flag")

                    # Force a layout refresh and log actual computed sizes after restore
                    def _post_restore():
                        try:
                            lc = self.query_one("#left-column")
                            r1c = self.query_one("#right1-column")
                            r2c = self.query_one("#right2-column")
                            logger.debug(
                                "post_restore columns styles: left=%s right1=%s right2=%s",
                                (lc.styles.width, lc.styles.flex),
                                (r1c.styles.width, r1c.styles.flex),
                                (r2c.styles.width, r2c.styles.flex),
                            )
                            try:
                                lreg = getattr(lc, "region", None)
                                r1reg = getattr(r1c, "region", None)
                                r2reg = getattr(r2c, "region", None)
                                logger.debug(
                                    "post_restore regions (width,height): left=%s right1=%s right2=%s",
                                    (getattr(lreg, "width", None), getattr(lreg, "height", None)),
                                    (getattr(r1reg, "width", None), getattr(r1reg, "height", None)),
                                    (getattr(r2reg, "width", None), getattr(r2reg, "height", None)),
                                )
                            except Exception as e:
                                self.printException(e, "could not read column regions")
                            try:
                                # Force a refresh to ensure layout is reflowed
                                try:
                                    self.refresh()
                                except Exception as e:
                                    self.printException(e, "could not refresh during post_restore")

                            except Exception as e:
                                self.printException(e, "could not refresh after restore")
                        except Exception as e:
                            self.printException(e, "post_restore diagnostics failed")

                    self.call_after_refresh(_post_restore)

                    # clear the suppress flag after post-restore completes
                    def _clear_suppress():
                        try:
                            if getattr(self, "_suppress_focus_layout", False):
                                self._suppress_focus_layout = False
                                logger.debug("exit_diff_fullscreen: cleared _suppress_focus_layout flag")
                        except Exception as e:
                            self.printException(e, "could not clear suppress flag")

                    self.call_after_refresh(_clear_suppress)

                    # Clear saved state after restoring
                    try:
                        self.saved_column_state = None
                    except Exception as e:
                        self.printException(e, "could not clear saved_column_state")

                    logger.debug("exit_diff_fullscreen: restored from saved state")
                else:
                    # restore a sensible three-column layout using helper
                    try:
                        try:
                            self.layout_three_columns()
                        except Exception as e:
                            self.printException(e, "layout_three_columns in exit_diff_fullscreen")
                        try:
                            # ensure right1/right2 displays are visible after layout
                            self.query_one("#right1").styles.display = None
                            self.query_one("#right2").styles.display = None
                        except Exception as e:
                            self.printException(e, "could not restore right1/right2 display after three-column layout")

                    except Exception as e:
                        self.printException(e, "could not restore three-column layout")
            except Exception as e:
                self.printException(e)

            try:
                self.diff_fullscreen = False
                logger.debug("exit_diff_fullscreen: diff_fullscreen flag cleared")
            except Exception as e:
                self.printException(e, "could not clear diff_fullscreen flag")

            # Ensure Diff column is visible and focused after restoring layout
            try:
                try:
                    right2 = self.query_one("#right2")
                    right2.styles.display = None
                except Exception as e:
                    self.printException(e, "could not query #right2 for focus after restore")
                    right2 = None

                def _focus_diff():
                    try:
                        if right2 is not None:
                            right2.focus()
                            logger.debug("exit_diff_fullscreen: focused #right2 (diff)")
                    except Exception as e:
                        self.printException(e, "could not focus right2 after restore")

                try:
                    self.call_after_refresh(_focus_diff)
                except Exception as e:
                    self.printException(e, "could not schedule _focus_diff via call_after_refresh")
                    _focus_diff()
            except Exception as e:
                self.printException(e, "exception ensuring diff visibility after restore")
            try:
                footer = self.query_one("#footer", Label)
                footer.update(Text("q(uit)  ?/h(elp)  ← ↑ ↓   PgUp/PgDn  c(olor)  →/f(ull)", style="bold"))
            except Exception as e:
                self.printException(e, "could not update footer")
        except Exception as e:
            self.printException(e)


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
