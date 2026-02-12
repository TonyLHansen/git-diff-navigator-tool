"""Repository helper module extracted from gitdiffnavtool.

Provides: `printException`, `AppException`, and `GitRepo`.

This module is intentionally self-contained so other modules can import
these helpers without depending on the larger UI code.
"""
from __future__ import annotations

import codecs
import hashlib
import logging
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from subprocess import CalledProcessError, check_output
from typing import Optional


logger = logging.getLogger(__name__)


def printException(e: Exception, msg: Optional[str] = None) -> None:
    """Module-level helper to log unexpected exceptions when `self` isn't available.

    Mirrors the widget-level `printException` helper used by widgets.
    """
    try:
        short_msg = msg or ""
        logger.warning("%s: %s", short_msg, e)
        logger.warning(traceback.format_exc())
    except Exception as _use_stderr:
        # Last-resort fallback to stderr — avoid recursive logging
        sys.stderr.write(f"printException fallback: {e}\n")
        sys.stderr.write(f"secondary exception: {_use_stderr}\n")


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

    gitRepo.getNormalizedHashListComplete()
        Returns a list of the hashes, their timestamps and commit messages for the entire repository.
    gitRepo.getNormalizedHashListFromFileName(filename)
        Returns a list of the hashes, their timestamps and commit messages associated with the specified filename.
        The filename is relative to the repoRoot.

    gitRepo.reset_cache() will reset the cache used by GitRepo's functions. Use this if you ever wish
    to have gitRepo restart with a fresh view of the repository.

    other getFileList and getHashList functions also exist



    Internally the git command is used to retrieve the information and cached.

    Note: an earlier version of this class used pyGit2, but it was found to produce
    results for getNormalizedHashListFromFileName() and gitRepo.getFileListBetweenNormalizedHashes()
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
        self._repoRoot = resolved

    def reset_cache(self) -> None:
        """Reset the per-process command/result cache."""
        self._cmd_cache = {}

    @classmethod
    def resolve_repo_top(cls, path: str, raise_on_missing: bool = False) -> tuple[str | None, Exception | None]:
        """Resolve the repository top-level directory for *path*.

        Returns a tuple ``(path_or_none, error_or_none)``. If
        ``raise_on_missing`` is True this function raises on failure.
        """
        if not path:
            e = RuntimeError("resolve_repo_top: empty path")
            if raise_on_missing:
                raise e
            return (None, e)
        cur_dir = os.path.abspath(path)
        logger.debug("resolve_repo_top: input path=%r computed cur=%r", path, cur_dir)
        if os.path.isfile(cur_dir):
            cur_dir = os.path.dirname(cur_dir)
            logger.debug("resolve_repo_top: path is a file, using parent dir cur=%r", cur_dir)
        cmd = ["git", "rev-parse", "--show-toplevel"]
        logger.debug("resolve_repo_top: running git command %r in cwd=%r", cmd, cur_dir)
        try:
            out = check_output(cmd, cwd=cur_dir, text=True)
            # log raw output before stripping to aid diagnosis of unexpected extra lines
            logger.debug("resolve_repo_top: raw output=%r", out)
            out = out.strip()
            logger.debug("resolve_repo_top: stripped output=%r", out)
            return (out, None)
        except FileNotFoundError as _use_raise:
            if raise_on_missing:
                raise RuntimeError("git not available on PATH") from _use_raise
            return (None, _use_raise)
        except CalledProcessError as _use_raise:
            if raise_on_missing:
                raise RuntimeError(f"not a git working tree: {path}") from _use_raise
            return (None, _use_raise)
        except Exception as _use_raise:
            if raise_on_missing:
                raise RuntimeError(f"not a git working tree: {path}") from _use_raise
            return (None, _use_raise)

    @classmethod
    def relpath_if_within(cls, base_path: str, conv_path: str) -> str | None:
        """Return ``conv_path`` relative to ``base_path`` if inside it.

        If ``conv_path`` is not contained within ``base_path`` return
        ``None``. Raises ``ValueError`` for empty inputs.
        """
        if not base_path:
            raise ValueError("relpath_if_within: empty base_path")
        if not conv_path:
            raise ValueError("relpath_if_within: empty conv_path")
        try:
            if base_path[0] != os.sep:
                base_path = os.path.abspath(os.path.join(os.getcwd(), base_path))
            base_path = os.path.abspath(os.path.normpath(base_path))
            if conv_path[0] != os.sep:
                conv_path = os.path.abspath(os.path.join(os.getcwd(), conv_path))
            conv_path = os.path.abspath(os.path.normpath(conv_path))
            if base_path == conv_path:
                common = ""
            else:
                common = os.path.relpath(conv_path, base_path)
            return common
        except Exception as _use_raise:
            raise ValueError(f"relpath_if_within: path evaluation failed: {_use_raise}") from _use_raise

    def get_repo_root(self) -> str:
        """Return the resolved repository root path for this GitRepo."""
        return self._repoRoot

    def full_path_for(self, rel_dir: str | None, rel_file: str | None) -> str:
        """Return an absolute path for a repo-relative directory/file pair.

        Validates that the computed path remains inside the repository
        root and raises ``ValueError`` on invalid inputs.
        """
        if rel_dir is None:
            rel_dir = ""
        if rel_file is None:
            rel_file = ""

        if rel_dir and os.path.isabs(rel_dir):
            raise ValueError("full_path_for: rel_dir must be repository-relative (not absolute)")
        if rel_file and os.path.isabs(rel_file):
            raise ValueError("full_path_for: rel_file must be repository-relative (not absolute)")

        try:
            full = os.path.normpath(os.path.join(self._repoRoot, rel_dir or "", rel_file or ""))
        except Exception as e:
            printException(e, "full_path_for: failed to construct path")
            raise ValueError(f"full_path_for: failed to construct path: {e}") from e

        try:
            repo_norm = os.path.normpath(self._repoRoot)
            common = os.path.commonpath([repo_norm, full])
            if common != repo_norm:
                raise ValueError("full_path_for: computed path is outside the repository root")
        except Exception as e:
            printException(e, "full_path_for: validation failed")
            raise ValueError(f"full_path_for: validation failed: {e}") from e

        return full

    def _deltas_to_results(self, detailed: list, a_raw, b_raw) -> list[tuple[str, str]]:
        """Convert detailed delta dicts to a list of ``(path, status)``.

        Robust, small translator used by higher-level diff helpers.
        """
        try:
            if not detailed:
                return []
            results: list[tuple[str, str]] = []
            for item in detailed:
                try:
                    status = item.get("status") or "modified"
                    path = item.get("path") or item.get("new_path") or item.get("old_path")
                    if not path:
                        continue
                    if isinstance(status, str) and status.startswith("renamed->"):
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
        """Return an ISO timestamp (UTC) based on the repository index modification time."""

        idx_candidates = [
            os.path.join(self._repoRoot, ".git", "index"),
            os.path.join(self._repoRoot, "index"),
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
        """Format an epoch seconds value as an ISO UTC timestamp string."""
        try:
            return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception as e:
            self.printException(e, "_epoch_to_iso: formatting timestamp failed")
            return "1970-01-01T00:00:00"

    def _git_cli_decode_quoted_path(self, rel: str) -> str:
        """Decode a git-quoted path string (surrounded by double quotes).

        Handles C-style escapes and returns a UTF-8 (or latin-1) decoded
        string. If input is not quoted, returns it unchanged.
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
        """Return the modification time for a repository-relative path.

        Returns ``None`` if the file does not exist. Handles symlinks
        and logs errors via ``printException``.
        """
        try:
            fp = os.path.join(self._repoRoot, rel)
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
        """Create a compact cache key from ``name`` and ``args``.

        Produces ``name:HEX`` where HEX is the first 16 chars of the
        sha256 of the repr of the payload.
        """
        try:
            payload = (name,) + tuple(args)
            h = hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()[:16]
            return f"{name}:{h}"
        except Exception as e:
            self.printException(e, "_make_cache_key: failed to build key")
            return f"{name}:{hashlib.sha256(name.encode()).hexdigest()[:16]}"

    def _paths_mtime_iso(self, paths: list[str]) -> str:
        """Return ISO timestamp for the most-recent mtime among ``paths``.

        Falls back to the repository index mtime if no mtimes available.
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
        """Compute an ISO timestamp for the pseudo-`NEWREPO` entry.

        Strategy: use the first commit timestamp or the oldest mtime under
        ``.git``; fall back to the repository index mtime when unavailable.
        """
        first_commit_ts: float | None = None
        git_dir = os.path.join(self._repoRoot, ".git")
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
                        self.printException(_no_logging, "_newrepo_timestamp_iso: parsing commit ts failed")
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
                        except Exception as e:
                            self.printException(e, "_newrepo_timestamp_iso: stat file failed")
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

        return self.index_mtime_iso()

    def _parse_git_log_output(self, output: str) -> list[tuple[int, str, str]]:
        """Parse lines of ``"%ct %H %s"`` style git log output into tuples.

        Returns a list of ``(timestamp_int, hash, subject)``.
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
        """Parse git "--name-status" style output into (path,status) pairs.

        Returns a sorted list of tuples.
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

    def _git_cli_getCachedFileList(self, key: str, git_args: list) -> list[tuple[str, str]]:
        """Run git command in ``git_args``, parse and cache name-status list.

        Returns a sorted list of ``(path,status)`` or ``[]`` on failure.
        """
        try:
            if key in self._cmd_cache:
                return self._cmd_cache[key]
            output = self._git_run(git_args, text=True, cache_key=key)
            results = self._git_cli_parse_name_status_output(output or "")
            self._cmd_cache[key] = results
            return results
        except Exception as e:
            self.printException(e, "_git_cli_getCachedFileList: unexpected failure")
            return []

    def _git_run(self, args: list, text: bool = True, cache_key: str | None = None):
        """Execute a git subprocess and return its output (string or bytes).

        Caches results and returns an empty string on failure.
        """
        try:
            internal_key = f"_git_run:{' '.join(args)}:{'text' if text else 'bytes'}"
            if internal_key in self._cmd_cache:
                return self._cmd_cache[internal_key]
            try:
                out = check_output(args, cwd=self._repoRoot, text=text)
            except CalledProcessError as e:
                self.printException(e, f"_git_run: git command failed: {' '.join(args)}")
                if cache_key:
                    self._cmd_cache[cache_key] = []
                    self._cmd_cache[internal_key] = ""
                    return ""
                self._cmd_cache[internal_key] = ""
                return ""
            self._cmd_cache[internal_key] = out
            return out
        except Exception as e:
            self.printException(e, "_git_run: unexpected failure")
            if cache_key:
                self._cmd_cache[cache_key] = []
                self._cmd_cache[internal_key] = ""
                return ""
            self._cmd_cache[internal_key] = ""
            return ""



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
        return self._repoRoot

    def full_path_for(self, rel_dir: str | None, rel_file: str | None) -> str:
        """Return an absolute filesystem path for a repository-relative directory/file pair.

        Contract:
        - `rel_dir` is a repository-relative directory path (relative to the repo root),
          or an empty string / None to indicate the repo root itself.
        - `rel_file` is a filename (possibly including subpath) relative to `rel_dir`,
          or an empty string / None to indicate the directory itself.

        The function validates that neither argument is absolute and that the
        resulting path resides within the repository root. A normalized
        absolute path is returned on success; a `ValueError` is raised on
        invalid inputs or if the computed path would escape the repository.
        """
        if rel_dir is None:
            rel_dir = ""
        if rel_file is None:
            rel_file = ""

        # Reject absolute components — callers should pass repository-relative
        if rel_dir and os.path.isabs(rel_dir):
            raise ValueError("full_path_for: rel_dir must be repository-relative (not absolute)")
        if rel_file and os.path.isabs(rel_file):
            raise ValueError("full_path_for: rel_file must be repository-relative (not absolute)")

        # Join and normalize
        try:
            full = os.path.normpath(os.path.join(self._repoRoot, rel_dir or "", rel_file or ""))
        except Exception as e:
            printException(e, "full_path_for: failed to construct path")
            raise ValueError(f"full_path_for: failed to construct path: {e}") from e

        # Ensure the resulting path is inside the repository root
        try:
            repo_norm = os.path.normpath(self._repoRoot)
            common = os.path.commonpath([repo_norm, full])
            if common != repo_norm:
                raise ValueError("full_path_for: computed path is outside the repository root")
        except Exception as e:
            printException(e, "full_path_for: validation failed")
            raise ValueError(f"full_path_for: validation failed: {e}") from e

        return full

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
            os.path.join(self._repoRoot, ".git", "index"),
            os.path.join(self._repoRoot, "index"),
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
            fp = os.path.join(self._repoRoot, rel)
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
        git_dir = os.path.join(self._repoRoot, ".git")
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

    def _git_name_status_dispatch(
        self, prev: str | None = None, curr: str | None = None, cached: bool = False, key: str | None = None
    ) -> list[tuple[str, str]]:
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
            cache_key = key or self._make_cache_key("git_name_status", prev, curr, "cached" if cached else "nocache")
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
                out = check_output(args, cwd=self._repoRoot, text=text)
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

    def getNormalizedHashListComplete(self) -> list[tuple[str, str, str]]:
        """Return a combined list of commit hashes for staged, new, and entire repo."""
        new = self.getHashListNewChanges()
        staged = self.getHashListStagedChanges()
        entire = self.getHashListEntireRepo()
        newrepo = self.getHashListNewRepo()
        combined = new + staged + entire + newrepo
        return combined

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

    def getNormalizedHashListFromFileName(self, file_name: str) -> list[tuple[str, str, str]]:
        """Return a list of commit hashes that modified the given file.

        Uses the git CLI (`git log` + `git status`) with a one-time cache per
        `file_name`. The previous walk/diff implementation has been
        removed for performance consistency.
        """
        key = self._make_cache_key("getNormalizedHashListFromFileName", file_name)
        try:
            if key in self._cmd_cache:
                return self._cmd_cache[key]

            output = self._git_run(
                ["git", "log", "--pretty=format:%ct %H %s", "--", file_name], text=True, cache_key=key
            )

            parsed_entries: list[tuple[str, str, str]] = []
            parsed = self._parse_git_log_output(output or "")
            for ts, h, subject in parsed:
                iso = self._epoch_to_iso(ts)
                parsed_entries.append((iso, h, subject if subject else ""))

            # Inspect working-tree/index status for the file and construct
            # explicit pseudo-entries for MODS/STAGED when present. We will
            # then assemble the final list in newest->oldest order with a
            # deterministic placement for these pseudo-entries so callers
            # that reverse the list (oldest->newest) observe STAGED before
            # MODS.
            status_out = self._git_run(["git", "status", "--porcelain", "--", file_name], text=True) or ""
            idx_flag = " "
            wt_flag = " "
            if status_out:
                s = status_out.splitlines()[0]
                if len(s) >= 2:
                    idx_flag = s[0]
                    wt_flag = s[1]
                else:
                    idx_flag = s[0] if s else " "
                    wt_flag = " "

            staged_entry = None
            mods_entry = None
            try:
                if idx_flag != " ":
                    iso_index = self.index_mtime_iso()
                    staged_entry = (iso_index, "STAGED", self.STAGED_MESSAGE)
                if wt_flag != " ":
                    iso_mods = self._paths_mtime_iso([file_name])
                    mods_entry = (iso_mods, "MODS", self.MODS_MESSAGE)
            except Exception as e:
                self.printException(e, "getNormalizedHashListFromFileName: computing pseudo-entry timestamps failed")

            # Assemble final entries in newest->oldest order. Place MODS
            # before STAGED here so that callers that reverse the list
            # (oldest->newest) will see STAGED before MODS.
            entries: list[tuple[str, str, str]] = []
            if mods_entry is not None:
                entries.append(mods_entry)
            if staged_entry is not None:
                entries.append(staged_entry)

            # Append parsed commits newest->oldest
            parsed_entries.sort(key=lambda x: x[0], reverse=True)
            entries.extend(parsed_entries)

            # Cache and return
            self._cmd_cache[key] = entries
            return entries
        except Exception as e:
            self.printException(e, "getNormalizedHashListFromFileName: unexpected failure")
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
            # If textual diff is empty, attempt metadata summary fallback
            if not (out and out.strip()):
                try:
                    pseudo_names = (self.MODS, self.STAGED)
                    # Determine whether metadata diff should use --cached.
                    if hash1 in pseudo_names and hash2 in pseudo_names and {hash1, hash2} == {self.STAGED, self.MODS}:
                        use_cached = False
                    else:
                        use_cached = hash1 == self.STAGED or hash2 == self.STAGED
                    meta_cmd = ["git", "-C", self._repoRoot, "diff"]
                    if use_cached:
                        meta_cmd.append("--cached")
                    meta_cmd += ["--name-status", "--summary"]
                    # Include explicit refs when provided (avoid pseudo names)
                    if hash1 in pseudo_names or hash2 in pseudo_names:
                        if hash1 and hash1 not in pseudo_names and hash2 and hash2 not in pseudo_names:
                            meta_cmd += [hash1, hash2]
                        elif hash2 and hash2 not in pseudo_names:
                            meta_cmd.append(hash2)
                        elif hash1 and hash1 not in pseudo_names:
                            meta_cmd.append(hash1)
                    else:
                        if hash1 and hash2:
                            meta_cmd += [hash1, hash2]
                        elif hash2 and not hash1:
                            meta_cmd.append(hash2)
                    if filename:
                        meta_cmd += ["--", filename]
                    meta_out = self._git_run(meta_cmd, text=True)
                    if meta_out and meta_out.strip():
                        out = meta_out
                    else:
                        out = "(no textual changes for this file)"
                except Exception as e:
                    self.printException(e, "getDiff: metadata diff failed")

            # Return output lines (preserve empty output as empty list)
            if not out:
                return []
            return out.splitlines()
        except Exception as e:
            # Log and re-raise so callers can handle errors explicitly
            self.printException(e, "getDiff: failed")
            raise

    def build_diff_cmd(self, filename: str, prev: str, curr: str, variant_index: int = 0) -> list[str]:
        """
        Return a git diff argv list for the given filenames and commit-ish pair.
        """
        try:
            repo_root = self._repoRoot

            # Determine repository empty-tree object id (sha1 or sha256)
            empty_tree = self._empty_tree_hash()

            # Map special tokens to git refs/markers used below.
            def token_to_ref(token: str):
                if token == self.NEWREPO:
                    return empty_tree
                if token == self.MODS:
                    # working tree represented by absence of a ref
                    return None
                if token == self.STAGED:
                    # staged/index is represented via --cached flag
                    return self.STAGED
                return token

            ref_prev = token_to_ref(prev) if prev is not None else None
            ref_curr = token_to_ref(curr) if curr is not None else None

            # Helper to build base diff argv
            def base_diff(use_cached: bool = False) -> list[str]:
                base = ["git", "-C", repo_root, "diff"]
                if use_cached:
                    base.append("--cached")
                return base

            # If either side refers to the staged index marker, prefer --cached
            use_cached = (ref_prev == self.STAGED) or (ref_curr == self.STAGED)

            # If both resolved refs are identical, return a diff invocation
            # that will produce empty output (no-op) rather than attempting
            # to build a meaningful comparison.
            if ref_prev == ref_curr:
                cmd = base_diff(use_cached=use_cached)
                if ref_prev is not None:
                    cmd.append(ref_prev)
                    cmd.append(ref_curr)
                if filename:
                    cmd += ["--", filename]
                return cmd

            # Cases to consider:
            # - working-tree side present (None) -> use diff/show semantics
            # - both concrete refs (including empty-tree) -> normal diff
            # - single concrete curr (prev None and prev token was NEWREPO) -> use show for commit
            if ref_prev is None or ref_curr is None:
                # If only curr specified and prev was the implicit working-tree
                if prev is None and ref_curr is not None and ref_prev is None:
                    # Show the commit's patch (git show produces patch for commit)
                    cmd = ["git", "-C", repo_root, "show", "--pretty=format:", ref_curr]
                else:
                    cmd = base_diff(use_cached=use_cached)
                    if ref_prev is not None:
                        cmd.append(ref_prev)
                    if ref_curr is not None:
                        cmd.append(ref_curr)
            else:
                # Both sides are concrete refs (may be empty-tree hash)
                cmd = base_diff(use_cached=use_cached)
                cmd.append(ref_prev)
                cmd.append(ref_curr)

            if filename:
                cmd += ["--", filename]

            return cmd
        except Exception as e:
            self.printException(e, "GitRepo.build_diff_cmd failed")
            return ["git", "diff"]

    def getFileContents(self, hashval: str, relpath: str) -> bytes | None:
        """Return raw bytes for the given repository-relative `relpath` at `hashval`.

        - `MODS` reads the working-tree file bytes.
        - `STAGED` reads from index via `git show :<relpath>`.
        - commit-ish reads from `git show <hash>:<relpath>`.
        Returns None on failure.
        """
        try:
            if hashval == self.MODS:
                try:
                    full = os.path.join(self._repoRoot, relpath)
                    with open(full, "rb") as f:
                        return f.read()
                except Exception as e:
                    self.printException(e, "getFileContents: reading working-tree failed")
                    return None

            if hashval == self.STAGED:
                out = self._git_run(["git", "-C", self._repoRoot, "show", f":{relpath}"], text=False)
                return out if out else None

            # commit-ish
            out = self._git_run(["git", "-C", self._repoRoot, "show", f"{hashval}:{relpath}"], text=False)
            return out if out else None
        except Exception as e:
            self.printException(e, "getFileContents failed")
            return None
        except Exception as e:
            # Log and re-raise so callers can handle errors explicitly
            self.printException(e, "getDiff: failed")
            raise
