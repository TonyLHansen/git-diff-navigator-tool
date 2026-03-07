Git Diff History Navigator Tool (gitdiffnavtool)
================================================

History
-------

I was doing a lot of "software archeology" on a repository to see what had changed
in various different commits into the code base. I looked around for a tool that would help
me do that, but didn't find one that did things in a way I found useful. So I wrote one.
It's a "Textual User Interface", meaning that it's designed to be run in a terminal window.
For now, you'll have to run `pip install` the libraries that are needed and run it in
whatever python environment you need.

I hope it helps you. 

Overview
--------
The Git Diff History Navigator Tool is a terminal Textual TUI that provides a multi-pane view for

* browsing a filesystem tree,
* viewing the git history for a selected file, and
* exploring diffs and opening file contents at selected commits.

Depending on layout, panes include file lists, file/repo history lists, a diff view, and an open-file view.

Type `q` or `Q` to exit the program.

Key features
------------

Arrow keys move up and down the various columns.
Left and Right arrow keys perform differently in each column.

- Files column: navigable directory listing; directories highlighted with a blue background.
  - Status markers & colors: files are prefixed with a short marker and colored by status:
    - ` ` (space) tracked & clean — bright white
    - `U` untracked — bold yellow
    - `M` modified — yellow
    - `A` staged (index changes) — cyan
    - `D` deleted in working tree — red
    - `I` ignored — dim italic
    - `!` conflicted — magenta

  - A Right Arrow will 
    - (for files) open the History column for the current filename
    - (for directories) navigates to the current directory name.
  - A Left Arrow navigates to the parent directory (no-op at repository root).

- History column:
  - Lines are populated from `git log --follow`.
  - Each commit row displays: **`TIMESTAMP ↑ HASH |AUTHOR_NAME EMAIL| SUBJECT`**
    - **TIMESTAMP**: ISO 8601 format (e.g., `2026-03-04 14:30:00`)
    - **↑**: Up arrow indicator for unpushed commits; absent for pushed commits
    - **HASH**: Short commit hash (configurable via `--hash-length`, default 12 characters)
    - **|AUTHOR NAME EMAIL|**: Author name and email in pipe-delimited format (use `--no-add-authors` to hide)
    - **SUBJECT**: First line of commit message
  - Pseudo-log entries `STAGED` and `MODS` are inserted at the top when the file has been staged, and when there are uncommitted/unstaged modifications, respectively.
  - Press `m` (or `M`) to _mark_ the current log row with a leading `✓`.
    - Only one history row may be checked at a time — toggling a new row clears any prior checkmark.
  - A Right Arrow will 
    - open the Diff column for the currently highlighted log entry against the checkmarked entry (if there is a checkmarked entry) or the next entry in the list.
  - A Left Arrow will close the History column.


- Diff column:
  - Lines are populated using `git diff` between the two hashes (or pseudo-hashes for staged and modified unstaged versions).
  - A header line indicates the two hashes being compared, e.g.:
    `Comparing: <old_hash>..<new_hash>`.
  - The order is always the lower list item vs the higher item, so diffs read `older..newer`.
  - A Left Arrow closes fullscreen diff back to split, or returns to the prior pane from split mode.
  - Diff variants can be cycled with `d` / `D` to view diffs in different formats:
    - **classic**: standard unified diff
    - **ignore-spaces**: unified diff ignoring whitespace
    - **patience**: patience algorithm for better alignment
    - **word-diff**: word-level differences with machine-parseable output
    - **side-by-side**: two-column format (left=old, right=new) with dynamic width adjustment
  - In side-by-side mode, use `[` and `]` to adjust column widths, and `=` to reset to 50/50 split.
  - Commands while focused in the Diff column:
    - `d` / `D`: rotate through available diff variants.
    - `c` / `C`: toggle the use of color.
    - `[`: decrease left panel width in side-by-side mode (5% per keystroke).
    - `]`: increase left panel width in side-by-side mode (5% per keystroke).
    - `=`: reset side-by-side split to 50/50.
    - `+`: increase unified diff context (`git diff -U`) by 1 line and re-run the diff.
    - `-`: decrease unified diff context (`git diff -U`) by 1 line (minimum 0) and re-run the diff.
    - `Right` / `Enter` / `f` / `F`: toggle split/fullscreen.
    - `t` / `T`: toggle paired split layouts (`history→file→diff` <-> `file→history→diff`).
    - `w` / `W`: write a snapshot of the currently-visible diff (previous docs referred to this as "save").

