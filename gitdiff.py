#!/usr/bin/env python3
"""

Usage: python textual_three_column.py [path]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import re
from typing import Optional
from rich.text import Text

# Optional pygit2 (preferred for repository queries)
try:
    import pygit2
    PYGIT2_AVAILABLE = True
except Exception:
    pygit2 = None
    PYGIT2_AVAILABLE = False

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


class FileList(ListView):
    """A ListView showing directory contents. Directories have a blue background.

    Navigation: arrow keys (up/down) move selection automatically because ListView
    handles keyboard navigation. The app focuses this widget on mount.
    """

    def set_path(self, path: str) -> None:
        path = os.path.abspath(path)
        self.path = path
        # keep the app aware of the full path currently displayed
        try:
            if hasattr(self, "app"):
                self.app.displayed_path = self.path
        except Exception:
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
                        except Exception:
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
                                    style = "grey50"
                                    repo_status = "untracked"
                                else:
                                    # tracked and clean — display in bright white
                                    style = "white"
                                    repo_status = "tracked_clean"
                            except Exception:
                                style = None
                                repo_status = None
                        else:
                            # outside repo tree -> untracked/not-in-repo
                            style = "grey50"
                            repo_status = "untracked"
                    else:
                        # no repo available -> treat as untracked
                        style = "grey50"
                        repo_status = "untracked"
                except Exception:
                    style = None
                    repo_status = None

                # Prefer a short marker to make status visually obvious
                markers = {
                    "conflicted": "!",
                    "staged": "A",
                    "wt_deleted": "D",
                    "ignored": "I",
                    "modified": "M",
                    "untracked": "?",
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
                except Exception:
                    pass
            # attach filename to the ListItem for reliable lookup later
            try:
                li._filename = name
            except Exception:
                pass
            self.append(li)

        # After populating, ensure the top entry is highlighted.
        try:
            self.call_after_refresh(self._highlight_top)
        except Exception:
            try:
                self.index = 0
            except Exception:
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
            except Exception:
                pass
            try:
                self.app.query_one("#right1-column").styles.width = "0%"
                self.app.query_one("#right1-column").styles.flex = 0
            except Exception:
                pass
            try:
                self.app.query_one("#right2-column").styles.width = "0%"
                self.app.query_one("#right2-column").styles.flex = 0
            except Exception:
                pass
            # Ensure inner left list fills its column
            self.styles.width = "100%"
            self.styles.flex = 0
        except Exception:
            pass
        try:
            right1 = self.app.query_one("#right1", HistoryList)
            right1.styles.display = "none"
        except Exception:
            pass
        try:
            right2 = self.app.query_one("#right2", ListView)
            right2.styles.display = "none"
        except Exception:
            pass

        # show/hide titles for columns: left visible, others hidden
        try:
            lbl = self.query_one("#left-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception:
            pass
        try:
            lbl = self.query_one("#right1-title", Label)
            lbl.styles.display = "none"
            lbl.styles.height = 0
            lbl.styles.width = 0
        except Exception:
            pass
        try:
            lbl = self.query_one("#right2-title", Label)
            lbl.styles.display = "none"
            lbl.styles.height = 0
            lbl.styles.width = 0
        except Exception:
            pass

    def on_key(self, event: events.Key) -> None:
        """Only allow up/down/left/right in this column.

        - Up: move to previous entry
        - Down: move to next entry
        - Left/Right: show a temporary "TBD" modal
        - Other keys: ignore
        """
        key = event.key
        if key == "q":
            # Allow global quit to bubble to the app
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
                    except Exception:
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
                    except Exception:
                        pass
                    # update app-level current path as well
                    try:
                        self.app.path = os.path.abspath(full)
                    except Exception:
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
                        except Exception:
                            pass

                        # remember which file this history is for
                        try:
                            hist._filename = item_name
                        except Exception:
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
                                    except Exception:
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
                            except Exception:
                                pseudo_entries = []

                            for pseudo in pseudo_entries:
                                pli = ListItem(Label(Text(pseudo)))
                                try:
                                    pli._hash = pseudo
                                except Exception:
                                    pass
                                hist.append(pli)

                            for line in out.splitlines():
                                li = ListItem(Label(Text(line)))
                                try:
                                    m = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", line)
                                    if m:
                                        li._hash = m.group(2)
                                except Exception:
                                    pass
                                hist.append(li)
                        else:
                            hist.append(ListItem(Label(Text(f"No git history for {item_name}"))))

                        # highlight and focus the top entry
                        try:
                            hist.index = 0
                        except Exception:
                            pass
                        try:
                                hist.focus()
                        except Exception:
                            pass
                    except Exception:
                        # If unable to update, show modal with output or message
                        msg = out or f"No git history for {item_name}"
                        try:
                            self.app.push_screen(_TBDModal(msg))
                        except Exception:
                            pass
                except Exception as exc:
                    try:
                        self.app.push_screen(_TBDModal(str(exc)))
                    except Exception:
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
                    except Exception:
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
                except Exception:
                    try:
                        # Fallback: set to first item
                        self.index = 0
                    except Exception:
                        pass

                # update app-level path info
                try:
                    self.app.path = os.path.abspath(parent)
                    self.app.displayed_path = self.path
                except Exception:
                    pass

                self.focus()
                return

            # Not the parent entry — show TBD for other left behaviors
            self.app.push_screen(_TBDModal())
        else:
            # ignore other keys
            event.stop()

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
                    except Exception:
                        pass
                    return
            # not found: default to first
            try:
                self.index = 0
            except Exception:
                pass
        except Exception:
            return

    def _highlight_top(self) -> None:
        """Highlight the first entry in the list after a refresh."""
        try:
            # If there are nodes, set index to 0; otherwise leave unset.
            nodes = getattr(self, "_nodes", [])
            if nodes:
                self.index = 0
        except Exception:
            return


class HistoryList(ListView):
    """ListView used for the History column. Left arrow moves focus back to Files."""

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
                    except Exception:
                        pass
                    try:
                        self.index = target
                    except Exception:
                        pass
                except Exception:
                    return

            try:
                self.call_after_refresh(_apply)
            except Exception:
                _apply()
        except Exception:
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
                except Exception:
                    pass
                try:
                    self.app.query_one("#right1-column").styles.width = "75%"
                    self.app.query_one("#right1-column").styles.flex = 0
                except Exception:
                    pass
                # inner lists should fill their outer column
                left.styles.width = "100%"
                left.styles.flex = 0
            except Exception:
                pass
            try:
                self.styles.width = "100%"
                self.styles.display = None
                self.styles.flex = 0
            except Exception:
                pass
            # hide diff list and shrink its outer column to zero so the
            # title doesn't consume space
            try:
                right2.styles.display = "none"
            except Exception:
                pass
            try:
                self.app.query_one("#right2-column").styles.width = "0%"
                self.app.query_one("#right2-column").styles.flex = 0
            except Exception:
                pass
        except Exception:
            pass
        # Titles: show left and history, hide diff
        try:
            lbl = self.app.query_one("#left-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception:
            pass
        try:
            lbl = self.app.query_one("#right1-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception:
            pass
        try:
            lbl = self.app.query_one("#right2-title", Label)
            lbl.styles.display = "none"
            lbl.styles.height = 0
            lbl.styles.width = 0
        except Exception:
            pass

    def on_key(self, event: events.Key) -> None:
        # handle left/right keys to move between columns or show diff
        key = event.key
        if key == "q":
            return
        if key == "left":
            event.stop()
            try:
                files = self.app.query_one("#left", FileList)
                files.focus()
            except Exception:
                pass
            return
        if key == "right":
            event.stop()
            # need at least two items after current index
            idx = getattr(self, "index", None)
            nodes = getattr(self, "_nodes", [])
            if idx is None or idx < 0 or idx >= len(nodes) - 1:
                # nothing to diff
                try:
                    self.app.push_screen(_TBDModal("No earlier commit to diff with"))
                except Exception:
                    pass
                return

            # helper to extract the text of a ListItem label
            def _text_of(node) -> str:
                try:
                    lbl = node.query_one(Label)
                    if hasattr(lbl, "text"):
                        return lbl.text
                    renderable = getattr(lbl, "renderable", None)
                    if isinstance(renderable, Text):
                        return renderable.plain
                    if renderable is not None:
                        return str(renderable)
                    return str(lbl)
                except Exception:
                    return str(node)

            current_line = _text_of(nodes[idx])
            next_line = _text_of(nodes[idx + 1])

            # Prefer attached _hash on ListItems; fallback to regex parsing
            current_hash = getattr(nodes[idx], "_hash", None)
            previous_hash = getattr(nodes[idx + 1], "_hash", None)
            if not current_hash or not previous_hash:
                try:
                    m1 = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", current_line)
                    m2 = re.match(r"^\s*(\S+)\s+([0-9a-fA-F]+)\b", next_line)
                    if not m1 or not m2:
                        raise ValueError(
                            f"Lines not in expected format:\n{current_line!r}\n{next_line!r}"
                        )
                    current_hash = m1.group(2)
                    previous_hash = m2.group(2)
                except Exception as exc:
                    try:
                        self.app.push_screen(_TBDModal(f"Could not parse hashes: {exc}"))
                    except Exception:
                        pass
                    return

            # determine filename for the history (attached when populated)
            filename = getattr(self, "_filename", None)
            if not filename:
                try:
                    self.app.push_screen(_TBDModal("Unknown filename for history"))
                except Exception:
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
                except Exception:
                    pass
                # ultimate fallback: show working-tree diff
                return ["git", "diff", "--", fname]

            try:
                cmd = _build_diff_cmd(previous_hash, current_hash, filename)
                proc = subprocess.run(cmd, cwd=self.app.path, capture_output=True, text=True)
                diff_out = proc.stdout or proc.stderr or ""
            except Exception as exc:
                try:
                    self.app.push_screen(_TBDModal(str(exc)))
                except Exception:
                    pass
                return

            # show the Diff column and populate it
            try:
                diff_view = self.app.query_one("#right2", ListView)
                try:
                    diff_view.clear()
                except Exception:
                    pass
                if diff_out:
                    for line in diff_out.splitlines():
                        diff_view.append(ListItem(Label(Text(line))))
                else:
                    diff_view.append(ListItem(Label(Text(f"No diff between {previous_hash}..{current_hash}"))))

                # make sure Diff column is visible
                try:
                    diff_view.styles.display = None
                except Exception:
                    pass

                try:
                    diff_view.index = 0
                except Exception:
                    pass
                try:
                    diff_view.focus()
                except Exception:
                    pass
            except Exception as exc:
                try:
                    self.app.push_screen(_TBDModal(str(exc)))
                except Exception:
                    pass
            return

        # Other keys: let default handling run by not stopping the event.
        return


class DiffList(ListView):
    """ListView used for the Diff column. Left arrow moves focus back to History."""

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "q":
            return
        if key == "left":
            event.stop()
            try:
                hist = self.app.query_one("#right1", HistoryList)
                hist.focus()
            except Exception:
                pass
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
                    except Exception:
                        pass
                    try:
                        self.index = target
                    except Exception:
                        pass
                except Exception:
                    return

            try:
                self.call_after_refresh(_apply)
            except Exception:
                _apply()
        except Exception:
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
                except Exception:
                    pass
                try:
                    self.app.query_one("#right1-column").styles.width = "15%"
                    self.app.query_one("#right1-column").styles.flex = 0
                except Exception:
                    pass
                try:
                    self.app.query_one("#right2-column").styles.width = "80%"
                    self.app.query_one("#right2-column").styles.flex = 0
                except Exception:
                    pass
                left.styles.width = "100%"
                left.styles.flex = 0
            except Exception:
                pass
            try:
                hist.styles.width = "100%"
                hist.styles.flex = 0
                hist.styles.display = None
            except Exception:
                pass
            try:
                diff.styles.width = "100%"
                diff.styles.display = None
                diff.styles.flex = 0
            except Exception:
                pass
        except Exception:
            pass
        # Show all titles when diff active
        try:
            lbl = self.app.query_one("#left-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception:
            pass
        try:
            lbl = self.app.query_one("#right1-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception:
            pass
        try:
            lbl = self.app.query_one("#right2-title", Label)
            lbl.styles.display = None
            lbl.styles.height = None
            lbl.styles.width = None
        except Exception:
            pass


class _TBDModal(ModalScreen):
    """Simple modal that shows a message (default "TBD") and closes on any key."""

    def __init__(self, message: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.message = message or "TBD"

    def compose(self) -> ComposeResult:
        yield Static(Text(self.message, style="bold"), id="tbd-msg")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.app.pop_screen()


class GitHistoryTool(App):
    TITLE = "Git History Navigator"
    CSS = """
