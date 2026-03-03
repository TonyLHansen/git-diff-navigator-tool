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
  - Commands while focused in the Diff column:
    - `d` / `D`: rotate the diff command variant. The variants cycle through common textual options (for example: ignore-space-change, patience, and word-diff) when a full textual diff is available.
    - `c` / `C`: toggle the use of color.
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

If `--no-color` is provided, the diff output will not be colorized.

`path` is optional — it defaults to the current working directory. If a filename is provided, the app will open its directory and populate the History column for that file on startup.

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
