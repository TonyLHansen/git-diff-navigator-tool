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
import traceback
from typing import Optional

# --- Constants -------------------------------------------------------------
# Highlight constants (defaults)
HIGHLIGHT_FILELIST_BG = "#f1c40f"
HIGHLIGHT_FILELIST_STYLE = f"white on {HIGHLIGHT_FILELIST_BG}"

HIGHLIGHT_REPOLIST_BG = "#3333CC"
HIGHLIGHT_REPOLIST_STYLE = f"white on {HIGHLIGHT_REPOLIST_BG}"


# Optional pygit2 support — best-effort import to enable repo status checks
try:
    import pygit2
except Exception:
    pygit2 = None

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
from rich.text import Text
from textual import events
from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.widgets import ListView, Label, ListItem, Footer, Header


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
        try:
            sys.stderr.write(f"printException fallback: {e}\n")
            sys.stderr.write(f"secondary exception: {e2}\n")
        except Exception:
            # If even stderr fails, give up quietly
            pass

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
            n = getattr(self, "children", None)
            return n if n else []
        except Exception:
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
                is_repo_mode = (
                    isinstance(self, RepoModeFileList) or isinstance(self, RepoModeHistoryList)
                )
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
                is_repo_mode = (
                    isinstance(self, RepoModeFileList) or isinstance(self, RepoModeHistoryList)
                )
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
                        logger.debug("watch_index: applied highlight to new index %s text=%s", new, self.text_of(node_new))
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
                        except Exception:
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
                                self.call_after_refresh(lambda: getattr(node_new, "scroll_visible", lambda *a, **k: None)(True))
                            except Exception as e:
                                self.printException(e, "watch_index: node_new.scroll_visible failed")
                        # Reset the page_scroll flag after scheduling
                        try:
                            if self._page_scroll:
                                self._page_scroll = False
                        except Exception:
                            pass
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
            except Exception:
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
            logger.debug("key_page_down invoked: index=%r nodes=%r", getattr(self, 'index', None), len(self.nodes()))
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
                except Exception:
                    pass
                self._activate_index(new_index)
            except Exception as e:
                self.printException(e, "key_page_down: activate failed")
        except Exception as e:
            self.printException(e, "key_page_down failed")

    # Alias handlers: terminals/terminfo may report different key names for
    # page up / page down (e.g. 'pageup', 'pagedown', 'prior', 'next'). Provide
    # aliases that delegate to the canonical handlers so keys are handled.
    def key_pageup(self, event: events.Key | None = None) -> None:
        try:
            logger.debug("alias key_pageup invoked: key=%r index=%r nodes=%r", getattr(event, 'key', None), getattr(self, 'index', None), len(self.nodes()))
        except Exception:
            pass
        return self.key_page_down(event)

    def key_pagedown(self, event: events.Key | None = None) -> None:
        try:
            logger.debug("alias key_pagedown invoked: key=%r index=%r nodes=%r", getattr(event, 'key', None), getattr(self, 'index', None), len(self.nodes()))
        except Exception:
            pass
        return self.key_page_down(event)

    def key_page_up(self, event: events.Key | None = None) -> None:
        try:
            logger.debug("key_page_up invoked: index=%r nodes=%r", getattr(self, 'index', None), len(self.nodes()))
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
                except Exception:
                    pass
                self._activate_index(new_index)
            except Exception as e:
                self.printException(e, "key_page_up: activate failed")
        except Exception as e:
            self.printException(e, "key_page_up failed")

    def key_pageup(self, event: events.Key | None = None) -> None:
        try:
            logger.debug("alias key_pageup invoked (alt): key=%r index=%r nodes=%r", getattr(event, 'key', None), getattr(self, 'index', None), len(self.nodes()))
        except Exception:
            pass
        return self.key_page_up(event)

    def key_pagedown(self, event: events.Key | None = None) -> None:
        try:
            logger.debug("alias key_pagedown invoked (alt): key=%r index=%r nodes=%r", getattr(event, 'key', None), getattr(self, 'index', None), len(self.nodes()))
        except Exception:
            pass
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

    def prepFileModeFileList(self, path: str) -> None:
        try:
            # Canonicalize path and allow callers to pass a file to highlight
            path = os.path.abspath(path)
            if os.path.isfile(path):
                hl = os.path.basename(path)
                path = os.path.dirname(path) or "."
            else:
                hl = None

            self.path = path
            try:
                if hasattr(self, "app"):
                    try:
                        self.app.displayed_path = self.path
                    except Exception:
                        pass
            except Exception:
                pass

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

            # clear and populate
            self.clear()

            # Optionally add a parent entry when appropriate
            try:
                parent = os.path.dirname(path)
                if parent and parent != path:
                    parent_item = ListItem(Label(Text(f"← ..", style="white on blue")))
                    try:
                        parent_item._filename = ".."
                        parent_item._is_dir = True
                        parent_item._raw_text = os.path.join(parent, "..")
                    except Exception:
                        pass
                    try:
                        self.append(parent_item)
                    except Exception as e:
                        self.printException(e, "prepFileModeFileList: append parent failed")
            except Exception:
                pass

            for name in entries:
                if name == ".git":
                    continue
                try:
                    full = os.path.join(path, name)
                    is_dir = os.path.isdir(full)

                    # Directories: show arrow tag and trailing slash
                    if is_dir:
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
                            self.append(item)
                        except Exception as e:
                            self.printException(e, "prepFileModeFileList append dir failed")
                        continue

                    # Files: determine repo status and marker
                    repo_status = None
                    style = None
                    try:
                        app = getattr(self, "app", None)
                        if app and getattr(app, "repo_available", False) and getattr(app, "repo_root", None):
                            try:
                                rel = os.path.relpath(full, app.repo_root)
                            except Exception:
                                rel = None
                            if rel and not rel.startswith(".."):
                                flags = app.repo_status_map.get(rel, 0) if getattr(app, "repo_status_map", None) is not None else 0
                                try:
                                    if pygit2 and isinstance(flags, int):
                                        if flags & getattr(pygit2, "GIT_STATUS_CONFLICTED", 0):
                                            repo_status = "conflicted"
                                        elif flags & (
                                            getattr(pygit2, "GIT_STATUS_INDEX_NEW", 0)
                                            | getattr(pygit2, "GIT_STATUS_INDEX_MODIFIED", 0)
                                            | getattr(pygit2, "GIT_STATUS_INDEX_DELETED", 0)
                                        ):
                                            repo_status = "staged"
                                        elif flags & getattr(pygit2, "GIT_STATUS_WT_DELETED", 0):
                                            repo_status = "wt_deleted"
                                        elif flags & getattr(pygit2, "GIT_STATUS_IGNORED", 0):
                                            repo_status = "ignored"
                                        elif flags & (
                                            getattr(pygit2, "GIT_STATUS_WT_MODIFIED", 0)
                                            | getattr(pygit2, "GIT_STATUS_INDEX_MODIFIED", 0)
                                        ):
                                            repo_status = "modified"
                                        elif flags & getattr(pygit2, "GIT_STATUS_WT_NEW", 0):
                                            repo_status = "untracked"
                                        else:
                                            repo_status = "tracked_clean"
                                    else:
                                        # If flags are stored as a status string
                                        if isinstance(flags, str):
                                            repo_status = flags
                                        else:
                                            repo_status = "untracked"
                                except Exception:
                                    repo_status = "untracked"
                            else:
                                repo_status = "untracked"
                        else:
                            repo_status = "untracked"
                    except Exception:
                        repo_status = None

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
                    except Exception:
                        marker = " "
                        style = None

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

    def key_left(self) -> None:
        # In file mode, left could go up a directory — leave as stub.
        return None

    def key_right(self) -> None:
        # Enter directory or open history for the selected file.
        try:
            idx = self.index or 0
            nodes = self.nodes()
            if 0 <= idx < len(nodes):
                filename = self._child_filename(nodes[idx])
                self._enter_directory(filename)
        except Exception as e:
            self.printException(e, "FileModeFileList.key_right failed")


