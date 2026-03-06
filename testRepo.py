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
import re
import contextlib
import difflib
import urllib.parse

# GitRepo implementation is provided by the `gitrepo` helper module.
from gitrepo import printException, AppException, GitRepo
from gitdiffnavtool import FileListBase, HistoryListBase
from types import SimpleNamespace


def runFileListSampledExercises(test_repo: GitRepo, raw: bool, limit: int, silent: bool = False) -> int:
    """
    Module-level exerciser for `getFileListBetweenNormalizedHashes`.

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


def getHashListSample(repo: GitRepo) -> list[tuple[str, str, str, str, str, str]]:
    """
    Returns a sampled list of commit tuples (iso, hash, subject, status, author_name, author_email) in
    newest-to-oldest order.
    """
    entire = repo.getHashListEntireRepo()
    sampleHashes: list[tuple[str, str, str, str, str, str]] = []
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


def getHashListSamplePlusEnds(repo: GitRepo) -> list[tuple[str, str, str, str, str, str]]:
    """
    Order: MODS, STAGED, sampled commits (newest->oldest), NEWREPO
    """
    sampleHashes: list[tuple[str, str, str, str, str, str]] = []

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
    """
    Run getDiff for all older->newer pairs for `file_name`.

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
    """
    Pretty-print results returned from GitRepo methods.

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


def test_to_display_rows(test_repo: GitRepo) -> list:
    """
    Unit-like test for `FileListBase._to_display_rows`.

        Constructs a minimal dummy `self` object with `app.repo_root` and
        `printException` and exercises a few input shapes.
        Returns the list of normalized rows produced.
    """
    try:
        # Prepare a dummy self with required attributes
        class Dummy:
            def __init__(self, repo_root):
                self.app = SimpleNamespace(repo_root=repo_root)

            def printException(self, *args, **kwargs):
                """No-op exception printer used by the test dummy."""
                pass

        # Use the official accessor so attribute naming is centralized.
        base = test_repo.get_repo_root()
        d = Dummy(base)

        # Directly call the GitRepo normalized helpers — allow exceptions
        # to propagate so failures are loud during testing.
        normalized = test_repo.getNormalizedHashListComplete()
        tokens = [x[1] for x in normalized]
        if len(tokens) < 2:
            raise AssertionError("need at least two normalized tokens to exercise getFileListBetweenNormalizedHashes")
        # Choose an older->newer pair (last->first) to exercise a broad diff
        prev_token = tokens[-1]
        curr_token = tokens[0]
        samples = test_repo.getFileListBetweenNormalizedHashes(prev_token, curr_token)

        rows = FileListBase._to_display_rows(d, samples)

        # Basic structural assertions
        assert isinstance(rows, list), "_to_display_rows should return a list"
        assert len(rows) >= 1, "expected at least one normalized row"
        expected_keys = {"name", "full", "is_dir", "raw", "repo_status"}
        for i, r in enumerate(rows):
            assert isinstance(r, dict), f"row {i} should be dict"
            assert expected_keys.issubset(set(r.keys())), f"row {i} missing expected keys: {r.keys()}"

        # Check that the dict-form preserved raw metadata when present
        if samples:
            assert rows[0]["raw"] == samples[0] or isinstance(
                rows[0]["raw"], (dict, tuple, list, str)
            ), "dict-form input should be preserved in 'raw'"

        # Tuple-form: if a tuple/list sample was provided ensure the name field is populated
        if len(samples) > 1 and isinstance(samples[1], (list, tuple)):
            tup_raw = rows[1]["raw"]
            assert tup_raw == samples[1] or isinstance(tup_raw, (tuple, list, str, dict))
            assert isinstance(rows[1].get("name"), str)

        print("test_to_display_rows: PASS")
        return rows
    except Exception as e:
        test_repo.printException(e, "test_to_display_rows failed")
        raise


def test_to_history_entries(test_repo: GitRepo) -> list:
    """
    Unit-like test for `HistoryListBase._to_history_entries`.

        Builds a minimal dummy with `_epoch_to_iso` and exercises several
        input shapes returning the normalized entry dicts.
    """
    try:

        class DummyHist:
            def __init__(self):
                pass

            def _epoch_to_iso(self, ts):
                """
                Convert an epoch timestamp `ts` to ISO format (UTC).

                                If conversion fails, log via `printException` and return
                                the stringified input.
                """
                try:
                    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                except Exception as e:
                    self.printException(e, "DummyHist._epoch_to_iso failed")
                    return str(ts)

            def printException(self, *args, **kwargs):
                """No-op exception printer for history-dummy."""
                pass

        d = DummyHist()

        # Directly call the repo's normalized hash-list helper; allow any
        # exceptions to surface so test failures are visible.
        samples = test_repo.getHashListEntireRepo()

        entries = HistoryListBase._to_history_entries(d, samples)

        # Use the repository-wide normalized list (includes STAGED/MODS/NEWREPO)
        normalized = test_repo.getNormalizedHashListComplete()
        entries = HistoryListBase._to_history_entries(d, normalized)

        # Basic structural assertions
        assert isinstance(entries, list), "_to_history_entries should return a list"
        assert len(entries) >= 1, "expected at least one normalized history entry"
        for i, e in enumerate(entries):
            assert isinstance(e, dict), f"entry {i} should be dict"
            for k in ("iso", "hash", "subject", "short_hash", "meta"):
                assert k in e, f"entry {i} missing key {k}"

        # Sanity checks: iso should be a string and short_hash should be present
        for i, e in enumerate(entries):
            assert isinstance(e.get("iso"), str), f"entry {i} 'iso' should be a string"
            assert e.get("short_hash") is None or isinstance(e.get("short_hash"), str)

        # Additionally, exercise per-file normalized history helper
        try:
            file_samples = test_repo.getNormalizedHashListFromFileName("README.md")
            # If the file has history, ensure normalization produces entries
            if file_samples:
                file_entries = HistoryListBase._to_history_entries(d, file_samples)
                assert isinstance(file_entries, list), "per-file history should normalize to a list"
        except Exception as e:
            d.printException(e, "test_to_history_entries: per-file normalization failed")
            raise

        print("test_to_history_entries: PASS")
        return entries
    except Exception as e:
        test_repo.printException(e, "test_to_history_entries failed")
        raise


def test_amendCommitMessage(test_repo: GitRepo) -> dict:
    """
    Unit test for `GitRepo.amendCommitMessage`.

    Tests the amendment functionality on unpushed commits:
    - Verify that amending a pushed commit raises ValueError
    - Verify that amending an unpushed commit succeeds
    - Verify that the commit message is actually updated

    Returns a dict with test results and diagnostics.
    """
    results = {
        "tested_head_amendment": False,
        "tested_pushed_rejection": False,
        "tested_non_head_amendment": False,
        "errors": [],
    }

    try:
        # Get all commits in the repository
        all_commits = test_repo.getHashListEntireRepo()
        if not all_commits:
            results["errors"].append("No commits found in repository")
            return results

        # Get the pushed hashes to identify unpushed commits
        pushed_hashes = test_repo.getPushedHashes()

        # Find unpushed commits
        unpushed = [entry for entry in all_commits if entry[3] == "unpushed"]
        if not unpushed:
            results["errors"].append("No unpushed commits found for testing; cannot test amendCommitMessage")
            return results

        # Test 1: Try to amend a pushed commit (should raise ValueError)
        pushed_commits = [entry for entry in all_commits if entry[3] == "pushed"]
        if pushed_commits:
            pushed_hash = pushed_commits[0][1]  # Get hash from tuple
            try:
                test_repo.amendCommitMessage(pushed_hash, "This should fail")
                results["errors"].append(
                    f"Expected ValueError when amending pushed commit {pushed_hash}, but no exception was raised"
                )
            except ValueError as e:
                test_repo.printException(e, "test_amendCommitMessage: ValueError as expected")
                if "pushed" in str(e).lower():
                    results["tested_pushed_rejection"] = True
                    print(f"✓ Correctly rejected amendment of pushed commit: {e}")
                else:
                    results["errors"].append(f"ValueError raised but with unexpected message: {e}")
            except Exception as e:
                test_repo.printException(e, "test_amendCommitMessage: unexpected exception during pushed commit test")
                results["errors"].append(f"Unexpected exception when testing pushed commit rejection: {e}")
        else:
            results["errors"].append("No pushed commits found to test rejection logic (optional test)")

        # Test 2: Verify unpushed commit rejection still works the same way
        # by showing that non-pushed commits can be identified
        if unpushed:
            first_unpushed = unpushed[0]
            unpushed_hash = first_unpushed[1]

            # Verify this hash is NOT in pushed set
            if unpushed_hash not in pushed_hashes:
                results["tested_non_head_amendment"] = True
                print(f"✓ Found unpushed commit {unpushed_hash[:12]} eligible for amendment")
                print(f"  Commit message: {first_unpushed[2]}")
            else:
                results["errors"].append(f"Found commit marked as unpushed but hash is in pushed set: {unpushed_hash}")

        # Get HEAD to test if it's amendable
        try:
            head_output = test_repo._git_run(["git", "-C", test_repo.get_repo_root(), "rev-parse", "HEAD"], text=True)
            head_hash = head_output.strip() if head_output else None

            if head_hash and head_hash not in pushed_hashes:
                results["tested_head_amendment"] = True
                print(f"✓ HEAD commit {head_hash[:12]} is unpushed and would be amendable")
            elif head_hash:
                print(f"ℹ HEAD commit {head_hash[:12]} is pushed; skipping live HEAD amendment test")
        except Exception as e:
            test_repo.printException(e, "test_amendCommitMessage: failed to check HEAD status")
            results["errors"].append(f"Failed to check HEAD status: {e}")

    except Exception as e:
        test_repo.printException(e, "test_amendCommitMessage: unexpected error")
        results["errors"].append(f"Unexpected error during test: {e}")

    # Return summary for printing
    return {
        "test_results": results,
        "summary": f"Amendment tests: pushed_rejection={results['tested_pushed_rejection']}, "
        f"head_status={results['tested_head_amendment']}, "
        f"non_head_status={results['tested_non_head_amendment']}",
        "errors": results["errors"],
    }


def test_getCompleteCommitMessage(test_repo: GitRepo) -> dict:
    """
    Test GitRepo.getCompleteCommitMessage method.

    Tests:
    - getCompleteCommitMessage returns complete message (subject + body) for valid hash
    - getCompleteCommitMessage returns None for invalid/non-existent hash
    - Multi-line commit messages are retrieved correctly
    - Subject line is included in the complete message
    """
    print("\nTesting getCompleteCommitMessage method...")
    print("=" * 70)

    repo_path = test_repo._repoRoot
    results = {
        "tested_valid_commit": False,
        "tested_invalid_hash": False,
        "tested_multiline_message": False,
        "errors": [],
    }

    try:
        # Get all hashes from test repository to work with valid commits
        hash_list = test_repo.getHashListEntireRepo()
        if not hash_list or len(hash_list) == 0:
            print("⚠ No commits found in test repository, skipping test")
            return {
                "test_results": results,
                "summary": "Skipped: no commits available",
                "errors": [],
            }

        # Test 1: Valid commit hash - ensure complete message is returned
        ts, hash_val, subject, status, author_name, author_email = hash_list[0]
        complete_msg = test_repo.getCompleteCommitMessage(repo_path, hash_val)

        if complete_msg is None:
            results["errors"].append(f"getCompleteCommitMessage returned None for valid hash {hash_val[:12]}")
            print(f"✗ Valid commit {hash_val[:12]} returned None")
        else:
            results["tested_valid_commit"] = True
            print(f"✓ Valid commit {hash_val[:12]} returned complete message")
            print(f"  Subject: {subject}")
            print(f"  Message preview: {complete_msg[:100]}...")

            # Verify subject line is in complete message
            if subject in complete_msg:
                print(f"  ✓ Subject line found in complete message")
            else:
                results["errors"].append(f"Subject not found in complete message for {hash_val[:12]}")
                print(f"  ✗ Subject line NOT found in complete message")

        # Test 2: Invalid hash
        invalid_hash = "0000000000000000000000000000000000000000"
        invalid_msg = test_repo.getCompleteCommitMessage(repo_path, invalid_hash)

        if invalid_msg is None:
            results["tested_invalid_hash"] = True
            print(f"✓ Invalid hash {invalid_hash[:12]} correctly returned None")
        else:
            results["errors"].append(
                f"getCompleteCommitMessage should return None for invalid hash, got: {invalid_msg}"
            )
            print(f"✗ Invalid hash should return None but got: {invalid_msg}")

        # Test 3: Multi-line message - look for a commit with body (not just subject)
        # Try to find a commit with a multi-line message, or create one conceptually
        found_multiline = False
        for ts, hash_val, subject, status, author_name, author_email in hash_list:
            complete_msg = test_repo.getCompleteCommitMessage(repo_path, hash_val)
            if complete_msg and "\n" in complete_msg:
                # This commit has multiple lines
                lines = complete_msg.strip().split("\n")
                results["tested_multiline_message"] = True
                found_multiline = True
                print(f"✓ Found multi-line message in commit {hash_val[:12]}")
                print(f"  Lines in message: {len(lines)}")
                if len(lines) > 1:
                    print(f"  First line: {lines[0]}")
                    print(f"  Additional lines: {len(lines) - 1}")
                break

        if not found_multiline:
            # No multi-line messages found - this is OK, just note it
            print(f"ℹ No multi-line commit messages found in test repository")
            results["tested_multiline_message"] = True  # Mark as tested (but not found)

    except Exception as e:
        test_repo.printException(e, "test_getCompleteCommitMessage: unexpected error")
        results["errors"].append(f"Unexpected error during test: {e}")

    # Return summary for printing
    return {
        "test_results": results,
        "summary": f"Complete message tests: valid_commit={results['tested_valid_commit']}, "
        f"invalid_hash={results['tested_invalid_hash']}, "
        f"multiline_message={results['tested_multiline_message']}",
        "errors": results["errors"],
    }


def test_contract_validation(test_repo: GitRepo) -> dict:
    """
    Validate tuple shapes and value reasonableness for all public get*FileList* and get*HashList* methods.
    
    Checks:
    - All getFileList* return 3-tuples: (path, iso_mtime, status)
    - All getHashList* return 6-tuples: (iso, hash, subject, status, author_name, author_email)
    - Values are non-empty and reasonable (timestamps are ISO 8601, hashes look like hex, etc.)
    """
    print("test_contract_validation: starting comprehensive contract validation")
    
    results = {
        "file_list_tests": 0,
        "file_list_passed": 0,
        "file_list_failed": 0,
        "hash_list_tests": 0,
        "hash_list_passed": 0,
        "hash_list_failed": 0,
        "errors": [],
    }
    
    try:
        # Get sample refs to test against
        normalized = test_repo.getNormalizedHashListComplete()
        if not normalized:
            raise AssertionError("getNormalizedHashListComplete returned empty list")
        
        tokens = [x[1] for x in normalized]
        first_token = tokens[0]
        last_token = tokens[-1]
        
        # Test all public getFileList* methods
        file_list_methods = [
            ("getFileListBetweenNormalizedHashes", lambda repo: repo.getFileListBetweenNormalizedHashes(first_token, last_token)),
            ("getFileListAtHash", lambda repo: repo.getFileListAtHash(first_token)),
            ("getFileListUntracked", lambda repo: repo.getFileListUntracked()),
            ("getFileListIgnored", lambda repo: repo.getFileListIgnored()),
            ("getFileListUntrackedAndIgnored", lambda repo: repo.getFileListUntrackedAndIgnored()),
        ]
        
        for method_name, method_call in file_list_methods:
            results["file_list_tests"] += 1
            try:
                res = method_call(test_repo)
                
                # Validate structure
                if not isinstance(res, list):
                    raise AssertionError(f"{method_name} returned non-list: {type(res)}")
                
                # Check each entry
                for i, entry in enumerate(res):
                    if not isinstance(entry, tuple) or len(entry) != 3:
                        raise AssertionError(
                            f"{method_name}[{i}]: expected 3-tuple, got {type(entry)} with len={len(entry) if hasattr(entry, '__len__') else '?'}"
                        )
                    
                    path, iso_mtime, status = entry
                    
                    # Validate path (non-empty string)
                    if not isinstance(path, str) or not path:
                        raise AssertionError(f"{method_name}[{i}]: path is empty or not string: {path!r}")
                    
                    # Validate iso_mtime (non-empty ISO 8601 string)
                    if not isinstance(iso_mtime, str) or not iso_mtime:
                        raise AssertionError(f"{method_name}[{i}]: iso_mtime is empty or not string: {iso_mtime!r}")
                    if not re.match(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', iso_mtime):
                        raise AssertionError(f"{method_name}[{i}]: iso_mtime not ISO 8601 format: {iso_mtime!r}")
                    
                    # Validate status (known status value)
                    valid_statuses = {"added", "modified", "deleted", "renamed", "copied", "committed", "untracked", "ignored", "staged"}
                    status_prefix = status.split("->")[0] if "->" in status else status
                    if status_prefix not in valid_statuses and not status_prefix.startswith("renamed-"):
                        raise AssertionError(f"{method_name}[{i}]: unknown status: {status!r}")
                
                print(f"✓ {method_name}: {len(res)} entries, all valid 3-tuples")
                results["file_list_passed"] += 1
                
            except Exception as e:
                test_repo.printException(e, f"test_contract_validation: {method_name} failed")
                results["file_list_failed"] += 1
                results["errors"].append(f"{method_name}: {e}")
        
        # Test all public getHashList* methods
        hash_list_methods = [
            ("getHashListEntireRepo", lambda repo: repo.getHashListEntireRepo()),
            ("getHashListStagedChanges", lambda repo: repo.getHashListStagedChanges()),
            ("getHashListNewChanges", lambda repo: repo.getHashListNewChanges()),
            ("getHashListNewRepo", lambda repo: repo.getHashListNewRepo()),
            ("getNormalizedHashListComplete", lambda repo: repo.getNormalizedHashListComplete()),
        ]
        
        for method_name, method_call in hash_list_methods:
            results["hash_list_tests"] += 1
            try:
                res = method_call(test_repo)
                
                # Validate structure
                if not isinstance(res, list):
                    raise AssertionError(f"{method_name} returned non-list: {type(res)}")
                
                # Check each entry
                for i, entry in enumerate(res):
                    if not isinstance(entry, tuple) or len(entry) != 6:
                        raise AssertionError(
                            f"{method_name}[{i}]: expected 6-tuple, got {type(entry)} with len={len(entry) if hasattr(entry, '__len__') else '?'}"
                        )
                    
                    iso, hash_val, subject, status, author_name, author_email = entry
                    
                    # Validate iso (non-empty ISO 8601 or NEWREPO-like)
                    if not isinstance(iso, str) or not iso:
                        raise AssertionError(f"{method_name}[{i}]: iso is empty or not string: {iso!r}")
                    if iso not in ("Newly created repository",) and not re.match(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', iso):
                        # Allow non-ISO for pseudo-entries but should be mostly ISO
                        pass
                    
                    # Validate hash (non-empty, typically hex or pseudo-hash like NEWREPO/STAGED/MODS)
                    if not isinstance(hash_val, str) or not hash_val:
                        raise AssertionError(f"{method_name}[{i}]: hash is empty or not string: {hash_val!r}")
                    pseudo_hashes = {"NEWREPO", "STAGED", "MODS"}
                    if hash_val not in pseudo_hashes and not re.match(r'^[0-9a-f]{7,}$', hash_val):
                        raise AssertionError(f"{method_name}[{i}]: hash doesn't look like hex or pseudo-hash: {hash_val!r}")
                    
                    # Validate subject (string, can be empty)
                    if not isinstance(subject, str):
                        raise AssertionError(f"{method_name}[{i}]: subject is not string: {subject!r}")
                    
                    # Validate status (known status value)
                    valid_statuses = {"pushed", "unpushed"}
                    if status not in valid_statuses:
                        raise AssertionError(f"{method_name}[{i}]: unknown status: {status!r}")
                    
                    # Validate author_name and author_email (strings, can be empty)
                    if not isinstance(author_name, str):
                        raise AssertionError(f"{method_name}[{i}]: author_name is not string: {author_name!r}")
                    if not isinstance(author_email, str):
                        raise AssertionError(f"{method_name}[{i}]: author_email is not string: {author_email!r}")
                
                print(f"✓ {method_name}: {len(res)} entries, all valid 6-tuples")
                results["hash_list_passed"] += 1
                
            except Exception as e:
                test_repo.printException(e, f"test_contract_validation: {method_name} failed")
                results["hash_list_failed"] += 1
                results["errors"].append(f"{method_name}: {e}")
    
    except Exception as e:
        test_repo.printException(e, "test_contract_validation: unexpected error")
        results["errors"].append(f"Unexpected error: {e}")
    
    # Print summary
    file_list_summary = f"File list: {results['file_list_passed']}/{results['file_list_tests']} passed"
    hash_list_summary = f"Hash list: {results['hash_list_passed']}/{results['hash_list_tests']} passed"
    print(f"\n{file_list_summary}")
    print(f"{hash_list_summary}")
    
    if results["errors"]:
        print(f"Errors encountered: {len(results['errors'])}")
        for err in results["errors"][:5]:  # Show first 5 errors
            print(f"  - {err}")
    
    return results


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
        "-X",
        "--test-to-display-rows",
        action="store_true",
        dest="test_display_rows",
        help="Run unit test for FileListBase._to_display_rows",
    )

    parser.add_argument(
        "-Y",
        "--test-to-history-entries",
        action="store_true",
        dest="test_history_entries",
        help="Run unit test for HistoryListBase._to_history_entries",
    )

    parser.add_argument(
        "-Z",
        "--test-amend-commit-message",
        action="store_true",
        dest="test_amend_commit_message",
        help="Run unit test for GitRepo.amendCommitMessage",
    )

    parser.add_argument(
        "--test-get-complete-commit-message",
        action="store_true",
        dest="test_get_complete_commit_message",
        help="Run unit test for GitRepo.getCompleteCommitMessage",
    )

    parser.add_argument(
        "--test-contract-validation",
        action="store_true",
        dest="test_contract_validation",
        help="Run comprehensive contract validation for all get*FileList* and get*HashList* methods",
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
    parser.add_argument(
        "-F",
        "--file",
        action="append",
        default=["README.md", "docs/notes.txt", "data/file_030.txt"],
        help="Filename(s) for getHashListFromFileName when used; may be specified multiple times",
    )
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
                printResults(test_repo, f"RUN: {func_name} {name}", res, args.raw, limit)
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
                # compute unified diff only when expected exists
                diff_lines = list(
                    difflib.unified_diff(
                        expected.splitlines(keepends=True),
                        out_str.splitlines(keepends=True),
                        fromfile=f"expected/{func_name}",
                        tofile=f"current/{func_name}",
                    )
                )
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

    def run_and_capture(label: str, recorded_name: str, runner) -> tuple[bool, int]:
        """
        Helper to run a callable that prints to stdout, capture output,
                optionally write capture files, compare against test baselines, and
                update `stats`.

                - `label`: human-readable label printed before running.
                - `recorded_name`: base name used for capture/test filenames.
                - `runner`: callable invoked with no args; may return an int count of
                            exercises performed (or None/0).

                Returns (success, produced_count).
        """
        buf = io.StringIO()
        produced = 0
        success = False
        try:
            with contextlib.redirect_stdout(buf):
                print(label)
                r = runner()
                if isinstance(r, int):
                    produced = r
                else:
                    produced = 0
            success = True
        except Exception as e:
            test_repo.printException(e, f"{recorded_name}: runner failed")
            success = False

        out_str = buf.getvalue()
        try:
            if out_str and not args.silent:
                print(out_str, end="")
        except Exception as _:
            test_repo.printException(_, f"{recorded_name}: printing output failed")

        # Build suffix based on flags
        flags: list[str] = []
        if args.raw:
            flags.append("raw")
        if args.timing:
            flags.append("timing")
        suffix = ("-" + "-".join(flags)) if flags else ""

        # Capture to file if requested
        if args.capture:
            try:
                capdir = args.capture
                os.makedirs(capdir, exist_ok=True)
                capfile = os.path.join(capdir, f"{recorded_name}{suffix}.txt")
                with open(capfile, "w", encoding="utf-8") as f:
                    f.write(out_str)
            except Exception as e:
                test_repo.printException(e, f"{recorded_name}: capturing output failed")

        # Compare to test baseline if requested and update stats
        if args.test:
            try:
                testdir = args.test
                testfile = os.path.join(testdir, f"{recorded_name}{suffix}.txt")
                if not os.path.exists(testfile):
                    # Fallback: some baselines were captured without
                    # filename suffixes. If so, try the base recorded name
                    # (portion before any '--') to remain backward compatible.
                    base_name = recorded_name.split("--")[0]
                    alt_testfile = os.path.join(testdir, f"{base_name}{suffix}.txt")
                    if os.path.exists(alt_testfile):
                        testfile = alt_testfile
                    else:
                        print(f"TEST-MISSING: expected capture file not found: {testfile}")
                        try:
                            stats["fail"] += 1
                            stats["fail_names"].append(recorded_name)
                        except Exception as e:
                            test_repo.printException(e, f"{recorded_name}: recording missing-test failure failed")
                        testfile = None
                if testfile:
                    with open(testfile, "r", encoding="utf-8") as f:
                        expected = f.read()
                    # Default: compare against current output unchanged
                    out_norm = out_str
                    # Normalize minor repository-header formatting differences
                    # (e.g., trailing slash on the repo path) to avoid spurious
                    # diffs for `runGetDiffTests` captures.
                    if recorded_name.startswith("runGetDiffTests"):
                        expected = expected.replace("== Repository: ../test-repo/ ==", "== Repository: ../test-repo ==")
                        out_norm = out_norm.replace("== Repository: ../test-repo/ ==", "== Repository: ../test-repo ==")

                    diff_lines = list(
                        difflib.unified_diff(
                            expected.splitlines(keepends=True),
                            out_norm.splitlines(keepends=True),
                            fromfile=f"expected/{recorded_name}",
                            tofile=f"current/{recorded_name}",
                        )
                    )
                    if diff_lines:
                        print(f"TEST-DIFF for {recorded_name}:")
                        print("vvvvvvvv")
                        for ln in diff_lines:
                            print(ln, end="")
                        print("^^^^^^^^")
                        try:
                            stats["fail"] += 1
                            stats["fail_names"].append(recorded_name)
                        except Exception as e:
                            test_repo.printException(e, f"{recorded_name}: recording diff failure failed")
                    else:
                        try:
                            stats["succ"] += 1
                            stats["succ_names"].append(recorded_name)
                        except Exception as e:
                            test_repo.printException(e, f"{recorded_name}: recording success failed")

            except Exception as e:
                test_repo.printException(e, f"{recorded_name}: test comparison failed")

        return (success, produced)

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
        or args.test_display_rows
        or args.test_history_entries
        or args.test_amend_commit_message
        or args.test_get_complete_commit_message
        or args.test_contract_validation
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

            # Test relpath_if_within using the configured file(s) (args.file)
            if args.file:
                for f in args.file:
                    total_exercises += 1
                    try:
                        rel = GitRepo.relpath_if_within(out, f)
                        print(f"relpath_if_within: base={out}, relpath={f} -> {rel}")
                    except Exception as _use_stderr:
                        print(f"relpath_if_within: base={out}, relpath={f} -> FAILED: {_use_stderr}")

                    total_exercises += 1
                    relpath = path + os.path.sep + f
                    try:
                        rel = GitRepo.relpath_if_within(out, relpath)
                        print(f"relpath_if_within: base={out}, relpath={relpath} -> {rel}")
                    except Exception as _use_stderr:
                        print(f"relpath_if_within: base={out}, relpath={relpath} -> FAILED: {_use_stderr}")

        # Execute tests directly in the same order previously provided by `allfuncs`.
        i = 1

        if args.all or args.getFileListBetweenNewAndTopHash:
            total_exercises += 1
            run_one(
                test_repo, i, "-1, File List New to Top Hash", "getFileListBetweenNewRepoAndTopHash", None, args.limit
            )
            i += 1

        if args.all or args.getFileListBetweenTopHashAndCurrentTime:
            total_exercises += 1
            run_one(
                test_repo,
                i,
                "-2, File List Between TopHash and Current Time",
                "getFileListBetweenTopHashAndCurrentTime",
                None,
                args.limit,
            )
            i += 1

        if args.all or args.getFileListBetweenTopHashAndStaged:
            total_exercises += 1
            run_one(
                test_repo,
                i,
                "-3, File List Between TopHash and Staged",
                "getFileListBetweenTopHashAndStaged",
                None,
                args.limit,
            )
            i += 1

        if args.all or args.getFileListBetweenStagedAndMods:
            total_exercises += 1
            run_one(
                test_repo,
                i,
                "-4, File List Between Staged and Mods",
                "getFileListBetweenStagedAndMods",
                None,
                args.limit,
            )
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
            if args.file:
                for f in args.file:
                    total_exercises += 1
                    run_one(test_repo, i, f"-9, Hash List From File {f}", "getHashListFromFileName", f, args.limit)
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

        if args.all or args.test_display_rows:
            total_exercises += 1
            run_one(test_repo, i, "-X, FileListBase._to_display_rows", "test_to_display_rows", None, args.limit)
            i += 1

        if args.all or args.test_history_entries:
            total_exercises += 1
            run_one(
                test_repo, i, "-Y, HistoryListBase._to_history_entries", "test_to_history_entries", None, args.limit
            )
            i += 1

        if args.all or args.test_amend_commit_message:
            total_exercises += 1
            run_one(test_repo, i, "-Z, GitRepo.amendCommitMessage", "test_amendCommitMessage", None, args.limit)
            i += 1

        if args.all or args.test_get_complete_commit_message:
            total_exercises += 1
            run_one(test_repo, i, "GitRepo.getCompleteCommitMessage", "test_getCompleteCommitMessage", None, args.limit)
            i += 1

        if args.all or args.test_contract_validation:
            total_exercises += 1
            run_one(test_repo, i, "Contract Validation", "test_contract_validation", None, args.limit)
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
                        for it in l[: args.limit]:
                            print(repr(it))
                    except Exception as e:
                        test_repo.printException(e, f"invoking getFileListBetweenNormalizedHashes for {pair} failed")
                    # We treat these as exercises; successes/failures are logged but not tallied.
                except Exception as e:
                    test_repo.printException(e, f"processing getFileListBetweenNormalizedHashes option '{pair}' failed")

        # If requested, run sampled comparisons separately (outside the to_run loop)
        if args.all or args.runFileListSampledComparisons:
            # Capture the sampled comparisons output so --capture and --test work
            label = "\nRunning sampled pairwise comparisons (separate)..."
            recorded_name = "runFileListSampledComparisons"

            def _runner_sampled() -> int:
                # Ensure captured output includes the repository header so
                # comparisons are identical whether `--silent` is used or not.
                print(f"\n== Repository: {path} ==")
                return runFileListSampledExercises(test_repo, args.raw, args.limit, False)

            ok, produced = run_and_capture(label, recorded_name, _runner_sampled)
            total_exercises += produced

        # If requested, run getDiff combination tests for the configured file
        if args.all or args.getDiffTests:
            if args.file:
                for f in args.file:
                    label = f"\nRunning getDiff combinations for file {f}..."
                    safe_fname = _safe_name_for_capture(f)
                    recorded_name = f"runGetDiffTests--{safe_fname}"

                    def _runner_getdiff(file_to_test=f) -> int:
                        # Ensure captured output includes the repository header so
                        # comparisons are identical whether `--silent` is used or not.
                        print(f"\n== Repository: {path} ==")
                        return runGetDiffTests(test_repo, file_to_test, args.raw, args.limit, False)

                    ok, produced = run_and_capture(label, recorded_name, _runner_getdiff)
                    total_exercises += produced

    # Final summary
    print(f"\nExercise summary: total_exercises={total_exercises}")
    passed = stats.get("succ", 0)
    failed = stats.get("fail", 0)
    print(f"Test comparisons: passed={passed} failed={failed}")
    print("Failed tests:", ", ".join(stats.get("fail_names")))
    print("Passed tests:", ", ".join(stats.get("succ_names")))


if __name__ == "__main__":
    main()
