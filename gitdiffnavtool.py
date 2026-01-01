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
from typing import Optional

# Optional pygit2 support — best-effort import to enable repo status checks
try:
    import pygit2  # type: ignore
except Exception:
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
HIGHLIGHT_FILELIST_STYLE = f"white on {HIGHLIGHT_FILELIST_BG}"

HIGHLIGHT_REPOLIST_BG = "#3333CC"
HIGHLIGHT_REPOLIST_STYLE = f"white on {HIGHLIGHT_REPOLIST_BG}"

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
    padding: 1 1;
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



# --- Logging setup --------------------------------------------------------
# NOTE: logging is configured in `main()` when `--debug` is passed.

logger = logging.getLogger(__name__)


def printException(e: Exception, msg: Optional[str] = None) -> None:
    """Module-level helper to log unexpected exceptions when `self` isn't available.

    This mirrors the widget-level `printException` helper used by widgets.
    """
    try:
        short_msg = msg or ""
        logger.warning("%s: %s", short_msg, e)
        logger.warning(traceback.format_exc())
    except Exception as e2:
        # Last-resort fallback to stderr — avoid recursive logging
        sys.stderr.write(f"printException fallback: {e}\n")
        sys.stderr.write(f"secondary exception: {e2}\n")



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
        self._filename = None
        self.current_prev_sha = None
        self.current_commit_sha = None
        self.current_diff_file = None
        # When True the next watch_index-triggered scroll should animate
        # (used by page up / page down handlers to make the jump more
        # visually noticeable).
        self._page_scroll = False
        # Ensure common attributes exist so code can access them directly
        # Rely on ListView to provide `children`, `_nodes`, `index`, and `app`.

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
            # determine highlight colors based on widget type (repo vs file)
            try:
                is_repo_mode = isinstance(self, RepoModeFileList) or isinstance(self, RepoModeHistoryList)
            except Exception as e:
                # If classes aren't available, default to file highlight
                self.printException(e, "_activate_index: repo-mode detection failed")
                is_repo_mode = False

            if is_repo_mode:
                highlight_bg = HIGHLIGHT_REPOLIST_BG
                text_color = "white"
            else:
                highlight_bg = HIGHLIGHT_FILELIST_BG
                text_color = "white"

            # Only set the index here; actual visual activation is performed
            # in `watch_index` which runs after Textual has processed the index
            # change so our styles/classes won't be clobbered.
            try:
                logger.debug("_activate_index: old=%r new=%r is_repo=%s", old, new_index, is_repo_mode)
                logger.debug("_activate_index: scheduling index set via call_after_refresh -> %s", new_index)
                self.call_after_refresh(lambda: setattr(self, "index", new_index))
            except Exception as e:
                self.printException(e, "_activate_index: scheduling index set failed")
                try:
                    logger.debug("_activate_index: falling back to direct index set -> %s", new_index)
                    self.index = new_index
                except Exception as e2:
                    self.printException(e2, "_activate_index: setting index failed")
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
            try:
                is_repo_mode = isinstance(self, RepoModeFileList) or isinstance(self, RepoModeHistoryList)
            except Exception as e:
                self.printException(e, "watch_index: repo-mode detection failed")
                is_repo_mode = False
            highlight_bg = HIGHLIGHT_REPOLIST_BG if is_repo_mode else HIGHLIGHT_FILELIST_BG
            text_color = "white"

            logger.debug("watch_index: old=%r new=%r nodes=%d", old, new, len(nodes))
            # remove style/class from old
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
                        try:
                            if self._page_scroll:
                                self._page_scroll = False
                        except Exception as e:
                            self.printException(e)
                    except Exception as e:
                        printException(e, "watch_index: scrolling new node failed")
                    except Exception as e:
                        self.printException(e, "watch_index: scrolling new node failed")
                    except Exception as e:
                        self.printException(e, "watch_index: scrolling new node failed")
                except Exception as e:
                    self.printException(e, "watch_index: finding new node failed")
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
                for i, node in enumerate(nodes):
                    try:
                        raw = getattr(node, "_raw_text", None)
                        h = getattr(node, "_hash", None)
                        if raw == match:
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

    def key_page_down(self, event: events.Key | None = None) -> None:
        try:
            logger.debug("key_page_down invoked: index=%r nodes=%r", self.index, len(self.nodes()))
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
            # Set index synchronously so the watch_index runs and updates
            # visual highlight immediately instead of relying on scheduled calls.
            # Use _activate_index which schedules the index change after refresh
            try:
                # Mark that this was a page-scroll so watch_index uses animation
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
        logger.debug(
                "alias key_pageup invoked: key=%r index=%r nodes=%r",
                getattr(event, "key", None),
                self.index,
                len(self.nodes()),
            )
        
            
        return self.key_page_down(event)

    def key_pagedown(self, event: events.Key | None = None) -> None:
        logger.debug(
                "alias key_pagedown invoked: key=%r index=%r nodes=%r",
                getattr(event, "key", None),
                self.index,
                len(self.nodes()),
            )
        return self.key_page_down(event)

    def key_page_up(self, event: events.Key | None = None) -> None:
        try:
            logger.debug("key_page_up invoked: index=%r nodes=%r", self.index, len(self.nodes()))
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

    def key_pageup(self, event: events.Key | None = None) -> None:
        logger.debug(
                "alias key_pageup invoked (alt): key=%r index=%r nodes=%r",
                getattr(event, "key", None),
                self.index,
                len(self.nodes()),
            )
        return self.key_page_up(event)

    def key_pagedown(self, event: events.Key | None = None) -> None:
        logger.debug(
                "alias key_pagedown invoked (alt): key=%r index=%r nodes=%r",
                getattr(event, "key", None),
                self.index,
                len(self.nodes()),
            )
        return self.key_page_down(event)

    def key_prior(self, event: events.Key | None = None) -> None:
        # 'prior' is sometimes used for PageUp
        return self.key_page_up(event)

    def key_next(self, event: events.Key | None = None) -> None:
        # 'next' is sometimes used for PageDown
        return self.key_page_down(event)

    def on_key(self, event: events.Key) -> None:
        """Lightweight debug logger to surface which key names arrive.

        Only logs keys that look like page/scroll keys to avoid noise.
        """
        try:
            k = getattr(event, "key", None)
            if not k:
                return
            if "page" in k or k in ("prior", "next"):
                logger.debug("on_key: widget=%s key=%r", type(self).__name__, k)
        except Exception as e:
            self.printException(e, "on_key failed")

    def key_home(self, event: events.Key | None = None) -> None:
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
    def key_left(self) -> None:
        return None

    def key_right(self) -> None:
        return None

    def key_enter(self) -> None:
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

    def _highlight_filename(self, filename: str) -> None:
        """Find the first node matching `filename` and move the index there."""
        try:
            nodes = self.nodes()
            for i, node in enumerate(nodes):
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
        try:
            logger.debug("list view highlighted: %s", event)
        except Exception as e:
            self.printException(e, "FileListBase.on_list_view_highlighted failed")

    def _child_filename(self, node) -> str:
        try:
            return self.text_of(node)
        except Exception as e:
            return str(node)

    def _enter_directory(self, filename: str) -> None:
        # Default: log and do nothing. Subclasses should override to change mode.
        try:
            logger.debug("enter directory requested: %s", filename)
        except Exception as e:
            self.printException(e, "FileListBase._enter_directory failed")


