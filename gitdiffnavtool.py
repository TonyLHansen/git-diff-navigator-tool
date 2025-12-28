#!/usr/bin/env python3
"""
gitdiffnavtool - regenerated scaffold (step 1 only)

This file is a minimal scaffold created by the regen plan. Subsequent
steps will fill in the concrete classes and behavior.
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from typing import Optional

# --- Constants -------------------------------------------------------------
# Highlight constants (defaults)
HIGHLIGHT_FILELIST_BG = "#f1c40f"
HIGHLIGHT_FILELIST_STYLE = f"white on {HIGHLIGHT_FILELIST_BG}"

HIGHLIGHT_REPOLIST_BG = "#44475a"
HIGHLIGHT_REPOLIST_STYLE = f"white on {HIGHLIGHT_REPOLIST_BG}"

# Enable debug logging to tmp/debug.log when True
DOLOGGING = False

from rich.text import Text
from textual import events
from textual.widgets import ListView, Label, ListItem
from textual.widgets import ListItem


# --- Logging setup --------------------------------------------------------
if DOLOGGING:
    logging.basicConfig(
        filename="tmp/debug.log",
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

logger = logging.getLogger(__name__)


def printException(e: Exception, msg: Optional[str] = None) -> None:
    """Module-level helper to log unexpected exceptions when `self` isn't available.

    This mirrors the widget-level `printException` helper used by widgets.
    """
    try:
        short_msg = msg or ""
        logger.warning("%s: %s", short_msg, e)
        logger.warning(traceback.format_exc())
    except Exception:
        # Last-resort fallback to stderr
        try:
            sys.stderr.write(f"printException fallback: {e}\n")
        except Exception:
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

    def printException(self, e: Exception, msg: Optional[str] = None) -> None:
        try:
            className = type(self).__name__
            funcName = sys._getframe(1).f_code.co_name
            short = msg or ""
            logger.warning(f"{className}.{funcName}: {short} - {e}")
            logger.warning(traceback.format_exc())
        except Exception:
            # Fall back to module-level printer
            printException(e, msg)

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
            try:
                self.printException(e, "extracting text")
            except Exception:
                logger.debug("text_of fallback failed: %s", e)
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
            try:
                self.printException(e, "extracting label text")
            except Exception:
                logger.debug("_extract_label_text fallback failed: %s", e)
            return str(lbl)

    # Key handlers: prefer `key_` methods on widgets instead of an `on_key` dispatcher.
    # Implement navigation handlers as `key_*` methods so subclasses may override
    # them individually and keep key logic co-located with widget state.

    def key_up(self) -> None:
        try:
            min_idx = getattr(self, "_min_index", 0) or 0
            cur = self.index
            if cur is None:
                try:
                    self.index = min_idx
                except Exception as e:
                    self.printException(e, "setting index on key_up")
                return
            if cur <= min_idx:
                return
            try:
                self.action_cursor_up()
            except Exception as e:
                self.printException(e, "cursor up failed")
        except Exception as e:
            self.printException(e, "key_up outer failure")

    def key_down(self) -> None:
        try:
            try:
                self.action_cursor_down()
            except Exception as e:
                self.printException(e, "cursor down failed")
        except Exception as e:
            self.printException(e, "key_down outer failure")

    def key_page_down(self) -> None:
        try:
            nodes = self._nodes
            if not nodes:
                return
            current_index = self.index or 0
            try:
                region = self.scrollable_content_region
                visible_height = int(getattr(region, "height", 10))
            except Exception:
                visible_height = 10
            page_size = max(1, visible_height // 2)
            new_index = min(current_index + page_size, len(nodes) - 1)
            try:
                self.call_after_refresh(lambda: setattr(self, "index", new_index))
            except Exception:
                try:
                    self.index = new_index
                except Exception as e:
                    self.printException(e, "setting index for page down")
        except Exception as e:
            self.printException(e, "key_page_down failed")

    def key_page_up(self) -> None:
        try:
            nodes = self._nodes
            if not nodes:
                return
            current_index = self.index or 0
            try:
                region = self.scrollable_content_region
                visible_height = int(getattr(region, "height", 10))
            except Exception:
                visible_height = 10
            page_size = max(1, visible_height // 2)
            min_idx = getattr(self, "_min_index", 0) or 0
            new_index = max(current_index - page_size, min_idx)
            try:
                self.call_after_refresh(lambda: setattr(self, "index", new_index))
            except Exception:
                try:
                    self.index = new_index
                except Exception as e:
                    self.printException(e, "setting index for page up")
        except Exception as e:
            self.printException(e, "key_page_up failed")

    def key_home(self) -> None:
        try:
            min_idx = getattr(self, "_min_index", 0) or 0
            try:
                self.call_after_refresh(lambda: setattr(self, "index", min_idx))
            except Exception:
                try:
                    self.index = min_idx
                except Exception as e:
                    self.printException(e, "home key set index failed")
        except Exception as e:
            self.printException(e, "key_home failed")

    def key_end(self) -> None:
        try:
            nodes = self._nodes
            if not nodes:
                return
            last_idx = max(0, len(nodes) - 1)
            try:
                self.call_after_refresh(lambda: setattr(self, "index", last_idx))
            except Exception:
                try:
                    self.index = last_idx
                except Exception as e:
                    self.printException(e, "end key set index failed")
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
                self.index = getattr(self, "_min_index", 0) or 0
        except Exception as e:
            self.printException(e, "FileListBase.on_focus")

    def _highlight_filename(self, filename: str) -> None:
        """Find the first node matching `filename` and move the index there."""
        try:
            nodes = getattr(self, "_nodes", None) or []
            for i, node in enumerate(nodes):
                try:
                    text = self.text_of(node)
                except Exception:
                    text = str(node)
                if text == filename:
                    try:
                        self.call_after_refresh(lambda: setattr(self, "index", i))
                    except Exception:
                        try:
                            self.index = i
                        except Exception as e:
                            self.printException(e, "setting index in _highlight_filename")
                    return
        except Exception as e:
            self.printException(e, "_highlight_filename failed")

    def _highlight_top(self) -> None:
        try:
            self.call_after_refresh(lambda: setattr(self, "index", getattr(self, "_min_index", 0) or 0))
        except Exception:
            try:
                self.index = getattr(self, "_min_index", 0) or 0
            except Exception as e:
                self.printException(e, "_highlight_top failed")

    def watch_index(self, old, new) -> None:
        # Placeholder watch — concrete subclasses may override
        try:
            logger.debug("FileListBase index changed %r -> %r", old, new)
        except Exception:
            pass

    def on_list_view_highlighted(self, event) -> None:
        # Textual-specific hook placeholder for when highlighting changes.
        try:
            logger.debug("list view highlighted: %s", event)
        except Exception:
            pass

    def _child_filename(self, node) -> str:
        try:
            return self.text_of(node)
        except Exception:
            return str(node)

    def _enter_directory(self, filename: str) -> None:
        # Default: log and do nothing. Subclasses should override to change mode.
        try:
            logger.debug("enter directory requested: %s", filename)
        except Exception:
            pass


class FileModeFileList(FileListBase):
    """File-mode file list: shows files for a working tree path.

    For regen Step 3 this class provides a `prepFileModeFileList` stub and
    default `key_left`/`key_right` handlers.
    """

    def prepFileModeFileList(self, path: str) -> None:
        try:
            self.clear()
            # Stubbed content: in later steps this will enumerate files.
            items = [
                ListItem(Label(Text(f"{path}/file1.txt"))),
                ListItem(Label(Text(f"{path}/file2.py"))),
            ]
            for it in items:
                try:
                    self.append(it)
                except Exception:
                    pass
            self._populated = True
            self._filename = path
        except Exception as e:
            self.printException(e, "prepFileModeFileList failed")

    def key_left(self) -> None:
        # In file mode, left could go up a directory — leave as stub.
        return None

    def key_right(self) -> None:
        # Enter directory or open history for the selected file.
        try:
            idx = self.index or 0
            nodes = getattr(self, "_nodes", None) or []
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
            items = [
                ListItem(Label(Text(f"changed_file1.py ({prev_hash[:7] if prev_hash else 'prev'})"))),
                ListItem(Label(Text(f"changed_file2.md ({curr_hash[:7] if curr_hash else 'curr'})"))),
            ]
            for it in items:
                try:
                    self.append(it)
                except Exception:
                    pass
            self._populated = True
        except Exception as e:
            self.printException(e, "prepRepoModeFileList failed")

    def key_left(self) -> None:
        # Move to previous view or update state in main app (stub)
        return None

    def key_right(self) -> None:
        # Open diff view for selected file (stub)
        try:
            idx = self.index or 0
            nodes = getattr(self, "_nodes", None) or []
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
            except Exception:
                pass
        except Exception as e:
            self.printException(e, "HistoryListBase._add_row failed")

    def toggle_check_current(self, idx: int | None = None) -> None:
        try:
            if idx is None:
                idx = self.index or 0
            nodes = getattr(self, "_nodes", None) or []
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
            except Exception:
                pass
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
                self.index = 0
        except Exception as e:
            self.printException(e, "HistoryListBase.on_focus")

    def on_list_view_highlighted(self, event) -> None:
        try:
            logger.debug("history highlighted: %s", event)
        except Exception:
            pass


class FileModeHistoryList(HistoryListBase):
    """History list for a single file's history. Stubbed prep method."""

    def prepFileModeHistoryList(self, path: str) -> None:
        try:
            self.clear()
            # Stubbed example commits
            self._add_row(f"commit 1 - fix bug in {path}", "aaaaaaaaaaaaaaaaaaaa")
            self._add_row(f"commit 2 - add feature to {path}", "bbbbbbbbbbbbbbbbbbbb")
            self._add_row(f"commit 3 - initial {path}", "cccccccccccccccccccc")
            self._populated = True
        except Exception as e:
            self.printException(e, "prepFileModeHistoryList failed")


