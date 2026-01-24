#!/usr/bin/env python3

"""Test pygit2 and git CLI repository file listing methods."""

#  getFileListBetweenHashAndCurrentTime, getFileListBetweenStagedAndMods,
# getFileListUntrackedAndIgnored, and getFileListBetweenNewRepoAndMods

import argparse
import difflib
import sys
import traceback
import os
import hashlib
import time

import codecs
from datetime import datetime, timezone
from subprocess import check_output, CalledProcessError
import traceback
import sys





def printException(e: Exception, msg: str) -> None:
    """Module-level exception logger used before TestRepo instances are available."""
    funcName = sys._getframe(1).f_code.co_name
    short = msg or ""
    print(f"{funcName}: {short} - {e}")
    print(traceback.format_exc())


class AppException:
    """Mixin providing instance-level exception logging for apps and widgets.

    This centralizes `printException` so multiple base classes can inherit
    it and avoid duplicate implementations.
    """

    # BEGIN: printException v1
    def printException(self, e: Exception, msg: str) -> None:
        """Log an exception with the calling class and function name.

        Mirrors the module-level `printException` but includes the
        originating class/function context when `self` is available.
        """
        className = type(self).__name__
        funcName = sys._getframe(1).f_code.co_name
        short = msg or ""
        # logger.warning(f"{className}.{funcName}: {short} - {e}")
        # logger.warning(traceback.format_exc())
        print(f"{className}.{funcName}: {short} - {e}")
        print(traceback.format_exc())

    # END: printException v1


