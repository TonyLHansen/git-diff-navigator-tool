#!/usr/bin/env python3

"""
Repository helper module extracted from gitdiffnavtool.

Provides: `printException`, `AppException`, and `GitRepo`.

This module is intentionally self-contained so other modules can import
these helpers without depending on the larger UI code.
"""

from __future__ import annotations

import codecs
import hashlib
import logging
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from subprocess import CalledProcessError, check_output, run
from typing import Optional, Any, Dict, List, Tuple, overload, Literal
import json
import base64
import argparse

logger = logging.getLogger(__name__)


def printException(e: Exception, msg: Optional[str] = None) -> None:
    """
    Module-level helper to log unexpected exceptions when `self` isn't available.

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
    """
    Mixin providing instance-level exception logging for apps and widgets.

    This centralizes `printException` so multiple base classes can inherit
    it and avoid duplicate implementations.
    """

    def printException(self, e: Exception, msg: Optional[str] = None) -> None:
        """
        Log an exception with the calling class and function name.

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

     gitRepo.get_repo_root() will return the repoRoot as a full path.


    ################ path conversion routines

     If you have a path (relative to the current directory), you can trim it down to one
     rooted in the repoRoot by using GitRepo.cwd_plus_path_to_reldir_relfile().

     reldir, relfile = GitRepo.repo_rel_path_to_reldir_relfile(file_path):
        file_path is a repository-relative path (must NOT be absolute or contain ".." outside)
        Returns (reldir,relfile) where relfile == "" for directories and
        reldir == "" when the item is at the repository top-level.

     reldir, relfile = gitRepo.cwd_plus_path_to_reldir_relfile(query_path)
         query_path is a path relative to the current directory.
         It is converted to a relative (reldir,relfile) based on the repoRoot.

     reldir, relfile = gitRepo.reldir_plus_path_to_reldir_relfile(rel_dir, query_path)
         query_path is a path (relative to rel_dir within the repo) to a directory or file.

     abspath = gitRepo.abs_path_for(rel_dir, rel_file)
         Return an absolute path from (reldir,relfile).

     is_dir = gitRepo.is_directory(rel_dir, rel_file)
         Return True if the repository-relative (reldir,relfile) resolves to an existing directory.


    Most of the remaining functions return information about the repository from various points of view,
     in particular:
         * file lists for the repo,
         * file lists associated with hashes,
         * hash lists associated with the repo or files,
         * and differences for a file with a given set of hashes.


    ################ File Lists

     gitRepo.getFileListAtHash(self, curr_hash)
        Return a list of the committed files present in `curr_hash`.


     gitRepo.getFileListUntrackedAndIgnored()
         Returns a list of the untracked and ignored files in the current repository view,


     gitRepo.getFileListBetweenNormalizedHashes(hash1, hash2)
         Returns a list of the files that were modified between two hashes, along
         with a status indicator and the timestamp for when that hash was committed
         In addition to the normal hex-string hashes that git normally supports, there
         are three pseudo-hashes that are supported:
             GitRepo.NEWREPO -- the initial state of a repository
             GitRepo.STAGED -- files that have been added to a repository but not yet committed
             GitRepo.MODS -- files that have been modified since STAGED or HEAD (if nothing is staged)

    ################ Hash Lists

     gitRepo.getNormalizedHashListComplete()
         Returns a list of the hashes, their timestamps and commit messages for the entire repository.
     gitRepo.getNormalizedHashListFromFileName(filename)
         Returns a list of the hashes, their timestamps and commit messages associated with the specified filename.
         The filename is relative to the repoRoot.

    ################ Diff Lists

     other getFileList and getHashList functions also exist


     Internally the git command is used to retrieve the information and cached.

     gitRepo.reset_cache() will reset the cache used by GitRepo's functions. Use this if you ever wish
     to have gitRepo restart with a fresh view of the repository.

     Note: an earlier version of this class used pyGit2, but it was found to produce
     results for various file lists and hash lists that were sufficiently different from
     git to be troublesome. Also, various operations were actually slower than forking
     the git command.
    """

    # Pseudo-hash tokens used across diff dispatching
    NEWREPO = "NEWREPO"
    STAGED = "STAGED"
    MODS = "MODS"

    NEWREPO_MESSAGE = "Newly created repository"
    STAGED_MESSAGE = "Staged changes"
    MODS_MESSAGE = "Unstaged modifications"

    def __init__(self, repoRoot: str, branch: str | None = None):
        # One-time per-process cache for git CLI command results
        self._cmd_cache: Dict[str, Any] = {}
        # Resolve the provided path to the git repository top; allow
        # exceptions from `resolve_repo_top(..., raise_on_missing=True)` to
        # propagate so callers receive a clear failure immediately.
        resolved, _ = GitRepo.resolve_repo_top(repoRoot, raise_on_missing=True)
        # resolve_repo_top should have raised on failure; assert for typing
        assert resolved is not None
        self._repoRoot: str = resolved
        self._branch: str | None = (branch or "").strip() or None

        # Validate the configured branch if provided; throw exception if invalid
        if self._branch:
            try:
                out = check_output(
                    ["git", "rev-parse", "--verify", self._branch],
                    cwd=self._repoRoot,
                    text=True,
                    stderr=open(os.devnull, "w"),
                )
                if not out or not out.strip():
                    raise ValueError(f"__init__: branch {self._branch!r} is not valid (did not resolve to a commit)")
            except CalledProcessError as _use_raise:
                raise ValueError(
                    f"__init__: branch {self._branch!r} is not valid or does not exist in repository"
                ) from _use_raise
            except Exception as _use_raise:
                raise ValueError(f"__init__: failed to validate branch {self._branch!r}") from _use_raise

    def reset_cache(self) -> None:
        """Reset the per-process command/result cache."""
        self._cmd_cache = {}

    def _get_default_ref(self) -> str:
        """
        Return the default git ref for this repository.

        Returns the configured branch if set and valid, otherwise returns "HEAD".
        This ensures all history queries are branch-aware when a branch is configured.
        """
        return self._branch if self._branch else "HEAD"

    def _get_upstream_ref(self) -> str | None:
        """
        Return the upstream tracking branch for the default ref, or None if unavailable.

        If a branch is configured, attempts to resolve <branch>@{upstream}.
        Otherwise attempts to resolve HEAD@{upstream}.
        Returns None if the upstream cannot be resolved.

        Used to compute pushed/unpushed status relative to the configured branch's upstream.
        """
        try:
            default_ref = self._get_default_ref()
            upstream_spec = f"{default_ref}@{{upstream}}"
            out = check_output(
                ["git", "rev-parse", "--verify", upstream_spec],
                cwd=self._repoRoot,
                text=True,
                stderr=open(os.devnull, "w"),
            )
            return upstream_spec if (out and out.strip()) else None
        except (CalledProcessError, Exception) as _no_logging:
            # Upstream not configured or resolution failed; return None
            return None

    @classmethod
    def resolve_repo_top(cls, path: str, raise_on_missing: bool = False) -> tuple[str | None, Exception | None]:
        """
        Resolve the repository top-level directory for *path*.

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

    def get_repo_root(self) -> str:
        """Return the resolved repository root path for this GitRepo instance."""
        return self._repoRoot

    def cwd_plus_path_to_reldir_relfile(self, query_path: str) -> tuple[str, str]:
        """
        reldir, relfile = GitRepo.cwd_plus_path_to_reldir_relfile(query_path)
            self._repoRoot is the full path to the root of the repository, and
            query_path is a path relative to the current directory.
            Relpath is computed from repoRoot to query_path if query_path is within repoRoot, else an exception is raised.
            Then invokes GitRepo.repo_rel_path_to_reldir_relfile() on the resulting relpath.
            Returns ("","") if the query_path and repoRoot are the same.
            Returns a directory,file pair relative to repoRoot if the file is within the repoRoot directory
            Raises an exception otherwise
        """
        try:
            abs_query_path = os.path.abspath(os.path.normpath(os.path.join(os.getcwd(), query_path)))
            relpath = os.path.relpath(abs_query_path, self._repoRoot)

            if relpath == ".":
                return ("", "")

            # If the provided query path corresponds to an existing directory
            # treat it as a directory even if the caller did not include a
            # trailing separator. This avoids swapping rel_dir/rel_file when
            # callers provide a directory name like 'scripts'.
            if os.path.isdir(abs_query_path):
                return (relpath, "")

            return GitRepo.repo_rel_path_to_reldir_relfile(relpath)
        except Exception as e:
            self.printException(e, "cwd_plus_path_to_reldir_relfile failed")
            raise ValueError(f"cwd_plus_path_to_reldir_rlfile failed: {e}") from e

    @classmethod
    def repo_rel_path_to_reldir_relfile(cls, file_path: str) -> tuple[str, str]:
        """
        file_path is a repository-relative path (must NOT be absolute).

        Returns (reldir, relfile) where relfile == "" for directories and
        reldir == "" when the item is at the repository top-level.

        Raises ValueError for absolute inputs or any normalized ".." components.
        """
        if file_path is None:
            raise ValueError("repo_rel_path_to_reldir_relfile: empty file_path")

        if os.path.isabs(file_path):
            raise ValueError("repo_rel_path_to_reldir_relfile: file_path must be repository-relative (not absolute)")

        # Preserve original to detect explicit trailing-separator directory intent
        original = file_path

        # Normalize relative path (collapses redundant separators, up-levels)
        norm = os.path.normpath(file_path)

        # Reject any up-level components that could escape the repo root
        comps = norm.split(os.sep)
        if any(part == ".." for part in comps):
            raise ValueError("repo_rel_path_to_reldir_relfile: file_path must not contain '..' components")

        # Treat "." or empty as the repository root directory
        if norm in ("", "."):
            return ("", "")

        # If caller explicitly ended with a separator consider it a directory
        if original.endswith(os.sep) or original.endswith("/") or original.endswith("\\"):
            # normalized form has no trailing sep; return as directory path
            return (norm, "")

        # Otherwise split into directory and file components
        dirpart = os.path.dirname(norm)
        filepart = os.path.basename(norm)
        if dirpart in ("", "."):
            return ("", filepart)
        return (dirpart, filepart)

    def reldir_plus_path_to_reldir_relfile(self, rel_dir: Optional[str], query_path: str) -> tuple[str, str]:
        """
        query_path is a path (relative to rel_dir within the repo) to a directory or file.
        join(repo_root, rel_dir, query_path)
        normalize
        reduce by repo_root
        Returns a directory,file pair relative to repoRoot if the file is within the repoRoot directory
        Raises an exception otherwise
        """
        try:
            # Normalize optional rel_dir to empty string for os.path.join
            if rel_dir is None:
                rel_dir = ""
            joined = os.path.join(self._repoRoot, rel_dir, query_path)
            norm = os.path.normpath(joined)
            removed_root = os.path.relpath(norm, self._repoRoot)
            return GitRepo.repo_rel_path_to_reldir_relfile(removed_root)
        except Exception as e:
            self.printException(e, "reldir_plus_path_to_reldir_relfile failed")
            raise ValueError(f"reldir_plus_path_to_reldir_relfile failed: {e}") from e

    def reldir_plus_dirname_to_reldir(self, rel_dir: Optional[str], dirname: str) -> str:
        """
        Compute a new repository-relative directory by appending `dirname` to
        `rel_dir` and normalizing. `rel_dir` may be None or empty to indicate
        the repository root. `dirname` is interpreted as a single path
        component (may be '..' to move up one level). The returned value is a
        repository-relative directory string (empty for repo root).

        Raises ValueError when the resulting directory would be outside the
        repository root (e.g., attempting to move above the repo root).
        """
        try:
            if rel_dir is None:
                rel_dir = ""
            # Join against repo root and normalize
            joined = os.path.join(self._repoRoot, rel_dir, dirname)
            norm = os.path.normpath(joined)
            # Compute path relative to repo root
            removed_root = os.path.relpath(norm, self._repoRoot)

            # Reject any up-level components which would escape the repo
            comps = removed_root.split(os.sep)
            if any(part == ".." for part in comps):
                raise ValueError("reldir_plus_dirname_to_reldir: resulting path escapes repository root")

            # Normalize '.' to empty string for repo root
            if removed_root in ("", "."):
                return ""
            return removed_root
        except Exception as e:
            self.printException(e, "reldir_plus_dirname_to_reldir failed")
            raise

    def abs_path_for(self, rel_dir: str, rel_file: str) -> str:
        """
        Return an absolute filesystem path for a repository-relative directory/file pair.

        Validates that the computed path remains inside the repository
        root and raises ``ValueError`` on invalid inputs.

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
            raise ValueError("abs_path_for: rel_dir must be repository-relative (not absolute)")
        if rel_file and os.path.isabs(rel_file):
            raise ValueError("abs_path_for: rel_file must be repository-relative (not absolute)")

        # Join and normalize
        try:
            full = os.path.normpath(os.path.join(self._repoRoot, rel_dir, rel_file))
        except Exception as e:
            self.printException(e, "abs_path_for: failed to construct path")
            raise ValueError(f"abs_path_for: failed to construct path: {e}") from e

        # Ensure the resulting path is inside the repository root
        try:
            repo_norm = os.path.normpath(self._repoRoot)
            common = os.path.commonpath([repo_norm, full])
            if common != repo_norm:
                raise ValueError("abs_path_for: computed path is outside the repository root")
        except Exception as e:
            self.printException(e, "abs_path_for: validation failed")
            raise ValueError(f"abs_path_for: validation failed: {e}") from e

        return full

    def is_directory(self, rel_dir: str, rel_file: str) -> bool:
        """
        Return True if the repository-relative (rel_dir, rel_file) resolves to an existing directory.

        If `rel_dir` is an empty string use `rel_file` directly under the repo root.
        """
        if rel_dir == "":
            full = os.path.join(self._repoRoot, rel_file)
        else:
            full = os.path.join(self._repoRoot, rel_dir, rel_file)
        try:
            return os.path.isdir(full)
        except Exception as e:
            self.printException(e, f"is_directory: failed to check directory {rel_dir}/{rel_file}")
            return False

    ################################################################
    # helper functions
    ################################################################

    def _deltas_to_results(self, detailed: list, a_raw, b_raw) -> list[tuple[str, str]]:
        """
        Convert detailed delta dicts to a list of ``(path, status)``.

        A straightforward, robust converter that
        extracts the most-relevant path and status from each detailed entry,
        to be used by higher-level diff helpers.
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
        Return an ISO timestamp (UTC) based on the repository index modification time.

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
        Format an epoch seconds value as an ISO UTC timestamp string.

        Centralized helper to avoid repeating the same try/except timestamp
        formatting logic throughout the codebase.
        """
        try:
            return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception as e:
            self.printException(e, "_epoch_to_iso: formatting timestamp failed")
            return "1970-01-01T00:00:00"

    def _git_cli_decode_quoted_path(self, rel: str) -> str:
        """
        Decode a git-quoted path ("...") emitted by `git ls-files`.

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
        """
        Return the modification time (float) for a repository-relative path.

        Returns ``None`` if the file does not exist.

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
        """
        Create a compact cache key from `name` and `args`.

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
        files.

        If no mtimes can be determined, fall back to the repository index mtime.
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

    def _newrepo_timestamp_iso(self, ignorecache: bool = False) -> str:
        """
        Compute an ISO timestamp for the pseudo-`NEWREPO` entry.

        Strategy: use the first commit timestamp or the oldest mtime under
        ``.git``; fall back to the repository index mtime when unavailable.

        Detailed Strategy:
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
            default_ref = self._get_default_ref()
            out = self._git_run(
                ["git", "log", default_ref, "--reverse", "--pretty=format:%at"], text=True, ignorecache=ignorecache
            )
            if out:
                for line in out.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        first_commit_ts = float(int(line))
                        break
                    except Exception as e:
                        self.printException(e, "_newrepo_timestamp_iso: parsing commit ts failed")
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

        # Fallback: use index mtime ISO
        return self.index_mtime_iso()

    def _get_commit_timestamp(self, hash_val: str) -> float | None:
        """
        Get the author timestamp (epoch seconds) for a commit hash.

        Returns the epoch seconds as a float, or None if not found.
        """
        try:
            output = self._git_run(["git", "-C", self._repoRoot, "log", "-1", "--format=%at", hash_val], text=True)
            if output:
                return float(output.strip())
            return None
        except Exception as e:
            self.printException(e, "_get_commit_timestamp: failed")
            return None

    def _parse_git_log_output(self, output: str) -> list[tuple[int, str, str, str, str]]:
        """
        Parse `git log --pretty=format:%at %H %an %ae %s` style output into tuples.

        Returns a list of ``(timestamp_int, hash, author_name, author_email, subject)``.
        """
        results: list[tuple[int, str, str, str, str]] = []
        try:
            for line in output.splitlines():
                if not line:
                    continue
                parts = line.split(None, 4)
                if len(parts) < 2:
                    continue
                try:
                    ts = int(parts[0])
                except Exception as e:
                    self.printException(e, "_parse_git_log_output: parsing timestamp failed")
                    ts = 0
                h = parts[1].strip()
                author_name = parts[2].strip() if len(parts) >= 3 else ""
                author_email = parts[3].strip() if len(parts) >= 4 else ""
                subject = parts[4].strip() if len(parts) >= 5 else ""
                results.append((ts, h, author_name, author_email, subject))
            return results
        except Exception as e:
            self.printException(e, "_parse_git_log_output: unexpected failure")
            return []

    def _git_cli_parse_name_status_output(self, output: str) -> list[tuple[str, str]]:
        """
        Parse git "--name-status" style output into ``(path, status)`` pairs.

        This parser is intentionally low-level and returns 2-tuples only.
        Timestamp enrichment to ``(path, iso_mtime, status)`` is performed by
        ``_git_cli_getCachedFileList``.

        Returns a path-sorted list of tuples.
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

    def _git_cli_getCachedFileList(
        self, key: str, git_args: list, ignorecache: bool = False
    ) -> list[tuple[str, str, str]]:
        """
        Run the git command in ``git_args``, parse and cache
        the name-status list under `key`.

        Returns a sorted list of ``(path, iso_mtime, status)`` or ``[]`` on failure.
        """
        try:
            if not ignorecache and key in self._cmd_cache:
                return self._cmd_cache[key]

            output = self._git_run(git_args, text=True, cache_key=key, ignorecache=ignorecache)

            # Parse the whole output using the consolidated parser.
            # Note: git only returns path+status and no times, so we will add mtime info in a second pass.
            results_2tuple = self._git_cli_parse_name_status_output(output or "")
            # Convert 2-tuples to 3-tuples by adding file mtime timestamp
            results: list[tuple[str, str, str]] = []
            for path, status in results_2tuple:
                mtime = self.safe_mtime(path)
                iso = self._epoch_to_iso(mtime) if mtime is not None else self.index_mtime_iso()
                results.append((path, iso, status))

            self._cmd_cache[key] = results
            return results
        except Exception as e:
            self.printException(e, "_git_cli_getCachedFileList: unexpected failure")
            return []

    @overload
    def _git_run(
        self, args: list, text: Literal[True], cache_key: str | None = None, ignorecache: bool = False
    ) -> str:  # pragma: no cover - typing only
        """Overload: when `text=True` this variant returns a string result."""
        ...

    @overload
    def _git_run(
        self, args: list, text: Literal[False], cache_key: str | None = None, ignorecache: bool = False
    ) -> bytes:  # pragma: no cover - typing only
        """Overload: when `text=False` this variant returns raw bytes."""
        ...

    def _git_run(
        self, args: list, text: bool = True, cache_key: str | None = None, ignorecache: bool = False
    ) -> str | bytes:
        """
        Run a git subprocess and return its output (string or bytes).

        Caches results and returns an empty string on failure.

        Detailed Behavior:
        - On success returns the command output (string when text=True).
        - On CalledProcessError: if `cache_key` is provided, store [] into
          `self._cmd_cache[cache_key]` and return an empty string. On any
          failure this function returns an empty string (never `None`).
        - Caches raw command output under an internal key derived from args
          so identical subprocess calls are cheap.
        """
        try:
            logger.debug("_git_run(%s)", args)
            internal_key = f"_git_run:{' '.join(args)}:{'text' if text else 'bytes'}"
            if not ignorecache and internal_key in self._cmd_cache:
                logger.debug("_git_run cache hit: %s", args)
                return self._cmd_cache[internal_key]

            try:
                out = check_output(args, cwd=self._repoRoot, text=text)
            except CalledProcessError as _use_logging:
                logger.debug("_git_run failed: %s", args)
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

    def _git_cli_name_status(self, args: list) -> list[tuple[str, str]]:
        """
        Run a `git` command that emits `--name-status`-style output and parse it.

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
        self,
        prev: str | None = None,
        curr: str | None = None,
        cached: bool = False,
        key: str | None = None,
        ignorecache: bool = False,
    ) -> list[tuple[str, str, str]]:
        """
        Generalized dispatcher for `git diff --name-status` variants.

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
            return self._git_cli_getCachedFileList(cache_key, args, ignorecache=ignorecache)
        except Exception as e:
            self.printException(e, "_git_name_status_dispatch: unexpected failure")
            return []

    def _empty_tree_hash(self, ignorecache: bool = False) -> str:
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
            if not ignorecache and cache_key in self._cmd_cache:
                return self._cmd_cache[cache_key]

            # Ask git which object format this repo uses. Example responses:
            #   sha1
            #   sha256
            out = self._git_run(["git", "rev-parse", "--show-object-format"], text=True, ignorecache=ignorecache) or ""
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

    ################
    # File Lists
    ################

    def getFileListBetweenNormalizedHashes(
        self, prev_hash: str, curr_hash: str, ignorecache: bool = False
    ) -> list[tuple[str, str, str]]:
        """
        Return a list of `(path, iso_mtime, status)` for files changed between `prev_hash` and `curr_hash`.

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
            return self.getFileListBetweenNewRepoAndHash(curr_hash, ignorecache=ignorecache)

        # If prev is NEWREPO and curr is staged -> initial->staged
        if prev_hash == self.NEWREPO and curr_hash == self.STAGED:
            return self.getFileListBetweenNewRepoAndStaged(ignorecache=ignorecache)

        # If prev is NEWREPO and curr is working tree (mods) -> initial->mods
        if prev_hash == self.NEWREPO and curr_hash == self.MODS:
            return self.getFileListBetweenNewRepoAndMods(ignorecache=ignorecache)

        # If prev and curr are both normal hashes -> direct commit->commit diff
        if prev_hash not in (self.NEWREPO, self.STAGED, self.MODS) and curr_hash not in (
            self.NEWREPO,
            self.STAGED,
            self.MODS,
        ):
            return self.getFileListBetweenTwoCommits(prev_hash, curr_hash, ignorecache=ignorecache)

        # Hash -> staged
        if prev_hash not in (self.NEWREPO, self.STAGED, self.MODS) and curr_hash == self.STAGED:
            return self.getFileListBetweenHashAndStaged(prev_hash, ignorecache=ignorecache)

        # Hash -> working tree (mods)
        if prev_hash not in (self.NEWREPO, self.STAGED, self.MODS) and curr_hash == self.MODS:
            return self.getFileListBetweenHashAndCurrentTime(prev_hash, ignorecache=ignorecache)

        # staged -> mods (working tree)
        if prev_hash == self.STAGED and curr_hash == self.MODS:
            return self.getFileListBetweenStagedAndMods(ignorecache=ignorecache)

        # Fallback: for remaining (likely commit-ish) combos delegate to the
        # explicit two-commit handler rather than the fully generic resolver.
        return self.getFileListBetweenTwoCommits(prev_hash, curr_hash, ignorecache=ignorecache)

    def getFileListBetweenNewRepoAndTopHash(self, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return a list of `(path, iso_mtime, status)` for files present in HEAD.

        Status will be `committed` to indicate file is present in the
        given commit (HEAD).
        """
        # Delegate to the new initial->commit helper to avoid duplication
        return self.getFileListBetweenNewRepoAndHash(self._get_default_ref(), ignorecache=ignorecache)

    def getFileListBetweenTwoCommits(
        self, prev_hash: str, curr_hash: str, ignorecache: bool = False
    ) -> list[tuple[str, str, str]]:
        """
        Direct commit->commit diff (both args expected to be commit-ish).

        Extracted helper containing the previous logic for diffing two commits.
        """
        # Use generalized dispatcher for commit->commit diffs
        key = self._make_cache_key("getFileListBetweenTwoCommits", prev_hash, curr_hash)
        return self._git_name_status_dispatch(
            prev=prev_hash, curr=curr_hash, cached=False, key=key, ignorecache=ignorecache
        )

    def getFileListBetweenNewRepoAndHash(self, curr_hash: str, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return a list of `(path, iso_mtime, status)` for files changed between the beginning and `curr_hash`.

        Status values are the same as other diffs (added/modified/etc.).
        """
        # Git-CLI implementation with a one-time per-hash cache. The
        key = self._make_cache_key("getFileListBetweenNewRepoAndHash", curr_hash)
        try:
            if not ignorecache and key in self._cmd_cache:
                return self._cmd_cache[key]

            output = self._git_run(
                ["git", "ls-tree", "-r", "--name-only", curr_hash], text=True, cache_key=key, ignorecache=ignorecache
            )

            # Get the commit timestamp once, not for each file
            commit_ts = self._get_commit_timestamp(curr_hash)
            iso = self._epoch_to_iso(commit_ts) if commit_ts is not None else self.index_mtime_iso()

            results: list[tuple[str, str, str]] = []
            for line in output.splitlines():
                ln = line.strip()
                if not ln:
                    continue
                results.append((ln, iso, "added"))
            results.sort(key=lambda x: x[0])
            self._cmd_cache[key] = results
            return results
        except Exception as e:
            self.printException(e, "getFileListBetweenNewRepoAndHash: unexpected failure")
            return []

    def getFileListAtHash(self, curr_hash: str, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return a list of `(path, iso_mtime, status)` for files present in `curr_hash`.

        Uses `git ls-tree -r --name-only <hash>` to list committed files for
        the given tree/commit. Results are cached per-process.
        Status is set to `committed` to indicate presence in the commit.
        """
        key = self._make_cache_key("getFileListAtHash", curr_hash)
        try:
            if not ignorecache and key in self._cmd_cache:
                return self._cmd_cache[key]

            output = self._git_run(
                ["git", "ls-tree", "-r", "--name-only", curr_hash], text=True, cache_key=key, ignorecache=ignorecache
            )

            # Get the commit timestamp once, not for each file
            commit_ts = self._get_commit_timestamp(curr_hash)
            iso = self._epoch_to_iso(commit_ts) if commit_ts is not None else self.index_mtime_iso()

            results: list[tuple[str, str, str]] = []
            for line in output.splitlines():
                ln = line.strip()
                if not ln:
                    continue
                results.append((ln, iso, "committed"))
            results.sort(key=lambda x: x[0])
            self._cmd_cache[key] = results
            return results
        except Exception as e:
            self.printException(e, "getFileListAtHash: unexpected failure")
            return []

    def getFileListBetweenNewRepoAndStaged(self, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return file list for the initial (empty) tree -> staged index comparison.

        This is the specialized handler for the `prev is None and curr == STAGED`
        case so `getFileListBetweenNormalizedHashes` can remain a dispatcher.
        """
        # Git-CLI-only implementation cached once per process.
        key = "getFileListBetweenNewRepoAndStaged"
        return self._git_name_status_dispatch(prev=None, curr=None, cached=True, key=key, ignorecache=ignorecache)

    def getFileListBetweenNewRepoAndMods(self, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Specialized handler for initial (empty) -> working tree (mods) comparison.

        This implementation is git-CLI-only and mirrors `git diff --name-status`.
        """
        key = "getFileListBetweenNewRepoAndMods"
        return self._git_name_status_dispatch(prev=None, curr=None, cached=False, key=key, ignorecache=ignorecache)

    def getFileListBetweenTopHashAndCurrentTime(self, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return a list of `(path, status)` for files changed between HEAD and working tree.

        Status will reflect the working-tree change type (modified/added/deleted).
        """
        # Delegate to the general handler to avoid duplicating logic
        return self.getFileListBetweenHashAndCurrentTime(self._get_default_ref(), ignorecache=ignorecache)

    def getFileListBetweenHashAndCurrentTime(self, hash: str, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return `(path,status)` for files changed between `hash` and working tree.

        Uses the git CLI plus a one-time cache via `_git_cli_getCachedFileList`.
        """
        key = self._make_cache_key("getFileListBetweenHashAndCurrentTime", hash)
        return self._git_name_status_dispatch(prev=hash, curr=None, cached=False, key=key, ignorecache=ignorecache)

    def getFileListBetweenTopHashAndStaged(self, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return a list of `(path, status)` for files changed between HEAD and staged index."""
        # Delegate to the generalized staged-vs-hash implementation to avoid duplication
        return self.getFileListBetweenHashAndStaged(self._get_default_ref(), ignorecache=ignorecache)

    def getFileListBetweenHashAndStaged(self, hash: str, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return `(path,status)` for files changed between `hash` and the staged index.

        Generalization of getFileListBetweenTopHashAndStaged for any commit-ish.
        """
        key = self._make_cache_key("getFileListBetweenHashAndStaged", hash)
        return self._git_name_status_dispatch(prev=hash, curr=None, cached=True, key=key, ignorecache=ignorecache)

    def getFileListBetweenStagedAndMods(self, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return a list of `(path, status)` for files changed between staged index and working tree (mods)."""
        # Use git CLI to get the list of files; cache the results once per process
        key = self._make_cache_key("getFileListBetweenStagedAndMods")
        return self._git_name_status_dispatch(prev=None, curr=None, cached=False, key=key, ignorecache=ignorecache)

    def getFileListUntracked(self, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return a sorted list of `(path, iso_mtime, 'untracked')` for files that
        are untracked in the working tree.
        """
        try:
            cache_key = self._make_cache_key("getFileListUntracked")
            if not ignorecache and cache_key in self._cmd_cache:
                return self._cmd_cache[cache_key]

            results: list[tuple[str, str, str]] = []
            seen: set[str] = set()

            untracked_out = (
                self._git_run(["git", "ls-files", "--others", "--exclude-standard"], text=True, ignorecache=ignorecache)
                or ""
            )

            for line in untracked_out.splitlines():
                rel = line.strip()
                rel = self._git_cli_decode_quoted_path(rel)
                if not rel or rel in seen:
                    continue
                seen.add(rel)
                mtime = self.safe_mtime(rel)
                iso = self._epoch_to_iso(mtime) if mtime is not None else self.index_mtime_iso()
                results.append((rel, iso, "untracked"))

            results.sort(key=lambda x: x[0])
            self._cmd_cache[cache_key] = results
            return results
        except Exception as e:
            self.printException(e, "getFileListUntracked: unexpected failure")
            return []

    def getFileListIgnored(self, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Return a sorted list of `(path, iso_mtime, 'ignored')` for files that
        are ignored in the working tree.
        """
        try:
            cache_key = self._make_cache_key("getFileListIgnored")
            if not ignorecache and cache_key in self._cmd_cache:
                return self._cmd_cache[cache_key]

            results: list[tuple[str, str, str]] = []
            seen: set[str] = set()

            ignored_out = (
                self._git_run(
                    ["git", "ls-files", "--others", "-i", "--exclude-standard"], text=True, ignorecache=ignorecache
                )
                or ""
            )

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
            self.printException(e, "getFileListIgnored: unexpected failure")
            return []

    def getFileListUntrackedAndIgnored(self, ignorecache: bool = False) -> list[tuple[str, str, str]]:
        """
        Aggregate untracked and ignored lists into a single sorted list.

        Calls `getFileListUntracked()` and `getFileListIgnored()` and merges
        them while avoiding duplicates. Results are cached per-call.
        """
        try:
            cache_key = self._make_cache_key("getFileListUntrackedAndIgnored")
            if not ignorecache and cache_key in self._cmd_cache:
                return self._cmd_cache[cache_key]

            untracked = self.getFileListUntracked(ignorecache=ignorecache) or []
            ignored = self.getFileListIgnored(ignorecache=ignorecache) or []

            combined: list[tuple[str, str, str]] = []
            seen: set[str] = set()
            for entry in untracked + ignored:
                try:
                    rel = entry[0] if len(entry) > 0 else None
                    if not rel or rel in seen:
                        continue
                    seen.add(rel)
                    combined.append(entry)
                except Exception as e:
                    self.printException(e, "getFileListUntrackedAndIgnored: skipping entry parse failed")
                    continue

            combined.sort(key=lambda x: x[0])
            self._cmd_cache[cache_key] = combined
            return combined
        except Exception as e:
            self.printException(e, "getFileListUntrackedAndIgnored: unexpected failure")
            return []

    def _entry_rel_path(self, entry: Any) -> str | None:
        """
        Extract and normalize repository-relative path from a file-list entry.

        Supports tuple/list entries where path is first element and dict
        entries with `raw`, `full`, `path`, or `name` keys.
        """
        try:
            rel: str | None = None
            if isinstance(entry, (list, tuple)):
                rel = str(entry[0]) if len(entry) > 0 and entry[0] is not None else None
            elif isinstance(entry, dict):
                for key in ("raw", "full", "path", "name"):
                    if key in entry and entry[key] is not None:
                        rel = str(entry[key])
                        break
            elif isinstance(entry, str):
                rel = entry

            if not rel:
                return None
            return os.path.normpath(rel)
        except Exception as e:
            self.printException(e, "_entry_rel_path failed")
            return None

    def remove_ignored_files(self, file_list: list[Any] | None, ignorecache: bool = False) -> list[Any]:
        """
        Return `file_list` with entries removed when their path is ignored.

        Preserves original entry objects and ordering.
        """
        try:
            if not file_list:
                return []

            ignored = self.getFileListIgnored(ignorecache=ignorecache) or []
            ignored_paths = {
                os.path.normpath(str(ent[0])) for ent in ignored if isinstance(ent, (list, tuple)) and len(ent) > 0
            }

            out: list[Any] = []
            for entry in file_list:
                rel = self._entry_rel_path(entry)
                if rel and rel in ignored_paths:
                    continue
                out.append(entry)
            return out
        except Exception as e:
            self.printException(e, "remove_ignored_files failed")
            return list(file_list or [])

    def remove_untracked_files(self, file_list: list[Any] | None, ignorecache: bool = False) -> list[Any]:
        """
        Return `file_list` with entries removed when their path is untracked.

        Preserves original entry objects and ordering.
        """
        try:
            if not file_list:
                return []

            untracked = self.getFileListUntracked(ignorecache=ignorecache) or []
            untracked_paths = {
                os.path.normpath(str(ent[0])) for ent in untracked if isinstance(ent, (list, tuple)) and len(ent) > 0
            }

            out: list[Any] = []
            for entry in file_list:
                rel = self._entry_rel_path(entry)
                if rel and rel in untracked_paths:
                    continue
                out.append(entry)
            return out
        except Exception as e:
            self.printException(e, "remove_untracked_files failed")
            return list(file_list or [])

    ################
    # Hash Lists
    ################

    def getPushedHashes(self, ignorecache: bool = False) -> set[str]:
        """
        Return commit hashes reachable from the active upstream/remote refs.

        Prefer commits reachable from `<default_ref>@{upstream}` where
        `default_ref` is the configured branch or HEAD. If no upstream is
        configured, fall back to all remote-tracking refs.
        """
        try:
            cache_key = "getPushedHashes"
            if not ignorecache and cache_key in self._cmd_cache:
                cached = self._cmd_cache[cache_key]
                return cached if isinstance(cached, set) else set()

            pushed_hashes: set[str] = set()

            # Prefer upstream for configured branch OR current HEAD branch.
            upstream_ref = self._get_upstream_ref()
            if upstream_ref:
                try:
                    output = self._git_run(["git", "rev-list", upstream_ref], text=True, ignorecache=ignorecache) or ""
                    if output:
                        pushed_hashes = {line.strip() for line in output.splitlines() if line.strip()}
                    self._cmd_cache[cache_key] = pushed_hashes
                    return pushed_hashes
                except CalledProcessError as e:
                    self.printException(e, "getPushedHashes: git rev-list for upstream failed")

            # Fallback: use all remote-tracking refs when no branch upstream or fallback mode
            remote_out = (
                self._git_run(["git", "config", "--get", "remote.origin.url"], text=True, ignorecache=ignorecache) or ""
            )
            if not remote_out.strip():
                self._cmd_cache[cache_key] = pushed_hashes
                return pushed_hashes

            try:
                output = self._git_run(["git", "rev-list", "--remotes"], text=True, ignorecache=ignorecache) or ""
                if output:
                    pushed_hashes = {line.strip() for line in output.splitlines() if line.strip()}
            except CalledProcessError as e:
                self.printException(e, "getPushedHashes: git rev-list failed")

            self._cmd_cache[cache_key] = pushed_hashes
            return pushed_hashes
        except Exception as e:
            self.printException(e, "getPushedHashes: unexpected failure")
            return set()

    def getNormalizedHashListComplete(
        self, ignorecache: bool = False, limit: int = 0
    ) -> list[tuple[str, str, str, str, str, str]]:
        """Return combined hash entries with pushed/unpushed status."""
        remaining_limit = limit

        new = self.getHashListNewChanges(ignorecache=ignorecache, limit=remaining_limit)
        if limit > 0:
            remaining_limit -= len(new)

        staged = self.getHashListStagedChanges(ignorecache=ignorecache, limit=remaining_limit)
        if limit > 0:
            remaining_limit -= len(staged)

        entire = self.getHashListEntireRepo(ignorecache=ignorecache, limit=remaining_limit)
        if limit > 0:
            remaining_limit -= len(entire)

        newrepo = self.getHashListNewRepo(ignorecache=ignorecache, limit=remaining_limit)

        return new + staged + entire + newrepo

    def getHashListEntireRepo(
        self, ignorecache: bool = False, limit: int = 0
    ) -> list[tuple[str, str, str, str, str, str]]:
        """Return all commit hashes in the configured branch with pushed status."""
        default_ref = self._get_default_ref()
        output = self._git_run(
            ["git", "log", default_ref, "--pretty=format:%at %H %an %ae %s"], text=True, ignorecache=ignorecache
        )
        pairs = self._parse_git_log_output(output or "")
        pairs.sort(key=lambda x: (x[0], x[1]), reverse=True)

        pushed_hashes = self.getPushedHashes(ignorecache=ignorecache)
        formatted: list[tuple[str, str, str, str, str, str]] = []
        for ts, h, author_name, author_email, subject in pairs:
            iso = self._epoch_to_iso(ts)
            status = "pushed" if h in pushed_hashes else "unpushed"
            formatted.append((iso, h, subject, status, author_name, author_email))
        if limit > 0:
            formatted = formatted[:limit]
        return formatted

    def getHashListStagedChanges(
        self, ignorecache: bool = False, limit: int = 0
    ) -> list[tuple[str, str, str, str, str, str]]:
        """Return staged pseudo-hash entries with pushed status."""
        key = self._make_cache_key("getHashListStagedChanges", self.index_mtime_iso())
        try:
            if not ignorecache and key in self._cmd_cache:
                result = self._cmd_cache[key]
                if limit > 0:
                    result = result[:limit]
                return result

            names_out = self._git_run(
                ["git", "diff", "--cached", "--name-only"], text=True, cache_key=key, ignorecache=ignorecache
            )

            if not names_out:
                self._cmd_cache[key] = []
                return []

            if any(ln.strip() for ln in names_out.splitlines()):
                iso = self.index_mtime_iso()
                res = [(iso, self.STAGED, self.STAGED_MESSAGE, "unpushed", "", "")]
                self._cmd_cache[key] = res
                if limit > 0:
                    res = res[:limit]
                return res

            self._cmd_cache[key] = []
            return []
        except Exception as e:
            self.printException(e, "getHashListStagedChanges: failure")
            return []

    def getHashListNewChanges(
        self, ignorecache: bool = False, limit: int = 0
    ) -> list[tuple[str, str, str, str, str, str]]:
        """Return working-tree "MODS" entry with pushed status."""
        try:
            names_out = self._git_run(["git", "diff", "--name-only"], text=True, ignorecache=ignorecache)

            if not names_out:
                return []

            paths = [ln.strip() for ln in names_out.splitlines() if ln.strip()]
            if not paths:
                return []

            iso = self._paths_mtime_iso(paths)
            key = self._make_cache_key("getHashListNewChanges", iso)
            if not ignorecache and key in self._cmd_cache:
                result = self._cmd_cache[key]
                if limit > 0:
                    result = result[:limit]
                return result
            res = [(iso, self.MODS, self.MODS_MESSAGE, "unpushed", "", "")]
            self._cmd_cache[key] = res
            if limit > 0:
                res = res[:limit]
            return res
        except Exception as e:
            self.printException(e, "getHashListNewChanges: failure")
            return []

    def getHashListNewRepo(
        self, ignorecache: bool = False, limit: int = 0
    ) -> list[tuple[str, str, str, str, str, str]]:
        """
        Return the pseudo-hash entry for a newly-created repository.

        Returns a single-entry list containing
        `(iso_timestamp, NEWREPO, NEWREPO_MESSAGE, status, author_name, author_email)` where status is
        copied from the oldest real commit's pushed status when available.
        """
        try:
            iso = self._newrepo_timestamp_iso(ignorecache=ignorecache)
            oldest_status = "unpushed"
            entire = self.getHashListEntireRepo(ignorecache=ignorecache)
            if entire:
                oldest_status = entire[-1][3]
            result = [(iso, self.NEWREPO, self.NEWREPO_MESSAGE, oldest_status, "", "")]
            if limit > 0:
                result = result[:limit]
            return result
        except Exception as e:
            self.printException(e, "getHashListNewRepo: failure")
            result = [(self.index_mtime_iso(), self.NEWREPO, self.NEWREPO_MESSAGE, "unpushed", "", "")]
            if limit > 0:
                result = result[:limit]
            return result

    def getNormalizedHashListFromFileName(
        self, file_name: str, ignorecache: bool = False, limit: int = 0
    ) -> list[tuple[str, str, str, str, str, str]]:
        """
        Return a list of commit hashes that modified the given file.

        Returns 6-tuples (iso_timestamp, hash, subject, status, author_name, author_email) where status is
        'pushed' or 'unpushed'. Uses the git CLI (`git log` + `git status`) with a
        one-time cache per `file_name`. The previous walk/diff implementation has
        been removed for performance consistency.
        """
        key = self._make_cache_key("getNormalizedHashListFromFileName", file_name)
        try:
            if not ignorecache and key in self._cmd_cache:
                result = self._cmd_cache[key]
                if limit > 0:
                    result = result[:limit]
                return result

            default_ref = self._get_default_ref()
            output = self._git_run(
                ["git", "log", default_ref, "--pretty=format:%at %H %an %ae %s", "--", file_name],
                text=True,
                cache_key=key,
                ignorecache=ignorecache,
            )

            # Get pushed hashes once for status lookup
            pushed_hashes = self.getPushedHashes(ignorecache=ignorecache)

            parsed_entries: list[tuple[str, str, str, str, str, str]] = []
            parsed = self._parse_git_log_output(output or "")
            for ts, h, author_name, author_email, subject in parsed:
                iso = self._epoch_to_iso(ts)
                status = "pushed" if h in pushed_hashes else "unpushed"
                parsed_entries.append((iso, h, subject if subject else "", status, author_name, author_email))

            # Inspect working-tree/index status for the file and construct
            # explicit pseudo-entries for MODS/STAGED when present. We will
            # then assemble the final list in newest->oldest order with a
            # deterministic placement for these pseudo-entries so callers
            # that reverse the list (oldest->newest) observe STAGED before
            # MODS.
            status_out = (
                self._git_run(["git", "status", "--porcelain", "--", file_name], text=True, ignorecache=ignorecache)
                or ""
            )
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
                    staged_entry = (iso_index, self.STAGED, self.STAGED_MESSAGE, "unpushed", "", "")
                if wt_flag != " ":
                    iso_mods = self._paths_mtime_iso([file_name])
                    mods_entry = (iso_mods, self.MODS, self.MODS_MESSAGE, "unpushed", "", "")
            except Exception as e:
                self.printException(e, "getNormalizedHashListFromFileName: computing pseudo-entry timestamps failed")

            # Assemble final entries in newest->oldest order. Place MODS
            # before STAGED here so that callers that reverse the list
            # (oldest->newest) will see STAGED before MODS.
            entries: list[tuple[str, str, str, str, str, str]] = []
            if mods_entry is not None:
                entries.append(mods_entry)
            if staged_entry is not None:
                entries.append(staged_entry)

            # Append parsed commits newest->oldest
            parsed_entries.sort(key=lambda x: x[0], reverse=True)
            entries.extend(parsed_entries)

            # Cache and return
            self._cmd_cache[key] = entries
            if limit > 0:
                entries = entries[:limit]
            return entries
        except Exception as e:
            self.printException(e, "getNormalizedHashListFromFileName: unexpected failure")
            return []

    ################
    # Diff List
    ################

    def getDiff(
        self, filename: str, hash1: str, hash2: str, variation: list[str] | None = None, unified_context: int = 3
    ) -> list[str]:
        """
        Return the lines produced by `git diff` for `filename` between `hash1` and `hash2`.

        - `filename` is repository-relative path to a file in this repo.
        - `hash1` and `hash2` must be non-None strings and may be full/partial
          git commit-ish hashes or the pseudo-hashes `NEWREPO`, `STAGED`, `MODS`.
        - `variation` is an optional list of additional git-diff arguments (e.g.
          ['--ignore-space-change', '--diff-algorithm=patience']).
        - `unified_context` is the number of context lines for the -U option (default: 3).

        Raises ValueError if `filename` is empty or either hash is None. On
        unexpected failures the exception is logged and re-raised.
        """
        try:
            if filename is None or filename == "":
                raise ValueError("getDiff: filename must be a non-empty repository-relative path")
            if hash1 is None or hash2 is None:
                raise ValueError("getDiff: hash1 and hash2 must be specified (not None)")

            logger.debug(
                "getDiff: start filename=%r hash1=%r hash2=%r variation=%r unified_context=%r",
                filename,
                hash1,
                hash2,
                variation,
                unified_context,
            )

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
            logger.debug("getDiff: normalized refs ref1=%r ref2=%r empty_tree=%r", ref1, ref2, EMPTY_TREE)

            # If both refs resolve to the special staged marker, map to cached diff
            args: list[str] = ["git", "diff"]
            # Add unified context option
            args.append(f"-U{unified_context}")
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
            logger.debug("getDiff: textual diff cmd=%r", args)

            out = self._git_run(args, text=True)
            # If textual diff is empty, attempt metadata summary fallback
            if not (out and out.strip()):
                logger.debug("getDiff: textual diff empty for filename=%r; entering metadata fallback", filename)
                try:
                    # Determine whether metadata diff should use --cached.
                    # Use normalized refs so NEWREPO maps to empty-tree hash
                    # consistently with the main textual diff path.
                    if (ref1 == self.STAGED and ref2 is None) or (ref2 == self.STAGED and ref1 is None):
                        use_cached = False
                    else:
                        use_cached = ref1 == self.STAGED or ref2 == self.STAGED
                    meta_cmd = ["git", "-C", self._repoRoot, "diff"]
                    if use_cached:
                        meta_cmd.append("--cached")
                    meta_cmd += ["--name-status", "--summary"]
                    # Include explicit refs when provided.
                    # `None` means working tree and is omitted.
                    # `STAGED` is represented by --cached and is omitted.
                    if ref1 is not None and ref1 != self.STAGED:
                        meta_cmd.append(ref1)
                    if ref2 is not None and ref2 != self.STAGED:
                        meta_cmd.append(ref2)
                    if filename:
                        meta_cmd += ["--", filename]
                    logger.debug("getDiff: metadata cmd=%r", meta_cmd)
                    meta_out = self._git_run(meta_cmd, text=True)
                    if meta_out and meta_out.strip():
                        logger.debug("getDiff: metadata fallback produced output (len=%d)", len(meta_out.splitlines()))
                        out = meta_out
                    else:
                        logger.debug(
                            "getDiff: metadata fallback empty for filename=%r; probing unfiltered rename status",
                            filename,
                        )
                        # Path-limited metadata can be empty for renames when
                        # the selected filename exists only on one side of the
                        # comparison. Probe unfiltered name-status and provide
                        # a clearer message when a rename is involved.
                        rename_msg = None
                        try:
                            probe_cmd = ["git", "-C", self._repoRoot, "diff"]
                            if use_cached:
                                probe_cmd.append("--cached")
                            probe_cmd += ["--name-status", "--find-renames"]
                            if ref1 is not None and ref1 != self.STAGED:
                                probe_cmd.append(ref1)
                            if ref2 is not None and ref2 != self.STAGED:
                                probe_cmd.append(ref2)
                            logger.debug("getDiff: rename probe cmd=%r", probe_cmd)

                            probe_out = self._git_run(probe_cmd, text=True) or ""
                            logger.debug("getDiff: rename probe output lines=%d", len(probe_out.splitlines()))

                            def _path_match(p: str) -> bool:
                                try:
                                    return p == filename or os.path.basename(p) == os.path.basename(filename)
                                except Exception as e:
                                    self.printException(e, "_path_match failed; falling back to direct equality")
                                    return p == filename

                            for ln in probe_out.splitlines():
                                parts = ln.split("\t")
                                if len(parts) < 3:
                                    continue
                                code = parts[0].strip()
                                if not code.startswith("R"):
                                    continue
                                oldp = parts[1].strip()
                                newp = parts[2].strip()
                                old_match = _path_match(oldp)
                                new_match = _path_match(newp)
                                logger.debug(
                                    "getDiff: rename candidate code=%r old=%r new=%r old_match=%r new_match=%r",
                                    code,
                                    oldp,
                                    newp,
                                    old_match,
                                    new_match,
                                )
                                if old_match or new_match:
                                    rename_msg = f"(file renamed: {oldp} -> {newp}; no textual changes)"
                                    break

                            # NEWREPO<->commit diffs may not expose rename
                            # pairs in tree-to-tree output because the empty
                            # tree side has no source path. Probe commit-level
                            # rename metadata as a second chance.
                            if rename_msg is None and (
                                (hash1 == self.NEWREPO and hash2 not in (self.NEWREPO, self.STAGED, self.MODS))
                                or (hash2 == self.NEWREPO and hash1 not in (self.NEWREPO, self.STAGED, self.MODS))
                            ):
                                try:
                                    commit_ref = hash2 if hash1 == self.NEWREPO else hash1
                                    show_cmd = [
                                        "git",
                                        "-C",
                                        self._repoRoot,
                                        "show",
                                        "--name-status",
                                        "--format=",
                                        "--find-renames",
                                        commit_ref,
                                    ]
                                    logger.debug("getDiff: commit-rename probe cmd=%r", show_cmd)
                                    show_out = self._git_run(show_cmd, text=True) or ""
                                    logger.debug(
                                        "getDiff: commit-rename probe output lines=%d for commit=%r",
                                        len(show_out.splitlines()),
                                        commit_ref,
                                    )
                                    for ln in show_out.splitlines():
                                        parts = ln.split("\t")
                                        if len(parts) < 3:
                                            continue
                                        code = parts[0].strip()
                                        if not code.startswith("R"):
                                            continue
                                        oldp = parts[1].strip()
                                        newp = parts[2].strip()
                                        old_match = _path_match(oldp)
                                        new_match = _path_match(newp)
                                        logger.debug(
                                            "getDiff: commit-rename candidate code=%r old=%r new=%r old_match=%r new_match=%r",
                                            code,
                                            oldp,
                                            newp,
                                            old_match,
                                            new_match,
                                        )
                                        if old_match or new_match:
                                            rename_msg = (
                                                f"(file renamed in commit {commit_ref[:12]}: {oldp} -> {newp}; "
                                                "no textual changes for selected path)"
                                            )
                                            break
                                except Exception as e:
                                    self.printException(e, "getDiff: commit rename probe failed")
                        except Exception as e:
                            self.printException(e, "getDiff: rename probe failed")

                        logger.debug("getDiff: rename probe result rename_msg=%r", rename_msg)
                        out = rename_msg or "(no textual changes for this file)"
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

    def getFileContents(self, hashval: str, reldir: str, relfile: str) -> bytes | None:
        """
        Return raw bytes for the given repository-relative (reldir,relfile)at `hashval`.

        - `MODS` reads the working-tree file bytes.
        - `STAGED` reads from index via `git show :<relpath>`.
        - `NEWREPO` has no files, so returns b"" to indicate empty content.
        - commit-ish reads from `git show <hash>:<relpath>`.
        Returns None on failure.
        """
        try:
            relpath = os.path.join(reldir, relfile)
            if hashval == self.MODS:
                try:
                    full = os.path.join(self._repoRoot, relpath)
                    with open(full, "rb") as f:
                        return f.read()
                except Exception as e:
                    self.printException(e, "getFileContents: reading working-tree failed")
                    return None

            if hashval == self.NEWREPO:
                # NEWREPO has no files, so return b"" to indicate empty content
                return b""

            if hashval == self.STAGED:
                out = self._git_run(["git", "-C", self._repoRoot, "show", f":{relpath}"], text=False)
            else:
                # commit-ish
                out = self._git_run(["git", "-C", self._repoRoot, "show", f"{hashval}:{relpath}"], text=False)

            # Normalize subprocess output to bytes in one place so callers
            # always receive `bytes` (or `None` on failure). Accept
            # `bytes`/`bytearray` as-is, convert `str` via UTF-8 (with a
            # surrogate-escape fallback) and propagate `None`.
            if out is None:
                return None

            if isinstance(out, (bytes, bytearray)):
                return bytes(out)

            return bytes(out, "utf-8", errors="surrogateescape")
        except Exception as e:
            # Log and re-raise so callers can handle errors explicitly
            self.printException(e, "getDiff: failed")
            raise

    def getCompleteCommitMessage(self, filepath: str, hash_val: str) -> str | None:
        """
        Return the complete commit message for a given commit hash.

        The complete message includes both the subject line and the body
        (all lines after the first blank line). This is distinct from
        the subject line only (first line) returned by commit list functions.

        Args:
            filepath: Repository-relative path (included for consistency with similar APIs,
                     but not used for message retrieval as messages are commit-level, not file-level)
            hash_val: The commit hash to retrieve the message for

        Returns:
            The complete commit message as a string, or None on failure.
        """
        try:
            # Use git show -s --format=%B to get the complete commit message
            # %B = raw body (commit message, skipping the subject line)
            # But we want the whole message, so we use %B which includes the full message
            output = self._git_run(["git", "-C", self._repoRoot, "show", "-s", "--format=%B", hash_val], text=True)

            # _git_run returns empty string on failure, not None
            if output is None or output == "":
                self.printException(
                    Exception(f"Failed to retrieve commit message for {hash_val}"),
                    f"getCompleteCommitMessage: git show failed for {hash_val}",
                )
                return None

            # Return the trimmed message (remove trailing newline if present)
            return output.rstrip("\n")

        except Exception as e:
            self.printException(e, "getCompleteCommitMessage: failed")
            return None

    def amendCommitMessage(self, hash_val: str, new_message: str) -> str:
        """
        Amend the commit message for a given hash.

        Only works on unpushed commits. Raises an exception if the hash is pushed.

        - For HEAD commits: uses `git commit --amend -m <new_message>`
        - For other unpushed commits: uses `git rebase` with a Python filter script

        Args:
            hash_val: The commit hash to amend (must be unpushed)
            new_message: The new commit message

        Returns:
            The new commit hash after the amendment (hash changes when message changes)

        Raises:
            ValueError: If the hash is not found or is pushed
            CalledProcessError: If the git command fails
        """
        # Verify the hash is unpushed
        pushed_hashes = self.getPushedHashes()
        if hash_val in pushed_hashes:
            raise ValueError(f"Cannot amend pushed commit {hash_val}")

        # Get current HEAD hash to determine if this is the top commit
        try:
            head_hash_output = self._git_run(["git", "-C", self._repoRoot, "rev-parse", "HEAD"], text=True)
            head_hash = head_hash_output.strip() if head_hash_output else None
        except CalledProcessError as e:
            self.printException(e, "amendCommitMessage: failed to get HEAD hash")
            raise ValueError("Failed to get HEAD commit")

        # Case 1: Amending HEAD directly
        if head_hash and head_hash.startswith(hash_val):
            try:
                # Run the amendment command
                self._git_run(
                    ["git", "-C", self._repoRoot, "commit", "--amend", "-m", new_message], text=True, ignorecache=True
                )
                logger.debug(f"amendCommitMessage: amended HEAD {hash_val}")
                # Get the new hash after amendment
                new_hash_output = self._git_run(
                    ["git", "-C", self._repoRoot, "rev-parse", "HEAD"], text=True, ignorecache=True
                )
                new_hash = new_hash_output.strip() if new_hash_output else hash_val
                return new_hash
            except CalledProcessError as e:
                self.printException(e, "amendCommitMessage: git commit --amend failed")
                raise

        # Case 2: Amending a non-HEAD unpushed commit using rebase
        # First verify the hash exists
        verification_output = self._git_run(["git", "-C", self._repoRoot, "cat-file", "-t", hash_val], text=True)
        if not verification_output:
            raise ValueError(f"Commit hash {hash_val} not found in repository")

        # Use git rebase --exec to amend the target commit when replayed.
        # This avoids interactive todo editing and does not require execute
        # permissions on the temporary script because it is invoked via
        # `python3 <script>`.
        try:
            # Determine rebase starting point. For non-root commits, rebase
            # the range after the target's parent. For root commits, use
            # --root so the target commit is replayed.
            parent_output = self._git_run(
                ["git", "-C", self._repoRoot, "rev-parse", f"{hash_val}^"],
                text=True,
            )
            parent_hash = parent_output.strip() if parent_output else None

            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                exec_script = f.name
                f.write(
                    "\n".join(
                        [
                            "import subprocess",
                            "import sys",
                            f'target_hash = "{hash_val}"',
                            f"new_message = {new_message!r}",
                            "current_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()",
                            "if current_hash.startswith(target_hash):",
                            "    subprocess.check_call(['git', 'commit', '--amend', '-m', new_message])",
                            "sys.exit(0)",
                        ]
                    )
                )

            try:
                if parent_hash:
                    rebase_cmd = [
                        "git",
                        "-c",
                        "rebase.autoStash=true",
                        "-C",
                        self._repoRoot,
                        "rebase",
                        "--exec",
                        f"python3 {exec_script}",
                        parent_hash,
                    ]
                else:
                    rebase_cmd = [
                        "git",
                        "-c",
                        "rebase.autoStash=true",
                        "-C",
                        self._repoRoot,
                        "rebase",
                        "--root",
                        "--exec",
                        f"python3 {exec_script}",
                    ]

                completed = run(rebase_cmd, cwd=self._repoRoot, text=True, capture_output=True)
                if completed.returncode != 0:
                    logger.warning(
                        "amendCommitMessage: rebase failed rc=%s stdout=%r stderr=%r",
                        completed.returncode,
                        completed.stdout,
                        completed.stderr,
                    )
                    raise CalledProcessError(
                        completed.returncode,
                        rebase_cmd,
                        output=completed.stdout,
                        stderr=completed.stderr,
                    )
                logger.debug(f"amendCommitMessage: amended {hash_val} via rebase --exec")
                # Get the new hash after rebase by finding the commit with the new message
                # Search for commits with matching message (use the subject line)
                subject_line = new_message.split("\n")[0] if new_message else ""
                log_output = self._git_run(
                    ["git", "-C", self._repoRoot, "log", "--all", "--pretty=format:%H %s", "-n", "20"],
                    text=True,
                    ignorecache=True,
                )
                new_hash = None
                for line in (log_output or "").splitlines():
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and parts[1] == subject_line:
                        new_hash = parts[0]
                        break
                return new_hash or hash_val  # Fallback to old hash if we can't find new one

            finally:
                try:
                    os.unlink(exec_script)
                except Exception as e:
                    self.printException(e, "amendCommitMessage: failed to clean up temporary script")

        except CalledProcessError as e:
            self.printException(e, "amendCommitMessage: git rebase --exec failed")
            raise

        except Exception as e:
            self.printException(e, "amendCommitMessage: rebase --exec setup failed")
            raise


def main(argv=None):
    """
    CLI entrypoint for exercising `GitRepo` helpers.

        Pass a filesystem `path` followed by one or more `--<method>` flags
        to invoke the corresponding `GitRepo` helper. Use `-j/--json` to
        request JSON output (bytes are base64-encoded when needed).
    """

    parser = argparse.ArgumentParser(description="CLI helper for GitRepo internals")
    parser.add_argument("path", help="file or directory used to discover the git repo")
    parser.add_argument("-1", "--hash1", dest="hash1", help="first hash/token")
    parser.add_argument("-2", "--hash2", dest="hash2", help="second hash/token")
    parser.add_argument("-f", "--file", dest="file", help="file (repo-relative or path)")
    parser.add_argument("-F", "--file2", dest="file2", help="second file")

    # Add boolean flags for many GitRepo methods (name matches method)
    flags = [
        "resolve_repo_top",
        "cwd_plus_path_to_reldir_relfile",
        "repo_rel_path_to_reldir_relfile",
        "reldir_plus_path_to_reldir_relfile",
        "abs_path_for",
        "get_repo_root",
        "index_mtime_iso",
        "safe_mtime",
        "getFileListBetweenNormalizedHashes",
        "getFileListBetweenNewRepoAndTopHash",
        "getFileListBetweenTwoCommits",
        "getFileListBetweenNewRepoAndHash",
        "getFileListBetweenNewRepoAndStaged",
        "getFileListBetweenNewRepoAndMods",
        "getFileListBetweenHashAndCurrentTime",
        "getFileListBetweenHashAndStaged",
        "getFileListBetweenStagedAndMods",
        "getFileListUntracked",
        "getFileListIgnored",
        "getFileListUntrackedAndIgnored",
        "getNormalizedHashListComplete",
        "getHashListEntireRepo",
        "getHashListStagedChanges",
        "getHashListNewChanges",
        "getHashListNewRepo",
        "getNormalizedHashListFromFileName",
        "getDiff",
        "build_diff_cmd",
        "getFileContents",
    ]

    for f in flags:
        parser.add_argument(f"--{f}", dest=f, action="store_true", help=f"invoke GitRepo.{f}()")

    # Optional JSON output for CLI consumers
    parser.add_argument("-j", "--json", dest="json", action="store_true", help="output results as JSON")
    # Convenience: set all boolean flags
    parser.add_argument("-A", "--all", dest="all", action="store_true", help="invoke all available method flags")

    args = parser.parse_args(argv)

    # If --all requested, enable every boolean flag defined above
    if args.all:
        for f in flags:
            setattr(args, f, True)

    # Create GitRepo using provided path
    try:
        repo = GitRepo(args.path)
    except Exception as e:
        printException(e, f"main: failed to create GitRepo for {args.path}")
        print(f"failed to create GitRepo for {args.path}: {e}")
        raise

    # mapping: option name -> (method_name, param_names, use_class)
    mapping = {
        "resolve_repo_top": ("resolve_repo_top", ["path"], True),
        "cwd_plus_path_to_reldir_relfile": ("cwd_plus_path_to_reldir_relfile", ["path"], False),
        "repo_rel_path_to_reldir_relfile": ("repo_rel_path_to_reldir_relfile", ["file"], True),
        "reldir_plus_path_to_reldir_relfile": ("reldir_plus_path_to_reldir_relfile", ["path", "file"], False),
        "abs_path_for": ("abs_path_for", ["path", "file"], False),
        "get_repo_root": ("get_repo_root", [], False),
        "index_mtime_iso": ("index_mtime_iso", [], False),
        "safe_mtime": ("safe_mtime", ["file"], False),
        "getFileListBetweenNormalizedHashes": ("getFileListBetweenNormalizedHashes", ["hash1", "hash2"], False),
        "getFileListBetweenNewRepoAndTopHash": ("getFileListBetweenNewRepoAndTopHash", [], False),
        "getFileListBetweenTwoCommits": ("getFileListBetweenTwoCommits", ["hash1", "hash2"], False),
        "getFileListBetweenNewRepoAndHash": ("getFileListBetweenNewRepoAndHash", ["hash2"], False),
        "getFileListBetweenNewRepoAndStaged": ("getFileListBetweenNewRepoAndStaged", [], False),
        "getFileListBetweenNewRepoAndMods": ("getFileListBetweenNewRepoAndMods", [], False),
        "getFileListBetweenHashAndCurrentTime": ("getFileListBetweenHashAndCurrentTime", ["hash1"], False),
        "getFileListBetweenHashAndStaged": ("getFileListBetweenHashAndStaged", ["hash1"], False),
        "getFileListBetweenStagedAndMods": ("getFileListBetweenStagedAndMods", [], False),
        "getFileListUntracked": ("getFileListUntracked", [], False),
        "getFileListIgnored": ("getFileListIgnored", [], False),
        "getFileListUntrackedAndIgnored": ("getFileListUntrackedAndIgnored", [], False),
        "getNormalizedHashListComplete": ("getNormalizedHashListComplete", [], False),
        "getHashListEntireRepo": ("getHashListEntireRepo", [], False),
        "getHashListStagedChanges": ("getHashListStagedChanges", [], False),
        "getHashListNewChanges": ("getHashListNewChanges", [], False),
        "getHashListNewRepo": ("getHashListNewRepo", [], False),
        "getNormalizedHashListFromFileName": ("getNormalizedHashListFromFileName", ["file"], False),
        "getDiff": ("getDiff", ["file", "hash1", "hash2"], False),
        "build_diff_cmd": ("build_diff_cmd", ["file", "hash1", "hash2"], False),
        "getFileContents": ("getFileContents", ["hash1", "file"], False),
    }

    def _invoke(repo: GitRepo, name: str, args, param_names: list[str], use_class: bool = False):
        """
        Invoke method `name` on `repo` (or class) using parameters from `args`.

        `param_names` lists attribute names on `args` to pass to the call in order.
        If `use_class` is True the call is made on the `GitRepo` class.
        """
        params = []
        for p in param_names:
            val = getattr(args, p, None)
            if val is None:
                raise ValueError(f"missing required argument for {name}: {p}")
            params.append(val)

        target = GitRepo if use_class else repo
        if not hasattr(target, name):
            raise AttributeError(f"{target!r} has no attribute {name}")
        fn = getattr(target, name)
        return fn(*params)

    # Iterate flags and invoke selected functions
    for opt, (method_name, params, use_class) in mapping.items():
        if getattr(args, opt, False):
            try:
                # Special-case handlers for methods that need argument conversion
                if method_name == "getFileContents":
                    # Expect --file to be repository-relative path; split to reldir/relfile
                    if args.file is None:
                        raise ValueError("getFileContents requires --file to be set")
                    reldir, relfile = GitRepo.repo_rel_path_to_reldir_relfile(args.file)
                    res = repo.getFileContents(args.hash1, reldir, relfile)
                else:
                    # Validate parameters
                    for p in params:
                        if getattr(args, p, None) is None:
                            raise ValueError(f"{method_name} requires --{p}")
                    res = _invoke(repo, method_name, args, params, use_class=use_class)

                print(f"== {method_name} ==")
                if args.json:
                    try:
                        out = res
                        # Bytes -> try UTF-8 decode, otherwise base64-encode
                        if isinstance(res, (bytes, bytearray)):
                            try:
                                out = res.decode("utf-8")
                            except Exception as e:
                                printException(e, "main: decoding bytes to utf-8 failed")
                                out = {"__bytes_base64__": base64.b64encode(bytes(res)).decode("ascii")}

                        print(json.dumps(out, indent=2, default=lambda o: repr(o)))
                    except Exception as e:
                        printException(e, "main: json serialization failed")
                        print(f"json serialization failed: {e}")
                        print(repr(res))
                else:
                    print(repr(res))

            except Exception as e:
                printException(e, f"main: error invoking {method_name}")
                print(f"error invoking {method_name}: {e}")
                break


if __name__ == "__main__":
    main()
