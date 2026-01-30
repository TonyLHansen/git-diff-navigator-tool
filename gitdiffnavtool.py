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


class AppException:
    """Mixin providing instance-level exception logging for apps and widgets.

    This centralizes `printException` so multiple base classes can inherit
    it and avoid duplicate implementations.
    """

    def printException(self, e: Exception, msg: Optional[str] = None) -> None:
        """Log an exception with the calling class and function name.

        Mirrors the module-level `printException` but includes the
        originating class/function context when `self` is available.
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
            printException(e_fallback, "AppException.printException fallback")


class GitRepo(AppException):
    """
    Tools for working on a git repository.
    There are four main functions or classes of functions.

    To use the class, start by creating a:

    gitRepo = GitRepo(directory/filename)

    GitRepo.__init__() in turn calls resolve_repo_top() to retrieve the root of the repo that
        contains the directory or filename. An exception is returned if there is no git repo.
    
        (repoRoot, e) = GitRepo.resolve_repo_top(directory/filename, raise_on_missing)
            Returns the root of a git repository

            If the path is for the top of a git repository, or a directory or file within,
            then the full path to that git repository is returned (with e set to None)..
            If not, then
                if raise_on_missing, throws an exception
                if not, return (None, e), where e is the exception that would have been raised.

    gitRepo.get_repo_root() will return the repoRoot.

    If you have a path (relative to the current directory), you can trim it down to one
    rooted in the repoRoot by using GitRepo.relpath_if_within(). 

        relpath = GitRepo.relpath_if_within(repoRoot, query_path)
            Both repoRoot and query_path to full paths based on the current directory.
            Returns "" if the query_path and repoRoot are the same.
            Returns a path relative to repoRoot if the file is within the repoRoot directory
            Raises an exception otherwise

    Most of the remaining functions return information about the repository from various points of view,
    in particular file lists associated with hashes, hash lists associated with the repo or files,
    and differences for a file with a given set of hashes.

    gitRepo.getFileListBetweenNormalizedHashes(hash1, hash2)
        Returns a list of the files that were modified between two hashes, along
        with a status indicator and the timestamp for when that hash was committed
        In addition to the normal hex-string hashes that git normally supports, there
        are three pseudo-hashes that are supported:
            GitRepo.NEWREPO -- the initial state of a repository
            GitRepo.STAGED -- files that have been added to a repository but not yet committed
            GitRepo.MODS -- files that have been modified since STAGED or HEAD (if nothing is staged)

    gitRepo.getHashListComplete()
        Returns a list of the hashes, their timestamps and commit messages for the entire repository.
    gitRepo.getHashListFromFileName(filename)
        Returns a list of the hashes, their timestamps and commit messages associated with the specified filename.
        The filename is relative to the repoRoot.


    gitRepo.reset_cache() will reset the cache used by GitRepo's functions. Use this if you ever wish
    to have gitRepo restart with a fresh view of the repository.

    other getFileList and getHashList functions also exist

    

    Internally the git command is used to retrieve the information and cached.

    Note: an earlier version of this class used pyGit2, but it was found to produce
    results for getHashListFromFileName() and gitRepo.getFileListBetweenNormalizedHashes()
    that were sufficiently different to be troublesome. Also, various operations were actually
    slower than forking the git command.
    """

    # Pseudo-hash tokens used across diff dispatching
    NEWREPO = "NEWREPO"
    STAGED = "STAGED"
    MODS = "MODS"

    NEWREPO_MESSAGE = "Newly created repository"
    STAGED_MESSAGE = "Staged changes"
    MODS_MESSAGE = "Unstaged modifications"

    def __init__(self, repoRoot: str):
        # One-time per-process cache for git CLI command results
        self._cmd_cache = {}
        # Resolve the provided path to the git repository top; allow
        # exceptions from `resolve_repo_top(..., raise_on_missing=True)` to
        # propagate so callers receive a clear failure immediately.
        resolved, _ = GitRepo.resolve_repo_top(repoRoot, raise_on_missing=True)
        self.repoRoot = resolved


    def reset_cache(self) -> None:
        """Reset the per-process command/result cache."""
        self._cmd_cache = {}

    @classmethod
    def resolve_repo_top(cls, path: str, raise_on_missing: bool = False) -> tuple[str | None, Exception | None]:
        """Resolve the git repository top-level directory for `path`.

        `path` may be a file or directory; returns a tuple `(out, err)` where
        `out` is the absolute path to the repository top (as reported by
        `git rev-parse --show-toplevel`) and `err` is `None` on success. If
        resolution fails and `raise_on_missing` is False, returns `(None, e)`
        where `e` is the exception encountered. If `raise_on_missing` is
        True the function raises on failure.

        This is a `classmethod` so it can be used without instantiating
        a `GitRepo` object.
        """
        if not path:
            e = RuntimeError("resolve_repo_top: empty path")
            if raise_on_missing:
                raise e
            return (None, e)
        cur = os.path.abspath(path)
        if os.path.isfile(cur):
            cur = os.path.dirname(cur)
        try:
            out = check_output(["git", "rev-parse", "--show-toplevel"], cwd=cur, text=True).strip()
            return (out, None)
        except FileNotFoundError as _use_raise:
            # git not installed or not on PATH
            if raise_on_missing:
                raise RuntimeError("git not available on PATH") from _use_raise
            return (None, _use_raise)
        except CalledProcessError as _use_raise:
            # Not a git work-tree or other git error
            if raise_on_missing:
                raise RuntimeError(f"not a git working tree: {path}") from _use_raise
            return (None, _use_raise)
        except Exception as _use_raise:
            if raise_on_missing:
                raise
            return (None, _use_raise)
    @classmethod
    def relpath_if_within(cls, base_path: str, conv_path: str) -> str | None:
        """
        Return `full_path` relative to `base_path` if it is inside it, else None.

        If base_path is not a full path (starts with /), 
        it will be augmented with the current directory, 
        then converted to the minimal version.

        If base_path is a full path (starts with /), 
        it will be converted to the minimal version.

        If conv_path is not a full path, it will be treated as relative to the current directory,
        then converted to the minimal version.

        If conv_path is a full path, it will be converted to the minimal version.

        If conv_path is within base_path, the relative path from base_path to conv_path is returned.
        If conv_path is not within base_path, None is returned. 
        """
        # Raise on invalid inputs.
        print(f"base_path= '{base_path}'")
        print(f"conv_path= '{conv_path}'")
        if not base_path:
            raise ValueError("relpath_if_within: empty base_path")
        if not conv_path:
            raise ValueError("relpath_if_within: empty conv_path")
        try:
            if base_path[0] != os.sep:
                base_path = os.path.abspath(os.path.join(os.getcwd(), base_path))
            base_path = os.path.abspath(os.path.normpath(base_path))
            print(f"base_path> '{base_path}'")
            if conv_path[0] != os.sep:
                conv_path = os.path.abspath(os.path.join(os.getcwd(), conv_path))
            conv_path = os.path.abspath(os.path.normpath(conv_path))
            print(f"conv_path> '{conv_path}'")
            if base_path == conv_path:
                common = ""
            else:
                common = os.path.relpath(conv_path, base_path)
            print(f"common> '{common}'")
            return common
        except Exception as _use_raise:
            raise ValueError(f"relpath_if_within: path evaluation failed: {_use_raise}") from _use_raise
    
    def get_repo_root(self) -> str:
        """Return the resolved repository root path for this GitRepo instance."""
        return self.repoRoot

    def _deltas_to_results(self, detailed: list, a_raw, b_raw) -> list[tuple[str, str]]:
        """Simplified conversion of detailed delta dicts to `(path,status)`.

        The original implementation attempted complex oid-based matching and
        lifecycle tracing; it had become fragile and contained malformed
        indentation. Replace it with a straightforward, robust converter that
        extracts the most-relevant path and status from each detailed entry.
        """
        try:
            if not detailed:
                return []

            results: list[tuple[str, str]] = []
            for item in detailed:
                try:
                    # Prefer an explicit status if present, else fall back
                    status = item.get("status") or "modified"

                    # Prefer canonical path order: explicit 'path', then new_path, then old_path
                    path = item.get("path") or item.get("new_path") or item.get("old_path")
                    if not path:
                        # Skip entries without a usable path
                        continue

                    # Normalize rename status if it encodes a target
                    if isinstance(status, str) and status.startswith("renamed->"):
                        # keep as-is (e.g. 'renamed->new/path')
                        pass

                    results.append((path, status))
                except Exception as e:
                    self.printException(e, "_deltas_to_results: processing item failed")
                    continue

            results.sort(key=lambda x: x[0])
            return results
        except Exception as e:
            self.printException(e, "_deltas_to_results: unexpected failure")
            return []


    def index_mtime_iso(self) -> str:
        """
        Return an ISO timestamp (UTC) based on the repository index mtime.

        Prefer `.git/index`, falling back to `index` at repo root, and
        finally to the current time if not available.
        """
        idx_candidates = [
            os.path.join(self.repoRoot, ".git", "index"),
            os.path.join(self.repoRoot, "index"),
        ]
        idx_mtime = None
        for p in idx_candidates:
            try:
                if os.path.exists(p):
                    idx_mtime = os.path.getmtime(p)
                    break
            except Exception as e:
                self.printException(e, "index_mtime_iso: checking index candidate failed")
                continue
        if idx_mtime is None:
            idx_mtime = datetime.now(timezone.utc).timestamp()
        return self._epoch_to_iso(idx_mtime)


    def _epoch_to_iso(self, epoch: float) -> str:
        """
        Convert an epoch (seconds) to an ISO UTC timestamp string.

        Centralized helper to avoid repeating the same try/except timestamp
        formatting logic throughout the codebase.
        """
        try:
            return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception as e:
            self.printException(e, "_epoch_to_iso: formatting timestamp failed")
            return "1970-01-01T00:00:00"


    def _git_cli_decode_quoted_path(self, rel: str) -> str:
        """Decode a git-quoted path ("...") emitted by `git ls-files`.

        - If the path is quoted (surrounded by double quotes), unescape
          backslash sequences (e.g. \\xHH, \\ooo) and interpret the resulting
          byte values as UTF-8 when possible. If UTF-8 decoding fails,
          fall back to latin-1 so the original byte values are preserved.
        - If the path is not quoted, return it unchanged.
        """
        if not rel:
            return rel
        if rel.startswith('"') and rel.endswith('"'):
            raw = rel[1:-1]
            try:
                tmp = codecs.decode(raw, "unicode_escape")
                b = tmp.encode("latin-1", "surrogatepass")
                try:
                    return b.decode("utf-8")
                except UnicodeDecodeError as e:
                    self.printException(e, "_git_cli_decode_quoted_path: unicode decode failed")
                    return b.decode("latin-1")
            except Exception as e:
                self.printException(e, "_git_cli_decode_quoted_path: decode failed")
                return raw
        return rel


    def safe_mtime(self, rel: str) -> float | None:
        """Return the mtime (float) for repository-relative `rel` or None.

        Centralized helper that resolves the filesystem path under `repoRoot`,
        handles symlinks via `lstat`, catches `FileNotFoundError` and other
        exceptions, and logs failures via `printException`.
        """
        try:
            fp = os.path.join(self.repoRoot, rel)
            if os.path.islink(fp):
                return os.lstat(fp).st_mtime
            if os.path.exists(fp):
                return os.path.getmtime(fp)
            return None
        except FileNotFoundError as e:
            self.printException(e, f"safe_mtime: file not found ({rel})")
            return None
        except Exception as e:
            self.printException(e, f"safe_mtime: stat failed ({rel})")
            return None


    def _make_cache_key(self, name: str, *args) -> str:
        """Build a compact cache key from `name` and `args`.

        Produces `name:HEX` where HEX is the first 16 chars of the
        sha256 of the repr of (name,args). This avoids ad-hoc string
        formatting throughout the codebase while keeping keys stable
        within a process.
        """
        try:
            payload = (name,) + tuple(args)
            h = hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()[:16]
            return f"{name}:{h}"
        except Exception as e:
            self.printException(e, "_make_cache_key: failed to build key")
            # Fallback to a simple name-based key
            return f"{name}:{hashlib.sha256(name.encode()).hexdigest()[:16]}"


    def _paths_mtime_iso(self, paths: list[str]) -> str:
        """
        Given a list of repository-relative paths, return an ISO timestamp
        (UTC) representing the most-recent modification time among those
        files. If no mtimes can be determined, fall back to the index mtime.
        """
        mtimes: list[float] = []
        for p in paths:
            try:
                m = self.safe_mtime(p)
                if m is not None:
                    mtimes.append(m)
            except Exception as e:
                self.printException(e, "_paths_mtime_iso: checking path mtime failed")
                continue
        if mtimes:
            return self._epoch_to_iso(max(mtimes))
        return self.index_mtime_iso()


    def _newrepo_timestamp_iso(self) -> str:
        """
        Compute an ISO timestamp for the pseudo-`NEWREPO` entry.

        Strategy:
        - Determine the timestamp (epoch seconds) of the first commit in the
          repository (oldest commit). If unable to obtain it, treat as None.
        - Walk the `.git` directory collecting file mtimes (using `lstat`
          for symlinks) and take the oldest (minimum) mtime if any are
          present.
        - Return the earliest (smallest) of the first-commit ts and the
          `.git`-files mtimes as an ISO UTC string. If neither is
          available, fall back to `index_mtime_iso()`.
        """
        first_commit_ts: float | None = None
        git_dir = os.path.join(self.repoRoot, ".git")
        try:
            out = self._git_run(["git", "log", "--reverse", "--pretty=format:%ct"], text=True)
            if out:
                for line in out.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        first_commit_ts = float(int(line))
                        break
                    except Exception as _no_logging:
                        continue
        except Exception as e:
            self.printException(e, "_newrepo_timestamp_iso: git log failed")

        git_mtimes: list[float] = []
        try:
            if os.path.exists(git_dir):
                for root, dirs, files in os.walk(git_dir):
                    for name in files:
                        fp = os.path.join(root, name)
                        try:
                            if os.path.islink(fp):
                                git_mtimes.append(os.lstat(fp).st_mtime)
                            else:
                                git_mtimes.append(os.path.getmtime(fp))
                        except Exception as _no_logging:
                            # ignore individual failures
                            continue
        except Exception as e:
            self.printException(e, "_newrepo_timestamp_iso: walking .git failed")

        candidates: list[float] = []
        if first_commit_ts is not None:
            candidates.append(first_commit_ts)
        if git_mtimes:
            candidates.append(min(git_mtimes))

        if candidates:
            earliest = min(candidates)
            return self._epoch_to_iso(earliest)

        # Fallback: use index mtime ISO
        return self.index_mtime_iso()

    def _parse_git_log_output(self, output: str) -> list[tuple[int, str, str]]:
        """Parse `git log --pretty=format:%ct %H %s` style output.

        Returns a list of tuples `(timestamp_int, hash, subject)`.
        """
        results: list[tuple[int, str, str]] = []
        try:
            for line in output.splitlines():
                if not line:
                    continue
                parts = line.split(None, 2)
                if len(parts) < 2:
                    continue
                try:
                    ts = int(parts[0])
                except Exception as e:
                    self.printException(e, "_parse_git_log_output: parsing timestamp failed")
                    ts = 0
                h = parts[1].strip()
                subject = parts[2].strip() if len(parts) >= 3 else ""
                results.append((ts, h, subject))
            return results
        except Exception as e:
            self.printException(e, "_parse_git_log_output: unexpected failure")
            return []



    
    def _git_cli_parse_name_status_output(self, output: str) -> list[tuple[str, str]]:
        """Parse `--name-status` output (possibly many lines) into `(path,status)` pairs.

        Returns a sorted list of `(path,status)` tuples.
        """
        try:
            results: list[tuple[str, str]] = []
            for line in output.splitlines():
                if not line:
                    continue
                try:
                    parts = line.split()
                    if not parts:
                        continue
                    code = parts[0].strip()
                    path = parts[1].strip() if len(parts) > 1 else ""

                    # Map code to status
                    status = "modified"
                    if code:
                        first = code[0]
                        if first == "A":
                            status = "added"
                        elif first == "M":
                            status = "modified"
                        elif first == "D":
                            status = "deleted"
                        elif first == "R":
                            status = "renamed"
                        elif first == "C":
                            status = "copied"

                    # If rename/copy includes a target path, include it
                    try:
                        if code.startswith("R") and len(parts) > 2:
                            newp = parts[-1].strip()
                            if newp:
                                status = f"renamed->{newp}"
                    except Exception as e:
                        self.printException(e, "_git_cli_parse_name_status_output: including rename target failed")

                    if path:
                        results.append((path, status))
                except Exception as e:
                    self.printException(e, "_git_cli_parse_name_status_output: line parse failed")
                    continue
            results.sort(key=lambda x: x[0])
            return results
        except Exception as e:
            self.printException(e, "_git_cli_parse_name_status_output: unexpected failure")
            return []

    def _git_cli_name_status(self, args: list) -> list[tuple[str, str]]:
        """Run a `git` command that emits `--name-status`-style output and parse it.

        `args` should be a list suitable for `subprocess.check_output`, for
        example `['git','diff','--name-status', 'A', 'B']` or
        `['git','diff','--name-status','--cached']`.
        Returns a sorted list of `(path, status)`.
        """
        try:
            output = self._git_run(args, text=True) or ""
            return self._git_cli_parse_name_status_output(output)
        except Exception as e:
            self.printException(e, "_git_cli_name_status: unexpected failure")
            return []


    def _git_name_status_dispatch(self, prev: str | None = None, curr: str | None = None, cached: bool = False, key: str | None = None) -> list[tuple[str, str]]:
        """Generalized dispatcher for `git diff --name-status` variants.

        Builds the appropriate `git` argument list from the template and
        delegates to the cached parser. Use `cached=True` to include
        `--cached` in the args. If `key` is omitted a synthetic cache key
        is derived from the parameters.
        """
        try:
            args = ["git", "diff", "--name-status"]
            if cached:
                args.append("--cached")
            if prev is not None and curr is not None:
                args += [prev, curr]
            elif prev is not None:
                args.append(prev)
            elif curr is not None:
                args.append(curr)
            cache_key = key or self._make_cache_key("git_name_status", prev, curr, 'cached' if cached else 'nocache')
            return self._git_cli_getCachedFileList(cache_key, args)
        except Exception as e:
            self.printException(e, "_git_name_status_dispatch: unexpected failure")
            return []


    def _git_run(self, args: list, text: bool = True, cache_key: str | None = None):
        """Run a git subprocess and return its output.

        Behavior:
        - On success returns the command output (string when text=True).
        - On CalledProcessError: if `cache_key` is provided, store [] into
          `self._cmd_cache[cache_key]` and return an empty string. On any
          failure this function returns an empty string (never `None`).
        - Caches raw command output under an internal key derived from args
          so identical subprocess calls are cheap.
        """
        try:
            internal_key = f"_git_run:{' '.join(args)}:{'text' if text else 'bytes'}"
            if internal_key in self._cmd_cache:
                return self._cmd_cache[internal_key]
            try:
                out = check_output(args, cwd=self.repoRoot, text=text)
            except CalledProcessError as e:
                self.printException(e, f"_git_run: git command failed: {' '.join(args)}")
                # If caller provided a parsed-result cache_key, store an empty
                # parsed result there so callers can return quickly next time.
                if cache_key:
                    self._cmd_cache[cache_key] = []
                    # record failure for this internal invocation as empty string
                    self._cmd_cache[internal_key] = ""
                    return ""
                # Otherwise, record failure sentinel as empty string and
                # return empty string (never None)
                self._cmd_cache[internal_key] = ""
                return ""
            # Success: cache raw output under internal key and return it.
            self._cmd_cache[internal_key] = out
            return out
        except Exception as e:
            self.printException(e, "_git_run: unexpected failure")
            if cache_key:
                self._cmd_cache[cache_key] = []
                self._cmd_cache[internal_key] = ""
                return ""
            # Always return an empty string on unexpected failures
            self._cmd_cache[internal_key] = ""
            return ""


    def _empty_tree_hash(self) -> str:
        """
        Return the canonical empty-tree object id for this repository's
        object format. Uses `git rev-parse --show-object-format` to detect
        the repository format and returns a constant for supported formats.

        Returns SHA-1 empty-tree by default if detection fails.
        """
        # Known constants per object format
        sha1_empty = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        sha256_empty = "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"

        try:
            cache_key = "_empty_tree_hash"
            if cache_key in self._cmd_cache:
                return self._cmd_cache[cache_key]

            # Ask git which object format this repo uses. Example responses:
            #   sha1
            #   sha256
            out = self._git_run(["git", "rev-parse", "--show-object-format"], text=True) or ""
            fmt = out.strip().lower()

            if fmt == "sha256":
                res = sha256_empty
            else:
                # Default to sha1 for "sha1" or any unknown/empty response
                res = sha1_empty

            # Cache result for this process
            self._cmd_cache[cache_key] = res
            return res
        except Exception as e:
            # On unexpected failures default to sha1 canonical id
            self.printException(e, "_empty_tree_hash: detection failed, defaulting to sha1")
            return sha1_empty


    

    def getFileListBetweenNormalizedHashes(self, prev_hash: str, curr_hash: str) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between `prev_hash` and `curr_hash`.

        Status values: `added`, `modified`, `deleted`, `renamed`, `copied`.
        This function expects `prev_hash` and `curr_hash` to be commit-ish values
        (i.e. resolvable by valid git commit refs).
        This function MUST be a dispatch to specialized handlers for each case.
        """
        # Dispatch to specialized handlers for common token combinations.

        # Validate tokens: disallow bare `None` — require explicit `NEWREPO` token.
        if prev_hash is None or curr_hash is None:
            raise ValueError(
                "getFileListBetweenNormalizedHashes: None is not a valid token; use GitRepo.NEWREPO for initial repository"
            )

        # If identical tokens were passed, there are no changes.
        if prev_hash == curr_hash:
            return []

        # If prev is the pseudo-NewRepo token and curr is a normal hash -> new->hash
        if prev_hash == self.NEWREPO and curr_hash not in (self.STAGED, self.MODS):
            return self.getFileListBetweenNewRepoAndHash(curr_hash)

        # If prev is NEWREPO and curr is staged -> initial->staged
        if prev_hash == self.NEWREPO and curr_hash == self.STAGED:
            return self.getFileListBetweenNewRepoAndStaged()

        # If prev is NEWREPO and curr is working tree (mods) -> initial->mods
        if prev_hash == self.NEWREPO and curr_hash == self.MODS:
            return self.getFileListBetweenNewRepoAndMods()

        # If prev and curr are both normal hashes -> direct commit->commit diff
        if prev_hash not in (self.NEWREPO, self.STAGED, self.MODS) and curr_hash not in (
            self.NEWREPO,
            self.STAGED,
            self.MODS,
        ):
            return self.getFileListBetweenTwoCommits(prev_hash, curr_hash)

        # Hash -> staged
        if prev_hash not in (self.NEWREPO, self.STAGED, self.MODS) and curr_hash == self.STAGED:
            return self.getFileListBetweenHashAndStaged(prev_hash)

        # Hash -> working tree (mods)
        if prev_hash not in (self.NEWREPO, self.STAGED, self.MODS) and curr_hash == self.MODS:
            return self.getFileListBetweenHashAndCurrentTime(prev_hash)

        # staged -> mods (working tree)
        if prev_hash == self.STAGED and curr_hash == self.MODS:
            return self.getFileListBetweenStagedAndMods()

        # Fallback: for remaining (likely commit-ish) combos delegate to the
        # explicit two-commit handler rather than the fully generic resolver.
        return self.getFileListBetweenTwoCommits(prev_hash, curr_hash)


    def getFileListBetweenNewRepoAndTopHash(self) -> list[str]:
        """Return a list of `(path, status)` for files present in HEAD.

        Status will be `committed` to indicate file is present in the
        given commit (HEAD).
        """
        # Delegate to the new initial->commit helper to avoid duplication
        return self.getFileListBetweenNewRepoAndHash("HEAD")


    def getFileListBetweenTwoCommits(self, prev_hash: str, curr_hash: str) -> list[tuple[str, str]]:
        """Direct commit->commit diff (both args expected to be commit-ish).

        Extracted helper containing the previous logic for diffing two commits.
        """
        # Use generalized dispatcher for commit->commit diffs
        key = self._make_cache_key("getFileListBetweenTwoCommits", prev_hash, curr_hash)
        return self._git_name_status_dispatch(prev=prev_hash, curr=curr_hash, cached=False, key=key)


    def getFileListBetweenNewRepoAndHash(self, curr_hash: str) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between the beginning and `curr_hash`.

        Status values are the same as other diffs (added/modified/etc.).
        """
        # Git-CLI implementation with a one-time per-hash cache. The
        key = self._make_cache_key("getFileListBetweenNewRepoAndHash", curr_hash)
        try:
            if key in self._cmd_cache:
                return self._cmd_cache[key]

            output = self._git_run(["git", "ls-tree", "-r", "--name-only", curr_hash], text=True, cache_key=key)

            results: list[tuple[str, str]] = []
            for line in output.splitlines():
                ln = line.strip()
                if not ln:
                    continue
                results.append((ln, "added"))
            results.sort(key=lambda x: x[0])
            self._cmd_cache[key] = results
            return results
        except Exception as e:
            self.printException(e, "getFileListBetweenNewRepoAndHash: unexpected failure")
            return []


    def getFileListBetweenNewRepoAndStaged(self) -> list[tuple[str, str]]:
        """Return file list for the initial (empty) tree -> staged index comparison.

        This is the specialized handler for the `prev is None and curr == STAGED`
        case so `getFileListBetweenNormalizedHashes` can remain a dispatcher.
        """
        # Git-CLI-only implementation cached once per process.
        key = "getFileListBetweenNewRepoAndStaged"
        return self._git_name_status_dispatch(prev=None, curr=None, cached=True, key=key)


    # make git-only
    def getFileListBetweenNewRepoAndMods(self) -> list[tuple[str, str]]:
        """Specialized handler for initial (empty) -> working tree (mods) comparison.

        This implementation is git-CLI-only and mirrors `git diff --name-status`.
        """
        key = "getFileListBetweenNewRepoAndMods"
        return self._git_name_status_dispatch(prev=None, curr=None, cached=False, key=key)


    def getFileListBetweenTopHashAndCurrentTime(self) -> list[str]:
        """Return a list of `(path, status)` for files changed between HEAD and working tree.

        Status will reflect the working-tree change type (modified/added/deleted).
        """
        # Delegate to the general handler to avoid duplicating logic
        return self.getFileListBetweenHashAndCurrentTime("HEAD")


    def _git_cli_getCachedFileList(self, key: str, git_args: list) -> list[tuple[str, str]]:
        """Run `git_args` (list) and cache the parsed name-status results under `key`.

        Returns a sorted list of `(path,status)`. On git failure returns [].
        """
        try:
            if key in self._cmd_cache:
                return self._cmd_cache[key]

            output = self._git_run(git_args, text=True, cache_key=key)

            # Parse the whole output using the consolidated parser
            results = self._git_cli_parse_name_status_output(output or "")
            self._cmd_cache[key] = results
            return results
        except Exception as e:
            self.printException(e, "_git_cli_getCachedFileList: unexpected failure")
            return []


    def getFileListBetweenHashAndCurrentTime(self, hash: str) -> list[tuple[str, str]]:
        """Return `(path,status)` for files changed between `hash` and working tree.

        Uses the git CLI plus a one-time cache via `_git_cli_getCachedFileList`.
        """
        key = self._make_cache_key("getFileListBetweenHashAndCurrentTime", hash)
        return self._git_name_status_dispatch(prev=hash, curr=None, cached=False, key=key)


    def getFileListBetweenTopHashAndStaged(self) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between HEAD and staged index."""
        # Delegate to the generalized staged-vs-hash implementation to avoid duplication
        return self.getFileListBetweenHashAndStaged("HEAD")


    def getFileListBetweenHashAndStaged(self, hash: str) -> list[tuple[str, str]]:
        """Return `(path,status)` for files changed between `hash` and the staged index.

        Generalization of getFileListBetweenTopHashAndStaged for any commit-ish.
        """
        key = self._make_cache_key("getFileListBetweenHashAndStaged", hash)
        return self._git_name_status_dispatch(prev=hash, curr=None, cached=True, key=key)


    # make git-only
    def getFileListBetweenStagedAndMods(self) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between staged index and working tree (mods)."""
        # Use git CLI to get the list of files; cache the results once per process
        key = self._make_cache_key("getFileListBetweenStagedAndMods")
        return self._git_name_status_dispatch(prev=None, curr=None, cached=False, key=key)


    # make git-only
    def getFileListUntrackedAndIgnored(self) -> list[tuple[str, str, str]]:
        """Return a sorted list of `(path, iso_mtime, status)` for files that are
        either untracked or ignored in the working tree.

        - `status` is one of: `untracked`, `ignored`.
        - `iso_mtime` is produced from the filesystem mtime via `_epoch_to_iso`.
        """
        try:
            cache_key = self._make_cache_key("getFileListUntrackedAndIgnored")
            if cache_key in self._cmd_cache:
                return self._cmd_cache[cache_key]

            results: list[tuple[str, str, str]] = []
            seen: set[str] = set()

            untracked_out = self._git_run(["git", "ls-files", "--others", "--exclude-standard"], text=True) or ""
            ignored_out = self._git_run(["git", "ls-files", "--others", "-i", "--exclude-standard"], text=True) or ""

            for line in untracked_out.splitlines():
                rel = line.strip()
                rel = self._git_cli_decode_quoted_path(rel)
                if not rel or rel in seen:
                    continue
                seen.add(rel)
                mtime = self.safe_mtime(rel)
                iso = self._epoch_to_iso(mtime) if mtime is not None else self.index_mtime_iso()
                results.append((rel, iso, "untracked"))

            for line in ignored_out.splitlines():
                rel = line.strip()
                rel = self._git_cli_decode_quoted_path(rel)
                if not rel or rel in seen:
                    continue
                seen.add(rel)
                mtime = self.safe_mtime(rel)
                iso = self._epoch_to_iso(mtime) if mtime is not None else self.index_mtime_iso()
                results.append((rel, iso, "ignored"))

            results.sort(key=lambda x: x[0])
            self._cmd_cache[cache_key] = results
            return results
        except Exception as e:
            self.printException(e, "getFileListUntrackedAndIgnored: unexpected failure")
            return []


    def getHashListComplete(self) -> list[tuple[str, str, str]]:
        """Return a combined list of commit hashes for staged, new, and entire repo."""
        new = self.getHashListNewChanges()
        staged = self.getHashListStagedChanges()
        entire = self.getHashListEntireRepo()
        newrepo = self.getHashListNewRepo()
        combined = new + staged + entire + newrepo
        return combined


    # NOTE: `getHashListSample` and `getHashListSamplePlusEnds` were moved
    # out of this class into module-level helpers (see testRepo.py). They
    # were intentionally removed to keep GitRepo focused on repository
    # operations. If callers still expect these symbols as methods, they
    # should call the module-level helpers instead.


    # runFileListSampledExercises moved to module-level function

    def getHashListEntireRepo(self) -> list[tuple[str, str, str]]:
        """Return a list of all commit hashes in the repository."""
        # Use git log to get commit epoch time, hash and subject for all refs
        output = self._git_run(["git", "log", "--all", "--pretty=format:%ct %H %s"], text=True)
        pairs = self._parse_git_log_output(output or "")
        pairs.sort(key=lambda x: (x[0], x[1]), reverse=True)
        formatted: list[tuple[str, str, str]] = []
        for ts, h, subject in pairs:
            iso = self._epoch_to_iso(ts)
            formatted.append((iso, h, subject))
        return formatted


    def getHashListStagedChanges(self) -> list[tuple[str, str, str]]:
        """Return a list of commit hashes for staged changes."""
        # Use git CLI to detect staged files and return a STAGED pseudo-hash.
        key = self._make_cache_key("getHashListStagedChanges", self.index_mtime_iso())
        try:
            if key in self._cmd_cache:
                return self._cmd_cache[key]

            names_out = self._git_run(["git", "diff", "--cached", "--name-only"], text=True, cache_key=key)

            if not names_out:
                self._cmd_cache[key] = []
                return []

            if any(ln.strip() for ln in names_out.splitlines()):
                iso = self.index_mtime_iso()
                res = [(iso, "STAGED", self.STAGED_MESSAGE)]
                self._cmd_cache[key] = res
                return res

            self._cmd_cache[key] = []
            return []
        except Exception as e:
            self.printException(e, "getHashListStagedChanges: failure")
            return []


    def getHashListNewChanges(self) -> list[tuple[str, str, str]]:
        """Return a list of commit hashes for new changes."""
        # Detect working-tree vs index differences via git CLI and return a
        # MODS pseudo-hash when there are modified files.
        try:
            names_out = self._git_run(["git", "diff", "--name-only"], text=True)

            if not names_out:
                return []

            paths = [ln.strip() for ln in names_out.splitlines() if ln.strip()]
            if not paths:
                return []

            iso = self._paths_mtime_iso(paths)
            key = self._make_cache_key("getHashListNewChanges", iso)
            if key in self._cmd_cache:
                return self._cmd_cache[key]
            res = [(iso, "MODS", self.MODS_MESSAGE)]
            self._cmd_cache[key] = res
            return res
        except Exception as e:
            self.printException(e, "getHashListNewChanges: failure")
            return []


    def getHashListNewRepo(self) -> list[tuple[str, str, str]]:
        """Return the pseudo-hash entry for a newly-created repository.

        Returns a single-entry list containing `(iso_timestamp, NEWREPO, NEWREPO_MESSAGE)`.
        The timestamp is computed from the earliest of the first commit time
        and mtimes under the `.git` directory; falls back to index mtime.
        """
        try:
            iso = self._newrepo_timestamp_iso()
            return [(iso, self.NEWREPO, self.NEWREPO_MESSAGE)]
        except Exception as e:
            self.printException(e, "getHashListNewRepo: failure")
            return [(self.index_mtime_iso(), self.NEWREPO, self.NEWREPO_MESSAGE)]


    def getHashListFromFileName(self, file_name: str) -> list[tuple[str, str, str]]:
        """Return a list of commit hashes that modified the given file.

        Uses the git CLI (`git log` + `git status`) with a one-time cache per
        `file_name`. The previous walk/diff implementation has been
        removed for performance consistency.
        """
        key = self._make_cache_key("getHashListFromFileName", file_name)
        try:
            if key in self._cmd_cache:
                return self._cmd_cache[key]

            output = self._git_run(["git", "log", "--pretty=format:%ct %H %s", "--", file_name], text=True, cache_key=key)

            entries: list[tuple[str, str, str]] = []
            parsed = self._parse_git_log_output(output or "")
            for ts, h, subject in parsed:
                iso = self._epoch_to_iso(ts)
                entries.append((iso, h, subject if subject else ""))

            status_out = self._git_run(["git", "status", "--porcelain", "--", file_name], text=True) or ""
            if status_out:
                s = status_out.splitlines()[0]
                if len(s) >= 2:
                    idx_flag = s[0]
                    wt_flag = s[1]
                else:
                    idx_flag = s[0] if s else " "
                    wt_flag = " "
                iso_index = self.index_mtime_iso()
                iso_mods = self._paths_mtime_iso([file_name])
                if idx_flag != " ":
                    entries.insert(0, (iso_index, "STAGED", self.STAGED_MESSAGE))
                if wt_flag != " ":
                    if entries and entries[0][1] == "STAGED":
                        entries.insert(1, (iso_mods, "MODS", self.MODS_MESSAGE))
                    else:
                        entries.insert(0, (iso_mods, "MODS", self.MODS_MESSAGE))

            entries.sort(key=lambda x: x[0], reverse=True)
            self._cmd_cache[key] = entries
            return entries
        except Exception as e:
            self.printException(e, "getHashListFromFileName: unexpected failure")
            return []

    def getDiff(self, filename: str, hash1: str, hash2: str, variation: list[str] | None = None) -> list[str]:
        """
        Return the lines produced by `git diff` for `filename` between `hash1` and `hash2`.

        - `filename` is repository-relative path to a file in this repo.
        - `hash1` and `hash2` must be non-None strings and may be full/partial
          git commit-ish hashes or the pseudo-hashes `NEWREPO`, `STAGED`, `MODS`.
        - `variation` is an optional list of additional git-diff arguments (e.g.
          ['--ignore-space-change', '--diff-algorithm=patience']).

        Raises ValueError if `filename` is empty or either hash is None. On
        unexpected failures the exception is logged and re-raised.
        """
        try:
            if filename is None or filename == "":
                raise ValueError("getDiff: filename must be a non-empty repository-relative path")
            if hash1 is None or hash2 is None:
                raise ValueError("getDiff: hash1 and hash2 must be specified (not None)")

            # return empty diff if both hashes are identical
            if hash1 == hash2:
                return []

            # Normalize variation list
            var_args: list[str] = []
            if variation:
                try:
                    for v in variation:
                        # Accept strings or single-item tuples/lists for backwards compat
                        if isinstance(v, (list, tuple)) and len(v) == 1:
                            var_args.append(str(v[0]))
                        else:
                            var_args.append(str(v))
                except Exception as e:
                    self.printException(e, "getDiff: processing variation args failed")

            # Validate and build git diff arguments
            # Use the repository-format empty-tree object for NEWREPO diffs
            EMPTY_TREE = self._empty_tree_hash()

            def token_to_ref(token: str):
                if token == self.NEWREPO:
                    return EMPTY_TREE
                if token == self.MODS:
                    # working tree; represented by absence of a tree/ref
                    return None
                if token == self.STAGED:
                    # staged/index is represented via --cached flag
                    return self.STAGED
                return token

            ref1 = token_to_ref(hash1)
            ref2 = token_to_ref(hash2)

            # If both refs resolve to the special staged marker, map to cached diff
            args: list[str] = ["git", "diff"]
            args.extend(var_args)

            # If either side is staged, use --cached and include the other ref if present
            if ref1 == self.STAGED or ref2 == self.STAGED:
                args.append("--cached")
                # include the non-staged ref if it maps to a concrete ref
                other_ref = ref2 if ref1 == self.STAGED else ref1
                if other_ref is not None and other_ref != self.STAGED:
                    args.append(other_ref)
            else:
                # Neither side staged: include concrete refs when available.
                # `None` indicates working tree (MODS) and is omitted so git diff
                # will compare commit<->working-tree when only one ref is present.
                if ref1 is not None:
                    args.append(ref1)
                if ref2 is not None:
                    args.append(ref2)

            # Append separator and filename
            args += ["--", filename]

            out = self._git_run(args, text=True)
            # Return output lines (preserve empty output as empty list)
            if not out:
                return []
            return out.splitlines()
        except Exception as e:
            # Log and re-raise so callers can handle errors explicitly
            self.printException(e, "getDiff: failed")
            raise



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

    # One-time warning flag (class-scoped) used by `compare_pygit2_to_git_output`
    # so the first backend mismatch shows a UI modal once per process/class.
    comparePygit2ToGitOutputWarn: bool = False

    # `printException` provided by AppException mixin

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

        If `path` is absolute it is normalized via `os.path.realpath`.
        On error returns the original `path`.
        """
        try:
            if os.path.isabs(path):
                return os.path.realpath(path)
            return os.path.realpath(os.path.join(repo_root, path))
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
                        full = self._canonical_relpath(path, self.app.repo_root)
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
                canonical = self._canonical_relpath(full_path, self.app.repo_root) if full_path else full_path
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
                item._raw_text = full_path or display

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

    def compare_pygit2_to_git_output(self, pygout, gitout, context: str | None = None) -> None:
        """Compare two outputs (pygit2 vs git) and print differences.

        This does a simple textual diff of the pretty-printed representations
        so structural differences are visible. `context` is an optional label
        describing where the comparison was invoked.
        """
        try:
            ctx = f" [{context}]" if context else ""
            pyg_s = pprint.pformat(pygout, width=120).splitlines()
            git_s = pprint.pformat(gitout, width=120).splitlines()
            if pyg_s == git_s:
                logger.debug("compare_pygit2_to_git_output%s: outputs identical", ctx)
                return
            msg = [f"compare_pygit2_to_git_output{ctx}: outputs differ:"]
            diff = list(difflib.unified_diff(git_s, pyg_s, fromfile="git", tofile="pygit2", lineterm=""))
            if not diff:
                msg.append("(difference detected but diff is empty)")
            else:
                msg.extend(diff)
            # Show a one-time modal notification (if possible) so the user
            # notices the first backend mismatch without flooding the UI.
            try:
                if not self.comparePygit2ToGitOutputWarn:
                    try:
                        self.app.push_screen(MessageModal("compare_pygit2_to_git_output: outputs differ"))
                    except Exception as _e_modal:
                        # Don't let modal failures block logging
                        self.printException(_e_modal, "compare_pygit2_to_git_output: showing modal failed")
                    try:
                        self.comparePygit2ToGitOutputWarn = True
                    except Exception as e:
                        # Best-effort: log inability to set flag
                        self.printException(e, "compare_pygit2_to_git_output: setting compare flag failed")
            except Exception as _e:
                # Best-effort: if anything fails above, continue to print/log
                self.printException(_e, "compare_pygit2_to_git_output modal handling failed")

            # Print to stdout for immediate visibility and also log warn.
            for ln in msg:
                print(ln)
                logger.warning(ln)
        except Exception as e:
            self.printException(e, "compare_pygit2_to_git_output failed")

    def _compare_backends(self, gitout, pygout, context: str | None = None) -> list[str]:
        """Compare two backend outputs and return unified diff lines.

        Produces a unified diff between the `gitout` and `pygout` Python
        data structures (pretty-printed). Returns an empty list when
        outputs are identical. When differences are present, the diff
        lines are returned and also logged at WARNING level for visibility.

        `context` is an optional label describing where the comparison
        was invoked (e.g. a path or preparer name).
        """
        try:
            ctx = f" [{context}]" if context else ""
            g = pprint.pformat(gitout, width=120).splitlines()
            p = pprint.pformat(pygout, width=120).splitlines()
            if g == p:
                logger.debug("_compare_backends%s: outputs identical", ctx)
                return []
            diff = list(difflib.unified_diff(g, p, fromfile="git", tofile="pygit2", lineterm=""))
            if not diff:
                msg = [f"_compare_backends{ctx}: difference detected but diff is empty"]
            else:
                msg = [f"_compare_backends{ctx}: outputs differ:"] + diff
            for ln in msg:
                print(ln)
                logger.warning(ln)
            return diff
        except Exception as e:
            self.printException(e, "_compare_backends failed")
            return []

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
                    match_full = self._canonical_relpath(match, self.app.repo_root)
                except Exception as e:
                    match_full = match
                    self.printException(e, "_highlight_match: normalizing match failed")

                for i, node in enumerate(nodes):
                    try:
                        raw = getattr(node, "_raw_text", None)
                        h = getattr(node, "_hash", None)
                        try:
                            if raw is not None:
                                node_full = self._canonical_relpath(raw, self.app.repo_root)
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
        state (`app.current_hash`, `app.previous_hash`, `app.current_path`)
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
            else:
                try:
                    if hasattr(self, "_compute_selected_pair"):
                        try:
                            self._compute_selected_pair()
                        except Exception as _ex:
                            self.printException(_ex, "_finalize_prep_common: _compute_selected_pair failed")
                except Exception as e:
                    self.printException(e, "_finalize_prep_common: computing selected pair failed")
                try:
                    if path is not None:
                        self.app.current_path = path
                except Exception as _ex:
                    self.printException(_ex, "_finalize_prep_common: setting app.current_path failed")
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
        (current_hash), or both versions of the current `app.path`/`app.current_path`.
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

            # Prefer canonical current_path then fallback to app.path
            filepath = getattr(app, "current_path", None) or getattr(app, "path", None)
            prev_hash = getattr(app, "previous_hash", None)
            curr_hash = getattr(app, "current_hash", None)

            # If filepath appears to be a directory, try to use app.path instead
            if filepath and os.path.isdir(filepath):
                filepath = getattr(app, "path", None) or filepath

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
                cmd = ["git", "-C", self.repo_root, "show", f":{relpath}"]
                proc = subprocess.run(cmd, capture_output=True)
                if proc.returncode != 0:
                    err = proc.stderr.decode(errors="replace") if proc.stderr else ""
                    raise Exception(f"git show failed: {err}")
                _write_bytes(proc.stdout)
                return
            except Exception as e:
                self.printException(e, "SaveSnapshotModal._save STAGED failed")
                return

        # Otherwise treat as commit-ish hash: git show <hash>:<relpath>
        try:
            cmd = ["git", "-C", self.repo_root, "show", f"{hashval}:{relpath}"]
            proc = subprocess.run(cmd, capture_output=True)
            if proc.returncode != 0:
                err = proc.stderr.decode(errors="replace") if proc.stderr else ""
                raise Exception(f"git show failed: {err}")
            _write_bytes(proc.stdout)
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
                    match_full = self._canonical_relpath(filename, self.app.repo_root)
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
                            node_full = self._canonical_relpath(raw, self.app.repo_root)
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

        Keeps `app.path` and `app.current_path` in sync with the newly-highlighted
        row's `_raw_text` value where appropriate.
        """
        try:
            raw = getattr(node_new, "_raw_text", None)
            if raw:
                try:
                    self.app.path = raw
                except Exception as _ex:
                    self.printException(_ex, "watch_filelist_index: setting app.path failed")
                try:
                    full = raw if os.path.isabs(raw) else os.path.join(self.app.repo_root or "", raw)
                    self.app.current_path = full
                except Exception as _ex:
                    self.printException(_ex, "watch_filelist_index: setting app.current_path failed")
            logger.debug(
                "watch_filelist_index: set app.path=%r app.current_path=%r",
                self.app.path,
                self.app.current_path,
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
            if parent and parent != path and path != self.app.repo_root:
                try:
                    parent_item = ListItem(Label(Text(f"← ..", style=STYLE_PARENT)))
                    try:
                        parent_item._filename = ".."
                        parent_item._is_dir = True
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
                    if not os.path.isabs(candidate):
                        if base_path is None:
                            try:
                                bp = self.path
                            except Exception as e:
                                self.printException(e, "_schedule_highlight_and_visibility: reading self.path failed")
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

        When `self.app.test_pygit2` is true we still prefer the git CLI map
        for parity testing. If `pygit2` is available and not testing,
        return None so callers may rely on pygit2-based per-file status
        helpers instead of a precomputed map.
        """
        try:
            if self.app.test_pygit2 or not pygit2:
                return self._prepFileModeFileList_status_map_from_git(path)
            return None
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
                            item._raw_text = full
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
                        item._raw_text = full
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
        except Exception as e:
            self.printException(e, "_build_status_map failed")
            return None


