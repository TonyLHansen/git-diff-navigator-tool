#!/usr/bin/env python3

"""Test pygit2 and git CLI repository file listing methods."""

import argparse
import difflib
import sys
import traceback
import os

import pygit2
import codecs
from datetime import datetime, timezone
from subprocess import check_output, CalledProcessError


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
    def __init__(self, repoRoot: str, verbose: int = 0):
        self.repoRoot = repoRoot
        self.pygit2_repo = pygit2.Repository(self.repoRoot)
        self.verbose = verbose

    # END: __init__ v1

    # BEGIN: _resolve_tree v1
    def _resolve_tree(self, obj):
        """Resolve a pygit2 object (Commit/Tag/Tree) to a Tree or None."""
        try:
            if obj is None:
                return None
            if isinstance(obj, pygit2.Tree):
                return obj
            if isinstance(obj, pygit2.Commit):
                return obj.tree
            if isinstance(obj, pygit2.Tag):
                # Peel annotated tag to its target via the repository
                try:
                    target = self.pygit2_repo.get(obj.target)
                    if isinstance(target, pygit2.Commit):
                        return target.tree
                    if isinstance(target, pygit2.Tree):
                        return target
                except Exception as e:
                    self.printException(e, "_resolve_tree: tag peel failed")
                    return None
        except Exception as e:
            self.printException(e, "_resolve_tree: unexpected error")
            return None
        return None

    # END: _resolve_tree v1

    # BEGIN: _format_commit_entry v1
    def _format_commit_entry(self, repo, commit_or_hash) -> tuple[str, str, str]:
        """Format a commit-like entry as "ISO HASH [subject]" when possible.

        If `commit_or_hash` can be resolved to a pygit2.Commit, include
        the commit timestamp and the top line of the commit message. If it
        cannot be resolved to a commit (e.g. blob/tree), return a stable
        hash string.
        """
        try:
            # If already a commit object, use it directly
            if isinstance(commit_or_hash, pygit2.Commit):
                c = commit_or_hash
            else:
                # Try to resolve via repo.get(); if that fails return raw tuple
                try:
                    c = repo.get(commit_or_hash)
                except Exception as e:
                    self.printException(e, "_format_commit_entry: repo.get failed")
                    return ("", str(commit_or_hash), "")

            if isinstance(c, pygit2.Commit):
                try:
                    ts = int(getattr(c, "time", None) or getattr(c, "commit_time", None) or 0)
                except Exception as e:
                    self.printException(e, "_format_commit_entry: parsing timestamp failed")
                    ts = 0
                try:
                    msg = getattr(c, "message", None) or ""
                except Exception as e:
                    self.printException(e, "_format_commit_entry: reading message failed")
                    msg = ""
                subject = msg.splitlines()[0] if msg else ""
                try:
                    iso = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                except Exception as e:
                    self.printException(e, "_format_commit_entry: formatting timestamp failed")
                    iso = "1970-01-01T00:00:00"
                ch = getattr(c, "hex", None)
                if not ch:
                    cid = getattr(c, "id", None)
                    ch = getattr(cid, "hex", None) or str(cid) if cid is not None else ""
                return (iso, ch, subject)

            # Not a commit object (blob/tree/etc) — return hash-like tuple
            ch = getattr(c, "hex", None)
            if not ch:
                cid = getattr(c, "id", None)
                ch = getattr(cid, "hex", None) or str(cid) if cid is not None else str(commit_or_hash)
            return ("", ch, "")
        except Exception as e:
            # Fallback to raw tuple representation on error
            self.printException(e, "_format_commit_entry failed")
            return ("", str(commit_or_hash), "")

    # END: _format_commit_entry v1

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

    # BEGIN: _decode_git_quoted_path v1
    def _decode_git_quoted_path(self, rel: str) -> str:
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
                tmp = codecs.decode(raw, 'unicode_escape')
                b = tmp.encode('latin-1', 'surrogatepass')
                try:
                    return b.decode('utf-8')
                except UnicodeDecodeError:
                    return b.decode('latin-1')
            except Exception:
                return raw
        return rel
    # END: _decode_git_quoted_path v1

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

    # BEGIN: _delta_status_to_str v1
    def _delta_status_to_str(self, status_code) -> str:
        """Map pygit2 delta status codes to human-friendly status strings."""
        try:
            if status_code == pygit2.GIT_DELTA_ADDED:
                return "added"
            if status_code == pygit2.GIT_DELTA_MODIFIED:
                return "modified"
            if status_code == pygit2.GIT_DELTA_DELETED:
                return "deleted"
            if status_code == pygit2.GIT_DELTA_RENAMED:
                return "renamed"
            if status_code == pygit2.GIT_DELTA_COPIED:
                return "copied"
        except Exception as e:
            self.printException(e, "_delta_status_to_str: mapping failed")
        return "modified"

    # END: _delta_status_to_str v1

    # BEGIN: _git_name_status_to_str v1
    def _git_name_status_to_str(self, code: str) -> str:
        """Map `git --name-status` status codes to human-friendly strings.

        Handles codes like 'A','M','D','C' and rename codes that start with 'R'.
        """
        try:
            if not code:
                return "modified"
            if code.startswith("R"):
                return "renamed"
            return {"A": "added", "M": "modified", "D": "deleted", "C": "copied"}.get(code, "modified")
        except Exception as e:
            self.printException(e, "_git_name_status_to_str: mapping failed")
            return "modified"

    # END: _git_name_status_to_str v1

    # BEGIN: _parse_git_name_status_line v1
    def _parse_git_name_status_line(self, line: str) -> tuple[str, str]:
        """Parse a single `git --name-status` line and return (path, status).

        Handles rename lines like `R087\told\tnew` by selecting the new
        path (last column). Uses `_git_name_status_to_str` to determine the
        canonical status string.
        """
        parts = line.split("\t")
        code = parts[0].strip() if parts else ""
        if code.startswith("R"):
            path = parts[-1].strip() if len(parts) > 1 else ""
        else:
            path = parts[1].strip() if len(parts) > 1 else ""
        status = self._git_name_status_to_str(code)
        return (path, status)

    # END: _parse_git_name_status_line v1

    # BEGIN: _empty_tree_for_repo v1
    def _empty_tree_for_repo(self, repo) -> "pygit2.Tree | None":
        """Construct and return an empty tree object for `repo`, or None on failure.

        Centralizes `TreeBuilder` usage to avoid repeated try/except blocks.
        """
        try:
            tb = repo.TreeBuilder()
            oid = tb.write()
            return repo.get(oid)
        except Exception as e:
            self.printException(e, "_empty_tree_for_repo: TreeBuilder failed")
            return None

    # END: _empty_tree_for_repo v1

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
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                a = self.pygit2_repo.revparse_single(prev_hash)
                b = self.pygit2_repo.revparse_single(curr_hash)
            except Exception as ex:
                self.printException(ex, "getFileListBetweenTwoCommits: revparse failed")
                return []
            try:
                a = self._resolve_tree(a)
                b = self._resolve_tree(b)
                if a is None:
                    a = self._empty_tree_for_repo(self.pygit2_repo)
                if b is None:
                    b = self._empty_tree_for_repo(self.pygit2_repo)
                diff = self.pygit2_repo.diff(a, b)
            except Exception as e:
                self.printException(e, "getFileListBetweenTwoCommits: pygit2 diff failed")
                return []
            results: list[tuple[str, str]] = []
            for delta in diff.deltas:
                path = getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None)
                status = self._delta_status_to_str(getattr(delta, "status", None))
                if path:
                    results.append((path, status))
            # stable sort by path
            results.sort(key=lambda x: x[0])
            return results

        else:
            # git CLI fallback when not using pygit2 (commit -> commit)
            try:
                output = check_output(
                    ["git", "diff", "--name-status", prev_hash, curr_hash],
                    cwd=self.repoRoot,
                    text=True,
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []

            results: list[tuple[str, str]] = []
            for line in output.splitlines():
                if not line:
                    continue
                path, status = self._parse_git_name_status_line(line)
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            return results

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
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of files
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                c = self.pygit2_repo.revparse_single(curr_hash)
            except Exception as e:
                self.printException(e, "revparse_single failed for curr_hash")
                return []
            cur_tree = self._resolve_tree(c)
            if cur_tree is None:
                self.printException(ValueError("could not resolve curr_tree"), "_resolve_tree failed")
                return []
            # Construct an empty tree and diff empty->cur to avoid passing None
            try:
                empty_tree = self._empty_tree_for_repo(self.pygit2_repo)
                if empty_tree is None:
                    self.printException(
                        RuntimeError("failed to construct empty tree"),
                        "getFileListBetweenNewRepoAndHash: empty tree construction failed",
                    )
                    return []
                diff = self.pygit2_repo.diff(empty_tree, cur_tree)
            except Exception as e:
                self.printException(e, "pygit2 initial-commit diff failed")
                return []
            results: list[tuple[str, str]] = []
            for delta in diff.deltas:
                path = getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None)
                status = self._delta_status_to_str(getattr(delta, "status", None))
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            return results

        else:
            # git CLI fallback when not using pygit2 for initial->commit
            try:
                # List all files in the commit (treat as 'added' vs empty repo)
                output = check_output(["git", "ls-tree", "-r", "--name-only", curr_hash], cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []
            results: list[tuple[str, str]] = []
            for line in output.splitlines():
                ln = line.strip()
                if not ln:
                    continue
                # All files present in the commit are 'added' relative to empty repo
                results.append((ln, "added"))
            results.sort(key=lambda x: x[0])
            return results

    # END: getFileListBetweenNewRepoAndHash v1

    # BEGIN: getFileListBetweenNewRepoAndStaged v1
    def getFileListBetweenNewRepoAndStaged(self, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return file list for the initial (empty) tree -> staged index comparison.

        This is the specialized handler for the `prev is None and curr == STAGED`
        case so `getFileListBetweenNormalizedHashes` can remain a dispatcher.
        """
        ST = "STAGED"
        if usePyGit2:
            try:
                repo = self.pygit2_repo
                # Build the index tree
                idx_tree_oid = repo.index.write_tree()
                idx_tree = repo.get(idx_tree_oid)

                # Prefer comparing HEAD -> index (git --cached semantics).
                head_tree = None
                try:
                    head_obj = repo.revparse_single("HEAD")
                    head_tree = self._resolve_tree(head_obj)
                except Exception as e:
                    self.printException(e, "getFileListBetweenNewRepoAndStaged: HEAD revparse/resolve failed")
                    head_tree = None

                # If HEAD resolved to a tree, diff HEAD vs index; otherwise
                # fall back to empty->index semantics for repositories without HEAD.
                if head_tree is not None:
                    diff = repo.diff(head_tree, idx_tree)
                else:
                    empty = self._empty_tree_for_repo(repo)
                    if empty is None:
                        raise RuntimeError("failed to construct empty tree")
                    diff = repo.diff(empty, idx_tree)
                results = []
                for delta in diff.deltas:
                    path = getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None)
                    status = self._delta_status_to_str(getattr(delta, "status", None))
                    if path:
                        results.append((path, status))
                results.sort(key=lambda x: x[0])
                return results
            except Exception as e:
                self.printException(e, "getFileListBetweenNewRepoAndStaged: pygit2 diff failed")
                return []

        # git CLI fallback when not using pygit2
        try:
            output = check_output(["git", "diff", "--name-status", "--cached"], cwd=self.repoRoot, text=True)
        except CalledProcessError as e:
            self.printException(e, "git command failed")
            return []
        res = []
        for line in output.splitlines():
            if not line:
                continue
            path, status = self._parse_git_name_status_line(line)
            if path:
                res.append((path, status))
        res.sort(key=lambda x: x[0])
        return res

    # END: getFileListBetweenNewRepoAndStaged v1

    # BEGIN: getFileListBetweenNewRepoAndMods v1
    def getFileListBetweenNewRepoAndMods(self, usePyGit2: bool) -> list[tuple[str, str]]:
        """Specialized handler for initial (empty) -> working tree (mods) comparison.

        Preserve the original behavior: use `git diff --name-status` to match
        git CLI semantics for the initial->working-tree comparison.
        """
        if usePyGit2:
            try:
                repo = self.pygit2_repo
                # Construct an explicit empty tree to represent the "new repo"
                empty = self._empty_tree_for_repo(repo)
                if empty is None:
                    self.printException(
                        RuntimeError("failed to construct empty tree"),
                        "getFileListBetweenNewRepoAndMods: empty tree construction failed",
                    )
                    return []

                # Build a mapping of working-tree files -> content bytes
                work_files: dict[str, bytes] = {}
                for root, dirs, files in os.walk(self.repoRoot):
                    # Skip .git directory
                    if ".git" in root.split(os.sep):
                        continue
                    for fname in files:
                        fp = os.path.join(root, fname)
                        try:
                            rel = os.path.relpath(fp, self.repoRoot)
                        except Exception as e:
                            self.printException(e, "getFileListBetweenNewRepoAndMods: relpath failed")
                            continue
                        if rel.startswith(".git"):
                            continue
                        try:
                            with open(fp, "rb") as fh:
                                work_files[rel] = fh.read()
                        except Exception as e:
                            self.printException(e, "getFileListBetweenNewRepoAndMods: reading file failed")
                            continue

                results: list[tuple[str, str]] = []
                # For initial->mods we want to mirror `git diff` (worktree vs index):
                # include only files that show a working-tree change (not untracked).
                wt_mask = 0
                try:
                    wt_mask = (
                        getattr(pygit2, "GIT_STATUS_WT_MODIFIED", 0)
                        | getattr(pygit2, "GIT_STATUS_WT_DELETED", 0)
                        | getattr(pygit2, "GIT_STATUS_WT_TYPECHANGE", 0)
                        | getattr(pygit2, "GIT_STATUS_WT_RENAMED", 0)
                    )
                except Exception as e:
                    self.printException(e, "getFileListBetweenNewRepoAndMods: building wt_mask failed")
                    wt_mask = 0

                for path in sorted(work_files.keys()):
                    try:
                        st = repo.status_file(path)
                    except Exception as e:
                        self.printException(e, "getFileListBetweenNewRepoAndMods: status_file failed")
                        continue

                    # Skip untracked files (WT_NEW) to match git CLI behavior
                    if st & getattr(pygit2, "GIT_STATUS_WT_NEW", 0):
                        continue

                    # Include only working-tree changes
                    if wt_mask and not (st & wt_mask):
                        continue

                    # Map working-tree status to human-friendly string
                    if st & getattr(pygit2, "GIT_STATUS_WT_DELETED", 0):
                        s = "deleted"
                    elif st & getattr(pygit2, "GIT_STATUS_WT_MODIFIED", 0):
                        s = "modified"
                    elif st & getattr(pygit2, "GIT_STATUS_WT_RENAMED", 0):
                        s = "renamed"
                    elif st & getattr(pygit2, "GIT_STATUS_WT_TYPECHANGE", 0):
                        s = "modified"
                    else:
                        s = "modified"

                    results.append((path, s))
                return results
            except Exception as e:
                self.printException(e, "getFileListBetweenNewRepoAndMods: pygit2 empty->worktree failed")
                return []

        # git CLI fallback when not using pygit2
        try:
            output = check_output(["git", "diff", "--name-status"], cwd=self.repoRoot, text=True)
        except CalledProcessError as e:
            self.printException(e, "git command failed")
            return []
        res = []
        for line in output.splitlines():
            if not line:
                continue
            path, status = self._parse_git_name_status_line(line)
            if path:
                res.append((path, status))
        res.sort(key=lambda x: x[0])
        return res

    # END: getFileListBetweenNewRepoAndMods v1

    # BEGIN: getFileListBetweenTopHashAndCurrentTime v1
    def getFileListBetweenTopHashAndCurrentTime(self, usePyGit2: bool) -> list[str]:
        """Return a list of `(path, status)` for files changed between HEAD and working tree.

        Status will reflect the working-tree change type (modified/added/deleted).
        """
        # Delegate to the general handler to avoid duplicating logic
        return self.getFileListBetweenHashAndCurrentTime("HEAD", usePyGit2)

    # END: getFileListBetweenTopHashAndCurrentTime v1

    # BEGIN: getFileListBetweenHashAndCurrentTime v1
    def getFileListBetweenHashAndCurrentTime(self, hash: str, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return `(path,status)` for files changed between `hash` and working tree.

        Generalization of getFileListBetweenTopHashAndCurrentTime for any commit-ish.
        """
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                commit = self.pygit2_repo.revparse_single(hash)
            except Exception as e:
                self.printException(e, "revparse_single hash failed")
                return []
            # Resolve the commit tree and compare it to the working directory
            # by inspecting file contents. This avoids passing `None` to
            # `pygit2.diff` which can raise ValueError in some environments.
            cur_tree = self._resolve_tree(commit)
            if cur_tree is None:
                self.printException(ValueError("could not resolve tree for hash"), "_resolve_tree failed")
                return []

            repo = self.pygit2_repo

            # Try libgit2/pygit2 diff against working directory directly first.
            try:
                diff = repo.diff(cur_tree, None)
                results: list[tuple[str, str]] = []
                for delta in diff.deltas:
                    path = getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None)
                    status = self._delta_status_to_str(getattr(delta, "status", None))
                    if path:
                        results.append((path, status))
                results.sort(key=lambda x: x[0])
                return results
            except Exception as e:
                self.printException(
                    e,
                    "getFileListBetweenHashAndCurrentTime: pygit2 tree->workdir diff failed, falling back to manual compare",
                )

            # Build a mapping of paths -> blob OIDs for the commit tree
            commit_files: dict[str, str] = {}

            def walk_tree(tree, prefix=""):
                count = 0
                for entry in tree:
                    p = os.path.join(prefix, entry.name) if prefix else entry.name
                    try:
                        # Debug small sample of entries
                        if count < 20:
                            if self.verbose:
                                print(f"DEBUG:tree entry name={entry.name} type={entry.type}")
                        count += 1
                        # Resolve oid/id attribute in a version-tolerant way
                        oid = getattr(entry, "oid", None) or getattr(entry, "id", None)
                        if entry.type == 2:  # tree
                            if oid is None:
                                raise RuntimeError("tree entry missing oid/id")
                            t = repo.get(oid)
                            walk_tree(t, p)
                        elif entry.type == 3:  # blob
                            try:
                                commit_files[p] = str(oid)
                                if len(commit_files) < 50:
                                    if self.verbose:
                                        print(f"DEBUG:added commit_file {p} -> {oid}")
                            except Exception as ex:
                                self.printException(
                                    ex, f"getFileListBetweenHashAndCurrentTime: failed to add commit_file {p}"
                                )
                    except Exception as e:
                        self.printException(e, "getFileListBetweenHashAndCurrentTime: tree entry handling failed")
                        continue

            try:
                # Debug constants
                if self.verbose:
                    print(
                        f"DEBUG:pygit2.GIT_OBJ_TREE={getattr(pygit2,'GIT_OBJ_TREE',None)} GIT_OBJ_BLOB={getattr(pygit2,'GIT_OBJ_BLOB',None)}"
                    )
                walk_tree(cur_tree)
            except Exception as e:
                self.printException(e, "getFileListBetweenHashAndCurrentTime: walking tree failed")
                return []

            # Debugging: small summary of discovered commit/work file counts
            try:
                if self.verbose:
                    print(f"DEBUG:getFileListBetweenHashAndCurrentTime commit_files={len(commit_files)}")
                    try:
                        print(f"DEBUG:cur_tree_type={type(cur_tree)}")
                        print(f"DEBUG:cur_tree_len={len(cur_tree)}")
                    except Exception as e:
                        self.printException(e, "getFileListBetweenHashAndCurrentTime: debug printing failed")
                    if len(commit_files) < 10:
                        print(f"DEBUG:commit sample={list(commit_files.keys())[:20]}")
            except Exception as e:
                self.printException(e, "getFileListBetweenHashAndCurrentTime: debug summary failed")

            # Build a mapping of working-tree files -> content bytes
            work_files: dict[str, bytes] = {}
            for root, dirs, files in os.walk(self.repoRoot):
                # Skip .git directory
                if ".git" in root.split(os.sep):
                    continue
                for fname in files:
                    fp = os.path.join(root, fname)
                    # Skip files outside repository worktree
                    try:
                        rel = os.path.relpath(fp, self.repoRoot)
                    except Exception as e:
                        self.printException(e, "getFileListBetweenHashAndCurrentTime: relpath failed")
                        continue
                    if rel.startswith(".git"):
                        continue
                    try:
                        with open(fp, "rb") as fh:
                            work_files[rel] = fh.read()
                    except Exception as e:
                        self.printException(e, "getFileListBetweenHashAndCurrentTime: reading work file failed")
                        continue

            results: list[tuple[str, str]] = []
            # Compare union of paths
            all_paths = set(commit_files.keys()) | set(work_files.keys())
            for path in sorted(all_paths):
                in_commit = path in commit_files
                in_work = path in work_files
                if in_commit and not in_work:
                    results.append((path, "deleted"))
                elif not in_commit and in_work:
                    # Exclude untracked files (WT_NEW) from being reported as
                    # 'added' — this matches `git diff <commit>` behavior.
                    try:
                        st = repo.status_file(path)
                        if st & pygit2.GIT_STATUS_WT_NEW:
                            # skip untracked
                            continue
                    except Exception as e:
                        self.printException(e, "getFileListBetweenHashAndCurrentTime: status_file failed")
                    results.append((path, "added"))
                else:
                    # Present in both: compare blob content
                    try:
                        blob = repo.get(commit_files[path])
                        commit_data = blob.data if hasattr(blob, "data") else blob.read_raw()
                    except Exception as e:
                        self.printException(e, "getFileListBetweenHashAndCurrentTime: repo.get blob failed")
                        commit_data = None
                    work_data = work_files.get(path)
                    if commit_data is None:
                        # If we can't read commit blob, conservatively mark modified
                        results.append((path, "modified"))
                    else:
                        if work_data != commit_data:
                            results.append((path, "modified"))

            return results

        else:
            # Use git CLI to get the list of files
            try:
                output = check_output(
                    ["git", "diff", "--name-status", hash],
                    cwd=self.repoRoot,
                    text=True,
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []
            results: list[tuple[str, str]] = []
            for line in output.splitlines():
                if not line:
                    continue
                # Parse git --name-status line (handles rename/new-path selection)
                path, status = self._parse_git_name_status_line(line)
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            return results

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
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                commit = self.pygit2_repo.revparse_single(hash)
            except Exception as e:
                self.printException(e, "revparse_single hash failed")
                return []
            head_tree = self._resolve_tree(commit)
            if head_tree is None:
                self.printException(ValueError("could not resolve tree for hash"), "_resolve_tree failed")
                return []
            try:
                idx_tree_oid = self.pygit2_repo.index.write_tree()
                idx_tree = self.pygit2_repo.get(idx_tree_oid)
                diff = self.pygit2_repo.diff(head_tree, idx_tree)
            except Exception as e:
                self.printException(e, "pygit2 staged-vs-hash diff failed")
                return []
            results: list[tuple[str, str]] = []
            for delta in diff.deltas:
                path = getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None)
                status = self._delta_status_to_str(getattr(delta, "status", None))
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            return results

        else:
            # Use git CLI for staged-vs-HEAD list
            try:
                output = check_output(
                    ["git", "diff", "--name-status", "--cached", hash],
                    cwd=self.repoRoot,
                    text=True,
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []
            results: list[tuple[str, str]] = []
            for line in output.splitlines():
                if not line:
                    continue
                # Parse git --name-status line (handles rename/new-path selection)
                path, status = self._parse_git_name_status_line(line)
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            return results

    # END: getFileListBetweenHashAndStaged v1

    # BEGIN: getFileListBetweenStagedAndMods v1
    def getFileListBetweenStagedAndMods(self, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between staged index and working tree (mods)."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of files
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                # Write the index to a tree and diff index-tree -> working tree (using empty tree substitution)
                idx_tree_oid = self.pygit2_repo.index.write_tree()
                idx_tree = self.pygit2_repo.get(idx_tree_oid)
                # Prefer diff against the working directory (None) when possible.
                try:
                    diff = self.pygit2_repo.diff(idx_tree, None)
                except Exception as e:
                    self.printException(e, "getFileListBetweenStagedAndMods: repo.diff(idx_tree, None) failed, falling back to empty tree")
                    # Fall back to explicit empty-tree if libgit2 build rejects None
                    empty_tree = self._empty_tree_for_repo(self.pygit2_repo)
                    if empty_tree is None:
                        self.printException(
                            RuntimeError("failed to construct empty tree"),
                            "getFileListBetweenStagedAndMods: empty tree construction failed",
                        )
                        return []
                    diff = self.pygit2_repo.diff(idx_tree, empty_tree)
            except Exception as e:
                self.printException(e, "pygit2 staged->working diff failed")
                return []
            results: list[tuple[str, str]] = []
            for delta in diff.deltas:
                path = getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None)
                status = self._delta_status_to_str(getattr(delta, "status", None))
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            return results

        else:
            # Use git CLI to get the list of files
            try:
                output = check_output(
                    ["git", "diff", "--name-status"],
                    cwd=self.repoRoot,
                    text=True,
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []
            results: list[tuple[str, str]] = []
            for line in output.splitlines():
                if not line:
                    continue
                # Parse git --name-status line (handles rename/new-path selection)
                path, status = self._parse_git_name_status_line(line)
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            return results

    # END: getFileListBetweenStagedAndMods v1

    # BEGIN: getFileListUntrackedAndIgnored v1
    def getFileListUntrackedAndIgnored(self, usePyGit2: bool) -> list[tuple[str, str, str]]:
        """Return a sorted list of `(path, iso_mtime, status)` for files that are
        either untracked or ignored in the working tree.

        - `status` is one of: `untracked`, `ignored`.
        - `iso_mtime` is produced from the filesystem mtime via `_epoch_to_iso`.

        Prefer `pygit2` when `usePyGit2` is True; otherwise fall back to `git ls-files`.
        """
        results: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        try:
            if usePyGit2:
                if not pygit2:
                    raise RuntimeError("pygit2 is not available")
                repo = self.pygit2_repo
                for root, dirs, files in os.walk(self.repoRoot):
                    # Skip .git directory contents
                    if ".git" in root.split(os.sep):
                        continue
                    for fname in files:
                        fp = os.path.join(root, fname)
                        try:
                            rel = os.path.relpath(fp, self.repoRoot)
                        except Exception as e:
                            self.printException(e, "getFileListUntrackedAndIgnored: relpath failed")
                            continue
                        if rel.startswith(".git"):
                            continue
                        try:
                            st = repo.status_file(rel)
                        except Exception as e:
                            self.printException(e, "getFileListUntrackedAndIgnored: status_file failed")
                            continue

                        # Detect untracked and ignored states
                        if st & getattr(pygit2, "GIT_STATUS_WT_NEW", 0):
                            status = "untracked"
                        elif st & getattr(pygit2, "GIT_STATUS_IGNORED", 0):
                            status = "ignored"
                        else:
                            continue

                        if rel in seen:
                            continue
                        seen.add(rel)
                        try:
                            if os.path.islink(fp):
                                # explicitly use lstat for symlink's own mtime
                                mtime = os.lstat(fp).st_mtime
                            else:
                                mtime = os.path.getmtime(fp)
                        except FileNotFoundError:
                            # file disappeared between listing and stat; skip it
                            continue
                        except Exception as e:
                            # fallback: try lstat in case getmtime failed for other reasons
                            try:
                                mtime = os.lstat(fp).st_mtime
                            except FileNotFoundError:
                                # target not present; skip
                                continue
                            except Exception:
                                self.printException(e, "getFileListUntrackedAndIgnored: getting mtime failed")
                                mtime = 0
                        iso = self._epoch_to_iso(mtime)
                        results.append((rel, iso, status))

                results.sort(key=lambda x: x[0])
                return results

            else:
                # git CLI fallback: use `git ls-files` to list untracked and ignored
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
                    # git may emit quoted/escaped paths for special chars; decode them
                    rel = self._decode_git_quoted_path(rel)
                    if not rel:
                        continue
                    if rel in seen:
                        continue
                    seen.add(rel)
                    fp = os.path.join(self.repoRoot, rel)
                    try:
                        if os.path.islink(fp):
                            mtime = os.lstat(fp).st_mtime
                        else:
                            mtime = os.path.getmtime(fp)
                    except FileNotFoundError:
                        # file vanished; skip adding
                        continue
                    except Exception as e:
                        try:
                            mtime = os.lstat(fp).st_mtime
                        except FileNotFoundError:
                            continue
                        except Exception:
                            self.printException(e, "getFileListUntrackedAndIgnored: mtime failed for untracked")
                            mtime = 0
                    iso = self._epoch_to_iso(mtime)
                    results.append((rel, iso, "untracked"))

                for line in ignored_out.splitlines():
                    rel = line.strip()
                    # git may emit quoted/escaped paths for special chars; decode them
                    rel = self._decode_git_quoted_path(rel)
                    if not rel or rel in seen:
                        continue
                    seen.add(rel)
                    fp = os.path.join(self.repoRoot, rel)
                    try:
                        if os.path.islink(fp):
                            mtime = os.lstat(fp).st_mtime
                        else:
                            mtime = os.path.getmtime(fp)
                    except FileNotFoundError:
                        # file vanished; skip adding
                        continue
                    except Exception as e:
                        try:
                            mtime = os.lstat(fp).st_mtime
                        except FileNotFoundError:
                            continue
                        except Exception:
                            self.printException(e, "getFileListUntrackedAndIgnored: mtime failed for ignored")
                            mtime = 0
                    iso = self._epoch_to_iso(mtime)
                    results.append((rel, iso, "ignored"))

                results.sort(key=lambda x: x[0])
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
        """Return a sampled list of commit hashes for staged, new, and entire repo."""
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
        """Return a sampled list of commit hashes for staged, new, and entire repo."""
        sampleHashes = [("", self.NEWREPO, "Newly created repository")]
        normalHashes = self.getHashListSample(usePyGit2)
        sampleHashes += normalHashes
        staged = self.getHashListStagedChanges(usePyGit2)
        sampleHashes += staged
        new = self.getHashListNewChanges(usePyGit2)
        sampleHashes += new
        return sampleHashes

    # END: getHashListSamplePlusEnds v1

    # BEGIN: runFileListSampledComparisons v1
    def runFileListSampledComparisons(self, top: bool, raw: bool) -> None:
        """Run sampled comparisons and display diffs using `show_diffs`.

        Calls `getFileListBetweenNormalizedHashes` for both backends and
        forwards results to `show_diffs(label, pygit2_list, gitcli_list, top, raw)`.
        """
        sample = self.getHashListSamplePlusEnds(False)
        tokens: list = [x[1] for x in sample]

        # For each sampled token pair, run both backends (pygit2 and git CLI)
        # and compare their outputs.
        for i in range(len(tokens)):
            for j in range(i + 1, len(tokens)):
                a = tokens[i]
                b = tokens[j]
                try:
                    p = self.getFileListBetweenNormalizedHashes(a, b, True)
                except Exception as e:
                    self.printException(e, f"runFileListSampledComparisons: pygit2 diff failed for {a}->{b}")
                    p = []
                try:
                    g = self.getFileListBetweenNormalizedHashes(a, b, False)
                except Exception as e:
                    self.printException(e, f"runFileListSampledComparisons: git CLI diff failed for {a}->{b}")
                    g = []

                try:
                    show_diffs(f"get {a}->{b}", p, g, top, raw, self.verbose)
                except Exception as e:
                    self.printException(e, f"runFileListSampledComparisons: show_diffs failed for {a}->{b}")

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
                formatted.append(self._format_commit_entry(repo, h))
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
        """Return a list of commit hashes that modified the given file."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of hashes
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            repo = self.pygit2_repo
            matches = []
            try:
                try:
                    head = repo.revparse_single("HEAD")
                except Exception as e:
                    self.printException(e, "getHashListFromFileName: revparse HEAD failed")
                    return []
                walker = repo.walk(head.id, pygit2.GIT_SORT_TIME)
                for c in walker:
                    try:
                        parents = list(c.parents) if len(c.parents) > 0 else [None]
                        found = False
                        for p in parents:
                            try:
                                a_tree = self._resolve_tree(p)
                                b_tree = self._resolve_tree(c)
                                # If both sides are None/non-tree, skip
                                if a_tree is None and b_tree is None:
                                    continue
                                # Ensure we have tree-ish objects: build an empty tree when needed
                                if a_tree is None:
                                    try:
                                        a_tree = self._empty_tree_for_repo(repo)
                                        if a_tree is None:
                                            raise RuntimeError("empty tree construction returned None")
                                    except Exception as e:
                                        self.printException(
                                            e, "getHashListFromFileName: failed to construct empty a_tree"
                                        )
                                        continue
                                if b_tree is None:
                                    try:
                                        b_tree = self._empty_tree_for_repo(repo)
                                        if b_tree is None:
                                            raise RuntimeError("empty tree construction returned None")
                                    except Exception as e:
                                        self.printException(
                                            e, "getHashListFromFileName: failed to construct empty b_tree"
                                        )
                                        continue
                                try:
                                    diff = repo.diff(a_tree, b_tree)
                                except Exception as e:
                                    self.printException(
                                        e,
                                        "getHashListFromFileName: repo.diff(a_tree,b_tree) failed; trying reversed args",
                                    )
                                    try:
                                        diff = repo.diff(b_tree, a_tree)
                                    except Exception as e:
                                        self.printException(
                                            e, "getHashListFromFileName: repo.diff reversed args failed"
                                        )
                                        continue
                            except Exception as e:
                                self.printException(e, "getHashListFromFileName: building trees for diff failed")
                                continue

                            if diff is None:
                                continue

                            # Iterate deltas and check for matching path
                            matched = False
                            for delta in diff.deltas:
                                path = getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None)
                                if path == file_name:
                                    matched = True
                                    break
                            if matched:
                                ch = getattr(c, "hex", None)
                                if not ch:
                                    cid = getattr(c, "id", None)
                                    if cid is not None:
                                        ch = getattr(cid, "hex", None) or str(cid)
                                if ch:
                                    # Use central formatter to produce ISO/hash/subject when available
                                    matches.append(self._format_commit_entry(repo, c))
                                found = True
                                break
                        if found:
                            continue
                    except Exception as e:
                        self.printException(e, "getHashListFromFileName: per-commit handling failed")
                        continue
            except Exception as e:
                self.printException(e, "pygit2 log walk failed")
                return []
            return matches
        else:
            try:
                output = check_output(
                    ["git", "log", "--pretty=format:%ct %H %s", "--", file_name], cwd=self.repoRoot, text=True
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
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
                if subject:
                    entries.append((iso, h, subject))
                else:
                    entries.append((iso, h, ""))
            # Detect working-tree/index state for this file and prepend pseudo-entries
            try:
                status_out = check_output(
                    ["git", "status", "--porcelain", "--", file_name], cwd=self.repoRoot, text=True
                )
            except CalledProcessError as e:
                self.printException(e, f"git status failed for {file_name}")
                status_out = ""
            if status_out:
                # porcelain: two-char XY at start
                s = status_out.splitlines()[0]
                if len(s) >= 2:
                    idx_flag = s[0]
                    wt_flag = s[1]
                else:
                    idx_flag = s[0] if s else " "
                    wt_flag = " "
                # Prepare timestamps for pseudo-entries
                iso_index = self.index_mtime_iso()
                iso_mods = self._paths_mtime_iso([file_name])
                # If index has a change, represent staged version with index timestamp
                if idx_flag != " ":
                    entries.insert(0, (iso_index, "STAGED", self.STAGED_MESSAGE))
                # If working tree has modifications (unstaged), represent as MODS
                if wt_flag != " ":
                    # Insert after STAGED if present, otherwise at top
                    if entries and entries[0][1] == "STAGED":
                        entries.insert(1, (iso_mods, "MODS", self.MODS_MESSAGE))
                    else:
                        entries.insert(0, (iso_mods, "MODS", self.MODS_MESSAGE))
            return entries

    # END: getHashListFromFileName v1


# BEGIN: show_diffs v1
def show_diffs(test_name: str, list1: list, list2: list, top: int = 0, raw: bool = False, verbose: int = 0) -> bool:
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
        if verbose > 0:
            print(f"[{test_name}] Differences found (verbose={verbose}):")
        else:
            print(f"[{test_name}] Differences found:")
        for line in diff:
            print(line)
        return False
    else:
        lines = len(disp1)
        if verbose > 0:
            print(f"[{test_name}] No differences found in {lines} lines of output (verbose={verbose})")
        else:
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
        "path",
        type=str,
        help="Path to the git repository to test.",
    )
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
        "-u",
        "--up-through",
        type=str,
        default=None,
        help="Run tests up through this base-36 digit (0-9, a-z). Example: -u 2 runs -1 and -2; -u a runs up through -a",
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

    args = parser.parse_args()

    test_repo = TestRepo(args.path, args.verbose)

    # If user requested an "up through" numeric run, set the matching
    # boolean flags so downstream selection logic runs the requested tests.
    if args.up_through is not None:
        try:
            # Interpret up-through as a base-36 digit/string so 'a'..'z' map to 10..35
            n = int(str(args.up_through), 36)
        except Exception as e:
            test_repo.printException(e, "up-through parse failed (expected base36)")
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

    # Helper to run a single comparison and return True on success
    def run_one(name: str, func_name: str, fname: str | None) -> bool:
        # Debug: report which test function is being invoked
        if test_repo.verbose:
            print(f"DEBUG: run_one invoking {func_name} (display name: {name})")
        if fname is not None:
            l1 = getattr(test_repo, func_name)(fname, usePyGit2=True)
            l2 = getattr(test_repo, func_name)(fname, usePyGit2=False)
        else:
            l1 = getattr(test_repo, func_name)(usePyGit2=True)
            l2 = getattr(test_repo, func_name)(usePyGit2=False)
        return show_diffs(name, l1, l2, args.top, args.raw, args.verbose)

    allfuncs = [
        ("getFileListBetweenNewRepoAndTopHash: File List New to Top Hash", "getFileListBetweenNewRepoAndTopHash", None),
        (
            "getFileListBetweenTopHashAndCurrentTime: File List Between TopHash and Current Time",
            "getFileListBetweenTopHashAndCurrentTime",
            None,
        ),
        (
            "getFileListBetweenTopHashAndStaged: File List Between TopHash and Staged",
            "getFileListBetweenTopHashAndStaged",
            None,
        ),
        ("getFileListBetweenStagedAndMods: File List Between Staged and Mods", "getFileListBetweenStagedAndMods", None),
        ("getFileListBetweenNewRepoAndStaged: File List New to Staged", "getFileListBetweenNewRepoAndStaged", None),
        ("getFileListBetweenNewRepoAndMods: File List New to Mods", "getFileListBetweenNewRepoAndMods", None),
        ("getHashListEntireRepo: Hash List Entire Repo", "getHashListEntireRepo", None),
        ("getHashListStagedChanges: Hash List Staged Changes", "getHashListStagedChanges", None),
        (f"getHashListFromFileName: Hash List From File {args.file}", "getHashListFromFileName", args.file),
        ("getHashListNewChanges: Hash List New Changes", "getHashListNewChanges", None),
        ("getHashListComplete: Hash List Complete", "getHashListComplete", None),
        ("getHashListSample: Hash List Sample", "getHashListSample", None),
        ("getHashListSamplePlusEnds: Hash List Sample Plus Ends", "getHashListSamplePlusEnds", None),
        ("getFileListUntrackedAndIgnored: Untracked and Ignored files", "getFileListUntrackedAndIgnored", None),
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
                    "getFileListBetweenNewRepoAndTopHash: File List New to Top Hash",
                    "getFileListBetweenNewRepoAndTopHash",
                    None,
                )
            )
        if args.getFileListBetweenTopHashAndCurrentTime:
            to_run.append(
                (
                    "getFileListBetweenTopHashAndCurrentTime: File List Between TopHash and Current Time",
                    "getFileListBetweenTopHashAndCurrentTime",
                    None,
                )
            )
        if args.getFileListBetweenTopHashAndStaged:
            to_run.append(
                (
                    "getFileListBetweenTopHashAndStaged: File List Between TopHash and Staged",
                    "getFileListBetweenTopHashAndStaged",
                    None,
                )
            )
        if args.getFileListBetweenStagedAndMods:
            to_run.append(
                (
                    "getFileListBetweenStagedAndMods: File List Between Staged and Mods",
                    "getFileListBetweenStagedAndMods",
                    None,
                )
            )
        if args.getFileListBetweenNewAndStaged:
            to_run.append(
                (
                    "getFileListBetweenNewRepoAndStaged: File List New to Staged",
                    "getFileListBetweenNewRepoAndStaged",
                    None,
                )
            )
        if args.getFileListBetweenNewAndMods:
            to_run.append(
                ("getFileListBetweenNewRepoAndMods: File List New to Mods", "getFileListBetweenNewRepoAndMods", None)
            )
        if args.getHashListEntireRepo:
            to_run.append(("getHashListEntireRepo: Hash List Entire Repo", "getHashListEntireRepo", None))
        if args.getHashListStagedChanges:
            to_run.append(("getHashListStagedChanges: Hash List Staged Changes", "getHashListStagedChanges", None))
        if args.getHashListFromFileName:
            to_run.append(
                (f"getHashListFromFileName: Hash List From File {args.file}", "getHashListFromFileName", args.file)
            )
        if args.getHashListNewChanges:
            to_run.append(("getHashListNewChanges: Hash List New Changes", "getHashListNewChanges", None))
        if args.getHashListComplete:
            to_run.append(("getHashListComplete: Hash List Complete", "getHashListComplete", None))
        if args.getHashListSample:
            to_run.append(("getHashListSample: Hash List Sample", "getHashListSample", None))
        # Include sample-plus-ends (-d) then untracked/ignored (-e) in option order
        if args.getHashListSamplePlusEnds:
            to_run.append(("getHashListSamplePlusEnds: Hash List Sample Plus Ends", "getHashListSamplePlusEnds", None))
        if args.getFileListUntrackedAndIgnored:
            to_run.append(("getFileListUntrackedAndIgnored: Untracked and Ignored files", "getFileListUntrackedAndIgnored", None))
        # Sampled comparisons are run separately to allow independent reporting
        # and avoid mixing their output with the main test loop.

    # If no specific flags provided, default to running all tests
    if not to_run and not args.getFileListSampledComparisons:
        args.all = True
        to_run = allfuncs
        args.getFileListSampledComparisons = True

    total = 0
    passed = 0
    failed = 0
    for name, func, fname in to_run:
        total += 1
        try:
            ok = run_one(name, func, fname)
        except Exception as e:
            test_repo.printException(e, f"running {name} failed")
            ok = False
        if ok:
            passed += 1
        else:
            failed += 1

    # Final summary
    print(f"\nTest summary: total={total} passed={passed} failed={failed}")

    # If requested, run sampled comparisons separately (outside the to_run loop)
    if args.getFileListSampledComparisons:
        print("\nRunning sampled pairwise comparisons (separate)...")
        try:
            # runFileListSampledComparisons now accepts (top, raw, verbose)
            test_repo.runFileListSampledComparisons(args.top, args.raw)
        except Exception as e:
            test_repo.printException(e, "running runFileListSampledComparisons failed")


if __name__ == "__main__":
    main()
