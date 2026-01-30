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
import urllib.parse

# GitRepo implementation moved into `gitdiffnavtool.py`; import it so
# this test harness continues to exercise the exact same class and
# methods without duplicating code.
from gitdiffnavtool import GitRepo

def runFileListSampledExercises(test_repo: GitRepo, raw: bool, limit: int, silent: bool = False) -> int:
    """Module-level exerciser for `getFileListBetweenNormalizedHashes`.

    Calls the dispatch logic for all sampled token pairs and prints a
    bounded sample of results. Returns the total number of exercised
    token pairs.
    """
    sample = getHashListSamplePlusEnds(test_repo)
    tokens: list = [x[1] for x in sample]
    tokens.reverse()
    if not silent:
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


def _safe_name_for_capture(name: str) -> str:
    """Produce a filesystem-safe representation of `name` for capture/test filenames."""
    if not name:
        return ""
    # Percent-encode to avoid path separators or odd characters
    return urllib.parse.quote_plus(name, safe="")


def getHashListSample(repo: GitRepo) -> list[tuple[str, str, str]]:
    """
    Returns a sampled list of commit tuples (iso, hash, subject) in
    newest-to-oldest order.
    """
    entire = repo.getHashListEntireRepo()
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


def getHashListSamplePlusEnds(repo: GitRepo) -> list[tuple[str, str, str]]:
    """
    Order: MODS, STAGED, sampled commits (newest->oldest), NEWREPO
    """
    sampleHashes: list[tuple[str, str, str]] = []

    # Put working-tree (MODS) first when present
    mods = repo.getHashListNewChanges()
    if mods:
        sampleHashes += mods

    # Then staged marker
    staged = repo.getHashListStagedChanges()
    if staged:
        sampleHashes += staged

    # Then the sampled commits (getHashListSample returns newest->oldest)
    normalHashes = getHashListSample(repo)
    if normalHashes:
        sampleHashes += normalHashes

    # Place NEWREPO pseudo-entry last using centralized helper
    sampleHashes += repo.getHashListNewRepo()
    return sampleHashes