class FileModeFileList(FileListBase):
    """File-mode file list: shows files for a working tree path.

    For regen Step 3 this class provides a `prepFileModeFileList` stub and
    default `key_left`/`key_right` handlers.
    """

    def prepFileModeFileList(self, path: str, highlight_filename: str | None = None) -> None:
        """Populate this widget with the file list for `path`.

        `highlight_filename` if provided will be highlighted in the list; if
        `path` names a file the file's containing directory is listed and the
        filename is used as the highlight candidate.
        """
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

            # Build a per-directory porcelain `status_map` mapping
            # repo-relative paths -> git porcelain two-char codes (index+worktree).
            # Examples: ' M' (worktree modified), 'A ' (staged/index added),
            # '??' (untracked), '!!' (ignored); codes containing 'U' indicate
            # conflicts. Use the centralized helper which respects the
            # `test_pygit2` flag and backend availability.
            status_map = self._build_status_map(path)

            # clear and populate
            self.clear()

            # Insert the unselectable key legend header at the top
            try:
                self._add_filelist_key_header()
            except Exception as e:
                self.printException(e, "prepFileModeFileList: adding filelist key header failed")

            # List directory contents (use helper)
            try:
                entries = self._list_directory(path)
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
                self._render_parent_entry_if_needed(path)
            except Exception as e:
                self.printException(e, "prepFileModeFileList: adding parent directory failed")
            try:
                file_infos: list[dict] = []
                # When testing, run both backends and compare outputs.
                if self.app.test_pygit2:
                    file_infos = self._prepFileModeFileList_from_pygit2(path, relpath)
                    file_infos_git = self._prepFileModeFileList_from_git(path, relpath, status_map)
                    self.compare_pygit2_to_git_output(file_infos, file_infos_git, "prepFileModeFileList")
                elif pygit2:
                    file_infos = self._prepFileModeFileList_from_pygit2(path, relpath)
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
                        # Schedule highlight and ensure visibility using helper
                        self._schedule_highlight_and_visibility(hl, base_path=path)
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
                        self.printException(e, "prepFileModeFileList: scheduling _highlight_top failed")
                        try:
                            self._highlight_top()
                        except Exception as e2:
                            self.printException(e2, "prepFileModeFileList: immediate _highlight_top fallback failed")
            except Exception as e:
                self.printException(e)

            # Mark populated and run centralized finalization so callers
            # get consistent app/hash/path synchronization.
            try:
                self._populated = True
                self._finalize_filelist_prep(curr_hash=None, prev_hash=None, path=path)
            except Exception as e:
                self.printException(e, "prepFileModeFileList: finalize failed")
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
                            # status_map keys may be relative to the current
                            # directory (e.g. 'notes.txt') or repo-relative
                            # (e.g. 'docs/notes.txt') depending on how the
                            # map was built. Try both forms so subdirectory
                            # listings match the status_map entries.
                            code = status_map.get(rel)
                            if code is None:
                                code = status_map.get(name)
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
                                # Not present in porcelain `status_map`.
                                # For files, use `git ls-files --error-unmatch`
                                # to detect tracked files and mark them
                                # explicitly as `tracked_clean`. Do not run
                                # this check for directories — leave directory
                                # `repo_status` as None to match pygit2.
                                try:
                                    if not is_dir:
                                        proc = self._run_cmd_log(
                                            [
                                                "git",
                                                "-C",
                                                self.app.repo_root,
                                                "ls-files",
                                                "--error-unmatch",
                                                rel,
                                            ],
                                            label="_prepFileModeFileList_from_git ls-files",
                                        )
                                        if getattr(proc, "returncode", 1) == 0:
                                            repo_status = "tracked_clean"
                                except Exception as _ex:
                                    self.printException(_ex, "_prepFileModeFileList_from_git: ls-files check failed")
                        # leave repo_status None if unknown and not tracked
                    except Exception as _ex:
                        self.printException(_ex, "_prepFileModeFileList_from_git: determining rel/status failed")
                        repo_status = None
                    infos.append({"name": name, "full": full, "is_dir": is_dir, "raw": raw, "repo_status": repo_status})
                except Exception as e:
                    self.printException(e, "_prepFileModeFileList_from_git: processing file info failed")
                    continue
        except Exception as e:
            self.printException(e, "_prepFileModeFileList_from_git failed")
        return infos

    def _prepFileModeFileList_from_pygit2(self, path: str, relpath: str) -> list[dict]:
        """Return a list of file info dicts using pygit2 when possible.

        For now this attempts to reuse the git-backed implementation for
        directory listing and augments repo_status via pygit2 if available.
        Each dict contains: name, full, is_dir, raw, repo_status
        """
        infos: list[dict] = []

        try:
            repo = self.app.pygit2_repo
            logger.debug(
                "_prepFileModeFileList_from_pygit2: entry path=%r repo_root=%r pygit2_module_present=%r app.pygit2_repo=%r",
                path,
                self.app.repo_root,
                bool(pygit2),
                repr(repo),
            )
            if repo is None:
                logger.warning(
                    "_prepFileModeFileList_from_pygit2: self.app.pygit2_repo is None — pygit2 disabled or initialization failed"
                )
                return infos

            try:
                items = sorted(os.listdir(path))
            except Exception as _ex:
                self.printException(_ex, "_prepFileModeFileList_from_pygit2: listing path failed")
                items = []

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
                                # Match git-backed helper behavior: leave unknown/clean
                                # tracked files as None so callers can treat them
                                # consistently when `status_map` is absent.
                                # This status represents "tracked_clean" files. None
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

    def _prepFileModeFileList_status_map_from_git(self, path: str) -> dict | None:
        """Build a repo-relative porcelain `status_map` for `path` using git CLI.

        Returns a dict mapping repo-relative paths to the two-char porcelain
        status code (e.g. ' M', 'A ', '??'), or `None` when the map cannot be
        built or `path` is outside the repository.
        """
        try:
            # Ensure this helper only runs when `path` is inside the repo.
            if not path.startswith(self.app.repo_root):
                return None

            prefix = os.path.relpath(path, self.app.repo_root)
            if prefix == ".":
                prefix = ""
            else:
                prefix = prefix + os.sep

            # Include ignored entries so porcelain emits '!!' for ignored files
            # and '??' for untracked files which we parse below.
            cmd = ["git", "-C", self.app.repo_root, "status", "--porcelain", "--ignored"]
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
            return m
        except Exception as e:
            self.printException(e, "prepFileModeFileList: building status_map failed")
            return None

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
                            self.prepFileModeFileList(raw)
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
                # Delegate pseudo-entry collection to backend helpers so
                # implementations can use either `git` or `pygit2`.
                if prev_hash in pseudo_names:
                    if pygit2:
                        pseudo_entries.extend(self._prepRepoModePseudo_from_pygit2(prev_hash))
                    else:
                        pseudo_entries.extend(self._prepRepoModePseudo_from_git(prev_hash))
                if curr_hash in pseudo_names:
                    if pygit2:
                        pseudo_entries.extend(self._prepRepoModePseudo_from_pygit2(curr_hash))
                    else:
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
                # Delegate diff collection to helpers so alternate backends
                # (pygit2 vs git CLI) can provide entries. Each helper returns
                # a list of dicts with keys: display, full, is_dir.
                try:
                    # When testing, run both backends and compare outputs.
                    if self.app.test_pygit2:
                        entries = self._prepRepoModeFileList_from_pygit2(prev_hash, curr_hash)
                        out_git = self._prepRepoModeFileList_from_git(prev_hash, curr_hash)
                        self.compare_pygit2_to_git_output(entries, out_git, "prepRepoModeFileList")
                    elif pygit2:
                        entries = self._prepRepoModeFileList_from_pygit2(prev_hash, curr_hash)
                    else:
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
                                file_infos.append(
                                    {"name": name, "full": full, "is_dir": is_dir, "raw": full, "repo_status": None}
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
                        # Prefer pygit2's workdir when available (non-bare repo).
                        base_workdir = getattr(self.app.pygit2_repo, "workdir", None) or self.app.repo_root
                        if not os.path.isabs(path):
                            full = os.path.realpath(os.path.join(base_workdir, path))
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
        logger.debug("_prepRepoModeFileList_from_pygit2: entry prev_hash=%r curr_hash=%r", prev_hash, curr_hash)
        try:
            if not pygit2:
                return entries
            repo = self.app.pygit2_repo
            logger.debug(
                "_prepRepoModeFileList_from_pygit2: entry repo_root=%r pygit2_module_present=%r app.pygit2_repo=%r",
                self.app.repo_root,
                bool(pygit2),
                repr(repo),
            )
            if repo is None:
                logger.warning(
                    "_prepRepoModeFileList_from_pygit2: self.app.pygit2_repo is None — pygit2 disabled or initialization failed"
                )
                return entries

            # Simplified flow: exactly two meaningful cases exist for callers
            # of this helper: (1) `curr_hash` present and `prev_hash` is None
            # (initial commit case) or (2) both `prev_hash` and `curr_hash`
            # present. If `curr_hash` is missing treat it as an error and
            # return empty results.
            def _resolve_tree(obj):
                try:
                    if obj is None:
                        logger.debug("_resolve_tree: received None")
                        return None

                    logger.debug("_resolve_tree: incoming obj type=%s repr=%r", type(obj), obj)

                    if hasattr(pygit2, "Tag") and isinstance(obj, pygit2.Tag):
                        try:
                            obj = obj.get_object()
                            logger.debug("_resolve_tree: tag.get_object() -> type=%s repr=%r", type(obj), obj)
                        except Exception as _ex:
                            self.printException(
                                _ex,
                                "_prepRepoModeFileList_from_pygit2: tag.get_object() failed — using target fallback",
                            )
                            obj = getattr(obj, "target", obj)
                            logger.debug("_resolve_tree: tag fallback target -> type=%s repr=%r", type(obj), obj)

                    if hasattr(pygit2, "Commit") and isinstance(obj, pygit2.Commit):
                        try:
                            t = obj.tree
                            logger.debug("_resolve_tree: commit.tree -> type=%s repr=%r", type(t), t)
                            return t
                        except Exception as _ex:
                            self.printException(_ex, "_prepRepoModeFileList_from_pygit2: commit.tree access failed")
                            return None

                    if hasattr(pygit2, "Tree") and isinstance(obj, pygit2.Tree):
                        logger.debug("_resolve_tree: obj is Tree")
                        return obj

                    if hasattr(obj, "tree"):
                        try:
                            t = obj.tree
                            logger.debug("_resolve_tree: obj.tree -> type=%s repr=%r", type(t), t)
                            return t
                        except Exception as _ex:
                            self.printException(_ex, "_prepRepoModeFileList_from_pygit2: obj.tree access failed")
                            return None

                    logger.debug("_resolve_tree: could not resolve tree for obj type=%s", type(obj))
                    return None
                except Exception as _ex:
                    self.printException(_ex, "_prepRepoModeFileList_from_pygit2: _resolve_tree failed")
                    return None

            # Build the diff consistently as `git diff <old> <new>` (old->new).
            # Cases:
            # - no refs: working-tree diff (repo.diff())
            # - prev is None, curr present: initial commit -> diff(None, cur_tree)
            # - prev and curr present: diff(prev_tree, curr_tree)
            try:
                if not curr_hash and not prev_hash:
                    # No hashes provided: treat as working-tree diff (repo.diff()).
                    diff = repo.diff()
                elif not curr_hash:
                    # Curr hash missing but prev present — invalid for our
                    # simplified assumptions: log and return empty.
                    self.printException(
                        ValueError("missing curr_hash"), "_prepRepoModeFileList_from_pygit2: curr_hash is required"
                    )
                    return entries
                elif prev_hash is None:
                    try:
                        # Initial commit case: diff from empty tree to curr_hash
                        c = repo.revparse_single(curr_hash)
                    except Exception as _ex:
                        self.printException(
                            _ex, f"_prepRepoModeFileList_from_pygit2: revparse_single failed for {curr_hash}"
                        )
                        return entries
                    cur_tree = _resolve_tree(c)
                    if cur_tree is None:
                        self.printException(
                            ValueError(f"could not resolve tree for {curr_hash}"),
                            "_prepRepoModeFileList_from_pygit2: resolve failed",
                        )
                        return entries
                    try:
                        diff = repo.diff(None, cur_tree)
                    except Exception as _ex:
                        # Some pygit2 versions/configurations raise when one side
                        # of the diff is "None" even if the other side is a
                        # valid tree (initial commit case). As a robust
                        # fallback, construct an explicit empty tree and diff
                        # against that.
                        self.printException(
                            _ex,
                            "_prepRepoModeFileList_from_pygit2: repo.diff(None, cur_tree) failed — trying empty-tree fallback",
                        )
                        try:
                            tb = repo.TreeBuilder()
                            empty_oid = tb.write()
                            empty_tree = repo.get(empty_oid)
                            logger.debug("_prepRepoModeFileList_from_pygit2: constructed empty tree %r", empty_tree)
                            diff = repo.diff(empty_tree, cur_tree)
                        except Exception as _ex2:
                            # If repo.TreeBuilder isn't available or write/get
                            # fail, as a last resort try the reversed arg
                            # order which some pygit2 variants accept.
                            self.printException(
                                _ex2,
                                "_prepRepoModeFileList_from_pygit2: empty-tree fallback failed — trying reversed args",
                            )
                            try:
                                diff = repo.diff(cur_tree, None)
                            except Exception as _ex3:
                                self.printException(
                                    _ex3, "_prepRepoModeFileList_from_pygit2: initial-commit diff fallbacks failed"
                                )
                                return entries
                else:
                    try:
                        a = repo.revparse_single(prev_hash)
                        b = repo.revparse_single(curr_hash)
                    except Exception as _ex:
                        self.printException(
                            _ex,
                            f"_prepRepoModeFileList_from_pygit2: revparse_single failed for {prev_hash} or {curr_hash}",
                        )
                        return entries
                    a_tree = _resolve_tree(a)
                    b_tree = _resolve_tree(b)
                    if a_tree is None or b_tree is None:
                        self.printException(
                            ValueError(f"could not resolve trees for {prev_hash}..{curr_hash}"),
                            "_prepRepoModeFileList_from_pygit2: resolve failed",
                        )
                        return entries
                    try:
                        diff = repo.diff(a_tree, b_tree)
                    except Exception as _ex:
                        self.printException(_ex, "_prepRepoModeFileList_from_pygit2: repo.diff(a_tree, b_tree) failed")
                        return entries
            except Exception as e:
                self.printException(e, "_prepRepoModeFileList_from_pygit2: building diff failed")
                return entries

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
            repo = self.app.pygit2_repo
            logger.debug(
                "_prepRepoModePseudo_from_pygit2: entry repo_root=%r pygit2_module_present=%r app.pygit2_repo=%r",
                self.app.repo_root,
                bool(pygit2),
                repr(repo),
            )
            if repo is None:
                logger.warning(
                    "_prepRepoModePseudo_from_pygit2: self.app.pygit2_repo is None — pygit2 disabled or initialization failed"
                )
                return items

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
                    # When testing, run both backends and compare outputs.
                    if self.app.test_pygit2:
                        pseudo_entries, entries = self._prepFileModeHistoryList_for_pygit2(repo_root, rel_path)
                        pseudo_entries_git, entries_git = self._prepFileModeHistoryList_for_git(repo_root, rel_path)
                        self.compare_pygit2_to_git_output(
                            pseudo_entries, pseudo_entries_git, "prepFileModeHistoryList pseudo_entries"
                        )
                        self.compare_pygit2_to_git_output(entries, entries_git, "prepFileModeHistoryList entries")
                    elif pygit2:
                        pseudo_entries, entries = self._prepFileModeHistoryList_for_pygit2(repo_root, rel_path)
                    else:
                        pseudo_entries, entries = self._prepFileModeHistoryList_for_git(repo_root, rel_path)

                # render pseudo entries first
                try:
                    # Attach timestamps for MODS/STAGED using centralized helper
                    try:
                        if repo_root and pseudo_entries:
                            full_path = path if os.path.isabs(path) else os.path.realpath(os.path.join(repo_root, path))
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
            except Exception as e:
                self.printException(e, "prepFileModeHistoryList collection/render failed")

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
                "--pretty=format:%H\t%aI\t%s",
                "--",
                rel_path,
            ]
            proc = self._run_cmd_log(cmd, label="prepFileModeHistoryList git log")
            lines = (proc.stdout or "").splitlines()
            entries = self._parse_git_log_lines(lines)
        except Exception as e:
            self.printException(e, "_prepFileModeHistoryList_commits_from_git failed")
        # Ensure newest-first ordering by natural tuple ordering (datetime first)
        try:
            entries.sort(reverse=True)
        except Exception as _ex:
            self.printException(_ex, "_prepFileModeHistoryList_commits_from_git sorting failed")
        return entries

    def _prepFileModeHistoryList_commits_from_pygit2(self, repo_root: str, rel_path: str) -> list[tuple[str, str, str]]:
        """Return a list of (hash, date, subject) using pygit2.

        Note: this implementation does not fully implement `--follow` (rename
        tracking). It walks commits reachable from HEAD and records commits
        whose diffs touch `rel_path`.
        """
        entries: list[tuple[str, str, str]] = []
        try:
            repo = self.app.pygit2_repo
            logger.debug(
                "_prepFileModeHistoryList_commits_from_pygit2: entry repo_root=%r pygit2_module_present=%r app.pygit2_repo=%r",
                repo_root,
                bool(pygit2),
                repr(repo),
            )
            if repo is None:
                logger.warning(
                    "_prepFileModeHistoryList_commits_from_pygit2: self.app.pygit2_repo is None — pygit2 disabled or initialization failed"
                )
                return entries

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
                        try:
                            # Enable find_similar/rename detection so we approximate
                            # `git log --follow` behavior and can follow renames.
                            diff.find_similar()
                        except Exception as _ex:
                            # Non-fatal: continue processing deltas even if
                            # rename detection couldn't be enabled.
                            self.printException(_ex)
                        # For non-initial commits, start with touched False
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
                            try:
                                ts = commit.author.time
                                off = getattr(commit.author, "offset", None)
                                if off is not None:
                                    tz = timezone(timedelta(minutes=off))
                                    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)
                                else:
                                    dt = datetime.fromtimestamp(ts)
                                date_stamp = dt.strftime("%Y-%m-%dT%H:%M:%S")
                            except Exception as _ex:
                                self.printException(
                                    _ex, "_prepFileModeHistoryList_commits_from_pygit2 failed parsing commit timestamp"
                                )
                                date_stamp = ""
                            msg = (commit.message or "").splitlines()[0].strip()
                            # store datetime object first for natural sorting
                            try:
                                dt
                            except NameError as _ex:
                                self.printException(
                                    _ex,
                                    "_prepFileModeHistoryList_commits_from_pygit2: dt undefined, using datetime.min",
                                )
                                dt = datetime.min
                            entries.append((dt, h, msg))
                        except Exception as e:
                            self.printException(e, "_prepFileModeHistoryList_commits_from_pygit2 entry build failed")
                except Exception as _ex:
                    printException(_ex)
                    continue
        except Exception as e:
            self.printException(e, "_prepFileModeHistoryList_commits_from_pygit2 failed")
        # Ensure newest-first ordering by natural tuple ordering (datetime first)
        try:
            entries.sort(reverse=True)
        except Exception as _ex:
            self.printException(_ex, "_prepFileModeHistoryList_commits_from_pygit2 sorting failed")
        return entries

    def _prepFileModeHistoryList_for_git(
        self, repo_root: str, rel_path: str
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        """Collect pseudo entries and commits for a single file using git CLI.

        Returns (pseudo_entries, commits) where pseudo_entries is a list of
        (status, desc) tuples and commits is a list of (hash, date, msg).
        """
        pseudo_entries: list[tuple[str, str]] = []
        commits: list[tuple[str, str, str]] = []
        try:
            try:
                cmd = ["git", "-C", repo_root, "diff", "--name-only", "--", rel_path]
                mods = self._run_git_lines(cmd, label="prepFileModeHistoryList diff --name-only")
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_git getting modified file failed")
                mods = []
            try:
                cmd = ["git", "-C", repo_root, "diff", "--name-only", "--cached", "--", rel_path]
                staged = self._run_git_lines(cmd, label="prepFileModeHistoryList diff --name-only --cached")
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_git getting staged file failed")
                staged = []

            # Also consult `git status --porcelain` to detect untracked/ignored
            try:
                cmd = ["git", "-C", repo_root, "status", "--porcelain", "--", rel_path]
                por = self._run_git_lines(cmd, label="prepFileModeHistoryList git status --porcelain")
                if por:
                    code = por[0][:2]
                    if code == "??":
                        pseudo_entries.append(("UNTRACKED", "UNTRACKED (untracked)"))
                    elif code == "!!":
                        pseudo_entries.append(("IGNORED", "IGNORED (ignored)"))
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_git git status check failed")

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

    def _prepFileModeHistoryList_for_pygit2(
        self, repo_root: str, rel_path: str
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        """Collect pseudo entries and commits for a single file using pygit2.

        Returns (pseudo_entries, commits) similar to the git helper.
        """
        pseudo_entries: list[tuple[str, str]] = []
        commits: list[tuple[str, str, str]] = []
        try:
            repo = self.app.pygit2_repo
            logger.debug(
                "_prepFileModeHistoryList_for_pygit2: entry repo_root=%r pygit2_module_present=%r app.pygit2_repo=%r",
                repo_root,
                bool(pygit2),
                repr(repo),
            )
            if repo is None:
                logger.warning(
                    "_prepFileModeHistoryList_for_pygit2: self.app.pygit2_repo is None — pygit2 disabled or initialization failed"
                )
                return (pseudo_entries, commits)

            try:
                status = repo.status_file(rel_path)
            except Exception as e:
                self.printException(e, "_prepFileModeHistoryList_for_pygit2: repo.status_file failed")
                status = 0

            try:
                # Treat untracked (WT_NEW) separately so it matches git
                # `status --porcelain` semantics (emit UNTRACKED, not MODS).
                wt_new = getattr(pygit2, "GIT_STATUS_WT_NEW", 0)
                if status & wt_new:
                    pseudo_entries.append(("UNTRACKED", "UNTRACKED (untracked)"))
                else:
                    mods_flags = (
                        pygit2.GIT_STATUS_WT_MODIFIED
                        | pygit2.GIT_STATUS_WT_RENAMED
                        | pygit2.GIT_STATUS_WT_TYPECHANGE
                        | pygit2.GIT_STATUS_WT_DELETED
                    )
                    if status & mods_flags:
                        pseudo_entries.append(("MODS", "MODS (modified, unstaged)"))

                index_flags = (
                    pygit2.GIT_STATUS_INDEX_NEW
                    | pygit2.GIT_STATUS_INDEX_MODIFIED
                    | pygit2.GIT_STATUS_INDEX_RENAMED
                    | pygit2.GIT_STATUS_INDEX_TYPECHANGE
                    | pygit2.GIT_STATUS_INDEX_DELETED
                )
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
        self, repo_path: str | None = None, prev_hash: str | None = None, curr_hash: str | None = None
    ) -> None:
        """Prepare the repository-wide commit history view.

        `repo_path` may narrow the view to a subpath; `prev_hash` and
        `curr_hash` may be used to constrain the commit range.
        """
        try:
            logger.debug(
                "prepRepoModeHistoryList: repo_path=%r prev_hash=%r curr_hash=%r", repo_path, prev_hash, curr_hash
            )
            self.clear()
            # Collect pseudo-entries and commit rows via backend helpers
            try:
                # When testing, run both backends and compare outputs.
                if self.app.test_pygit2:
                    pseudo_entries, commits = self._prepRepoModeHistoryList_for_pygit2(repo_path, prev_hash, curr_hash)
                    pseudo_entries_git, commits_git = self._prepRepoModeHistoryList_for_git(
                        repo_path, prev_hash, curr_hash
                    )
                    self.compare_pygit2_to_git_output(
                        pseudo_entries, pseudo_entries_git, "prepRepoModeHistoryList pseudo_entries"
                    )
                    self.compare_pygit2_to_git_output(commits, commits_git, "prepRepoModeHistoryList commits")
                elif pygit2:
                    pseudo_entries, commits = self._prepRepoModeHistoryList_for_pygit2(repo_path, prev_hash, curr_hash)
                else:
                    pseudo_entries, commits = self._prepRepoModeHistoryList_for_git(repo_path, prev_hash, curr_hash)

                # Insert MODS then STAGED at the top if present
                try:
                    if pseudo_entries:
                        # pseudo_entries contains tuples like (status, path) and may include MODS/STAGED labels
                        for status, path in pseudo_entries:
                            logger.debug("prepRepoModeHistoryList: processing pseudo-entry: %r %r", status, path)
                            # status may be 'MODS' or 'STAGED' summary rows represented specially
                            if status in ("MODS", "STAGED"):
                                # path carries the count/caption
                                logger.debug("prepRepoModeHistoryList: adding pseudo-summary row: %r %r", path, status)
                                self._add_row(path, status)
                                continue
                            # otherwise these are file tuples (status, path)
                            display = f"{status} {path}"
                            # Use _add_row so row padding/formatting matches commits
                            try:
                                logger.debug("prepRepoModeHistoryList: adding pseudo file row: %r %r", display, status)
                                self._add_row(display, status)
                            except Exception as e:
                                self.printException(e, "prepRepoModeHistoryList: adding pseudo row failed")
                                continue
                            # Attach metadata similar to previous manual creation
                            try:
                                nodes = self.nodes()
                                if nodes:
                                    last = nodes[-1]
                                    try:
                                        if not os.path.isabs(path):
                                            full = os.path.realpath(os.path.join(self.app.repo_root, path))
                                        else:
                                            full = os.path.realpath(path)
                                        setattr(last, "_raw_text", full)
                                    except Exception as e:
                                        self.printException(e, "prepRepoModeHistoryList: resolving full path failed")
                                        setattr(last, "_raw_text", path)
                                    try:
                                        setattr(last, "_is_dir", False)
                                    except Exception as e:
                                        self.printException(e, "prepRepoModeHistoryList: setting _is_dir failed")
                            except Exception as e:
                                self.printException(e, "prepRepoModeHistoryList setting pseudo row metadata failed")
                except Exception as e:
                    self.printException(e, "prepRepoModeHistoryList adding pseudo-rows failed")

                # Render commit rows
                try:
                    for ts, commit_hash, msg in commits:
                        try:
                            try:
                                if hasattr(ts, "strftime"):
                                    date_stamp = ts.strftime("%Y-%m-%dT%H:%M:%S")
                                else:
                                    date_stamp = str(ts)
                            except Exception as _ex:
                                self.printException(_ex, "prepRepoModeHistoryList: date_stamp formatting failed")
                                date_stamp = str(ts)
                            short_hash = commit_hash[:HASH_LENGTH] if commit_hash else ""
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
            # the `curr_hash`. If a `prev_hash` is also provided, mark it
            # (single-check semantics) so callers may use a checked row as
            # one side of the commit pair.
            try:
                if curr_hash:
                    self._highlight_match(curr_hash)
                    # If prev_hash also provided, mark that row as checked
                    if prev_hash:
                        try:
                            for i, node in enumerate(self.nodes()):
                                if getattr(node, "_hash", None) == prev_hash:
                                    try:
                                        logger.debug(
                                            "prepRepoModeHistoryList: invoking toggle_check_current at index=%d for prev_hash=%r",
                                            i,
                                            prev_hash,
                                        )
                                        self.toggle_check_current(i)
                                    except Exception as e:
                                        self.printException(e, "prepRepoModeHistoryList toggle_check_current failed")
                                    break
                        except Exception as e:
                            self.printException(e, "prepRepoModeHistoryList marking prev_hash failed")
                elif prev_hash:
                    # find and mark the previous commit row
                    for i, node in enumerate(self.nodes()):
                        if getattr(node, "_hash", None) == prev_hash:
                            try:
                                logger.debug(
                                    "prepRepoModeHistoryList: invoking toggle_check_current at index=%d for prev_hash=%r",
                                    i,
                                    prev_hash,
                                )
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
                self.printException(e, "prepRepoModeHistoryList: compute selected pair failed")

            # Centralize post-prep finalization so hashes/selection/path
            # synchronization happens in one place.
            try:
                self._finalize_historylist_prep(curr_hash=curr_hash, prev_hash=prev_hash, path=repo_path)
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: finalize failed")
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

            # add summary pseudo rows first if present and attach timestamps
            try:
                try:
                    mods_ts, staged_ts = self._compute_pseudo_timestamps(self.app.repo_root, mods=mods, single_path="")
                except Exception as _ex:
                    self.printException(_ex, "_prepRepoModeHistoryList_for_git computing pseudo timestamps failed")

                if mods:
                    date_part = mods_ts.strip() if mods_ts else ""
                    status_short = "MODS"
                    msg = f"({len(mods)} modified file{'s' if len(mods) != 1 else ''})"
                    display = f"{date_part} {status_short[:HASH_LENGTH]} {msg}".strip()
                    pseudo_entries.append(("MODS", display))
                if staged:
                    date_part = staged_ts.strip() if staged_ts else ""
                    status_short = "STAGED"
                    msg = f"({len(staged)} staged file{'s' if len(staged) != 1 else ''})"
                    display = f"{date_part} {status_short[:HASH_LENGTH]} {msg}".strip()
                    pseudo_entries.append(("STAGED", display))
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_git adding pseudo summaries failed")

            # collect commits
            try:
                if self.app.repo_root:
                    cmd = [
                        "git",
                        "-C",
                        self.app.repo_root,
                        "log",
                        "--pretty=format:%H\t%aI\t%s",
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
                            date_s = parts[1] if len(parts) > 1 else ""
                            msg = parts[2] if len(parts) > 2 else ""
                            try:
                                dt = datetime.fromisoformat(date_s) if date_s else datetime.min
                            except Exception as _ex:
                                self.printException(
                                    _ex, f"_prepRepoModeHistoryList_for_git failed parsing ISO datetime '{date_s}'"
                                )
                                try:
                                    dt = datetime.strptime(date_s, "%Y-%m-%d") if date_s else datetime.min
                                except Exception as _ex:
                                    self.printException(
                                        _ex, f"_prepRepoModeHistoryList_for_git failed parsing date-only '{date_s}'"
                                    )
                                    dt = datetime.min
                            commits.append((dt, commit_hash, msg))
                        except Exception as e:
                            self.printException(e, "_prepRepoModeHistoryList_for_git parse failed")
            except subprocess.CalledProcessError as _ex:
                printException(_ex)
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_git git log failed")
        except Exception as e:
            self.printException(e, "_prepRepoModeHistoryList_for_git failed")
        # Sort commits newest-first by natural tuple ordering (datetime first)
        try:
            commits.sort(reverse=True)
        except Exception as _ex:
            self.printException(_ex, "_prepRepoModeHistoryList_for_git sorting failed")
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
            repo = self.app.pygit2_repo

            # status map: repo.status() -> dict[path] = flags
            try:
                status_map = repo.status()
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_pygit2: repo.status() failed")
                status_map = {}

            # Exclude WT_NEW from `mods` so it matches `git diff` behavior
            # (which doesn't list untracked files). Treat WT_NEW separately
            # if callers need untracked information.
            wt_new = getattr(pygit2, "GIT_STATUS_WT_NEW", 0)
            mods = [
                p
                for p, f in status_map.items()
                if f
                & (
                    pygit2.GIT_STATUS_WT_MODIFIED
                    | pygit2.GIT_STATUS_WT_RENAMED
                    | pygit2.GIT_STATUS_WT_TYPECHANGE
                    | pygit2.GIT_STATUS_WT_DELETED
                )
            ]
            staged = [
                p
                for p, f in status_map.items()
                if f
                & (
                    pygit2.GIT_STATUS_INDEX_NEW
                    | pygit2.GIT_STATUS_INDEX_MODIFIED
                    | pygit2.GIT_STATUS_INDEX_RENAMED
                    | pygit2.GIT_STATUS_INDEX_TYPECHANGE
                    | pygit2.GIT_STATUS_INDEX_DELETED
                )
            ]

            try:
                # Compute timestamp for most recently modified working-tree file
                mods_ts = ""
                try:
                    latest_m = None
                    for p in mods:
                        try:
                            full = os.path.join(self.app.repo_root, p)
                            if os.path.exists(full):
                                m = os.path.getmtime(full)
                                if latest_m is None or m > latest_m:
                                    latest_m = m
                        except Exception as e:
                            self.printException(
                                e, "_prepRepoModeHistoryList_for_pygit2 skipping file mtime due to error"
                            )
                            continue
                    if latest_m is not None:
                        mods_ts = " " + datetime.fromtimestamp(latest_m).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
                except Exception as _ex:
                    self.printException(_ex, "_prepRepoModeHistoryList_for_pygit2 computing mods timestamp failed")

                # Compute timestamp for index (last staged change)
                staged_ts = ""
                try:
                    idx_path = os.path.join(self.app.repo_root, ".git", "index")
                    if os.path.exists(idx_path):
                        m = os.path.getmtime(idx_path)
                        staged_ts = " " + datetime.fromtimestamp(m).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
                except Exception as _ex:
                    self.printException(_ex, "_prepRepoModeHistoryList_for_pygit2 computing staged timestamp failed")

                if mods:
                    date_part = mods_ts.strip() if mods_ts else ""
                    status_short = "MODS"
                    msg = f"({len(mods)} modified file{'s' if len(mods) != 1 else ''})"
                    display = f"{date_part} {status_short[:HASH_LENGTH]} {msg}".strip()
                    pseudo_entries.append(("MODS", display))
                    logger.debug("_prepRepoModeHistoryList_for_pygit2: detected modified files: %r", mods)
                if staged:
                    date_part = staged_ts.strip() if staged_ts else ""
                    status_short = "STAGED"
                    msg = f"({len(staged)} staged file{'s' if len(staged) != 1 else ''})"
                    display = f"{date_part} {status_short[:12]} {msg}".strip()
                    pseudo_entries.append(("STAGED", display))
                    logger.debug("_prepRepoModeHistoryList_for_pygit2: detected staged files: %r", staged)
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_pygit2 adding pseudo summaries failed")

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
                            commit_hash = str(c.id)
                            # use author time for date
                            t = getattr(c.author, "time", None)
                            if t:
                                try:
                                    off = getattr(c.author, "offset", None)
                                    if off is not None:
                                        tz = timezone(timedelta(minutes=off))
                                        dt = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(tz)
                                    else:
                                        dt = datetime.fromtimestamp(t)
                                    date_stamp = dt.strftime("%Y-%m-%dT%H:%M:%S")
                                except Exception as _ex:
                                    self.printException(
                                        _ex, "_prepRepoModeHistoryList_for_pygit2 failed parsing commit timestamp"
                                    )
                                    date_stamp = ""
                            else:
                                date_stamp = ""
                            msg = (c.message or "").splitlines()[0]
                            # ensure dt exists when t missing
                            try:
                                dt
                            except NameError as _ex:
                                self.printException(
                                    _ex, "_prepRepoModeHistoryList_for_pygit2: dt undefined, using datetime.min"
                                )
                                dt = datetime.min
                            commits.append((dt, commit_hash, msg))
                            count += 1
                            if count >= 200:
                                break
                        except Exception as e:
                            self.printException(e, "_prepRepoModeHistoryList_for_pygit2 walk parse failed")
            except Exception as e:
                self.printException(e, "_prepRepoModeHistoryList_for_pygit2 commit walk failed")
        except Exception as e:
            self.printException(e, "_prepRepoModeHistoryList_for_pygit2 failed")
        # Sort commits newest-first by natural tuple ordering (datetime first)
        try:
            commits.sort(reverse=True)
        except Exception as _ex:
            self.printException(_ex, "_prepRepoModeHistoryList_for_pygit2 sorting failed")
        logger.trace("Pseudo_entries: %r", pseudo_entries)
        logger.trace("Commits: %r", commits)
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
        """Enter-key handler — same behavior as Right: open the commit-pair file list."""
        logger.debug("RepoModeHistoryList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_right(event, recursive=True)


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
                    # Determine whether metadata diff should use --cached.
                    # When comparing STAGED <-> MODS prefer non-cached (index
                    # vs working tree). Otherwise use --cached if either side
                    # is STAGED so we compare index to HEAD when appropriate.
                    if prev in pseudo_names and curr in pseudo_names and {prev, curr} == {"STAGED", "MODS"}:
                        use_cached = False
                    else:
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
    for a selected file/commit pair. It can use the `git` CLI or `pygit2`
    as backends for status and history operations.

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
- If available, the app uses `pygit2` for its work. If `pygit2` is not installed or
    cannot open the repository, the app falls back to using the `git` CLI.
"""

#    - `toggle-color` / `c`: toggle colorized diff output.
#    - `cycle-diff-variant` / `d`: cycle to the next diff variant (e.g. ignore-space-change, patience).
# Command palette (^P):
# - Press Ctrl-P (Textual command palette) to run commands directly. Useful
#    commands to wire up include:
#    - `open-file`, `diff <file> [prev] <curr>`, `file-history <path>`,
#        `goto-commit <hash>`, `toggle-color`, `next-hunk`, `prev-hunk`,
#        `stage <path>`, `unstage <path>`, `refresh`, `use-pygit2 on|off`.


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
        path: str,
        no_color: bool,
        repo_first: bool,
        repo_hashes: list,
        repo_root: str,
        test_pygit2: bool = False,
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
                    self.printException(e, "GitHistoryNavTool.__init__: #3 pygit2.Repository init failed")
                    globals()["pygit2"] = None  # disable pygit2 usage on failure (module-level)
            logger.debug("GitHistoryNavTool.__init__: pygit2=%r, pygit2_repo=%r", pygit2, self.pygit2_repo)
            logger.debug("================================================")

            # Optional diff variant arguments indexed by variant_index.
            # index 0 -> None (no extra arg), 1 -> ignore-space-change, 2 -> patience algorithm
            self.diff_variants: list[Optional[str]] = [None, "--ignore-space-change", "--diff-algorithm=patience"]

            # Initialize `_current_path` to either the provided path when
            # it's a directory, or the dirname when `path` is a file.
            # Use the property setter so the value is canonicalized.
            self._current_path = self.path if os.path.isdir(self.path) else os.path.dirname(self.path)
            # Test mode: if True, prep helpers will run both pygit2 and git
            # implementations and compare their outputs for discrepancies.
            self.test_pygit2 = bool(test_pygit2)
            if self.test_pygit2 and not pygit2:
                logger.warning(
                    "GitHistoryNavTool.__init__: test_pygit2=True but pygit2 module not available; disabling test mode"
                )
                self.test_pygit2 = False
        except Exception as e:
            self.printException(e, "GitHistoryNavTool.__init__ failed")

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
        """Setter for `current_path` which canonicalizes and stores a realpath.

        Treats falsy values as `.` and logs failures while preserving the
        original value on error.
        """
        try:
            # Treat empty/false as '.' and always store realpath
            p = value if value else "."
            self._current_path = os.path.realpath(p)
        except Exception as e:
            # Fall back to storing raw value and log
            self.printException(e, "setting current_path failed")
            self._current_path = value

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
                            self.repo_mode_history_list.prepRepoModeHistoryList(
                                repo_path=self.path or ".", prev_hash=prev, curr_hash=curr
                            )
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
            prev = getattr(app, "previous_hash", None)
            curr = getattr(app, "current_hash", None)
            path = getattr(app, "path", None) or getattr(app, "current_path", None) or "."

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
                        app.file_mode_file_list.prepFileModeFileList(path=path)
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
                # When both sides are pseudo (e.g. STAGED and MODS) prefer
                # the working-tree vs index comparison (git diff) so users
                # see what is staged vs what is modified. Only use
                # --cached when appropriate (e.g. comparing staged vs a
                # concrete commit).
                if prev in pseudo_names and curr in pseudo_names and {prev, curr} == {"STAGED", "MODS"}:
                    use_cached = False
                else:
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
        try:
            # When toggling from file_fullscreen, populate the repo history
            # so the paired history_fullscreen view is ready.
            self.repo_mode_history_list.prepRepoModeHistoryList(repo_path=self.path or ".")
        except Exception as e:
            self.printException(e, "toggle_file_fullscreen prepRepoModeHistoryList failed")
        try:
            self.change_state("history_fullscreen", f"#{LEFT_HISTORY_LIST_ID}", LEFT_HISTORY_FOOTER)
        except Exception as e:
            self.printException(e, "toggle_file_fullscreen change_state failed")

    def toggle_history_fullscreen(self) -> None:
        """Toggle between history fullscreen and the paired file fullscreen view.

        Prepares the file list and sets focus/footers appropriately.
        """
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
        """Switch to a history view for the current file and prepare paired file list.

        Reads authoritative commit hashes after preparing the repo history and
        then prepares the repo file list highlighting the canonical filename.
        """
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
        """Switch to file-history layout for the current file and prepare lists.

        Prepares the right file list and the file's history preparer, then
        switches the UI to the paired layout.
        """
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
        "-P", "--no-pygit2", dest="no_pygit2", action="store_true", help="disable pygit2 usage even if installed"
    )
    parser.add_argument(
        "-T",
        "--test-pygit2",
        dest="test_pygit2",
        action="store_true",
        help="run both pygit2 and git helpers and compare their outputs",
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
            test_pygit2=bool(args.test_pygit2),
        )
        # Run the textual app (blocks until exit)
        app.run()
        return 0
    except Exception as e:
        printException(e, "fatal error running GitHistoryNavTool")
        return 2


if __name__ == "__main__":
    sys.exit(main())