class TestRepo(AppException):
    """Test pygit2 and git CLI repository file listing methods."""

    STAGED_MESSAGE = "Staged changes"
    MODS_MESSAGE = "Unstaged modifications"
    # Pseudo-hash tokens used across diff dispatching
    NEWREPO = "NEWREPO"
    STAGED = "STAGED"
    MODS = "MODS"

    # BEGIN: __init__ v1
    def __init__(self, repoRoot: str, verbose: int = 0, silent: bool = False):
        self.repoRoot = repoRoot
        self.verbose = verbose
        self.silent = silent
        # One-time per-process cache for git CLI command results
        self._cmd_cache = {}

    # END: __init__ v1


    # pygit2_resolve_token_to_tree removed — git CLI only

    # _pygit2_format_commit_entry removed — git CLI commit formatting will be used

    # _pygit2_run_pygit2_diff removed — git CLI helpers are used for diffs

    # BEGIN: _deltas_to_results v1
    def _deltas_to_results(self, detailed: list, a_raw, b_raw) -> list[tuple[str, str]]:
        """Simplified conversion of detailed pygit2 deltas to `(path,status)`.

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

    # END: _deltas_to_results v1

    # BEGIN: index_mtime_iso v1
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

    # END: index_mtime_iso v1

    # BEGIN: _epoch_to_iso v1
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

    # END: _epoch_to_iso v1

    # BEGIN: _git_cli_decode_quoted_path v1
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

    # END: _git_cli_decode_quoted_path v1

    # BEGIN: _paths_mtime_iso v1
    def _paths_mtime_iso(self, paths: list[str]) -> str:
        """
        Given a list of repository-relative paths, return an ISO timestamp
        (UTC) representing the most-recent modification time among those
        files. If no mtimes can be determined, fall back to the index mtime.
        """
        mtimes: list[float] = []
        for p in paths:
            fp = os.path.join(self.repoRoot, p)
            try:
                if os.path.exists(fp):
                    mtimes.append(os.path.getmtime(fp))
            except Exception as e:
                self.printException(e, "_paths_mtime_iso: checking path mtime failed")
                continue
        if mtimes:
            return self._epoch_to_iso(max(mtimes))
        return self.index_mtime_iso()

    # END: _paths_mtime_iso v1

    # _pygit2_delta_status_to_str removed — mapping handled via git CLI name-status parsing
    # BEGIN: _git_cli_name_status_to_str v1
    def _git_cli_name_status_to_str(self, code: str) -> str:
        """Map git `--name-status` codes (e.g. A, M, D, R, C) to status strings.

        Accepts codes like 'A', 'M', 'D', 'R100', 'C75' and returns one of:
        'added', 'modified', 'deleted', 'renamed', 'copied'.
        """
        try:
            if not code:
                return ""
            first = code[0]
            if first == "A":
                return "added"
            if first == "M":
                return "modified"
            if first == "D":
                return "deleted"
            if first == "R":
                return "renamed"
            if first == "C":
                return "copied"
            return "modified"
        except Exception as e:
            self.printException(e, "_git_cli_name_status_to_str failed")
            return "modified"
    # END: _git_cli_name_status_to_str v1

    # BEGIN: _git_cli_parse_name_status_line v1
    def _git_cli_parse_name_status_line(self, line: str) -> tuple[str, str]:
        """Parse a single `--name-status` line into `(path,status)`.

        Handles rename/copy output where the new path may be present.
        """
        try:
            parts = line.split()
            if not parts:
                return ("", "")
            code = parts[0].strip()
            path = parts[1].strip() if len(parts) > 1 else ""
            status = self._git_cli_name_status_to_str(code)
            try:
                if code.startswith("R") and len(parts) > 2:
                    newp = parts[-1].strip()
                    if newp:
                        status = f"renamed->{newp}"
            except Exception as e:
                self.printException(e, "_git_cli_parse_name_status_line: including rename target failed")
            return (path, status)
        except Exception as e:
            self.printException(e, "_git_cli_parse_name_status_line failed")
            return ("", "")

    # END: _git_cli_parse_name_status_line v1

    # BEGIN: _git_cli_name_status v1
    def _git_cli_name_status(self, args: list) -> list[tuple[str, str]]:
        """Run a `git` command that emits `--name-status`-style output and parse it.

        `args` should be a list suitable for `subprocess.check_output`, for
        example `['git','diff','--name-status', 'A', 'B']` or
        `['git','diff','--name-status','--cached']`.
        Returns a sorted list of `(path, status)`.
        """
        try:
            try:
                output = check_output(args, cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []

            results: list[tuple[str, str]] = []
            for line in output.splitlines():
                if not line:
                    continue
                path, status = self._git_cli_parse_name_status_line(line)
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            return results
        except Exception as e:
            self.printException(e, "_git_cli_name_status: unexpected failure")
            return []

    # END: _git_cli_name_status v1

    # _pygit2_empty_tree_for_repo removed — not needed when using git CLI

    # BEGIN: getFileListBetweenNewRepoAndTopHash v1
    def getFileListBetweenNewRepoAndTopHash(self, usePyGit2: bool) -> list[str]:
        """Return a list of `(path, status)` for files present in HEAD.

        Status will be `committed` to indicate file is present in the
        given commit (HEAD).
        """
        # Delegate to the new initial->commit helper to avoid duplication
        return self.getFileListBetweenNewRepoAndHash("HEAD", usePyGit2)

    # END: getFileListBetweenNewRepoAndTopHash v1

    # BEGIN: getFileListBetweenNormalizedHashes v1
    def getFileListBetweenNormalizedHashes(
        self, prev_hash: str, curr_hash: str, usePyGit2: bool
    ) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between `prev_hash` and `curr_hash`.

        Status values: `added`, `modified`, `deleted`, `renamed`, `copied`.
        This function expects `prev_hash` and `curr_hash` to be commit-ish values
        (i.e. resolvable by pygit2.revparse_single or valid git commit refs).
        This function MUST be a dispatch to specialized handlers for each case.
        """
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of files
        # Dispatch to specialized handlers for common token combinations.

        # Validate tokens: disallow bare `None` — require explicit `NEWREPO` token.
        if prev_hash is None or curr_hash is None:
            raise ValueError(
                "getFileListBetweenNormalizedHashes: None is not a valid token; use TestRepo.NEWREPO for initial repository"
            )

        # If identical tokens were passed, there are no changes.
        if prev_hash == curr_hash:
            return []

        # If prev is the pseudo-NewRepo token and curr is a normal hash -> new->hash
        if prev_hash == self.NEWREPO and curr_hash not in (self.STAGED, self.MODS):
            return self.getFileListBetweenNewRepoAndHash(curr_hash, usePyGit2)

        # If prev is NEWREPO and curr is staged -> initial->staged
        if prev_hash == self.NEWREPO and curr_hash == self.STAGED:
            return self.getFileListBetweenNewRepoAndStaged(usePyGit2)

        # If prev is NEWREPO and curr is working tree (mods) -> initial->mods
        if prev_hash == self.NEWREPO and curr_hash == self.MODS:
            return self.getFileListBetweenNewRepoAndMods(usePyGit2)

        # If prev and curr are both normal hashes -> direct commit->commit diff
        if prev_hash not in (self.NEWREPO, self.STAGED, self.MODS) and curr_hash not in (
            self.NEWREPO,
            self.STAGED,
            self.MODS,
        ):
            return self.getFileListBetweenTwoCommits(prev_hash, curr_hash, usePyGit2)

        # Hash -> staged
        if prev_hash not in (self.NEWREPO, self.STAGED, self.MODS) and curr_hash == self.STAGED:
            return self.getFileListBetweenHashAndStaged(prev_hash, usePyGit2)

        # Hash -> working tree (mods)
        if prev_hash not in (self.NEWREPO, self.STAGED, self.MODS) and curr_hash == self.MODS:
            return self.getFileListBetweenHashAndCurrentTime(prev_hash, usePyGit2)

        # staged -> mods (working tree)
        if prev_hash == self.STAGED and curr_hash == self.MODS:
            return self.getFileListBetweenStagedAndMods(usePyGit2)

        # Fallback: for remaining (likely commit-ish) combos delegate to the
        # explicit two-commit handler rather than the fully generic resolver.
        return self.getFileListBetweenTwoCommits(prev_hash, curr_hash, usePyGit2)

    # END: getFileListBetweenNormalizedHashes v1

    # BEGIN: getFileListBetweenTwoCommits v1
    def getFileListBetweenTwoCommits(self, prev_hash: str, curr_hash: str, usePyGit2: bool) -> list[tuple[str, str]]:
        """Direct commit->commit diff (both args expected to be commit-ish).

        Extracted helper containing the previous logic for diffing two commits.
        """
        # Use git CLI for commit->commit diffs (pygit2 removed)
        return self._git_cli_name_status(["git", "diff", "--name-status", prev_hash, curr_hash])

    # END: getFileListBetweenTwoCommits v1

    # def getFileListBetweenAnyTwoHashes(self, prev_hash: str, curr_hash: str, usePyGit2: bool) -> list[tuple[str, str]]:
    #     """Return a list of `(path, status)` for files changed between `prev_hash` and `curr_hash`.

    #     Status values are the same as other diffs (added/modified/etc.).
    #     """
    #     # General handler: support tokens None, "STAGED", "MODS", or commit-ish
    #     if usePyGit2:
    #         if not pygit2:
    #             raise RuntimeError("pygit2 is not available")
    #         repo = self.pygit2_repo

    #         def _resolve_token(tok):
    #             try:
    #                 if tok == "STAGED":
    #                     # index tree
    #                     try:
    #                         repo.index.read()
    #                         oid = repo.index.write_tree()
    #                         return repo.get(oid)
    #                     except Exception as e:
    #                         self.printException(e, "getFileListBetweenAnyTwoHashes: index -> tree failed")
    #                         return None
    #                 if tok == "MODS":
    #                     # working tree represented as None for pygit2.diff
    #                     return None
    #                 if tok is None:
    #                     # initial / no-ancestor case
    #                     return None
    #                 # commit-ish: try revparse then fallback to repo.get
    #                 try:
    #                     obj = repo.revparse_single(tok)
    #                 except Exception:
    #                     try:
    #                         obj = repo.get(tok)
    #                     except Exception:
    #                         return None
    #                 return self._resolve_tree(obj)
    #             except Exception as e:
    #                 self.printException(e, "getFileListBetweenAnyTwoHashes: resolve_token failed")
    #                 return None

    #         a = _resolve_token(prev_hash)
    #         b = _resolve_token(curr_hash)

    #         # Coerce any non-tree pygit2 objects to tree-ish using _resolve_tree
    #         try:
    #             if a is not None and not isinstance(a, pygit2.Tree):
    #                 a = self._resolve_tree(a)
    #             if b is not None and not isinstance(b, pygit2.Tree):
    #                 b = self._resolve_tree(b)
    #         except Exception as e:
    #             self.printException(e, "getFileListBetweenAnyTwoHashes: coercing to tree failed")

    #         # Debug: show token types when attempting diff to aid diagnosis
    #         print(f"getFileListBetweenAnyTwoHashes: DEBUG tokens {prev_hash}->{curr_hash} a={type(a)} b={type(b)}")

    #         # Ensure we don't pass None to pygit2.diff; replace None with an
    #         # empty tree object constructed via TreeBuilder. Some pygit2
    #         # builds accept diff(None, tree) but many raise ValueError.
    #         try:
    #             if a is None:
    #                 a = self._empty_tree_for_repo(repo)
    #             if b is None:
    #                 b = self._empty_tree_for_repo(repo)
    #             if a is None or b is None:
    #                 return []
    #         except Exception as e:
    #             self.printException(e, "getFileListBetweenAnyTwoHashes: constructing empty tree failed")
    #             return []

    #         try:
    #             diff = repo.diff(a, b)
    #         except Exception as e:
    #             self.printException(e, "getFileListBetweenAnyTwoHashes: pygit2 diff failed even after empty-tree substitution")
    #             return []

    #         results: list[tuple[str, str]] = []
    #         for delta in diff.deltas:
    #             path = getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None)
    #             status = self._delta_status_to_str(getattr(delta, "status", None))
    #             if path:
    #                 results.append((path, status))
    #         results.sort(key=lambda x: x[0])
    #         return results

    #     else:
    #         # Build git CLI args to express the desired comparison
    #         base = ["git", "diff", "--name-status"]
    #         args = list(base)

    #         ph = prev_hash
    #         ch = curr_hash

    #         try:
    #             if ph is None and ch not in ("STAGED", "MODS"):
    #                 # compare initial -> commit (single-arg form)
    #                 args += [ch]
    #             elif ph is None and ch == "STAGED":
    #                 # staged vs empty (rare): use --cached
    #                 args += ["--cached"]
    #             elif ph not in (None, "STAGED", "MODS") and ch not in (None, "STAGED", "MODS"):
    #                 # commit -> commit
    #                 args += [ph, ch]
    #             elif ph not in (None, "STAGED", "MODS") and ch == "STAGED":
    #                 # commit -> staged (use --cached <commit>)
    #                 args += ["--cached", ph]
    #             elif ph not in (None, "STAGED", "MODS") and ch == "MODS":
    #                 # commit -> working tree (single-arg commit)
    #                 args += [ph]
    #             elif ph == "STAGED" and ch == "MODS":
    #                 # index -> worktree
    #                 pass
    #             elif ph == "STAGED" and ch not in (None, "STAGED", "MODS"):
    #                 # staged -> commit (compare index to commit)
    #                 args += ["--cached", ch]
    #             elif ph == "MODS" and ch not in (None, "STAGED", "MODS"):
    #                 # worktree -> commit (single-arg commit)
    #                 args += [ch]
    #             else:
    #                 # fallback to comparing index vs worktree
    #                 pass
    #         except Exception as e:
    #             self.printException(e, "getFileListBetweenAnyTwoHashes: building git args failed")
    #             return []

    #         try:
    #             output = check_output(args, cwd=self.repoRoot, text=True)
    #         except CalledProcessError as e:
    #             self.printException(e, "git command failed")
    #             return []

    #         results: list[tuple[str, str]] = []
    #         for line in output.splitlines():
    #             if not line:
    #                 continue
    #             path, status = self._parse_git_name_status_line(line)
    #             if path:
    #                 results.append((path, status))
    #         results.sort(key=lambda x: x[0])
    #         return results

    # BEGIN: getFileListBetweenNewRepoAndHash v1
    def getFileListBetweenNewRepoAndHash(self, curr_hash: str, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between the beginning and `curr_hash`.

        Status values are the same as other diffs (added/modified/etc.).
        """
        # Git-CLI implementation with a one-time per-hash cache. The
        # `usePyGit2` flag is ignored for this specialized initial->commit case.
        key = f"getFileListBetweenNewRepoAndHash:{curr_hash}"
        try:
            if key in self._cmd_cache:
                return self._cmd_cache[key]

            try:
                output = check_output(["git", "ls-tree", "-r", "--name-only", curr_hash], cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                self._cmd_cache[key] = []
                return []

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

    # END: getFileListBetweenNewRepoAndHash v1

    # BEGIN: getFileListBetweenNewRepoAndStaged v1
    def getFileListBetweenNewRepoAndStaged(self, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return file list for the initial (empty) tree -> staged index comparison.

        This is the specialized handler for the `prev is None and curr == STAGED`
        case so `getFileListBetweenNormalizedHashes` can remain a dispatcher.
        """
        # Git-CLI-only implementation cached once per process.
        key = "getFileListBetweenNewRepoAndStaged"
        return self._git_cli_getCachedFileList(key, ["git", "diff", "--name-status", "--cached"])

    # END: getFileListBetweenNewRepoAndStaged v1

    # BEGIN: getFileListBetweenNewRepoAndMods v1
    # make git-only
    def getFileListBetweenNewRepoAndMods(self, usePyGit2: bool) -> list[tuple[str, str]]:
        """Specialized handler for initial (empty) -> working tree (mods) comparison.

        This implementation is git-CLI-only and mirrors `git diff --name-status`.
        The `usePyGit2` flag is ignored and results are cached once per process.
        """
        key = "getFileListBetweenNewRepoAndMods"
        return self._git_cli_getCachedFileList(key, ["git", "diff", "--name-status"])

    # END: getFileListBetweenNewRepoAndMods v1

    # BEGIN: getFileListBetweenTopHashAndCurrentTime v1
    def getFileListBetweenTopHashAndCurrentTime(self, usePyGit2: bool) -> list[str]:
        """Return a list of `(path, status)` for files changed between HEAD and working tree.

        Status will reflect the working-tree change type (modified/added/deleted).
        """
        # Delegate to the general handler to avoid duplicating logic
        return self.getFileListBetweenHashAndCurrentTime("HEAD", usePyGit2)

    # END: getFileListBetweenTopHashAndCurrentTime v1

    # BEGIN: _git_cli_getCachedFileList v1
    def _git_cli_getCachedFileList(self, key: str, git_args: list) -> list[tuple[str, str]]:
        """Run `git_args` (list) and cache the parsed name-status results under `key`.

        Returns a sorted list of `(path,status)`. On git failure returns [].
        """
        try:
            if key in self._cmd_cache:
                return self._cmd_cache[key]

            try:
                output = check_output(git_args, cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                self._cmd_cache[key] = []
                return []

            results: list[tuple[str, str]] = []
            for line in output.splitlines():
                if not line:
                    continue
                path, status = self._git_cli_parse_name_status_line(line)
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            self._cmd_cache[key] = results
            return results
        except Exception as e:
            self.printException(e, "_git_cli_getCachedFileList: unexpected failure")
            return []

    # END: _git_cli_getCachedFileList v1

    # BEGIN: getFileListBetweenHashAndCurrentTime v1
    def getFileListBetweenHashAndCurrentTime(self, hash: str, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return `(path,status)` for files changed between `hash` and working tree.

        Uses the git CLI plus a one-time cache via `_git_cli_getCachedFileList`.
        """
        key = f"getFileListBetweenHashAndCurrentTime:{hash}"
        return self._git_cli_getCachedFileList(key, ["git", "diff", "--name-status", hash])

    # END: getFileListBetweenHashAndCurrentTime v1

    # BEGIN: getFileListBetweenTopHashAndStaged v1
    def getFileListBetweenTopHashAndStaged(self, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between HEAD and staged index."""
        # Delegate to the generalized staged-vs-hash implementation to avoid duplication
        return self.getFileListBetweenHashAndStaged("HEAD", usePyGit2)

    # END: getFileListBetweenTopHashAndStaged v1

    # BEGIN: getFileListBetweenHashAndStaged v1
    def getFileListBetweenHashAndStaged(self, hash: str, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return `(path,status)` for files changed between `hash` and the staged index.

        Generalization of getFileListBetweenTopHashAndStaged for any commit-ish.
        """
        # Use git-CLI with a per-hash cache. The `usePyGit2` branch is
        # intentionally ignored to prefer the cached git-CLI path.
        key = f"getFileListBetweenHashAndStaged:{hash}"
        return self._git_cli_getCachedFileList(key, ["git", "diff", "--name-status", "--cached", hash])

    # END: getFileListBetweenHashAndStaged v1

    # BEGIN: getFileListBetweenStagedAndMods v1
    # make git-only
    def getFileListBetweenStagedAndMods(self, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between staged index and working tree (mods)."""
        # Use git CLI to get the list of files; cache the results once per process
        key = "getFileListBetweenStagedAndMods"
        return self._git_cli_getCachedFileList(key, ["git", "diff", "--name-status"]) 

    # END: getFileListBetweenStagedAndMods v1

    # BEGIN: getFileListUntrackedAndIgnored v1
    # make git-only
    def getFileListUntrackedAndIgnored(self, usePyGit2: bool) -> list[tuple[str, str, str]]:
        """Return a sorted list of `(path, iso_mtime, status)` for files that are
        either untracked or ignored in the working tree.

        This implementation is git-CLI-only and ignores the `usePyGit2` flag.
        - `status` is one of: `untracked`, `ignored`.
        - `iso_mtime` is produced from the filesystem mtime via `_epoch_to_iso`.
        """
        try:
            cache_key = "getFileListUntrackedAndIgnored"
            if cache_key in self._cmd_cache:
                return self._cmd_cache[cache_key]

            results: list[tuple[str, str, str]] = []
            seen: set[str] = set()

            untracked_out = ""
            ignored_out = ""
            try:
                untracked_out = check_output(
                    ["git", "ls-files", "--others", "--exclude-standard"], cwd=self.repoRoot, text=True
                )
            except CalledProcessError as e:
                self.printException(e, "git ls-files untracked failed")
                untracked_out = ""

            try:
                ignored_out = check_output(
                    ["git", "ls-files", "--others", "-i", "--exclude-standard"], cwd=self.repoRoot, text=True
                )
            except CalledProcessError as e:
                self.printException(e, "git ls-files ignored failed")
                ignored_out = ""

            for line in untracked_out.splitlines():
                rel = line.strip()
                rel = self._git_cli_decode_quoted_path(rel)
                if not rel or rel in seen:
                    continue
                seen.add(rel)
                fp = os.path.join(self.repoRoot, rel)
                try:
                    if os.path.islink(fp):
                        mtime = os.lstat(fp).st_mtime
                    elif os.path.exists(fp):
                        mtime = os.path.getmtime(fp)
                    else:
                        mtime = None
                except FileNotFoundError as e:
                    self.printException(e, "getFileListUntrackedAndIgnored: file not found (untracked)")
                    continue
                except Exception as e:
                    self.printException(e, "getFileListUntrackedAndIgnored: stat failed")
                    mtime = None
                iso = self._epoch_to_iso(mtime) if mtime is not None else self.index_mtime_iso()
                results.append((rel, iso, "untracked"))

            for line in ignored_out.splitlines():
                rel = line.strip()
                rel = self._git_cli_decode_quoted_path(rel)
                if not rel or rel in seen:
                    continue
                seen.add(rel)
                fp = os.path.join(self.repoRoot, rel)
                try:
                    if os.path.islink(fp):
                        mtime = os.lstat(fp).st_mtime
                    elif os.path.exists(fp):
                        mtime = os.path.getmtime(fp)
                    else:
                        mtime = None
                except FileNotFoundError as e:
                    self.printException(e, "getFileListUntrackedAndIgnored: file not found (ignored)")
                    continue
                except Exception as e:
                    self.printException(e, "getFileListUntrackedAndIgnored: stat failed")
                    mtime = None
                iso = self._epoch_to_iso(mtime) if mtime is not None else self.index_mtime_iso()
                results.append((rel, iso, "ignored"))

            results.sort(key=lambda x: x[0])
            self._cmd_cache[cache_key] = results
            return results
        except Exception as e:
            self.printException(e, "getFileListUntrackedAndIgnored: unexpected failure")
            return []

    # END: getFileListUntrackedAndIgnored v1

    # BEGIN: getHashListComplete v1
    def getHashListComplete(self, usePyGit2: bool) -> list[tuple[str, str, str]]:
        """Return a combined list of commit hashes for staged, new, and entire repo."""
        new = self.getHashListNewChanges(usePyGit2)
        staged = self.getHashListStagedChanges(usePyGit2)
        entire = self.getHashListEntireRepo(usePyGit2)
        combined = new + staged + entire
        return combined

    # END: getHashListComplete v1

    # BEGIN: getHashListSample v1
    def getHashListSample(self, usePyGit2: bool) -> list[tuple[str, str, str]]:
        """Return a sampled list of commit hashes for staged, new, and entire repo.
        in the order newest to oldest.
        """
        entire = self.getHashListEntireRepo(usePyGit2)
        sampleHashes: list[tuple[str, str, str]] = []
        if len(entire) >= 4:
            sampleHashes.append(entire[0])
            sampleHashes.append(entire[len(entire) // 3])
            sampleHashes.append(entire[len(entire) * 2 // 3])
        elif len(entire) == 3:
            sampleHashes.append(entire[0])
            sampleHashes.append(entire[len(entire) // 2])
        elif len(entire) == 2:
            sampleHashes.append(entire[0])
        if len(entire) >= 1:
            sampleHashes.append(entire[-1])  # always add TOP
        return sampleHashes

    # END: getHashListSample v1

    # BEGIN: getHashListSamplePlusEnds v1
    def getHashListSamplePlusEnds(self, usePyGit2: bool) -> list[tuple[str, str, str]]:
        """Return a sampled list of commit hashes for staged, new, and entire repo.

        Order: MODS, STAGED, sampled commits (newest->oldest), NEWREPO
        """
        sampleHashes: list[tuple[str, str, str]] = []

        # Put working-tree (MODS) first when present
        mods = self.getHashListNewChanges(usePyGit2)
        if mods:
            sampleHashes += mods

        # Then staged marker
        staged = self.getHashListStagedChanges(usePyGit2)
        if staged:
            sampleHashes += staged

        # Then the sampled commits (getHashListSample returns newest->oldest)
        normalHashes = self.getHashListSample(usePyGit2)
        if normalHashes:
            sampleHashes += normalHashes

        # Place NEWREPO pseudo-entry last
        sampleHashes.append(("", self.NEWREPO, "Newly created repository"))
        return sampleHashes

    # END: getHashListSamplePlusEnds v1

    # BEGIN: runFileListSampledComparisons v1
    def runFileListSampledComparisons(self, top: bool, raw: bool) -> tuple[int, int, int]:
        """Run sampled comparisons and display diffs using `show_diffs`.

        Calls `getFileListBetweenNormalizedHashes` for both backends and
        forwards results to `show_diffs(label, pygit2_list, gitcli_list, top, raw)`.
        """
        sample = self.getHashListSamplePlusEnds(False)
        tokens: list = [x[1] for x in sample]
        # Reverse the token order for comparisons (user-requested)
        tokens.reverse()
        print(f"Tokens (newest to oldest)={tokens}")

        # For each sampled token pair, run both backends (pygit2 and git CLI)
        # and compare their outputs. Track simple statistics so callers can
        # aggregate totals across repositories.
        total = 0
        passed = 0
        failed = 0
        for i in range(len(tokens)):
            for j in range(i + 1, len(tokens)):
                a = tokens[i]
                b = tokens[j]
                total += 1
                # Ensure timing variables always exist (avoid locals() checks)
                t0 = t1 = t2 = t3 = 0
                try:
                    t0 = time.perf_counter()
                    # Use the public dispatcher so all call paths (including
                    # commit->staged merging) are exercised for pygit2 results.
                    try:
                        p = self.getFileListBetweenNormalizedHashes(a, b, True)
                    except Exception as e:
                        self.printException(e, f"runFileListSampledComparisons: pygit2 handler failed for {a}->{b}")
                        p = []
                    # Keep detailed raw deltas empty so diagnostics can
                    # request them if needed (they are expensive).
                    detailed = None
                    a_raw = None
                    b_raw = None
                    t1 = time.perf_counter()
                except Exception as e:
                    self.printException(e, f"runFileListSampledComparisons: pygit2 diff failed for {a}->{b}")
                    p = []
                try:
                    t2 = time.perf_counter()
                    g = self.getFileListBetweenNormalizedHashes(a, b, False)
                    t3 = time.perf_counter()
                except Exception as e:
                    self.printException(e, f"runFileListSampledComparisons: git CLI diff failed for {a}->{b}")
                    g = []

                try:
                    ok = show_diffs(f"\nget {a}->{b}", p, g, top, raw, self.verbose, self.silent)
                    if self.verbose:
                        # Report timings for the sampled pair
                        try:
                            dt_py = t1 - t0
                            dt_cli = t3 - t2
                            print(f"TIMING: get {a}->{b} pygit2={dt_py:.3f}s git={dt_cli:.3f}s")
                        except Exception as e:
                            self.printException(e, "runFileListSampledComparisons: timing print failed")
                    # If the sampled comparison failed, produce focused debug
                    # output to help diagnose mismatches between pygit2 and git CLI.
                    if not ok and self.verbose:
                        try:
                            print(f"DEBUG: Failure diagnostics for sampled pair {a}->{b}")
                            print(f"DEBUG: pygit2 returned {len(p)} entries; git returned {len(g)} entries")
                            set_p = set([x[0] for x in p])
                            set_g = set([x[0] for x in g])
                            only_p = sorted(list(set_p - set_g))
                            only_g = sorted(list(set_g - set_p))
                            print(f"DEBUG: paths only in pygit2 ({len(only_p)}): {only_p[:10]}")
                            print(f"DEBUG: paths only in git ({len(only_g)}): {only_g[:10]}")
                            # Request detailed pygit2 delta objects for deeper inspection
                            try:
                                # Prefer reusing the previously computed detailed
                                # list so diagnostics examine the same objects.
                                if detailed is None:
                                    detailed, a_raw, b_raw = self._pygit2_run_pygit2_diff(a, b)
                                print(f"DEBUG: _pygit2_run_pygit2_diff returned {len(detailed)} detailed deltas")
                                if self.verbose > 1:
                                    for dd in detailed[:50]:
                                        try:
                                            print(
                                                f"DEBUG: detailed delta: path={dd.get('path')} status={dd.get('status')} old_oid={dd.get('old_oid')} new_oid={dd.get('new_oid')}"
                                            )
                                        except Exception as e:
                                            self.printException(e, "debug: printing detailed delta failed")
                                            print(f"DEBUG: detailed delta repr: {dd!r}")
                                # Print raw tree refs (if available)
                                print(f"DEBUG: a_raw={a_raw!r} b_raw={b_raw!r}")

                                # Run the same post-processing used by the normal pygit2 path
                                try:
                                    processed = self._deltas_to_results(detailed, a_raw, b_raw)
                                    print(
                                        f"DEBUG: post-processed pygit2 results (from detailed) count={len(processed)}"
                                    )
                                    if self.verbose > 0:
                                        for it in processed[:50]:
                                            try:
                                                print(f"DEBUG: post-processed: {it}")
                                            except Exception as e:
                                                self.printException(e, "debug: printing post-processed result failed")
                                                print(f"DEBUG: post-processed repr: {it!r}")
                                    # Compare processed results to the previously computed `p`
                                    try:
                                        set_processed = set([x[0] for x in processed])
                                        set_p_orig = set([x[0] for x in p])
                                        only_proc = sorted(list(set_processed - set_p_orig))
                                        only_orig = sorted(list(set_p_orig - set_processed))
                                        print(f"DEBUG: paths only in post-processed (not in p): {only_proc[:10]}")
                                        print(
                                            f"DEBUG: paths only in original p (not in post-processed): {only_orig[:10]}"
                                        )
                                    except Exception as e:
                                        self.printException(
                                            e, "runFileListSampledComparisons: comparing processed->p failed"
                                        )
                                except Exception as e:
                                    self.printException(
                                        e, "runFileListSampledComparisons: post-processing detailed deltas failed"
                                    )
                            except Exception as e:
                                self.printException(
                                    e, "runFileListSampledComparisons: fetching detailed pygit2 diff failed"
                                )
                        except Exception as e:
                            self.printException(e, "runFileListSampledComparisons: failure diagnostics failed")
                    if ok:
                        passed += 1
                    else:
                        failed += 1
                except Exception as e:
                    self.printException(e, f"runFileListSampledComparisons: show_diffs failed for {a}->{b}")
                    failed += 1

        return (total, passed, failed)

    # END: runFileListSampledComparisons v1

    # BEGIN: getHashListEntireRepo v1
    def getHashListEntireRepo(self, usePyGit2: bool) -> list[tuple[str, str, str]]:
        """Return a list of all commit hashes in the repository."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of hashes
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            repo = self.pygit2_repo
            commits = set()
            start_oids = set()
            try:
                # Debug: show references available (helpful when matching git rev-list)
                try:
                    ref_preview = list(repo.references)[:20]
                except Exception as e:
                    self.printException(e, "getHashListEntireRepo: previewing references failed")
                # Collect starting commit OIDs from all references. Try multiple
                # resolution strategies to match `git rev-list --all` semantics.
                for ref_entry in repo.references:
                    try:
                        # Normalize to a reference name string; repo.references
                        # may yield Reference objects or string names depending
                        # on pygit2 version.
                        ref_name = ref_entry if isinstance(ref_entry, str) else getattr(ref_entry, "name", None)
                        if not ref_name:
                            continue
                        # Prefer resolving via revparse_single(name)
                        try:
                            obj = repo.revparse_single(ref_name)
                        except Exception as e:
                            self.printException(e, f"getHashListEntireRepo: revparse_single failed for {ref_name}")
                            ref = repo.references.get(ref_name)
                            if ref is None:
                                continue
                            target = getattr(ref, "target", None)
                            if target is None:
                                continue
                            try:
                                obj = repo.get(target)
                            except Exception as e:
                                self.printException(
                                    e, f"getHashListEntireRepo: repo.get failed for target of {ref_name}"
                                )
                                continue

                        # Peel annotated tags to commits
                        if isinstance(obj, pygit2.Tag):
                            try:
                                obj = repo.get(obj.target)
                            except Exception as e:
                                self.printException(e, "getHashListEntireRepo: tag peel failed")
                                continue

                        if isinstance(obj, pygit2.Commit):
                            start_oids.add(obj.id)
                    except Exception as e:
                        self.printException(e, "getHashListEntireRepo: resolving ref entry failed")
                        continue

                # Also include HEAD explicitly if present
                try:
                    try:
                        head_obj = repo.revparse_single("HEAD")
                    except Exception as e:
                        self.printException(e, "getHashListEntireRepo: head revparse failed")
                        head_obj = None
                    if isinstance(head_obj, pygit2.Tag):
                        try:
                            head_obj = repo.get(head_obj.target)
                        except Exception as e:
                            self.printException(e, "getHashListEntireRepo: repo.get for HEAD tag failed")
                            head_obj = None
                    if isinstance(head_obj, pygit2.Commit):
                        start_oids.add(head_obj.id)
                except Exception as e:
                    self.printException(e, "getHashListEntireRepo: head handling failed")

                # Walk from a combined walker seeded with all start OIDs to
                # collect commits.
                if start_oids:
                    oids = list(start_oids)
                    first = oids[0]
                    try:
                        walker = repo.walk(first, pygit2.GIT_SORT_TIME)
                        for extra in oids[1:]:
                            try:
                                walker.push(extra)
                            except Exception as e:
                                self.printException(e, "getHashListEntireRepo: walker.push failed")
                        for c in walker:
                            ch = getattr(c, "hex", None)
                            if not ch:
                                cid = getattr(c, "id", None)
                                if cid is not None:
                                    ch = getattr(cid, "hex", None) or str(cid)
                            if ch:
                                commits.add(ch)
                    except Exception as ex:
                        self.printException(ex, "pygit2 combined walker failed")

            except Exception as e:
                self.printException(e, "enumerating references failed")
                return []

            # Build timestamped entries for each commit and sort by (ts, hash)
            commit_info = []
            for h in commits:
                try:
                    obj = repo.get(h)
                    t = getattr(obj, "time", None) or getattr(obj, "commit_time", None) or 0
                    ts = int(t)
                except Exception as e:
                    self.printException(e, "getHashListEntireRepo: commit time extraction failed")
                    ts = 0
                commit_info.append((ts, h))

            commit_info.sort(key=lambda x: (x[0], x[1]), reverse=True)
            formatted: list[tuple[str, str, str]] = []
            for ts, h in commit_info:
                formatted.append(self._pygit2_format_commit_entry(repo, h))
            return formatted

        else:
            try:
                # Use git log to get commit epoch time, hash and subject for all refs
                output = check_output(
                    ["git", "log", "--all", "--pretty=format:%ct %H %s"], cwd=self.repoRoot, text=True
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []
            pairs = []
            for line in output.splitlines():
                if not line:
                    continue
                parts = line.split(None, 2)
                if len(parts) < 2:
                    continue
                try:
                    ts = int(parts[0])
                except Exception as e:
                    self.printException(e, "getHashListEntireRepo: parsing git log timestamp failed")
                    ts = 0
                h = parts[1].strip()
                subject = parts[2].strip() if len(parts) >= 3 else ""
                pairs.append((ts, h, subject))

            pairs.sort(key=lambda x: (x[0], x[1]), reverse=True)
            formatted: list[tuple[str, str, str]] = []
            for ts, h, subject in pairs:
                iso = self._epoch_to_iso(ts)
                formatted.append((iso, h, subject))
            return formatted

    # END: getHashListEntireRepo v1

    # BEGIN: getHashListStagedChanges v1
    def getHashListStagedChanges(self, usePyGit2: bool) -> list[tuple[str, str, str]]:
        """Return a list of commit hashes for staged changes."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of hashes
        # Return "STAGED" pseudo-hash if there are staged changes
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            repo = self.pygit2_repo
            # Read the index to ensure it is up-to-date with the on-disk index file
            repo.index.read()

            # Compare the index to the HEAD commit's tree
            # This diff represents the staged changes
            # repo.head.target is the OID of the current HEAD commit
            # repo[repo.head.target] gets the commit object
            # .tree gets the tree object for that commit
            head_tree = repo[repo.head.target].tree
            staged_changes = repo.index.diff_to_tree(head_tree)

            # If no staged changes, return quickly without computing index mtime
            # `staged_changes` may be an iterator-like; treat false as no changes
            if not staged_changes:
                return []

            iso = self.index_mtime_iso()
            return [(iso, "STAGED", self.STAGED_MESSAGE)] if staged_changes else []

        else:
            # Enumerate staged-only files via git diff --cached --name-only
            try:
                names_out = check_output(["git", "diff", "--cached", "--name-only"], cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []
            if not names_out:
                return []

            if any(ln.strip() for ln in names_out.splitlines()):
                iso = self.index_mtime_iso()
                return [(iso, "STAGED", self.STAGED_MESSAGE)]
            return []

    # END: getHashListStagedChanges v1

    # BEGIN: getHashListNewChanges v1
    def getHashListNewChanges(self, usePyGit2: bool) -> list[tuple[str, str, str]]:
        """Return a list of commit hashes for new changes."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of hashes
        # Return "NEW" pseudo-hash if there are changes made to any files in the repo
        # since the latest change to staging.
        paths: list[str] = []
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            repo = self.pygit2_repo
            # Read the index to ensure it is up-to-date with the on-disk index file
            repo.index.read()
            # Compare the working tree (None) to the index
            new_changes = repo.index.diff_to_workdir()
            if not new_changes:
                return []
            # Collect changed paths from the diff deltas
            try:
                for delta in getattr(new_changes, "deltas", []):
                    p = getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None)
                    if p:
                        paths.append(p)
            except Exception as e:
                self.printException(e, "getHashListNewChanges: collecting paths from deltas failed")

        else:
            # Enumerate working-tree-vs-index files via git diff --name-only
            try:
                names_out = check_output(["git", "diff", "--name-only"], cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")

            if not names_out:
                return []

            lns = [ln.strip() for ln in names_out.splitlines() if ln.strip()]
            if any(lns):
                paths = lns

        # Compute ISO based on working-tree paths' mtimes (centralized)
        iso = self._paths_mtime_iso(paths)
        return [(iso, "MODS", self.MODS_MESSAGE)] if paths else []

    # END: getHashListNewChanges v1

    # BEGIN: getHashListFromFileName v1
    def getHashListFromFileName(self, file_name: str, usePyGit2: bool) -> list[tuple[str, str, str]]:
        """Return a list of commit hashes that modified the given file.

        Uses the git CLI (`git log` + `git status`) with a one-time cache per
        `file_name`. The previous `pygit2` walk/diff implementation has been
        removed for performance consistency.
        """
        key = f"getHashListFromFileName:{file_name}"
        try:
            if key in self._cmd_cache:
                return self._cmd_cache[key]

            try:
                output = check_output(
                    ["git", "log", "--pretty=format:%ct %H %s", "--", file_name], cwd=self.repoRoot, text=True
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                self._cmd_cache[key] = []
                return []

            entries: list[tuple[str, str, str]] = []
            for line in output.splitlines():
                if not line:
                    continue
                parts = line.split(None, 2)
                if len(parts) < 2:
                    continue
                try:
                    ts = int(parts[0])
                except Exception as e:
                    self.printException(e, "getHashListFromFileName: parsing git log timestamp failed")
                    ts = 0
                h = parts[1].strip()
                subject = parts[2].strip() if len(parts) >= 3 else ""
                iso = self._epoch_to_iso(ts)
                entries.append((iso, h, subject if subject else ""))

            try:
                status_out = check_output(
                    ["git", "status", "--porcelain", "--", file_name], cwd=self.repoRoot, text=True
                )
            except CalledProcessError as e:
                self.printException(e, f"git status failed for {file_name}")
                status_out = ""
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

    # END: getHashListFromFileName v1


# BEGIN: show_diffs v1
def show_diffs(
    test_name: str, list1: list, list2: list, top: int = 0, raw: bool = False, verbose: int = 0, silent: bool = False
) -> bool:
    """Show differences between two file lists. If equal and `top` > 0,
    print the first `top` lines from `list1`.
    Returns True when lists are equal, False when differences are found.
    """

    def fmt(e) -> str:
        if raw:
            return repr(e)
        if isinstance(e, tuple):
            if len(e) == 2:
                # file list entry: (path, status)
                return f"{e[0]} {e[1]}"
            if len(e) >= 3:
                t, h, *rest = e
                subj = rest[0] if rest else ""
                parts = []
                if t:
                    parts.append(t)
                if h:
                    parts.append(h)
                s = " ".join(parts)
                if subj:
                    return f"{s} {subj}" if s else subj
                return s
        return str(e)

    disp1 = [fmt(e) for e in list1]
    disp2 = [fmt(e) for e in list2]
    diff = list(difflib.unified_diff(disp1, disp2, fromfile="pygit2", tofile="git", lineterm=""))
    if diff:
        # Always report when differences are found. `--silent` only
        # suppresses success/no-diff messages.
        if verbose > 0:
            print(f"[{test_name}] Differences found (verbose={verbose}):")
        else:
            print(f"[{test_name}] Differences found:")
        for line in diff:
            print(line)
        return False
    else:
        lines = len(disp1)
        if not silent:
            print(f"[{test_name}] No differences found in {lines} lines of output")
        if top and top > 0:
            print(f"[{test_name}] Top {top} lines from pygit2 result:")
            for ln in list1[:top]:
                if raw:
                    print(repr(ln))
                else:
                    print(fmt(ln))
        return True


# END: show_diffs v1


def main():
    """Main function to run the tests."""
    parser = argparse.ArgumentParser(prog="gitdiffnavtool.py", description=__doc__)
    parser.add_argument(
        "-t",
        "--top",
        type=int,
        default=0,
        help="When two lists are equal, print the top N lines from the pygit2 result.",
    )
    parser.add_argument(
        "-R",
        "--raw",
        action="store_true",
        help="Display raw tuple values returned by the getHash* functions instead of formatted strings",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (can be specified multiple times)",
    )

    parser.add_argument(
        "-S",
        "--silent",
        action="store_true",
        default=False,
        help="Silence summary printouts such as 'No differences found in ## lines of output'",
    )

    parser.add_argument(
        "-u",
        "--up-through",
        type=str,
        default=None,
        help="Run tests up through this base-36 digit (0-9, a-z). Example: -u 2 runs -1 and -2; -u a runs up through -a",
    )

    parser.add_argument(
        "-H",
        "--getFileListBetweenNormalizedHashes",
        action="append",
        default=None,
        help="Invoke getFileListBetweenNormalizedHashes for comma-separated pairs (e.g. -H d22ead,f225e7)",
    )

    # Add independent flags to run one or more test functions (-1..-7 allowed together)
    parser.add_argument(
        "-1", "--getFileListBetweenNewAndTopHash", action="store_true", help="Run getFileListBetweenNewAndTopHash"
    )
    parser.add_argument(
        "-2",
        "--getFileListBetweenTopHashAndCurrentTime",
        action="store_true",
        help="Run getFileListBetweenTopHashAndCurrentTime",
    )
    parser.add_argument(
        "-3", "--getFileListBetweenTopHashAndStaged", action="store_true", help="Run getFileListBetweenTopHashAndStaged"
    )
    parser.add_argument(
        "-4", "--getFileListBetweenStagedAndMods", action="store_true", help="Run getFileListBetweenStagedAndMods"
    )
    parser.add_argument(
        "-5", "--getFileListBetweenNewAndStaged", action="store_true", help="Run getFileListBetweenNewAndStaged"
    )
    parser.add_argument(
        "-6", "--getFileListBetweenNewAndMods", action="store_true", help="Run getFileListBetweenNewAndMods"
    )
    parser.add_argument("-7", "--getHashListEntireRepo", action="store_true", help="Run getHashListEntireRepo")
    parser.add_argument("-8", "--getHashListStagedChanges", action="store_true", help="Run getHashListStagedChanges")
    parser.add_argument("-9", "--getHashListFromFileName", action="store_true", help="Run getHashListFromFileName")
    parser.add_argument("-a", "--getHashListNewChanges", action="store_true", help="Run getHashListNewChanges")
    parser.add_argument("-b", "--getHashListComplete", action="store_true", help="Run getHashListComplete")
    parser.add_argument("-c", "--getHashListSample", action="store_true", help="Run getHashListSample")
    parser.add_argument("-d", "--getHashListSamplePlusEnds", action="store_true", help="Run getHashListSamplePlusEnds")
    parser.add_argument(
        "-e",
        "--getFileListUntrackedAndIgnored",
        action="store_true",
        help="Run getFileListUntrackedAndIgnored",
    )
    parser.add_argument(
        "-f",
        "--getFileListSampledComparisons",
        action="store_true",
        help="Run getFileListSampledComparisons",
    )
    parser.add_argument("-A", "--all", action="store_true", help="Run all tests")
    parser.add_argument("-F", "--file", default="README.md", help="Filename for getHashListFromFileName when used")
    parser.add_argument(
        "path",
        nargs="+",
        help="One or more paths to the git repository to test.",
    )

    args = parser.parse_args()

    # If user requested an "up through" numeric run, set the matching
    # boolean flags so downstream selection logic runs the requested tests.
    if args.up_through is not None:
        try:
            # Interpret up-through as a base-36 digit/string so 'a'..'z' map to 10..35
            n = int(str(args.up_through), 36)
        except Exception as e:
            # No `TestRepo` instance available yet; log via module helper
            printException(e, "up-through parse failed (expected base36)")
            n = 0
        if n >= 1:
            args.getFileListBetweenNewAndTopHash = True
        if n >= 2:
            args.getFileListBetweenTopHashAndCurrentTime = True
        if n >= 3:
            args.getFileListBetweenTopHashAndStaged = True
        if n >= 4:
            args.getFileListBetweenStagedAndMods = True
        if n >= 5:
            args.getFileListBetweenNewAndStaged = True
        if n >= 6:
            args.getFileListBetweenNewAndMods = True
        if n >= 7:
            args.getHashListEntireRepo = True
        if n >= 8:
            args.getHashListStagedChanges = True
        if n >= 9:
            args.getHashListFromFileName = True
        if n >= 10:
            args.getHashListNewChanges = True
        if n >= 11:
            args.getHashListComplete = True
        if n >= 12:
            args.getHashListSample = True
        if n >= 13:
            args.getHashListSamplePlusEnds = True
        if n >= 14:
            args.getFileListUntrackedAndIgnored = True

    # Helper to run a single comparison and return True on success. Accept
    # a `TestRepo` instance so the helper can be defined once and reused.
    def run_one(test_repo, i: int, name: str, func_name: str, fname: str | None) -> bool:
        # Debug: report which test function is being invoked
        if test_repo.verbose > 1:
            print(f"DEBUG: run_one invoking {func_name} (display name: {name})")
        # Time each backend separately so we can report durations
        if fname is not None:
            t0 = time.perf_counter()
            l1 = getattr(test_repo, func_name)(fname, usePyGit2=True)
            t1 = time.perf_counter()
            l2 = getattr(test_repo, func_name)(fname, usePyGit2=False)
            t2 = time.perf_counter()
        else:
            t0 = time.perf_counter()
            l1 = getattr(test_repo, func_name)(usePyGit2=True)
            t1 = time.perf_counter()
            l2 = getattr(test_repo, func_name)(usePyGit2=False)
            t2 = time.perf_counter()
        dur_py = t1 - t0
        dur_cli = t2 - t1
        # Prefix the displayed test name with the enumeration and function name
        disp_name = f"{func_name}:{name}"
        if test_repo.verbose:
            print(f"TIMING: {disp_name} pygit2={dur_py:.3f}s git={dur_cli:.3f}s")
        return show_diffs(disp_name, l1, l2, args.top, args.raw, args.verbose, args.silent)

    allfuncs = [
        ("-1, File List New to Top Hash", "getFileListBetweenNewRepoAndTopHash", None),
        (
            "File List Between TopHash and Current Time",
            "getFileListBetweenTopHashAndCurrentTime",
            None,
        ),
        (
            "-2, File List Between TopHash and Current Time",
            "getFileListBetweenTopHashAndStaged",
            None,
        ),
        ("-3, File List Between Staged and Mods", "getFileListBetweenStagedAndMods", None),
        ("-4, File List New to Staged", "getFileListBetweenNewRepoAndStaged", None),
        ("-5, File List New to Mods", "getFileListBetweenNewRepoAndMods", None),
        ("-6, Hash List Entire Repo", "getHashListEntireRepo", None),
        ("-7, Hash List Staged Changes", "getHashListStagedChanges", None),
        (f"-8, Hash List From File {args.file}", "getHashListFromFileName", args.file),
        ("-9, Hash List New Changes", "getHashListNewChanges", None),
        ("-a, Hash List New Changes", "getHashListNewChanges", None),
        ("-b, Hash List Complete", "getHashListComplete", None),
        ("-c, Hash List Sample", "getHashListSample", None),
        ("-d, Hash List Sample Plus Ends", "getHashListSamplePlusEnds", None),
        ("-e, Untracked and Ignored files", "getFileListUntrackedAndIgnored", None),
    ]

    # Determine which tests to run. If -A/--all is set, run all tests.
    to_run: list[tuple[str, str, str | None]] = []
    if args.all:
        to_run = allfuncs
        sampled_flag = True
    else:
        # Append tests in the numeric/option order to match `allfuncs`:
        # 1,-2,-3,-4,-5,-6 then -7,-8,-9 then -a,-b,-c
        if args.getFileListBetweenNewAndTopHash:
            to_run.append(
                (
                    "-1, File List New to Top Hash",
                    "getFileListBetweenNewRepoAndTopHash",
                    None,
                )
            )
        if args.getFileListBetweenTopHashAndCurrentTime:
            to_run.append(
                (
                    "-2, File List Between TopHash and Current Time",
                    "getFileListBetweenTopHashAndCurrentTime",
                    None,
                )
            )
        if args.getFileListBetweenTopHashAndStaged:
            to_run.append(
                (
                    "-3, File List Between TopHash and Staged",
                    "getFileListBetweenTopHashAndStaged",
                    None,
                )
            )
        if args.getFileListBetweenStagedAndMods:
            to_run.append(
                (
                    "-4, File List Between Staged and Mods",
                    "getFileListBetweenStagedAndMods",
                    None,
                )
            )
        if args.getFileListBetweenNewAndStaged:
            to_run.append(
                (
                    "-5, File List New to Staged",
                    "getFileListBetweenNewRepoAndStaged",
                    None,
                )
            )
        if args.getFileListBetweenNewAndMods:
            to_run.append(("-6, File List New to Mods", "getFileListBetweenNewRepoAndMods", None))
        if args.getHashListEntireRepo:
            to_run.append(("-7, Hash List Entire Repo", "getHashListEntireRepo", None))
        if args.getHashListStagedChanges:
            to_run.append(("-8, Hash List Staged Changes", "getHashListStagedChanges", None))
        if args.getHashListFromFileName:
            to_run.append((f"-9, Hash List From File {args.file}", "getHashListFromFileName", args.file))
        if args.getHashListNewChanges:
            to_run.append(("-a, Hash List New Changes", "getHashListNewChanges", None))
        if args.getHashListComplete:
            to_run.append(("-b, Hash List Complete", "getHashListComplete", None))
        if args.getHashListSample:
            to_run.append(("-c, Hash List Sample", "getHashListSample", None))
        # Include sample-plus-ends (-d) then untracked/ignored (-e) in option order
        if args.getHashListSamplePlusEnds:
            to_run.append(("-d, Hash List Sample Plus Ends", "getHashListSamplePlusEnds", None))
        if args.getFileListUntrackedAndIgnored:
            to_run.append(("-e, Untracked and Ignored files", "getFileListUntrackedAndIgnored", None))
        # Sampled comparisons are run separately to allow independent reporting
        # and avoid mixing their output with the main test loop.

    # If no specific flags provided, default to running all tests
    if not to_run and not args.getFileListSampledComparisons and not args.getFileListBetweenNormalizedHashes:
        args.all = True
        to_run = allfuncs
        args.getFileListSampledComparisons = True

    total = 0
    passed = 0
    failed = 0

    for path in args.path:
        print(f"\n== Repository: {path} ==")
        test_repo = TestRepo(path, args.verbose, args.silent)

        for i, (name, func, fname) in enumerate(to_run, 1):
            total += 1
            try:
                ok = run_one(test_repo, i, name, func, fname)
            except Exception as e:
                # Use the enumerated index in the error context for clarity
                test_repo.printException(e, f"running -{i},{func}:{name} failed")
                ok = False
            if ok:
                passed += 1
            else:
                failed += 1

        # Process any explicit getFileListBetweenNormalizedHashes pairs supplied
        if args.getFileListBetweenNormalizedHashes:
            for pair in args.getFileListBetweenNormalizedHashes:
                try:
                    if not pair:
                        continue
                    parts = pair.split(",")
                    if len(parts) != 2:
                        print(f"Skipping invalid pair '{pair}'; expected format prev,curr")
                        continue
                    prev_hash = parts[0].strip()
                    curr_hash = parts[1].strip()
                    label = f"getFileListBetweenNormalizedHashes {prev_hash}->{curr_hash}"
                    total += 1
                    try:
                        l1 = test_repo.getFileListBetweenNormalizedHashes(prev_hash, curr_hash, True)
                        l2 = test_repo.getFileListBetweenNormalizedHashes(prev_hash, curr_hash, False)
                        ok = show_diffs(label, l1, l2, args.top, args.raw, args.verbose, args.silent)
                    except Exception as e:
                        test_repo.printException(e, f"invoking getFileListBetweenNormalizedHashes for {pair} failed")
                        ok = False
                    if ok:
                        passed += 1
                    else:
                        failed += 1
                except Exception as e:
                    test_repo.printException(e, f"processing getFileListBetweenNormalizedHashes option '{pair}' failed")

        # If requested, run sampled comparisons separately (outside the to_run loop)
        if args.getFileListSampledComparisons:
            print("\nRunning sampled pairwise comparisons (separate)...")
            try:
                # runFileListSampledComparisons returns (total, passed, failed)
                (t, p, f) = test_repo.runFileListSampledComparisons(args.top, args.raw)
                total += t
                passed += p
                failed += f
            except Exception as e:
                test_repo.printException(e, "running runFileListSampledComparisons failed")

    # Final summary
    print(f"\nTest summary: total={total} passed={passed} failed={failed}")


if __name__ == "__main__":
    main()