class FileModeFileList(FileListBase):
    """File-mode file list: shows files for a working tree path.

    For regen Step 3 this class provides a `prepFileModeFileList` stub and
    default `key_left`/`key_right` handlers.
    """

    def prepFileModeFileList(self, path: str, highlight_filename: str | None = None) -> None:
        try:
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
            except Exception:
                self.printException(e, "prepFileModeFileList: realpath failed")
                # If realpath fails for some reason, keep the original path
                pass

            # Record the list's path
            self.path = path
            # Keep the application's canonical current path in sync with
            # this widget's directory so other components can rely on it.
            self.app.current_path = self.path
            relpath = path[len(self.app.repo_root)+1 :]
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
                    if self.app.repo_root and path.startswith(self.app.repo_root):
                        prefix = os.path.relpath(path, self.app.repo_root)
                        if prefix == ".":
                            prefix = ""
                        else:
                            prefix = prefix + os.sep
                        try:
                            out = subprocess.check_output(
                                ["git", "-C", self.app.repo_root, "status", "--porcelain"],
                                text=True,
                                stderr=subprocess.DEVNULL,
                            )
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
                                        logger.debug("prepFileModeFileList: skipping file %s as it does not start with prefix %s", name, prefix)
                                        continue
                                    rel = name[len(prefix):]
                                else:
                                    rel = name
                                m[rel] = code
                            status_map = m
                        except Exception:
                            self.printException(e, "prepFileModeFileList: git status subprocess failed")
            except Exception:
                self.printException(e, "prepFileModeFileList: building status_map failed")
                status_map = None

            # clear and populate
            self.clear()

            try:
                entries = sorted(os.listdir(path))
            except Exception as e:
                self.printException(e, f"Error reading {path}")
                try:
                    self.append(ListItem(Label(Text(f"Error reading {path}: {e}", style="red"))))
                except Exception as e:
                    self.printException(e)
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
                    parent_item = ListItem(Label(Text(f"← ..", style="white on blue")))
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

            for name in entries:
                logger.debug("prepFileModeFileList: processing entry %s", name)
                if name == ".git":
                    continue
                try:
                    full = os.path.join(path, name)
                    logger.debug("prepFileModeFileList: full path %s", full)

                    # Directories: show arrow tag and trailing slash
                    if os.path.isdir(full):
                        tag = "→"
                        display_name = f"{name}/"
                        display = f"{tag} {display_name}"
                        style = "white on blue"
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

                    # Files: determine repo status and marker
                    repo_status = None
                    style = None
                    try:
                        repo_status = None
                        style = None
                        # Compute rel by slicing the canonical repo root from full.
                        rel = full[len(self.app.repo_root) :]
                        if rel.startswith(os.sep):
                            rel = rel[1:]
                        rel = os.path.normpath(rel) if rel else rel
                        logger.debug("prepFileModeFileList: rel path %s", rel)

                        if rel:
                            # Try pygit2 fast-path first (single-file query). If
                            # unavailable or it fails, fall back to git CLI.
                            try:
                                if self.app.pygit2_repo:
                                    try:
                                        flags = self.app.pygit2_repo.status_file(rel)
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
                                    except Exception as e:
                                        self.printException(e, "pygit2 status_file failed")
                                        repo_status = None
                            except Exception:
                                repo_status = None

                            if repo_status is None:
                                # If we built a batch status_map earlier, prefer it
                                # to avoid per-file `git status` calls.
                                if status_map is not None:
                                    try:
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
                                        else:
                                            # Not present in status_map: check tracked via ls-files
                                            try:
                                                subprocess.check_call(
                                                    ["git", "-C", self.app.repo_root, "ls-files", "--error-unmatch", rel],
                                                    stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL,
                                                )
                                                repo_status = "tracked_clean"
                                            except subprocess.CalledProcessError:
                                                repo_status = "untracked"
                                    except Exception as e:
                                        self.printException(e, "status_map processing failed")
                                        repo_status = "tracked_clean"
                                else:
                                    # No batch map: fall back to per-file git status
                                    try:
                                        cmd = ["git", "-C", self.app.repo_root, "status", "--porcelain", "--", rel]
                                        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
                                        if out:
                                            code = out[:2]
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
                                        else:
                                            try:
                                                subprocess.check_call(
                                                    ["git", "-C", self.app.repo_root, "ls-files", "--error-unmatch", rel],
                                                    stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL,
                                                )
                                                repo_status = "tracked_clean"
                                            except subprocess.CalledProcessError:
                                                repo_status = "untracked"
                                    except Exception as e:
                                        self.printException(e, "git status check failed")
                                        repo_status = "tracked_clean"
                        else:
                            # If not inside repo root, treat as untracked for safety
                            repo_status = "untracked"
                    except Exception as e:
                        self.printException(e, "determining repo status failed")
                        repo_status = None

                    logger.debug("prepFileModeFileList: file %s repo_status=%s", name, repo_status) 

                    # Map repo_status to marker and style
                    try:
                        marker = MARKERS.get(repo_status, " ")
                        if repo_status == "conflicted":
                            style = "magenta"
                        elif repo_status == "staged":
                            style = "cyan"
                        elif repo_status == "wt_deleted":
                            style = "red"
                        elif repo_status == "ignored":
                            style = "dim italic"
                        elif repo_status == "modified":
                            style = "yellow"
                        elif repo_status == "untracked":
                            style = "bold yellow"
                        else:
                            style = "white"
                    except Exception as e:
                        self.printException(e, "mapping repo_status to marker and style failed")
                        marker = " "
                        style = None

                    # Debug: log final decision for this file
                    # Existence heuristic: treat anything not explicitly 'untracked' as existing
                    logger.debug(
                            "prepFileModeFileList: name=%s rel=%r repo_status=%r marker=%r style=%r",
                            name,
                            rel,
                            repo_status,
                            marker,
                            style,
                        )


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

            try:
                self._populated = True
                self._filename = path
                if hl:
                    try:
                        self.call_after_refresh(lambda: self._highlight_match(hl))
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

    def _activate_or_open(self, event: events.Key | None = None, enter_dir_test_fn=lambda name: True) -> None:
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
                        self.prepFileModeFileList(raw)
                    except Exception as e:
                        self.printException(e, "FileModeFileList._activate_or_open prep failed")
                return

            # File selected: open repo view for tracked files only
            if raw and repo_status not in ("untracked", "ignored"):
                try:
                    self.app.file_mode_history_list.prepFileModeHistoryList(raw)
                    try:
                        # Switch UI to file-history layout and focus
                        self.app.change_state("file_history", RIGHT_FILE_TITLE, RIGHT_FILE_FOOTER)
                    except Exception as e:
                        self.printException(e, "FileModeFileList._activate_or_open change_state failed")
                except Exception as e:
                    self.printException(e, "FileModeFileList._activate_or_open repo open failed")
        except Exception as e:
            self.printException(e, "FileModeFileList._activate_or_open failed")

    def key_left(self, event: events.Key | None = None) -> None:
        # Navigate up only when the selected directory is the parent entry ('..')
        # Use shared helper so event.stop() is honored and behavior is unified.
        self._activate_or_open(event, enter_dir_test_fn=lambda name: name == "..")

    def key_right(self, event: events.Key | None = None) -> None:
        # Use shared helper to handle directory enter or file open.
        self._activate_or_open(event, enter_dir_test_fn=lambda name: (name is not None) and name != "..")

    def key_enter(self, event: events.Key | None = None) -> None:
        # Enter key: enter directories or open file history for tracked files.
        self._activate_or_open(event, enter_dir_test_fn=lambda name: True)


