#!/usr/bin/env python3

"""Test pygit2 and git CLI repository file listing methods."""

import argparse
import difflib
import sys
import traceback

import pygit2
from datetime import datetime, timezone
from subprocess import check_output, CalledProcessError


class AppException:
    """Mixin providing instance-level exception logging for apps and widgets.

    This centralizes `printException` so multiple base classes can inherit
    it and avoid duplicate implementations.
    """

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


class TestRepo(AppException):
    """Test pygit2 and git CLI repository file listing methods."""

    def __init__(self, repoRoot: str):
        self.repoRoot = repoRoot
        self.pygit2_repo = pygit2.Repository(self.repoRoot)

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
                # Peel annotated tag to its target if possible
                try:
                    target = obj.get(obj.target)
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

    def getFileListNewToTopHash(self, usePyGit2: bool) -> list[str]:
        """Return a list of all files added from the beginning to the current repository state."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of files
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            # Use pygit2 to get the list of files
            commit = self.pygit2_repo.revparse_single("HEAD")
            tree = commit.tree
            files: list[str] = []

            def _walk(t, prefix: str = ""):
                for entry in t:
                    path = prefix + entry.name
                    if entry.type == pygit2.GIT_OBJECT_TREE:
                        _walk(self.pygit2_repo[entry.id], path + "/")
                    else:
                        files.append(path)

            _walk(tree)
            return sorted(files)

        else:
            # Use git CLI to get the list of files
            try:
                output = check_output(
                    ["git", "ls-tree", "-r", "--name-only", "HEAD"],
                    cwd=self.repoRoot,
                    text=True,
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []

            files = [line for line in output.splitlines() if line]
            return sorted(files)

    def getFileListBetweenHashes(self, prev_hash: str, curr_hash: str, usePyGit2: bool) -> list[str]:
        """Return a list of files changed between `prev_hash` and `curr_hash`."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of files
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                a = self.pygit2_repo.revparse_single(prev_hash)
                b = self.pygit2_repo.revparse_single(curr_hash)
            except Exception as ex:
                self.printException(ex, "pygit2 combined walker failed")
            # Attach timestamps to each commit and sort by (timestamp, hex)
            commit_info = []
            for h in commits:
                try:
                    obj = repo.get(h)
                    t = getattr(obj, "time", None) or getattr(obj, "commit_time", None) or 0
                    ts = int(t)
                except Exception as e:
                    self.printException(e, "getFileListBetweenHashes: commit time extraction failed")
                    ts = 0
                commit_info.append((ts, h))

            # Sort by timestamp descending, then by hash to stabilize ordering
            commit_info.sort(key=lambda x: (x[0], x[1]), reverse=True)

            formatted = []
            for ts, h in commit_info:
                try:
                    dt = datetime.fromtimestamp(ts, timezone.utc)
                    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
                except Exception as e:
                    self.printException(e, "getFileListBetweenHashes: timestamp formatting failed")
                    iso = "1970-01-01T00:00:00"
                formatted.append(f"{iso} {h}")
            return formatted
            a_tree = self._resolve_tree(a)
            b_tree = self._resolve_tree(b)
            if a_tree is None or b_tree is None:
                self.printException(ValueError("could not resolve trees"), "_resolve_tree failed")
                return []
            try:
                diff = self.pygit2_repo.diff(a_tree, b_tree)
            except Exception as e:
                self.printException(e, "pygit2 diff failed")
                return []
            files = [getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None) for delta in diff.deltas]
            return sorted([p for p in files if p])

        else:
            # Use git CLI to get the list of files
            try:
                output = check_output(
                    ["git", "diff", "--name-only", prev_hash, curr_hash],
                    cwd=self.repoRoot,
                    text=True,
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []

            files = [line for line in output.splitlines() if line]
            return sorted(files)

    def getFileListBetweenNewAndHash(self, curr_hash: str, usePyGit2: bool) -> list[str]:
        """Return a list of files changed between the beginning and `curr_hash`."""
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
            # Try diff(None, cur_tree) first; some pygit2 builds accept it.
            try:
                diff = self.pygit2_repo.diff(None, cur_tree)
            except Exception as e:
                self.printException(e, "pygit2 diff(None, cur_tree) not supported; using TreeBuilder fallback")
                # Fallback: construct index/tree from empty index
                try:
                    tb = self.pygit2_repo.TreeBuilder()
                    empty_oid = tb.write()
                    empty_tree = self.pygit2_repo.get(empty_oid)
                    diff = self.pygit2_repo.diff(empty_tree, cur_tree)
                except Exception as e:
                    self.printException(e, "pygit2 TreeBuilder fallback failed")
                    self.printException(e, "pygit2 initial-commit diff failed")
                    return []
            files = [getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None) for delta in diff.deltas]
            return sorted([p for p in files if p])

        else:
            # Use git CLI to get the list of files
            try:
                output = check_output(
                    ["git", "diff", "--name-only", curr_hash],
                    cwd=self.repoRoot,
                    text=True,
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []

            files = [line for line in output.splitlines() if line]
            return sorted(files)

    def getFileListBetweenTopHashAndCurrentTime(self, usePyGit2: bool) -> list[str]:
        """Return a list of files changed between the latest commit and the current time."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of files
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                commit = self.pygit2_repo.revparse_single("HEAD")
            except Exception as e:
                self.printException(e, "revparse_single HEAD failed")
                return []
            cur_tree = self._resolve_tree(commit)
            if cur_tree is None:
                self.printException(ValueError("could not resolve HEAD tree"), "_resolve_tree failed")
                return []
            try:
                diff = self.pygit2_repo.diff(cur_tree, None)
            except Exception as e:
                self.printException(e, "pygit2 working-tree diff failed")
                return []
            files = [getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None) for delta in diff.deltas]
            return sorted([p for p in files if p])

        else:
            # Use git CLI to get the list of files
            try:
                output = check_output(
                    ["git", "diff", "--name-only", "HEAD"],
                    cwd=self.repoRoot,
                    text=True,
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []

            files = [line for line in output.splitlines() if line]
            return sorted(files)

    def getFileListBetweenTopHashAndStaged(self, usePyGit2: bool) -> list[str]:
        """Return a list of files changed between the latest commit and the staged changes."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of files
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                commit = self.pygit2_repo.revparse_single("HEAD")
            except Exception as e:
                self.printException(e, "revparse_single HEAD failed")
                return []
            head_tree = self._resolve_tree(commit)
            if head_tree is None:
                self.printException(ValueError("could not resolve HEAD tree"), "_resolve_tree failed")
                return []
            try:
                # Write the index to a tree object and diff against HEAD tree
                idx_tree_oid = self.pygit2_repo.index.write_tree()
                idx_tree = self.pygit2_repo.get(idx_tree_oid)
                diff = self.pygit2_repo.diff(head_tree, idx_tree)
            except Exception as e:
                self.printException(e, "pygit2 staged-vs-HEAD diff failed")
                return []
            files = [getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None) for delta in diff.deltas]
            return sorted([p for p in files if p])
        else:
            # Use git CLI for staged-vs-HEAD list
            try:
                output = check_output(
                    ["git", "diff", "--name-only", "--cached", "HEAD"],
                    cwd=self.repoRoot,
                    text=True,
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []

            files = [line for line in output.splitlines() if line]
            return sorted(files)

    def getFileListBetweenStagedAndWorkingTree(self, usePyGit2: bool) -> list[str]:
        """Return a list of files changed between the staged changes and the working tree."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of files
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                # Write the index to a tree and diff index-tree -> working tree (None)
                idx_tree_oid = self.pygit2_repo.index.write_tree()
                idx_tree = self.pygit2_repo.get(idx_tree_oid)
                diff = self.pygit2_repo.diff(idx_tree, None)
            except Exception as e:
                self.printException(e, "pygit2 staged->working diff failed")
                return []
            files = [getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None) for delta in diff.deltas]
            return sorted([p for p in files if p])

        else:
            # Use git CLI to get the list of files
            try:
                output = check_output(
                    ["git", "diff", "--name-only"],
                    cwd=self.repoRoot,
                    text=True,
                )
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []

            files = [line for line in output.splitlines() if line]
            return sorted(files)


    def getHashListEntireRepo(self, usePyGit2: bool) -> list[str]:
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
                                self.printException(e, f"getHashListEntireRepo: repo.get failed for target of {ref_name}")
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
            formatted = []
            for ts, h in commit_info:
                try:
                    dt = datetime.fromtimestamp(ts, timezone.utc)
                    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
                except Exception as e:
                    self.printException(e, "getHashListEntireRepo: timestamp formatting failed")
                    iso = "1970-01-01T00:00:00"
                formatted.append(f"{iso} {h}")
            return formatted
        else:
            try:
                # Use git log to get commit epoch time and hash for all refs
                output = check_output(["git", "log", "--all", "--pretty=format:%ct %H"], cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []
            pairs = []
            for line in output.splitlines():
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                try:
                    ts = int(parts[0])
                except Exception as e:
                    self.printException(e, "getHashListEntireRepo: parsing git log timestamp failed")
                    ts = 0
                h = parts[1].strip()
                pairs.append((ts, h))

            pairs.sort(key=lambda x: (x[0], x[1]), reverse=True)
            formatted = []
            for ts, h in pairs:
                try:
                    dt = datetime.fromtimestamp(ts, timezone.utc)
                    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
                except Exception as e:
                    self.printException(e, "getHashListEntireRepo: formatting timestamp failed")
                    iso = "1970-01-01T00:00:00"
                formatted.append(f"{iso} {h}")
            return formatted


    def getHashListStagedChanges(self, usePyGit2: bool) -> list[str]:
        """Return a list of commit hashes for staged changes."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of hashes
        # Interpret "hashes for staged changes" as the blob OIDs present in the index
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                idx = self.pygit2_repo.index
                oids = [str(e.id) for e in idx]
                return sorted(oids)
            except Exception as e:
                self.printException(e, "pygit2 index inspection failed")
                return []
        else:
            try:
                output = check_output(["git", "ls-files", "-s"], cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []
            oids = []
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    # format: <mode> <object> <stage>\t<file>
                    oids.append(parts[1])
            return sorted(oids)
    
    
    def getHashListFromFileName(self, file_name: str, usePyGit2: bool) -> list[str]:
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
                                        tb = repo.TreeBuilder()
                                        empty_oid = tb.write()
                                        a_tree = repo.get(empty_oid)
                                    except Exception as e:
                                        self.printException(e, "getHashListFromFileName: failed to construct empty a_tree")
                                        continue
                                if b_tree is None:
                                    try:
                                        tb = repo.TreeBuilder()
                                        empty_oid = tb.write()
                                        b_tree = repo.get(empty_oid)
                                    except Exception as e:
                                        self.printException(e, "getHashListFromFileName: failed to construct empty b_tree")
                                        continue
                                try:
                                    diff = repo.diff(a_tree, b_tree)
                                except Exception as e:
                                    self.printException(e, "getHashListFromFileName: repo.diff(a_tree,b_tree) failed; trying reversed args")
                                    try:
                                        diff = repo.diff(b_tree, a_tree)
                                    except Exception as e:
                                        self.printException(e, "getHashListFromFileName: repo.diff reversed args failed")
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
                                    matches.append(ch)
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
                output = check_output(["git", "log", "--pretty=format:%H", "--", file_name], cwd=self.repoRoot, text=True)
            except CalledProcessError as e:
                self.printException(e, "git command failed")
                return []
            return [ln for ln in output.splitlines() if ln]
    
    

def show_diffs(test_name: str, list1: list[str], list2: list[str]) -> None:
    """Show differences between two file lists."""
    diff = list(difflib.unified_diff(list1, list2, fromfile="pygit2", tofile="git", lineterm=""))
    if diff:
        print(f"[{test_name}] Differences found:")
        for line in diff:
            print(line)
    else:
        print(f"[{test_name}] No differences found.")


def main():
    """Main function to run the tests."""
    parser = argparse.ArgumentParser(prog="gitdiffnavtool.py", description=__doc__)
    parser.add_argument(
        "path",
        type=str,
        help="Path to the git repository to test.",
    )

    args = parser.parse_args()
    test_repo = TestRepo(args.path)

    # Run tests for both pygit2 and git CLI methods
    show_diffs("getFileListNewToTopHash: File List New to Top Hash", 
               test_repo.getFileListNewToTopHash(usePyGit2=True), 
               test_repo.getFileListNewToTopHash(usePyGit2=False))
    show_diffs("getFileListBetweenTopHashAndCurrentTime: File List Between TopHash and Current Time", 
               test_repo.getFileListBetweenTopHashAndCurrentTime(usePyGit2=True), 
               test_repo.getFileListBetweenTopHashAndCurrentTime(usePyGit2=False))
    show_diffs("getFileListBetweenTopHashAndStaged:File List Between TopHash and Staged",
               test_repo.getFileListBetweenTopHashAndStaged(usePyGit2=True), 
               test_repo.getFileListBetweenTopHashAndStaged(usePyGit2=False))
    show_diffs("getFileListBetweenStagedAndWorkingTree: File List Between Staged and Working Tree",
               test_repo.getFileListBetweenStagedAndWorkingTree(usePyGit2=True), 
               test_repo.getFileListBetweenStagedAndWorkingTree(usePyGit2=False))
    show_diffs("getHashListEntireRepo: Hash List Entire Repo",
               test_repo.getHashListEntireRepo(usePyGit2=True),
               test_repo.getHashListEntireRepo(usePyGit2=False))
    show_diffs("getHashListStagedChanges: Hash List Staged Changes",
               test_repo.getHashListStagedChanges(usePyGit2=True),
               test_repo.getHashListStagedChanges(usePyGit2=False))
    # Compare commit lists that touched README.md (adjust filename as needed)
    show_diffs("getHashListFromFileName: Hash List From File README.md",
               test_repo.getHashListFromFileName("README.md", usePyGit2=True),
               test_repo.getHashListFromFileName("README.md", usePyGit2=False))
    
if __name__ == "__main__":
    main()