class RepoModeFileList(FileListBase):
    """Repo-mode file list: shows files changed between commits.

    Provides a `prepRepoModeFileList` stub and navigation handlers.
    """

    def prepRepoModeFileList(self, prev_hash: str | None, curr_hash: str | None) -> None:
        try:
            self.clear()
            # Generate many changed files with synthetic commit hashes
            try:
                for i in range(1, 121):
                    hprev = (prev_hash or "pv")[:7]
                    hcurr = (curr_hash or "cr")[:7]
                    name = f"changed_file_{i:04d}.py ({hprev}/{hcurr})"
                    item = ListItem(Label(Text(name)))
                    try:
                        setattr(item, "_raw_text", name)
                        # attach a fake hash for matching tests
                        setattr(item, "_hash", f"{i:040d}" )
                        self.append(item)
                    except Exception as e:
                        self.printException(e, "prepRepoModeFileList append failed")
            except Exception as e:
                self.printException(e, "prepRepoModeFileList generation failed")
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
            item = ListItem(Label(Text(text)))
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
            node = nodes[idx]
            current = getattr(node, "_checked", False)
            setattr(node, "_checked", not current)
            # Optionally mutate label to show a marker
            try:
                lbl = node.query_one(Label)
                text = self._extract_label_text(lbl)
                prefix = "[x] " if not current else "[ ] "
                lbl.update(Text(prefix + text))
            except Exception as e:
                self.printException(e, "HistoryListBase.toggle_check_current label update failed")
        except Exception as e:
            self.printException(e, "toggle_check_current failed")

    def compute_commit_pair_hashes(self, idx: int | None = None) -> tuple[str | None, str | None]:
        try:
            if idx is None:
                idx = self.index or 0
            nodes = getattr(self, "_nodes", None) or []
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
            # Generate many synthetic commits for the file history
            try:
                for i in range(1, 121):
                    h = f"{i:040x}"[-20:]
                    self._add_row(f"commit {i} - update {path}", h)
            except Exception as e:
                self.printException(e, "prepFileModeHistoryList generation failed")
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
            # Generate many synthetic repo commits for testing navigation
            try:
                for i in range(1, 121):
                    h = f"{i:040x}"[-20:]
                    self._add_row(f"repo commit {i} - change", h)
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList generation failed")
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
            # Stubbed example diff lines
            lines = [
                f"diff --git a/{filename} b/{filename}",
                "@@ -1,3 +1,3 @@",
                "-old line",
                "+new line",
                " context line",
            ]
            for ln in lines:
                try:
                    self.append(ListItem(Label(Text(ln))))
                except Exception as e:
                    self.printException(e, "prepDiffList append failed")
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
            if app and hasattr(app, "restore_state"):
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