def runGetDiffTests(test_repo: GitRepo, file_name: str, raw: bool, limit: int, silent: bool = False) -> int:
    """Run getDiff for all older->newer pairs for `file_name`.

    Produces DIFF outputs via `printResults` so the harness can capture
    and compare them. Returns the total number of diff invocations run.
    """
    if not file_name:
        if not silent:
            print("runGetDiffTests: no file specified via --file; skipping")
        return 0

    # Build a chronological list of refs for this file (oldest->newest).
    refs: list[str] = [test_repo.NEWREPO]
    try:
        entries = test_repo.getHashListFromFileName(file_name)
        # entries are returned newest->oldest; reverse to oldest->newest
        for iso, h, subj in reversed(entries):
            if h not in refs:
                refs.append(h)
    except Exception as e:
        test_repo.printException(e, "runGetDiffTests: getting hash list failed")
        return 0

    # Print the list of refs used for diffs (oldest->newest)
    if not silent:
        print(f"Refs (oldest->newest)={refs}")

    # If there are fewer than 2 refs, nothing to diff
    if len(refs) < 2:
        if not silent:
            print(f"runGetDiffTests: insufficient refs for {file_name}; refs={refs}")
        return 0

    total = 0
    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            older = refs[i]
            newer = refs[j]
            total += 1
            try:
                diff_lines = test_repo.getDiff(file_name, older, newer)
                printResults(test_repo, f"DIFF: {older}->{newer}", diff_lines, raw, limit)
            except Exception as e:
                test_repo.printException(e, f"runGetDiffTests: handler failed for {older}->{newer}")

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

    parser.add_argument(
        "--getDiffTests",
        action="store_true",
        dest="getDiffTests",
        help="Run getDiff combinations for the file specified by -F/--file",
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


    # Tally of test comparison successes/failures and record names.
    stats = {"succ": 0, "fail": 0, "succ_names": [], "fail_names": []}

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
                # Prefer method on the test_repo instance; fall back to
                # module-level helper functions that accept the repo as
                # their first parameter (these were moved out of GitRepo).
                t0 = time.perf_counter()
                if hasattr(test_repo, func_name):
                    if fname is not None:
                        res = getattr(test_repo, func_name)(fname)
                    else:
                        res = getattr(test_repo, func_name)()
                else:
                    # Attempt to call a module-level function
                    fn = globals().get(func_name)
                    if fn is None:
                        raise AttributeError(f"no function or method named {func_name}")
                    if fname is not None:
                        res = fn(test_repo, fname)
                    else:
                        res = fn(test_repo)
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

        # Print current output only when not running in silent mode; keep
        # `out_str` available for capture/test comparison regardless.
        try:
            if out_str and not args.silent:
                print(out_str, end="")
        except Exception as _:
            test_repo.printException(_, "run_one: printing stdout failed")

        # If capture dir specified, save output to file named after func and flags
        if args.capture:
            try:
                capdir = args.capture
                os.makedirs(capdir, exist_ok=True)
                flags: list[str] = []
                if args.raw:
                    flags.append("raw")
                if args.timing:
                    flags.append("timing")
                suffix = ("-" + "-".join(flags)) if flags else ""
                if fname:
                    safe = _safe_name_for_capture(fname)
                    capfile = os.path.join(capdir, f"{func_name}--{safe}{suffix}.txt")
                else:
                    capfile = os.path.join(capdir, f"{func_name}{suffix}.txt")
                with open(capfile, "w", encoding="utf-8") as f:
                    f.write(out_str)
            except Exception as e:
                test_repo.printException(e, "run_one: capturing output failed")

        # If test dir specified, compare current output to captured file
        if args.test:
            try:
                testdir = args.test
                flags: list[str] = []
                if args.raw:
                    flags.append("raw")
                if args.timing:
                    flags.append("timing")
                suffix = ("-" + "-".join(flags)) if flags else ""
                if fname:
                    safe = _safe_name_for_capture(fname)
                    testfile = os.path.join(testdir, f"{func_name}--{safe}{suffix}.txt")
                else:
                    testfile = os.path.join(testdir, f"{func_name}{suffix}.txt")
                if not os.path.exists(testfile):
                    print(f"TEST-MISSING: expected capture file not found: {testfile}")
                    success = False
                else:
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
                    print("vvvvvvvvvvvv")
                    for ln in diff_lines:
                        print(ln, end="")
                    print("^^^^^^^^^^^^")
                    success = False
            except Exception as e:
                test_repo.printException(e, "run_one: test comparison failed")
                return False

        # Update global stats for this test invocation and record the name
        try:
            if success:
                stats["succ"] += 1
                stats["succ_names"].append(func_name)
            else:
                stats["fail"] += 1
                stats["fail_names"].append(func_name)
        except Exception as e:
            test_repo.printException(e, "run_one: updating stats failed")

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
        or args.getDiffTests
        or args.getFileListBetweenNormalizedHashes
        or args.test_resolve
    )
    if not any_flag and not args.all:
        parser.error("No test functions specified; use -A to run all tests or specify one or more test flags.")

    total_exercises = 0

    for path in args.path:
        if not args.silent:
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
            run_one(test_repo, i, "-2, File List Between TopHash and Current Time", "getFileListBetweenTopHashAndCurrentTime", None, args.limit)
            i += 1

        if args.all or args.getFileListBetweenTopHashAndStaged:
            total_exercises += 1
            run_one(test_repo, i, "-3, File List Between TopHash and Staged", "getFileListBetweenTopHashAndStaged", None, args.limit)
            i += 1

        if args.all or args.getFileListBetweenStagedAndMods:
            total_exercises += 1
            run_one(test_repo, i, "-4, File List Between Staged and Mods", "getFileListBetweenStagedAndMods", None, args.limit)
            i += 1

        if args.all or args.getFileListBetweenNewAndStaged:
            total_exercises += 1
            run_one(test_repo, i, "-5, File List New to Staged", "getFileListBetweenNewRepoAndStaged", None, args.limit)
            i += 1

        if args.all or args.getFileListBetweenNewAndMods:
            total_exercises += 1
            run_one(test_repo, i, "-6, File List New to Mods", "getFileListBetweenNewRepoAndMods", None, args.limit)
            i += 1

        if args.all or args.getHashListEntireRepo:
            total_exercises += 1
            run_one(test_repo, i, "-7, Hash List Entire Repo", "getHashListEntireRepo", None, args.limit)
            i += 1

        if args.all or args.getHashListStagedChanges:
            total_exercises += 1
            run_one(test_repo, i, "-8, Hash List Staged Changes", "getHashListStagedChanges", None, args.limit)
            i += 1

        if args.all or args.getHashListFromFileName:
            total_exercises += 1
            run_one(test_repo, i, f"-9, Hash List From File {args.file}", "getHashListFromFileName", args.file, args.limit)
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
            # Capture the sampled comparisons output so --capture and --test work
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    print("\nRunning sampled pairwise comparisons (separate)...")
                    # runFileListSampledExercises returns total exercises run
                    # Always capture the full output for `--test`/`--capture` comparisons
                    # (do not pass the `silent` flag into the worker so captured
                    # output is identical whether --silent is used or not).
                    t = runFileListSampledExercises(test_repo, args.raw, args.limit, False)
                    total_exercises += t
                success_sampled = True
            except Exception as e:
                test_repo.printException(e, "running runFileListSampledExercises failed")
                success_sampled = False

            out_str = buf.getvalue()
            try:
                if out_str and not args.silent:
                    print(out_str, end="")
            except Exception as _:
                test_repo.printException(_, "printing sampled comparisons output failed")

            # If capture dir specified, save sampled comparisons output
            if args.capture:
                try:
                    capdir = args.capture
                    os.makedirs(capdir, exist_ok=True)
                    flags: list[str] = []
                    if args.raw:
                        flags.append("raw")
                    if args.timing:
                        flags.append("timing")
                    suffix = ("-" + "-".join(flags)) if flags else ""
                    capfile = os.path.join(capdir, f"runFileListSampledComparisons{suffix}.txt")
                    with open(capfile, "w", encoding="utf-8") as f:
                        f.write(out_str)
                except Exception as e:
                    test_repo.printException(e, "capturing sampled comparisons output failed")

            # If test dir specified, compare sampled comparisons output to captured file
            if args.test:
                try:
                    testdir = args.test
                    flags: list[str] = []
                    if args.raw:
                        flags.append("raw")
                    if args.timing:
                        flags.append("timing")
                    suffix = ("-" + "-".join(flags)) if flags else ""
                    testfile = os.path.join(testdir, f"runFileListSampledComparisons{suffix}.txt")
                    if not os.path.exists(testfile):
                        print(f"TEST-MISSING: expected capture file not found: {testfile}")
                        try:
                            stats["fail"] += 1
                            stats["fail_names"].append("runFileListSampledComparisons")
                        except Exception as e:
                            test_repo.printException(e, "runFileListSampledComparisons: recording missing-test failure failed")
                    else:
                        with open(testfile, "r", encoding="utf-8") as f:
                            expected = f.read()
                        diff_lines = list(difflib.unified_diff(
                            expected.splitlines(keepends=True),
                            out_str.splitlines(keepends=True),
                            fromfile=f"expected/runFileListSampledComparisons",
                            tofile=f"current/runFileListSampledComparisons",
                        ))
                        if diff_lines:
                            print(f"TEST-DIFF for runFileListSampledComparisons:")
                            print("vvvvvvvv")
                            for ln in diff_lines:
                                print(ln, end="")
                            print("^^^^^^^^")
                            try:
                                stats["fail"] += 1
                                stats["fail_names"].append("runFileListSampledComparisons")
                            except Exception as e:
                                test_repo.printException(e, "runFileListSampledComparisons: recording diff failure failed")
                        else:
                            try:
                                stats["succ"] += 1
                                stats["succ_names"].append("runFileListSampledComparisons")
                            except Exception as e:
                                test_repo.printException(e, "runFileListSampledComparisons: recording success failed")
                        
                except Exception as e:
                    test_repo.printException(e, "test comparison for sampled comparisons failed")

        # If requested, run getDiff combination tests for the configured file
        if args.all or args.getDiffTests:
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    print(f"\nRunning getDiff combinations for file {args.file}...")
                    t = runGetDiffTests(test_repo, args.file, args.raw, args.limit, False)
                    total_exercises += t
                success_getdiff = True
            except Exception as e:
                test_repo.printException(e, "running runGetDiffTests failed")
                success_getdiff = False

            out_str = buf.getvalue()
            try:
                if out_str and not args.silent:
                    print(out_str, end="")
            except Exception as _:
                test_repo.printException(_, "printing getDiffTests output failed")

            # If capture dir specified, save getDiffTests output
            if args.capture:
                try:
                    capdir = args.capture
                    os.makedirs(capdir, exist_ok=True)
                    flags: list[str] = []
                    if args.raw:
                        flags.append("raw")
                    if args.timing:
                        flags.append("timing")
                    suffix = ("-" + "-".join(flags)) if flags else ""
                    capfile = os.path.join(capdir, f"runGetDiffTests{suffix}.txt")
                    with open(capfile, "w", encoding="utf-8") as f:
                        f.write(out_str)
                except Exception as e:
                    test_repo.printException(e, "capturing getDiffTests output failed")

            # If test dir specified, compare getDiffTests output to captured file
            if args.test:
                try:
                    testdir = args.test
                    flags: list[str] = []
                    if args.raw:
                        flags.append("raw")
                    if args.timing:
                        flags.append("timing")
                    suffix = ("-" + "-".join(flags)) if flags else ""
                    testfile = os.path.join(testdir, f"runGetDiffTests{suffix}.txt")
                    if not os.path.exists(testfile):
                        print(f"TEST-MISSING: expected capture file not found: {testfile}")
                        try:
                            stats["fail"] += 1
                            stats["fail_names"].append("runGetDiffTests")
                        except Exception as e:
                            test_repo.printException(e, "runGetDiffTests: recording missing-test failure failed")
                    else:
                        with open(testfile, "r", encoding="utf-8") as f:
                            expected = f.read()
                        diff_lines = list(difflib.unified_diff(
                            expected.splitlines(keepends=True),
                            out_str.splitlines(keepends=True),
                            fromfile=f"expected/runGetDiffTests",
                            tofile=f"current/runGetDiffTests",
                        ))
                        if diff_lines:
                            print(f"TEST-DIFF for runGetDiffTests:")
                            print("vvvvvvvv")
                            for ln in diff_lines:
                                print(ln, end="")
                            print("^^^^^^^^")
                            try:
                                stats["fail"] += 1
                                stats["fail_names"].append("runGetDiffTests")
                            except Exception as e:
                                test_repo.printException(e, "runGetDiffTests: recording diff failure failed")
                        else:
                            try:
                                stats["succ"] += 1
                                stats["succ_names"].append("runGetDiffTests")
                            except Exception as e:
                                test_repo.printException(e, "runGetDiffTests: recording success failed")

                except Exception as e:
                    test_repo.printException(e, "test comparison for getDiffTests failed")

    # Final summary
    print(f"\nExercise summary: total_exercises={total_exercises}")
    passed = stats.get("succ", 0)
    failed = stats.get("fail", 0)
    print(f"Test comparisons: passed={passed} failed={failed}")
    print("Failed tests:", ", ".join(stats.get("fail_names")))
    print("Passed tests:", ", ".join(stats.get("succ_names")))


if __name__ == "__main__":
    main()