class RepoModeFileList(FileListBase):
    """Repo-mode file list: shows files changed between commits.

    Provides a `prepRepoModeFileList` stub and navigation handlers.
    """

    def prepRepoModeFileList(self, prev_hash: str | None, curr_hash: str | None) -> None:
        try:
            self.clear()
            if not self.app.repo_root:
                # nothing to show; fallback to empty synthetic list
                self._populated = True
                self._highlight_top()
                return

            # Build git diff command to list changed files between the two refs
            try:
                cmd = ["git", "-C", self.app.repo_root, "diff", "--name-status"]
                if prev_hash and curr_hash:
                    cmd += [prev_hash, curr_hash]
                elif curr_hash:
                    # diff against working tree (curr only) or HEAD
                    cmd += [curr_hash]
                # run command
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                for ln in out.splitlines():
                    if not ln:
                        continue
                    try:
                        parts = ln.split("\t", 1)
                        status = parts[0]
                        path = parts[1] if len(parts) > 1 else parts[0]
                        display = f"{status} {path}"
                        item = ListItem(Label(Text(display)))
                        try:
                            item._raw_text = path
                            item._is_dir = False
                            self.append(item)
                        except Exception as e:
                            self.printException(e, "prepRepoModeFileList append failed")
                    except Exception as e:
                        self.printException(e, "prepRepoModeFileList parsing line failed")
            except subprocess.CalledProcessError:
                # Fallback: no diff output
                pass
            except Exception as e:
                self.printException(e, "prepRepoModeFileList git diff failed")

            self._populated = True
            # Highlight based on provided hashes (prefer curr_hash)
            try:
                target = curr_hash or prev_hash
                if target:
                    self._highlight_match(target)
                else:
                    self._highlight_top()
            except Exception as e:
                self.printException(e, "prepRepoModeFileList: highlight failed")
        except Exception as e:
            self.printException(e, "prepRepoModeFileList failed")

    def key_left(self) -> None:
        # Move to previous view or update state in main app (stub)
        return None

    def key_right(self) -> None:
        # Open diff view for selected file (stub)
        try:
            idx = self.index or 0
            nodes = self.nodes()
            if 0 <= idx < len(nodes):
                filename = self._child_filename(nodes[idx])
                logger.debug("RepoModeFileList open diff for %s", filename)
        except Exception as e:
            self.printException(e, "RepoModeFileList.key_right failed")


