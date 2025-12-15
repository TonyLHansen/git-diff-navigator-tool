Git Diff History Navigator Tool (gitdiff)
=========================================

Overview
--------
The Git Diff History Navigator Tool is a terminal Textual TUI that provides a three-column view for

* browsing a filesystem tree,
* viewing the git history for a selected file, and
* exploring the diffs between different versions.

The three columns are titled: Files (left), History (middle), Diff (right).

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
  - A Left Arrow on the directory ".." will navigate to the parent directory.

- History column:
  - Lines are populated from `git log --follow`.
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
  - A Left Arrow will close the Diff column.
  - Commands while focused in the Diff column:
    - `d` / `D`: rotate the diff command variant. The variants cycle through: 
      - the default `git diff`, 
      - `git diff --ignore-space-change`, and 
      - `git diff --diff-algorithm=patience`.
    - `c` / `C`: toggle the use of color.
    - `f` / `F`: toggle fullscreen mode (hide other columns).


Implementation notes
--------------------
- Language: Python 3.14
- UI: Textual (ListView, ListItem, Label, ModalScreen)
- Git integration:
  - `git` CLI is used for `log --follow` and `diff` (preserves `--follow` rename semantics).
  - `pygit2` is used for repository discovery, status mapping, and per-index-entry mtime (used for `STAGED` timestamps).
- Data model: ListItems have attached metadata attributes `_filename`, `_hash`, `_repo_status`, and `_raw_text` for robust lookups and reliable UI updates.

Running
-------
Run the application as follows:

```bash
gitdiff.py [path]
```

`[path]` is optional — it defaults to the current working directory. If a filename is provided, the app will open its directory and populate the History column for that file on startup.

Dependencies
------------
- Required python libraries: `textual`, `rich`, `pygit2`.

Troubleshooting
---------------
- If the app shows no git history for a file, confirm the file is inside a git repo and that `git` is available on PATH.

Source
------
Main program: `gitdiff.py`

License
-------
Unspecified — modify as needed.
