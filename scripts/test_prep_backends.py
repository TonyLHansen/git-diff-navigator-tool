#!/usr/bin/env python3
"""Quick CLI to exercise FileModeFileList preparers and compare backends.

Usage:
  python scripts/test_prep_backends.py /path/to/repo

Outputs diffs to stdout and writes a summary to tmp/debug-prep-test.log
"""
from __future__ import annotations
import os
import sys
import argparse
import subprocess
import pprint
import difflib
from types import SimpleNamespace
import logging

# Ensure we can import the project module from workspace root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gitdiffnavtool

logger = logging.getLogger(__name__)

LOGPATH = os.path.join(ROOT, "tmp", "debug-prep-test.log")

try:
    import pygit2  # type: ignore
except Exception:
    pygit2 = None


def make_dummy(repo_root: str, pyg_repo=None):
    """Create a lightweight object providing attributes/methods used by the
    FileModeFileList preparer methods when bound as unbound functions.
    """
    class Dummy:
        def __init__(self, repo_root, pyg_repo):
            self.app = SimpleNamespace(repo_root=repo_root, pygit2_repo=pyg_repo)

        def printException(self, e: Exception, msg: str | None = None):
            # Log exceptions via module logger
            logger.warning("printException: %s %s", msg or "", e)

        def _run_cmd_log(self, cmd, label: str | None = None, text: bool = True, capture_output: bool = True):
            # Minimal wrapper to match AppBase behavior
            proc = subprocess.run(cmd, text=text, capture_output=capture_output)
            if proc.stderr:
                logger.warning("%s stderr: %s", label or "cmd", proc.stderr)
            return proc

    return Dummy(repo_root, pyg_repo)


def compare_lists(git_list, pyg_list, context: str) -> list[str]:
    g = pprint.pformat(git_list, width=120).splitlines()
    p = pprint.pformat(pyg_list, width=120).splitlines()
    if g == p:
        return []
    return list(difflib.unified_diff(g, p, fromfile='git', tofile='pygit2', lineterm=''))


def run(root: str, max_dirs: int | None = None) -> tuple[int, int]:
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        print(f"Not a directory: {root}")
        return 0, 0

    # Construct a `pygit2.Repository` for the repo root — `pygit2` is required.
    pyg_repo = pygit2.Repository(root)

    dummy = make_dummy(root, pyg_repo)

    # Unbound method references
    git_method = gitdiffnavtool.FileModeFileList._prepFileModeFileList_from_git
    pyg_method = gitdiffnavtool.FileModeFileList._prepFileModeFileList_from_pygit2
    status_map_fn = gitdiffnavtool.FileModeFileList._prepFileModeFileList_status_map_from_git

    dirs_seen = 0
    diffs_found = 0
    for dirpath, dirnames, filenames in os.walk(root):
            # Skip .git tree
            if os.path.basename(dirpath) == ".git":
                logger.debug("Skipping .git directory: %s", dirpath)
                continue
            # Only test directories within the repo root
            if not dirpath.startswith(root):
                logger.error("Skipping outside root: %s", dirpath)
                continue
            relpath = dirpath[len(root) + 1 :]
            if relpath == "":
                rel = ""
            else:
                rel = relpath

            logger.info("=== DIR: %s (rel=%s) ===", dirpath, rel)
            # build status_map
            try:
                status_map = status_map_fn(dummy, dirpath)
            except Exception as e:
                dummy.printException(e, "status_map failed")
                status_map = None

            git_succeeded = False
            pyg_succeeded = False
            try:
                git_list = git_method(dummy, dirpath, rel, status_map)
                git_succeeded = True
            except Exception as e:
                dummy.printException(e, "git_method failed")
                git_list = []

            try:
                pyg_list = pyg_method(dummy, dirpath, rel)
                pyg_succeeded = True
            except Exception as e:
                dummy.printException(e, "pyg_method failed")
                pyg_list = []


            if not(git_succeeded and pyg_succeeded):
                logger.error("Skipping diff: one or both methods failed in %s", dirpath)
                continue
            
            diffs = compare_lists(git_list, pyg_list, dirpath)
            if diffs:
                diffs_found += 1
                logger.error("DIFF FOUND for path %s:", dirpath)
                logger.info("%s", "\n".join(diffs))
            else:
                logger.info("outputs identical")

            dirs_seen += 1
            if max_dirs and dirs_seen >= max_dirs:
                break

    return dirs_seen, diffs_found


def main(argv=None):
    ap = argparse.ArgumentParser(description="Test FileModeFileList backends and diff outputs")
    ap.add_argument("path", help="Repository root path to test")
    ap.add_argument("--max", type=int, default=None, help="Max directories to check")
    ap.add_argument("-o", "--output", dest="output", default=LOGPATH, help="Log output file (default tmp/debug-prep-test.log)")
    ap.add_argument("-v", "--verbosity", dest="verbose", action="count", default=0, help="Increase verbosity (-v for INFO, -vv for DEBUG)")
    args = ap.parse_args(argv)

    # Fail fast if pygit2 is required but not importable at module load time.
    if not pygit2:
        print("pygit2 is required for this test script but the module could not be imported.")
        return 2

    # Prepare output path and file handler for logging
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    fh = logging.FileHandler(args.output, mode="w", encoding="utf-8")
    # Map verbosity count to logging level: 0 -> WARNING, 1 -> INFO, 2+ -> DEBUG
    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose == 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)
    logger.setLevel(level)

    dirs_seen, diffs_found = run(args.path, max_dirs=args.max)
    print(f"Checked {dirs_seen} directories; diffs in {diffs_found}; log: {args.output}")
    return 1 if diffs_found else 0


if __name__ == "__main__":
    sys.exit(main())