# (imports consolidated at top)


class GitHistoryNavTool(App):
    """Main Textual application wiring the lists together.

    This is a minimal implementation for regen Step 6: it composes the
    previously defined widgets, mounts a header/footer, and provides simple
    state save/restore stubs and a repo-cache builder.
    """

    CSS = INLINE_CSS

    def __init__(self, path: str = ".", no_color: bool = False, repo_first: bool = False, repo_hashes: list | None = None, **kwargs):
        # Accept CLI options here so the app can inspect them during mount
        super().__init__(**kwargs)
        try:
            self.path = path
            self.no_color = no_color
            self.repo_first = repo_first
            # optional repo hash initialization (list of 1 or 2 hashes)
            self.repo_hashes = repo_hashes or []
            # placeholders for runtime state
            self.repo_cache = None
            self._saved_state = None
            self._current_layout = None
        except Exception as e:
            printException(e, "GitHistoryNavTool.__init__ failed")

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
        except Exception:
            # Fallback to module-level logger
            printException(e, msg)

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
            # Build light repo cache (stub); real discovery in a later step
            self.build_repo_cache()
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
                        rh = getattr(self, "repo_hashes", None) or []
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
                            except Exception:
                                pass
                            try:
                                # If starting in repo-first mode, populate the repo file list
                                # with the specified hashes so the UI reflects them immediately.
                                if self.repo_first:
                                    try:
                                        self.repo_mode_file_list.prepRepoModeFileList(prev, curr)
                                    except Exception:
                                        pass
                            except Exception as e:
                                self.printException(e, "on_mount: initializing repo hashes failed")
                    except Exception as e:
                        self.printException(e, "on_mount: initializing repo hashes failed")
                    
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
                except Exception:
                    pass
            logger.debug("key_q invoked; exiting app")
            try:
                # App.exit() is the Textual API to stop the app.
                self.exit()
            except Exception:
                # Fallback to raising SystemExit
                raise SystemExit(0)
        except Exception as e:
            self.printException(e, "key_q failed")

    def key_Q(self, event: events.Key | None = None) -> None:
        """Uppercase Q also quits."""
        return self.key_q(event)

    def build_repo_cache(self) -> None:
        # Stub: populate lightweight repo metadata for the UI. Later replaced.
        try:
            self.repo_cache = {}
        except Exception as e:
            printException(e, "build_repo_cache failed")

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

    def save_state(self) -> dict:
        try:
            return {"dummy": True}
        except Exception as e:
            printException(e, "save_state failed")
            return {}

    def restore_state(self, state: dict | None = None) -> None:
        try:
            # no-op for now
            return None
        except Exception as e:
            printException(e, "restore_state failed")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gitdiffnavtool.py")
    p.add_argument("path", nargs="?", default=".", help="directory or file to open")
    p.add_argument("-C", "--no-color", dest="no_color", action="store_true", help="start with diff colorization off")
    p.add_argument("-r", "--repo-first", dest="repo_first", action="store_true", help="start in repo-first mode")
    p.add_argument("-d", "--debug", dest="debug", metavar="FILE", help="write debug log to FILE (enables debug logging)")
    p.add_argument(
        "-R",
        "--repo-hash",
        dest="repo_hash",
        action="append",
        metavar="HASH",
        help="specify a repo commit hash; may be provided up to two times (implies --repo-first)",
    )
    return p

def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    # Configure logging if debug file requested
    try:
        if args.debug:
            try:
                os.makedirs(os.path.dirname(args.debug) or "", exist_ok=True)
            except Exception:
                pass
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

        # Wire CLI into the Textual app and run it.
        logger.debug("Starting GitHistoryNavTool; args=%s", args)
        app = GitHistoryNavTool(path=args.path, no_color=args.no_color, repo_first=args.repo_first, repo_hashes=repo_hashes)
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
