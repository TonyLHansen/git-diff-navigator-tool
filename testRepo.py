#!/usr/bin/env python3

"""Harness around git CLI repository file listing methods."""

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
    """Test git CLI repository file listing methods."""

    STAGED_MESSAGE = "Staged changes"
    MODS_MESSAGE = "Unstaged modifications"
    # Pseudo-hash tokens used across diff dispatching
    NEWREPO = "NEWREPO"
    STAGED = "STAGED"
    MODS = "MODS"

    # BEGIN: __init__ v1
    def __init__(self, repoRoot: str, verbose: int = 0):
        self.repoRoot = repoRoot
        self.verbose = verbose
        # One-time per-process cache for git CLI command results
        self._cmd_cache = {}

    # END: __init__ v1


    

    # BEGIN: _deltas_to_results v1
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

    

    # BEGIN: getFileListBetweenNewRepoAndTopHash v1
    def getFileListBetweenNewRepoAndTopHash(self) -> list[str]:
        """Return a list of `(path, status)` for files present in HEAD.

        Status will be `committed` to indicate file is present in the
        given commit (HEAD).
        """
        # Delegate to the new initial->commit helper to avoid duplication
        return self.getFileListBetweenNewRepoAndHash("HEAD")

    # END: getFileListBetweenNewRepoAndTopHash v1

    # BEGIN: getFileListBetweenNormalizedHashes v1
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
                "getFileListBetweenNormalizedHashes: None is not a valid token; use TestRepo.NEWREPO for initial repository"
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

    # END: getFileListBetweenNormalizedHashes v1

    # BEGIN: getFileListBetweenTwoCommits v1
    def getFileListBetweenTwoCommits(self, prev_hash: str, curr_hash: str) -> list[tuple[str, str]]:
        """Direct commit->commit diff (both args expected to be commit-ish).

        Extracted helper containing the previous logic for diffing two commits.
        """
        # Use git CLI for commit->commit diffs
        return self._git_cli_name_status(["git", "diff", "--name-status", prev_hash, curr_hash])

    # END: getFileListBetweenTwoCommits v1

    # BEGIN: getFileListBetweenNewRepoAndHash v1
    def getFileListBetweenNewRepoAndHash(self, curr_hash: str) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between the beginning and `curr_hash`.

        Status values are the same as other diffs (added/modified/etc.).
        """
        # Git-CLI implementation with a one-time per-hash cache. The
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
    def getFileListBetweenNewRepoAndStaged(self) -> list[tuple[str, str]]:
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
    def getFileListBetweenNewRepoAndMods(self) -> list[tuple[str, str]]:
        """Specialized handler for initial (empty) -> working tree (mods) comparison.

        This implementation is git-CLI-only and mirrors `git diff --name-status`.
        """
        key = "getFileListBetweenNewRepoAndMods"
        return self._git_cli_getCachedFileList(key, ["git", "diff", "--name-status"])

    # END: getFileListBetweenNewRepoAndMods v1

    # BEGIN: getFileListBetweenTopHashAndCurrentTime v1
    def getFileListBetweenTopHashAndCurrentTime(self) -> list[str]:
        """Return a list of `(path, status)` for files changed between HEAD and working tree.

        Status will reflect the working-tree change type (modified/added/deleted).
        """
        # Delegate to the general handler to avoid duplicating logic
        return self.getFileListBetweenHashAndCurrentTime("HEAD")

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
    def getFileListBetweenHashAndCurrentTime(self, hash: str) -> list[tuple[str, str]]:
        """Return `(path,status)` for files changed between `hash` and working tree.

        Uses the git CLI plus a one-time cache via `_git_cli_getCachedFileList`.
        """
        key = f"getFileListBetweenHashAndCurrentTime:{hash}"
        return self._git_cli_getCachedFileList(key, ["git", "diff", "--name-status", hash])

    # END: getFileListBetweenHashAndCurrentTime v1

    # BEGIN: getFileListBetweenTopHashAndStaged v1
    def getFileListBetweenTopHashAndStaged(self) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between HEAD and staged index."""
        # Delegate to the generalized staged-vs-hash implementation to avoid duplication
        return self.getFileListBetweenHashAndStaged("HEAD")

    # END: getFileListBetweenTopHashAndStaged v1

    # BEGIN: getFileListBetweenHashAndStaged v1
    def getFileListBetweenHashAndStaged(self, hash: str) -> list[tuple[str, str]]:
        """Return `(path,status)` for files changed between `hash` and the staged index.

        Generalization of getFileListBetweenTopHashAndStaged for any commit-ish.
        """
        key = f"getFileListBetweenHashAndStaged:{hash}"
        return self._git_cli_getCachedFileList(key, ["git", "diff", "--name-status", "--cached", hash])

    # END: getFileListBetweenHashAndStaged v1

    # BEGIN: getFileListBetweenStagedAndMods v1
    # make git-only
    def getFileListBetweenStagedAndMods(self) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between staged index and working tree (mods)."""
        # Use git CLI to get the list of files; cache the results once per process
        key = "getFileListBetweenStagedAndMods"
        return self._git_cli_getCachedFileList(key, ["git", "diff", "--name-status"]) 

    # END: getFileListBetweenStagedAndMods v1

    # BEGIN: getFileListUntrackedAndIgnored v1
    # make git-only
    def getFileListUntrackedAndIgnored(self) -> list[tuple[str, str, str]]:
        """Return a sorted list of `(path, iso_mtime, status)` for files that are
        either untracked or ignored in the working tree.

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
    def getHashListComplete(self) -> list[tuple[str, str, str]]:
        """Return a combined list of commit hashes for staged, new, and entire repo."""
        new = self.getHashListNewChanges()
        staged = self.getHashListStagedChanges()
        entire = self.getHashListEntireRepo()
        combined = new + staged + entire
        return combined

    # END: getHashListComplete v1

    # BEGIN: getHashListSample v1
    def getHashListSample(self) -> list[tuple[str, str, str]]:
        """Return a sampled list of commit hashes for staged, new, and entire repo.
        in the order newest to oldest.
        """
        entire = self.getHashListEntireRepo()
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
    def getHashListSamplePlusEnds(self) -> list[tuple[str, str, str]]:
        """Return a sampled list of commit hashes for staged, new, and entire repo.

        Order: MODS, STAGED, sampled commits (newest->oldest), NEWREPO
        """
        sampleHashes: list[tuple[str, str, str]] = []

        # Put working-tree (MODS) first when present
        mods = self.getHashListNewChanges()
        if mods:
            sampleHashes += mods

        # Then staged marker
        staged = self.getHashListStagedChanges()
        if staged:
            sampleHashes += staged

        # Then the sampled commits (getHashListSample returns newest->oldest)
        normalHashes = self.getHashListSample()
        if normalHashes:
            sampleHashes += normalHashes

        # Place NEWREPO pseudo-entry last
        sampleHashes.append(("", self.NEWREPO, "Newly created repository"))
        return sampleHashes

    # END: getHashListSamplePlusEnds v1

    # runFileListSampledExercises moved to module-level function

    # BEGIN: getHashListEntireRepo v1
    def getHashListEntireRepo(self) -> list[tuple[str, str, str]]:
        """Return a list of all commit hashes in the repository."""
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
    def getHashListStagedChanges(self) -> list[tuple[str, str, str]]:
        """Return a list of commit hashes for staged changes."""
        # Use git CLI to detect staged files and return a STAGED pseudo-hash.
        key = f"getHashListStagedChanges:{self.index_mtime_iso()}"
        try:
            if key in self._cmd_cache:
                return self._cmd_cache[key]

            try:
                names_out = check_output(["git", "diff", "--cached", "--name-only"], cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                self._cmd_cache[key] = []
                return []

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

    # END: getHashListStagedChanges v1

    # BEGIN: getHashListNewChanges v1
    def getHashListNewChanges(self) -> list[tuple[str, str, str]]:
        """Return a list of commit hashes for new changes."""
        # Detect working-tree vs index differences via git CLI and return a
        # MODS pseudo-hash when there are modified files.
        try:
            try:
                names_out = check_output(["git", "diff", "--name-only"], cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []

            if not names_out:
                return []

            paths = [ln.strip() for ln in names_out.splitlines() if ln.strip()]
            if not paths:
                return []

            iso = self._paths_mtime_iso(paths)
            key = f"getHashListNewChanges:{iso}"
            if key in self._cmd_cache:
                return self._cmd_cache[key]
            res = [(iso, "MODS", self.MODS_MESSAGE)]
            self._cmd_cache[key] = res
            return res
        except Exception as e:
            self.printException(e, "getHashListNewChanges: failure")
            return []

    # END: getHashListNewChanges v1

    # BEGIN: getHashListFromFileName v1
    def getHashListFromFileName(self, file_name: str) -> list[tuple[str, str, str]]:
        """Return a list of commit hashes that modified the given file.

        Uses the git CLI (`git log` + `git status`) with a one-time cache per
        `file_name`. The previous walk/diff implementation has been
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


def runFileListSampledExercises(test_repo: TestRepo, raw: bool, limit: int) -> int:
    """Module-level exerciser for `getFileListBetweenNormalizedHashes`.

    Calls the dispatch logic for all sampled token pairs and prints a
    bounded sample of results. Returns the total number of exercised
    token pairs.
    """
    sample = test_repo.getHashListSamplePlusEnds()
    tokens: list = [x[1] for x in sample]
    tokens.reverse()
    print(f"Tokens (newest to oldest)={tokens}")

    total = 0
    for i in range(len(tokens)):
        for j in range(i + 1, len(tokens)):
            a = tokens[i]
            b = tokens[j]
            total += 1
            try:
                res = test_repo.getFileListBetweenNormalizedHashes(a, b)
                print(f"\nEXERCISE: {a}->{b} returned {len(res)} entries:")
                for it in res[:limit]:
                    print(repr(it))
            except Exception as e:
                test_repo.printException(e, f"runFileListSampledExercises: handler failed for {a}->{b}")

    return total


def main():
    """Main function to run the tests."""
    parser = argparse.ArgumentParser(prog="gitdiffnavtool.py", description=__doc__)
    
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

    # Make --silent and --limit mutually exclusive: you may either silence output
    # (which forces no printed entries) or specify a numeric --limit to print.
    mux = parser.add_mutually_exclusive_group()
    mux.add_argument(
        "-S",
        "--silent",
        action="store_true",
        default=False,
        help="Silence summary printouts such as 'No differences found in ## lines of output' (mutually exclusive with --limit)",
    )

    mux.add_argument(
        "-L",
        "--limit",
        type=int,
        default=sys.maxsize,
        help="Maximum number of entries to print when showing results (default: unlimited). Mutually exclusive with --silent.",
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

    # If `--silent` is requested, force `--limit` to 0 so no entries are printed.
    if args.silent:
        args.limit = 0


    # Helper to run a single exercise and return True on success. Accept
    # a `TestRepo` instance so the helper can be defined once and reused.
    def run_one(test_repo, i: int, name: str, func_name: str, fname: str | None, limit: int) -> bool:
        # Debug: report which test function is being invoked
        if test_repo.verbose > 1:
            print(f"DEBUG: run_one invoking {func_name} (display name: {name})")
        # Run the test function once (git-CLI implementation) and print results.
        try:
            if fname is not None:
                t0 = time.perf_counter()
                res = getattr(test_repo, func_name)(fname)
                t1 = time.perf_counter()
            else:
                t0 = time.perf_counter()
                res = getattr(test_repo, func_name)()
                t1 = time.perf_counter()
            dur = t1 - t0
            print(f"RUN: {func_name} {name} returned {len(res) if hasattr(res, '__len__') else '1'} entries (t={dur:.3f}s)")
            # Pretty-print up to `limit` entries
            try:
                if isinstance(res, list):
                    for it in res[:limit]:
                        print(repr(it))
                else:
                    print(repr(res))
            except Exception as e:
                test_repo.printException(e, "run_one: pretty-printing result failed")
                print(repr(res))
            return True
        except Exception as e:
            test_repo.printException(e, f"run_one invocation of {func_name} failed")
            return False

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

    # If no specific flags provided, default to running all exercises
    if not to_run and not args.getFileListSampledComparisons and not args.getFileListBetweenNormalizedHashes:
        args.all = True
        to_run = allfuncs
        args.getFileListSampledComparisons = True

    total_exercises = 0

    for path in args.path:
        print(f"\n== Repository: {path} ==")
        test_repo = TestRepo(path, args.verbose)

        for i, (name, func, fname) in enumerate(to_run, 1):
            total_exercises += 1
            try:
                _ = run_one(test_repo, i, name, func, fname, args.limit)
            except Exception as e:
                # Use the enumerated index in the error context for clarity
                test_repo.printException(e, f"running -{i},{func}:{name} failed")

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
                    total_exercises += 1
                    try:
                        l = test_repo.getFileListBetweenNormalizedHashes(prev_hash, curr_hash)
                        print(f"{label} result ({len(l)} entries):")
                        for it in l[:args.limit]:
                            print(repr(it))
                        ok = True
                    except Exception as e:
                        test_repo.printException(e, f"invoking getFileListBetweenNormalizedHashes for {pair} failed")
                        ok = False
                    # We treat these as exercises; successes/failures are logged but not tallied.
                except Exception as e:
                    test_repo.printException(e, f"processing getFileListBetweenNormalizedHashes option '{pair}' failed")

        # If requested, run sampled comparisons separately (outside the to_run loop)
        if args.getFileListSampledComparisons:
            print("\nRunning sampled pairwise comparisons (separate)...")
            try:
                # runFileListSampledExercises returns total exercises run
                t = runFileListSampledExercises(test_repo, args.raw, args.limit)
                total_exercises += t
            except Exception as e:
                test_repo.printException(e, "running runFileListSampledExercises failed")

    # Final summary
    print(f"\nExercise summary: total_exercises={total_exercises}")


if __name__ == "__main__":
    main()