class HistoryListBase(AppBase):
    """Base for history (commit) lists.

    Provides helpers to attach metadata to rows and compute commit-pair hashes.
    """

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
        try:
            logger.debug("history highlighted: %s", event)
        except Exception as e:
            self.printException(e, "HistoryListBase.on_list_view_highlighted failed")


class FileModeHistoryList(HistoryListBase):
    """History list for a single file's history. Stubbed prep method."""

    def prepFileModeHistoryList(self, path: str) -> None:
        try:
            self.clear()
            # If repository available, call git log --follow for the path
            repo_root = self.app.repo_root
            if repo_root:
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
                        path,
                    ]
                    out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                    for ln in out.splitlines():
                        try:
                            parts = ln.split("\t", 2)
                            h = parts[0]
                            text = parts[1] + " " + (parts[2] if len(parts) > 2 else "")
                            self._add_row(text, h)
                        except Exception as e:
                            self.printException(e, "prepFileModeHistoryList parse failed")
                except subprocess.CalledProcessError:
                    # no history or git failed; fall back to synthetic
                    pass
                except Exception as e:
                    self.printException(e, "prepFileModeHistoryList git log failed")

            self._populated = True
            # Default to highlighting top commit for file history
            try:
                self._highlight_top()
            except Exception as e:
                self.printException(e, "prepFileModeHistoryList: highlight failed")
        except Exception as e:
            self.printException(e, "prepFileModeHistoryList failed")


