#!/usr/bin/env python3

"""Harness around git CLI repository file listing methods."""

import argparse
import sys
import traceback
import os
import hashlib
import time

import codecs
from datetime import datetime, timezone
from subprocess import check_output, CalledProcessError
import io
import contextlib
import difflib

# GitRepo implementation moved into `gitdiffnavtool.py`; import it so
# this test harness continues to exercise the exact same class and
# methods without duplicating code.
from gitdiffnavtool import GitRepo

def runFileListSampledExercises(test_repo: GitRepo, raw: bool, limit: int) -> int:
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
                printResults(test_repo, f"EXERCISE: {a}->{b}", res, raw, limit)
            except Exception as e:
                test_repo.printException(e, f"runFileListSampledExercises: handler failed for {a}->{b}")

    return total


def printResults(test_repo: GitRepo, label: str, res, raw: bool, limit: int) -> None:
    """Pretty-print results returned from GitRepo methods.

    - `label` is a short description printed as a header.
    - `res` may be a list (of tuples) or another value.
    - `raw` forces printing raw tuple/list reprs.
    - `limit` bounds printed entries when `res` is a list.
    """
    try:
        count = len(res) if hasattr(res, "__len__") else 1
        header = f"{label} returned {count} entries:"
        print(f"\n{header}")

        if raw:
            # Raw printing: show Python reprs
            if isinstance(res, list):
                for it in res[:limit]:
                    print(repr(it))
            else:
                print(repr(res))
            return

        # Default formatted printing (fallback to repr for unknown types)
        if isinstance(res, list):
            for it in res[:limit]:
                try:
                    print(repr(it))
                except Exception as e:
                    test_repo.printException(e, "printResults: item repr failed")
                    print(str(it))
        else:
            try:
                print(repr(res))
            except Exception as e:
                test_repo.printException(e, "printResults: repr failed")
                print(str(res))
    except Exception as e:
        test_repo.printException(e, "printResults: unexpected failure")
        try:
            print(repr(res))
        except Exception as e:
            test_repo.printException(e, "printResults: fallback repr failed")


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

    parser.add_argument(
        "-T",
        "--timing",
        action="store_true",
        dest="timing",
        help="Print timing for each run",
    )

    parser.add_argument(
        "--capture",
        metavar="DIR",
        help="Capture outputs for exercised tests into directory DIR",
        default=None,
    )

    parser.add_argument(
        "--test",
        metavar="DIR",
        help="Compare current outputs against captured files in DIR",
        default=None,
    )

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

    # Alias for legacy option name used in some call sites
    parser.add_argument(
        "--runFileListSampledComparisons",
        action="store_true",
        dest="runFileListSampledComparisons",
        help="Run runFileListSampledComparisons (alias)",
    )

    parser.add_argument(
        "-g",
        "--test-resolve",
        action="store_true",
        dest="test_resolve",
        help="Exercise resolve_repo_top and relpath_if_within for quick verification",
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
    # a `GitRepo` instance so the helper can be defined once and reused.
    def run_one(test_repo, i: int, name: str, func_name: str, fname: str | None, limit: int) -> bool:
        # Debug: report which test function is being invoked
        if args.verbose > 1:
            print(f"DEBUG: run_one invoking {func_name} (display name: {name})")
        # Run the test function once (git-CLI implementation) and print results.
        # Capture stdout for optional capture/compare behavior
        buf = io.StringIO()
        success = False
        try:
            with contextlib.redirect_stdout(buf):
                if fname is not None:
                    t0 = time.perf_counter()
                    res = getattr(test_repo, func_name)(fname)
                    t1 = time.perf_counter()
                else:
                    t0 = time.perf_counter()
                    res = getattr(test_repo, func_name)()
                    t1 = time.perf_counter()
                dur = t1 - t0
                count_str = f"{len(res) if hasattr(res, '__len__') else '1'}"
                if args.timing:
                    print(f"RUN: {func_name} {name} returned {count_str} entries (t={dur:.3f}s)")
                # Delegate printing to helper to keep output consistent
                try:
                    printResults(test_repo, f"RUN: {func_name} {name}", res, args.raw, limit)
                except Exception as e:
                    test_repo.printException(e, "run_one: printing result failed")
            success = True
        except Exception as e:
            # printException prints to logger; still capture current stdout
            test_repo.printException(e, f"run_one invocation of {func_name} failed")
            success = False

        out_str = buf.getvalue()

        # Always print current output to stdout so behavior is unchanged
        try:
            if out_str:
                print(out_str, end="")
        except Exception as _:
            test_repo.printException(_, "run_one: printing stdout failed")

        # If capture dir specified, save output to file named after func and flags
        if getattr(args, "capture", None):
            try:
                capdir = args.capture
                os.makedirs(capdir, exist_ok=True)
                flags: list[str] = []
                if getattr(args, "raw", False):
                    flags.append("raw")
                if getattr(args, "timing", False):
                    flags.append("timing")
                suffix = ("-" + "-".join(flags)) if flags else ""
                capfile = os.path.join(capdir, f"{func_name}{suffix}.txt")
                with open(capfile, "w", encoding="utf-8") as f:
                    f.write(out_str)
            except Exception as e:
                test_repo.printException(e, "run_one: capturing output failed")

        # If test dir specified, compare current output to captured file
        if getattr(args, "test", None):
            try:
                testdir = args.test
                flags: list[str] = []
                if getattr(args, "raw", False):
                    flags.append("raw")
                if getattr(args, "timing", False):
                    flags.append("timing")
                suffix = ("-" + "-".join(flags)) if flags else ""
                testfile = os.path.join(testdir, f"{func_name}{suffix}.txt")
                if not os.path.exists(testfile):
                    print(f"TEST-MISSING: expected capture file not found: {testfile}")
                    return False
                with open(testfile, "r", encoding="utf-8") as f:
                    expected = f.read()
                # compute unified diff
                diff_lines = list(difflib.unified_diff(
                    expected.splitlines(keepends=True),
                    out_str.splitlines(keepends=True),
                    fromfile=f"expected/{func_name}",
                    tofile=f"current/{func_name}",
                ))
                if diff_lines:
                    print(f"TEST-DIFF for {func_name}:")
                    for ln in diff_lines:
                        print(ln, end="")
                    return False
            except Exception as e:
                test_repo.printException(e, "run_one: test comparison failed")
                return False

        return success

    # If no specific flags provided, default to running all exercises
    any_flag = (
        args.getFileListBetweenNewAndTopHash
        or args.getFileListBetweenTopHashAndCurrentTime
        or args.getFileListBetweenTopHashAndStaged
        or args.getFileListBetweenStagedAndMods
        or args.getFileListBetweenNewAndStaged
        or args.getFileListBetweenNewAndMods
        or args.getHashListEntireRepo
        or args.getHashListStagedChanges
        or args.getHashListFromFileName
        or args.getHashListNewChanges
        or args.getHashListComplete
        or args.getHashListSample
        or args.getHashListSamplePlusEnds
        or args.getFileListUntrackedAndIgnored
        or args.runFileListSampledComparisons
        or args.getFileListBetweenNormalizedHashes
        or args.test_resolve
    )
    if not any_flag and not args.all:
        parser.error("No test functions specified; use -A to run all tests or specify one or more test flags.")

    total_exercises = 0

    for path in args.path:
        print(f"\n== Repository: {path} ==")
        try:
            test_repo = GitRepo(path)
        except Exception as _use_stderr:
            print(f"ERROR: initializing GitRepo for {path} failed: {_use_stderr}")
            continue

        if args.test_resolve:
            total_exercises += 1
            # Test resolve_repo_top (returns (out, err))
            out, err = GitRepo.resolve_repo_top(path, raise_on_missing=False)
            if out:
                print(f"resolve_repo_top: {path} -> {out}")
            else:
                print(f"resolve_repo_top: {path} -> FAILED: {err}")

            # Test relpath_if_within using the configured file (args.file)
            total_exercises += 1
            try:
                rel = GitRepo.relpath_if_within(out, path)
                print(f"relpath_if_within: base={out}, relpath={path} -> {rel}")
            except Exception as _use_stderr:
                print(f"relpath_if_within: base={out}, relpath={path} -> FAILED: {e}")

            # Test relpath_if_within using the configured file (args.file)
            total_exercises += 1
            relpath = args.file
            try:
                rel = GitRepo.relpath_if_within(out, relpath)
                print(f"relpath_if_within: base={out}, relpath={relpath} -> {rel}")
            except Exception as _use_stderr:
                print(f"relpath_if_within: base={out}, relpath={relpath} -> FAILED: {e}")

            # Test relpath_if_within using the configured file (args.file)
            total_exercises += 1
            relpath = path + os.path.sep + args.file
            try:
                rel = GitRepo.relpath_if_within(out, relpath)
                print(f"relpath_if_within: base={out}, relpath={relpath} -> {rel}")
            except Exception as _use_stderr:
                print(f"relpath_if_within: base={out}, relpath={relpath} -> FAILED: {e}")

        # Execute tests directly in the same order previously provided by `allfuncs`.
        i = 1

        if args.all or args.getFileListBetweenNewAndTopHash:
            total_exercises += 1
            run_one(test_repo, i, "-1, File List New to Top Hash", "getFileListBetweenNewRepoAndTopHash", None, args.limit)
            i += 1

        if args.all or args.getFileListBetweenTopHashAndCurrentTime:
            total_exercises += 1
            run_one(test_repo, i, "File List Between TopHash and Current Time", "getFileListBetweenTopHashAndCurrentTime", None, args.limit)
            i += 1

        if args.all or args.getFileListBetweenTopHashAndStaged:
            total_exercises += 1
            run_one(test_repo, i, "-2, File List Between TopHash and Current Time", "getFileListBetweenTopHashAndStaged", None, args.limit)
            i += 1

        if args.all or args.getFileListBetweenStagedAndMods:
            total_exercises += 1
            run_one(test_repo, i, "-3, File List Between Staged and Mods", "getFileListBetweenStagedAndMods", None, args.limit)
            i += 1

        if args.all or args.getFileListBetweenNewAndStaged:
            total_exercises += 1
            run_one(test_repo, i, "-4, File List New to Staged", "getFileListBetweenNewRepoAndStaged", None, args.limit)
            i += 1

        if args.all or args.getFileListBetweenNewAndMods:
            total_exercises += 1
            run_one(test_repo, i, "-5, File List New to Mods", "getFileListBetweenNewRepoAndMods", None, args.limit)
            i += 1

        if args.all or args.getHashListEntireRepo:
            total_exercises += 1
            run_one(test_repo, i, "-6, Hash List Entire Repo", "getHashListEntireRepo", None, args.limit)
            i += 1

        if args.all or args.getHashListStagedChanges:
            total_exercises += 1
            run_one(test_repo, i, "-7, Hash List Staged Changes", "getHashListStagedChanges", None, args.limit)
            i += 1

        if args.all or args.getHashListFromFileName:
            total_exercises += 1
            run_one(test_repo, i, f"-8, Hash List From File {args.file}", "getHashListFromFileName", args.file, args.limit)
            i += 1

        # Two separate entries mapping to the same function (preserve original ordering)
        if args.all or args.getHashListNewChanges:
            total_exercises += 1
            run_one(test_repo, i, "-9, Hash List New Changes", "getHashListNewChanges", None, args.limit)
            i += 1

        if args.all or args.getHashListNewChanges:
            total_exercises += 1
            run_one(test_repo, i, "-a, Hash List New Changes", "getHashListNewChanges", None, args.limit)
            i += 1

        if args.all or args.getHashListComplete:
            total_exercises += 1
            run_one(test_repo, i, "-b, Hash List Complete", "getHashListComplete", None, args.limit)
            i += 1

        if args.all or args.getHashListSample:
            total_exercises += 1
            run_one(test_repo, i, "-c, Hash List Sample", "getHashListSample", None, args.limit)
            i += 1

        if args.all or args.getHashListSamplePlusEnds:
            total_exercises += 1
            run_one(test_repo, i, "-d, Hash List Sample Plus Ends", "getHashListSamplePlusEnds", None, args.limit)
            i += 1

        if args.all or args.getFileListUntrackedAndIgnored:
            total_exercises += 1
            run_one(test_repo, i, "-e, Untracked and Ignored files", "getFileListUntrackedAndIgnored", None, args.limit)
            i += 1

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
                    except Exception as e:
                        test_repo.printException(e, f"invoking getFileListBetweenNormalizedHashes for {pair} failed")
                    # We treat these as exercises; successes/failures are logged but not tallied.
                except Exception as e:
                    test_repo.printException(e, f"processing getFileListBetweenNormalizedHashes option '{pair}' failed")

        # If requested, run sampled comparisons separately (outside the to_run loop)
        if args.all or args.runFileListSampledComparisons:
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