class RepoModeHistoryList(HistoryListBase):
    """History list for repository-wide commits. Stubbed prep method."""

    def prepRepoModeHistoryList(self, repo_path: str | None = None) -> None:
        try:
            self.clear()
            self._add_row("repo commit 1 - overhaul", "11111111111111111111")
            self._add_row("repo commit 2 - cleanup", "22222222222222222222")
            self._add_row("repo commit 3 - init", "33333333333333333333")
            self._populated = True
        except Exception as e:
            self.printException(e, "prepRepoModeHistoryList failed")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gitdiffnavtool.py")
    p.add_argument("path", nargs="?", default=".", help="directory or file to open")
    p.add_argument("-C", "--no-color", dest="no_color", action="store_true", help="start with diff colorization off")
    p.add_argument("-r", "--repo-first", dest="repo_first", action="store_true", help="start in repo-first mode")
    return p


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
                except Exception:
                    pass
            self._populated = True
            self._filename = filename
        except Exception as e:
            self.printException(e, "prepDiffList failed")

    def key_c(self) -> None:
        try:
            self._colorized = not getattr(self, "_colorized", True)
            logger.debug("DiffList colorized=%s", self._colorized)
            # Re-render could be done here; stubbed for now
        except Exception as e:
            self.printException(e, "DiffList.key_c failed")

    def key_d(self) -> None:
        try:
            logger.debug("DiffList.key_d: save diff for %s", getattr(self, "_filename", None))
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
                except Exception:
                    pass
            self._populated = True
        except Exception as e:
            self.printException(e, "prepHelp failed")

    def key_enter(self) -> None:
        try:
            app = getattr(self, "app", None)
            if app and hasattr(app, "restore_state"):
                try:
                    app.restore_state()
                except Exception as e:
                    self.printException(e, "HelpList.restore_state failed")
        except Exception as e:
            self.printException(e, "HelpList.key_enter failed")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # For step 1 we only validate CLI wiring and logging setup.
    logger.debug("Starting scaffold main; args=%s", args)
    print("gitdiffnavtool scaffold (step 1) - parsed args:")
    print(f"  path={args.path!r}")
    print(f"  no_color={args.no_color}")
    print(f"  repo_first={args.repo_first}")

    # future steps will construct and run the Textual App here
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        printException(e, "fatal error in gitdiffnavtool")
        raise
