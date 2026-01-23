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

import pygit2
import codecs
from datetime import datetime, timezone
from subprocess import check_output, CalledProcessError
import traceback
import sys


def _pygit2_similarity_flags() -> int:
    """Return the strongest available pygit2 diff similarity flags.

    Combine rename/copy related flags if defined in the installed pygit2.
    Returns 0 when `pygit2` is not available or no flags are found.
    """
    flags = 0
    # Base support: renames + copies
    if hasattr(pygit2, "GIT_DIFF_FIND_RENAMES"):
        flags |= getattr(pygit2, "GIT_DIFF_FIND_RENAMES")
    if hasattr(pygit2, "GIT_DIFF_FIND_COPIES"):
        flags |= getattr(pygit2, "GIT_DIFF_FIND_COPIES")
    # Extend where available
    for name in ("GIT_DIFF_FIND_COPIES_FROM_UNMODIFIED", "GIT_DIFF_FIND_RENAMES_FROM_REWRITES", "GIT_DIFF_FIND_FOR_UNTRACKED"):
        if hasattr(pygit2, name):
            flags |= getattr(pygit2, name)
    return flags


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
        self.pygit2_repo = pygit2.Repository(self.repoRoot)
        self.verbose = verbose
        self.silent = silent

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

    # BEGIN: pygit2_resolve_token_to_tree v1
    def pygit2_resolve_token_to_tree(self, token):
        """Resolve a token (NEWREPO/STAGED/MODS or commit-ish) to a pygit2.Tree or None.

        - `STAGED` -> tree built from index
        - `MODS` -> None (represents working tree for pygit2.diff)
        - `NEWREPO` -> explicit empty tree via `_empty_tree_for_repo`
        - otherwise: try `revparse_single` then `repo.get`, and coerce to a tree
        Returns a `pygit2.Tree` or `None` on failure.
        """
        try:
            repo = self.pygit2_repo
            if token == self.STAGED:
                try:
                    repo.index.read()
                    oid = repo.index.write_tree()
                    return repo.get(oid)
                except Exception as e:
                    self.printException(e, "pygit2_resolve_token_to_tree: index -> tree failed")
                    return None
            if token == self.MODS:
                # Represent working tree as None for pygit2.diff
                return None
            if token == self.NEWREPO:
                return self._empty_tree_for_repo(repo)

            # commit-ish: try revparse_single then repo.get
            try:
                obj = repo.revparse_single(token)
            except Exception as e:
                self.printException(e, f"pygit2_resolve_token_to_tree: revparse_single({token}) failed")
                try:
                    obj = repo.get(token)
                except Exception as e2:
                    self.printException(e2, f"pygit2_resolve_token_to_tree: resolving {token} failed")
                    return None
            return self._resolve_tree(obj)
        except Exception as e:
            self.printException(e, "pygit2_resolve_token_to_tree: unexpected failure")
            return None

    # END: pygit2_resolve_token_to_tree v1

    # BEGIN: _pygit2_format_commit_entry v1
    def _pygit2_format_commit_entry(self, repo, commit_or_hash) -> tuple[str, str, str]:
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
                    self.printException(e, "_pygit2_format_commit_entry: repo.get failed")
                    return ("", str(commit_or_hash), "")

            if isinstance(c, pygit2.Commit):
                # Build ISO timestamp, commit hex, and top-line subject
                try:
                    ts = int(getattr(c, "time", None) or getattr(c, "commit_time", None) or 0)
                except Exception as e:
                    self.printException(e, "_pygit2_format_commit_entry: parsing timestamp failed")
                    ts = 0

                iso = self._epoch_to_iso(ts)

                ch = getattr(c, "hex", None)
                if not ch:
                    cid = getattr(c, "id", None)
                    ch = getattr(cid, "hex", None) or (str(cid) if cid is not None else "")

                try:
                    msg = getattr(c, "message", "") or ""
                    subject = msg.splitlines()[0].strip() if msg else ""
                except Exception as e:
                    self.printException(e, "_pygit2_format_commit_entry: extracting subject failed")
                    subject = ""

                return (iso, ch, subject)

            # Not a commit object (blob/tree/etc) — return hash-like tuple
            ch = getattr(c, "hex", None)
            if not ch:
                cid = getattr(c, "id", None)
                ch = getattr(cid, "hex", None) or str(cid) if cid is not None else str(commit_or_hash)
            return ("", ch, "")
        except Exception as e:
            # Fallback to raw tuple representation on error
            self.printException(e, "_pygit2_format_commit_entry failed")
            return ("", str(commit_or_hash), "")

    # END: _pygit2_format_commit_entry v1

    # BEGIN: _pygit2_run_pygit2_diff v1
    def _pygit2_run_pygit2_diff(self, prev_token, curr_token):
        """Resolve tokens, run a pygit2 diff, and return detailed delta dicts + raw trees.

        Returns a tuple `(detailed_list, a_raw, b_raw)` where `a_raw`/`b_raw` are
        the resolved tree objects (or None when the token represents the working
        tree) and `detailed_list` is a list of dicts with keys:
        `path`, `status`, `old_oid`, `new_oid`, `old_path`, `new_path`, `delta`.
        """
        try:
            repo = self.pygit2_repo
            try:
                a_raw = self.pygit2_resolve_token_to_tree(prev_token)
                b_raw = self.pygit2_resolve_token_to_tree(curr_token)
            except Exception as e:
                self.printException(e, "_pygit2_run_pygit2_diff: token resolve failed")
                return ([], None, None)

            # Instrumentation: small runtime summary for debugging diff invocation
            try:
                if self.verbose > 0:
                    a_type = type(a_raw).__name__ if a_raw is not None else "None"
                    b_type = type(b_raw).__name__ if b_raw is not None else "None"
                    print(f"DEBUG: _pygit2_run_pygit2_diff resolved a_raw={a_type} b_raw={b_type} prev={prev_token} curr={curr_token}")
                    try:
                        repo.index.read()
                        try:
                            idx_oid = repo.index.write_tree()
                        except Exception:
                            idx_oid = None
                        print(f"DEBUG: _pygit2_run_pygit2_diff index write_tree oid={idx_oid}")
                    except Exception:
                        print("DEBUG: _pygit2_run_pygit2_diff index info unavailable")
                    try:
                        st = repo.status()
                        total = len(st)
                        untracked = sum(1 for _, flags in st.items() if flags & getattr(pygit2, 'GIT_STATUS_WT_NEW', 0))
                        modified = sum(1 for _, flags in st.items() if flags & getattr(pygit2, 'GIT_STATUS_WT_MODIFIED', 0))
                        deleted = sum(1 for _, flags in st.items() if flags & getattr(pygit2, 'GIT_STATUS_WT_DELETED', 0))
                        print(f"DEBUG: _pygit2_run_pygit2_diff repo.status summary total={total} untracked={untracked} modified={modified} deleted={deleted}")
                    except Exception:
                        print("DEBUG: _pygit2_run_pygit2_diff repo.status unavailable")
            except Exception:
                pass

            try:
                # Try to diff using the resolved raw objects directly. For
                # working-tree comparisons `a_raw` or `b_raw` may be `None` and
                # libgit2/python bindings often accept `None` to represent the
                # working tree. Only fall back to an explicit empty-tree when
                # repo.diff(a,b) raises an exception.
                a = a_raw
                b = b_raw

                try:
                    diff = repo.diff(a, b)
                except Exception as e:
                    # Fall back to empty-tree substitution when diff(a,None)
                    # is not supported by this libgit2 build.
                    self.printException(e, "_pygit2_run_pygit2_diff: repo.diff(a,b) failed, falling back to empty-tree")
                    empty = self._empty_tree_for_repo(repo)
                    if empty is None:
                        self.printException(RuntimeError("failed to construct empty tree"), "_pygit2_run_pygit2_diff: empty tree construction failed")
                        return ([], a_raw, b_raw)
                    a = a if a is not None else empty
                    b = b if b is not None else empty
                    diff = repo.diff(a, b)

                try:
                    flags = _pygit2_similarity_flags()
                    if flags:
                        diff.find_similar(flags)
                except Exception as e:
                    self.printException(e, "_pygit2_run_pygit2_diff: find_similar failed")
            except Exception as e:
                self.printException(e, "_pygit2_run_pygit2_diff: pygit2 diff failed")
                return ([], a_raw, b_raw)

            detailed = []
            for delta in diff.deltas:
                old_path = getattr(delta.old_file, "path", None)
                new_path = getattr(delta.new_file, "path", None)
                path = new_path or old_path
                status = self._delta_status_to_str(getattr(delta, "status", None), delta)
                oid_old = None
                oid_new = None
                # Extra debug: print raw delta object and oid objects when verbose
                if self.verbose > 1:
                    try:
                        print(f"DEBUG: raw delta repr={delta!r}")
                        of = getattr(delta, 'old_file', None)
                        nf = getattr(delta, 'new_file', None)
                        oo = getattr(of, 'oid', None) or getattr(of, 'id', None) if of is not None else None
                        no = getattr(nf, 'oid', None) or getattr(nf, 'id', None) if nf is not None else None
                        print(f"DEBUG: old_file.path={getattr(of,'path',None)} old_oid_obj={oo}")
                        print(f"DEBUG: new_file.path={getattr(nf,'path',None)} new_oid_obj={no}")
                    except Exception as e:
                        self.printException(e, "_pygit2_run_pygit2_diff: debug print failed")
                try:
                    oid_old_obj = getattr(delta.old_file, "oid", None) or getattr(delta.old_file, "id", None)
                    if oid_old_obj is not None:
                        oid_old = str(oid_old_obj)
                except Exception as e:
                    oid_old = None
                    self.printException(e, "_pygit2_run_pygit2_diff: extracting old oid failed")
                try:
                    oid_new_obj = getattr(delta.new_file, "oid", None) or getattr(delta.new_file, "id", None)
                    if oid_new_obj is not None:
                        oid_new = str(oid_new_obj)
                except Exception as e:
                    oid_new = None
                    self.printException(e, "_pygit2_run_pygit2_diff: extracting new oid failed")
                if path:
                    detailed.append({
                        "path": path,
                        "status": status,
                        "old_oid": oid_old,
                        "new_oid": oid_new,
                        "old_path": old_path,
                        "new_path": new_path,
                        "delta": delta,
                    })

            return (detailed, a_raw, b_raw)
        except Exception as e:
            self.printException(e, "_pygit2_run_pygit2_diff: unexpected failure")
            return ([], None, None)
    # END: _pygit2_run_pygit2_diff v1

    # BEGIN: _deltas_to_results v1
    def _deltas_to_results(self, detailed: list, a_raw, b_raw) -> list[tuple[str, str]]:
        """Convert the detailed delta dicts into final `(path,status)` results.

        This performs OID-based coalescing of delete+add -> rename, falls back
        to walking the provided trees to resolve OIDs when needed, and finally
        uses a fuzzy filename heuristic to coalesce likely renames.
        """
        try:
            if not detailed:
                return []

            if self.verbose > 1:
                print("DEBUG:raw detailed deltas:")
                for it in detailed:
                    print(f"DEBUG: delta path={it.get('path')} status={it.get('status')} old_path={it.get('old_path')} new_path={it.get('new_path')} old_oid={it.get('old_oid')} new_oid={it.get('new_oid')}")
                    # Also print the raw pygit2 delta object and its file oids
                    try:
                        d = it.get('delta')
                        if d is not None:
                            of = getattr(d, 'old_file', None)
                            nf = getattr(d, 'new_file', None)
                            oo = getattr(of, 'oid', None) or getattr(of, 'id', None) if of is not None else None
                            no = getattr(nf, 'oid', None) or getattr(nf, 'id', None) if nf is not None else None
                            print(f"DEBUG: raw delta repr={d!r}")
                            print(f"DEBUG: raw old_file obj={of!r} path={getattr(of,'path',None)} oid_obj={oo}")
                            print(f"DEBUG: raw new_file obj={nf!r} path={getattr(nf,'path',None)} oid_obj={no}")
                    except Exception as e:
                        self.printException(e, "_deltas_to_results: debug print failed")

            added_by_oid: dict[str, list[dict]] = {}
            deleted_by_oid: dict[str, list[dict]] = {}
            # Per-item entry tracing for lifecycle debugging
            for item in detailed:
                if self.verbose > 1:
                    try:
                        print(f"DEBUG: entering item id={id(item)} path={item.get('path')} status={item.get('status')} old_oid={item.get('old_oid')} new_oid={item.get('new_oid')}")
                    except Exception:
                        pass
                if item["status"] == "added" and item["new_oid"]:
                    added_by_oid.setdefault(item["new_oid"], []).append(item)
                    if self.verbose > 1:
                        try:
                            print(f"DEBUG: mapped added id={id(item)} new_oid={item.get('new_oid')} -> {item.get('path')}")
                        except Exception:
                            pass
                if item["status"] == "deleted" and item["old_oid"]:
                    deleted_by_oid.setdefault(item["old_oid"], []).append(item)
                    if self.verbose > 1:
                        try:
                            print(f"DEBUG: mapped deleted id={id(item)} old_oid={item.get('old_oid')} -> {item.get('path')}")
                        except Exception:
                            pass

            if self.verbose > 1:
                try:
                    print(f"DEBUG:_deltas_to_results added_by_oid count={len(added_by_oid)} deleted_by_oid count={len(deleted_by_oid)}")
                    if added_by_oid:
                        for k, v in list(added_by_oid.items())[:10]:
                            paths = [it.get('path') or it.get('new_path') for it in v]
                            print(f"DEBUG: added_by_oid {k} -> {paths}")
                    if deleted_by_oid:
                        for k, v in list(deleted_by_oid.items())[:10]:
                            paths = [it.get('path') or it.get('old_path') for it in v]
                            print(f"DEBUG: deleted_by_oid {k} -> {paths}")
                except Exception as _:
                    pass

            # If no oid info, attempt to resolve by walking trees
            if not added_by_oid and not deleted_by_oid:
                try:
                    commit_a_map: dict[str, str] = {}
                    commit_b_map: dict[str, str] = {}

                    def walk_commit_tree(tree, prefix, out_map):
                        for entry in tree:
                            p = os.path.join(prefix, entry.name) if prefix else entry.name
                            try:
                                oid_obj = getattr(entry, "oid", None) or getattr(entry, "id", None)
                                if entry.type == 2:  # tree
                                    sub = self.pygit2_repo.get(oid_obj) if oid_obj is not None else None
                                    if sub is not None:
                                        walk_commit_tree(sub, p, out_map)
                                elif entry.type == 3:  # blob
                                    out_map[p] = str(oid_obj) if oid_obj is not None else None
                            except Exception as e:
                                self.printException(e, "_deltas_to_results: walk_commit_tree entry handling failed")
                                continue

                    try:
                        if a_raw is not None:
                            walk_commit_tree(a_raw, "", commit_a_map)
                        if b_raw is not None:
                            walk_commit_tree(b_raw, "", commit_b_map)
                    except Exception as e:
                        commit_a_map = {}
                        commit_b_map = {}
                        self.printException(e, "_deltas_to_results: walking commit trees failed")

                    for item in detailed:
                        if item["status"] == "added":
                            oid = None
                            np = item.get("new_path") or item.get("path")
                            if np and commit_b_map:
                                oid = commit_b_map.get(np)
                            if oid:
                                added_by_oid.setdefault(oid, []).append(item)
                        if item["status"] == "deleted":
                            oid = None
                            op = item.get("old_path") or item.get("path")
                            if op and commit_a_map:
                                oid = commit_a_map.get(op)
                            if oid:
                                deleted_by_oid.setdefault(oid, []).append(item)
                except Exception as e:
                    self.printException(e, "_deltas_to_results: resolving blob OIDs from trees failed")

            used = set()
            results: list[tuple[str, str]] = []

            for oid in set(added_by_oid.keys()) & set(deleted_by_oid.keys()):
                adds = added_by_oid.get(oid, [])
                dels = deleted_by_oid.get(oid, [])
                for a, d in zip(adds, dels):
                    newp = a.get("new_path") or a.get("path")
                    results.append((newp, f"renamed->{newp}"))
                    if self.verbose > 1:
                        try:
                            print(f"DEBUG: coalesced oid={oid} rename {d.get('path')} -> {newp}")
                        except Exception:
                            pass
                    used.add(id(a))
                    used.add(id(d))

            for item in detailed:
                if id(item) in used:
                    if self.verbose > 1:
                        try:
                            print(f"DEBUG: skipping used item {item.get('path')} status={item.get('status')}")
                        except Exception:
                            pass
                    continue
                results.append((item["path"], item["status"]))
                if self.verbose > 1:
                    try:
                        print(f"DEBUG: appended result {item.get('path')} status={item.get('status')}")
                    except Exception:
                        pass

            try:
                remaining_added = [it for it in detailed if it["status"] == "added" and id(it) not in used]
                remaining_deleted = [it for it in detailed if it["status"] == "deleted" and id(it) not in used]
                for d in remaining_deleted:
                    best = None
                    best_ratio = 0.0
                    for a in remaining_added:
                        r = difflib.SequenceMatcher(None, d.get("old_path") or d.get("path"), a.get("new_path") or a.get("path")).ratio()
                        if r > best_ratio:
                            best_ratio = r
                            best = a
                    if best and best_ratio >= 0.6:
                        if self.verbose > 1:
                            try:
                                print(f"DEBUG: fuzzy rename match {d.get('path')} -> {best.get('path')} ratio={best_ratio}")
                            except Exception:
                                pass
                        results = [r for r in results if r != (d["path"], d["status"]) and r != (best["path"], best["status"])]
                        newp = best.get("new_path") or best.get("path")
                        results.append((newp, f"renamed->{newp}"))
                        used.add(id(d))
                        used.add(id(best))
                        remaining_added = [x for x in remaining_added if id(x) != id(best)]
            except Exception as e:
                self.printException(e, "_deltas_to_results: fuzzy rename heuristic failed")

            try:
                # Ensure any remaining 'added' items that weren't consumed
                # by rename/coalesce logic are preserved in the final list.
                res_paths = {p for p, _ in results}
                for it in detailed:
                    try:
                        if it.get("status") == "added" and id(it) not in used:
                            p = it.get("new_path") or it.get("path")
                            if p and p not in res_paths:
                                results.append((p, "added"))
                                if self.verbose > 1:
                                    print(f"DEBUG: preserved remaining added {p}")
                    except Exception:
                        pass
            except Exception as e:
                self.printException(e, "_deltas_to_results: preserving remaining added entries failed")

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
                tmp = codecs.decode(raw, "unicode_escape")
                b = tmp.encode("latin-1", "surrogatepass")
                try:
                    return b.decode("utf-8")
                except UnicodeDecodeError as e:
                    self.printException(e, "_decode_git_quoted_path: unicode decode failed")
                    return b.decode("latin-1")
            except Exception as e:
                self.printException(e, "_decode_git_quoted_path: decode failed")
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
    def _delta_status_to_str(self, status_code, delta=None) -> str:
        """Map pygit2 delta status codes to human-friendly status strings.

        If `delta` is provided and the status indicates a rename, include the
        target path in the returned string (e.g. "renamed->new/path").
        """
        try:
            if status_code == pygit2.GIT_DELTA_ADDED:
                return "added"
            if status_code == pygit2.GIT_DELTA_MODIFIED:
                return "modified"
            if status_code == pygit2.GIT_DELTA_DELETED:
                return "deleted"
            if status_code == pygit2.GIT_DELTA_RENAMED:
                # Try to include the new path when available
                new_path = None
                try:
                    if delta is not None:
                        new_path = getattr(delta.new_file, "path", None) or getattr(delta, "new_path", None)
                except Exception as e:
                    new_path = None
                    self.printException(e, "_delta_status_to_str: extracting new_path failed")
                return f"renamed->{new_path}" if new_path else "renamed"
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
        # If this is a rename line, include the new-path in the status string
        try:
            if code.startswith("R") and len(parts) > 2:
                newp = parts[-1].strip()
                if newp:
                    status = f"renamed->{newp}"
        except Exception as e:
            self.printException(e, "_parse_git_name_status_line: including rename target failed")
        return (path, status)

    # END: _parse_git_name_status_line v1

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
                path, status = self._parse_git_name_status_line(line)
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            return results
        except Exception as e:
            self.printException(e, "_git_cli_name_status: unexpected failure")
            return []

    # END: _git_cli_name_status v1

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
                repo = self.pygit2_repo
                try:
                    prev_obj = repo.revparse_single(prev_hash)
                    curr_obj = repo.revparse_single(curr_hash)
                except Exception as e:
                    # Fall back to repo.get if revparse_single fails
                    try:
                        prev_obj = repo.get(prev_hash)
                    except Exception:
                        prev_obj = None
                    try:
                        curr_obj = repo.get(curr_hash)
                    except Exception:
                        curr_obj = None

                if prev_obj is None or curr_obj is None:
                    return []

                # Use pygit2 diff between the two commit/tree objects. Keep
                # ordering consistent with `git diff prev curr`.
                try:
                    diff = repo.diff(prev_obj, curr_obj)
                except Exception as e:
                    # If direct diff fails, try resolving to trees explicitly
                    a_tree = self._resolve_tree(prev_obj)
                    b_tree = self._resolve_tree(curr_obj)
                    if a_tree is None or b_tree is None:
                        self.printException(e, "getFileListBetweenTwoCommits: cannot resolve commits to trees for diff")
                        return []
                    diff = repo.diff(a_tree, b_tree)

                results: list[tuple[str, str]] = []
                for delta in diff.deltas:
                    try:
                        status_code = getattr(delta, "status", None)
                        old_path = getattr(delta.old_file, "path", None)
                        new_path = getattr(delta.new_file, "path", None)
                        if status_code == pygit2.GIT_DELTA_ADDED:
                            results.append((new_path or old_path, "added"))
                        elif status_code == pygit2.GIT_DELTA_DELETED:
                            results.append((old_path or new_path, "deleted"))
                        elif status_code == pygit2.GIT_DELTA_MODIFIED:
                            results.append((new_path or old_path, "modified"))
                        elif status_code == pygit2.GIT_DELTA_RENAMED:
                            tgt = new_path or old_path
                            # Guard against spurious rename-to-self cases where
                            # libgit2/pygit2 reports a rename but the old and
                            # new paths are identical. Treat these as
                            # modifications to avoid confusing "renamed->same".
                            if old_path and new_path and old_path == new_path:
                                results.append((tgt, "modified"))
                            else:
                                results.append((tgt, f"renamed->{tgt}" if tgt else "renamed"))
                        elif status_code == pygit2.GIT_DELTA_COPIED:
                            results.append((new_path or old_path, "copied"))
                        else:
                            # Fallback: treat as modified
                            results.append((new_path or old_path, "modified"))
                    except Exception as e:
                        self.printException(e, "getFileListBetweenTwoCommits: processing delta failed")
                        continue

                results.sort(key=lambda x: x[0])
                return results
            except Exception as e:
                self.printException(e, "getFileListBetweenTwoCommits: pygit2 simple diff failed")
                return []

        else:
            # git CLI fallback when not using pygit2 (commit -> commit)
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
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of files
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                detailed, a_raw, b_raw = self._pygit2_run_pygit2_diff(self.NEWREPO, curr_hash)
            except Exception as e:
                self.printException(e, "getFileListBetweenNewRepoAndHash: _pygit2_run_pygit2_diff failed")
                return []
            try:
                return self._deltas_to_results(detailed, a_raw, b_raw)
            except Exception as e:
                self.printException(e, "getFileListBetweenNewRepoAndHash: _deltas_to_results failed")
                return []

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
            repo = self.pygit2_repo
            try:
                head_tree = self.pygit2_resolve_token_to_tree("HEAD")
            except Exception as e:
                self.printException(e, "getFileListBetweenNewRepoAndStaged: token resolve failed")
                return []

            try:
                if head_tree is not None:
                    detailed, a_raw, b_raw = self._pygit2_run_pygit2_diff("HEAD", self.STAGED)
                else:
                    detailed, a_raw, b_raw = self._pygit2_run_pygit2_diff(self.NEWREPO, self.STAGED)
            except Exception as e:
                self.printException(e, "getFileListBetweenNewRepoAndStaged: _pygit2_run_pygit2_diff failed")
                return []

            try:
                return self._deltas_to_results(detailed, a_raw, b_raw)
            except Exception as e:
                self.printException(e, "getFileListBetweenNewRepoAndStaged: _deltas_to_results failed")
                return []

        # git CLI fallback when not using pygit2
        return self._git_cli_name_status(["git", "diff", "--name-status", "--cached"])

    # END: getFileListBetweenNewRepoAndStaged v1

    # BEGIN: getFileListBetweenNewRepoAndMods v1
    # make git-only
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

                # Build a mapping of working-tree files -> SHA1 hash (or mtime fallback)
                work_files: dict[str, dict] = {}
                status_map: dict[str, int] = {}
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
                        # Only skip the actual .git directory (not names like .github)
                        # Use os.sep to ensure we only match top-level .git or its children.
                        if rel == ".git" or rel.startswith(".git" + os.sep):
                            continue
                        # Query repo status early to avoid reading untracked files
                        st = None
                        try:
                            st = repo.status_file(rel)
                        except Exception as e:
                            # If status check fails, log and continue to attempt reading
                            self.printException(e, "getFileListBetweenNewRepoAndMods: status_file failed")
                        if st is not None:
                            status_map[rel] = st

                        # Compute a SHA-1 hash of the file contents; if reading
                        # fails, fall back to storing the file mtime so callers
                        # can decide conservatively later.
                        try:
                            hasher = hashlib.sha1()
                            with open(fp, "rb") as fh:
                                for chunk in iter(lambda: fh.read(8192), b""):
                                    hasher.update(chunk)
                            work_files[rel] = {"hash": hasher.hexdigest()}
                        except FileNotFoundError as _no_logging:
                            # File disappeared between os.walk and open; log and skip it
                            # self.printException(e, "getFileListBetweenNewRepoAndMods: file vanished during read")
                            continue
                        except Exception as _no_logging:
                            # self.printException(e, "getFileListBetweenNewRepoAndMods: reading file failed; falling back to mtime")
                            try:
                                mtime = os.path.getmtime(fp)
                                work_files[rel] = {"mtime": int(mtime)}
                            except Exception as e2:
                                self.printException(e2, "getFileListBetweenNewRepoAndMods: getting mtime failed")
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
                    # Prefer the previously-cached status when available to
                    # avoid redundant repo.status_file calls.
                    st = status_map.get(path)
                    if st is None:
                        try:
                            st = repo.status_file(path)
                        except Exception as e:
                            self.printException(e, "getFileListBetweenNewRepoAndMods: status_file failed")
                            continue

                    # Include untracked files (WT_NEW) as 'added' to match
                    # `git diff` semantics for initial->working-tree comparisons.
                    if st & getattr(pygit2, "GIT_STATUS_WT_NEW", 0):
                        s = "added"
                    else:
                        # Include only working-tree changes (mask may be 0)
                        if wt_mask and not (st & wt_mask):
                            continue

                    # Map working-tree status to human-friendly string
                    if st & getattr(pygit2, "GIT_STATUS_WT_DELETED", 0):
                        s = "deleted"
                    elif st & getattr(pygit2, "GIT_STATUS_WT_MODIFIED", 0):
                        s = "modified"
                    elif st & getattr(pygit2, "GIT_STATUS_WT_RENAMED", 0):
                        s = f"renamed->{path}"
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
        return self._git_cli_name_status(["git", "diff", "--name-status"]) 

    # END: getFileListBetweenNewRepoAndMods v1

    # BEGIN: getFileListBetweenTopHashAndCurrentTime v1
    def getFileListBetweenTopHashAndCurrentTime(self, usePyGit2: bool) -> list[str]:
        """Return a list of `(path, status)` for files changed between HEAD and working tree.

        Status will reflect the working-tree change type (modified/added/deleted).
        """
        # Delegate to the general handler to avoid duplicating logic
        return self.getFileListBetweenHashAndCurrentTime("HEAD", usePyGit2)

    # END: getFileListBetweenTopHashAndCurrentTime v1

    # BEGIN: _getCachedFileList v1
    def _getCachedFileList(self, key: str, git_args: list) -> list[tuple[str, str]]:
        """Run `git_args` (list) and cache the parsed name-status results under `key`.

        Returns a sorted list of `(path,status)`. On git failure returns [].
        """
        try:
            if not hasattr(self, "_cmd_cache"):
                self._cmd_cache = {}
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
                path, status = self._parse_git_name_status_line(line)
                if path:
                    results.append((path, status))
            results.sort(key=lambda x: x[0])
            self._cmd_cache[key] = results
            return results
        except Exception as e:
            self.printException(e, "_getCachedFileList: unexpected failure")
            return []
    # END: _getCachedFileList v1

    # BEGIN: getFileListBetweenHashAndCurrentTime v1
    def getFileListBetweenHashAndCurrentTime(self, hash: str, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return `(path,status)` for files changed between `hash` and working tree.

        Uses the git CLI plus a one-time cache via `_getCachedFileList`.
        """
        key = f"getFileListBetweenHashAndCurrentTime:{hash}"
        return self._getCachedFileList(key, ["git", "diff", "--name-status", hash])
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
                head_tree = self.pygit2_resolve_token_to_tree(hash)
            except Exception as e:
                self.printException(e, "getFileListBetweenHashAndStaged: token resolve failed")
                return []

            if head_tree is None:
                self.printException(ValueError("could not resolve tree for hash"), "_resolve_tree failed")
                return []

            try:
                detailed, a_raw, b_raw = self._pygit2_run_pygit2_diff(hash, self.STAGED)
            except Exception as e:
                self.printException(e, "getFileListBetweenHashAndStaged: _pygit2_run_pygit2_diff failed")
                return []

            try:
                return self._deltas_to_results(detailed, a_raw, b_raw)
            except Exception as e:
                self.printException(e, "getFileListBetweenHashAndStaged: _deltas_to_results failed")
                return []

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
    # make git-only
    def getFileListBetweenStagedAndMods(self, usePyGit2: bool) -> list[tuple[str, str]]:
        """Return a list of `(path, status)` for files changed between staged index and working tree (mods)."""
        # Use pygit2 if `usePyGit2` is True (throw an exception if pygit2 is not available)
        # Else use git CLI to get the list of files
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            try:
                detailed, a_raw, b_raw = self._pygit2_run_pygit2_diff(self.STAGED, self.MODS)
            except Exception as e:
                self.printException(e, "getFileListBetweenStagedAndMods: _pygit2_run_pygit2_diff failed")
                return []
            try:
                return self._deltas_to_results(detailed, a_raw, b_raw)
            except Exception as e:
                self.printException(e, "getFileListBetweenStagedAndMods: _deltas_to_results failed")
                return []

        else:
            # Use git CLI to get the list of files; cache the results once per process
            key = "getFileListBetweenStagedAndMods"
            return self._getCachedFileList(key, ["git", "diff", "--name-status"]) 

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
            if not hasattr(self, "_cmd_cache"):
                self._cmd_cache = {}
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
                rel = self._decode_git_quoted_path(rel)
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
                except FileNotFoundError:
                    continue
                except Exception as e:
                    self.printException(e, "getFileListUntrackedAndIgnored: stat failed")
                    mtime = None
                iso = self._epoch_to_iso(mtime) if mtime is not None else self.index_mtime_iso()
                results.append((rel, iso, "untracked"))

            for line in ignored_out.splitlines():
                rel = line.strip()
                rel = self._decode_git_quoted_path(rel)
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
                except FileNotFoundError:
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
                                            print(f"DEBUG: detailed delta: path={dd.get('path')} status={dd.get('status')} old_oid={dd.get('old_oid')} new_oid={dd.get('new_oid')}")
                                        except Exception:
                                            print(f"DEBUG: detailed delta repr: {dd!r}")
                                # Print raw tree refs (if available)
                                print(f"DEBUG: a_raw={a_raw!r} b_raw={b_raw!r}")

                                # Run the same post-processing used by the normal pygit2 path
                                try:
                                    processed = self._deltas_to_results(detailed, a_raw, b_raw)
                                    print(f"DEBUG: post-processed pygit2 results (from detailed) count={len(processed)}")
                                    if self.verbose > 0:
                                        for it in processed[:50]:
                                            try:
                                                print(f"DEBUG: post-processed: {it}")
                                            except Exception:
                                                print(f"DEBUG: post-processed repr: {it!r}")
                                    # Compare processed results to the previously computed `p`
                                    try:
                                        set_processed = set([x[0] for x in processed])
                                        set_p_orig = set([x[0] for x in p])
                                        only_proc = sorted(list(set_processed - set_p_orig))
                                        only_orig = sorted(list(set_p_orig - set_processed))
                                        print(f"DEBUG: paths only in post-processed (not in p): {only_proc[:10]}")
                                        print(f"DEBUG: paths only in original p (not in post-processed): {only_orig[:10]}")
                                    except Exception as e:
                                        self.printException(e, "runFileListSampledComparisons: comparing processed->p failed")
                                except Exception as e:
                                    self.printException(e, "runFileListSampledComparisons: post-processing detailed deltas failed")
                            except Exception as e:
                                self.printException(e, "runFileListSampledComparisons: fetching detailed pygit2 diff failed")
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

        When `usePyGit2` is True, use `pygit2` only and include a commit
        only when `file_name` appears in the diff between the commit and
        ALL of its parents (root commits compared to an empty tree).
        """
        if usePyGit2:
            if not pygit2:
                raise RuntimeError("pygit2 is not available")
            repo = self.pygit2_repo
            matches: list[tuple[str, str, str]] = []
            try:
                try:
                    head = repo.revparse_single("HEAD")
                except Exception as e:
                    self.printException(e, "getHashListFromFileName: revparse HEAD failed")
                    return []

                walker = repo.walk(head.id, pygit2.GIT_SORT_TIME)

                def path_in_diff(a_tree, b_tree, orig_a_none=False, orig_b_none=False) -> bool:
                    try:
                        if a_tree is None:
                            a_tree = self._empty_tree_for_repo(repo)
                        if b_tree is None:
                            b_tree = self._empty_tree_for_repo(repo)
                        if a_tree is None or b_tree is None:
                            return False
                        diff = repo.diff(a_tree, b_tree)
                        try:
                            # Only enable rename detection when both sides were real trees
                            if not orig_a_none and not orig_b_none:
                                flags = _pygit2_similarity_flags()
                                if flags:
                                    diff.find_similar(flags)
                        except Exception as e:
                            self.printException(e, "getHashListFromFileName: find_similar failed")
                    except Exception as e:
                        self.printException(e, "getHashListFromFileName: repo.diff failed")
                        return False
                    if not diff:
                        return False
                    for delta in getattr(diff, "deltas", []):
                        path = getattr(delta.new_file, "path", None) or getattr(delta.old_file, "path", None)
                        if path == file_name:
                            return True
                    return False

                for c in walker:
                    try:
                        parents = list(c.parents)
                        # Root commit: compare against empty tree
                        if not parents:
                            b_tree = self._resolve_tree(c)
                            if path_in_diff(None, b_tree, True, False):
                                matches.append(self._pygit2_format_commit_entry(repo, c))
                            continue

                        # For merges and normal commits: require path to appear
                        # in the diff versus every parent (differ-from-all).
                        b_tree = self._resolve_tree(c)
                        all_match = True
                        for p in parents:
                            a_tree = self._resolve_tree(p)
                            if not path_in_diff(a_tree, b_tree, False, False):
                                all_match = False
                                break
                        if all_match:
                            matches.append(self._pygit2_format_commit_entry(repo, c))
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
                entries.append((iso, h, subject if subject else ""))
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
def show_diffs(test_name: str, list1: list, list2: list, top: int = 0, raw: bool = False, verbose: int = 0, silent: bool = False) -> bool:
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