class RepoModeHistoryList(HistoryListBase):
    """History list for repository-wide commits. Stubbed prep method."""

    def prepRepoModeHistoryList(self, repo_path: str | None = None) -> None:
        try:
            self.clear()
            # Use git log to populate repo-wide history when possible
            if self.app.repo_root:
                try:
                    cmd = ["git", "-C", self.app.repo_root, "log", "--pretty=format:%H\t%ad\t%s", "--date=short", "-n", "200"]
                    out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                    for ln in out.splitlines():
                        if not ln:
                            continue
                        try:
                            parts = ln.split("\t", 2)
                            commit_hash = parts[0]
                            date_stamp = parts[1] if len(parts) > 1 else ""
                            msg = parts[2] if len(parts) > 2 else ""
                            short_hash = commit_hash[:12]
                            text = f"{date_stamp} {short_hash} {msg}"
                            self._add_row(text, commit_hash)
                        except Exception as e:
                            self.printException(e, "prepRepoModeHistoryList parse failed")
                except subprocess.CalledProcessError:
                    pass
                except Exception as e:
                    self.printException(e, "prepRepoModeHistoryList git log failed")
            self._populated = True
            # Default to highlighting top commit for repo history
            try:
                self._highlight_top()
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: highlight failed")
        except Exception as e:
            self.printException(e, "prepRepoModeHistoryList failed")


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

    def prepDiffList(self, filename: str, prev: str | None, curr: str | None, variant_index: int | None = None) -> None:
        try:
            self.clear()
            # Prefer the canonicalized `current_path` on the app when available
            cmd = None
            try:
                try:
                    cmd = self.app.build_diff_cmd(filename, prev, curr, variant_index)
                except Exception as e:
                    self.printException(e, "prepDiffList: building diff command failed")
                    # fallback basic diff
                    if self.app.repo_root:
                        cmd = ["git", "-C", self.app.repo_root, "diff"]
                    else:
                        cmd = ["git", "diff"]
                    if prev and curr:
                        cmd += [prev, curr]
                    if filename:
                        cmd += ["--", filename]
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                out = ""
            except Exception as e:
                self.printException(e, "prepDiffList: running git diff failed")
                out = ""

            # Colorize lines by prefix and append to list
            try:
                for ln in out.splitlines() or []:
                    try:
                        style = None
                        if ln.startswith("+") and not ln.startswith("+++"):
                            style = "green"
                        elif ln.startswith("-") and not ln.startswith("---"):
                            style = "red"
                        elif ln.startswith("@@"):
                            style = "magenta"
                        elif ln.startswith("diff --git") or ln.startswith("index "):
                            style = "bold white"
                        else:
                            style = None
                        if style:
                            self.append(ListItem(Label(Text(ln, style=style))))
                        else:
                            self.append(ListItem(Label(Text(ln))))
                    except Exception as e:
                        self.printException(e, "prepDiffList append failed")
            except Exception as e:
                self.printException(e, "prepDiffList processing output failed")

            self._populated = True
            self._filename = filename
            try:
                self._highlight_top()
            except Exception as e:
                self.printException(e, "prepDiffList: highlight failed")
        except Exception as e:
            self.printException(e, "prepDiffList failed")

    def key_c(self) -> None:
        try:
            self._colorized = not self._colorized
            logger.debug("DiffList colorized=%s", self._colorized)
            # Re-render could be done here; stubbed for now
        except Exception as e:
            self.printException(e, "DiffList.key_c failed")

    def key_d(self) -> None:
        try:
            logger.debug("DiffList.key_d: save diff for %s", self._filename)
        except Exception as e:
            self.printException(e, "DiffList.key_d failed")

    def key_f(self) -> None:
        try:
            logger.debug("DiffList.key_f: find in diff")
        except Exception as e:
            self.printException(e, "DiffList.key_f failed")