- OpenFile column:
  - Open with `o` from history/file views.
  - Shows file content at a selected hash with line numbers.
  - `Right` / `Enter` / `f` / `F`: toggle split/fullscreen.
  - `t` / `T`: toggle paired split layouts (`history→file→open` <-> `file→history→open`).
  - `Left`: close fullscreen to split, then return toward the originating pane.
  - `w` / `W`: write snapshot files.


Implementation notes
--------------------
- Language: Python 3.14
- UI: Textual (ListView, ListItem, Label, ModalScreen)
- Git integration:
  - `git` CLI is used for all git operations and cached for speed.
- Data model: ListItems have attached metadata attributes `_filename`, `_hash`, `_repo_status`, and `_raw_text` for robust lookups and reliable UI updates.

Running
-------
Run the application as follows:

```bash
gitdiffnavtool.py [options] [path]
```

`path` is optional — it defaults to the current working directory. If a filename is provided, the app will open its directory and populate the History column for that file on startup.

Configuration
-------------
You can set defaults in `.gitdiffnavtool.ini` (current directory or `$HOME`) under `[gitdiffnavtool]`.

Example (comprehensive configuration with all available options):

```ini
[gitdiffnavtool]
# Display width for short commit hashes (default: 12, must be >= 1)
hash-length = 12
# Show author name and email in commit rows (default: true)
add-authors = true
# Color scheme for diffs: red-green, blue-orange, teal-purple, style, none (default: style)
color = style
# Diff variant: classic, ignore-spaces, patience, word-diff, side-by-side (default: classic)
diff = classic
# Unified diff context lines (git diff -U; default: 3, must be >= 0)
unified-context = 3
# Minimum terminal width for side-by-side view (default: 60, must be >= 1)
# If terminal width is less than this, side-by-side falls back to unified format
minimum-sidebyside-width = 60
# Start in repository mode instead of file-first mode (default: false)
repo-first = false
# Show startup welcome popup (default: true)
initial-popup = true
# Include ignored files in file lists (default: false)
ignored-files = false
# Include untracked files in file lists (default: true)
untracked-files = true
# Directory where snapshot files are written (optional, default: beside source file)
# output-directory = /tmp/snapshots
# Use a specific git branch (optional, default: current branch)
# branch = main
# Enable debug logging to a file (optional, default: disabled)
# debug = /tmp/gitdiffnavtool.log
```

Command-line and Configuration Options
---------------------------------------

- All configuration values can be specified on the command line as well (use `--help` to see all options).
- Command-line options override configuration file values.
- Boolean options have `--no-` variations (e.g., `--no-add-authors` to disable author display).

Key Options:
- `--color SCHEME`: Set diff colorization (red-green, blue-orange, teal-purple, style, none).
- `-C` or `--no-color`: Disable diff colorization.
- `--hash-length N`: Display short commit hashes using N characters (default: 12).
- `--unified-context N`: Use N lines of context in diffs (default: 3, minimum 0).
- `--minimum-sidebyside-width N`: Minimum terminal width before side-by-side falls back to unified (default: 60).
- `--no-add-authors`: Hide author name and email in commit rows.
- `--output-directory DIR`: Directory where snapshot files are written (default: beside source file).
- `--show-help`: Display the help screen and exit.
- `--show-initial-popup`: Display the startup popup and exit.

Some of the Options
-------------------

If `--no-color` is provided, the diff output will not be colorized.
If `--hash-length N` is provided, displayed short commit hashes will use `N` characters.
If `--unified-context N` is provided, diffs will use `N` lines of context (git diff -U option, default 3, minimum 0).
If `--no-add-authors` is provided, author name and email will not be displayed in commit rows.
If `--output-directory DIR` is provided, all snapshot files will be written to that directory (created if it doesn't exist).
The command line options override the config values.

Dependencies
------------
- Required python libraries: `textual`, `rich`.

Troubleshooting
---------------
- If the app shows no git history for a file, confirm the file is inside a git repo and that `git` is available on PATH.

Source
------
Main program: `gitdiffnavtool.py`

License
-------
Apache Version 2.0
