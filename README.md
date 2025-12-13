Git History Navigator (gitdiff)
===============================

Overview
--------
Git History Navigator is a terminal Textual TUI that provides a three-column view for
browsing a filesystem tree, viewing git history for a selected file, and exploring diffs.
The three columns are titled: Files (left), History (middle), Diff (right).

Key features
------------
- Files column: navigable directory listing; directories highlighted with a blue background.
- History column: shows commit history for a selected file (uses `git log --follow` to preserve renames).
- Diff column: shows unified diffs between commits, between staged and working copies, or between staged and HEAD.
- Repository awareness: optionally uses `pygit2` (if installed) to detect repository root and file status.
- Status markers & colors: files are prefixed with a short marker (e.g. "?", "A", "M") and colored by status.
  - ` ` (space) tracked & clean — bright white
  - `?` untracked — grey50
  - `M` modified — yellow
  - `A` staged (index changes) — cyan
  - `D` deleted in working tree — red
  - `I` ignored — dim italic
  - `!` conflicted — magenta
- Pseudo-history entries: when relevant, `STAGED` and `MODS` pseudo-log lines are inserted at the top
  of the History column with date stamps:
  - `YYYY-MM-DD STAGED` — per-file staged timestamp (from `pygit2` index entry mtime, fallback to `.git/index` mtime)
  - `YYYY-MM-DD MODS` — working-tree file mtime (if there are unstaged modifications)
- Smart diff resolution: the app maps pseudo-hashes (`STAGED`, `MODS`) and real commit hashes to the
  appropriate `git diff` invocations:
  - working vs staged: `git diff -- <file>`
  - staged vs HEAD: `git diff --cached -- <file>`
  - working vs commit: `git diff <commit> -- <file>`
  - staged vs commit: `git diff --cached <commit> -- <file>`
  - commit vs commit: `git diff <old> <new> -- <file>`

Keyboard / Navigation
---------------------
- Up / Down: move selection in the current column (ListView keyboard handling).
- Right (in Files):
  - on directory: enter it and refresh Files column
  - on file: populate History column for that file (git log)
- Left (in Files):
  - on `..` entry: go up a directory and highlight previous directory
  - on other entries: ignored (no action)
- Right (in History): show Diff column and compute appropriate `git diff` between selected lines (handles pseudo-lines).
- Left (in History or Diff): move focus back to the left column.
- q: quit

Footer and Palette
------------------
- The Textual command palette (Ctrl+P) is disabled in this app (to avoid the built-in palette).
- The footer shows custom hints: `q Quit  ← ↑ ↓ →`.

Implementation notes
--------------------
- Language: Python 3.14
- UI: Textual (ListView, ListItem, Label, ModalScreen)
- Git integration:
  - `git` CLI is used for `log --follow` and `diff` (preserves `--follow` rename semantics).
  - `pygit2` (optional) is used for repository discovery and per-file index metadata (status map and index entry mtimes).
- Data model: ListItems have attached metadata attributes `_filename`, `_hash`, and `_repo_status` for robust lookups.

Running
-------
In this repository's root, activate the Python venv and run:

```bash
./venv-3.14/bin/python gitdiff.py [path]
```

`[path]` is optional — defaults to the current working directory.

Dependencies
------------
- Required: `textual`, `rich` (expected in development environment)
- Optional but recommended: `pygit2` (enables repository discovery, per-file staged timestamps, and status mapping)

Extensibility and customization
-------------------------------
- Status colors and marker symbols can be edited in `gitdiff.py` where the `markers` and style mapping live.
- The pseudo-history behavior (`STAGED`/`MODS`) and diff resolution is implemented in the History and Diff handlers
  — those can be changed to alter which diffs are shown by default.
- Footer text and title styling live in the small CSS block at the top of `GitHistoryTool.CSS` and can be tuned.

Troubleshooting
---------------
- If the app shows no git history for a file, confirm the file is inside a git repo and that `git` is available on PATH.
- If `pygit2` is missing, the app falls back to treating files as untracked for coloring and will still use the `git` CLI
  for history/diff operations.
- If the UI layout hides the footer or title, check terminal height and that no external CSS edits were made.

Source
------
Main program: `gitdiff.py`

License
-------
Unspecified — modify as needed.