Horizontal {
    height: 100%;
}
#left {
    border: solid white;
}
#right1 {
    border: heavy #555555;
}
#right2 {
    border: heavy #555555;
}
"""

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, path: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.path = os.path.abspath(path or os.getcwd())
        # store the full path that will be displayed at startup
        self.displayed_path = self.path
        # repository cache populated at mount
        self.repo_available = False
        self.repo_root: Optional[str] = None
        self.repo_index_set: set[str] = set()
        self.repo_status_map: dict[str, int] = {}

    def build_repo_cache(self) -> None:
        """Discover repository (if any) and build in-memory index/status maps.

        If pygit2 is unavailable or no repo is found, treat as empty repository
        (no tracked files).
        """
        self.repo_available = False
        self.repo_root = None
        self.repo_index_set = set()
        self.repo_status_map = {}

        if not PYGIT2_AVAILABLE:
            return

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
            except Exception:
                self.repo_index_set = set()

            # status: mapping path -> flags
            try:
                status_map = repo.status()
                # keys are paths relative to repo root
                self.repo_status_map = {k: int(v) for k, v in status_map.items()}
            except Exception:
                self.repo_status_map = {}

            self.repo_available = True
        except Exception:
            # leave as not available
            self.repo_available = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            # left column: flex so it takes remaining space
            with Vertical(id="left-column"):
                yield Label(Text("Files", style="bold"), id="left-title")
                yield FileList(id="left")
            # two minimal right columns
            with Vertical(id="right1-column"):
                yield Label(Text("History", style="bold"), id="right1-title")
                yield HistoryList(id="right1")
            with Vertical(id="right2-column"):
                yield Label(Text("Diff", style="bold"), id="right2-title")
                yield DiffList(id="right2")
        yield Footer()

    async def on_mount(self) -> None:
        """set sizes and populate left"""
        left = self.query_one("#left", FileList)
        right1 = self.query_one("#right1", HistoryList)
        right2 = self.query_one("#right2", ListView)
        # build repository cache (pygit2-based) before populating file list
        try:
            self.build_repo_cache()
        except Exception:
            pass
        # Start with Files column full-width, other columns hidden
        try:
            left.styles.width = "100%"
            left.styles.flex = 0
        except Exception:
            try:
                left.styles.flex = 1
            except Exception:
                pass

        try:
            right1.styles.display = "none"
        except Exception:
            pass
        try:
            right2.styles.display = "none"
        except Exception:
            pass

        left.set_path(self.path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Three-column Textual TUI")
    parser.add_argument("path", nargs="?", help="Directory to list", default=os.getcwd())
    args = parser.parse_args()

    app = GitHistoryTool(args.path)
    app.run()


if __name__ == "__main__":
    main()