class HelpList(AppBase):
    """Renders help text as list rows and allows restoring previous state."""

    def prepHelp(self) -> None:
        try:
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

    def key_enter(self) -> None:
        try:
            app = self.app
            try:
                app.restore_state()
            except Exception as e:
                self.printException(e, "HelpList.restore_state failed")
        except Exception as e:
            self.printException(e, "HelpList.key_enter failed")


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
RIGHT_FILE_FOOTER = Text("File history: press Left to return")


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
            self._saved_state = None
            self._current_layout = None
            # Best-effort: cache a pygit2 Repository object to avoid
            # constructing it per-file. If pygit2 isn't available set
            # `pygit2_repo` to None so callers can fall back to CLI.
            self.pygit2_repo = None
            if pygit2:
                try:
                    self.pygit2_repo = pygit2.Repository(self.repo_root)
                except Exception as e:
                    printException(e, "GitHistoryNavTool.__init__: pygit2.Repository init failed")

            # Initialize `_current_path` to either the provided path when
            # it's a directory, or the dirname when `path` is a file.
            # Use the property setter so the value is canonicalized.
            self._current_path = (self.path if os.path.isdir(self.path) else os.path.dirname(self.path))
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

        yield Footer()

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
                # composition must match expected ids
                raise RuntimeError(f"widget resolution failed in on_mount: {e}") from e

            # initial prep placeholders (populate left file list)
            # Start in history_fullscreen when repo_first is True; otherwise
            # start in file_fullscreen. This guarantees consistent initial
            # layouts for both modes and keeps layout logic centralized.
            try:
                if self.repo_first:
                    self.change_layout("history_fullscreen")
                else:
                    self.change_layout("file_fullscreen")
            except Exception as e:
                self.printException(e, "on_mount: change_layout failed")

            # Populate the canonical left lists and set focus so key handlers
            # and highlight behavior work immediately in both modes.
            try:
                if not self.repo_first:
                    try:
                        self.file_mode_file_list.prepFileModeFileList(path=self.path or ".")
                        # ensure the file list has focus so arrow keys affect it
                        try:
                            # `set_focus` is not awaitable in this Textual version.
                            self.set_focus(self.file_mode_file_list)
                        except Exception as e:
                            # Fallback: resolve widget by id and call set_focus
                            try:
                                widget = self.query_one(f"#{LEFT_FILE_LIST_ID}")
                                self.set_focus(widget)
                            except Exception as e2:
                                self.printException(e2, "on_mount: focusing file list failed")
                            self.printException(e, "on_mount: file list focus failed")
                        # ensure top/highlight is active
                        try:
                            self.file_mode_file_list._highlight_top()
                        except Exception as e:
                            self.printException(e, "on_mount: file list highlight failed")
                    except Exception as e:
                        self.printException(e, "on_mount: prepFileModeFileList failed")
                else:
                    # If starting in repo-first mode, pre-populate the left
                    # repository-history widget so the UI shows commits immediately.
                    try:
                        self.repo_mode_history_list.prepRepoModeHistoryList(repo_path=self.path or ".")
                        # focus the history list so key navigation works
                        try:
                            # Call non-awaitable set_focus
                            self.set_focus(self.repo_mode_history_list)
                        except Exception as e:
                            try:
                                widget = self.query_one(f"#{LEFT_HISTORY_LIST_ID}")
                                self.set_focus(widget)
                            except Exception as e2:
                                self.printException(e2, "on_mount: focusing repo history failed")
                            self.printException(e, "on_mount: repo history focus failed")
                        try:
                            self.repo_mode_history_list._highlight_top()
                        except Exception as e:
                            self.printException(e, "on_mount: repo history highlight failed")
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
        return self.key_q(event)

    def key_h(self, event: events.Key | None = None) -> None:
        """Show help: save state, prepare help, then display help fullscreen.

        This records the single-slot state, ensures help content is prepared,
        and switches layout/focus/footer to the help configuration.
        """
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
                footer_txt = Text("Help: press Enter to return")
                self.change_state("help_fullscreen", f"#{HELP_LIST_ID}", footer_txt)
            except Exception as e:
                self.printException(e, "key_h: change_state failed")
        except Exception as e:
            self.printException(e, "key_h outer failure")

    def key_H(self, event: events.Key | None = None) -> None:
        return self.key_h(event)

    def key_question(self, event: events.Key | None = None) -> None:
        # Some terminals map '?' to 'question'
        return self.key_h(event)


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

    def build_diff_cmd(
        self, filename: str | None, prev: str | None, curr: str | None, variant_index: int | None = None
    ) -> list[str]:
        """Return a git diff command list for the given filenames and commit-ish pair.

        This is a small helper used by `DiffList.prepDiffList` to centralize
        how diffs are constructed. It returns an argv list suitable for
        `subprocess.check_output`.
        """
        try:
            repo_root = self.repo_root
            if repo_root:
                cmd = ["git", "-C", repo_root, "diff"]
            else:
                cmd = ["git", "diff"]

            # If both refs provided, diff between them; if only curr provided,
            # diff that ref against working tree; if neither provided, diff
            # working tree against HEAD.
            if prev and curr:
                cmd += [prev, curr]
            elif curr and not prev:
                cmd += [curr]

            if filename:
                cmd += ["--", filename]
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
                    logger.warning(f"change_focus: unknown canonical focus target {target}")
                    return

                try:
                    if widget is not None:
                        try:
                            self.set_focus(widget)
                        except Exception as e:
                            self.printException(e, f"could not set focus to widget for {target}")
                            # Fallback: resolve widget by id and call set_focus
                            try:
                                widget.focus()
                            except Exception as e:
                                self.printException(e, f"could not fallback focus to widget for {target}")
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


def discover_repo_worktree(start_path: str | None) -> str:
    """Discover the repository worktree root starting at `start_path`.

    Uses `pygit2.discover_repository` when available; otherwise uses
    git -C <start_path> rev-parse --show-toplevel to find the worktree root.
    If no repository is found this function exits the program with an error message.
    """
    try:
        start = os.path.abspath(start_path or os.getcwd())
    except Exception:
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
        topo = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
        if topo:
            try:
                worktree = os.path.realpath(topo)
            except Exception:
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
    parser.add_argument("-C", "--no-color", dest="no_color", action="store_true", help="start with diff colorization off")
    parser.add_argument("-r", "--repo-first", dest="repo_first", action="store_true", help="start in repo-first mode")
    parser.add_argument(
        "-d", "--debug", dest="debug", metavar="FILE", help="write debug log to FILE (enables debug logging)"
    )
    parser.add_argument("-P", "--no-pygit2", dest="no_pygit2", action="store_true", help="disable pygit2 usage even if installed")
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
