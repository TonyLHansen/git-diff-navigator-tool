#!/usr/bin/env python3
"""
gitdiffnavtool - regenerated scaffold (step 1 only)

This file is a minimal scaffold created by the regen plan. Subsequent
steps will fill in the concrete classes and behavior.
"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
import sys
import subprocess
import traceback
import inspect
from typing import Optional, Callable
from datetime import datetime, timezone, timedelta
from functools import wraps
import pprint
import difflib
import re
import hashlib
import codecs
import time
from subprocess import check_output, CalledProcessError

# Third-party UI and rendering imports
from rich.text import Text
from rich.markdown import Markdown
from rich.panel import Panel
from rich.console import Console
from textual import events
from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.widgets import ListView, Label, ListItem, Footer, Header, TextArea, Input, Static
from textual.screen import ModalScreen
from textual.css.query import NoMatches

# Repository helpers (extracted): provide printException, AppException, GitRepo
from gitrepo import printException, AppException, GitRepo

# --- Constants -------------------------------------------------------------
# Highlight constants (defaults)
HIGHLIGHT_FILELIST_BG = "#f1c40f"

HIGHLIGHT_REPOLIST_BG = "#3333CC"

# Diff-list specific highlight background
HIGHLIGHT_DIFF_BG = "#2ecc71"

# Help-list specific highlight background
HIGHLIGHT_HELP_BG = "#95a5a6"

# Default highlight background used when a widget doesn't specify one
HIGHLIGHT_DEFAULT_BG = "light_gray"

# Background used specifically for `style`-scheme deletions so the global
# default highlight background doesn't need to be changed for this purpose.
STYLE_DELETE_BG = "#a3a3a3"

# Status markers mapping used to render the left-most TAG for file rows.
# Keys correspond to computed repo statuses (strings used by preparatory APIs).
MARKERS = {
    "conflicted": "!",
    "staged": "A",
    "wt_deleted": "D",
    "ignored": "I",
    "modified": "M",
    "untracked": "U",
    "tracked_clean": "\u00a0",
}

# Inline CSS used by the Textual App (can be edited in-place)
INLINE_CSS = (
    """
    /* gitdiffnavtool inline CSS */

    /* Title labels */
    #left-file-title, #left-history-title, #right-history-title, #right-file-title, #diff-title, #help-title {
        padding: 0 1;
        background: $surface;
        color: $text;
    }

    .title.active {
        background: $accent-darken-1;
        color: white;
        text-style: bold;
    }

    /* Simple column spacing */
    ListView {
        padding: 0 1;
    }

    /* Highlight active list item per widget so colors are deterministic. */
    /* File lists */
    #left-file-list ListItem.active,
    #right-file-list ListItem.active {
        background: [[HIGHLIGHT_FILELIST_BG]];
        color: white;
    }

    /* History (repo) lists */
    #left-history-list ListItem.active,
    #right-history-list ListItem.active {
        background: [[HIGHLIGHT_REPOLIST_BG]];
        color: white;
    }
    
    /* Non-modal find overlay: single line input with layer positioning */
    #find-container {
        layer: overlay;
        dock: top;
        height: 1;
        width: 100%;
        border: none;
        background: $boost;
        layout: horizontal;
    }

    #find-label {
        width: auto;
        background: $accent;
        color: $text;
        padding: 0 1;
    }

    #find-input {
        width: 1fr;
        background: $boost;
        color: $text;
        border: none;
    }

    /* Make modal screens transparent so the find modal doesn't blank the UI */
    ModalScreen {
        background: transparent;
    }

    /* The overlay root is a pre-composed Static container that will host
       floating overlays. Keep it full-width and transparent; runtime
       code will set absolute positioning on mounted overlays. */
    #overlay-root {
        width: 100%;
        height: 0;
        background: transparent;
        padding: 0;
        display: none;
    }

    /* Diff list */
    #diff-list ListItem.active {
        background: [[HIGHLIGHT_DIFF_BG]];
        color: white;
    }

    /* Help list */
    #help-list ListItem.active {
        background: [[HIGHLIGHT_HELP_BG]];
        color: white;
    }

    /* Focus border classes for active/inactive widgets */
    ListView.focused-white {
        border: solid white;
    }

    ListView.focused-gray {
        border: solid gray;
    }

    /* Centered message modal styling */
    #msg-modal-wrapper {
        align: center middle;
        height: 100%;
        width: 100%;
    }

    #msg-modal {
        content-align: center middle;
    }

    #msg-modal-row {
        align: center middle;
    }

    #msg-modal-prompt {
        align: center middle;
        padding-top: 1;
    }
""".replace("[[HIGHLIGHT_FILELIST_BG]]", HIGHLIGHT_FILELIST_BG)
    .replace("[[HIGHLIGHT_REPOLIST_BG]]", HIGHLIGHT_REPOLIST_BG)
    .replace("[[HIGHLIGHT_DIFF_BG]]", HIGHLIGHT_DIFF_BG)
    .replace("[[HIGHLIGHT_HELP_BG]]", HIGHLIGHT_HELP_BG)
)


# Canonical widget and label IDs (six canonical widgets)
LEFT_FILE_LIST_ID = "left-file-list"
LEFT_FILE_TITLE = "left-file-title"

LEFT_HISTORY_LIST_ID = "left-history-list"
LEFT_HISTORY_TITLE = "left-history-title"

RIGHT_FILE_LIST_ID = "right-file-list"
RIGHT_FILE_TITLE = "right-file-title"

RIGHT_HISTORY_LIST_ID = "right-history-list"
RIGHT_HISTORY_TITLE = "right-history-title"

DIFF_LIST_ID = "diff-list"
DIFF_TITLE = "diff-title"

HELP_LIST_ID = "help-list"
HELP_TITLE = "help-title"

OPEN_FILE_LIST_ID = "open-file-list"
OPEN_FILE_TITLE = "open-file-title"

# Footer text used when showing the left file list
LEFT_FILE_FOOTER = Text("File: q(uit)  t(oggle)  ?/h(elp)  ←(prev) ↑/↓/PgUp/PgDn/Home/End  →/␍ ", style="bold")

# Footer text used when switching to file-history view
RIGHT_HISTORY_FOOTER = Text(
    "History: q(uit)  t(oggle)  w(rite)  ?/h(elp)  ↑/↓/ PgUp/PgDn/Home/End  →/␍   m(ark)", style="bold"
)

# Footer text used when showing the left history pane
LEFT_HISTORY_FOOTER = Text("History: q(uit)  t(oggle)  ?/h(elp)  ← ↑/↓/ PgUp/PgDn/Home/End  →/␍   m(ark)", style="bold")

# Footer text used when showing the right file list (file list view)
RIGHT_FILE_FOOTER = Text(
    "File: q(uit)  t(oggle)  w(rite)  ?/h(elp)  ←(close) ↑/↓/PgUp/PgDn/Home/End  →/␍ ", style="bold"
)

# Footer text used for help screen
HELP_FOOTER = Text("Help: q(uit)  ↑/↓/PgUp/PgDn/Home/End  Press Enter/␍ to return", style="bold")
# Text("Help: q(uit)  ↑/↓/PgUp/PgDn  Press any key to return", style="bold")

# Footer text used when showing open file content (split panes)
OPEN_FILE_FOOTER_1 = Text(
    "OpenFile: q(uit)  t(oggle)  w(rite)  ?/h(elp)  ←(close) ↑/↓/PgUp/PgDn/Home/End  →/f(ull)", style="bold"
)
# Footer text used when showing open file content (fullscreen)
OPEN_FILE_FOOTER_2 = Text(
    "OpenFile: q(uit)  t(oggle)  w(rite)  ?/h(elp)  ←/f(ull)  ↑/↓/PgUp/PgDn/Home/End", style="bold"
)
# Footer text used when showing the diff for a history/file selection
DIFF_FOOTER_1 = Text(
    "Diff: q(uit)  t(oggle)  w(rite)  ?/h(elp)  ←(close)  ↑/↓/PgUp/PgDn/Home/End →/f(ull)  c(olor)  d(iff-type)  [=](width)  +/-(ctx)  {}(hscroll)",
    style="bold",
)
DIFF_FOOTER_2 = Text(
    "Diff: q(uit)  t(oggle)  w(rite)  ?/h(elp)  ←/f(ull)  ↑/↓/PgUp/PgDn/Home/End c(olor)  d(iff-type)  [=](width)  +/-(ctx)  {}(hscroll)",
    style="bold",
)

# Supported diff color schemes (used by CLI/config and runtime cycling)
DIFF_COLOR_SCHEMES = [
    "red-green",
    "blue-orange",
    "teal-purple",
    "style",
    "none",
]

# Map scheme name -> mapping of run/line types to Rich styles.
DIFF_SCHEME_MAP = {
    "red-green": {
        "add_run": "bold green",
        "del_run": "bold red",
        "add_line": "green",
        "del_line": "red",
        # compatibility keys used by renderers
        "add": "bold green",
        "del": "bold red",
        "add_span": "green",
        "del_span": "red",
    },
    "blue-orange": {
        "add_run": "bold blue",
        "del_run": "bold #ff8800",
        "add_line": "blue",
        "del_line": "#ff8800",
        "add": "bold blue",
        "del": "bold #ff8800",
        "add_span": "blue",
        "del_span": "#ff8800",
    },
    "teal-purple": {
        "add_run": "bold cyan",
        "del_run": "bold magenta",
        "add_line": "cyan",
        "del_line": "magenta",
        "add": "bold cyan",
        "del": "bold magenta",
        "add_span": "cyan",
        "del_span": "magenta",
    },
    "style": {
        # 'style' uses highlighting and text-style only (no colors):
        # additions -> bold + reverse (highlight), deletions -> underline
        # with a light-gray background for contrast.
        "add_run": "bold reverse",
        "del_run": f"black underline on {STYLE_DELETE_BG}",
        "add_line": "reverse",
        "del_line": f"black on {STYLE_DELETE_BG}",
        "add": "bold reverse",
        "del": f"black underline on {STYLE_DELETE_BG}",
        "add_span": "reverse",
        "del_span": f"black underline on {STYLE_DELETE_BG}",
    },
    "none": {
        "add_run": None,
        "del_run": None,
        "add_line": None,
        "del_line": None,
        "add": None,
        "del": None,
        "add_span": None,
        "del_span": None,
    },
}

# Friendly names for diff variants (used by CLI/config)
DIFF_VARIANT_NAMES = ["classic", "ignore-spaces", "patience", "word-diff", "side-by-side"]

# Minimum terminal width required for side-by-side view (falls back to unified if narrower)
MIN_SIDE_BY_SIDE_WIDTH = 60

INITIAL_POPUP_TEXT = """
Welcome to Git Diff Navigator Tool!

This tool helps you explore git repositories and their histories with a focus on navigating diffs and file changes.
You may type a `q` (or `Q`) to quit at any time. Press `Enter/␍` to dismiss this message and get started, or `?` for help.

The basic navigation keys are:

- Up/Down arrows/PageUp/PageDown/Beginning/End: move the current selection up and down
- Left/Right arrows: move back/forward through views depending on the focused pane.
- Enter/␍: in list panes, behaves like Right; in Diff/OpenFile panes, toggles split/fullscreen.

The program normally starts in a file list view showing the files in the root of the current repository,
somewhat similar to what you see with `git status`. You can then navigate up and down to switch the current
selection, and press Right or Enter/␍ to either 

1) drill down into subdirectories to see the files there, or 

2) switch to the history view (using Right arrow or Enter/␍) to see all of the commits associated with the chosen file.

From the history view you can navigate the commits and press Right or Enter/␍ again to see the diff for that commit and file.
(You can also mark a particular commit in the history view with `m` and then navigate to another commit; 
press Right/Enter/␍ to see the diff between the marked commit and the current selection.)

From the history view, you can also press `o` to open the file content in the OpenFile pane.

While viewing Diff or OpenFile content, Right/Enter/`f` promotes the pane to fullscreen and
Left returns to the split view (or back to the prior pane when already in split).

From the Diff view, you can toggle color on and off, rotate through diff variants (e.g., ignore-space-change),
and `+`/`-` to adjust the unified diff context lines. Side-by-side diff mode includes additional commands to 
adjust the relative widths of the old and new content columns. See the help screen for details on these and other commands.

Alternatively, you can start the program in repository mode (using the `-R`/`--repo-first` flag) that
initially shows a history view of all commits in the repository. You can then select a commit
and press Right arrow to see the file list for that commit (or that commit and a marked commit when using "m" to mark a commit).
Pressing Right/Enter/␍ on a file in that list will show the diff for that file and commit.
Press `o` to open file content in the OpenFile pane.

Each window will also display a footer with context-sensitive hints for available actions.
For example, when viewing the file list, the footer will prompt you to press Right to view the file history. 

Remember, you can press `?` at any time to view the help screen with these and additional instructions.
And of course, you can quit at any time by pressing `q` or `Q`. (Run the program with `--show-help` to see the help screen on startup.)

If you want to skip this message on future launches, you can edit the configuration file (gitdiffnavtool.ini) 
and set `initial-popup = false` under the `[gitdiffnavtool]` section.
(See the help screen for more info on the config file and available options.)
"""

HELP_TEXT = """
# gitdiffnavtool help

Overview
--------
- gitdiffnavtool is a terminal UI for exploring a Git repository with
    file lists, per-file and repo history lists, a diff pane, and an
    open-file content pane. It uses the `git` CLI for status and history operations.

Invocation:
- Run `gitdiffnavtool [path]` to open the app for `path` (directory or
    file).
- Run `gitdiffnavtool [-r/--repo-first [--repo-hash hash1] [--repo-hash hash2]] [path]` to open
    the app in repository mode, optionally comparing `hash1` and `hash2`.

Basic navigation:
- Arrow keys: Up / Down / PageUp / PageDown / Home / End move
    the selection within the focused column.
- Right: open/enter the selected row, or promote Diff/OpenFile to fullscreen.
- Enter/␍: in list columns behaves like Right; in Diff/OpenFile toggles split/fullscreen.
- Left: go back / close (in Diff/OpenFile: fullscreen -> split, split -> prior state).
- `q` (or Ctrl-Q): quit the application.

Global actions:
- `h` or `?`: show this help screen.
- `r`: refresh the current view.
- `t`: toggle between file-first and repo-first views.
- `w`: prompt to write snapshot files for the current file/hash combination.

Column-specific information and commands
----------------------------------------

Left File Column (Files):
- Shows directory tree or file list for the working tree path.
- Right on a directory: enter that directory.
- Right on a tracked file: open the file's history in the right-side
    history column.
- File rows include a one-character status marker before the filename:
    - `'\u00a0'`: tracked and currently clean (present in `HEAD`, no detected local change)
    - `M`: modified in the working tree
    - `A`: staged (index differs from `HEAD` for that path)
    - `D`: deleted in the working tree
    - `U`: untracked (present in working tree, not tracked by git)
    - `I`: ignored (matched by ignore rules and shown when ignored files are enabled)
    - `!`: conflicted (reserved marker; displayed if a path is classified as conflicted)
- Press `i` to toggle showing ignored files; press `u` to toggle showing untracked files.


Right History Column (File History for left pane):
- Shows commits affecting the file selected in the left file pane.
- Mark a row with `m` to select it as the `prev` commit; navigate to a
    second row and press Right to diff between the two marked rows.
- Right on a row: open the diff for that file between the selected
    commit pair.

Left History Column (Repository History):
- Shows repository-wide commits (newest first). Use this to pick commit
    ranges to inspect repository-wide changes.
- Press `m` on a row to mark it (acts as one side of a commit pair).
- With a commit pair selected, press Right to populate the Right File
    Column with the files changed between those commits.
- The pseudo-hash names MODS, STAGED and NEWREPO are displayed 
  when there are unstaged modifications, staged changes, or the new 
  repository state, respectively.

Right File Column (Files for selected commit-pair or pseudo refs):
- When populated from a repo commit pair you will see per-file status
    markers (A/M/D/U/I/!) followed by the filename.
- Special pseudo-rows: `MODS` and `STAGED` appear at the top when the
    selected refs are the working tree/index. Expanding `MODS` shows the
    modified (unstaged) files; expanding `STAGED` shows staged changes.
- Right on a file row: open the file-level diff between the selected
    commit pair (or between index and working-tree when using `STAGED`/`MODS`).

Commit History Format:
- Each commit row displays: TIMESTAMP ↑ HASH |AUTHOR_NAME EMAIL| SUBJECT
  where:
  - TIMESTAMP: ISO 8601 format (e.g., 2026-03-04 14:30:00)
  - ↑: Up arrow marks unpushed commits; omitted for pushed commits
  - HASH: Short commit hash (width controlled by --hash-length, default 12 chars)
  - |AUTHOR_NAME EMAIL|: Author information surrounded by pipes, showing author's
    name and email address.
  - SUBJECT: First line of the commit message

Diff Column:
- Shows the textual patch for the current file/commit pair. The first
    line is a one-line header describing the file and the two refs being
    compared and is not selectable.
- Commands when focused:
    - `d` / `D`: rotate the diff command variant. When a full textual diff is available this cycles through configured textual variants (for example, ignore-space-change, patience, word-diff, and side-by-side).
    - `c` / `C`: toggle colorized diffs on/off.
    - `[`: decrease left panel width in side-by-side mode (5% increment).
    - `]`: increase left panel width in side-by-side mode (5% increment).
    - `=`: reset to 50/50 split in side-by-side mode.
    - `{`: scroll side-by-side view left by 10 columns.
    - `}`: scroll side-by-side view right by 10 columns.
    - `+`: increase unified diff context (`git diff -U`) by 1 and re-run the diff.
    - `-`: decrease unified diff context (`git diff -U`) by 1 (minimum 0) and re-run the diff.
    - `Right` / `Enter` / `f` / `F`: toggle split <-> fullscreen diff view.
    - `t` / `T`: toggle paired split layouts (`history_file_diff` <-> `file_history_diff`).
    - `w` / `W`: write a snapshot of the currently-visible diff (previous docs used the term "save").

Open File Column:
- Shows file content at a selected commit (opened via `o` from history/file views).
- Commands when focused:
    - `Right` / `Enter` / `f` / `F`: toggle split <-> fullscreen open-file view.
    - `t` / `T`: toggle paired split layouts (`history_file_open` <-> `file_history_open`).
    - `Left`: close/open-file navigation back toward the originating list.
    - `w` / `W`: write a snapshot for the current file/hash.

Snapshot Writing:
- Pressing `w` or `W` in the Diff or Open File columns writes a snapshot (copy) of the currently-visible content
  to a file in the snapshot output directory.
- By default, snapshots are written beside the source file with names like `filename.hash.ext` where `ext`
  is the source file extension and `hash` is the commit hash.
- Use `--output-directory DIR` (or `output-directory = DIR` in config) to specify an alternate directory
  where all snapshot files will be written. The app will create the directory if it doesn't exist.
- Snapshot files preserve the repository-relative path structure when written to an output directory
  (e.g., a snapshot of `src/main.py` at hash `abc123` would become `output-directory/src/main.py.abc123.py`).

Tips and behavior notes:
- Short commit hashes are shown using the configured hash length (`--hash-length` / `hash-length` in config).
- `MODS` lists working-tree modifications (unstaged).
- `STAGED` lists index changes (files that were added (staged) but not committed).
- When diffing between `STAGED` and `MODS` the UI shows the comparison the user
    expects (index vs working-tree).
- The app uses the `git` CLI for its repository operations.

Color Schemes:
- Use `-c SCHEME` or `--color SCHEME` to select a color scheme for diffs.
- Available schemes: `red-green`, `blue-orange`, `teal-purple`, `style`, `none`.
- Use `-C` or `--no-color` to disable diff colorization (equivalent to `--color=none`).
- Color preference can be toggled at runtime with `c` or `C` in the Diff pane.

Diff Variants:
- Use `--diff VARIANT` to select how diffs are displayed.
- Available variants:
    - `classic`: standard unified diff format
    - `ignore-spaces`: unified diff ignoring whitespace changes
    - `patience`: patience algorithm for improved hunk alignment
    - `word-diff`: word-level diff with machine-parseable output
    - `side-by-side`: two-column side-by-side format with dynamic width adjustment
- The selected diff variant can be rotated at runtime with `d` or `D` in the Diff pane.
- Side-by-side mode displays old content in the left column and new content in the right column, separated by a gutter marker:
    - ` ` (space): unchanged line
    - `<`: line only in left (deleted)
    - `>`: line only in right (added)
    - `|`: line differs between sides
- In side-by-side mode, use `[` and `]` to adjust column widths, and `=` to reset to 50/50 split.
- Long lines are truncated with ellipsis (…) to fit the available space.

Configuration File
------------------
- An optional configuration file allows you to set default values for the command-line options.
- `--config/-c` can be specified, or the file `.gitdiffnavtool.ini` will be looked for
  in the current directory first, then `$HOME` directory.
- File format: INI-style configuration with a `[gitdiffnavtool]` section.
- Command-line options always take precedence over configuration file values.
- Use `--init-config [cwd|home]` to generate a commented `.gitdiffnavtool.ini` template and exit.

- Example configuration (showing all available options with their defaults):
    ```
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
    # If the terminal is narrower than this threshold, the side-by-side diff falls back to unified format even if side-by-side is selected.
    minimum-sidebyside-width = 60
    # Start in repository mode instead of file-first mode (default: false)
    repo-first = false
    # Show startup welcome popup (default: true)
    initial-popup = true    
    # Include ignored files in file lists (default: false)
    # This can be toggled at run time with 'i'/'I' when focused on file lists.
    ignored-files = true
    # Include untracked files in file lists (default: true)
    # This can be toggled at run time with 'u'/'U' when focused on file lists.
    untracked-files = true
    # Directory where snapshot files are written (optional, default: beside source file)
    # output-directory = /tmp/snapshots
    # Use a specific git branch (optional, default: current branch)
    # branch = main
    # Enable debug logging to a file (optional, default: disabled)
    # debug = /tmp/gitdiffnavtool.log
    ```

Command-line Options
--------------------
- All of the configuration values can also be specified on the command line, plus some additional options.
- All boolean configuration options also have a `--no-` variation to turn off the value should it be set
  in the configuration file. For example, use `--no-add-authors` to turn off displaying the author information.
- Use the `--help` option to see the complete list of options.
- Use the `--show-help` or `--show-help-color` options to see this help information.
- Use the `--show-initial-popup` or `--show-initial-popup-color` options to see the initial popup help information.
"""


def build_default_config_template() -> str:
    """Build a commented .gitdiffnavtool.ini template with defaults."""
    lines = [
        "[gitdiffnavtool]",
        f"# Display width for short commit hashes (default: {HASH_LENGTH}, must be >= 1)",
        f"hash-length = {HASH_LENGTH}",
        "# Show author name and email in commit rows (default: true)",
        "add-authors = true",
        "# Color scheme for diffs: red-green, blue-orange, teal-purple, style, none (default: style)",
        "color = style",
        "# Diff variant: classic, ignore-spaces, patience, word-diff, side-by-side (default: classic)",
        "diff = classic",
        "# Unified diff context lines (git diff -U; default: 3, must be >= 0)",
        "unified-context = 3",
        f"# Minimum terminal width for side-by-side view (default: {MIN_SIDE_BY_SIDE_WIDTH}, must be >= 1)",
        "# If the terminal is narrower than this threshold, side-by-side falls back to unified format.",
        f"minimum-sidebyside-width = {MIN_SIDE_BY_SIDE_WIDTH}",
        "# Start in repository mode instead of file-first mode (default: false)",
        "repo-first = false",
        "# Show startup welcome popup (default: true)",
        "initial-popup = true",
        "# Include ignored files in file lists (default: true)",
        "ignored-files = true",
        "# Include untracked files in file lists (default: true)",
        "untracked-files = true",
        "# Directory where snapshot files are written (optional, default: beside source file)",
        "# output-directory = /tmp/snapshots",
        "# Use a specific git branch (optional, default: current branch)",
        "# branch = main",
        "# Enable debug logging to a file (optional, default: disabled)",
        "# debug = /tmp/gitdiffnavtool.log",
        "# Truncate debug log before writing (default: false / append mode)",
        "trim-debug = false",
    ]
    return "\n".join(lines) + "\n"


def build_missing_config_option_comment_block(
    missing_keys: list[str], has_gitdiffnavtool_section: bool, existing_text: str
) -> str:
    """Build a comment-only block describing missing config options."""
    option_docs: list[tuple[str, str, str]] = [
        ("repo-first", "Start in repository mode instead of file-first mode.", "repo-first = false"),
        ("ignored-files", "Include ignored files in file lists.", "ignored-files = true"),
        ("untracked-files", "Include untracked files in file lists.", "untracked-files = true"),
        ("initial-popup", "Show startup welcome popup.", "initial-popup = true"),
        ("add-authors", "Show author name and email in commit rows.", "add-authors = true"),
        ("trim-debug", "Truncate debug log before writing (false means append mode).", "trim-debug = false"),
        ("branch", "Use a specific git branch.", "branch = main"),
        (
            "color",
            "Color scheme: red-green, blue-orange, teal-purple, style, none.",
            "color = style",
        ),
        (
            "diff",
            "Diff variant: classic, ignore-spaces, patience, word-diff, side-by-side.",
            "diff = classic",
        ),
        ("debug", "Enable debug logging to a file.", "debug = /tmp/gitdiffnavtool.log"),
        ("output-directory", "Directory where snapshot files are written.", "output-directory = /tmp/snapshots"),
        ("hash-length", "Display width for short commit hashes (must be >= 1).", f"hash-length = {HASH_LENGTH}"),
        ("unified-context", "Unified diff context lines (must be >= 0).", "unified-context = 3"),
        ("history-limit", "Maximum history entries to display (0 means unlimited).", "history-limit = 0"),
        (
            "minimum-sidebyside-width",
            "Minimum terminal width for side-by-side view (must be >= 1).",
            f"minimum-sidebyside-width = {MIN_SIDE_BY_SIDE_WIDTH}",
        ),
    ]
    missing_key_set = set(missing_keys)
    lines: list[str] = []
    if existing_text and not existing_text.endswith("\n"):
        lines.append("")
    lines.append("# --- Added by --init-config: missing option hints ---")
    lines.append("# Uncomment and adjust any options you want to set explicitly.")
    if not has_gitdiffnavtool_section:
        lines.append("# NOTE: No [gitdiffnavtool] section was found in this file.")
        lines.append("# Add the section below and place desired options under it.")
        lines.append("# [gitdiffnavtool]")

    for key, description, sample in option_docs:
        if key not in missing_key_set:
            continue
        if re.search(rf"(?im)^\s*#\s*{re.escape(key)}\s*=", existing_text):
            continue
        lines.append(f"# {description}")
        lines.append(f"# {sample}")

    if len(lines) <= 2:
        return ""
    return "\n".join(lines) + "\n"


def build_backup_path(path: str) -> str:
    """Return a non-conflicting backup filename for path."""
    candidate = f"{path}.bak"
    idx = 1
    while os.path.exists(candidate):
        candidate = f"{path}.bak.{idx}"
        idx += 1
    return candidate


# Common styles used across file/history preparers
STYLE_DIR = "white on blue"
STYLE_PARENT = STYLE_DIR
STYLE_WT_DELETED = "red"
STYLE_ERROR = "red"
STYLE_CONFLICTED = "magenta"
STYLE_STAGED = "cyan"
STYLE_IGNORED = "dim italic"
STYLE_MODIFIED = "yellow"
STYLE_MODIFIED_DIR = "black on yellow"
STYLE_UNTRACKED = "bold yellow"
STYLE_DEFAULT = "white"

STYLE_FILELIST_KEY = "dim"

# Help background style used by help/header labels
STYLE_HELP_BG = f"white on {HIGHLIGHT_HELP_BG}"

# Header row text for file lists (unselectable)
FILELIST_KEY_ROW_TEXT = "Key:  '\u00a0' tracked  M modified  A staged  D deleted  I ignored  U untracked  ! conflicted"

# Number of characters to display for short hashes
HASH_LENGTH = 12


# --- Logging setup --------------------------------------------------------
# NOTE: logging is configured in `main()` when `--debug` is passed.

logger = logging.getLogger(__name__)

# Define a TRACE level lower than DEBUG and add a convenience `trace` method
# so callers can emit very-verbose trace messages when enabled.
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _logger_trace(self, msg, *args, **kwargs):
    """
    Logger method implementing TRACE-level logging.

    Attached to `logging.Logger` as `trace`; emits the message at the
    numeric TRACE level when enabled.
    """
    if self.isEnabledFor(TRACE):
        self._log(TRACE, msg, args, **kwargs)


setattr(logging.Logger, "trace", _logger_trace)


def enable_trace_logging(enabled: bool) -> None:
    """
    Enable or disable TRACE-level logging across the root logger and handlers.

    When enabled this sets the root logger and all its handlers to the numeric
    TRACE level so `logger.trace(...)` messages are emitted. When disabled this
    does nothing (existing logging configuration remains).
    """
    try:
        root = logging.getLogger()
        if enabled:
            root.setLevel(TRACE)
            for h in root.handlers:
                h.setLevel(TRACE)

            logger.debug("Trace logging enabled")
    except Exception as e:
        printException(e, "enable_trace_logging failed")


class AppBase(AppException, ListView):
    """
    Base widget class for list-like components providing shared helpers.

    This is a minimal, safe implementation intended for Step 2 of the regen
    plan. It implements defensive defaults, exception logging, text
    extraction helpers, and basic navigation key handling.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Safe defaults so other code can access these attributes early
        self._min_index = 1
        self._populated = False
        self.current_diff_file = None
        # When True the next watch_index-triggered scroll should animate
        # (used by page up / page down handlers to make the jump more
        # visually noticeable).
        self._page_scroll = False
        # Flags to identify widget type without relying on isinstance checks
        # These are set to 0 by default and overridden by subclasses.
        self.is_history_list = 0
        self.is_file_list = 0
        # When True, `watch_index_helper` should defer applying visual
        # highlight/scroll changes. Used to avoid transient highlights
        # while we atomically change the list contents and set the index.
        self._suppress_watch: bool = False
        # Ensure common attributes exist so code can access them directly
        # Rely on ListView to provide `children`, `_nodes`, `index`, and `app`.
        # Per-widget highlight background; subclasses override with specific backgrounds
        self.highlight_bg_style = HIGHLIGHT_DEFAULT_BG
        # Last interactive search string for repeated '>'/'<' searches
        self._last_search: str | None = None

    def _log_visible_items(self, msg: str) -> None:
        """
        Diagnostic helper: log every visible node with hidden attrs and highlighted item.

        Intended for debugging navigation and focus issues. Logs one debug
        message per visible node with its index, visible text, and any
        underscore-prefixed attributes attached to the node. Also logs the
        current `self.index` and the expected highlighted identifier.
        """
        # Only emit the expensive per-node debug when verbosity is high.
        if self.app.verbose <= 2:
            return

        try:
            header = {
                "widget_class": type(self).__name__,
                "widget_id": getattr(self, "id", None),
                "widget_name": getattr(self, "name", None),
                "index": getattr(self, "index", None),
            }
            header.update(
                {
                    "app_focus": self.app._current_focus,
                    "app_layout": self.app._current_layout,
                    "rel_dir": self.app.rel_dir,
                    "rel_file": self.app.rel_file,
                }
            )
        except Exception as _e:
            self.printException(_e)
            header = {"widget_class": type(self).__name__}
        logger.debug("_log_visible_items called_from=%s -- %s", header, msg)
        try:
            nodes = self.nodes() or []
            logger.debug(
                "_log_visible_items: widget=%s node_count=%d current_index=%r",
                type(self).__name__,
                len(nodes),
                getattr(self, "index", None),
            )
            for i, node in enumerate(nodes):
                try:
                    txt = self.text_of(node)

                    # Collect underscore-prefixed attributes and their reprs
                    attrs = {}
                    for a in dir(node):
                        if a.startswith("_"):
                            try:
                                val = getattr(node, a)
                                attrs[a] = repr(val)
                            except Exception as _e:
                                self.printException(_e, f"_log_visible_items: getattr(node, {a!r}) failed")
                                attrs[a] = "<error>"
                    logger.debug("_log_visible_items: item idx=%d text=%r attrs=%s", i, txt, attrs)
                except Exception as _e:
                    self.printException(_e, "_log_visible_items: item inspect failed")
                    logger.debug("_log_visible_items: item idx=%d failed to inspect: %s", i, _e)
            # Log currently highlighted item's identifying info if available
            try:
                idx = getattr(self, "index", None)
                if idx is not None and 0 <= idx < len(nodes):
                    try:
                        node = nodes[idx]
                        ident = (
                            getattr(node, "_filename", None) or getattr(node, "_raw_text", None) or self.text_of(node)
                        )
                        logger.debug("_log_visible_items: highlighted index=%d ident=%r", idx, ident)
                    except Exception as _e:
                        self.printException(_e, "_log_visible_items: reading highlighted node failed")
                        logger.debug("_log_visible_items: highlighted inspection failed: %s", _e)
                else:
                    logger.debug("_log_visible_items: no valid highlighted index (index=%r)", idx)
            except Exception as _e:
                self.printException(_e, "_log_visible_items: highlighted index computation failed")
        except Exception as e:
            self.printException(e, "_log_visible_items failed")

    def _clear_active_classes(self) -> None:
        """
        Defensive helper: clear any stray `active` class on visible nodes.

        Implemented on `AppBase` so all list-like subclasses can call
        this uniformly to avoid duplicating the same defensive logic.
        """
        try:
            for n in self.nodes() or []:
                try:
                    n.set_class(False, "active")
                except Exception as e:
                    self.printException(e, "AppBase._clear_active_classes: set_class failed")
                    try:
                        n.remove_class("active")
                    except Exception as e2:
                        self.printException(e2, "AppBase._clear_active_classes: remove_class failed")
        except Exception as e:
            self.printException(e, "AppBase._clear_active_classes failed")

    def _canonical_relpath(self, path: str, repo_root: str) -> str:
        """
        Return a canonical realpath for `path` using `repo_root` for
        repository-relative paths.

        Return a canonical repository-relative path.
        If `path` is absolute, convert it to a repo-relative path using
        `repo_root`. On error return the original `path`.
        """
        try:
            # Always return a repo-relative normalized path so callers
            # can compare canonical values without dealing with system
            # absolute paths.
            if not path:
                return ""
            # If `path` appears to be absolute (leading slash) convert to
            # a repo-relative normalized path; otherwise normalize as-is.
            if path and path.startswith(os.sep):
                try:
                    return os.path.relpath(os.path.normpath(path), repo_root)
                except Exception as e:
                    self.printException(e, "_canonical_relpath: realpath->relpath failed")
                    return os.path.normpath(path)
            # Normalize relative paths (collapse ./ and ../)
            return os.path.normpath(path)
        except Exception as e:
            self.printException(e, "_canonical_relpath failed")
            return path

    def text_of(self, node) -> str:
        """Extract visible text from a ListItem's Label or renderable."""
        try:
            # Prefer an explicit canonical raw text when it's meaningful
            # (non-empty). Some rows (notably the parent '..' entry) store
            # an empty `_raw_text` value used for navigation, but the visible
            # label contains the human-friendly text. Treat empty strings as
            # absent so callers that want visible text receive the label
            # contents instead of an empty string.
            raw = getattr(node, "_raw_text", None)
            if raw is not None and raw != "":
                return raw
            # If a filename was attached (e.g. '..' for parent entries), prefer it
            # to querying the Label child which may not be available immediately
            # during certain mount/refresh timing windows.
            fname = getattr(node, "_filename", None)
            if fname:
                return fname
            # Try to query for a Label child, but handle the case where none exists
            try:
                lbl = node.query_one(Label)
                if hasattr(lbl, "text") and getattr(lbl, "text"):
                    return lbl.text
                renderable = getattr(lbl, "renderable", None)
                if isinstance(renderable, Text):
                    return renderable.plain
                if renderable is not None:
                    return str(renderable)
                return str(lbl)
            except Exception as _no_logging:
                # No Label found or query failed - try to get text from node directly
                if hasattr(node, "renderable"):
                    renderable = getattr(node, "renderable", None)
                    if isinstance(renderable, Text):
                        return renderable.plain
                    if renderable is not None:
                        return str(renderable)
                # Last resort: stringify the node
                return str(node)
        except Exception as e:
            self.printException(e, "extracting text")
            return str(node)

    def nodes(self):
        """
        Return the underlying nodes list or an empty list if unset.

        Uses getattr to tolerate Textual internals not being present yet.
        """
        try:
            # Prefer currently-displayed children so highlight/class changes
            # apply to visible rows during async remove/append cycles.
            c = self.children
            if c:
                return c
            # Fallback to ListView internal storage when no children exist.
            n = self._nodes
            return n if n else []
        except Exception as e:
            self.printException(e, "nodes failed")
            return []

    def _activate_index(self, new_index: int) -> None:
        """
        Set the active/selected index and update ListItem 'active' class.

        Deactivates the previously-active item, activates the new item,
        and applies the change immediately by rebuilding the view from
        authoritative data rather than mutating UI after the fact.
        """
        try:
            nodes = self.nodes()
            if not nodes:
                return
            old = self.index
            # per-widget highlight is provided via `self.highlight_bg_style`

            # Set the index immediately and synchronously apply the visual
            # changes by invoking `apply_index_change` which will re-render
            # the widget from `self._nodes_by_dir` when available.
            try:
                logger.debug(
                    "_activate_index: old=%r new=%r highlight_style=%s nodes=%d",
                    old,
                    new_index,
                    self.highlight_bg_style,
                    len(nodes),
                )
                try:
                    self.index = new_index
                except Exception as e:
                    self.printException(e, "_activate_index: direct index set failed")
                    try:
                        setattr(self, "index", new_index)
                    except Exception as _e:
                        self.printException(_e, "_activate_index: setattr(index) failed")
                # Apply highlight/scroll behavior synchronously by rebuilding
                # the current view from authoritative data rather than
                # mutating styles after the fact.
                self.apply_index_change(old, new_index)
                # Final defensive pass: ensure the selected row has the
                # `active` class even if render/update races occurred.
                try:
                    self._enforce_active_class_for_index(new_index)
                except Exception as _e:
                    self.printException(_e, "_activate_index: enforcing active class failed")
            except Exception as e:
                self.printException(e, "_activate_index: failed to set index and apply changes")
        except Exception as e:
            self.printException(e, "_activate_index failed")

    def _enforce_active_class_for_index(self, idx: int | None) -> None:
        """Set `active` class on exactly one visible node for deterministic highlight."""
        try:
            nodes = self.nodes()
            if not nodes:
                return
            for i, node in enumerate(nodes):
                try:
                    node.set_class(i == idx, "active")
                except Exception as e:
                    self.printException(e, "_enforce_active_class_for_index: node.set_class failed")
                try:
                    node.refresh()
                except Exception as e:
                    self.printException(e, "_enforce_active_class_for_index: node.refresh failed")
        except Exception as e:
            self.printException(e, "_enforce_active_class_for_index failed")

    def apply_index_change(self, old: int | None, new: int | None):
        """
        Imperatively apply highlight and scrolling for index change.

        This contains the previous `watch_index_helper` logic but is meant
        to be invoked directly from key handlers or other imperative code
        paths. It does not consult `_suppress_watch` — callers should
        respect any higher-level suppression semantics.
        """
        try:
            nodes = self.nodes()
            # Defensive debug: log node count and whether we have authoritative
            # `_nodes_by_dir` data available so we can determine which branch
            # the apply logic will take.
            logger.debug(
                "apply_index_change enter: nodes=%d has_nodes_by_dir=%r index=%r",
                len(nodes),
                (bool(self._nodes_by_dir) if hasattr(self, "_nodes_by_dir") else False),
                getattr(self, "index", None),
            )
            if not nodes:
                return None

            # Update the stored index first so renderers can consult it.
            try:
                t_index_start = time.perf_counter()
                self.index = new if new is not None else getattr(self, "index", None)
                t_index_end = time.perf_counter()
                logger.debug("apply_index_change: set index in %.3fms", (t_index_end - t_index_start) * 1000)
            except Exception as e:
                self.printException(e, "apply_index_change: setting index failed")

            # Fast-path: adjacent single-step moves (Up/Down)
            try:
                if old is not None and new is not None and abs((old or 0) - (new or 0)) == 1 and hasattr(self, "nodes"):
                    t0 = time.perf_counter()
                    t_nodes_start = time.perf_counter()
                    nodes_local = self.nodes()
                    t_nodes_end = time.perf_counter()
                    logger.debug(
                        "apply_index_change: nodes() fast-path took %.3fms",
                        (t_nodes_end - t_nodes_start) * 1000,
                    )
                    old_node = nodes_local[old] if 0 <= old < len(nodes_local) else None
                    new_node = nodes_local[new] if 0 <= new < len(nodes_local) else None
                    if old_node:
                        old_node.set_class(False, "active")
                        old_node.refresh()
                    if new_node:
                        new_node.set_class(True, "active")
                        new_node.refresh()
                    if hasattr(self, "_ensure_index_visible"):
                        self._ensure_index_visible()
                    try:
                        # Post-toggle debug: log visible node texts and active state
                        debug_nodes = []
                        for i, n in enumerate(self.nodes()):
                            txt = self.text_of(n)
                            try:
                                state = (
                                    n.has_class("active")
                                    if hasattr(n, "has_class")
                                    else ("active" in getattr(n, "classes", []))
                                )
                            except Exception as _e:
                                self.printException(_e, "apply_index_change: fast-path checking class failed")
                                state = False
                            debug_nodes.append(f"{i}:{txt}:{'A' if state else '_'}")
                        logger.debug("apply_index_change: fast-path post-toggle nodes=%s", debug_nodes)
                    except Exception as _e:
                        self.printException(_e, "apply_index_change: fast-path post-toggle logging failed")
                    t1 = time.perf_counter()
                    logger.debug("apply_index_change: fast-path toggle completed in %.3fms", (t1 - t0) * 1000)
                    return new_node
            except Exception as e:
                self.printException(e, "apply_index_change: fast-path toggle failed")

            # File-mode in-place path: for index-only changes (including
            # non-adjacent jumps from search), update classes directly
            # instead of re-rendering. This keeps highlight deterministic
            # and avoids duplicate-row races.
            try:
                if (
                    hasattr(self, "_nodes_by_dir")
                    and bool(getattr(self, "_nodes_by_dir", None))
                    and old is not None
                    and new is not None
                ):
                    nodes_local = self.nodes()
                    if nodes_local and 0 <= new < len(nodes_local):
                        old_node = nodes_local[old] if 0 <= old < len(nodes_local) else None
                        new_node = nodes_local[new]
                        if old_node is not None:
                            try:
                                old_node.set_class(False, "active")
                                old_node.refresh()
                            except Exception as _e:
                                self.printException(_e, "apply_index_change: filemode in-place clear old failed")
                        try:
                            new_node.set_class(True, "active")
                            new_node.refresh()
                        except Exception as _e:
                            self.printException(_e, "apply_index_change: filemode in-place set new failed")
                        try:
                            if hasattr(self, "_ensure_index_visible"):
                                self._ensure_index_visible()
                        except Exception as _e:
                            self.printException(_e, "apply_index_change: filemode in-place ensure visible failed")
                        logger.debug(
                            "apply_index_change: filemode in-place toggle old=%r new=%r nodes=%d",
                            old,
                            new,
                            len(nodes_local),
                        )
                        return new_node
            except Exception as e:
                self.printException(e, "apply_index_change: filemode in-place toggle failed")

            # Authoritative re-render when we have node data available.
            try:
                if hasattr(self, "_nodes_by_dir") and self._nodes_by_dir and hasattr(self, "_render_filemode_display"):
                    # Focus handoff frequently invokes apply_index_change(None, idx)
                    # with the same current index on startup. Re-rendering in
                    # that case can duplicate rows because clear/append run in
                    # the same cycle. For same-index updates, enforce classes
                    # in-place and skip authoritative re-render.
                    try:
                        current_index = getattr(self, "index", None)
                    except Exception as e:
                        self.printException(e, "apply_index_change: reading current index failed")
                        current_index = None
                    if new is not None and current_index == new and (old is None or old == new):
                        nodes_same = self.nodes()
                        if nodes_same and 0 <= new < len(nodes_same):
                            for i, node in enumerate(nodes_same):
                                try:
                                    node.set_class(i == new, "active")
                                except Exception as _e:
                                    self.printException(
                                        _e, "apply_index_change: node.set_class failed in same-index branch"
                                    )
                            try:
                                if hasattr(self, "_ensure_index_visible"):
                                    self._ensure_index_visible()
                            except Exception as _e:
                                self.printException(
                                    _e, "apply_index_change: _ensure_index_visible failed in same-index branch"
                                )
                            return nodes_same[new]

                    rel_dir = self.app.rel_dir
                    rel_file = self.app.rel_file
                    logger.debug(
                        "apply_index_change: calling _render_filemode_display rel_dir=%r rel_file=%r",
                        rel_dir,
                        rel_file,
                    )
                    t_rstart = time.perf_counter()
                    self._render_filemode_display(self._nodes_by_dir, rel_dir, rel_file)
                    t_rend = time.perf_counter()
                    logger.debug("apply_index_change: _render_filemode_display took %.3fms", (t_rend - t_rstart) * 1000)
                    t_nodes_after_start = time.perf_counter()
                    nodes_after = self.nodes()
                    t_nodes_after_end = time.perf_counter()
                    logger.debug(
                        "apply_index_change: nodes() after render took %.3fms",
                        (t_nodes_after_end - t_nodes_after_start) * 1000,
                    )
                    # Ensure the newly-rendered node has the active class set so
                    # visual highlighting is deterministic after an authoritative
                    # re-render (some render paths don't apply classes).
                    try:
                        if nodes_after and (new is not None and 0 <= new < len(nodes_after)):
                            for i, node in enumerate(nodes_after):
                                try:
                                    node.set_class(i == new, "active")
                                except Exception as _e:
                                    self.printException(
                                        _e, "apply_index_change: node.set_class failed in authoritative branch"
                                    )
                                try:
                                    node.refresh()
                                except Exception as _e:
                                    self.printException(
                                        _e, "apply_index_change: node.refresh failed in authoritative branch"
                                    )
                            try:
                                # Post-render debug: log visible node texts and active state
                                debug_nodes = []
                                for i, n in enumerate(nodes_after):
                                    txt = self.text_of(n)

                                    try:
                                        state = (
                                            n.has_class("active")
                                            if hasattr(n, "has_class")
                                            else ("active" in getattr(n, "classes", []))
                                        )
                                    except Exception as _e:
                                        self.printException(
                                            _e, "apply_index_change: authoritative checking class failed"
                                        )
                                        state = False
                                    debug_nodes.append(f"{i}:{txt}:{'A' if state else '_'}")
                                logger.debug("apply_index_change: authoritative post-render nodes=%s", debug_nodes)
                            except Exception as _e:
                                self.printException(_e, "apply_index_change: authoritative post-render logging failed")
                            if hasattr(self, "_ensure_index_visible"):
                                self._ensure_index_visible()
                            return nodes_after[new]
                    except Exception as _e:
                        self.printException(_e, "apply_index_change: applying active class after render failed")
                    return None
            except Exception as e:
                self.printException(e, "apply_index_change: re-rendering filemode display failed")

            # Fallback: ensure index visibility, apply active class, and
            # return the node if present. Some preparers don't trigger a
            # full authoritative re-render path, so ensure visual active
            # state here as a last-resort.
            try:
                self.index = new
                if hasattr(self, "_ensure_index_visible"):
                    self._ensure_index_visible()
                nodes_now = self.nodes()
                if nodes_now and (new is not None and 0 <= new < len(nodes_now)):
                    try:
                        # Clear/Set active class deterministically across nodes
                        for i, node in enumerate(nodes_now):
                            node.set_class(i == new, "active")
                            try:
                                node.refresh()
                            except Exception as e:
                                self.printException(e, "apply_index_change: node.refresh failed in fallback")
                        try:
                            # Post-fallback debug: log visible node texts and active state
                            debug_nodes = []
                            for i, n in enumerate(nodes_now):
                                txt = self.text_of(n)
                                try:
                                    state = (
                                        n.has_class("active")
                                        if hasattr(n, "has_class")
                                        else ("active" in getattr(n, "classes", []))
                                    )
                                except Exception as _e:
                                    self.printException(_e, "apply_index_change: fallback checking class failed")
                                    state = False
                                debug_nodes.append(f"{i}:{txt}:{'A' if state else '_'}")
                            if self.app.verbose > 2:
                                logger.debug("apply_index_change: fallback post-activation nodes=%s", debug_nodes)
                        except Exception as _e:
                            self.printException(_e, "apply_index_change: fallback post-activation logging failed")
                    except Exception as _e:
                        self.printException(_e, "apply_index_change: applying active class in fallback failed")
                    return nodes_now[new]
                return None
            except Exception as e:
                self.printException(e, "apply_index_change: fallback path failed")
                return None

        except Exception as e:
            self.printException(e, "apply_index_change failed")
            return None

    def _highlight_match(self, match: Optional[str]) -> None:
        """
        Highlight the first node whose raw text or _hash matches `match`.

        If `match` is None or no matching node is found, highlight the top item.
        Matching rules: exact match against `_raw_text`, exact match against
        `_hash`, or node text equality. For hashes allow prefix matching.
        """
        try:
            nodes = self.nodes()
            if not nodes:
                return
            if match:
                # Normalize match to canonical full path when possible so
                # comparisons against `_raw_text` (which we now store as
                # full paths for repo-mode rows) succeed.
                try:
                    repo_root_local = self.app.gitRepo.get_repo_root()
                    match_full = self._canonical_relpath(match, repo_root_local)
                except Exception as e:
                    match_full = match
                    self.printException(e, "_highlight_match: normalizing match failed")

                for i, node in enumerate(nodes):
                    try:
                        raw = getattr(node, "_raw_text", None)
                        h = getattr(node, "_hash", None)
                        try:
                            if raw is not None:
                                repo_root_local = self.app.gitRepo.get_repo_root()
                                node_full = self._canonical_relpath(raw, repo_root_local)
                            else:
                                node_full = None
                        except Exception as e:
                            node_full = raw
                            self.printException(e, "_highlight_match: computing node_full failed")

                        if node_full is not None and match_full is not None and node_full == match_full:
                            self._activate_index(i)
                            return

                        if h is not None and (h == match or str(h).startswith(match)):
                            self._activate_index(i)
                            return

                        # fallback to visible text equality
                        txt = self.text_of(node)
                        if txt == match:
                            self._activate_index(i)
                            return
                    except Exception as e:
                        self.printException(e, "_highlight_match: checking node failed")
            # No match found; highlight top
            self._highlight_top()
        except Exception as e:
            self.printException(e, "_highlight_match failed")

    def _find_and_activate(self, query: str, forward: bool = True) -> bool:
        """
        Find the next (or previous if forward=False) node whose visible
        text contains `query` (case-insensitive). If found, activate its
        index and return True. Otherwise return False.
        """
        try:
            if not query:
                return False
            nodes = self.nodes()
            if not nodes:
                return False
            total = len(nodes)
            start = int(getattr(self, "index", 0) or 0)
            logger.debug("_find_and_activate: query=%r forward=%r start=%r total=%r", query, forward, start, total)
            # Record last search even if no match found so repeated keys re-use it
            self._last_search = query
            q = query.casefold()

            # Build ordered index sequence depending on direction
            if forward:
                seq = list(range(start + 1, total)) + list(range(0, start + 1))
            else:
                seq = list(range(start - 1, -1, -1)) + list(range(total - 1, start - 1, -1))

            for i in seq:
                try:
                    node = nodes[i]
                    # Prefer an explicit search text attached at render time.
                    st = getattr(node, "_search_text", None)
                    if st is not None:
                        txt = st
                    else:
                        txt = self.text_of(node)
                    if txt is None:
                        continue
                    logger.debug("_find_and_activate: checking idx=%d text=%r", i, txt)
                    if q in str(txt).casefold():
                        logger.debug("_find_and_activate: match at idx=%d text=%r", i, txt)
                        self._last_search = query
                        self._activate_index(i)
                        return True
                    # Fallback: attempt to extract text from children/renderables
                    try:
                        alt_parts: list[str] = []
                        raw = getattr(node, "_raw_text", None)
                        if raw:
                            alt_parts.append(str(raw))
                        fname = getattr(node, "_filename", None)
                        if fname:
                            alt_parts.append(str(fname))

                        # If node itself is a Label, prefer its text/renderable
                        try:
                            if isinstance(node, Label):
                                if getattr(node, "text", None):
                                    alt_parts.append(str(node.text))
                                else:
                                    rend = getattr(node, "renderable", None)
                                    if isinstance(rend, Text):
                                        alt_parts.append(rend.plain)
                        except Exception as e:
                            self.printException(e, "_find_and_activate: node text/renderable extraction failed")

                        # Inspect children for Label or Text renderables
                        ch = getattr(node, "children", None) or []
                        for c in ch:
                            try:
                                if isinstance(c, Label):
                                    if getattr(c, "text", None):
                                        alt_parts.append(str(c.text))
                                        continue
                                    rend = getattr(c, "renderable", None)
                                    if isinstance(rend, Text):
                                        alt_parts.append(rend.plain)
                                        continue
                                # Generic renderable on child
                                rend = getattr(c, "renderable", None)
                                if isinstance(rend, Text):
                                    alt_parts.append(rend.plain)
                            except Exception as e:
                                self.printException(e, "_find_and_activate: child node text/renderable extraction failed")

                        alt = " ".join([p for p in alt_parts if p])
                        logger.debug("_find_and_activate: fallback combined alt for idx=%d -> %r", i, alt)
                        if alt:
                            if q in alt.casefold():
                                logger.debug("_find_and_activate: fallback match at idx=%d text=%r", i, alt)
                                self._last_search = query
                                self._activate_index(i)
                                return True
                    except Exception as e:
                        self.printException(e, "_find_and_activate: fallback extraction failed")
                except Exception as e:
                    self.printException(e, "_find_and_activate: checking node failed")
            logger.debug("_find_and_activate: no match for %r", query)
            return False
        except Exception as e:
            self.printException(e, "_find_and_activate failed")
            return False

    def _highlight_top(self) -> None:
        """
        Schedule highlighting of the logical top item for this widget.

        Centralized implementation so subclasses don't need to duplicate
        the call_after_refresh/fallback pattern. Uses `self._min_index`
        when available.
        """
        try:
            top = self._min_index or 0
            try:
                self.call_after_refresh(lambda: self._safe_activate_index(top))
            except Exception as e:
                self.printException(e, "_highlight_top: scheduling index set failed")
                # Fall back to direct activation if scheduling fails
                self._activate_index(top)
        except Exception as e:
            self.printException(e, "AppBase._highlight_top failed")

    # Consolidated safe-call helpers used for scheduling post-refresh actions.
    # These centralize try/except logic so lambdas passed to
    # `call_after_refresh` remain small and identical behavior isn't
    # duplicated across the codebase.
    def _safe_activate_index(self, idx: int) -> None:
        """
        Invoke `_activate_index` and handle any exceptions.

        Intended to be called from lambdas passed to `call_after_refresh`.
        """
        self._activate_index(idx)

    def _safe_scroll_to_widget(self, node, animate: bool = False) -> None:
        """
        Scroll the given `node` into view (safe wrapper).

        Uses the framework `scroll_to_widget` API when available and logs
        exceptions instead of raising so UI callbacks remain stable.
        """
        try:
            # Prefer the framework-provided scroll, if present.
            self.scroll_to_widget(node, animate=animate)
        except Exception as e:
            self.printException(e, "_safe_scroll_to_widget failed")

    def _safe_node_scroll_visible(self, node, visible: bool = True) -> None:
        """
        Call a node's `scroll_visible` method safely.

        This is a non-fatal fallback used when the widget-level
        `scroll_to_widget` API is not available.
        """
        try:
            getattr(node, "scroll_visible", lambda *a, **k: None)(visible)
        except Exception as e:
            self.printException(e, "_safe_node_scroll_visible failed")

    def error_message(self, message: str) -> None:
        """
        Show a simple MessageModal with `message` pushed to the app.

        Centralizing this ensures callers don't need to reference
        `self.app.push_screen(...)` and provides a single exception
        handling site for message display.
        """
        try:
            self.app.push_screen(MessageModal(message))
        except Exception as e:
            self.printException(e, "AppBase.error_message failed")

    def _finalize_prep_common(
        self, curr_hash: str | None = None, prev_hash: str | None = None, path: str | None = None
    ) -> None:
        """
        Shared app-level sync used by all preparers.

        This function performs the conservative updates to the application
        state (`app.current_hash`, `app.previous_hash`, `app.rel_dir`/`app.rel_file`)
        and invokes `_compute_selected_pair` when appropriate. It does not
        perform widget-specific highlighting or marking.
        """
        try:
            if curr_hash is not None or prev_hash is not None:
                try:
                    self.app.current_hash = curr_hash
                    self.app.previous_hash = prev_hash
                except Exception as _ex:
                    self.printException(_ex, "_finalize_prep_common: updating app hashes failed")
                return

            # If no explicit hashes provided, attempt to compute the selected pair.
            if hasattr(self, "_compute_selected_pair"):
                self._compute_selected_pair()

            # Normalize and store repo-relative path components when provided.
            if path is not None:
                try:
                    rel = os.path.normpath(path)
                    rd, rf = os.path.split(rel)
                    self.app.rel_dir = rd or ""
                    self.app.rel_file = rf or ""
                except Exception as _ex:
                    self.printException(_ex, "_finalize_prep_common: setting app.rel_dir/rel_file failed")

        except Exception as e:
            self.printException(e, "_finalize_prep_common: app state sync failed")

    # Common key handler for list widgets.
    def on_key(self, event: events.Key) -> None:
        """Handle common find shortcuts ('>' forward, '<' backward)."""
        try:
            ch = getattr(event, "character", None)
            if ch not in (">", "<"):
                return
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "AppBase.on_key: event.stop failed")

            # If overlay is already present, do not recreate it; just refocus
            # the existing input so repeated '>' or '<' behaves predictably.
            try:
                if getattr(self.app, "_find_overlay_widget", None) is not None:
                    try:
                        inp = self.app.screen.query_one("#find-input", Input)
                    except Exception:
                        inp = self.app.query_one("#find-input", Input)
                    self.app.call_later(lambda: inp.focus())
                    return
            except Exception as e:
                self.printException(e, "AppBase.on_key: refocus existing overlay failed")

            try:
                init = getattr(self, "_last_search", "") or ""
                forward = ch == ">"
                title = "Find (forward)" if forward else "Find (reverse)"
                self.app.show_find_overlay(init, title, lambda v, s=self, f=forward: s._find_and_activate(v, f))
            except Exception as e:
                self.printException(e, "AppBase.on_key: show_find_overlay failed")
        except Exception as e:
            self.printException(e, "AppBase.on_key failed")

    # Key handlers: implement navigation as `key_*` methods so subclasses
    # may override them individually and keep key logic co-located with
    # widget state.

    def key_up(self, event: events.Key | None = None) -> None:
        """Move the selection up by one item, honoring `event.stop()` if provided."""
        logger.debug("AppBase.key_up called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_up: event.stop failed")
            min_idx = self._min_index or 0
            cur = self.index
            logger.debug("AppBase.key_up: computed cur=%r min_idx=%r", cur, min_idx)
            if cur is None:
                self._activate_index(min_idx)
                return
            if cur <= min_idx:
                return
            new_index = cur - 1
            logger.debug("AppBase.key_up: moving from %r to %r", cur, new_index)
            self._activate_index(new_index)
        except Exception as e:
            self.printException(e, "key_up outer failure")

        self._log_visible_items("key_up after processing index change")

    def key_down(self, event: events.Key | None = None) -> None:
        """Move the selection down by one item, honoring `event.stop()` if provided."""
        logger.debug("AppBase.key_down called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_down: event.stop failed")
            # Preserve zero index correctly: don't treat 0 as falsy.
            cur = self.index if getattr(self, "index", None) is not None else (self._min_index or 0)
            nodes = self.nodes()
            logger.debug("AppBase.key_down: cur=%r nodes=%d min_index=%r", cur, len(nodes), self._min_index)
            if not nodes:
                return
            new_index = min(len(nodes) - 1, cur + 1)
            logger.debug("AppBase.key_down: moving from %r to %r", cur, new_index)
            self._activate_index(new_index)
        except Exception as e:
            self.printException(e, "key_down outer failure")

        try:
            nodes = self.nodes()
            idx = getattr(self, "index", None)
            fname = None
            if idx is not None and 0 <= idx < len(nodes):
                fname = getattr(nodes[idx], "_filename", None) or getattr(nodes[idx], "_raw_text", None)
            logger.debug("AppBase.key_down: post-action index=%r filename=%r", idx, fname)
        except Exception as _e:
            self.printException(_e, "key_down post-action logging failed")

        self._log_visible_items("key_down after processing index change")

    def key_page_down(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """
        Scroll forward by approximately one page and activate the new index.

        When `recursive` is true this is an alias invocation and logging is
        suppressed to avoid duplicate messages.
        """
        if not recursive:
            logger.debug("AppBase.key_pagedown called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_page_down: event.stop failed")
            nodes = self.nodes()
            if not nodes:
                return
            current_index = self.index or 0
            try:
                region = self.scrollable_content_region
                visible_height = int(getattr(region, "height", 10))
            except Exception as e:
                self.printException(e, "key_page_down: measuring region height failed")
                visible_height = 10
            page_size = max(1, visible_height // 2)
            new_index = min(current_index + page_size, len(nodes) - 1)
            try:
                try:
                    self._page_scroll = True
                except Exception as e:
                    self.printException(e, "key_page_down: setting _page_scroll failed")

                # Immediate visual toggle: deactivate old node and activate
                # the new node before scheduling the authoritative update.
                try:
                    nodes_local = self.nodes()
                    old_node = nodes_local[current_index] if 0 <= current_index < len(nodes_local) else None
                    new_node = nodes_local[new_index] if 0 <= new_index < len(nodes_local) else None
                    if old_node:
                        try:
                            old_node.set_class(False, "active")
                            old_node.refresh()
                        except Exception as e:
                            self.printException(e, "key_page_down: clearing old_node active failed")
                    if new_node:
                        try:
                            new_node.set_class(True, "active")
                            new_node.refresh()
                        except Exception as e:
                            self.printException(e, "key_page_down: setting new_node active failed")
                    try:
                        self.index = new_index
                    except Exception as e:
                        self.printException(e, "key_page_down: setting index failed")
                    if hasattr(self, "_ensure_index_visible"):
                        self._ensure_index_visible()
                except Exception as e:
                    self.printException(e, "key_page_down: immediate toggle failed")

                # Authoritative activation to keep internal state consistent.
                self._activate_index(new_index)
            except Exception as e:
                self.printException(e, "key_page_down: activate failed")
        except Exception as e:
            self.printException(e, "key_page_down failed")

    # Alias handlers: terminals/terminfo may report different key names for
    # page up / page down (e.g. 'pageup', 'pagedown', 'prior', 'next'). Provide
    # aliases that delegate to the canonical handlers so keys are handled.
    def key_pageup(self, event: events.Key | None = None) -> None:
        """Alias for `key_page_up`; preserves event semantics and logging."""
        logger.debug("AppBase.key_pageup called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_up(event, recursive=True)

    def key_pagedown(self, event: events.Key | None = None) -> None:
        """Alias for `key_page_down`; preserves event semantics and logging."""
        logger.debug("AppBase.key_pagedown called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_down(event, recursive=True)

    def key_page_up(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """
        Scroll backward by approximately one page and activate the new index.

        When `recursive` is true this is an alias invocation and logging is
        suppressed to avoid duplicate messages.
        """
        if not recursive:
            logger.debug("AppBase.key_pageup called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_page_up: event.stop failed")
            nodes = self.nodes()
            if not nodes:
                return
            current_index = self.index or 0
            try:
                region = self.scrollable_content_region
                visible_height = int(getattr(region, "height", 10))
            except Exception as e:
                self.printException(e, "key_page_up: measuring region height failed")
                visible_height = 10
            page_size = max(1, visible_height // 2)
            min_idx = self._min_index or 0
            new_index = max(current_index - page_size, min_idx)
            # Use _activate_index which schedules the index change after refresh
            try:
                try:
                    self._page_scroll = True
                except Exception as e:
                    self.printException(e, "key_page_up: setting _page_scroll failed")

                # Immediate visual toggle for page-up: deactivate old and
                # activate new node to avoid accent fallback during paging.
                try:
                    nodes_local = self.nodes()
                    old_node = nodes_local[current_index] if 0 <= current_index < len(nodes_local) else None
                    new_node = nodes_local[new_index] if 0 <= new_index < len(nodes_local) else None
                    if old_node:
                        try:
                            old_node.set_class(False, "active")
                            old_node.refresh()
                        except Exception as e:
                            self.printException(e, "key_page_up: clearing old_node active failed")
                    if new_node:
                        try:
                            new_node.set_class(True, "active")
                            new_node.refresh()
                        except Exception as e:
                            self.printException(e, "key_page_up: setting new_node active failed")
                    try:
                        self.index = new_index
                    except Exception as e:
                        self.printException(e, "key_page_up: setting index failed")
                    if hasattr(self, "_ensure_index_visible"):
                        self._ensure_index_visible()
                except Exception as e:
                    self.printException(e, "key_page_up: immediate toggle failed")

                self._activate_index(new_index)
            except Exception as e:
                self.printException(e, "key_page_up: activate failed")
        except Exception as e:
            self.printException(e, "key_page_up failed")

    def key_prior(self, event: events.Key | None = None) -> None:
        """Alias mapping for terminals that report PageUp as 'prior'."""
        logger.debug("AppBase.key_prior called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_up(event, recursive=True)

    def key_next(self, event: events.Key | None = None) -> None:
        """Alias mapping for terminals that report PageDown as 'next'."""
        logger.debug("AppBase.key_next called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_page_down(event, recursive=True)

    def key_home(self, event: events.Key | None = None) -> None:
        """Move selection to the first selectable index."""
        logger.debug("AppBase.key_home called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_home: event.stop failed")
            min_idx = self._min_index or 0
            self._activate_index(min_idx)
        except Exception as e:
            self.printException(e, "key_home failed")

    def key_end(self, event: events.Key | None = None) -> None:
        """Move selection to the last selectable index."""
        logger.debug("AppBase.key_end called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_end: event.stop failed")
            nodes = self._nodes
            if not nodes:
                return
            last_idx = max(0, len(nodes) - 1)
            self._activate_index(last_idx)
        except Exception as e:
            self.printException(e, "key_end failed")

    # Default stubs for left/right/enter — subclasses should override as needed
    def key_left(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Default left-key handler; subclasses may override to provide actions."""
        if not recursive:
            logger.debug("AppBase.key_left called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "AppBase.key_left: event.stop failed")
        except Exception as e:
            self.printException(e, "AppBase.key_left failed")
        self._log_visible_items("key_left after processing index change")
        return None

    def key_right(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Default right-key handler; subclasses may override to provide actions."""
        if not recursive:
            logger.debug("AppBase.key_right called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "AppBase.key_right: event.stop failed")
        except Exception as e:
            self.printException(e, "AppBase.key_right failed")
        self._log_visible_items("key_right after processing index change")
        return None

    def key_enter(self, event: events.Key | None = None) -> None:
        """Default Enter-key handler; subclasses may override to provide actions."""
        logger.debug("AppBase.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "AppBase.key_enter: event.stop failed")
        except Exception as e:
            self.printException(e, "AppBase.key_enter failed")
        self._log_visible_items("key_enter after processing index change")
        return None

    def _preserve_filemode_selection_for_refresh(self) -> None:
        """Capture current file-mode selection so redraw can reselect it."""
        try:
            fl = self.app.file_mode_file_list
            nodes = fl.nodes()
            idx = fl.index if fl.index is not None else (fl._min_index or 0)
            if idx is None or not (0 <= idx < len(nodes)):
                return
            node = nodes[idx]
            selected = getattr(node, "_filename", None) or getattr(node, "_raw_text", None)
            if not selected:
                return
            fl._preselected_filename = selected
            logger.debug("%s: preserved file-mode selection=%r", type(self).__name__, selected)
        except Exception as e:
            self.printException(e, "_preserve_filemode_selection_for_refresh failed")

    def toggle_ignore(self, event: events.Key | None = None) -> None:
        """Toggle app-level ignored-file visibility and refresh file-mode list."""
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "toggle_ignore: event.stop failed")
            self._preserve_filemode_selection_for_refresh()
            self.app.no_ignored = not bool(self.app.no_ignored)
            logger.debug("%s.toggle_ignore: no_ignored=%r", type(self).__name__, self.app.no_ignored)
            self.app.file_mode_file_list.prepFileModeFileList()
        except Exception as e:
            self.printException(e, "toggle_ignore failed")

    def toggle_untracked(self, event: events.Key | None = None) -> None:
        """Toggle app-level untracked-file visibility and refresh file-mode list."""
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "toggle_untracked: event.stop failed")
            self._preserve_filemode_selection_for_refresh()
            self.app.no_untracked = not bool(self.app.no_untracked)
            logger.debug("%s.toggle_untracked: no_untracked=%r", type(self).__name__, self.app.no_untracked)
            self.app.file_mode_file_list.prepFileModeFileList()
        except Exception as e:
            self.printException(e, "toggle_untracked failed")

    def key_w_helper(self, event: events.Key | None = None) -> None:
        """
        Common helper to prompt and write snapshot files for a visible widget.

        Pops a modal asking whether to write the older (previous_hash), newer
        (current_hash), or both versions of the current `app.rel_dir`/`app.rel_file`.
        The modal performs the actual file extraction and writing.
        """
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_w_helper: event.stop failed")

            # In history views, recompute the selected commit pair so snapshot
            # writes follow the currently highlighted row, not stale app hashes.
            prev_hash = self.app.previous_hash
            curr_hash = self.app.current_hash
            try:
                if hasattr(self, "_compute_selected_pair"):
                    pair = self._compute_selected_pair()
                    if isinstance(pair, tuple) and len(pair) == 2:
                        prev_hash, curr_hash = pair
            except Exception as e:
                self.printException(e, "key_w_helper: _compute_selected_pair failed")

            # Build an absolute filepath from app rel_dir/rel_file when available
            filepath = self.app.gitRepo.abs_path_for(self.app.rel_dir, self.app.rel_file)

            logger.debug(
                "%s.key_w_helper: rel_dir=%r rel_file=%r filepath=%r prev_hash=%r curr_hash=%r output_directory=%r",
                type(self).__name__,
                getattr(self.app, "rel_dir", None),
                getattr(self.app, "rel_file", None),
                filepath,
                prev_hash,
                curr_hash,
                getattr(self.app, "output_directory", None),
            )

            # If filepath appears to be a directory, keep it as-is
            if filepath and os.path.isdir(filepath):
                pass

            try:
                # Prefer asking the GitRepo for the canonical repo root
                repo_root_val = self.app.gitRepo.get_repo_root()
                relpath = os.path.normpath(
                    os.path.join(getattr(self.app, "rel_dir", "") or "", getattr(self.app, "rel_file", "") or "")
                )

                # Compute the full ordered list of hashes (newest→oldest) so
                # the modal can offer the "all" option when there are
                # intermediate commits between the two boundary hashes.
                try:
                    all_hashes = self.app.gitRepo.getHashesBetween(relpath, prev_hash, curr_hash)
                except Exception as e:
                    self.printException(e, "key_w_helper: getHashesBetween failed")
                    all_hashes = []

                has_intermediates = len(all_hashes) > 2
                if has_intermediates:
                    msg = (
                        f"Create {os.path.basename(filepath)}.HASH.\n\n"
                        "Do you wish to write the (o)lder file, the (n)ewer file, (b)oth,\n"
                        f"or (a)ll {len(all_hashes)} versions (old to new and in between)?\n\n"
                        "(Any other key to cancel.)"
                    )
                else:
                    msg = (
                        f"Create {os.path.basename(filepath)}.HASH.\n\n"
                        "Do you wish to write the (o)lder file, the (n)ewer file, or (b)oth?\n\n"
                        "(Any other key to cancel.)"
                    )
                logger.debug("%s.key_w_helper: presenting SaveSnapshotModal for %r", type(self).__name__, filepath)
                self.app.push_screen(
                    SaveSnapshotModal(
                        msg,
                        filepath=filepath,
                        prev_hash=prev_hash,
                        curr_hash=curr_hash,
                        repo_root=repo_root_val,
                        all_hashes=all_hashes if has_intermediates else None,
                    )
                )
            except Exception as e:
                self.printException(e, "key_w_helper: push SaveSnapshotModal failed")
        except Exception as e:
            self.printException(e, "key_w_helper failed")


class SaveSnapshotModal(AppException, ModalScreen):
    """
    Modal that prompts the user to write older/newer versions of a file.

    The modal handles the key press and writes the requested snapshots
    to files named '<filepath>.<hash>'. Supported keys: o/O (older),
    n/N (newer), b/B (both), a/A (all, when intermediates exist). Any
    other key cancels.
    """

    def __init__(
        self,
        message: str | None = None,
        filepath: str | None = None,
        prev_hash: str | None = None,
        curr_hash: str | None = None,
        repo_root: str | None = None,
        all_hashes: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.message = message or ""
        self.filepath = filepath
        self.prev_hash = prev_hash
        self.curr_hash = curr_hash
        self.repo_root = repo_root
        # Full ordered list (newest→oldest) when there are intermediates;
        # None means the "a" option is not available.
        self.all_hashes = all_hashes

    def compose(self):
        """Compose the modal contents (a single Label with the message)."""
        try:
            # yield Label(Text(self.message, style="bold"))
            boxed = Panel(Text(self.message, style="bold"), expand=False)
            yield Vertical(
                Horizontal(Label(boxed, id="msg-modal"), id="msg-modal-row"),
                Label(Text("Press any key to continue", style="dim"), id="msg-modal-prompt"),
                id="msg-modal-wrapper",
            )
        except Exception as e:
            # Best-effort: avoid modal failure — ensure we log the original
            self.printException(e, "SaveSnapshotModal.compose failed")

            try:
                # yield Label(Text(self.message or "", style="bold"))
                boxed = Panel(Text(self.message, style="bold"), expand=False)
                yield Vertical(
                    Horizontal(Label(boxed, id="msg-modal"), id="msg-modal-row"),
                    Label(Text("Press any key to continue", style="dim"), id="msg-modal-prompt"),
                    id="msg-modal-wrapper",
                )
            except Exception as e2:
                # If even yielding fails, log and give up
                self.printException(e2, "SaveSnapshotModal.compose fallback failed")

    def on_key(self, event: events.Key) -> None:
        """Handle a single key press: o/O -> older, n/N -> newer, b/B -> both."""
        saved_paths: list[str] = []
        failed_saves: list[tuple[str, str]] = []

        def _attempt_save(label: str, hashval: str | None) -> None:
            if not hashval:
                failed_saves.append((label, "missing hash value"))
                return
            out_path, err = self._save(hashval)
            if out_path:
                saved_paths.append(out_path)
                return
            failed_saves.append((label, err or f"save failed for hash {hashval}"))

        try:
            key = getattr(event, "key", "")
            logger.debug(
                "SaveSnapshotModal.on_key: key=%r filepath=%r prev_hash=%r curr_hash=%r",
                key,
                self.filepath,
                self.prev_hash,
                self.curr_hash,
            )
            try:
                if key not in ("q", "Q"):
                    # Prevent further handling unless user pressed q/Q to cancel
                    event.stop()
            except Exception as e:
                self.printException(e, "SaveSnapshotModal.on_key: event.stop failed")

            try:
                # Older (prev_hash)
                if key in ("o", "O"):
                    _attempt_save("older", self.prev_hash)
                    return

                # Newer (curr_hash)
                if key in ("n", "N"):
                    _attempt_save("newer", self.curr_hash)
                    return

                # Both
                if key in ("b", "B"):
                    _attempt_save("older", self.prev_hash)
                    _attempt_save("newer", self.curr_hash)
                    return

                # All versions (only available when intermediates exist)
                if key in ("a", "A") and self.all_hashes:
                    for i, h in enumerate(self.all_hashes):
                        label = f"version {i + 1} of {len(self.all_hashes)} ({h[:8]})"
                        _attempt_save(label, h)
                    return
            except Exception as e:
                self.printException(e, "SaveSnapshotModal.on_key: _save failed")

        finally:
            try:
                self.app.pop_screen()
            except Exception as e:
                self.printException(e, "SaveSnapshotModal.on_key: pop_screen failed")

            # Show a confirmation popup for each successfully written snapshot.
            for saved_path in saved_paths:
                try:
                    logger.debug("SaveSnapshotModal.on_key: showing success modal for %r", saved_path)
                    self.app.push_screen(MessageModal(f"Snapshot written:\n\n{saved_path}"))
                except Exception as e:
                    self.printException(e, "SaveSnapshotModal.on_key: push success modal failed")

            for label, reason in failed_saves:
                try:
                    logger.debug("SaveSnapshotModal.on_key: showing failure modal label=%r reason=%r", label, reason)
                    self.app.push_screen(MessageModal(f"Snapshot save failed ({label}):\n\n{reason}"))
                except Exception as e:
                    self.printException(e, "SaveSnapshotModal.on_key: push failure modal failed")

    def _save(self, hashval: str | None) -> tuple[str | None, str | None]:
        """Write the file content for the given hash into a target snapshot file."""
        if not hashval or not self.filepath:
            logger.debug("SaveSnapshotModal._save: skipped (hashval=%r filepath=%r)", hashval, self.filepath)
            return None, "missing hash or filepath"

        try:
            relpath = os.path.relpath(self.filepath, self.repo_root)
        except Exception as e:
            self.printException(e, "SaveSnapshotModal._save: computing relpath failed")
            relpath = os.path.basename(self.filepath)

        # Normalize repository-relative components into (reldir, relfile)
        try:
            gitrepo = self.app.gitRepo
            reldir, relfile = gitrepo.repo_rel_path_to_reldir_relfile(relpath)
        except Exception as e:
            self.printException(e, "SaveSnapshotModal._save: computing reldir/relfile failed")
            reldir, relfile = os.path.dirname(relpath), os.path.basename(relpath)

        # Build target path from app-level output_directory when configured.
        # Keep snapshots under that directory while preserving repo-relative
        # path structure to avoid basename collisions.
        target_path: str
        outdir = self.app.output_directory
        if outdir:
            try:
                os.makedirs(outdir, exist_ok=True)
            except Exception as e:
                self.printException(e, f"SaveSnapshotModal._save: creating output-directory failed ({outdir})")
                return None, f"could not create output directory: {outdir}"

            try:
                safe_rel = os.path.normpath(relpath)
                if os.path.isabs(safe_rel) or safe_rel == ".." or safe_rel.startswith(f"..{os.sep}"):
                    safe_rel = os.path.basename(self.filepath)
            except Exception as e:
                self.printException(e, "SaveSnapshotModal._save: normalizing relpath for output-directory failed")
                safe_rel = os.path.basename(self.filepath)

            target_path = os.path.join(outdir, f"{safe_rel}.{hashval}")
        else:
            target_path = f"{self.filepath}.{hashval}"

        logger.debug(
            "SaveSnapshotModal._save: hash=%r relpath=%r reldir=%r relfile=%r target=%r",
            hashval,
            relpath,
            reldir,
            relfile,
            target_path,
        )

        # Helper to write bytes to target
        def _write_bytes(bdata: bytes) -> bool:
            try:
                ddir = os.path.dirname(target_path)
                if ddir and not os.path.exists(ddir):
                    try:
                        os.makedirs(ddir, exist_ok=True)
                    except Exception as e:
                        self.printException(e, "SaveSnapshotModal._save: makedirs failed")

                logger.debug(
                    "SaveSnapshotModal._save: writing %d bytes to %r",
                    len(bdata),
                    target_path,
                )
                with open(target_path, "wb") as out:
                    out.write(bdata)
                logger.info("SaveSnapshotModal._save: wrote snapshot %s", target_path)
                return True
            except Exception as e:
                self.printException(e, f"SaveSnapshotModal._write failed for {target_path}")
                logger.debug("SaveSnapshotModal._save: write failed for %r", target_path)
                return False

        # Different strategies based on hash semantics
        if hashval == "MODS":
            # Working tree (unstaged) version
            try:
                logger.debug("SaveSnapshotModal._save: source=working-tree path=%r", self.filepath)
                with open(self.filepath, "rb") as f:
                    data = f.read()
                if _write_bytes(data):
                    return target_path, None
                return None, f"write failed: {target_path}"
            except Exception as e:
                self.printException(e, "SaveSnapshotModal._save read working-tree failed")
                return None, f"working-tree read failed: {self.filepath}"

        if hashval == "STAGED":
            # Read from index via git show :<relpath>
            try:
                logger.debug("SaveSnapshotModal._save: source=STAGED reldir=%r relfile=%r", reldir, relfile)
                gitrepo = self.app.gitRepo
                data = gitrepo.getFileContents("STAGED", reldir, relfile)
                if data is None:
                    raise Exception("git show STAGED failed")
                if _write_bytes(data):
                    return target_path, None
                return None, f"write failed: {target_path}"
            except Exception as e:
                self.printException(e, "SaveSnapshotModal._save STAGED failed")
                return None, f"STAGED read failed: {relpath}"

        # Otherwise treat as commit-ish hash: git show <hash>:<relpath>
        try:
            logger.debug(
                "SaveSnapshotModal._save: source=commit hash=%r reldir=%r relfile=%r",
                hashval,
                reldir,
                relfile,
            )
            gitrepo = self.app.gitRepo
            data = gitrepo.getFileContents(hashval, reldir, relfile)
            if data is None:
                raise Exception("git show failed")
            if _write_bytes(data):
                return target_path, None
            return None, f"write failed: {target_path}"
        except Exception as e:
            self.printException(e, "SaveSnapshotModal._save commit show failed")
            return None, f"commit read failed for {hashval}: {relpath}"


# Top-level modal so callers can push it via `self.app.push_screen(_TBDModal(...))`
class MessageModal(ModalScreen):
    """
    Simple modal that shows a message (default "") and closes on any key.
    """

    def __init__(self, message: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.message = message or ""

    def compose(self):
        """
        Compose the modal contents: boxed message and static prompt.

        The main message is shown inside a Rich `Panel` to provide a
        visible box; a dim prompt label is shown below instructing the
        user to press any key to continue. The message is rendered as
        Markdown.
        """
        try:
            # Main boxed message (Panel handles the box drawing)
            # Render the message as Markdown for rich formatting
            boxed = Panel(Markdown(self.message), expand=False)
            # Wrap the boxed message in centering containers so it appears
            # in the middle of the screen.
            yield Vertical(
                Horizontal(Label(boxed, id="msg-modal"), id="msg-modal-row"),
                Label(Text("Press any key to continue", style="dim"), id="msg-modal-prompt"),
                id="msg-modal-wrapper",
            )
        except Exception as e:
            printException(e, "MessageModal.compose failed")

    def on_key(self, event: events.Key) -> None:
        """Close the modal on any key press."""
        try:
            try:
                event.stop()
            except Exception as _use_pass:
                pass
            # Record the key used to dismiss the modal so callers can inspect it.
            try:
                self.dismiss_key = getattr(event, "key", None)
                self.app.last_modal_key = self.dismiss_key
                # non-fatal if app doesn't accept attribute assignment
            except Exception as _ex:
                printException(_ex, "MessageModal.on_key: recording dismiss key failed")
            try:
                self.app.pop_screen()
            except Exception as e:
                printException(e, "MessageModal.on_key: pop_screen failed")
        except Exception as e:
            printException(e, "MessageModal.on_key failed")


class EditMessageModal(ModalScreen):
    """
    Modal that presents an editable text box for the user to modify content.

    Allows editing multi-line text and returns either the modified text or
    None if the user cancels the operation.
    - Ctrl+S: Save and return the edited text
    - Escape: Cancel and return None
    """

    def __init__(self, initial_text: str = "", title: str = "Edit text", on_save=None, **kwargs) -> None:
        """
        Initialize the modal with initial text.
        Args:
            initial_text: The text to pre-populate the editor with.
            title: A title or prompt to display above the editor.
            on_save: Optional callback to invoke after saving (receives edited text).
        """
        super().__init__(**kwargs)
        self.initial_text = initial_text
        self.title = title
        self.edited_text = initial_text
        self.on_save = on_save

    def compose(self):
        """
        Compose the modal with a text editor and instructions.

        Creates a layout with:
        - Title/prompt label
        - TextArea widget for editing
        - Instructions label for keyboard commands
        """
        try:
            text_area = TextArea(
                text=self.initial_text,
                id="edit-text-area",
                language="text",
            )
            yield Vertical(
                Label(Text(self.title, style="bold"), id="edit-title"),
                text_area,
                Label(
                    Text("Ctrl+S to save | Escape to cancel", style="dim"),
                    id="edit-prompt",
                ),
                id="edit-modal-wrapper",
            )
        except Exception as e:
            printException(e, "EditMessageModal.compose failed")

    def on_key(self, event: events.Key) -> None:
        """Handle save/cancel keys for commit-message editing."""
        try:
            key = getattr(event, "key", "")
            logger.debug(f"EditMessageModal.on_key: key={key!r}")

            if key == "ctrl+s":
                logger.debug("EditMessageModal.on_key: save key pressed")
                try:
                    event.stop()
                except Exception as ex:
                    self.printException(ex, "EditMessageModal.on_key: event.stop() failed")

                try:
                    text_area = self.query_one("#edit-text-area", TextArea)
                    self.edited_text = text_area.text
                    logger.debug("EditMessageModal.on_key: retrieved text, length=%s", len(self.edited_text))
                    logger.debug("EditMessageModal.on_key: edited_text=%r", self.edited_text)
                except Exception as e:
                    printException(e, "EditMessageModal.on_key: retrieving text failed")
                    self.edited_text = self.initial_text
                    logger.debug("EditMessageModal.on_key: exception during text retrieval, using initial_text")

                try:
                    self.app.pop_screen()
                    logger.debug("EditMessageModal.on_key: pop_screen completed")
                except Exception as e:
                    printException(e, "EditMessageModal.on_key: pop_screen failed")
                    return

                if self.on_save:
                    try:
                        self.on_save()
                    except Exception as e:
                        printException(e, "EditMessageModal.on_key: on_save callback failed")
                return

            # Cancel on Escape
            if key == "escape":
                logger.debug("EditMessageModal.on_key: cancel key pressed (escape)")
                try:
                    event.stop()
                except Exception as ex:
                    self.printException(ex, "EditMessageModal.on_key: event.stop() failed")

                try:
                    self.app.pop_screen()
                    logger.debug("EditMessageModal.on_key: pop_screen completed")
                except Exception as e:
                    printException(e, "EditMessageModal.on_key: pop_screen failed")
                return
        except Exception as e:
            self.printException(e, "EditMessageModal.on_key failed")


class FindModal(ModalScreen):
    """
    Modal that prompts the user for a single-line search string.

    Submits the value via `on_submit(text)` when Enter is pressed.
    """

    def __init__(self, initial_text: str = "", title: str = "Find", on_submit=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.initial_text = initial_text or ""
        self.title = title
        self.on_submit = on_submit

    def compose(self):
        """Compose a single-line Input docked at top for quick searches."""
        try:
            inp = Input(value=self.initial_text, id="find-input")
            yield Vertical(
                Horizontal(Label(Text(self.title, style="bold"), id="find-title"), inp),
                id="find-modal-wrapper",
            )
        except Exception as e:
            self.printException(e, "FindModal.compose failed")

    def on_mount(self) -> None:
        """Focus the input when the modal is shown."""
        try:
            inp = self.query_one("#find-input", Input)
            inp.focus()
            logger.debug("FindModal.on_mount: focused input id=%r initial_text=%r", "#find-input", self.initial_text)
        except Exception as e:
            self.printException(e, "FindModal.on_mount failed")

        # Ensure the modal background is transparent so the rest of the
        # UI remains visible when the find modal is shown. Some Textual
        # backends render a solid overlay for ModalScreen; enforce
        # transparency on the screen and wrapper at mount time.
        try:
            try:
                self.styles.background = "transparent"
            except Exception as e:
                self.printException(e, "FindModal.on_mount: setting screen background failed")
            try:
                wrapper = self.query_one("#find-modal-wrapper")
                wrapper.styles.background = "transparent"
            except Exception as e:
                self.printException(e, "FindModal.on_mount: setting wrapper background failed")
                pass
        except Exception as e:
            self.printException(e, "FindModal.on_mount: setting transparent background failed")

    def on_input_submitted(self, message: Input.Submitted) -> None:
        """Handle Input submit (Enter) and call on_submit callback."""
        try:
            try:
                val = message.value
            except Exception as e:
                self.printException(e, "FindModal.on_input_submitted: reading value failed")
                val = ""

            logger.debug("FindModal.on_input_submitted: value=%r", val)

            try:
                self.app.pop_screen()
            except Exception as e:
                self.printException(e, "FindModal.on_input_submitted: pop_screen failed")

            if self.on_submit:
                try:
                    self.on_submit(val)
                except Exception as e:
                    self.printException(e, "FindModal.on_input_submitted: on_submit callback failed")
        except Exception as e:
            self.printException(e, "FindModal.on_input_submitted failed")


class RightSideBase(AppBase):
    """
    Mixin for right-side widgets that support opening files with the 'o' key.

    Provides the _load_open_file method used by FileModeHistoryList and RepoModeFileList.
    """

    def _load_open_file(self, filepath: str, commit_hash: str) -> None:
        """Load and display file content asynchronously."""
        try:
            self.app.openfile_list.prepOpenFileList(filepath, commit_hash)
            self.app.openfile_list._update_title()
        except Exception as e:
            self.printException(e, "_load_open_file failed")


class FullScreenBase(AppBase):
    """
    Base class for widgets supporting fullscreen/split view toggles.

    Provides common key handlers and state management for DiffList and OpenFileList.
    Subclasses should call super().__init__() and can override specific behaviors.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Track the layout we came from for fullscreen restoration
        self._saved_layout: str | None = None
        # Where to return when leaving the view when in split mode
        # Subclasses should set this to (state_name, widget_id, footer)
        self._go_back: tuple | None = None
        # Subclasses should set these in their __init__:
        # _split_layout_history_file: split layout with history on left/file on right
        # _split_layout_file_history: split layout with file on left/history on right
        # _split_layouts: tuple of layout names where fullscreen can be invoked
        # _fullscreen_layout: the name of the fullscreen layout to switch to
        # _fullscreen_widget_id: widget ID to focus in fullscreen
        # _fullscreen_footer: footer to display in fullscreen
        self._split_layout_history_file: str = ""
        self._split_layout_file_history: str = ""
        self._split_layouts: tuple[str, ...] = ()
        self._fullscreen_layout: str = ""
        self._fullscreen_widget_id: str = ""
        self._split_footer = None
        self._fullscreen_footer = None

    def key_right(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Promote to fullscreen when in a split layout."""
        if not recursive:
            logger.debug(
                f"{self.__class__.__name__}.key_right called: key=%r index=%r", getattr(event, "key", None), self.index
            )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, f"{self.__class__.__name__}.key_right: event.stop failed")
            current = self.app._current_layout
            if current in self._split_layouts:
                # Save the current layout before going fullscreen
                self._saved_layout = current
                self.app.change_state(
                    self._fullscreen_layout, f"#{self._fullscreen_widget_id}", self._fullscreen_footer
                )
        except Exception as e:
            self.printException(e, f"{self.__class__.__name__}.key_right failed")

    def key_left(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Return from fullscreen to split, or close split view to return to prior state."""
        if not recursive:
            logger.debug(
                f"{self.__class__.__name__}.key_left called: key=%r index=%r", getattr(event, "key", None), self.index
            )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, f"{self.__class__.__name__}.key_left: event.stop failed")
            try:
                # If we're in fullscreen, restore the saved layout.
                current = self.app._current_layout
                if current == self._fullscreen_layout:
                    try:
                        target = self._saved_layout or self._split_layout_history_file
                        # restore layout
                        self.app.change_layout(target)
                        self.app.change_footer(self._split_footer)
                        # clear saved layout
                        self._saved_layout = None
                        return
                    except Exception as e:
                        self.printException(e, f"{self.__class__.__name__}.key_left restore layout failed")

                # Otherwise fall back to the recorded go-back tuple.
                if self._go_back:
                    state_name, widget_id, footer = self._go_back
                    self.app.change_state(state_name, f"#{widget_id}", footer)
            except Exception as e:
                self.printException(e, f"{self.__class__.__name__}.key_left change_state failed")
        except Exception as e:
            self.printException(e, f"{self.__class__.__name__}.key_left failed")

        self._log_visible_items("key_left after processing index change")

    def key_f(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Toggle fullscreen/split view."""
        if not recursive:
            logger.debug(
                f"{self.__class__.__name__}.key_f called: key=%r index=%r", getattr(event, "key", None), self.index
            )
        return self.key_enter(event, recursive=True)

    def key_F(self, event: events.Key | None = None) -> None:
        """Alias for key_f (Shift-F)."""
        logger.debug(f"{self.__class__.__name__}.key_F called: key=%r", getattr(event, "key", None))
        return self.key_f(event, recursive=True)

    def key_t(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Toggle between paired layouts (split layout A <-> split layout B)."""
        if not recursive:
            logger.debug(f"{self.__class__.__name__}.key_t called: key=%r", getattr(event, "key", None))
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, f"{self.__class__.__name__}.key_t: event.stop failed")

            current = self.app._current_layout
            if len(self._split_layouts) >= 2:
                # Toggle between the two split layouts
                if current == self._split_layout_file_history:
                    self.app.change_state(
                        self._split_layout_history_file, f"#{self._fullscreen_widget_id}", self._split_footer
                    )
                elif current == self._split_layout_history_file:
                    self.app.change_state(
                        self._split_layout_file_history, f"#{self._fullscreen_widget_id}", self._split_footer
                    )
                elif current == self._fullscreen_layout:
                    # Delegate to app toggle to handle fullscreen layout toggle
                    self.app.toggle(current, event)
        except Exception as e:
            self.printException(e, f"{self.__class__.__name__}.key_t failed")

    def key_T(self, event: events.Key | None = None) -> None:
        """Alias for key_t (Shift-T)."""
        logger.debug(f"{self.__class__.__name__}.key_T called: key=%r", getattr(event, "key", None))
        return self.key_t(event, recursive=True)

    def key_enter(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Toggle between fullscreen and split view."""
        if not recursive:
            logger.debug(
                f"{self.__class__.__name__}.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index
            )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, f"{self.__class__.__name__}.key_enter: event.stop failed")

            current = self.app._current_layout
            if current == self._fullscreen_layout:
                # Restore the saved layout
                self.app.change_state(self._saved_layout, f"#{self._fullscreen_widget_id}", self._split_footer)
            elif current in self._split_layouts:
                # Save current and go fullscreen
                self._saved_layout = current
                self.app.change_state(
                    self._fullscreen_layout, f"#{self._fullscreen_widget_id}", self._fullscreen_footer
                )
        except Exception as e:
            self.printException(e, f"{self.__class__.__name__}.key_enter failed")

    def key_w(self, event: events.Key | None = None) -> None:
        """Prompt to save snapshot files for the current content."""
        logger.debug(
            "%s.key_w called: key=%r layout=%r rel_dir=%r rel_file=%r prev_hash=%r curr_hash=%r",
            self.__class__.__name__,
            getattr(event, "key", None),
            getattr(self.app, "_current_layout", None),
            getattr(self.app, "rel_dir", None),
            getattr(self.app, "rel_file", None),
            getattr(self.app, "previous_hash", None),
            getattr(self.app, "current_hash", None),
        )
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, f"{self.__class__.__name__}.key_w: event.stop failed")
        self.key_w_helper(event)

    def key_W(self, event: events.Key | None = None) -> None:
        """Alias for key_w (Shift-W)."""
        logger.debug(f"{self.__class__.__name__}.key_W called: key=%r", getattr(event, "key", None))
        return self.key_w(event)


class FileListBase(AppBase):
    """
    Base for file list widgets.

    Provides safe focus handling, highlighting helpers, and small default
    implementations that concrete subclasses can override.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Mark this base as a file-list so AppBase.watch_index can act
        # conservatively based on widget type flags instead of isinstance.
        self.is_file_list = 1
        # File lists are selectable from the first data row by default.
        # Ensure subclasses inherit an explicit _min_index of 0 so they
        # can override if they prepend non-selectable header rows.
        self._min_index = 0
        # Program-managed preselection marker used by render/keypress flow.
        # Initialize here so static checks know the attribute exists.
        self._preselected_filename = None

    def on_focus(self) -> None:
        """Ensure the widget has a valid `index` when it receives focus."""
        try:
            if self.index is None:
                self.index = self._min_index or 0
        except Exception as e:
            self.printException(e, "FileListBase.on_focus")

    def _ensure_index_visible(self) -> None:
        """
        Ensure the current `index` node is scrolled into view.

        Safe no-op when scrolling APIs are unavailable.
        """
        try:
            idx = self.index or 0
            # If header rows were pruned by the list virtualization when
            # scrolling, re-render the prepared filelist using the last-
            # collected `_nodes_by_dir` so the canonical Key:/Directory
            # header rows are present again. This keeps the visual UI
            # consistent when users page back to the top of a long list.
            try:
                # GitDiffNavTool always creates the external header Labels;
                # assume they exist and skip the in-list header re-render path.
                # This avoids redundant re-renders and keeps behavior deterministic.
                pass
            except Exception as e:
                self.printException(e, "_ensure_index_visible: re-rendering filemode display failed")

            logger.debug(
                "_activate_or_open: entry idx=%r rel_dir=%r rel_file=%r", idx, self.app.rel_dir, self.app.rel_file
            )
            nodes = self.nodes()
            if not nodes or idx is None:
                return
            if not (0 <= idx < len(nodes)):
                return
            node = nodes[idx]
            # Prefer Textual's scroll_to_widget when available
            if hasattr(self, "scroll_to_widget"):
                try:
                    self.call_after_refresh(lambda: self._safe_scroll_to_widget(node, animate=False))
                    return
                except Exception as e:
                    self.printException(e, "_ensure_index_visible: scroll_to_widget failed")
            # Fallback to node.scroll_visible if provided
            try:
                t0 = time.perf_counter()
                self.call_after_refresh(lambda: self._safe_node_scroll_visible(node, True))
                t1 = time.perf_counter()
                logger.debug("_ensure_index_visible: scroll_visible scheduled in %.3fms", (t1 - t0) * 1000)
            except Exception as e:
                self.printException(e, "_ensure_index_visible: scroll_visible failed")
        except Exception as e:
            self.printException(e, "_ensure_index_visible failed")

    def _highlight_filename(self, filename: str) -> None:
        """Find the first node matching `filename` and move the index there."""
        try:
            nodes = self.nodes()
            # Normalize the provided filename to a canonical full path when
            # possible so comparisons match stored `_raw_text` values.
            try:
                match_full = None
                if filename:
                    repo_root_local = self.app.gitRepo.get_repo_root()
                    match_full = self._canonical_relpath(filename, repo_root_local)
            except Exception as e:
                match_full = filename
                self.printException(e, "_highlight_filename: normalizing filename failed")

            for i, node in enumerate(nodes):
                try:
                    # Prefer matching against canonical `_raw_text` when
                    # available; fall back to visible text equality.
                    raw = getattr(node, "_raw_text", None)
                    if raw is not None and match_full is not None:
                        try:
                            repo_root_local = self.app.gitRepo.get_repo_root()
                            node_full = self._canonical_relpath(raw, repo_root_local)
                        except Exception as e:
                            node_full = raw
                            self.printException(e, "_highlight_filename: computing node_full failed")
                        if node_full == match_full:
                            try:
                                self.call_after_refresh(lambda: self._safe_activate_index(i))
                            except Exception as e:
                                self.printException(e, "_highlight_filename: scheduling index set failed")
                                self._activate_index(i)
                            return

                    text = self.text_of(node)
                    if text == filename:
                        try:
                            self.call_after_refresh(lambda: self._safe_activate_index(i))
                        except Exception as e:
                            self.printException(e, "_highlight_filename: scheduling index set failed")
                            self._activate_index(i)
                        return
                except Exception as e:
                    self.printException(e, "_highlight_filename: checking node failed")
        except Exception as e:
            self.printException(e, "_highlight_filename failed")

    def _finalize_filelist_prep(
        self, curr_hash: str | None = None, prev_hash: str | None = None, path: str | None = None
    ) -> None:
        """Finalize for file-list widgets: highlight by filename/path then sync common state."""
        try:
            try:
                if path is not None:
                    # Prefer filename/path highlighting when a path is provided
                    self._highlight_filename(path)
                elif curr_hash:
                    self._highlight_match(curr_hash)
                else:
                    self._highlight_top()
            except Exception as e:
                self.printException(e, "FileListBase._finalize_filelist_prep: highlight step failed")

            self._finalize_prep_common(curr_hash=curr_hash, prev_hash=prev_hash, path=path)

        except Exception as e:
            self.printException(e, "FileListBase._finalize_filelist_prep failed")

    def on_prune(self, event) -> None:
        """
        Handle Textual prune events for diagnostic and recovery.

        Log the event and any pruned nodes if available. If pruning
        removed the persistent filelist headers (Key:/Directory),
        schedule a re-render of the prepared filelist from the last
        collected `_nodes_by_dir` so the headers are restored.
        """
        try:
            logger.debug("on_prune: event=%r", event)

            # Track whether pruned nodes included our header rows;
            # default to False so we only re-render when necessary.
            pruned_headers_found = False
            try:
                pruned = getattr(event, "pruned", None) or getattr(event, "nodes", None)
                logger.debug("on_prune: pruned_attr=%r type=%s", pruned, type(event))
                if pruned is not None:
                    try:
                        count = len(pruned) if hasattr(pruned, "__len__") else "unknown"
                        logger.debug("on_prune: pruned_count=%s", count)

                        # Inspect the first few pruned nodes and record notable flags
                        sample = []
                        pruned_headers_found = False
                        max_sample = 12
                        for i, node in enumerate(pruned):
                            if i >= max_sample:
                                break
                            try:
                                is_key_header = bool(getattr(node, "_filelist_key_header", False))
                                is_dir_header = bool(getattr(node, "_dir_header", False))
                                text = self._child_filename(node) if hasattr(self, "_child_filename") else str(node)
                                sample.append(
                                    {
                                        "idx": i,
                                        "text": text,
                                        "_filelist_key_header": is_key_header,
                                        "_dir_header": is_dir_header,
                                    }
                                )
                                if is_key_header or is_dir_header:
                                    pruned_headers_found = True
                            except Exception as e:
                                self.printException(e, "on_prune: inspecting pruned node failed")
                                sample.append({"idx": i, "text": "<inspect-failed>", "exc": repr(e)})

                        logger.debug("on_prune: pruned_sample=%r", sample)
                        logger.debug("on_prune: pruned_headers_found=%r", pruned_headers_found)
                    except Exception as e:
                        self.printException(e, "on_prune: counting pruned items failed")
                        logger.debug("on_prune: pruned items present but count failed")
            except Exception as e:
                self.printException(e, "on_prune: introspection failed")

            # If headers were pruned re-render the prepared filelist so
            # the canonical Key:/Directory header rows are reinstated.
            try:
                # Only schedule a re-render when header rows were actually
                # pruned; unconditional re-renders can cause a render loop
                # when Textual emits prune messages during normal virtualization.
                if pruned_headers_found and self._nodes_by_dir:
                    self.call_after_refresh(
                        lambda: self._render_filemode_display(self._nodes_by_dir, self.app.rel_dir, self.app.rel_file)
                    )
            except Exception as e:
                self.printException(e, "on_prune: schedule re-render failed")
        except Exception as e:
            self.printException(e, "on_prune failed")

    def _child_filename(self, node) -> str:
        """
        Return the filename or visible text for a child `node`.

        Safe wrapper around `text_of` that falls back to stringifying the
        node when extraction fails.
        """
        return self.text_of(node)

    def _render_hash_header(self, prev_hash: str | None, curr_hash: str | None) -> None:
        """
        Render the non-selectable hash header row for repo-mode file lists.

        The header is appended as a `ListItem` with `_hash_header=True` and
        `_selectable=False` so navigation logic can skip it.
        """
        # Build the header text and attempt to update the external
        # `#right-file-hash` Label. If that label is not present fall
        # back to appending an in-list non-selectable header for
        # compatibility with older layouts.
        try:
            logger.debug("_render_hash_header: prev_hash=%r curr_hash=%r", prev_hash, curr_hash)
            display_hash_length = self.app.hash_length

            def _short(h: str | None) -> str:
                if not h:
                    return "None"
                return h[:display_hash_length] if len(h) > display_hash_length else h

            hash_text = f"Hashes: prev={_short(prev_hash)}  curr={_short(curr_hash)}"

            # Try updating external label first
            try:
                app = self.app
                if app is not None:
                    hash_lbl = app.query_one("#right-file-hash", Label)
                    hash_lbl.update(Text(hash_text, style=STYLE_FILELIST_KEY))
                    return
            except Exception as e:
                # External label not present or update failed; fall back
                # to appending an in-list header so the caller still sees
                # the header when no external label exists.
                self.printException(e, "_render_hash_header: external label update failed or not present; falling back")

            # Fallback: append a non-selectable ListItem header (legacy)
            try:
                hash_item = ListItem(Label(Text(hash_text, style=STYLE_FILELIST_KEY)))
                hash_item._hash_header = True
                hash_item._selectable = False
                self.append(hash_item)
            except Exception as e:
                self.printException(e, "_render_hash_header: creating/appending in-list header failed")
        except Exception as e:
            self.printException(e, "_render_hash_header failed")

    def _populate_from_file_infos(
        self, file_infos: list[dict], active_raw: str | None = None, active_index: int | None = None
    ) -> None:
        """
        Append ListItems for each dict in `file_infos`.

        Each dict is expected to have keys: `name`, `is_dir`, `raw`, `repo_status`.
        - `raw`: repo-relative path for display and storage (required)
        - `is_dir`: boolean flag indicating directory status
        - All stored paths (_raw_text) are repo-relative.
        """
        try:
            t_total_start = time.perf_counter()
            next_idx = 0
            appended_count = 0
            for info in file_infos:
                try:
                    name = info.get("name")
                    is_dir = info.get("is_dir", False)
                    raw = info.get("raw", name)  # Repo-relative path (primary data)
                    repo_status = info.get("repo_status")

                    if is_dir:
                        tag = "→"
                        display_name = f"{name}/"
                        display = f"{tag} {display_name}"
                        style = STYLE_DIR
                        item = ListItem(Label(Text(display, style=style)))
                        try:
                            item._is_dir = True
                            item._repo_status = None
                            # Always store repo-relative path from raw parameter
                            item._raw_text = raw or name
                            item._filename = name
                            logger.debug("_populate_from_file_infos: adding dir item %s", raw or name)
                            # Determine activation before appending to avoid render races
                            try:
                                should_activate = False
                                if active_index is not None and next_idx == active_index:
                                    should_activate = True
                                if active_raw is not None and item._raw_text == active_raw:
                                    should_activate = True
                                if should_activate:
                                    try:
                                        item.set_class(True, "active")
                                    except Exception as e:
                                        self.printException(
                                            e, "_populate_from_file_infos: set_class failed for dir; trying add_class"
                                        )
                                        try:
                                            item.add_class("active")
                                        except Exception as e2:
                                            self.printException(
                                                e2, "_populate_from_file_infos: add_class failed for dir"
                                            )
                                    try:
                                        self.index = next_idx
                                    except Exception as e:
                                        self.printException(
                                            e,
                                            "_populate_from_file_infos: setting index attribute failed for dir; trying setattr",
                                        )
                                        try:
                                            setattr(self, "index", next_idx)
                                        except Exception as e2:
                                            self.printException(
                                                e2, "_populate_from_file_infos: setattr for index failed for dir"
                                            )
                            except Exception as _e:
                                self.printException(
                                    _e, "_populate_from_file_infos: pre-append activation failed for dir"
                                )
                            try:
                                t_app_start = time.perf_counter()
                                self.append(item)
                                t_app_end = time.perf_counter()
                                logger.debug(
                                    "_populate_from_file_infos: appended dir item in %.3fms index=%d",
                                    (t_app_end - t_app_start) * 1000,
                                    next_idx,
                                )
                                appended_count += 1
                            except Exception as e:
                                self.printException(e, "_populate_from_file_infos: append dir failed")
                            next_idx += 1
                        except Exception as e:
                            self.printException(e, "_populate_from_file_infos append dir failed")
                        continue

                    marker = MARKERS.get(repo_status, " ")
                    if repo_status == "conflicted":
                        style = STYLE_CONFLICTED
                    elif repo_status == "staged":
                        style = STYLE_STAGED
                    elif repo_status == "wt_deleted":
                        style = STYLE_WT_DELETED
                    elif repo_status == "ignored":
                        style = STYLE_IGNORED
                    elif repo_status == "modified":
                        style = STYLE_MODIFIED
                    elif repo_status == "untracked":
                        style = STYLE_UNTRACKED
                    else:
                        style = STYLE_DEFAULT

                    display = f"{marker} {name}"
                    try:
                        if style:
                            item = ListItem(Label(Text(display, style=style)))
                        else:
                            item = ListItem(Label(display))
                        item._repo_status = repo_status
                        item._is_dir = False
                        # Always store repo-relative path from raw parameter
                        item._raw_text = raw or name
                        item._filename = name
                        # Determine activation before appending to avoid render races
                        try:
                            should_activate = False
                            if active_index is not None and next_idx == active_index:
                                should_activate = True
                            if active_raw is not None and item._raw_text == active_raw:
                                should_activate = True
                            if should_activate:
                                try:
                                    item.set_class(True, "active")
                                except Exception as e:
                                    self.printException(
                                        e, f"_populate_from_file_infos: set_class failed for {name}; trying add_class"
                                    )
                                    try:
                                        item.add_class("active")
                                    except Exception as e2:
                                        self.printException(
                                            e2, f"_populate_from_file_infos: add_class failed for {name}"
                                        )
                                try:
                                    self.index = next_idx
                                except Exception as e:
                                    self.printException(
                                        e,
                                        f"_populate_from_file_infos: setting index attribute failed for {name}; trying setattr",
                                    )
                                    try:
                                        setattr(self, "index", next_idx)
                                    except Exception as e2:
                                        self.printException(
                                            e2, f"_populate_from_file_infos: setattr for index failed for {name}"
                                        )
                        except Exception as _e:
                            self.printException(
                                _e, f"_populate_from_file_infos: pre-append activation failed for {name}"
                            )
                        try:
                            t_app_start = time.perf_counter()
                            self.append(item)
                            t_app_end = time.perf_counter()
                            logger.debug(
                                "_populate_from_file_infos: appended file item in %.3fms index=%d name=%r",
                                (t_app_end - t_app_start) * 1000,
                                next_idx,
                                name,
                            )
                            appended_count += 1
                        except Exception as e:
                            self.printException(e, f"_populate_from_file_infos appending {name} failed")
                        next_idx += 1
                    except Exception as e:
                        self.printException(e, f"_populate_from_file_infos appending {name} failed")
                        continue
                except Exception as e:
                    self.printException(e, f"_populate_from_file_infos processing entry failed")
                    continue
        except Exception as e:
            self.printException(e, "_populate_from_file_infos failed")
        finally:
            try:
                t_total_end = time.perf_counter()
                logger.debug(
                    "_populate_from_file_infos: total time %.3fms appended=%d",
                    (t_total_end - t_total_start) * 1000,
                    appended_count,
                )
            except Exception as e:
                self.printException(e, "_populate_from_file_infos: final timing logging failed")


class FileModeFileList(FileListBase):
    """
    File-mode file list: shows files for a working tree path.

    For regen Step 3 this class provides a `prepFileModeFileList` stub and
    default `key_left`/`key_right` handlers.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # History of directory basenames visited (left-to-right) and the
        # current position within that history. Maintained so left/right
        # navigation can restore child highlights when moving up/down.
        self._highlight_history: list[str] = []
        self._highlight_pos: int = -1
        # Map from repo-relative directory -> last-selected child filename
        # This lets us restore a child's highlight when re-entering a dir.
        self._last_child_by_dir: dict[str, str] = {}
        # Per-widget highlight background style.
        self.highlight_bg_style = HIGHLIGHT_FILELIST_BG
        # Guard against overlapping render passes that can duplicate rows.
        self._render_filemode_in_progress: bool = False


    def _collect_filemode_nodes(self) -> None:
        """
        Collect git-based file lists and build nodes_by_dir mapping.

        Stores the mapping on `self._nodes_by_dir`.
        Safe on errors; exceptions are logged and an empty mapping is stored.
        """
        nodes_by_dir: dict = {}
        # nodes_by_dir structure:
        # - key: repository-relative directory path ("" for repo root)
        # - value: dict with keys:
        #     'dirs'  : set[str] of immediate child directory basenames
        #     'files' : list[tuple[name:str, status:str, iso:str|None]] for
        #               immediate files in that directory
        # This compact mapping lets UI preparers render a single directory
        # slice quickly without holding full absolute paths.
        try:
            gitrepo = self.app.gitRepo
            # Gather committed, untracked and ignored entries and working-tree mods.
            try:
                committed = gitrepo.getFileListAtHash("HEAD")
            except Exception as e:
                self.printException(e, "_collect_filemode_nodes: getFileListAtHash failed")
                committed = []

            if not self.app.no_untracked:
                try:
                    untracked = gitrepo.getFileListUntracked()
                except Exception as e:
                    self.printException(e, "_collect_filemode_nodes: getFileListUntracked failed")
                    untracked = []
            else:
                untracked = []

            if not self.app.no_ignored:
                try:
                    ignored = gitrepo.getFileListIgnored()
                except Exception as e:
                    self.printException(e, "_collect_filemode_nodes: getFileListIgnored failed")
                    ignored = []
            else:
                ignored = []

            # Gather working-tree modifications (MODS): list of (path,status)
            try:
                mods = gitrepo.getFileListBetweenNormalizedHashes("HEAD", "MODS")
            except Exception as e:
                self.printException(e, "_collect_filemode_nodes: getFileListBetweenNormalizedHashes failed")
                mods = []

            def ensure_dir_node(d: str):
                if d not in nodes_by_dir:
                    nodes_by_dir[d] = {"dirs": set(), "files": []}

            def register_file(rel_path: str, status: str, iso: str | None):
                parent = os.path.dirname(rel_path) or ""
                ensure_dir_node(parent)
                name = os.path.basename(rel_path)
                files = nodes_by_dir[parent]["files"]
                # If file already present in this directory, update its entry
                for idx, (n, s, t) in enumerate(files):
                    if n == name:
                        files[idx] = (name, status, iso)
                        break
                else:
                    files.append((name, status, iso))

                # register parent as a child in its parent directory
                if parent:
                    grand = os.path.dirname(parent) or ""
                    ensure_dir_node(grand)
                    nodes_by_dir[grand]["dirs"].add(os.path.basename(parent))

            # First, add committed files from HEAD as tracked_clean baseline
            for p, iso, _status in committed:
                try:
                    register_file(p, "tracked_clean", None)
                except Exception as e:
                    self.printException(e, "_collect_filemode_nodes: registering committed file failed")
                    continue

            # Add untracked entries: (path, iso, status)
            for p, iso, status in untracked:
                try:
                    register_file(p, status, iso)
                except Exception as e:
                    self.printException(e, "_collect_filemode_nodes: registering untracked file failed")
                    continue

            # Add ignored entries: (path, iso, status)
            for p, iso, status in ignored:
                try:
                    register_file(p, status, iso)
                except Exception as e:
                    self.printException(e, "_collect_filemode_nodes: registering ignored file failed")
                    continue

            # Add mods entries: (path, iso, status) - override committed
            for p, iso, s in mods:
                try:
                    register_file(p, s, iso)
                except Exception as e:
                    self.printException(e, "_collect_filemode_nodes: registering mod file failed")
                    continue

        except Exception as e:
            self.printException(e, "_collect_filemode_nodes failed")

        # Persist the collected nodes in the instance so callers need not
        # hold a local copy.
        self._nodes_by_dir = nodes_by_dir

    def _render_filemode_display(self, nodes_by_dir: dict, rel_dir: str, rel_path: str) -> None:
        """
        Render the file-list UI for the given `nodes_by_dir` and `rel_dir`.

        Preserves existing ListItem metadata semantics so callers need not
        change downstream logic.
        """
        try:
            if self._render_filemode_in_progress:
                return
            self._render_filemode_in_progress = True

            # Prepare the widget for fresh rendering: clear existing rows
            # and insert the canonical key legend header.
            # Capture current index before clearing children; clearing the
            # widget may reset `self.index` internally, so preserve the
            # intended index here for use when deciding which item to
            # highlight after we rebuild the list.
            pre_clear_index = getattr(self, "index", None)

            try:
                children_before = len(getattr(self, "children", []))
                logger.debug("_render_filemode_display: clearing children count=%d", children_before)
                self.clear()
                logger.debug(
                    "_render_filemode_display: clear() completed; children now=%d", len(getattr(self, "children", []))
                )
            except Exception as _e:
                self.printException(_e, "_render_filemode_display: clear() failed")

            # Build the entire set of ListItem rows locally first so we can
            # decide highlighting and inline-label styles up-front. After
            # computing every line's desired style we will write the list to
            # the widget in a single pass which avoids post-mutation races.
            # Update the static header labels that live outside the ListView
            # so they are never subject to Textual's virtualization/pruning.
            try:
                app = self.app
                if app is not None:
                    try:
                        key_lbl = app.query_one("#left-file-key", Label)
                        try:
                            key_lbl.update(Text(FILELIST_KEY_ROW_TEXT, style=STYLE_FILELIST_KEY))
                        except Exception as e:
                            self.printException(e, "_render_filemode_display: updating left-file-key with style failed")
                            key_lbl.update(FILELIST_KEY_ROW_TEXT)
                    except Exception as _e:
                        self.printException(_e, "_render_filemode_display: updating left-file-key failed")
                    try:
                        dir_lbl = app.query_one("#left-file-dir", Label)
                        try:
                            dir_lbl.update(Text(f"Directory: {rel_dir or 'Repository Root'}", style=STYLE_HELP_BG))
                        except Exception as e:
                            self.printException(e, "_render_filemode_display: updating left-file-dir with style failed")
                            dir_lbl.update(f"Directory: {rel_dir or 'Repository Root'}")
                    except Exception as _e:
                        self.printException(_e, "_render_filemode_display: updating left-file-dir failed")
            except Exception as _e:
                self.printException(_e, "_render_filemode_display: updating external headers failed")

            new_items: list = []
            slice_node = nodes_by_dir.get(rel_dir, {"dirs": set(), "files": []})

            # If we're not at the repo root, add a parent entry ('..')
            # so users can navigate up the tree.
            try:
                if rel_dir:
                    parent_rel = os.path.dirname(rel_dir) or ""
                    # Display as a directory entry with '..' name
                    try:
                        parent_item = ListItem(Label(Text(f"← ../", style=STYLE_DIR)))
                        parent_item._is_dir = True
                        parent_item._filename = ".."
                        parent_item._raw_text = parent_rel
                        new_items.append(parent_item)
                    except Exception as _e:
                        self.printException(_e, "_render_filemode_display: building parent entry failed")
            except Exception as _e:
                self.printException(_e, "_render_filemode_display: parent-entry block failed")

            # Show directories first (use right-arrow marker and include '/').
            for dname in sorted(slice_node["dirs"]):
                try:
                    # Compute repo-relative raw path for this directory
                    try:
                        raw = os.path.join(rel_dir, dname) if rel_dir else dname
                    except Exception as _e:
                        self.printException(_e, "_render_filemode_display: os.path.join failed")
                        raw = dname

                    # Determine if this directory (recursively) contains any modified files.
                    try:
                        has_modified = False
                        prefix = raw + os.sep
                        for k, v in nodes_by_dir.items():
                            # match the directory itself or any descendant directories
                            if k == raw or k.startswith(prefix):
                                files_list = v.get("files", []) if isinstance(v, dict) else []
                                for _name, _status, _iso in files_list:
                                    if _status == "modified":
                                        has_modified = True
                                        break
                            if has_modified:
                                break
                    except Exception as _e:
                        self.printException(_e, "_render_filemode_display: checking modified children failed")

                    # Build a Text label where the arrow prefix is styled specially
                    try:
                        if has_modified:
                            # txt = Text("→ ", style=STYLE_MODIFIED_DIR)
                            # txt.append(f"{dname}/", style=STYLE_DIR)
                            txt = Text(f"→ {dname}/", style=STYLE_MODIFIED_DIR)
                        else:
                            txt = Text(f"→ {dname}/", style=STYLE_DIR)
                    except Exception as _e:
                        self.printException(_e, "_render_filemode_display: building dir Text failed")
                        txt = Text(f"→ {dname}/", style=STYLE_DIR)

                    try:
                        dir_item = ListItem(Label(txt))
                        dir_item._is_dir = True
                        dir_item._filename = dname
                        dir_item._raw_text = raw
                    except Exception as _e:
                        self.printException(_e, "_render_filemode_display: setting dir item metadata failed")
                    new_items.append(dir_item)
                except Exception as e:
                    self.printException(e, "_render_filemode_display: appending dir entry failed")

            # Then show files (use marker key and per-status styles)
            for name, status, iso in sorted(slice_node["files"], key=lambda x: x[0]):
                try:
                    ts = (iso if iso is not None else "").replace("T", " ")
                    marker = MARKERS.get(status, " ")
                    if status == "conflicted":
                        base_style = STYLE_CONFLICTED
                    elif status == "staged":
                        base_style = STYLE_STAGED
                    elif status == "wt_deleted":
                        base_style = STYLE_WT_DELETED
                    elif status == "ignored":
                        base_style = STYLE_IGNORED
                    elif status == "modified":
                        base_style = STYLE_MODIFIED
                    elif status == "untracked":
                        base_style = STYLE_UNTRACKED
                    else:
                        base_style = STYLE_DEFAULT

                    # Preserve leading non-breaking space marker; avoid
                    # stripping leading whitespace which would remove it.
                    display_parts = [marker, name]
                    if ts:
                        display_parts.append(ts)
                    display = " ".join(display_parts)
                    # If marker is a non-breaking space, prefix a zero-width
                    # joiner so UI trimming doesn't remove the NBSP.
                    try:
                        if marker == "\u00a0":
                            display = "\u200d" + display
                    except Exception as _e:
                        self.printException(_e, "_render_filemode_display: NBSP handling failed")

                    # Create the label now; highlight application will be
                    # decided later once we know the desired selected index.
                    item = ListItem(Label(Text(display, style=base_style)))
                    try:
                        item._repo_status = status
                        item._is_dir = False
                        # store repo-relative raw path
                        try:
                            raw = os.path.join(rel_dir, name) if rel_dir else name
                        except Exception as _e:
                            self.printException(_e, "_render_filemode_display: os.path.join failed for file entry")
                            raw = name
                        item._raw_text = raw
                        item._filename = name
                    except Exception as _e:
                        self.printException(_e, "_render_filemode_display: setting file item metadata failed")
                    new_items.append(item)
                except Exception as e:
                    self.printException(e, "_render_filemode_display: appending file entry failed")

            # Finalize minimal population state and ensure navigation starts
            # on the first actual entry. Header rows are now external
            # to the ListView so the first selectable index is 0.
            try:
                # Decide which index should be selected/highlighted before
                # committing anything to the widget.
                self._populated = True
                # Headers are rendered outside the virtualized list now.
                header_count = 0
                if len(new_items) > header_count:
                    self._min_index = header_count
                else:
                    self._min_index = 0

                # Choose desired index: prefer an explicit preselection, else
                # preserve `self.index` when valid, otherwise fall back to the
                # first selectable index.
                desired = self._preselected_filename
                desired_index = None
                try:
                    if desired:
                        logger.debug(
                            "_render_filemode_display: preselected candidate=%r history=%r pos=%r",
                            desired,
                            self._highlight_history,
                            self._highlight_pos,
                        )
                        # Diagnostic: log a short sample of the prepared items
                        try:
                            sample = []
                            for si, sn in enumerate(new_items):
                                if si >= 80:
                                    break
                                try:
                                    sf = getattr(sn, "_filename", None)
                                    sr = getattr(sn, "_raw_text", None)
                                    sample.append((si, sf, sr))
                                except Exception as _e:
                                    self.printException(_e, "_render_filemode_display: sample extraction failed")
                                    sample.append((si, "<extract-failed>", None))
                            logger.debug("_render_filemode_display: new_items sample=%r", sample)
                        except Exception as _e:
                            self.printException(_e, "_render_filemode_display: logging new_items failed")

                        # Try a robust matching strategy: match `_filename` first,
                        # then fall back to basename of `_raw_text` when available.
                        for i, n in enumerate(new_items):
                            try:
                                node_fname = getattr(n, "_filename", None)
                                node_raw = getattr(n, "_raw_text", None)
                                if node_fname == desired:
                                    desired_index = i
                                    logger.debug(
                                        "_render_filemode_display: matched by _filename idx=%d node_raw=%r", i, node_raw
                                    )
                                    break
                                try:
                                    if node_raw and os.path.basename(node_raw) == desired:
                                        desired_index = i
                                        logger.debug(
                                            "_render_filemode_display: matched by basename(_raw_text) idx=%d node_raw=%r",
                                            i,
                                            node_raw,
                                        )
                                        break
                                except Exception as _e:
                                    self.printException(_e, "_render_filemode_display: basename match failed")
                            except Exception as e:
                                self.printException(
                                    e, "_render_filemode_display: checking for preselection match failed"
                                )
                                continue

                        # Set the widget index to the selected index and ensure
                        # visibility of the selection.
                        try:
                            self.index = desired_index
                            try:
                                if hasattr(self, "_ensure_index_visible"):
                                    self._ensure_index_visible()
                            except Exception as _e:
                                self.printException(_e, "_render_filemode_display: _ensure_index_visible failed")
                        except Exception as _e:
                            self.printException(_e, "_render_filemode_display: setting index failed")
                    if desired_index is None:
                        # Use the index captured before we cleared the widget
                        # so the intended selection is preserved across the
                        # clear/append rebuild cycle.
                        cur_idx = pre_clear_index
                        if cur_idx is None:
                            cur_idx = getattr(self, "index", None)
                        if cur_idx is not None and 0 <= cur_idx < len(new_items):
                            desired_index = cur_idx
                        else:
                            desired_index = self._min_index or 0
                except Exception as _e:
                    self.printException(_e, "_render_filemode_display: computing desired index failed")

                # Clear the preselection marker so it does not affect later renders.
                self._preselected_filename = None

                # Now commit the prepared items to the widget, applying
                # inline highlight styles for the chosen index so the label
                # itself renders with the intended background.
                try:
                    logger.debug(
                        "_render_filemode_display: desired_index=%r header_count=%d min_index=%r total_items=%d",
                        desired_index,
                        header_count,
                        self._min_index,
                        len(new_items),
                    )

                    # Apply selection class to the chosen item before mounting
                    # so the batch mount can render with the correct CSS in one pass.
                    for i, it in enumerate(new_items):
                        try:
                            # When the index matches the desired selection,
                            # mark the ListItem with the 'active' class so the
                            # CSS-driven `ListItem.active` style is used for
                            # highlighting. Avoid inline label styles which
                            # produce inconsistent coloring across list types.
                            if i == desired_index:
                                try:
                                    it.set_class(True, "active")
                                except Exception as e:
                                    self.printException(e, "_render_filemode_display: setting active class failed")
                                    try:
                                        it.add_class("active")
                                    except Exception as e2:
                                        self.printException(e2, "_render_filemode_display: adding active class failed")
                            else:
                                try:
                                    it.set_class(False, "active")
                                except Exception as e:
                                    self.printException(e, "_render_filemode_display: clearing active class failed")
                        except Exception as _e:
                            self.printException(_e, "_render_filemode_display: preparing item classes failed")

                    # Commit prepared items synchronously so the active row
                    # class is present on first paint (startup should show
                    # row 0 selected without post-refresh correction).
                    try:
                        t0 = time.perf_counter()
                        try:
                            self.clear()
                        except Exception as e_clear:
                            self.printException(e_clear, "_render_filemode_display: clear() before append failed")
                        for it in new_items:
                            try:
                                self.append(it)
                            except Exception as e_add:
                                self.printException(e_add, "_render_filemode_display: append failed")
                        t1 = time.perf_counter()
                        logger.debug("_render_filemode_display: sync append completed in %.3fms", (t1 - t0) * 1000)
                    except Exception as e:
                        self.printException(e, "_render_filemode_display: batch add failed")
                except Exception as _e:
                    self.printException(_e, "_render_filemode_display: committing prepared items failed")

                # Set the widget index to the selected index and ensure
                # visibility of the selection. If the chosen index is the
                # first selectable row then the canonical header rows may
                # sit above it; explicitly scroll the header into view to
                # preserve the Key:/Directory legend when paging back up.
                self.index = desired_index

                try:
                    if hasattr(self, "_ensure_index_visible"):
                        try:
                            if desired_index == self._min_index and getattr(self, "children", None):
                                first = self.children[0]
                                try:
                                    self.call_after_refresh(lambda: self._safe_node_scroll_visible(first, True))
                                except Exception as e:
                                    self.printException(e, "_render_filemode_display: call_after_refresh failed")
                                    # best-effort fallback
                                    self._ensure_index_visible()
                            else:
                                self._ensure_index_visible()
                        except Exception as e:
                            self.printException(e, "_render_filemode_display: initial _ensure_index_visible failed")
                            self._ensure_index_visible()
                except Exception as _e:
                    self.printException(_e, "_render_filemode_display: _ensure_index_visible failed")
            except Exception as e:
                self.printException(e, "_render_filemode_display: finalizing population state failed")

        except Exception as e:
            self.printException(e, "_render_filemode_display failed")
        finally:
            try:
                self._render_filemode_in_progress = False
            except Exception as _e:
                self.printException(_e, "_render_filemode_display: resetting in-progress flag failed")

    def prepFileModeFileList(self, highlight: str | None = None) -> None:
        """
        Populate this widget with the file list for `path`.

        `highlight` if provided will be highlighted in the list; if
        `path` names a file the file's containing directory is listed and the
        filename is used as the highlight candidate.
        """
        try:
            # Data collection done by `_collect_filemode_nodes`; UI rendering
            # (including clearing the list and inserting the key header) is
            # handled by `_render_filemode_display`.

            try:
                # If the application started with a non-root `rel_dir`,
                # prepopulate the highlight stack as if we'd navigated
                # down into each component so upward navigation can
                # restore the previously-highlighted child entry.
                try:
                    # If caller provided a basename `highlight` and no file is
                    # currently selected, use it as the preselected filename
                    # which helps initialize the highlight history display.
                    try:
                        if highlight and (self.app.rel_file or "") == "":
                            # Only accept basenames here; defensive check
                            if os.path.basename(highlight) == highlight:
                                self._preselected_filename = highlight
                    except Exception as _e:
                        self.printException(_e, "prepFileModeFileList: applying highlight failed")

                    if self._highlight_history is not None:
                        if not self._highlight_history and self.app.rel_dir:
                            comps = [p for p in (self.app.rel_dir or "").split(os.sep) if p]
                            # Populate history left-to-right and set position to
                            # the current (deepest) directory so upward navigation
                            # can restore the most-recent child.
                            try:
                                for c in comps:
                                    self._highlight_history.append(c)
                                self._highlight_pos = (
                                    len(self._highlight_history) - 1 if self._highlight_history else -1
                                )
                            except Exception as _e:
                                self.printException(_e, "prepFileModeFileList: prepopulate _highlight_history failed")
                except Exception as _e:
                    self.printException(_e, "prepFileModeFileList: prepopulate _highlight_history failed")

                self._collect_filemode_nodes()
            except Exception as e:
                self.printException(e, "prepFileModeFileList: collecting file nodes failed")
                self._nodes_by_dir = {}

            # Delegate UI rendering to helper (renderer reads `self._nodes_by_dir`).
            self._render_filemode_display(self._nodes_by_dir, self.app.rel_dir, self.app.rel_file)

            # Log visible items after rendering so diagnostics capture the
            # freshly-populated list and the highlighted item.
            self._log_visible_items("prepFileModeFileList after rendering display")

        except Exception as e:
            self.printException(e, "prepFileModeFileList failed")

    def _activate_or_open(
        self,
        event: events.Key | None = None,
        enter_dir_test_fn=lambda name: True,
        allow_file_open: bool = True,
    ) -> None:
        """
        Activate the selected node or open its history if it's a file.

        - If the selected node is a directory and `enter_dir_test_fn(name)`
          returns True, navigate into it.
        - If the selected node is a file and `allow_file_open` is True,
          open the file's history via `prepFileModeHistoryList` unless the
          file is untracked.
        The `event` (if provided) will be stopped to prevent further handling.
        """
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "_activate_or_open: event.stop failed")

            idx = self.index or 0
            nodes = self.nodes()
            if not (0 <= idx < len(nodes)):
                return
            item = nodes[idx]
            is_dir = getattr(item, "_is_dir", False)
            name = getattr(item, "_filename", None)
            raw = getattr(item, "_raw_text", None)
            repo_status = getattr(item, "_repo_status", None)

            if is_dir:
                if enter_dir_test_fn(name):
                    # Maintain a highlight history so when we return to a
                    # parent directory we can re-highlight the child we
                    # just came from. For downward navigation append the
                    # dirname; for upward navigation (parent '..') move
                    # left in the history and restore the child as
                    # preselection.
                    try:
                        if name == "..":
                            # Moving up: restore the child at the current
                            # history position and move the position left.
                            if 0 <= self._highlight_pos < len(self._highlight_history):
                                child = self._highlight_history[self._highlight_pos]
                                self._preselected_filename = child
                                self._highlight_pos = max(self._highlight_pos - 1, -1)
                            elif self._highlight_history:
                                # Fallback: pop last entry
                                child = self._highlight_history.pop()
                                self._preselected_filename = child
                                self._highlight_pos = len(self._highlight_history) - 1
                        else:
                            # Moving down: if this name matches the next
                            # forward history entry, advance the position
                            # so right-navigation restores the previous
                            # child highlight. Otherwise truncate any
                            # forward history and append the new directory.
                            try:
                                next_pos = self._highlight_pos + 1
                                if (
                                    next_pos < len(self._highlight_history)
                                    and self._highlight_history[next_pos] == name
                                ):
                                    # Advance position along existing history
                                    self._highlight_pos = next_pos
                                    # Preselect the child (one step forward) if present
                                    child_pos = self._highlight_pos + 1
                                    if child_pos < len(self._highlight_history):
                                        self._preselected_filename = self._highlight_history[child_pos]
                                    else:
                                        self._preselected_filename = None
                                else:
                                    if self._highlight_pos < len(self._highlight_history) - 1:
                                        del self._highlight_history[self._highlight_pos + 1 :]
                                    self._highlight_history.append(name)
                                    self._highlight_pos = len(self._highlight_history) - 1
                                    self._preselected_filename = None
                            except Exception as _e:
                                self.printException(_e, "_activate_or_open: pushing to _highlight_history failed")
                    except Exception as _e:
                        self.printException(_e, "_activate_or_open: highlight history update failed")

                    # Compute and set new repository-relative directory
                    try:
                        cur_rel = self.app.rel_dir or ""
                        new_rel = self.app.gitRepo.reldir_plus_dirname_to_reldir(cur_rel, name)
                        self.app.rel_dir = new_rel
                        # Clear any selected file when entering a directory
                        self.app.rel_file = ""
                    except Exception as _e:
                        self.printException(_e, "_activate_or_open: computing new rel_dir failed")

                    # Prefer restoring a previously-recorded child for
                    # this directory if available. Keys in
                    # `_last_child_by_dir` are normalized repo-relative
                    # paths (see `key_left`), so normalize here too.
                    try:
                        try:
                            norm_new_rel = os.path.normpath(new_rel)
                        except Exception as e:
                            self.printException(e, "_activate_or_open: normalizing new_rel failed")
                            norm_new_rel = new_rel
                        last_child = None
                        if self._last_child_by_dir is not None:
                            last_child = self._last_child_by_dir.get(norm_new_rel)
                        logger.debug(
                            "_activate_or_open: new_rel=%r last_child=%r history=%r pos=%r",
                            norm_new_rel,
                            last_child,
                            self._highlight_history,
                            self._highlight_pos,
                        )
                        if last_child:
                            self._preselected_filename = last_child
                    except Exception as e:
                        self.printException(e, "_activate_or_open: restoring last child preselection failed")

                    self.prepFileModeFileList()
                    return

                else:
                    # Caller explicitly chose not to treat this directory
                    # selection as an enter action (for example, selecting
                    # the parent '..' when pressing Right). In that case
                    # we should not fall through to file-history handling
                    # (which would run `git log` on a directory path).
                    self.error_message(f"No history for directory: {name}")
                    return

            # not is_dir
            try:
                # Default behavior: prepare the right-hand file-history widget
                # (the app composes a FileModeHistoryList on the right) and
                # invoke its preparer so the UI shows the file's history.
                # Record this selection as the last child for the current dir
                try:
                    cur_rel = self.app.rel_dir or ""
                    sel_name = getattr(item, "_filename", None) or getattr(item, "_raw_text", None)
                    if sel_name:
                        self._last_child_by_dir[cur_rel] = sel_name

                    # If this is a file and it's ignored or untracked, do
                    # not attempt to prepare its history — treated as a no-op
                    # for Right key.
                    try:
                        status = repo_status
                        if status in ("I", "U", "ignored", "untracked"):
                            logger.debug(
                                "_activate_or_open: skipping history prep for ignored/untracked file status=%r",
                                status,
                            )
                            # Show a modal explaining why Right does nothing for
                            # ignored/untracked files so the user gets visible
                            # feedback instead of a silent no-op.
                            self.error_message(
                                f"No history for ignored file: {sel_name}"
                                if status in ("I", "ignored")
                                else f"No history for untracked file: {sel_name}"
                            )
                            return
                    except Exception as _e:
                        self.printException(_e, "_activate_or_open: checking repo_status failed")

                    # Ensure `app.rel_dir`/`app.rel_file` reflect the
                    # currently-selected file so history preparers can
                    # rely on a single source of truth.
                    try:
                        raw_rel = getattr(item, "_raw_text", None) or sel_name or ""
                        raw_rel = os.path.normpath(raw_rel)
                        rd, rf = os.path.split(raw_rel)
                        self.app.rel_dir = rd or ""
                        self.app.rel_file = rf or ""
                    except Exception as _e:
                        self.printException(_e, "_activate_or_open: setting app.rel_dir/rel_file failed")
                except Exception as _e:
                    self.printException(_e, "_activate_or_open: recording last child failed")

                # Invoke the file-history preparer using canonical app-level
                # `rel_dir`/`rel_file` so it can compute the hash list from
                # the correct file path.
                try:
                    file_path = os.path.join(self.app.rel_dir, self.app.rel_file)
                    file_path = os.path.normpath(file_path)
                except Exception as _e:
                    self.printException(_e, "_activate_or_open: normalizing file_path failed")
                    file_path = raw
                self.app.file_mode_history_list.prepFileModeHistoryList(file_path)
            except Exception as e:
                self.printException(e, "_activate_or_open: prepFileModeHistoryList failed")

            # Switch UI to file-history layout and focus
            self.app.change_state("file_history", f"#{RIGHT_HISTORY_LIST_ID}", RIGHT_HISTORY_FOOTER)
        except Exception as e:
            self.printException(e, "FileModeFileList._activate_or_open failed")

    def key_left(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """
        Handle Left key in a file list: enter parent directory when selected.

        This delegates to `_activate_or_open` and prevents opening files.
        """
        if not recursive:
            logger.debug("FileModeFileList.key_left called: key=%r index=%r", getattr(event, "key", None), self.index)
        # If we're at the repository root, Left should be a no-op.
        if self.app.rel_dir == "":
            logger.debug("FileModeFileList.key_left: at repo root, no-op")
            return

        # Capture the current index so we can imperatively apply highlighting
        # after the directory-enter flow completes. This makes the visual
        # highlight deterministic even if the watcher path races.
        old_idx = getattr(self, "index", None)
        before_state = (self.app.rel_dir, self.app.rel_file, self.app._current_layout)
        # If we're not currently positioned on the parent entry ('..'),
        # record the current child selection for this directory so we can
        # restore it when re-entering. Then move the selection to the
        # parent entry if present so `_activate_or_open` will navigate up.
        try:
            nodes = self.nodes()
            idx = getattr(self, "index", None)
            cur_name = None
            if idx is not None and 0 <= idx < len(nodes):
                cur_name = getattr(nodes[idx], "_filename", None) or getattr(nodes[idx], "_raw_text", None)
            if cur_name != "..":
                try:
                    # Record the currently-selected child for the current
                    # directory so re-entering that directory can restore
                    # the same child highlight.
                    cur_rel = self.app.rel_dir or ""
                    try:
                        cur_rel = os.path.normpath(cur_rel)
                    except Exception as e:
                        self.printException(e, "FileModeFileList.key_left: normalizing path failed")
                    if cur_name:
                        self._last_child_by_dir[cur_rel] = cur_name
                        logger.debug("FileModeFileList.key_left: recorded last_child_by_dir[%r]=%r", cur_rel, cur_name)
                except Exception as _e:
                    self.printException(_e, "FileModeFileList.key_left: recording last child failed")
                for i, n in enumerate(nodes):
                    try:
                        if getattr(n, "_filename", None) == "..":
                            logger.debug("FileModeFileList.key_left: pre-selecting '..' at index=%d", i)
                            try:
                                self.index = i
                            except Exception as _e:
                                self.printException(_e, "FileModeFileList.key_left: setting index to parent failed")
                            break
                    except Exception as e:
                        self.printException(e, "FileModeFileList.key_left: checking for parent entry failed")
                        continue
        except Exception as _e:
            self.printException(_e, "FileModeFileList.key_left preselect parent failed")

        self._activate_or_open(event, enter_dir_test_fn=lambda name: name == "..", allow_file_open=False)
        after_state = (self.app.rel_dir, self.app.rel_file, self.app._current_layout)
        if before_state == after_state:
            self.apply_index_change(old_idx, getattr(self, "index", None))

        try:
            nodes = self.nodes()
            idx = getattr(self, "index", None)
            fname = None
            if idx is not None and 0 <= idx < len(nodes):
                fname = getattr(nodes[idx], "_filename", None) or getattr(nodes[idx], "_raw_text", None)
            logger.debug("FileModeFileList.key_left: post-action index=%r filename=%r", idx, fname)
        except Exception as _e:
            self.printException(_e, "FileModeFileList.key_left post-action logging failed")
        self._log_visible_items("key_left after processing index change")

    def key_right(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Handle Right key in a file list: enter directories or open files."""
        if not recursive:
            logger.debug("FileModeFileList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index)
        before_state = (self.app.rel_dir, self.app.rel_file, self.app._current_layout)
        try:
            old_idx = getattr(self, "index", None)
            # If the current node is a directory, proactively suppress watch
            # and mark the intended preselection so the watcher cannot apply
            # a transient highlight before directory-enter flow runs.
            nodes = self.nodes()
            idx = getattr(self, "index", None)
            if idx is not None and 0 <= idx < len(nodes):
                it = nodes[idx]
                is_dir = getattr(it, "_is_dir", False)
                name = getattr(it, "_filename", None) or getattr(it, "_raw_text", None)
                if is_dir and name != "..":
                    logger.debug(
                        "FileModeFileList.key_right: pre-setting _suppress_watch True (idx=%r name=%r)", idx, name
                    )
                    self._suppress_watch = True
                    # Do not force preselection of the parent ('..') here;
                    # allow `_activate_or_open` to set `self._preselected_filename`
                    # based on the highlight history so the child highlight
                    # is preserved when navigating right.
                    self._preselected_filename = None
        except Exception as _e:
            self.printException(_e, "FileModeFileList.key_right: pre-suppress failed")

        self._activate_or_open(event, enter_dir_test_fn=lambda name: (name is not None) and name != "..")
        after_state = (self.app.rel_dir, self.app.rel_file, self.app._current_layout)
        try:
            if before_state == after_state:
                self.apply_index_change(old_idx, getattr(self, "index", None))
        except Exception as e:
            self.printException(e, "FileModeFileList.key_right: applying index change failed")
        self._log_visible_items("key_right after processing index change")

    def key_enter(self, event: events.Key | None = None) -> None:
        """Enter key: enter directories or open file history for tracked files."""
        logger.debug("FileModeFileList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        self._activate_or_open(event, enter_dir_test_fn=lambda name: True)
        self._log_visible_items("key_enter after processing index change")

    def key_i(self, event: events.Key | None = None) -> None:
        """Toggle ignored-file visibility and refresh the file-mode list."""
        return self.toggle_ignore(event)

    def key_u(self, event: events.Key | None = None) -> None:
        """Toggle untracked-file visibility and refresh the file-mode list."""
        return self.toggle_untracked(event)


class RepoModeFileList(FileListBase, RightSideBase):
    """
    Repo-mode file list: shows files changed between commits.

    Provides a `prepRepoModeFileList` stub and navigation handlers.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.highlight_bg_style = HIGHLIGHT_FILELIST_BG

    def prepRepoModeFileList(self, prev_hash: str | None, curr_hash: str | None) -> None:
        """
        Populate this widget with files changed between `prev_hash` and `curr_hash`.

        If either hash is a pseudo-name (e.g. 'MODS' or 'STAGED') the
        corresponding pseudo-entries are collected and rendered instead of
        delegating to `git diff`.
        """
        try:
            if prev_hash is None or curr_hash is None:
                raise ValueError("prepRepoModeFileList: prev_hash and curr_hash must not be None")

            logger.debug(
                "prepRepoModeFileList: prev_hash=%r curr_hash=%r",
                prev_hash,
                curr_hash,
            )
            # Defensive: clear any stray active classes left by virtualization
            self._clear_active_classes()

            try:
                self.clear()
            except Exception as e:
                self.printException(e, "prepFileModeHistoryList: clear failed")

            # Insert a hash header and the unselectable key legend header at the top
            try:
                self._render_hash_header(prev_hash, curr_hash)
                # Update external static key legend for the right file column
                key_lbl = self.app.query_one("#right-file-key", Label)
                key_lbl.update(Text(FILELIST_KEY_ROW_TEXT, style=STYLE_FILELIST_KEY))
            except Exception as e:
                self.printException(e, "prepRepoModeFileList header setup failed")

            # Use GitRepo as the single authority for file lists between
            # normalized tokens. `getFileListBetweenNormalizedHashes` will
            # handle pseudo-hashes like NEWREPO/STAGED/MODS and commit hashes.
            try:
                entries: list[tuple[str, str, str]] = []
                entries = self.app.gitRepo.getFileListBetweenNormalizedHashes(prev_hash, curr_hash) or []

                # Normalize entries and delegate row creation to shared helper
                try:
                    file_infos: list[dict] = []

                    for rel_path, iso_mtime, status in entries:
                        try:
                            # GitRepo contract: entries are (repo_relative_path, iso_mtime, status)
                            name = rel_path or ""
                            is_dir = False

                            file_infos.append(
                                {
                                    "name": name,
                                    "is_dir": is_dir,
                                    "raw": name,
                                    "repo_status": status,
                                }
                            )
                        except Exception as _ex:
                            self.printException(_ex, "prepRepoModeFileList: normalizing entry failed")
                            continue

                    # Determine active target from the current app selection
                    # (rel_dir/rel_file) and otherwise default to top row.
                    active_raw = None
                    active_idx = 0
                    selected_rel = (
                        os.path.join(self.app.rel_dir or "", self.app.rel_file) if self.app.rel_file else None
                    )
                    if selected_rel:
                        active_raw = os.path.normpath(selected_rel)
                    self._populate_from_file_infos(
                        file_infos, active_raw=active_raw, active_index=(None if active_raw else active_idx)
                    )
                except Exception as e:
                    self.printException(e, "prepRepoModeFileList processing entries failed")
            except Exception as e:
                self.printException(e, "prepRepoModeFileList failed while querying GitRepo")

            self._populated = True
            # Highlight from current app rel_dir/rel_file selection.
            try:
                nodes = self.nodes()
                # The hash header is external so there is no in-list header
                # to skip. Set `_min_index` to 0 so navigation includes the
                # top-most data row.
                self._min_index = 0
            except Exception as e:
                self.printException(e, "prepRepoModeFileList: setting _min_index failed")
            # Immediately record the repo-level commit pair so other
            # components can access the selected refs.
            try:
                self.app.previous_hash = prev_hash
                self.app.current_hash = curr_hash
            except Exception as e:
                self.printException(e, "prepRepoModeFileList: recording app-level state failed")
            try:
                selected_rel = os.path.join(self.app.rel_dir or "", self.app.rel_file) if self.app.rel_file else None
                if selected_rel:
                    self._highlight_filename(selected_rel)
                else:
                    self._highlight_top()
            except Exception as e:
                self.printException(e, "prepRepoModeFileList: highlight failed")

            # Run centralized finalization so UI/app state is kept consistent
            try:
                selected_rel = os.path.join(self.app.rel_dir or "", self.app.rel_file) if self.app.rel_file else None
                self._finalize_filelist_prep(curr_hash=curr_hash, prev_hash=prev_hash, path=selected_rel)
            except Exception as e:
                self.printException(e, "prepRepoModeFileList: finalize failed")
        except Exception as e:
            self.printException(e, "prepRepoModeFileList failed")

    def key_left(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """
        Handle Left key in repo-mode file list: switch to history fullscreen.

        Typically moves focus back to the left history column or toggles
        the paired layout; defensive with event.stop() handling.
        """
        if not recursive:
            logger.debug("RepoModeFileList.key_left called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "RepoModeFileList.key_left: event.stop failed")
        # Switch layout back to left-side history fullscreen
        self.app.change_state("history_fullscreen", f"#{LEFT_HISTORY_LIST_ID}", LEFT_HISTORY_FOOTER)
        self._log_visible_items("key_left after processing index change")

    def key_right(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """
        Open diff view for the selected file and switch to the file view.

        Delegates to `DiffList` to prepare the diff and records the
        app-level `path` for downstream helpers. Honors `recursive` when
        invoked as an alias.
        """
        if not recursive:
            logger.debug("RepoModeFileList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "RepoModeFileList.key_right: event.stop failed")
        try:
            idx = self.index or 0
            nodes = self.nodes()
            if not (0 <= idx < len(nodes)):
                return
            node = nodes[idx]
            filename = getattr(node, "_raw_text", None) or self._child_filename(node)

            # Use the currently-selected diff variant so startup --diff and
            # runtime d/D selection are honored when opening a diff.
            variant_index = int(getattr(self.app.diff_list, "variant", 0) or 0)
            try:
                rel = os.path.normpath(filename)
                rd, rf = os.path.split(rel)
                self.app.rel_dir = rd or ""
                self.app.rel_file = rf or ""
            except Exception as _ex:
                self.printException(_ex, "RepoModeFileList.key_right: setting app.rel_dir/rel_file failed")

            try:
                # When opening from the repo-file list, we want DiffList.key_left
                # to return to the repo file list view. If there is no previous
                # hash (e.g. selecting the bottom-most/top-most row) use the
                # canonical NEWREPO sentinel from the app's gitRepo.
                if self.app.previous_hash is not None:
                    prev = self.app.previous_hash
                else:
                    prev = GitRepo.NEWREPO

                diff_list = self.app.diff_list
                curr = self.app.current_hash

                if diff_list is not None:
                    diff_list.prepDiffList(
                        filename,
                        prev,
                        curr,
                        variant_index,
                        ("history_file", RIGHT_FILE_LIST_ID, RIGHT_FILE_FOOTER),
                    )
            except Exception as e:
                self.printException(e, "RepoModeFileList.key_right: prepDiffList failed")

            self.app.change_state("history_file_diff", f"#{DIFF_LIST_ID}", DIFF_FOOTER_1)
        except Exception as e:
            self.printException(e, "RepoModeFileList.key_right failed")
        self._log_visible_items("key_right after processing index change")

    def key_enter(self, event: events.Key | None = None) -> None:
        """Same behavior as Right: open the diff for the selected file."""
        logger.debug("RepoModeFileList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_right(event, recursive=True)

    def key_w(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Prompt to save snapshot files for the selected file (older/newer/both)."""
        if not recursive:
            logger.debug("RepoModeFileList.key_w called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "RepoModeFileList.key_w: event.stop failed")
        self.key_w_helper(event)
        self._log_visible_items("key_w after processing index change")

    def key_W(self, event: events.Key | None = None) -> None:
        """Alias for key_w to support uppercase 'W' as well."""
        logger.debug("RepoModeFileList.key_W called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_w(event, recursive=True)

    def key_o(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Open the file content for the selected commit (key 'o')."""
        if not recursive:
            logger.debug("RepoModeFileList.key_o called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "RepoModeFileList.key_o: event.stop failed")
        try:
            # Get selected file path and current commit hash
            idx = self.index
            if idx < 0 or idx >= len(self.children):
                return

            selected_item = self.children[idx]
            filepath = self.text_of(selected_item)

            if not filepath:
                return

            commit_hash = self.app.current_hash
            if commit_hash is None:
                commit_hash = GitRepo.NEWREPO

            # Switch to history_file_open layout immediately (don't wait for file load)
            # Save the layout so OpenFileList knows where we came from
            self.app.openfile_list._saved_layout = "history_file_open"
            self.app.openfile_list._go_back = ("history_file", RIGHT_FILE_LIST_ID, RIGHT_FILE_FOOTER)
            self.app.change_state("history_file_open", f"#{OPEN_FILE_LIST_ID}", OPEN_FILE_FOOTER_1)

            # Load file content asynchronously in the background
            self.app.call_later(lambda: self._load_open_file(filepath, commit_hash))
        except Exception as e:
            self.printException(e, "RepoModeFileList.key_o failed")
        self._log_visible_items("key_o after processing")

    def key_O(self, event: events.Key | None = None) -> None:
        """Alias for key_o (Shift-O)."""
        logger.debug("RepoModeFileList.key_O called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_o(event, recursive=True)


class HistoryListBase(AppBase):
    """
    Base for history (commit) lists.

    Provides helpers to attach metadata to rows and compute commit-pair hashes.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # History lists should use repository highlight backgrounds
        self.highlight_bg_style = HIGHLIGHT_REPOLIST_BG
        # Mark as history list for flag-based checks in AppBase.watch_index
        self.is_history_list = 1
        # History lists are selectable from the first data row (index 0).
        self._min_index = 0
        # Last search string for repeated forward/backward searches
        self._last_search = None

    def _add_row(self, text: str, commit_hash: str | None, mark_active: bool = False) -> None:
        """
        Append a commit-row with `text` and attach `commit_hash` metadata.

        If `mark_active` is True the newly-appended row is immediately
        marked with the `active` class and the widget `index` updated so
        it appears highlighted without waiting for post-refresh scheduling.
        """
        try:
            # Visible rows are prefixed with two spaces for alignment; keep
            # `_raw_text` as the original value for metadata and matching.
            display_text = f"  {text}"
            item = ListItem(Label(Text(display_text)))
            # Attach helpful metadata for later lookup
            setattr(item, "_hash", commit_hash)
            setattr(item, "_raw_text", text)
            try:
                self.append(item)
                # If requested, mark this newly-appended item active so the
                # highlight is visible immediately (avoids accent fallback).
                if mark_active:
                    try:
                        item.set_class(True, "active")
                    except Exception as e:
                        self.printException(e, "HistoryListBase._add_row setting active class failed")
                        try:
                            item.add_class("active")
                        except Exception as e:
                            self.printException(e, "HistoryListBase._add_row adding active class failed")
                    try:
                        # Update the widget index to point at the newly
                        # appended row so other logic observes the selection.
                        nodes = self.nodes()
                        new_idx = len(nodes) - 1 if nodes else None
                        if new_idx is not None:
                            try:
                                self.index = new_idx
                            except Exception as e:
                                self.printException(e, "HistoryListBase._add_row setting index failed")
                                try:
                                    setattr(self, "index", new_idx)
                                except Exception as e:
                                    self.printException(e, "HistoryListBase._add_row setattr index failed")
                    except Exception as e:
                        self.printException(e, "HistoryListBase._add_row updating index failed")
            except Exception as e:
                self.printException(e, "HistoryListBase._add_row append failed")
        except Exception as e:
            self.printException(e, "HistoryListBase._add_row failed")

    def _format_commit_row(
        self, ts, h: str | None, msg: str, status: str | None, author_name: str, author_email: str
    ) -> str:
        """
        Return a formatted commit row string for display.

        Centralized so formatting is consistent across preparers.
        When status is 'unpushed', prepends an up arrow (↑) to indicate unpushed commits.
        Display timestamp with space instead of T separator ("2024-03-03 14:30:00").
        When add_authors is True, includes author name and email in pipe-delimited format.
        """
        try:
            date_stamp = str(ts).replace("T", " ")
            display_hash_length = self.app.hash_length
            short_hash = (h or "")[:display_hash_length]
            push_marker = "↑ " if status == "unpushed" else ""

            # Include author info if enabled and available
            author_part = ""
            if self.app.add_authors and (author_name or author_email):
                author_part = f" |{author_name} {author_email}|"

            return f"{date_stamp} {push_marker}{short_hash}{author_part} {msg}".strip()
        except Exception as e:
            self.printException(e, "_format_commit_row failed")
            return f"{h or ''} {msg}".strip()

    def toggle_check_current(self, idx: int | None = None) -> None:
        """
        Toggle a single-mark (checked) state on the selected history row.

        Enforces single-mark semantics: marking one row unmarks others.
        """
        try:
            if idx is None:
                idx = self.index or 0
            nodes = self.nodes()
            logger.debug(
                "toggle_check_current: called idx=%r node_count=%d", idx, len(nodes) if nodes is not None else 0
            )
            if not (0 <= idx < len(nodes)):
                return
            # Enforce single-mark semantics: mark the selected item (M ) and
            # unmark all others. If the selected item was already marked, clear it.
            try:
                selected_node = nodes[idx]
                was_marked = getattr(selected_node, "_checked", False)
                logger.debug(
                    "toggle_check_current: selected_idx=%d selected_hash=%r was_marked=%r",
                    idx,
                    getattr(selected_node, "_hash", None),
                    was_marked,
                )
                # If it was marked, unmark everything; otherwise mark selected and unmark others
                for i, node in enumerate(nodes):
                    try:
                        is_selected = i == idx
                        if was_marked:
                            setattr(node, "_checked", False)
                        else:
                            setattr(node, "_checked", is_selected)

                        # Update label renderable
                        try:
                            lbl = node.query_one(Label)
                            raw = getattr(node, "_raw_text", "")
                            if getattr(node, "_checked", False):
                                # Marked: prefix with 'M ' and apply contrasting
                                # style on the label renderable. Avoid mutating
                                # container/node inline styles here; the label's
                                # renderable already carries the visible
                                # highlighting.
                                marked_txt = Text(f"M {raw}", style="bold white on red")
                                lbl.update(marked_txt)
                                logger.debug(
                                    "toggle_check_current: marked idx=%d hash=%r",
                                    i,
                                    getattr(node, "_hash", None),
                                )
                            else:
                                # Unmarked: two-space prefix, plain style
                                lbl.update(Text(f"  {raw}"))
                                logger.debug(
                                    "toggle_check_current: unmarked idx=%d hash=%r",
                                    i,
                                    getattr(node, "_hash", None),
                                )
                        except Exception as e:
                            self.printException(e, "updating label renderable failed")
                    except Exception as e:
                        self.printException(e, "updating _checked attribute failed")
            except Exception as e:
                self.printException(e, "HistoryListBase.toggle_check_current update failed")
        except Exception as e:
            self.printException(e, "toggle_check_current failed")

    def key_m(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Toggle the 'marked' state for the currently-selected history row."""
        if not recursive:
            logger.debug("HistoryListBase.key_m called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "HistoryListBase.key_m: event.stop failed")
                self.toggle_check_current()
        except Exception as e:
            self.printException(e, "HistoryListBase.key_m failed")

    def key_M(self, event: events.Key | None = None) -> None:
        """Alias for `key_m` used to support Shift-M bindings."""
        logger.debug("HistoryListBase.key_M called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_m(event, recursive=True)

    def compute_commit_pair_hashes(self, idx: int | None = None) -> tuple[str | None, str | None]:
        """
        Compute (prev_hash, curr_hash) pair from the history list selection.

        Returns (prev, curr) where `prev` is the older commit and `curr` is the
        currently-selected commit when available.
        """
        try:
            if idx is None:
                idx = self.index or 0
            nodes = self._nodes or []
            if not nodes:
                return (None, None)
            # Current commit is at idx; previous is idx+1 (newer->older ordering varies)
            curr = getattr(nodes[idx], "_hash", None) if 0 <= idx < len(nodes) else None
            prev = getattr(nodes[idx + 1], "_hash", None) if 0 <= (idx + 1) < len(nodes) else None
            return (prev, curr)
        except Exception as e:
            self.printException(e, "compute_commit_pair_hashes failed")
            return (None, None)

    def on_focus(self) -> None:
        """Ensure the history widget has a valid `index` when focused."""
        try:
            if self.index is None:
                # Respect widget-specific minimum index when focusing
                self.index = self._min_index or 0
        except Exception as e:
            self.printException(e, "HistoryListBase.on_focus")

    def _compute_selected_pair(self) -> tuple[str | None, str | None]:
        """
        Return (prev_hash, curr_hash) where prev is older and curr is newer.

        If a row is marked (single-mark semantics) use the marked row and the
        currently-selected row as the pair. Otherwise compute the pair as the
        currently-selected row and the following row.
        """
        try:
            idx = self.index or 0
            nodes = self.nodes()
            if not nodes:
                return (None, None)
            selected_hash = getattr(nodes[idx], "_hash", None)
            marked_idx = None
            for i, node in enumerate(nodes):
                if getattr(node, "_checked", False):
                    marked_idx = i
                    break
            if marked_idx is not None:
                marked_hash = getattr(nodes[marked_idx], "_hash", None)
                # History ordering: lower index == newer, higher index == older
                if marked_idx > idx:
                    # marked is older
                    # update app-level hashes for other components
                    try:
                        self.app.current_hash = selected_hash
                        self.app.previous_hash = marked_hash
                    except Exception as e:
                        self.printException(e, "updating app-level hashes failed")
                    return (marked_hash, selected_hash)
                else:
                    try:
                        self.app.current_hash = marked_hash
                        self.app.previous_hash = selected_hash
                    except Exception as e:
                        self.printException(e, "updating app-level hashes failed")
                    return (selected_hash, marked_hash)

            # No marked row — fall back to adjacent pair computation
            prev, curr = self.compute_commit_pair_hashes(idx)
            try:
                self.app.current_hash = curr
                self.app.previous_hash = prev
            except Exception as e:
                self.printException(e, "updating app-level hashes failed")
            return (prev, curr)
        except Exception as e:
            self.printException(e, "_compute_selected_pair failed")
            return (None, None)

    def _finalize_historylist_prep(
        self, curr_hash: str | None = None, prev_hash: str | None = None, path: str | None = None
    ) -> None:
        """
        History-specific finalization then call shared common sync.

        This implements history-only behavior (e.g. marking a previously
        checked commit via `toggle_check_current`) and defers app-level
        state synchronization to `_finalize_prep_common`.
        """
        try:
            try:
                marked_applied = False
                if curr_hash:
                    self._highlight_match(curr_hash)
                elif not prev_hash:
                    self._highlight_top()

                if prev_hash:
                    try:
                        if hasattr(self, "toggle_check_current"):
                            for i, node in enumerate(self.nodes()):
                                try:
                                    node_hash = getattr(node, "_hash", None)
                                    if node_hash and (
                                        node_hash == prev_hash
                                        or node_hash.startswith(prev_hash)
                                        or prev_hash.startswith(node_hash)
                                    ):
                                        try:
                                            logger.debug(
                                                "prepRepoModeHistoryList: invoking toggle_check_current at index=%d for prev_hash=%r node_hash=%r",
                                                i,
                                                prev_hash,
                                                node_hash,
                                            )
                                            self.toggle_check_current(i)
                                        except Exception as e:
                                            self.printException(
                                                e,
                                                "HistoryListBase._finalize_historylist_prep: toggle_check_current failed",
                                            )
                                        marked_applied = True
                                        break
                                except Exception as e:
                                    self.printException(
                                        e, "HistoryListBase._finalize_historylist_prep: checking node failed"
                                    )
                            logger.debug(
                                "HistoryListBase._finalize_historylist_prep: prev_hash=%r mark_applied=%r",
                                prev_hash,
                                marked_applied,
                            )
                        else:
                            self._highlight_top()
                        if not marked_applied and not curr_hash:
                            self._highlight_top()
                    except Exception as e:
                        self.printException(e, "HistoryListBase._finalize_historylist_prep: locating prev_hash failed")
            except Exception as e:
                self.printException(e, "HistoryListBase._finalize_historylist_prep: highlight step failed")

            self._finalize_prep_common(curr_hash=curr_hash, prev_hash=prev_hash, path=path)
        except Exception as e:
            self.printException(e, "HistoryListBase._finalize_historylist_prep failed")

    def key_e(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """
        Edit the commit message for the currently-selected unpushed commit.

        Retrieves the complete commit message, opens EditMessageModal for
        editing, and if modified, amends the commit message in the repository.
        Does nothing for pushed commits.
        """
        if not recursive:
            logger.debug("HistoryListBase.key_e called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "HistoryListBase.key_e: event.stop failed")

            # Get the current hash from the selected row
            idx = self.index or 0
            nodes = self.nodes()
            if not nodes or not (0 <= idx < len(nodes)):
                return

            hash_val = getattr(nodes[idx], "_hash", None)
            if not hash_val:
                return

            # Check if the hash is pushed
            try:
                pushed_hashes = self.app.gitRepo.getPushedHashes()
                if hash_val in pushed_hashes:
                    self.error_message(f"Cannot edit pushed commit {hash_val[:self.app.hash_length]}")
                    return
            except Exception as e:
                self.printException(e, "HistoryListBase.key_e: checking pushed status failed")
                return

            # Get the complete commit message
            try:
                filepath = self.app.rel_file or ""
                complete_msg = self.app.gitRepo.getCompleteCommitMessage(filepath, hash_val)
                if complete_msg is None:
                    self.error_message(f"Failed to retrieve commit message for {hash_val[:self.app.hash_length]}")
                    return
            except Exception as e:
                self.printException(e, "HistoryListBase.key_e: retrieving commit message failed")
                self.error_message(f"Error retrieving commit message: {str(e)}")
                return

            # Open the edit modal
            try:
                short_hash = hash_val[: self.app.hash_length]
                title = f"Edit commit message for {short_hash}"
                modal: EditMessageModal | None = None

                def check_edit_result() -> None:
                    try:
                        edited = modal.edited_text if modal is not None else None
                        logger.debug(
                            "key_e: check_edit_result callback executing, edited_text len=%s, original len=%s",
                            len(edited) if edited else 0,
                            len(complete_msg),
                        )
                        logger.debug("key_e: edited_text=%r", edited)
                        logger.debug("key_e: complete_msg=%r", complete_msg)
                        logger.debug("key_e: texts equal? %s", edited == complete_msg)

                        if edited and edited != complete_msg:
                            logger.debug("key_e: text was modified, calling amendCommitMessage for hash=%s", short_hash)
                            try:
                                new_hash = self.app.gitRepo.amendCommitMessage(hash_val, edited)
                                logger.debug("key_e: amendCommitMessage succeeded, new_hash=%s", new_hash)
                                self.error_message(f"Commit {short_hash} amended successfully")
                                # Refresh the history list to show the updated commit message
                                try:
                                    logger.debug(
                                        "key_e: refreshing history list with ignorecache=True, preserving hash=%s",
                                        new_hash,
                                    )
                                    # Refresh with new_hash to preserve selection (hash changes after amend)
                                    self.prepRepoModeHistoryList(curr_hash=new_hash, ignorecache=True)
                                except Exception as refresh_err:
                                    self.printException(
                                        refresh_err, "HistoryListBase.key_e: refreshing history list failed"
                                    )
                            except ValueError as ve:
                                self.printException(ve, "HistoryListBase.key_e: ValueError during amend")
                                self.error_message(f"Cannot amend: {str(ve)}")
                            except Exception as e:
                                self.printException(e, "HistoryListBase.key_e: amending commit failed")
                                self.error_message(f"Error amending commit: {str(e)}")
                        else:
                            logger.debug("key_e: no changes detected or text is empty")
                    except Exception as e:
                        self.printException(e, "HistoryListBase.key_e: check_edit_result failed")

                modal = EditMessageModal(initial_text=complete_msg, title=title, on_save=check_edit_result)
                logger.debug(f"key_e: pushing EditMessageModal for hash={short_hash}")
                self.app.push_screen(modal)

            except Exception as e:
                self.printException(e, "HistoryListBase.key_e: pushing edit modal failed")
                self.error_message(f"Error opening edit dialog: {str(e)}")

        except Exception as e:
            self.printException(e, "HistoryListBase.key_e failed")

    def key_E(self, event: events.Key | None = None) -> None:
        """Alias for `key_e` used to support Shift-E bindings."""
        logger.debug("HistoryListBase.key_E called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_e(event, recursive=True)


class FileModeHistoryList(HistoryListBase, RightSideBase):
    """History list for a single file's history. Stubbed prep method."""

    def prepFileModeHistoryList(self, path: str, prev_hash: str | None = None, curr_hash: str | None = None) -> None:
        """
        Prepare the commit history listing for a single file at `path`.

        `prev_hash` and `curr_hash` may be provided to restrict the commit
        range; when omitted the full history is used.
        """
        try:
            logger.debug("prepFileModeHistoryList: path=%r prev_hash=%r curr_hash=%r", path, prev_hash, curr_hash)
            self.clear()

            # Determine the repo-relative path to the file from the app-level
            # rel_dir/rel_file pair. This is the canonical input to the
            # GitRepo helper used to obtain a normalized list of hashes.
            try:
                rel_dir = self.app.rel_dir or ""
                rel_file = self.app.rel_file or ""
                rel_path = os.path.normpath(os.path.join(rel_dir, rel_file))
            except Exception as _e:
                self.printException(_e, "prepFileModeHistoryList: computing rel_path failed")
                rel_path = path or ""

            # Ask GitRepo for the normalized list of hashes touching this file.
            try:
                entries = (
                    self.app.gitRepo.getNormalizedHashListFromFileName(rel_path, limit=self.app.history_limit) or []
                )
            except Exception as _e:
                self.printException(_e, "prepFileModeHistoryList: gitRepo.getNormalizedHashListFromFileName failed")
                entries = []

            # Render returned entries. Expect tuples like (iso, hash, subject, status, author_name, author_email).
            try:
                first = True
                for ts_iso, h, subject, status, author_name, author_email in entries:
                    try:
                        text = self._format_commit_row(ts_iso, h, subject, status, author_name, author_email)
                        # Mark the first appended history row active immediately
                        # so the highlight is present without waiting for refresh.
                        self._add_row(text, h, mark_active=first)
                        first = False
                    except Exception as _e:
                        self.printException(_e, "prepFileModeHistoryList: rendering entry failed")
            except Exception as _e:
                self.printException(_e, "prepFileModeHistoryList: iterating entries failed")

            self._populated = True
            self._finalize_historylist_prep(curr_hash=curr_hash, prev_hash=prev_hash, path=rel_path)
        except Exception as e:
            self.printException(e, "prepFileModeHistoryList failed")

    def key_w(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Prompt to save snapshot files for the current file history selection."""
        if not recursive:
            logger.debug("FileModeHistoryList.key_w called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "FileModeHistoryList.key_w: event.stop failed")
        self.key_w_helper(event)

    def key_W(self, event: events.Key | None = None) -> None:
        """Alias for key_w to support uppercase 'W' as well."""
        logger.debug("FileModeHistoryList.key_W called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_w(event, recursive=True)

    def key_i(self, event: events.Key | None = None) -> None:
        """Toggle ignored-file visibility and refresh file-mode list."""
        return self.toggle_ignore(event)

    def key_u(self, event: events.Key | None = None) -> None:
        """Toggle untracked-file visibility and refresh file-mode list."""
        return self.toggle_untracked(event)

    def key_right(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """
        Open the diff for the selected file commit-pair.

        Compute the current and previous hashes (using marked rows if present),
        determine the filename from the app-level `path`, call
        `self.app.diff_list.prepDiffList(filename, prev, curr)` and switch the
        UI to the file-history-diff layout.
        """
        if not recursive:
            logger.debug(
                "FileModeHistoryList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index
            )
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "FileModeHistoryList.key_right: event.stop failed")

        prev_hash, curr_hash = self._compute_selected_pair()
        try:
            # Build a repository-relative filename from rel_dir/rel_file
            try:
                rd = self.app.rel_dir or ""
                rf = self.app.rel_file or ""
                filename = os.path.join(rd, rf) if rf else (rd or "")
            except Exception as _e:
                self.printException(_e, "FileModeHistoryList.key_right: computing filename failed")
                filename = self.app.rel_file or ""
            # Ask the diff list to prepare the diff for this file and pair
            try:
                # When opening from a file's history, ensure left returns to
                # the file-history view on the right history column. Use the
                # repository's canonical NEWREPO sentinel when available.
                p = prev_hash if prev_hash is not None else GitRepo.NEWREPO
                variant_index = int(getattr(self.app.diff_list, "variant", 0) or 0)
                self.app.diff_list.prepDiffList(
                    filename,
                    p,
                    curr_hash,
                    variant_index,
                    ("file_history", RIGHT_HISTORY_LIST_ID, RIGHT_HISTORY_FOOTER),
                )
            except Exception as e:
                self.printException(e, "FileModeHistoryList.key_right: prepDiffList failed")

            # Switch to the file-history-diff layout and focus diff list
            self.app.change_state("file_history_diff", f"#{DIFF_LIST_ID}", DIFF_FOOTER_1)
        except Exception as e:
            self.printException(e, "FileModeHistoryList.key_right prep failed")
        self._log_visible_items("key_right after processing index change")

    def key_enter(self, event: events.Key | None = None) -> None:
        """Enter-key handler — same behavior as Right: open the file commit-pair diff."""
        logger.debug("FileModeHistoryList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_right(event, recursive=True)

    def key_left(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Return to file fullscreen and focus the left file list."""
        if not recursive:
            logger.debug(
                "FileModeHistoryList.key_left called: key=%r index=%r", getattr(event, "key", None), self.index
            )
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "FileModeHistoryList.key_left: event.stop failed")
        try:
            app = self.app
            app.change_state("file_fullscreen", f"#{LEFT_FILE_LIST_ID}", LEFT_FILE_FOOTER)
        except Exception as e:
            self.printException(e, "FileModeHistoryList.key_left change_state failed")
        self._log_visible_items("key_left after processing index change")

    def key_o(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Open the file content for the selected commit (key 'o')."""
        if not recursive:
            logger.debug("FileModeHistoryList.key_o called: key=%r index=%r", getattr(event, "key", None), self.index)
        if event is not None:
            try:
                event.stop()
            except Exception as e:
                self.printException(e, "FileModeHistoryList.key_o: event.stop failed")
        try:
            # Get selected commit hash from the current node
            nodes = self._nodes or []
            idx = self.index or 0
            commit_hash = getattr(nodes[idx], "_hash", None) if 0 <= idx < len(nodes) else None
            if commit_hash is None:
                return

            # Get the file path being displayed
            filepath = os.path.join(self.app.rel_dir, self.app.rel_file) if self.app.rel_file else self.app.rel_dir

            logger.debug("FileModeHistoryList.key_o about to change_state to file_history_open")
            # Switch to file_history_open layout immediately (don't wait for file load)
            # Save the layout so OpenFileList knows where we came from
            self.app.openfile_list._saved_layout = "file_history_open"
            self.app.openfile_list._go_back = ("file_history", RIGHT_HISTORY_LIST_ID, RIGHT_HISTORY_FOOTER)
            self.app.change_state("file_history_open", f"#{OPEN_FILE_LIST_ID}", OPEN_FILE_FOOTER_1)
            logger.debug("FileModeHistoryList.key_o change_state completed, now scheduling _load_open_file")

            # Load file content asynchronously in the background
            self.app.call_later(lambda: self._load_open_file(filepath, commit_hash))
            logger.debug("FileModeHistoryList.key_o call_later scheduled, returning")
        except Exception as e:
            self.printException(e, "FileModeHistoryList.key_o failed")
        self._log_visible_items("key_o after processing")

    def key_O(self, event: events.Key | None = None) -> None:
        """Alias for key_o (Shift-O)."""
        logger.debug("FileModeHistoryList.key_O called: key=%r", getattr(event, "key", None))
        return self.key_o(event, recursive=True)


class RepoModeHistoryList(HistoryListBase):
    """History list for repository-wide commits. Stubbed prep method."""

    def prepRepoModeHistoryList(
        self,
        prev_hash: str | None = None,
        curr_hash: str | None = None,
        ignorecache: bool = False,
    ) -> None:
        """
        Prepare the repository-wide commit history view.

        `prev_hash` and `curr_hash` may be used to constrain the commit range.
        `ignorecache` when True forces re-fetching of git data.
        """
        try:
            logger.debug(
                "prepRepoModeHistoryList: prev_hash=%r curr_hash=%r ignorecache=%r",
                prev_hash,
                curr_hash,
                ignorecache,
            )

            # Clear any stray active classes (defensive) and existing rows
            self._clear_active_classes()

            try:
                self.clear()
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: clear failed")

            # Use GitRepo to obtain a normalized list of hashes (including
            # pseudo-entries like MODS/STAGED). GitRepo centralizes git CLI
            # invocation and caching so prefer its helpers.
            try:
                entries = (
                    self.app.gitRepo.getNormalizedHashListComplete(
                        ignorecache=ignorecache, limit=self.app.history_limit
                    )
                    or []
                )
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: gitRepo.getNormalizedHashListComplete failed")
                entries = []

            try:
                # Entries are tuples (iso, hash, subject, status, author_name, author_email). Render each as a row
                # and mark the first row active for immediate focus.
                first = True
                for ts_iso, h, subject, status, author_name, author_email in entries:
                    try:
                        text = self._format_commit_row(ts_iso, h, subject, status, author_name, author_email)
                        is_active = first

                        self._add_row(text, h, mark_active=is_active)
                        first = False
                    except Exception as e:
                        self.printException(e, "prepRepoModeHistoryList: adding commit row failed")
                        continue
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: rendering entries failed")

            # Mark populated and delegate final highlighting/selection logic
            # to the centralized finalizer which will honor provided hashes.
            try:
                self._populated = True
                self._finalize_historylist_prep(curr_hash=curr_hash, prev_hash=prev_hash)
            except Exception as e:
                self.printException(e, "prepRepoModeHistoryList: finalize or setting populated failed")
        except Exception as e:
            self.printException(e, "prepRepoModeHistoryList failed")

    def key_right(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """
        Open the selected/marked commit-pair in the repo file list preparer.

        This method lives on the repo-mode history widget because the action
        it performs (populate the repo file list and switch to the files
        column) is meaningful only for repository-wide history views.
        """
        if not recursive:
            logger.debug(
                "RepoModeHistoryList.key_right called: key=%r index=%r", getattr(event, "key", None), self.index
            )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "RepoModeHistoryList.key_right: event.stop failed")
            prev_hash, curr_hash = self._compute_selected_pair()
            try:
                # Delegate to the repo-mode file list preparer. The preparer
                # understands pseudo-hashes like MODS/STAGED and uses the app's
                # current rel_dir/rel_file selection for file highlighting.
                logger.debug(
                    "RepoModeHistoryList.key_right: prev=%r curr=%r",
                    prev_hash,
                    curr_hash,
                )
                self.app.repo_mode_file_list.prepRepoModeFileList(prev_hash, curr_hash)
                # Switch to the right-file list view and update footer
                self.app.change_state("history_file", f"#{RIGHT_FILE_LIST_ID}", RIGHT_FILE_FOOTER)
            except Exception as e:
                self.printException(e, "RepoModeHistoryList.key_right prep failed")
        except Exception as e:
            self.printException(e, "RepoModeHistoryList.key_right failed")

        self._log_visible_items("key_right after processing index change")

    def key_enter(self, event: events.Key | None = None) -> None:
        """Enter-key handler — same behavior as Right: open the commit-pair file list."""
        logger.debug("RepoModeHistoryList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_right(event, recursive=True)


class DiffList(FullScreenBase):
    """
    List view for showing diffs.

    `prepDiffList` is a stub here; later steps will call `git diff` and
    colorize output. Key handlers toggle colorization and expose actions.
    """

    BINDINGS = [
        ("bracket_left", "left_bracket", "Decrease left pane width (side-by-side)"),
        ("bracket_right", "right_bracket", "Increase left pane width (side-by-side)"),
        ("equal", "equal", "Reset to 50/50 split (side-by-side)"),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._colorized = True
        self.highlight_bg_style = HIGHLIGHT_DIFF_BG
        # Stored output lines from the last diff command
        self.output: list[str] = []
        # Current diff variant index used when re-prepping the diff
        self.variant: int = 0
        # Store diff parameters for re-running with different context
        self._diff_filename: str = ""
        self._diff_prev: str = ""
        self._diff_curr: str = ""
        # Where to return when leaving the diff view: (state_name, widget_id, footer)
        # Always initialized to a non-None default so callers can rely on it.
        self._go_back: tuple = ("history_file", RIGHT_FILE_LIST_ID, RIGHT_FILE_FOOTER)
        # FullScreenBase configuration for key_right
        self._split_layout_history_file = "history_file_diff"
        self._split_layout_file_history = "file_history_diff"
        self._split_layouts = (self._split_layout_history_file, self._split_layout_file_history)
        self._fullscreen_layout = "diff_fullscreen"
        self._fullscreen_widget_id = DIFF_LIST_ID
        self._fullscreen_footer = DIFF_FOOTER_2
        self._split_footer = DIFF_FOOTER_1
        # Side-by-side rendering configuration
        self._sbs_left_width_pct = 50  # Left panel percentage (0-100)
        self._sbs_gutter_width = 3  # Gutter width in characters
        # Horizontal scroll offset for side-by-side panels (columns)
        # Measured in characters from the left of each panel.
        self._sbs_hscroll = 0
        # Last search term for repeated '>'/'<' searches
        self._last_search = None

    def on_key(self, event: events.Key) -> None:
        """Handle bracket/equal keys directly for side-by-side width control."""
        try:
            k = getattr(event, "key", None)
            ch = getattr(event, "character", None)
            logger.debug(
                "DiffList.on_key: key=%r character=%r variant=%r side_idx=%r split=%r",
                k,
                ch,
                self.variant,
                self.app.variant_sidebyside_index,
                self._sbs_left_width_pct,
            )

            if ch == "{":
                logger.debug("DiffList.on_key: consumed '{' -> action_hscroll_left")
                event.stop()
                self.action_hscroll_left()
                return
            if ch == "}":
                logger.debug("DiffList.on_key: consumed '}' -> action_hscroll_right")
                event.stop()
                self.action_hscroll_right()
                return
            if k in ("bracket_left", "left_square_bracket") or ch == "[":
                logger.debug("DiffList.on_key: consumed '[' -> action_left_bracket")
                event.stop()
                self.action_left_bracket()
                return
            if k in ("bracket_right", "right_square_bracket") or ch == "]":
                logger.debug("DiffList.on_key: consumed ']' -> action_right_bracket")
                event.stop()
                self.action_right_bracket()
                return
            if k in ("equal", "equals") or ch == "=":
                logger.debug("DiffList.on_key: consumed '=' -> action_equal")
                event.stop()
                self.action_equal()
                return
            super().on_key(event)
        except Exception as e:
            self.printException(e, "DiffList.on_key failed")

    def _rerender_side_by_side_if_active(self, reason: str) -> None:
        """Re-render side-by-side output after layout/size changes."""
        try:
            if self.output and self.variant == self.app.variant_sidebyside_index:
                logger.debug(
                    "DiffList._rerender_side_by_side_if_active: reason=%r size=%r split=%r",
                    reason,
                    (self.size.width, self.size.height) if hasattr(self, "size") else None,
                    self._sbs_left_width_pct,
                )
                self._render_output()
        except Exception as e:
            self.printException(e, "_rerender_side_by_side_if_active failed")

    def key_right(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Promote to fullscreen and re-render side-by-side after layout settles."""
        try:
            super().key_right(event, recursive=recursive)
            try:
                self.app.call_later(lambda: self._rerender_side_by_side_if_active("key_right layout change"))
            except Exception as e:
                self.printException(e, "DiffList.key_right: call_later re-render failed")
        except Exception as e:
            self.printException(e, "DiffList.key_right failed")

    def key_left(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Return from fullscreen/split and re-render side-by-side after layout settles."""
        try:
            super().key_left(event, recursive=recursive)
            try:
                self.app.call_later(lambda: self._rerender_side_by_side_if_active("key_left layout change"))
            except Exception as e:
                self.printException(e, "DiffList.key_left: call_later re-render failed")
        except Exception as e:
            self.printException(e, "DiffList.key_left failed")

    def on_resize(self, event: events.Resize) -> None:
        """Re-render side-by-side output when DiffList is resized."""
        try:
            logger.debug(
                "DiffList.on_resize: size=(%r,%r) virtual=(%r,%r)",
                getattr(event, "size", None).width if getattr(event, "size", None) else None,
                getattr(event, "size", None).height if getattr(event, "size", None) else None,
                getattr(event, "virtual_size", None).width if getattr(event, "virtual_size", None) else None,
                getattr(event, "virtual_size", None).height if getattr(event, "virtual_size", None) else None,
            )
            self._rerender_side_by_side_if_active("on_resize")
        except Exception as e:
            self.printException(e, "DiffList.on_resize failed")

    def prepDiffList(self, filename: str, prev: str, curr: str, variant_index: int, go_back: tuple) -> None:
        """
        Prepare and display a diff for `filename` between `prev` and `curr`.

        This builds a diff command via `app.build_diff_cmd`, falls back to
        a metadata summary when no textual diff is present, and renders the
        output into the diff list. `variant_index` selects a diff variant
        from `app.diff_variants` and `go_back` records the return location.
        """
        try:
            logger.debug(
                "DiffList.prepDiffList: filename=%s prev=%s curr=%s variant=%s go_back=%s unified_context=%s",
                filename,
                prev,
                curr,
                variant_index,
                go_back,
                self.app.unified_context,
            )
            # Store parameters for re-running with different context values
            self._diff_filename = filename
            self._diff_prev = prev
            self._diff_curr = curr

            # Use the app-level `gitRepo` and build the selected variant
            try:
                gitrepo = self.app.gitRepo
                variant_arg = None
                try:
                    if 0 <= variant_index < len(self.app.diff_variants):
                        variant_arg = self.app.diff_variants[variant_index]
                except Exception as _ex:
                    self.printException(_ex, "prepDiffList: retrieving app.diff_variants failed")
                    variant_arg = None

                # Support variants as either a single arg string or a list
                # of argv tokens (e.g. ["--word-diff=porcelain", "--no-color"]).
                variation = None
                if variant_arg:
                    if isinstance(variant_arg, (list, tuple)):
                        variation = [str(v) for v in variant_arg if v]
                    else:
                        variation = [str(variant_arg)]

                out_lines = gitrepo.getDiff(filename, prev, curr, variation, unified_context=self.app.unified_context)
                out = "\n".join(out_lines) if out_lines else ""
            except Exception as e:
                self.printException(e, "prepDiffList: gitRepo.getDiff failed")

            # Save output lines on the object and render via helper
            # Prepend a human-readable header describing the diff context
            try:
                display_hash_length = self.app.hash_length
                p_short = prev[:display_hash_length] if prev else "None"
                c_short = curr[:display_hash_length] if curr else "None"

                try:
                    # Prefer a human-friendly variant name for the header
                    variant_name = None
                    try:
                        if 0 <= variant_index < len(DIFF_VARIANT_NAMES):
                            variant_name = DIFF_VARIANT_NAMES[variant_index]
                    except Exception as e:
                        self.printException(e, "prepDiffList: retrieving variant name failed")
                        variant_name = None

                    if not variant_name:
                        variant_name = DIFF_VARIANT_NAMES[0] if DIFF_VARIANT_NAMES else "default"

                    header = f"Diff ({variant_name}, color={self.app.color_scheme}, context=-U{self.app.unified_context}) for {filename} between {p_short} and {c_short}"
                except Exception as e:
                    self.printException(e, "prepDiffList: building header failed")
                    header = (
                        f"Diff (context=-U{self.app.unified_context}) for {filename} between {p_short} and {c_short}"
                    )
            except Exception as e:
                self.printException(e, "prepDiffList: header preparation failed")
                header = "Diff"
            self.output = [header] + (out.splitlines() if out else [])
            # Ensure the header line is not selectable by setting the
            # minimum selectable index to 1 so navigation skips it.
            try:
                self._min_index = 1
            except Exception as e:
                self.printException(e, "prepDiffList: setting _min_index failed")
            # Record the active variant for future re-renders
            self.variant = variant_index
            # Update go-back state only.
            self._go_back = go_back

            self._render_output()
            self._populated = True
            self._highlight_top()

            self._finalize_prep_common(curr_hash=curr, prev_hash=prev, path=filename)
        except Exception as e:
            self.printException(e, "prepDiffList failed")

    def key_c(self, event: events.Key | None = None) -> None:
        """
        Cycle diff color schemes (c/C) and re-render.

        Cycles through DIFF_COLOR_SCHEMES in order. Selecting `none` disables
        colorized rendering; other schemes enable color/styling based on the
        configured mapping.
        """
        logger.debug("DiffList.key_c called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "DiffList.key_c: event.stop failed")
            try:
                # cycle to next scheme
                current = self.app.color_scheme
                try:
                    idx = DIFF_COLOR_SCHEMES.index(current)
                except ValueError as e:
                    self.printException(e, "DiffList.key_c: unknown current color_scheme")
                    idx = 0
                new_idx = (idx + 1) % len(DIFF_COLOR_SCHEMES)
                new_scheme = DIFF_COLOR_SCHEMES[new_idx]
                self.app.color_scheme = new_scheme
                # update boolean used by render helpers
                self._colorized = new_scheme != "none"
                logger.debug(
                    "DiffList: switched color scheme %s -> %s (colorized=%s)", current, new_scheme, self._colorized
                )
                # Update Diff header to reflect new scheme
                try:
                    hdr = None
                    try:
                        hdr = self.app.query_one(f"#{DIFF_TITLE}", Label)
                    except Exception as e:
                        self.printException(e, "DiffList.key_c: query header failed")
                        hdr = None
                    if hdr is not None:
                        try:
                            # Reflect both the active variant and the new color
                            vname = DIFF_VARIANT_NAMES[self.variant]
                            hdr.update(Text(f"Diff ({vname}, color={new_scheme})"))
                        except Exception as e:
                            self.printException(e, "DiffList.key_c: updating header failed")
                except Exception as e:
                    self.printException(e, "DiffList.key_c header update outer failure")
                self._render_output()
            except Exception as e:
                self.printException(e, "DiffList.key_c re-render failed")
        except Exception as e:
            self.printException(e, "DiffList.key_c failed")

    def key_C(self, event: events.Key | None = None) -> None:
        """Alias for `key_c` (Shift-C)."""
        logger.debug("DiffList.key_C called: key=%r", getattr(event, "key", None))
        return self.key_c(event, recursive=True)

    def _build_porcelain_rows(self, body_lines: list[str], colorized: bool) -> list[Text]:
        """
        Convert `git diff --word-diff=porcelain` body lines into render rows.

        Porcelain uses prefixed runs:
        - `'\u00a0'` context run
        - `'+'` added run
        - `'-'` removed run
        - `'~'` newline marker
        This helper reconstructs visual lines and applies inline styles to
        added/removed runs so coloring appears *within* each line.
        """
        rows: list[Text] = []
        current = Text("")

        def flush_current(force_empty: bool = False) -> None:
            nonlocal current
            if current.plain or force_empty:
                rows.append(current)
                current = Text("")

        def is_patch_header(ln: str) -> bool:
            return (
                ln.startswith("diff --git")
                or ln.startswith("index ")
                or ln.startswith("@@")
                or ln.startswith("---")
                or ln.startswith("+++")
            )

        try:
            for ln in body_lines:
                # newline marker in porcelain output
                if ln == "~":
                    flush_current(force_empty=True)
                    continue

                # flush accumulated inline row before rendering patch metadata
                if is_patch_header(ln):
                    flush_current(force_empty=False)
                    # Optionally insert a blank line before hunk headers
                    if ln.startswith("@@") and self.app.blank_before_hunk:
                        rows.append(Text(""))

                    style = None
                    if colorized:
                        if ln.startswith("@@"):
                            style = "magenta"
                        elif ln.startswith("diff --git") or ln.startswith("index "):
                            style = "bold white"
                    rows.append(Text(ln, style=style) if style else Text(ln))
                    continue

                if ln and ln[0] in (" ", "+", "-"):
                    token = ln[0]
                    payload = ln[1:]
                    # When colorized, apply inline styles; when not, use
                    # compact bracket markers to indicate inline changes.
                    if colorized:
                        # map scheme -> style
                        scheme = self.app.color_scheme
                        mapping = DIFF_SCHEME_MAP.get(scheme, DIFF_SCHEME_MAP[DIFF_COLOR_SCHEMES[0]])
                        if token == "+":
                            seg_style = mapping.get("add")
                            current.append(payload, style=seg_style)
                        elif token == "-":
                            seg_style = mapping.get("del")
                            current.append(payload, style=seg_style)
                        else:
                            # context run: no inline styling
                            current.append(payload)
                    else:
                        if token == "+":
                            current.append(f"[+{payload}+]")
                        elif token == "-":
                            current.append(f"[-{payload}-]")
                        else:
                            current.append(payload)
                else:
                    # Defensive fallback for unexpected lines
                    flush_current(force_empty=False)
                    rows.append(Text(ln))

            flush_current(force_empty=False)
            return rows
        except Exception as e:
            self.printException(e, "_build_porcelain_rows failed")
            return [Text(ln) for ln in body_lines]

    def _parse_unified_to_side_by_side(self, lines: list[str]) -> list[tuple[str, str, str]]:
        """
        Convert unified diff format to side-by-side (left, gutter, right) tuples.

        This reformats unified diff output (with +/- prefixes) into a paired format
        where deleted lines appear in the left column and added lines in the right.
        Unchanged lines appear in both columns. The gutter marker indicates the
        relationship: space (context), '<' (deleted), '>' (added), '|' (changed).
        """
        try:
            result: list[tuple[str, str, str]] = []
            i = 0

            while i < len(lines):
                line = lines[i]

                # Pass through headers and hunk markers unchanged (full-width, left column)
                if (
                    line.startswith("diff --git")
                    or line.startswith("index ")
                    or line.startswith("---")
                    or line.startswith("+++")
                    or line.startswith("@@")
                ):
                    result.append((line, "", ""))
                    i += 1
                    continue

                if not line:
                    result.append(("", "", ""))
                    i += 1
                    continue

                # Unified diff format: first character indicates line type
                if len(line) >= 1:
                    prefix = line[0]
                    content = line[1:] if len(line) > 1 else ""

                    if prefix == " ":
                        # Context line: same on both sides
                        result.append((content, " ", content))
                        i += 1
                    elif prefix == "-":
                        # Deleted line: check if next line is an addition for pairing
                        if i + 1 < len(lines) and len(lines[i + 1]) >= 1 and lines[i + 1][0] == "+":
                            # Pair with following addition: show as changed
                            right_content = lines[i + 1][1:] if len(lines[i + 1]) > 1 else ""
                            result.append((content, "|", right_content))
                            i += 2  # Skip both - and + lines
                        else:
                            # Unpaired deletion
                            result.append((content, "<", ""))
                            i += 1
                    elif prefix == "+":
                        # Unpaired addition (paired case handled above)
                        result.append(("", ">", content))
                        i += 1
                    else:
                        # Unknown prefix: pass through as-is
                        result.append((line, "", ""))
                        i += 1
                else:
                    result.append((line, "", ""))
                    i += 1

            return result
        except Exception as e:
            self.printException(e, "_parse_unified_to_side_by_side failed")
            # Fallback: return lines as-is in left column
            return [(line, " ", "") for line in lines]

    def _render_side_by_side(self, parsed_lines: list[tuple[str, str, str]], colorized: bool) -> list[Text]:
        """
        Render side-by-side formatted lines with truncation, gutter, and color.

        Layout: [left_panel][gutter][right_panel]
        - Dynamic width based on self._sbs_left_width_pct
        - Truncate with ellipsis for long lines
        - Apply color scheme based on gutter marker:
          * space: unchanged context
          * <: deleted line (left only)
          * >: added line (right only)
          * |: changed line (both sides differ)
        - Single vertical cursor stays in left column
        """
        try:
            rendered: list[Text] = []

            # Use the diff widget width (not full app width) so side-by-side
            # aligns correctly in split/3-pane layouts.
            try:
                if hasattr(self, "size") and hasattr(self.size, "width") and self.size.width:
                    term_width = int(self.size.width)
                elif hasattr(self.app, "size") and hasattr(self.app.size, "width"):
                    term_width = int(self.app.size.width)
                else:
                    term_width = 120
            except Exception as e:
                printException(e, "_render_side_by_side width calculation failed")
                term_width = 120

            # Calculate panel widths
            gutter_w = self._sbs_gutter_width
            # Keep a small safety margin for list/label padding/borders.
            usable_width = max(20, term_width - gutter_w - 2)
            left_w = max(10, int(usable_width * (self._sbs_left_width_pct / 100)))
            right_w = max(8, usable_width - left_w)

            logger.debug(
                "_render_side_by_side: pane_width=%r usable=%r left_w=%r right_w=%r split=%r",
                term_width,
                usable_width,
                left_w,
                right_w,
                self._sbs_left_width_pct,
            )

            # Get color scheme mapping
            scheme = self.app.color_scheme if colorized else "none"
            colors = DIFF_SCHEME_MAP.get(scheme, DIFF_SCHEME_MAP["red-green"])

            for left, gutter, right in parsed_lines:
                # Handle metadata/header lines (span full width, no gutter).
                # Important: do NOT treat left-only/right-only diff rows as
                # headers; those need truncation and gutter rendering.
                if gutter == "":
                    style = None
                    if colorized and (
                        left.startswith("diff --git") or left.startswith("index ") or left.startswith("@@")
                    ):
                        if left.startswith("diff "):
                            style = "bold white"
                        else:
                            style = "magenta"

                    # Optionally insert a blank line before hunk headers
                    if left.startswith("@@") and self.app.blank_before_hunk:
                        rendered.append(Text(""))

                    rendered.append(Text(left, style=style) if style else Text(left))
                    continue

                # Determine styles based on gutter marker
                left_style = None
                right_style = None
                gutter_style = None

                # Make unchanged rows visually distinct with a white gutter block.
                if gutter == " ":
                    gutter_style = "on white"

                if colorized:
                    if gutter == "<":  # Line only in left (deleted)
                        left_style = colors.get("del_span")
                        gutter_style = colors.get("del_span")
                    elif gutter == ">":  # Line only in right (added)
                        right_style = colors.get("add_span")
                        gutter_style = colors.get("add_span")
                    elif gutter == "|":  # Line differs between sides
                        left_style = colors.get("del_span")
                        right_style = colors.get("add_span")
                        gutter_style = "yellow"

                # Truncate with ellipsis for long lines, applying horizontal
                # scroll offset so the user can pan left/right across long
                # lines. When scrolled, show leading/trailing ellipses as
                # appropriate.
                def _windowed(text: str, width: int) -> str:
                    o = max(0, int(self._sbs_hscroll))
                    if width <= 0:
                        return ""
                    # No left offset: simple leading/trailing ellipsis
                    if o == 0:
                        if len(text) > width:
                            if width > 1:
                                return text[: max(1, width - 1)] + "…"
                            return text[:width]
                        return text

                    # Left offset present: show leading ellipsis
                    # Determine if there's content beyond the right edge
                    if len(text) > o + width:
                        # Both sides elided: use leading+trailing ellipses
                        if width > 2:
                            mid = text[o : o + width - 2]
                            return "…" + mid + "…"
                        # Very narrow width: fill with ellipses
                        return "…" * width
                    else:
                        # Only leading elided
                        if width > 1:
                            mid = text[o : o + width - 1]
                            return "…" + mid
                        return "…"

                left_display = _windowed(left, left_w)
                right_display = _windowed(right, right_w)

                # Pad to maintain column alignment
                left_display = left_display.ljust(left_w)
                gutter_display = gutter.center(gutter_w)
                right_display = right_display.ljust(right_w)

                # Build combined Text object with styled segments
                row = Text()
                row.append(left_display, style=left_style)
                if gutter == " " and gutter_w >= 3:
                    # Only the center separator cell should be highlighted.
                    side_pad = (gutter_w - 1) // 2
                    row.append(" " * side_pad)
                    row.append(" ", style="on white")
                    row.append(" " * (gutter_w - side_pad - 1))
                else:
                    row.append(gutter_display, style=gutter_style)
                row.append(right_display, style=right_style)

                rendered.append(row)

            return rendered
        except Exception as e:
            self.printException(e, "_render_side_by_side failed")
            # Fallback to plain text with space-separated columns
            return [Text(f"{left} {gutter} {right}") for left, gutter, right in parsed_lines]

    def _render_output(self) -> None:
        """Clear and render `self.output` honoring `self._colorized` and variant type."""
        try:
            old_index = int(self.index or 0)
            # Determine variant type
            is_porcelain_variant = False
            is_side_by_side_variant = False
            try:
                variant_arg = None
                if 0 <= int(self.variant or 0) < len(self.app.diff_variants):
                    variant_arg = self.app.diff_variants[int(self.variant or 0)]

                if isinstance(variant_arg, (list, tuple)):
                    is_porcelain_variant = any(str(v).startswith("--word-diff=porcelain") for v in variant_arg)
                    is_side_by_side_variant = any(str(v) == "--side-by-side" for v in variant_arg)
                elif isinstance(variant_arg, str):
                    is_porcelain_variant = variant_arg.startswith("--word-diff=porcelain")
                    is_side_by_side_variant = variant_arg == "--side-by-side"

                # Side-by-side no longer relies on a git flag; variant index/name
                # are the source of truth.
                try:
                    v_idx = int(self.variant or 0)
                    if 0 <= v_idx < len(DIFF_VARIANT_NAMES):
                        if DIFF_VARIANT_NAMES[v_idx] == "side-by-side":
                            is_side_by_side_variant = True
                except Exception as _e:
                    self.printException(_e, "_render_output: side-by-side variant-name check failed")
            except Exception as e:
                self.printException(e, "_render_output: determining variant type failed")

            rendered_rows: list[Text] = []
            try:
                if self.output:
                    header_text = Text(self.output[0])
                    body_lines = self.output[1:]

                    if is_side_by_side_variant:
                        # Check if terminal width is sufficient for side-by-side
                        current_width = int(self.size.width)
                        min_width = self.app.minimum_sidebyside_width
                        if current_width >= min_width:
                            # Convert unified diff to side-by-side format and render
                            parsed = self._parse_unified_to_side_by_side(body_lines)
                            rendered_rows = [header_text] + self._render_side_by_side(parsed, self._colorized)
                            logger.debug(
                                "_render_output: rendering side-by-side (width=%d >= min=%d)",
                                current_width,
                                min_width,
                            )
                        else:
                            # Terminal too narrow - fall back to unified view
                            logger.debug(
                                "_render_output: falling back to unified view (width=%d < min=%d)",
                                current_width,
                                min_width,
                            )
                            # Render as classic unified diff with note in header
                            header_with_note = Text.assemble(
                                header_text,
                                (" [terminal too narrow for side-by-side]", "italic dim"),
                            )
                            rendered_rows = [header_with_note]
                            for ln in body_lines:
                                # Optionally insert blank line before hunk headers
                                if ln.startswith("@@") and self.app.blank_before_hunk:
                                    rendered_rows.append(Text(""))
                                style = None
                                if self._colorized:
                                    scheme = self.app.color_scheme
                                    mapping = DIFF_SCHEME_MAP.get(scheme, DIFF_SCHEME_MAP[DIFF_COLOR_SCHEMES[0]])
                                    if ln.startswith("+") and not ln.startswith("+++"):
                                        style = mapping.get("add_span")
                                    elif ln.startswith("-") and not ln.startswith("---"):
                                        style = mapping.get("del_span")
                                    elif ln.startswith("@@"):
                                        style = "magenta"
                                    elif ln.startswith("diff --git") or ln.startswith("index "):
                                        style = "bold white"
                                rendered_rows.append(Text(ln, style=style) if style else Text(ln))
                    elif is_porcelain_variant:
                        rendered_rows = [header_text] + self._build_porcelain_rows(body_lines, self._colorized)
                    else:
                        # Classic unified diff rendering
                        rendered_rows = [header_text]
                        for ln in body_lines:
                            # Optionally insert blank line before hunk headers
                            if ln.startswith("@@") and self.app.blank_before_hunk:
                                rendered_rows.append(Text(""))
                            style = None
                            if self._colorized:
                                scheme = self.app.color_scheme
                                mapping = DIFF_SCHEME_MAP.get(scheme, DIFF_SCHEME_MAP[DIFF_COLOR_SCHEMES[0]])
                                if ln.startswith("+") and not ln.startswith("+++"):
                                    style = mapping.get("add_span")
                                elif ln.startswith("-") and not ln.startswith("---"):
                                    style = mapping.get("del_span")
                                elif ln.startswith("@@"):
                                    style = "magenta"
                                elif ln.startswith("diff --git") or ln.startswith("index "):
                                    style = "bold white"
                            rendered_rows.append(Text(ln, style=style) if style else Text(ln))
            except Exception as e:
                self.printException(e, "_render_output: preparing rendered rows failed")
                rendered_rows = [Text(ln) for ln in (self.output or [])]

            self.clear()
            for i, txt in enumerate(rendered_rows):
                try:
                    # Compute a plain-text search representation for the row so
                    # find/search logic can operate on a stable string regardless
                    # of how the Label stores renderables internally.
                    try:
                        if hasattr(txt, "plain"):
                            search_text = str(txt.plain or "")
                        else:
                            search_text = str(txt)
                    except Exception as e:
                        self.printException(e, "_render_output: computing search_text failed")
                        search_text = str(txt)

                    item = ListItem(Label(txt))
                    # Attach a search-friendly text attribute used by _find_and_activate
                    try:
                        setattr(item, "_search_text", search_text)
                    except Exception as _ex:
                        self.printException(_ex, "_render_output: setting _search_text failed")

                    # Make the first line (our diff header) unselectable so
                    # navigation/highlight skips it.
                    try:
                        if i == 0:
                            item._selectable = False
                            item._diff_header = True
                            # Keep header search text empty so it isn't matched by finds
                            try:
                                item._search_text = ""
                            except Exception as e:
                                self.printException(e, "_render_output: setting header search_text failed")
                    except Exception as _ex:
                        self.printException(_ex, "_render_output: setting header metadata failed")

                    self.append(item)
                except Exception as e:
                    self.printException(e, "_render_output append failed")

            # Restore prior selection index after re-render so layout toggles
            # and width updates don't jump the cursor unexpectedly.
            if rendered_rows:
                min_idx = int(self._min_index)
                max_idx = len(rendered_rows) - 1
                target_index = max(min_idx, min(old_index, max_idx))
                logger.debug(
                    "_render_output: restoring index old=%r target=%r min=%r max=%r",
                    old_index,
                    target_index,
                    min_idx,
                    max_idx,
                )
                try:
                    self.index = target_index
                    # Schedule highlight activation after refresh so visual highlighting
                    # is correctly applied after layout transitions.
                    try:
                        self.call_after_refresh(lambda: self._safe_activate_index(target_index))
                    except Exception as e:
                        self.printException(e, "_render_output: scheduling highlight activation failed")
                        # Fallback to immediate activation if scheduling fails
                        self._activate_index(target_index)
                except Exception as e:
                    self.printException(e, "_render_output: restoring index failed")
        except Exception as e:
            self.printException(e, "_render_output failed")

    def key_d(self, event: events.Key | None = None) -> None:
        """Cycle to the next diff variant and re-run `prepDiffList`."""
        logger.debug(
            "DiffList.key_d called: key=%r variant=%r index=%r",
            getattr(event, "key", None),
            self.variant,
            self.index,
        )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "DiffList.key_d: event.stop failed")
            # Rotate to the next diff variant and re-run the diff preparer.
            try:
                total = len(self.app.diff_variants or [None])
                new_variant = (int(self.variant or 0) + 1) % max(1, total)
            except Exception as e:
                self.printException(e, "DiffList.key_d: computing new variant failed")
                new_variant = 0
            logger.debug("DiffList.key_d: switching to variant %s from %s", new_variant, self.variant)
            try:
                # Use the app-level path and selected commit pair when re-prepping
                # Preserve the current go_back state when re-prepping.
                # Build repository-relative filename from rel_dir/rel_file
                try:
                    rd = self.app.rel_dir
                    rf = self.app.rel_file
                    filename = os.path.join(rd or "", rf) if rf else (rd or "")
                except Exception as e:
                    self.printException(e, "DiffList.key_d: computing filename failed")
                    filename = ""
                if self.app.previous_hash is not None:
                    prev = self.app.previous_hash
                else:
                    prev = GitRepo.NEWREPO

                curr = self.app.current_hash

                self.prepDiffList(
                    filename,
                    prev,
                    curr,
                    new_variant,
                    self._go_back,
                )
            except Exception as e:
                self.printException(e, "DiffList.key_d: re-prep failed")
        except Exception as e:
            self.printException(e, "DiffList.key_d failed")

    def action_left_bracket(self) -> None:
        """Decrease left panel width in side-by-side mode ([ key)."""
        try:
            # Only adjust if we're in side-by-side variant
            if self.variant == self.app.variant_sidebyside_index:
                old_pct = self._sbs_left_width_pct
                self._sbs_left_width_pct = max(20, self._sbs_left_width_pct - 5)
                logger.debug("action_left_bracket: split old=%r new=%r", old_pct, self._sbs_left_width_pct)
                logger.debug(f"DiffList: adjusted left width to {self._sbs_left_width_pct}%")
                self._render_output()
        except Exception as e:
            self.printException(e, "action_left_bracket failed")

    def action_right_bracket(self) -> None:
        """Increase left panel width in side-by-side mode (] key)."""
        try:
            # Only adjust if we're in side-by-side variant
            if self.variant == self.app.variant_sidebyside_index:
                old_pct = self._sbs_left_width_pct
                self._sbs_left_width_pct = min(80, self._sbs_left_width_pct + 5)
                logger.debug("action_right_bracket: split old=%r new=%r", old_pct, self._sbs_left_width_pct)
                logger.debug(f"DiffList: adjusted left width to {self._sbs_left_width_pct}%")
                self._render_output()
        except Exception as e:
            self.printException(e, "action_right_bracket failed")

    def action_equal(self) -> None:
        """Reset to 50/50 split in side-by-side mode (= key)."""
        try:
            # Only adjust if we're in side-by-side variant
            if self.variant == self.app.variant_sidebyside_index:
                old_pct = self._sbs_left_width_pct
                self._sbs_left_width_pct = 50
                logger.debug("action_equal: split old=%r new=%r", old_pct, self._sbs_left_width_pct)
                logger.debug("DiffList: reset to 50/50 split")
                self._render_output()
        except Exception as e:
            self.printException(e, "action_equal failed")

    def action_hscroll_left(self) -> None:
        """Scroll side-by-side view left by 10 columns ({ key)."""
        try:
            if self.variant == self.app.variant_sidebyside_index:
                old = self._sbs_hscroll
                self._sbs_hscroll = max(0, self._sbs_hscroll - 10)
                logger.debug("action_hscroll_left: old=%r new=%r", old, self._sbs_hscroll)
                self._render_output()
        except Exception as e:
            self.printException(e, "action_hscroll_left failed")

    def action_hscroll_right(self) -> None:
        """Scroll side-by-side view right by 10 columns (} key)."""
        try:
            if self.variant == self.app.variant_sidebyside_index:
                old = self._sbs_hscroll
                self._sbs_hscroll = max(0, self._sbs_hscroll + 10)
                logger.debug("action_hscroll_right: old=%r new=%r", old, self._sbs_hscroll)
                self._render_output()
        except Exception as e:
            self.printException(e, "action_hscroll_right failed")

    def key_plus(self, event: events.Key | None = None) -> None:
        """Increment unified context (-U value) and re-run the diff."""
        logger.debug(
            "DiffList.key_plus called: key=%r current_context=%r index=%r",
            getattr(event, "key", None),
            self.app.unified_context,
            self.index,
        )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "DiffList.key_plus: event.stop failed")

            # Increment the unified context value and re-run the diff
            try:
                self.app.unified_context = self.app.unified_context + 1
                logger.debug("DiffList.key_plus: incremented context to %s", self.app.unified_context)
            except Exception as e:
                self.printException(e, "DiffList.key_plus: incrementing context failed")
                return

            self.prepDiffList(
                self._diff_filename,
                self._diff_prev,
                self._diff_curr,
                self.variant,
                self._go_back,
            )
        except Exception as e:
            self.printException(e, "DiffList.key_plus failed")

    def key_minus(self, event: events.Key | None = None) -> None:
        """Decrement unified context (-U value, minimum 0) and re-run the diff."""
        logger.debug(
            "DiffList.key_minus called: key=%r current_context=%r index=%r",
            getattr(event, "key", None),
            self.app.unified_context,
            self.index,
        )
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "DiffList.key_minus: event.stop failed")

            # Decrement the unified context value (with minimum of 0) and re-run the diff
            try:
                self.app.unified_context = max(0, self.app.unified_context - 1)
                logger.debug("DiffList.key_minus: decremented context to %s", self.app.unified_context)
            except Exception as e:
                self.printException(e, "DiffList.key_minus: decrementing context failed")
                return

            self.prepDiffList(
                self._diff_filename,
                self._diff_prev,
                self._diff_curr,
                self.variant,
                self._go_back,
            )
        except Exception as e:
            self.printException(e, "DiffList.key_minus failed")

    def key_D(self, event: events.Key | None = None) -> None:
        """Alias for `key_d` (Shift-D)."""
        logger.debug("DiffList.key_D called: key=%r index=%r", getattr(event, "key", None), self.index)
        return self.key_d(event, recursive=True)

    def watch_size(self) -> None:
        """
        Re-render side-by-side output when widget size changes (e.g., during layout recalculation).

        This ensures that when DiffList is laid out in 3-pane mode, the side-by-side
        rendering is recalculated with correct pane widths after the widget receives
        its final size from the layout engine.
        """
        try:
            # Only re-render if we have output and we're in side-by-side mode
            if self.output and self.variant == self.app.variant_sidebyside_index:
                logger.debug(
                    "DiffList.watch_size triggered: re-rendering side-by-side with new size=%r",
                    (self.size.width, self.size.height) if hasattr(self, "size") else None,
                )
                self._render_output()
        except Exception as e:
            self.printException(e, "watch_size failed")


#    - `toggle-color` / `c`: toggle colorized diff output.
#    - `cycle-diff-variant` / `d`: cycle to the next diff variant (e.g. ignore-space-change, patience).
# Command palette (^P):
# - Press Ctrl-P (Textual command palette) to run commands directly. Useful
#    commands to wire up include:
#    - `open-file`, `diff <file> [prev] <curr>`, `file-history <path>`,
#        `goto-commit <hash>`, `toggle-color`, `next-hunk`, `prev-hunk`,
#        `stage <path>`, `unstage <path>`, `refresh`.


class HelpList(AppBase):
    """Renders help text as list rows and allows restoring previous state."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.highlight_bg_style = HIGHLIGHT_HELP_BG
        # Help lists are selectable from the first block/row (index 0).
        self._min_index = 0

    def prepHelp(self) -> None:
        """
        Populate the help list with rendered Markdown blocks.

        Splits the help text into paragraph blocks and appends each as a
        separate `ListItem` so the ListView can provide natural scrolling.
        """
        try:
            logger.debug("prepHelp: invoked")
            self.clear()
            try:
                # Split help text into paragraph/block chunks so each block
                # is its own ListItem. This preserves Markdown formatting
                # while allowing the ListView to provide scrolling behavior.
                blocks = re.split(r"\n\s*\n", HELP_TEXT.strip())
                sep = None
                for i, blk in enumerate(blocks):
                    if not blk:
                        continue
                    try:
                        # Add a spacer row between each block to provide visual
                        # separation when rendered in the ListView.
                        if sep is not None:
                            sep_item = ListItem(Label(Text(sep)))
                            sep_item._search_text = ""
                            self.append(sep_item)
                        else:
                            sep = ""
                        # Render each block using Markdown; allow the renderer
                        # to determine spacing/formatting (including H1).
                        block_item = ListItem(Label(Markdown(blk)))
                        block_item._search_text = str(blk)
                        self.append(block_item)
                    except Exception as e:
                        self.printException(e, "prepHelp append failed for Markdown block")
            except Exception as e:
                self.printException(e, "prepHelp append failed")
            self._populated = True
            self._highlight_top()
        except Exception as e:
            self.printException(e, "prepHelp failed")

    def key_enter(self, event: events.Key | None = None) -> None:
        """Return from the help view to the previously-saved app state."""
        logger.debug("HelpList.key_enter called: key=%r index=%r", getattr(event, "key", None), self.index)
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "HelpList.key_enter: event.stop failed")
            app = self.app
            app.restore_state()
        except Exception as e:
            self.printException(e, "HelpList.key_enter failed")


class OpenFileList(FullScreenBase):
    """Renders file content as scrollable lines. Opened from history lists with 'o' key."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.highlight_bg_style = HIGHLIGHT_HELP_BG
        # Track current filename and hash for title display
        self._open_filename = ""
        self._open_hash = ""
        # Cache of currently-rendered content key (filename, hash)
        self._cached_key: tuple[str, str] | None = None
        # Track in-flight background load key to avoid duplicate scheduling
        self._loading_key: tuple[str, str] | None = None
        # FullScreenBase configuration for key_right
        self._split_layout_history_file = "history_file_open"
        self._split_layout_file_history = "file_history_open"
        self._split_layouts = (self._split_layout_file_history, self._split_layout_history_file)
        self._fullscreen_layout = "open_file_fullscreen"
        self._fullscreen_widget_id = OPEN_FILE_LIST_ID
        self._fullscreen_footer = OPEN_FILE_FOOTER_2
        # Open file views are selectable from the first content line (index 0).
        self._min_index = 0
        # Last search term for repeated '>'/'<' searches
        self._last_search = None
        self._split_footer = OPEN_FILE_FOOTER_1
        # Where to return when leaving the open-file view: (state_name, widget_id, footer)
        # Set when file is opened; allows consistent navigation regardless of split layout.
        self._go_back: tuple = ("file_history", RIGHT_HISTORY_LIST_ID, RIGHT_HISTORY_FOOTER)

    def prepOpenFileList(self, filename: str, hash_value: str) -> None:
        """
        Prepare and display file content for the given filename at hash_value.

        Shows a loading message immediately, then schedules the actual file loading
        in the background to keep the UI responsive.
        """
        try:
            logger.debug("prepOpenFileList: filename=%r hash=%r", filename, hash_value)
            requested_key = (filename, hash_value)

            # Fast path: if this exact content is already rendered, reuse it.
            try:
                if self._cached_key == requested_key and len(self.nodes() or []) > 0:
                    logger.debug("prepOpenFileList: cache hit for %r @ %r; skipping reload", filename, hash_value)
                    self._open_filename = filename
                    self._open_hash = hash_value
                    self._highlight_top()
                    return
            except Exception as e:
                self.printException(e, "prepOpenFileList: cache check failed")

            # If this key is already loading, do not schedule another load.
            if self._loading_key == requested_key:
                logger.debug("prepOpenFileList: load already in progress for %r @ %r", filename, hash_value)
                self._open_filename = filename
                self._open_hash = hash_value
                return

            self._open_filename = filename
            self._open_hash = hash_value
            self._loading_key = requested_key
            self._cached_key = None

            # Clear the list and show loading message immediately
            self.clear()
            self.append(ListItem(Label(Text("Loading file..."))))

            # Schedule the actual loading in the background so UI can update
            self.app.call_later(lambda: self._load_and_render(filename, hash_value))

            self._populated = True
        except Exception as e:
            self.printException(e, "prepOpenFileList failed")
            self._loading_key = None

    def _load_and_render(self, filename: str, hash_value: str) -> None:
        """Load file from git and start progressive rendering."""
        try:
            logger.debug("_load_and_render: starting load for %r @ %r", filename, hash_value)
            # Get file contents at the given hash using gitRepo
            reldir, relfile = self.app.gitRepo.repo_rel_path_to_reldir_relfile(filename)
            content = self.app.gitRepo.getFileContents(hash_value, reldir, relfile)

            if content is None:
                content = b"(File not found or unable to read)"

            # Decode content to string
            try:
                text_content = content.decode("utf-8", errors="replace")
            except Exception as e:
                self.printException(e, "_load_and_render: decoding content failed")
                text_content = "(Unable to decode file content)"

            # Split into lines for progressive rendering
            lines = text_content.splitlines()
            logger.debug("_load_and_render: loaded %d lines, starting progressive render", len(lines))

            # Clear the list (remove loading message)
            self.clear()

            # Start chunked rendering
            self._render_file_chunk(lines, 0)

            # Mark cache key as ready for fast reopen.
            self._cached_key = (filename, hash_value)
            self._loading_key = None

            self._highlight_top()
        except Exception as e:
            self.printException(e, "_load_and_render failed")
            self._loading_key = None
            self.clear()
            self.append(ListItem(Label(Text("(Error reading file)"))))

    def _render_file_chunk(self, lines: list, start_idx: int, chunk_size: int = 500) -> None:
        """Render a chunk of file lines, then schedule the next chunk."""
        try:
            # Render this chunk
            end_idx = min(start_idx + chunk_size, len(lines))
            for i in range(start_idx, end_idx):
                line = lines[i]
                display_line = f"{i+1:6d}:  {line}"
                item = ListItem(Label(Text(display_line)))
                # Keep a stable plain-text representation for find logic.
                try:
                    item._search_text = display_line
                except Exception as e:
                    self.printException(e, "_render_file_chunk: setting _search_text failed")
                self.append(item)

            logger.debug("prepOpenFileList: rendered lines %d-%d", start_idx, end_idx)

            # Schedule next chunk if there are more lines
            if end_idx < len(lines):
                self.app.call_later(lambda: self._render_file_chunk(lines, end_idx, chunk_size))
        except Exception as e:
            self.printException(e, "_render_file_chunk failed")

    def _update_title(self) -> None:
        """Update the title to show filename and hash."""
        try:
            title_text = f"OpenFile: {self._open_filename} @ {self._open_hash[:12]}"
            title_widget = self.app.query_one(f"#{OPEN_FILE_TITLE}", Label)
            title_widget.update(Text(title_text))
        except Exception as e:
            self.printException(e, "_update_title failed")

    def text_of(self, node: ListItem) -> str:
        """
        Extract file line text from a ListItem in OpenFileList.

        Items are structured as ListItem(Label(Text(...))), so we access directly.
        """
        try:
            # Get the Label child (first child of ListItem)
            if node.children and isinstance(node.children[0], Label):
                lbl = node.children[0]
                if hasattr(lbl, "renderable"):
                    renderable = lbl.renderable
                    if isinstance(renderable, Text):
                        return renderable.plain
            # Fallback
            return str(node)

        except Exception as e:
            self.printException(e, "OpenFileList.text_of failed")
            return str(node)


class GitDiffNavTool(AppException, App):
    """
    Main Textual application wiring the lists together.

    It composes the previously defined widgets, mounts a header/footer,
    and provides simple state save/restore stubs.
    """

    CSS = INLINE_CSS

    def __init__(
        self,
        gitRepo: GitRepo,
        rel_dir: str | None,
        rel_file: str | None,
        repo_first: bool,
        repo_hashes: list,
        no_ignored: bool,
        no_untracked: bool,
        no_initial_popup: bool,
        verbose: int,
        highlight: str | None,
        color_scheme: str | None,
        diff_variant: str | None,
        hash_length: int,
        add_authors: bool,
        unified_context: int,
        history_limit: int,
        minimum_sidebyside_width: int,
        blank_before_hunk: bool,
        output_directory: str | None,
        **kwargs,
    ):
        """
        Create the textual app.

        Parameters contract:
        - `rel_dir`: a repository-relative directory path (relative to the
          repository root). May be an empty string to indicate the
          repository root itself. Must NOT be an absolute filesystem path.
        - `rel_file`: a filename relative to `rel_dir`. Must be a basename
            (no path separators or subdirectories). May be an empty string to
            indicate no file selection. Must NOT be an absolute
            filesystem path.

        The application and preparers expect only repository-relative paths in
        their state; absolute/full filesystem paths are constructed only when
        performing filesystem or git calls using `GitRepo.full_path_for()`.
        """
        # Accept CLI options here so the app can inspect them during mount
        super().__init__(**kwargs)
        self.gitRepo = gitRepo
        # Record rel_dir/rel_file and compute canonical self.path for
        # backward compatibility with existing code paths.
        # Normalize and validate inputs per the documented contract
        self.rel_dir = os.path.normpath(rel_dir) if rel_dir else ""
        # Normalize but reject any path separators in rel_file immediately.
        if rel_file:
            # Reject any rel_file that is not a basename (no subpath)
            if os.path.basename(rel_file) != rel_file:
                raise ValueError("GitDiffNavTool.__init__: rel_file must be a basename (no subpath)")
            # Assign the validated basename directly (no normalization needed)
            self.rel_file = rel_file
        else:
            self.rel_file = ""

        # Normalize `.` to empty string for rel_dir
        if self.rel_dir == ".":
            self.rel_dir = ""

        # Application state uses only `rel_dir` and `rel_file`.
        # Do not maintain `self.path` to avoid multiple source-of-truth values.

        # Log initial rel_dir / rel_file for debugging
        logger.debug("GitDiffNavTool.__init__: rel_dir=%r rel_file=%r", self.rel_dir, self.rel_file)

        # Preserve verbosity for diagnostic controls
        self.verbose = verbose
        # Number of characters used when displaying shortened commit hashes.
        # Can be overridden via --hash-length / config.
        self.hash_length = hash_length
        # Whether to display author name and email in commit rows.
        # Can be overridden via --add-authors / config.
        self.add_authors = add_authors
        # Number of context lines for unified diffs (git diff -U option).
        # Can be overridden via --unified-context / config.
        self.unified_context = unified_context
        # Maximum number of history entries to display (0 = unlimited).
        # Can be overridden via --history-limit / config.
        self.history_limit = history_limit
        # Minimum terminal width required for side-by-side view.
        # Can be overridden via --minimum-sidebyside-width / config.
        self.minimum_sidebyside_width = minimum_sidebyside_width
        # Whether to insert a blank line before each hunk header (@@)
        self.blank_before_hunk = bool(blank_before_hunk)
        # Optional output directory used by snapshot writes from w/W.
        # When unset, snapshots are written beside the source file (legacy behavior).
        self.output_directory = os.path.abspath(output_directory) if output_directory else None

        # Optional initial filename basename to highlight when listing a dir
        self.highlight = highlight

        self.no_initial_popup = no_initial_popup
        self.no_ignored = no_ignored
        self.no_untracked = no_untracked
        self.repo_first = repo_first
        # optional repo hash initialization (list of 1 or 2 hashes)
        # Normalize repo_hashes to a list (avoid mutable default)
        self.repo_hashes = repo_hashes or []
        # placeholders for runtime state
        # `repo_root` is provided by main and should not be modified further.
        # Set the application title to include the repository path
        self.title = f"GitDiffNavTool ({self.gitRepo.get_repo_root()})"
        self._saved_state = None
        self._current_layout = None
        # Track current focus selector for save/restore; initialize here
        self._current_focus = None
        # Track the currently-selected and previous commit hashes
        self.current_hash = None
        self.previous_hash = None

        # Optional diff variant argument sets indexed by variant_index.
        # index 0 -> default unified diff
        # index 1 -> ignore space changes
        # index 2 -> patience algorithm
        # index 3 -> word-diff porcelain (machine-parseable)
        # index 4 -> side-by-side (internally reformatted from unified diff)
        self.diff_variants: list[Optional[list[str]]] = [
            None,
            ["--ignore-space-change"],
            ["--diff-algorithm=patience"],
            ["--word-diff=porcelain", "--no-color"],
            None,  # Side-by-side uses unified diff and reformats it
        ]
        self.color_scheme = color_scheme
        # Compute the index of the side-by-side variant for use in render checks
        try:
            self.variant_sidebyside_index = DIFF_VARIANT_NAMES.index("side-by-side")
        except ValueError as e:
            printException(e, "variant_sidebyside_index: 'side-by-side' not in DIFF_VARIANT_NAMES")
            raise RuntimeError("'side-by-side' variant must exist in DIFF_VARIANT_NAMES") from e
        # Record any requested initial diff variant name for on_mount application
        logger.debug(
            "GitDiffNavTool.__init__: diff_variant=%r, DIFF_VARIANT_NAMES=%r variant_sidebyside_index=%r",
            diff_variant,
            DIFF_VARIANT_NAMES,
            self.variant_sidebyside_index,
        )
        self.initial_diff_variant = diff_variant

    def compose(self):
        """
        Yield the canonical six-column layout widgets for the app.

        The method composes header, six content columns (files/history/diff/help),
        and the footer label used by `change_footer`.
        """
        # Compose the canonical six-column layout using Vertical columns
        yield Header()
        # Dedicated overlay root for non-modal overlays (find input, etc.)
        yield Static(id="overlay-root")
        with Horizontal(id="main"):
            with Vertical(id="left-file-column"):
                yield Label(Text("Files"), id=LEFT_FILE_TITLE)
                # Key legend (static, outside the virtualized ListView so
                # it cannot be pruned by Textual's virtualization).
                yield Label(Text(FILELIST_KEY_ROW_TEXT, style=STYLE_FILELIST_KEY), id="left-file-key")
                # Directory header updated by the file-list renderer when
                # navigating between directories.
                yield Label(Text("", style=STYLE_HELP_BG), id="left-file-dir")
                yield FileModeFileList(id=LEFT_FILE_LIST_ID)
            with Vertical(id="left-history-column"):
                yield Label(Text("History"), id=LEFT_HISTORY_TITLE)
                yield RepoModeHistoryList(id=LEFT_HISTORY_LIST_ID)
            with Vertical(id="right-history-column"):
                yield Label(Text("History"), id=RIGHT_HISTORY_TITLE)
                yield FileModeHistoryList(id=RIGHT_HISTORY_LIST_ID)
            with Vertical(id="right-file-column"):
                yield Label(Text("Files"), id=RIGHT_FILE_TITLE)
                # Hash header (external, outside the virtualized ListView)
                yield Label(Text("", style=STYLE_FILELIST_KEY), id="right-file-hash")
                # Key legend for repo-mode file column
                yield Label(Text(FILELIST_KEY_ROW_TEXT, style=STYLE_FILELIST_KEY), id="right-file-key")
                yield RepoModeFileList(id=RIGHT_FILE_LIST_ID)
            with Vertical(id="diff-column"):
                # Simple Diff title; detailed variant+color appears in the
                # first line of the diff output (prepDiffList builds it).
                yield Label(Text("Diff"), id=DIFF_TITLE)
                yield DiffList(id=DIFF_LIST_ID)
            with Vertical(id="help-column"):
                yield Label(Text("Help"), id=HELP_TITLE)
                yield HelpList(id=HELP_LIST_ID)
            with Vertical(id="open-column"):
                yield Label(Text("OpenFile"), id=OPEN_FILE_TITLE)
                yield OpenFileList(id=OPEN_FILE_LIST_ID)

        # Use a Label with id="footer" so `change_footer` can update it.
        # Placing it outside the `Horizontal` ensures it always sits below
        # the columns and remains visible regardless of layout changes.
        yield Label(Text(""), id="footer")

    async def on_mount(self) -> None:
        """
        Resolve widget references and perform initial preparatory actions.

        This should not perform repository discovery; `main()` handles that
        and passes `repo_root` into the app constructor.
        """
        try:
            # Repo discovery is handled by `main()` and passed into the app;
            # do not perform any repo scans here.
            # Resolve and store references to the six canonical widgets
            try:
                self.file_mode_file_list = self.query_one(f"#{LEFT_FILE_LIST_ID}", FileModeFileList)
                self.repo_mode_history_list = self.query_one(f"#{LEFT_HISTORY_LIST_ID}", RepoModeHistoryList)
                self.file_mode_history_list = self.query_one(f"#{RIGHT_HISTORY_LIST_ID}", FileModeHistoryList)
                self.repo_mode_file_list = self.query_one(f"#{RIGHT_FILE_LIST_ID}", RepoModeFileList)
                self.diff_list = self.query_one(f"#{DIFF_LIST_ID}", DiffList)
                self.help_list = self.query_one(f"#{HELP_LIST_ID}", HelpList)
                self.openfile_list = self.query_one(f"#{OPEN_FILE_LIST_ID}", OpenFileList)
            except Exception as e:
                self.printException(e)
                # composition must match expected ids
                raise RuntimeError(f"widget resolution failed in on_mount: {e}") from e

            # Ensure help content is prepared so help is immediately available
            self.help_list.prepHelp()

            # Ensure an overlay-root exists and cache it to avoid repeated
            # query_one calls which can raise NoMatches and spam logs.
            try:
                try:
                    self._overlay_root = self.query_one("#overlay-root")
                except Exception as e:
                    self.printException(e, "on_mount: overlay-root query failed")
                    # If overlay-root is not present in composition, create
                    # a transparent Static and mount it at the app/screen
                    # so floating overlays have a stable parent.
                    try:
                        from textual.widgets import Static

                        root = Static(id="overlay-root")
                        scr = getattr(self, "screen", None)
                        if scr is not None:
                            scr.mount(root)
                        else:
                            self.mount(root)
                        self._overlay_root = root
                    except Exception as _e:
                        self.printException(_e, "on_mount: creating overlay-root failed")
                        self._overlay_root = None
            except Exception as e:
                self.printException(e, "on_mount: overlay-root setup failed")

            # Apply any requested initial diff variant (from CLI or config)
            # args.diff is guaranteed to be a valid variant name by main()
            logger.debug(
                "on_mount: initial_diff_variant=%r, DIFF_VARIANT_NAMES=%r",
                self.initial_diff_variant,
                DIFF_VARIANT_NAMES,
            )
            self.diff_list.variant = DIFF_VARIANT_NAMES.index(self.initial_diff_variant)

            # Populate the canonical left lists and set focus so key handlers
            # and highlight behavior work immediately in both modes.
            try:
                if not self.repo_first:
                    # If the app was started with a specific file, ensure
                    # that file is preselected in the file-mode list so the
                    # UI highlights it on startup. `prepFileModeFileList`
                    # will honor `_preselected_filename` when rendering.
                    try:
                        if self.rel_file:
                            self.file_mode_file_list._preselected_filename = os.path.basename(self.rel_file)
                    except Exception as _e:
                        self.printException(_e, "on_mount: setting _preselected_filename failed")

                    self.file_mode_file_list.prepFileModeFileList(highlight=self.highlight)

                    # If a file was provided on the command line, open its
                    # history as if the user had navigated down to it and
                    # pressed Right. Otherwise show the left file list in
                    # fullscreen so users starting with no filename see the
                    # file listing immediately.
                    try:
                        if self.rel_file:
                            self.file_mode_file_list._activate_or_open(None, enter_dir_test_fn=lambda name: True)
                            # Show the file-history layout so the history list is
                            # visible when the app is started with a specific file.
                            self.change_state("file_history", f"#{RIGHT_HISTORY_LIST_ID}", RIGHT_HISTORY_FOOTER)
                        else:
                            self.change_state("file_fullscreen", f"#{LEFT_FILE_LIST_ID}", LEFT_FILE_FOOTER)
                    except Exception as _e:
                        self.printException(_e, "on_mount: opening initial file history failed")
                else:
                    # If starting in repo-first mode, pre-populate the left
                    # repository-history widget so the UI shows commits immediately.
                    try:
                        # Normalize CLI-provided repo hashes (first -> curr, second -> prev)
                        rh = self.repo_hashes or []
                        prev = None
                        curr = None
                        if rh:
                            curr = rh[0]
                            if len(rh) > 1:
                                prev = rh[1]
                        logger.debug(
                            "on_mount repo-first: repo_hashes=%r -> curr_hash=%r marked_prev_hash=%r",
                            rh,
                            curr,
                            prev,
                        )

                        # Call preparer once with any provided hashes so it may
                        # highlight/mark the requested commits during prep.
                        self.repo_mode_history_list.prepRepoModeHistoryList(prev_hash=prev, curr_hash=curr)
                        self.change_state("history_fullscreen", f"#{LEFT_HISTORY_LIST_ID}", LEFT_HISTORY_FOOTER)

                        # If in repo-first mode, check to see if there is a filename:
                        #     if so, also prep repo mode file list and pass the names as a highlight
                        #          and change  state to the file list
                        # .    else, there is no repo mode file list at this time
                        #           and change  state to the history list only
                        # if self.rel_file:
                        #    self.repo_mode_file_list.prepRepoModeFileList(prev, curr)
                        #    self.change_state("history_file", f"#{RIGHT_FILE_LIST_ID}", RIGHT_FILE_FOOTER)
                        # else:
                        #    self.change_state("history_fullscreen", f"#{LEFT_HISTORY_LIST_ID}", LEFT_HISTORY_FOOTER)

                    except Exception as e:
                        self.printException(e, "on_mount: repo-first initialization failed")

            except Exception as e:
                self.printException(e, "on_mount: initial prep failed")

            if not self.no_initial_popup:
                try:
                    self.push_screen(MessageModal(INITIAL_POPUP_TEXT))
                except Exception as e:
                    self.printException(e, "on_mount: push test modal failed")
        except Exception as e:
            self.printException(e, "on_mount failed")

    # -- Non-modal find overlay helpers ---------------------------------
    def show_find_overlay(self, initial_text: str, title: str, on_submit: Callable[[str], None]) -> None:
        """Show a non-modal, top-docked find input overlay.

        The overlay is mounted into the app DOM with id `find-overlay` and
        contains an Input with id `find-input`. Submission is handled by
        `on_input_submitted` which delegates to the provided `on_submit`.
        """
        try:
            self._find_overlay_callback = on_submit
            self._find_overlay_title = title

            # Reuse an existing overlay if present to avoid duplicate-id races
            # when multiple key handlers fire for the same keypress.
            existing_container = getattr(self, "_find_overlay_widget", None)
            if existing_container is None:
                scr = getattr(self, "screen", None)
                if scr is not None:
                    try:
                        existing_container = scr.query_one("#find-container", Horizontal)
                    except Exception:
                        existing_container = None
                if existing_container is None:
                    try:
                        existing_container = self.query_one("#find-container", Horizontal)
                    except Exception:
                        existing_container = None

            if existing_container is not None:
                try:
                    self._find_overlay_widget = existing_container
                    existing_container.display = True
                except Exception as e:
                    self.printException(e, "show_find_overlay: making existing container visible failed")

                try:
                    lbl = existing_container.query_one("#find-label", Label)
                    lbl.update(title)
                except Exception as e:
                    self.printException(e, "show_find_overlay: updating existing label failed")

                try:
                    inp = existing_container.query_one("#find-input", Input)
                    inp.value = initial_text or ""
                    self.call_later(lambda: inp.focus())
                except Exception as e:
                    self.printException(e, "show_find_overlay: focusing existing input failed")
                return

            # Create container with label and input
            label = Label(title, id="find-label")
            inp = Input(value=initial_text or "", id="find-input")
            container = Horizontal(label, inp, id="find-container")

            # Mount container to screen
            try:
                scr = getattr(self, "screen", None)
                if scr is not None:
                    try:
                        scr.mount(container)
                    except Exception as e:
                        self.printException(e, "show_find_overlay: screen.mount failed")
                        self.mount(container)
                else:
                    self.mount(container)

                # Cache the widget
                self._find_overlay_widget = container
            except Exception as e:
                self.printException(e, "show_find_overlay: mount/fallback failed")

            # Focus the input after mount (direct reference avoids query)
            try:
                self.call_later(lambda: inp.focus())
            except Exception as e:
                self.printException(e, "show_find_overlay: focus input failed")
        except Exception as e:
            self.printException(e, "show_find_overlay failed")

    def _set_find_overlay_label(self, text: str) -> None:
        """Update the visible find prompt label if the overlay exists."""
        try:
            scr = getattr(self, "screen", None)
            label = None
            if scr is not None:
                try:
                    label = scr.query_one("#find-label", Label)
                except Exception as e:
                    self.printException(e, "_set_find_overlay_label: query_one on screen failed")
                    label = None
            if label is None:
                try:
                    label = self.query_one("#find-label", Label)
                except Exception as e:
                    self.printException(e, "_set_find_overlay_label: query_one on self failed")
                    label = None
            if label is not None:
                label.update(text)
        except Exception as e:
            self.printException(e, "_set_find_overlay_label failed")

    def _submit_find_overlay(self, value: str) -> None:
        """Submit the current find value and keep overlay visible on no match."""
        try:
            cb = getattr(self, "_find_overlay_callback", None)
            if not cb:
                try:
                    self.hide_find_overlay()
                except Exception as e:
                    self.printException(e, "_submit_find_overlay: hide_find_overlay without callback failed")
                return

            matched = True
            try:
                result = cb(value)
                if result is False:
                    matched = False
            except Exception as e:
                self.printException(e, "_submit_find_overlay: callback failed")
                matched = False

            if matched:
                try:
                    self.hide_find_overlay()
                except Exception as e:
                    self.printException(e, "_submit_find_overlay: hide_find_overlay on success failed")
            else:
                base = getattr(self, "_find_overlay_title", "Find (forward)")
                self._set_find_overlay_label(f"{base} - not found. ESC to return")
        except Exception as e:
            self.printException(e, "_submit_find_overlay failed")

    def hide_find_overlay(self) -> None:
        try:
            # If we cached the mounted overlay widget, remove it directly
            try:
                cached = getattr(self, "_find_overlay_widget", None)
                if cached is not None:
                    try:
                        cached.remove()
                    except Exception as e:
                        self.printException(e, "hide_find_overlay: removing cached overlay failed")
                    finally:
                        self._find_overlay_widget = None
                    self._find_overlay_callback = None
                    self._find_overlay_title = None
                    return
            except Exception as e:
                self.printException(e, "hide_find_overlay: clearing cached overlay failed")

            # Fallback: try removing by selector from screen/app
            scr = getattr(self, "screen", None)
            if scr is not None:
                try:
                    existing = scr.query_one("#find-container")
                    existing.remove()
                except Exception as e:
                    self.printException(e, "hide_find_overlay: removing overlay from screen failed")
            else:
                try:
                    existing = self.query_one("#find-container")
                    existing.remove()
                except Exception as e:
                    self.printException(e, "hide_find_overlay: removing overlay from self failed")
            self._find_overlay_callback = None
            self._find_overlay_title = None
        except Exception as e:
            self.printException(e, "hide_find_overlay failed")

    def on_input_submitted(self, message: Input.Submitted) -> None:
        """Handle submission from the non-modal find input."""
        try:
            try:
                inp = message.input
                if getattr(inp, "id", None) != "find-input":
                    return
                val = message.value
            except Exception as e:
                self.printException(e, "on_input_submitted: reading find input failed")
                val = ""

            self._submit_find_overlay(val)
        except Exception as e:
            self.printException(e, "on_input_submitted failed")

    def on_key(self, event: events.Key) -> None:
        """When MessageModal is active, dismiss it on any key and stop propagation."""
        try:
            logger.debug(
                "GitDiffNavTool.on_key: key=%r character=%r",
                getattr(event, "key", None),
                getattr(event, "character", None),
            )
            try:
                current_screen = self.screen
            except Exception as e:
                self.printException(e, "on_key: reading current screen failed")
                current_screen = None
            # If the non-modal find overlay is visible, handle Enter/Escape here
            try:
                scr = getattr(self, "screen", None)
                # Prefer checking the cached overlay widget to avoid noisy
                # query_one exceptions. Fall back to a lightweight screen
                # query only if no cached widget is present.
                overlay_present = getattr(self, "_find_overlay_widget", None) is not None
                if not overlay_present:
                    try:
                        if scr is not None:
                            try:
                                scr.query_one("#find-container")
                                overlay_present = True
                            except NoMatches as _no_logging:
                                # Expected when overlay not present
                                overlay_present = False
                            except Exception as e:
                                self.printException(e, "on_key: querying find overlay failed")
                                overlay_present = False
                        else:
                            try:
                                self.query_one("#find-container")
                                overlay_present = True
                            except NoMatches as _no_logging:
                                # Expected when overlay not present
                                overlay_present = False
                            except Exception as e:
                                self.printException(e, "on_key: querying find overlay failed")
                                overlay_present = False
                    except Exception as e:
                        self.printException(e, "on_key: querying find overlay failed")
                        overlay_present = False
                if overlay_present:
                    key = getattr(event, "key", "")
                    ch = getattr(event, "character", None)
                    # Enter/Return: submit
                    if key in ("enter",) or ch == "\r":
                        try:
                            event.stop()
                        except Exception as e:
                            self.printException(e, "on_key: event.stop failed for find submit")
                        try:
                            inp = None
                            try:
                                inp = scr.query_one("#find-input", Input)
                            except Exception as e:
                                self.printException(e, "on_key: querying find input failed via scr")
                                try:
                                    inp = self.query_one("#find-input", Input)
                                except Exception as e:
                                    self.printException(e, "on_key: querying find input failed via self")
                                    inp = None
                            val = inp.value if (inp is not None and hasattr(inp, "value")) else ""
                            self._submit_find_overlay(val)
                        except Exception as e:
                            self.printException(e, "on_key: handling find submit failed")
                        return
                    # Escape: cancel
                    if key == "escape":
                        try:
                            event.stop()
                        except Exception as e:
                            self.printException(e, "on_key: event.stop failed for find cancel")
                        try:
                            self.hide_find_overlay()
                        except Exception as e:
                            self.printException(e, "on_key: hide_find_overlay failed for escape")
                        return

            except Exception as e:
                self.printException(e, "on_key: overlay handling failed")

            if isinstance(current_screen, MessageModal):
                logger.debug("GitDiffNavTool.on_key: MessageModal active, dismissing")
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "on_key: event.stop failed while modal active")
                try:
                    self.pop_screen()
                except Exception as e:
                    self.printException(e, "on_key: pop_screen failed while modal active")
        except Exception as e:
            self.printException(e, "GitDiffNavTool.on_key failed")

    def key_q(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """Quit the application on `q` keypress (synonym for ^Q)."""
        if not recursive:
            logger.debug("GitDiffNavTool.key_q called: key=%r", getattr(event, "key", None))
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_q: event.stop failed")
            logger.debug("key_q invoked; exiting app")
            try:
                # App.exit() is the Textual API to stop the app.
                self.exit()
            except Exception as e:
                self.printException(e, "key_q: app.exit failed")
                # Fallback to raising SystemExit
                raise SystemExit(0)
        except Exception as e:
            self.printException(e, "key_q failed")

    def key_Q(self, event: events.Key | None = None) -> None:
        """Uppercase Q also quits."""
        logger.debug("GitDiffNavTool.key_Q called: key=%r", getattr(event, "key", None))
        return self.key_q(event, recursive=True)

    def key_h(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """
        Show help: save state, prepare help, then display help fullscreen.

        This records the single-slot state, ensures help content is prepared,
        and switches layout/focus/footer to the help configuration.
        """
        if not recursive:
            logger.debug("GitDiffNavTool.key_h called: key=%r", getattr(event, "key", None))
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_h: event.stop failed")
            logger.debug("key_h invoked: saving state and showing help")
            self.save_state()
            # Help text is static and prepopulated during on_mount; no need
            # to call prepHelp() again here.

            self.change_state("help_fullscreen", f"#{HELP_LIST_ID}", HELP_FOOTER)
        except Exception as e:
            self.printException(e, "key_h outer failure")

    def key_H(self, event: events.Key | None = None) -> None:
        """Alias for `key_h` (uppercase H)."""
        logger.debug("GitDiffNavTool.key_H called: key=%r", getattr(event, "key", None))
        return self.key_h(event, recursive=True)

    def key_question(self, event: events.Key | None = None) -> None:
        """Handle terminal mappings where '?' is reported as 'question' by delegating to help."""
        logger.debug("GitDiffNavTool.key_question called: key=%r", getattr(event, "key", None))
        return self.key_h(event, recursive=True)

    def key_question_mark(self, event: events.Key | None = None) -> None:
        """Handle terminal mappings where '?' is reported as 'question_mark'."""
        logger.debug("GitDiffNavTool.key_question_mark called: key=%r", getattr(event, "key", None))
        return self.key_h(event, recursive=True)

    def key_r(self, event: events.Key | None = None, recursive: bool = False) -> None:
        """
        Global refresh: reset GitRepo cache and refresh the active layout.

        This keeps refresh behavior simple by avoiding path/hash recomputation
        in this handler and delegating state handling to each preparer.
        """
        if not recursive:
            logger.debug("GitDiffNavTool.key_r called: key=%r", getattr(event, "key", None))
        try:
            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "key_r: event.stop failed")
            try:
                self.gitRepo.reset_cache()
            except Exception as e:
                self.printException(e, "key_r: gitRepo.reset_cache failed")

            layout = self._current_layout
            current_rel_path = (
                os.path.join(self.rel_dir or "", self.rel_file) if self.rel_file else (self.rel_dir or ".")
            )

            def _refresh_diff() -> None:
                filename = os.path.join(self.rel_dir or "", self.rel_file) if self.rel_file else (self.rel_dir or "")
                prev = self.previous_hash if self.previous_hash is not None else GitRepo.NEWREPO
                self.diff_list.prepDiffList(
                    filename,
                    prev,
                    self.current_hash,
                    self.diff_list.variant,
                    self.diff_list.go_back,
                )

            if layout == "file_fullscreen":
                self.file_mode_file_list.prepFileModeFileList(highlight=self.highlight)

            elif layout == "history_fullscreen":
                self.repo_mode_history_list.prepRepoModeHistoryList(
                    prev_hash=self.previous_hash,
                    curr_hash=self.current_hash,
                )

            elif layout == "file_history":
                self.file_mode_file_list.prepFileModeFileList(highlight=self.highlight)
                self.file_mode_history_list.prepFileModeHistoryList(
                    path=current_rel_path,
                    prev_hash=self.previous_hash,
                    curr_hash=self.current_hash,
                )

            elif layout == "history_file":
                self.repo_mode_history_list.prepRepoModeHistoryList(
                    prev_hash=self.previous_hash,
                    curr_hash=self.current_hash,
                )
                self.repo_mode_file_list.prepRepoModeFileList(self.previous_hash, self.current_hash)

            elif layout == "file_history_diff":
                self.file_mode_file_list.prepFileModeFileList(highlight=self.highlight)
                self.file_mode_history_list.prepFileModeHistoryList(
                    path=current_rel_path,
                    prev_hash=self.previous_hash,
                    curr_hash=self.current_hash,
                )
                _refresh_diff()

            elif layout == "history_file_diff":
                self.repo_mode_history_list.prepRepoModeHistoryList(
                    prev_hash=self.previous_hash,
                    curr_hash=self.current_hash,
                )
                self.repo_mode_file_list.prepRepoModeFileList(self.previous_hash, self.current_hash)
                _refresh_diff()

            elif layout == "diff_fullscreen":
                _refresh_diff()

            elif layout == "help_fullscreen":
                self.help_list.prepHelp()

            else:
                logger.debug("key_r: unknown layout %r; refreshing active file list as fallback", layout)
                self.file_mode_file_list.prepFileModeFileList(highlight=self.highlight)

        except Exception as e:
            self.printException(e, "key_r failed")

    def key_R(self, event: events.Key | None = None) -> None:
        """Alias for `key_r` (Shift-R)."""
        logger.debug("GitDiffNavTool.key_R called: key=%r", getattr(event, "key", None))
        return self.key_r(event, recursive=True)

    def _apply_column_layout(
        self,
        left_file_w: int,
        left_history_w: int,
        right_history_w: int,
        right_file_w: int,
        diff_w: int,
        help_w: int,
        open_file_w: int,
    ) -> None:
        """
        Set column widths and visibility for the seven canonical columns.

        If a width is zero the column is hidden (styles.display set to "none").
        Otherwise `styles.width` is set to "{width}%" and `styles.display` is cleared.
        """
        show = None
        hide = "none"
        try:
            # set container widths
            try:
                c = self.query_one("#left-file-column")
                c.styles.width = f"{left_file_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set left-file-column")
            try:
                c = self.query_one("#left-history-column")
                c.styles.width = f"{left_history_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set left-history-column")
            try:
                c = self.query_one("#right-history-column")
                c.styles.width = f"{right_history_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set right-history-column")
            try:
                c = self.query_one("#right-file-column")
                c.styles.width = f"{right_file_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set right-file-column")
            try:
                c = self.query_one("#diff-column")
                c.styles.width = f"{diff_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set diff-column")
            try:
                c = self.query_one("#help-column")
                c.styles.width = f"{help_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set help-column")
            try:
                c = self.query_one("#open-column")
                c.styles.width = f"{open_file_w}%"
                c.styles.flex = 0
            except Exception as e:
                self.printException(e, "could not set open-column")

            # set widget visibility based on widths
            try:
                self.file_mode_file_list.styles.display = show if left_file_w else hide
            except Exception as e:
                self.printException(e, "could not set left-file-list display")
            try:
                self.repo_mode_history_list.styles.display = show if left_history_w else hide
            except Exception as e:
                self.printException(e, "could not set left-history-list display")
            try:
                self.file_mode_history_list.styles.display = show if right_history_w else hide
            except Exception as e:
                self.printException(e, "could not set right-history-list display")
            try:
                self.repo_mode_file_list.styles.display = show if right_file_w else hide
            except Exception as e:
                self.printException(e, "could not set right-file-list display")
            try:
                self.diff_list.styles.display = show if diff_w else hide
            except Exception as e:
                self.printException(e, "could not set diff-list display")
            try:
                self.help_list.styles.display = show if help_w else hide
            except Exception as e:
                self.printException(e, "could not set help-list display")
            try:
                self.openfile_list.styles.display = show if open_file_w else hide
            except Exception as e:
                self.printException(e, "could not set openfile-list display")
        except Exception as e:
            self.printException(e, "error applying column layout")

    def build_diff_cmd(self, filename: str, prev: str, curr: str, variant_index: int = 0) -> list[str]:
        """
        Delegate to the app-level `GitRepo` to construct a git diff argv list.

        Keeping `git` command construction inside `GitRepo` ensures all
        direct `git` invocations remain within that class.
        """
        return self.gitRepo.build_diff_cmd(filename, prev, curr, variant_index)

    def change_layout(self, newlayout: str) -> None:
        """Change column layout using a named layout."""
        try:
            logger.debug(f"change_layout: newlayout={newlayout}")
            if newlayout == "file_fullscreen":
                self._apply_column_layout(100, 0, 0, 0, 0, 0, 0)
            elif newlayout == "history_fullscreen":
                self._apply_column_layout(0, 100, 0, 0, 0, 0, 0)
            elif newlayout == "file_history":
                self._apply_column_layout(15, 0, 85, 0, 0, 0, 0)
            elif newlayout == "history_file":
                self._apply_column_layout(0, 15, 0, 85, 0, 0, 0)
            elif newlayout == "file_history_diff":
                self._apply_column_layout(5, 0, 20, 0, 75, 0, 0)
            elif newlayout == "history_file_diff":
                self._apply_column_layout(0, 5, 0, 20, 75, 0, 0)
            elif newlayout == "file_history_open":
                self._apply_column_layout(5, 0, 20, 0, 0, 0, 75)
            elif newlayout == "history_file_open":
                self._apply_column_layout(0, 5, 0, 20, 0, 0, 75)
            elif newlayout == "open_file_fullscreen":
                self._apply_column_layout(0, 0, 0, 0, 0, 0, 100)
                self.change_footer(OPEN_FILE_FOOTER_2)
            elif newlayout == "diff_fullscreen":
                self._apply_column_layout(0, 0, 0, 0, 100, 0, 0)
                self.change_footer(DIFF_FOOTER_2)
            elif newlayout == "help_fullscreen":
                self._apply_column_layout(0, 0, 0, 0, 0, 100, 0)
            else:
                raise ValueError(f"unknown layout: {newlayout}")
            try:
                self._current_layout = newlayout
            except Exception as e:
                self.printException(e, "setting _current_layout in change_layout")
        except Exception as e:
            self.printException(e, f"change_layout {newlayout}")

    def change_state(
        self, layout: Optional[str] = None, focus: Optional[str] = None, footer: Optional[Text | str] = None
    ) -> None:
        """
        Change to the provided layout/focus/footer immediately.

        This applies the requested layout, focus, and footer using existing
        helpers and records the current values for save/restore semantics.
        """
        try:
            logger.debug(f"change_state(layout={layout}, focus={focus}, footer={footer}) - applying requested changes")
            logger.debug("change_state: focus raw=%r type=%s", focus, type(focus))

            if layout is not None:
                self.change_layout(layout)
            if focus is not None:
                self.change_focus(focus)
            if footer is not None:
                self.change_footer(footer)

            # change_layout/change_focus/change_footer are responsible for
            # recording their own current values; do not duplicate here.

        except Exception as e:
            self.printException(e, "change_state outer failure")

    def save_state(self) -> None:
        """
        Save the current single-value state (layout, focus, footer).

        This is a single-slot save; calling multiple times overwrites the slot.
        """
        try:
            self._saved_state = (
                self._current_layout,
                self._current_focus,
                self._current_footer,
            )
            logger.debug(f"save_state: saved={self._saved_state}")
        except Exception as e:
            self.printException(e, "save_state failed")

    def restore_state(self) -> None:
        """
        Restore the state saved by `save_state`.

        Raises RuntimeError if no saved state exists.
        """
        try:
            s = self._saved_state
            if s is None:
                raise RuntimeError("restore_state called without a prior save_state")

            layout, focus, footer = s

            logger.debug(f"restore_state: restoring layout={layout} focus={focus} footer={footer}")
            self.change_state(layout, focus, footer)

            # clear saved slot after restore
            try:
                self._saved_state = None
            except Exception as e:
                self.printException(e, "restore_state clearing saved state failed")
        except Exception as e:
            self.printException(e, "restore_state failed")

    def change_focus(self, target: str) -> None:
        """
        Change focus to the given widget id (safely).

        Records the desired focus id for save/restore semantics.
        """
        try:

            def _do():
                sel = str(target)
                # normalize selector to a bare id (without leading '#')
                if sel.startswith("#"):
                    key = sel[1:]
                    logger.debug("change_focus: stripped leading '#' -> %r", key)
                else:
                    key = sel

                widget = None
                label_name = None

                # Reset title label classes
                title_ids = [
                    LEFT_FILE_TITLE,
                    LEFT_HISTORY_TITLE,
                    RIGHT_HISTORY_TITLE,
                    RIGHT_FILE_TITLE,
                    DIFF_TITLE,
                    HELP_TITLE,
                    OPEN_FILE_TITLE,
                ]
                for tid in title_ids:
                    try:
                        lbl = self.query_one(f"#{tid}", Label)
                        try:
                            lbl.set_class(False, "active")
                        except Exception as e:
                            self.printException(e, "change_focus resetting title label class failed")
                            try:
                                lbl.remove_class("active")
                            except Exception as e:
                                self.printException(e, "change_focus removing title label class failed")
                    except Exception as e:
                        self.printException(e, "change_focus querying title label failed")

                if key == LEFT_FILE_LIST_ID:
                    widget = self.file_mode_file_list
                    label_name = LEFT_FILE_TITLE
                elif key == LEFT_HISTORY_LIST_ID:
                    widget = self.repo_mode_history_list
                    label_name = LEFT_HISTORY_TITLE
                elif key == RIGHT_FILE_LIST_ID:
                    widget = self.repo_mode_file_list
                    label_name = RIGHT_FILE_TITLE
                elif key == RIGHT_HISTORY_LIST_ID:
                    widget = self.file_mode_history_list
                    label_name = RIGHT_HISTORY_TITLE
                elif key == DIFF_LIST_ID:
                    widget = self.diff_list
                    label_name = DIFF_TITLE
                elif key == HELP_LIST_ID:
                    widget = self.help_list
                    label_name = HELP_TITLE
                elif key == OPEN_FILE_LIST_ID:
                    widget = self.openfile_list
                    label_name = OPEN_FILE_TITLE

                else:
                    try:
                        caller = inspect.stack()[1]
                        caller_info = f"{caller.filename}:{caller.lineno} in {caller.function}()"
                    except Exception as _e:
                        self.printException(_e)
                        caller_info = "<caller-info-unavailable>"
                    logger.warning(
                        "change_focus:%d: unknown canonical focus target raw=%r normalized=%r caller=%s",
                        inspect.currentframe().f_lineno,
                        target,
                        key,
                        caller_info,
                    )
                    return

                try:
                    # If there is an existing focused column, set its border to gray
                    # Force all canonical candidate widgets to gray borders,
                    # then we'll set the chosen widget to white below.
                    try:
                        candidates = [
                            ("left_file_list", self.file_mode_file_list),
                            ("left_history_list", self.repo_mode_history_list),
                            ("right_file_list", self.repo_mode_file_list),
                            ("right_history_list", self.file_mode_history_list),
                            ("diff_list", self.diff_list),
                            ("help_list", self.help_list),
                            ("open_file_list", self.openfile_list),
                        ]
                        for cname, w in candidates:
                            if w is None:
                                continue
                            try:
                                before = getattr(w.styles, "border", None)
                            except Exception as _ex:
                                self.printException(_ex)
                                before = "<unavailable>"
                            logger.debug(
                                "change_focus:%d: forcing gray border for %s (before=%r)",
                                inspect.currentframe().f_lineno,
                                cname,
                                before,
                            )
                            try:
                                # Prefer applying a CSS class to represent
                                # unfocused/gray state so rendering decisions
                                # stay declarative. Fall back to directly
                                # mutating `styles.border` when the widget
                                # doesn't support `set_class`.
                                w.set_class(False, "focused")
                                w.set_class(False, "focused-white")
                                w.set_class(True, "focused-gray")
                            except Exception as e:
                                self.printException(e)
                                try:
                                    w.styles.border = ("solid", "gray")
                                except Exception as _ex:
                                    self.printException(_ex)
                            try:
                                readback = getattr(w.styles, "border", None)
                            except Exception as _ex:
                                self.printException(_ex)
                                readback = "<unavailable>"
                            logger.debug(
                                "change_focus:%d: forced gray border readback=%r for %s",
                                inspect.currentframe().f_lineno,
                                readback,
                                cname,
                            )
                    except Exception as _ex:
                        self.printException(_ex)

                    logger.debug(
                        "change_focus:%d: calling set_focus on widget=%r key=%r",
                        inspect.currentframe().f_lineno,
                        type(widget).__name__ if widget is not None else None,
                        key,
                    )
                    try:
                        self.set_focus(widget)
                    except Exception as e:
                        self.printException(e, f"could not set focus to widget for {target}")
                        # Fallback: resolve widget by id and call set_focus
                        logger.debug(
                            "change_focus:%d: attempting widget.focus() fallback for key=%r",
                            inspect.currentframe().f_lineno,
                            key,
                        )
                        try:
                            widget.focus()
                        except Exception as e:
                            self.printException(e, f"could not fallback focus to widget for {target}")

                    # Now set the new focused widget's border to white
                    try:
                        # read current focused widget border safely
                        try:
                            cur_border = getattr(widget.styles, "border", None)
                        except Exception as _ex:
                            self.printException(_ex)
                            cur_border = "<unavailable>"
                        logger.debug(
                            "change_focus:%d: focused widget before set border=%r key=%r",
                            inspect.currentframe().f_lineno,
                            cur_border,
                            key,
                        )
                        try:
                            widget.set_class(True, "focused")
                            widget.set_class(False, "focused-gray")
                            widget.set_class(True, "focused-white")
                        except Exception as e:
                            self.printException(e)
                            try:
                                widget.styles.border = ("solid", "white")
                            except Exception as _ex:
                                self.printException(_ex)
                        try:
                            readback = getattr(widget.styles, "border", None)
                        except Exception as _ex:
                            self.printException(_ex)
                            readback = "<unavailable>"
                        logger.debug(
                            "change_focus:%d: focused widget.styles.border readback=%r key=%r",
                            inspect.currentframe().f_lineno,
                            readback,
                            key,
                        )
                    except Exception as _ex:
                        self.printException(_ex)

                    # Best-effort: some terminals and render paths can race
                    # focus changes and style application. Try to apply the
                    # index highlight synchronously first to avoid visible
                    # delay; fall back to a post-refresh scheduled call if
                    # the immediate attempt doesn't take effect.
                    try:
                        logger.debug(
                            "change_focus:%d: attempting immediate apply_index_change for key=%r",
                            inspect.currentframe().f_lineno,
                            key,
                        )
                        applied = None
                        try:
                            # Prefer an explicit target index when possible. Some
                            # widgets initialize with `index=None` until they are
                            # populated; pass `_min_index` (or 0) to ensure an
                            # immediate activation attempt actually targets a
                            # concrete row instead of becoming a no-op.
                            t_im_start = time.perf_counter()
                            target_idx = getattr(widget, "index", None)
                            if target_idx is None:
                                target_idx = getattr(widget, "_min_index", 0) or 0
                            applied = widget.apply_index_change(None, target_idx)
                            t_im_end = time.perf_counter()
                            logger.debug(
                                "change_focus:%d: immediate apply_index_change took %.3fms returned=%r target_idx=%r",
                                inspect.currentframe().f_lineno,
                                (t_im_end - t_im_start) * 1000,
                                bool(applied),
                                target_idx,
                            )
                        except Exception as _ex:
                            self.printException(_ex, "change_focus: immediate apply_index_change failed")
                            applied = None
                        # If immediate application returned None or seemed ineffective,
                        # schedule a post-refresh fallback to ensure the UI eventually
                        # receives the active-class update.
                        if applied is None:
                            try:
                                # Try to authoritative-apply the active class
                                # synchronously in-case rendering paths race and
                                # the framework doesn't pick up the immediate
                                # `apply_index_change` side-effects. This mirrors
                                # the fallback behavior but runs inline to avoid
                                # the visible delay before `call_after_refresh`.
                                widget_idx = getattr(widget, "index", None)
                                nodes_now = widget.nodes()

                                if nodes_now and (widget_idx is not None and 0 <= widget_idx < len(nodes_now)):
                                    try:
                                        logger.debug(
                                            "change_focus:%d: authoritative immediate activation for key=%r idx=%r",
                                            inspect.currentframe().f_lineno,
                                            key,
                                            widget_idx,
                                        )
                                        for i, node in enumerate(nodes_now):
                                            try:
                                                node.set_class(i == widget_idx, "active")
                                            except Exception as _ex:
                                                self.printException(
                                                    _ex,
                                                    "change_focus: node.set_class failed during authoritative activation",
                                                )
                                            try:
                                                node.refresh()
                                            except Exception as _ex:
                                                self.printException(
                                                    _ex,
                                                    "change_focus: node.refresh failed during authoritative activation",
                                                )
                                        try:
                                            if hasattr(widget, "_ensure_index_visible"):
                                                widget._ensure_index_visible()
                                        except Exception as _ex:
                                            self.printException(
                                                _ex,
                                                "change_focus: _ensure_index_visible failed during authoritative activation",
                                            )
                                        # We applied the authoritative activation inline; no
                                        # need to schedule the post-refresh fallback for
                                        # most widget types. Still preserve the historical
                                        # skip for FileModeHistoryList.
                                        widget_name = type(widget).__name__ if widget is not None else "<unknown>"
                                        if widget_name == "FileModeHistoryList":
                                            logger.debug(
                                                "change_focus:%d: skipping scheduled fallback for %s",
                                                inspect.currentframe().f_lineno,
                                                widget_name,
                                            )
                                        else:
                                            logger.debug(
                                                "change_focus:%d: authoritative activation applied for key=%r; scheduling a no-op post-refresh for safety",
                                                inspect.currentframe().f_lineno,
                                                key,
                                            )
                                            try:
                                                # Schedule a lightweight no-op to allow any
                                                # remaining render pipeline steps to complete.
                                                t_sched_start = time.perf_counter()
                                                self.call_after_refresh(lambda: None)
                                                t_sched_end = time.perf_counter()
                                                logger.debug(
                                                    "change_focus:%d: call_after_refresh no-op scheduled in %.3fms",
                                                    inspect.currentframe().f_lineno,
                                                    (t_sched_end - t_sched_start) * 1000,
                                                )
                                            except Exception as _ex:
                                                self.printException(
                                                    _ex,
                                                    "change_focus: scheduling post-refresh no-op failed",
                                                )
                                    except Exception as _ex:
                                        self.printException(
                                            _ex, "change_focus: authoritative immediate activation failed"
                                        )
                                else:
                                    # If we couldn't find nodes to directly update,
                                    # fall back to the prior behavior and schedule the
                                    # post-refresh `apply_index_change` to complete
                                    # the activation when the UI finishes rendering.
                                    try:
                                        widget_name = type(widget).__name__ if widget is not None else "<unknown>"
                                        if widget_name == "FileModeHistoryList":
                                            logger.debug(
                                                "change_focus:%d: skipping scheduled fallback for %s",
                                                inspect.currentframe().f_lineno,
                                                widget_name,
                                            )
                                        else:
                                            logger.debug(
                                                "change_focus:%d: scheduling post-refresh apply_index_change for key=%r",
                                                inspect.currentframe().f_lineno,
                                                key,
                                            )
                                            try:
                                                t_sched_start = time.perf_counter()
                                                target_idx_sched = getattr(widget, "index", None)
                                                if target_idx_sched is None:
                                                    target_idx_sched = getattr(widget, "_min_index", 0) or 0
                                                self.call_after_refresh(
                                                    lambda: widget.apply_index_change(None, target_idx_sched)
                                                )
                                                t_sched_end = time.perf_counter()
                                                logger.debug(
                                                    "change_focus:%d: call_after_refresh scheduled in %.3fms target_idx=%r",
                                                    inspect.currentframe().f_lineno,
                                                    (t_sched_end - t_sched_start) * 1000,
                                                    target_idx_sched,
                                                )
                                            except Exception as _ex:
                                                self.printException(
                                                    _ex,
                                                    "change_focus: scheduling post-refresh apply_index_change failed",
                                                )
                                    except Exception as _ex:
                                        self.printException(
                                            _ex,
                                            "change_focus: scheduling post-refresh apply_index_change failed",
                                        )
                            except Exception as _ex:
                                self.printException(
                                    _ex, "change_focus: scheduling post-refresh apply_index_change failed"
                                )

                    except Exception as _ex:
                        self.printException(_ex, f"change_focus: applying focus styles failed for {target}")
                        # Attempt to resolve by id and set border
                        try:
                            w = None
                            try:
                                w = self.query_one(f"#{key}")
                            except Exception as _ex:
                                self.printException(_ex)
                                w = None
                            if w is not None:
                                logger.debug(
                                    "change_focus:%d: setting fallback widget class -> focused for resolved id=%r",
                                    inspect.currentframe().f_lineno,
                                    key,
                                )
                                try:
                                    w.set_class(True, "focused")
                                except Exception as e:
                                    self.printException(e)
                                    try:
                                        w.styles.border = ("solid", "white")
                                    except Exception as _ex:
                                        self.printException(_ex)
                        except Exception as _ex:
                            self.printException(_ex)

                    # best-effort normalize index/scroll for file lists
                    try:
                        if hasattr(widget, "index"):
                            idx = getattr(widget, "index", None)
                            if idx is None:
                                widget.index = getattr(widget, "_min_index", 0) or 0
                    except Exception as e:
                        self.printException(e, f"could not normalize index/scroll for widget {target}")

                except Exception as e:
                    self.printException(e, f"could not focus resolved widget for {target}")

                # Update title label
                try:
                    if label_name:
                        try:
                            title_lbl = self.query_one(f"#{label_name}", Label)
                            try:
                                title_lbl.set_class(True, "active")
                            except Exception as e:
                                self.printException(e, "change_focus setting title label class failed")
                                try:
                                    title_lbl.add_class("active")
                                except Exception as e:
                                    self.printException(e, "change_focus adding title label class failed")
                        except Exception as e:
                            self.printException(e, f"could not update title label {label_name}")
                except Exception as e:
                    self.printException(e, "change_focus: updating title label failed")

            try:
                self.call_after_refresh(_do)
            except Exception as e:
                self.printException(e, "change_focus.call_after_refresh failed")
                _do()

            # record desired focus target for save/restore
            try:
                sel = str(target)
                if sel.startswith("#"):
                    key = sel
                else:
                    key = f"#{sel}"
                self._current_focus = key
            except Exception as e:
                self.printException(e, "change_focus recording _current_focus failed")
        except Exception as e:
            self.printException(e, "change_focus outer failure")

    def change_footer(self, value: Text | str) -> None:
        """Set the footer to `value` (Text or str) immediately and record it."""
        try:
            txt = value if isinstance(value, Text) else Text(str(value))
            try:
                footer = None
                try:
                    footer = self.query_one("#footer", Label)
                except Exception as e:
                    self.printException(e, "change_footer querying footer label failed")
                    footer = None
                if footer is not None:
                    try:
                        footer.update(txt)
                    except Exception as e:
                        self.printException(e, "change_footer updating footer label failed")
            except Exception as e:
                self.printException(e, "could not update footer in change_footer")
            try:
                self._current_footer = txt
            except Exception as e:
                self.printException(e, "change_footer recording _current_footer failed")
        except Exception as e:
            self.printException(e, "change_footer outer failure")

    # Layout toggle helpers -------------------------------------------------
    def toggle(self, layout: str, event: events.Key | None = None) -> None:
        """
        Dispatch to a per-layout toggle_* handler for `layout`.

        If the layout is `help_fullscreen` this is a no-op. Otherwise stop
        the event (if provided) and call the corresponding `toggle_<layout>`
        method if it exists.
        """
        try:
            if layout == "help_fullscreen":
                return

            # Log app and focused-widget state to help debug path/hash swapping
            try:
                logger.trace(
                    "toggle_%s invoked: _current_layout=%s _current_focus=%s",
                    layout,
                    self._current_layout,
                    self._current_focus,
                )
                logger.trace(
                    "app state: rel_dir=<%r> rel_file=<%r> current_hash=<%r> previous_hash=<%r>",
                    self.rel_dir,
                    self.rel_file,
                    self.current_hash,
                    self.previous_hash,
                )

                focused_info = None
                try:
                    fsel = self._current_focus
                    if fsel:
                        fid = fsel[1:] if str(fsel).startswith("#") else str(fsel)
                        try:
                            widget = self.query_one(f"#{fid}")
                        except Exception as e:
                            self.printException(e, f"toggle: querying focused widget #{fid} failed")
                            widget = None
                        if widget is not None:
                            wtype = type(widget).__name__
                            wpath = getattr(widget, "path", None)
                            # If history list, compute selected commit pair
                            pair = None
                            try:
                                if isinstance(widget, HistoryListBase):
                                    pair = widget.compute_commit_pair_hashes()
                            except Exception as e:
                                self.printException(e, "toggle: computing commit pair hashes failed")
                                pair = None
                            # If file list, try to get selected node raw text
                            selected_raw = None
                            try:
                                nodes = widget.nodes()
                                idx = widget.index or getattr(widget, "_min_index", 0) or 0
                                if 0 <= idx < len(nodes):
                                    selected_raw = getattr(nodes[idx], "_raw_text", None)
                            except Exception as e:
                                self.printException(e, "toggle: getting selected node raw text failed")
                                selected_raw = None
                            focused_info = (wtype, wpath, pair, selected_raw)
                except Exception as e:
                    self.printException(e, "toggle: getting focused widget info failed")
                    focused_info = None
                logger.debug("focused widget info: %r", focused_info)
            except Exception as e:
                self.printException(e, "toggle outer failure")

            if event is not None:
                try:
                    event.stop()
                except Exception as e:
                    self.printException(e, "toggle: event.stop failed")
            handler = getattr(self, f"toggle_{layout}", None)
            if callable(handler):
                try:
                    handler()
                except Exception as e:
                    self.printException(e, f"toggle_{layout} failed")
            else:
                logger.debug("toggle: no handler for layout %s", layout)
        except Exception as e:
            self.printException(e, "toggle outer failure")

    def key_t(self, event: events.Key | None = None) -> None:
        """Swap (Toggle) the paired layout for the current layout (invoked by 't')."""
        logger.debug("GitDiffNavTool.key_t called: key=%r", getattr(event, "key", None))
        return self.toggle(self._current_layout, event)

    def key_T(self, event: events.Key | None = None) -> None:
        """Alias for `key_t` (Shift-T)."""
        logger.debug("GitDiffNavTool.key_T called: key=%r", getattr(event, "key", None))
        return self.key_t(event, recursive=True)

    # Per-layout toggle implementations. These prepare lists and switch
    # layouts in pairs so the `t` key toggles between related views.
    def toggle_file_fullscreen(self) -> None:
        """
        Toggle between file fullscreen and the paired history fullscreen view.

        Prepares the paired view content so the transition feels immediate.
        """
        # When toggling from file_fullscreen, populate the repo history
        # so the paired history_fullscreen view is ready.
        self.repo_mode_history_list.prepRepoModeHistoryList()
        self.change_state("history_fullscreen", f"#{LEFT_HISTORY_LIST_ID}", LEFT_HISTORY_FOOTER)

    def toggle_history_fullscreen(self) -> None:
        """
        Toggle between history fullscreen and the paired file fullscreen view.

        Prepares the file list and sets focus/footers appropriately.
        """
        try:
            # Determine a highlight filename: prefer rel_file when present.
            # Use empty string to indicate no highlight rather than None.
            hl = self.rel_file or ""
            gitrepo = self.gitRepo
            init_path = os.path.join(gitrepo.get_repo_root(), self.rel_dir) if self.rel_dir else gitrepo.get_repo_root()
            try:
                root = gitrepo.get_repo_root()
                ip = init_path
                if ip == root:
                    rel = ""
                elif ip.startswith(root + os.sep):
                    rel = ip[len(root) + 1 :]
                else:
                    rel = os.path.relpath(ip, root)
                if os.path.isdir(ip):
                    rdir = rel
                    rpath = None
                else:
                    rdir = os.path.dirname(rel) or ""
                    rpath = os.path.basename(rel)
                try:
                    self.file_mode_file_list.app.rel_dir = rdir
                    self.file_mode_file_list.app.rel_file = rpath
                except Exception as _e:
                    self.printException(_e, "toggle_history_fullscreen: setting file_mode app rels failed")
                self.file_mode_file_list.prepFileModeFileList(highlight=self.highlight)
            except Exception as _ex:
                self.printException(_ex, "toggle_history_fullscreen prepFileModeFileList failed")
        except Exception as e:
            self.printException(e, "toggle_history_fullscreen prepFileModeFileList failed")
        self.change_state("file_fullscreen", f"#{LEFT_FILE_LIST_ID}", LEFT_FILE_FOOTER)

    def toggle_file_history(self) -> None:
        """
        Switch to a history view for the current file and prepare paired file list.

        Reads authoritative commit hashes after preparing the repo history and
        then prepares the repo file list highlighting the canonical filename.
        """
        # Save transient values (use repo-relative rel_dir/rel_file)
        saved_path = os.path.join(self.rel_dir or "", self.rel_file) if self.rel_file else (self.rel_dir or "")
        try:
            logger.debug(
                "toggle_file_history: before prepRepoModeHistoryList app.previous_hash=%r app.current_hash=%r saved_path=%r",
                self.previous_hash,
                self.current_hash,
                saved_path,
            )
            # Prepare repo history and request that preparer highlight and
            # mark the provided commit hashes when present.
            # Use the current app-level hashes as the initial request; the
            # preparer will update app-level state to reflect the highlighted
            # selection and we will read back the authoritative values.
            self.repo_mode_history_list.prepRepoModeHistoryList(
                prev_hash=self.previous_hash, curr_hash=self.current_hash
            )
        except Exception as e:
            self.printException(e, "toggle_file_history preparing repo history failed")
        try:
            logger.debug(
                "toggle_file_history: after prepRepoModeHistoryList app.previous_hash=%r app.current_hash=%r",
                self.previous_hash,
                self.current_hash,
            )
            # After the history preparer runs it will have updated
            # `app.current_hash`/`app.previous_hash` to match the highlighted
            # selection. Read those authoritative values and pass them to the
            # file preparer so it lists the correct commit-pair.
            use_prev = self.previous_hash
            use_curr = self.current_hash
            # Compute a repo-relative highlight filename so it matches the
            # `_raw_text` values attached to repo-mode file list rows (these
            # are repository-relative paths like 'docs/notes.txt'). Prefer a
            # normalized relative path when `saved_path` is inside the repo.

            # Pass the repo-relative highlight so matching uses repository-relative rows.
            logger.debug("toggle_file_history: using rel_dir=%r rel_file=%r", self.rel_dir, self.rel_file)
            self.repo_mode_file_list.prepRepoModeFileList(use_prev, use_curr)
        except Exception as e:
            self.printException(e, "toggle_file_history preparing repo file list failed")
        self.change_state("history_file", f"#{RIGHT_FILE_LIST_ID}", RIGHT_FILE_FOOTER)

    def toggle_history_file(self) -> None:
        """
        Switch to file-history layout for the current file and prepare lists.

        Prepares the right file list and the file's history preparer, then
        switches the UI to the paired layout.
        """
        # Save transient values (use repo-relative rel_dir/rel_file)
        saved_path = os.path.join(self.rel_dir or "", self.rel_file) if self.rel_file else (self.rel_dir or "")
        saved_curr = self.current_hash
        saved_prev = self.previous_hash
        logger.debug(
            "toggle_history_file: before prepFileModeFileList app.previous_hash=%r app.current_hash=%r saved_path=%r",
            self.previous_hash,
            self.current_hash,
            saved_path,
        )
        try:
            # Prepare the right file list (file pane on right) showing files
            # Use the full path as the highlight so matching is
            # performed against canonical full paths instead of basenames.
            hl = saved_path
            logger.debug("toggle_history_file: saved_path=%r computed_highlight=%r", saved_path, hl)
            gitrepo = self.gitRepo
            try:
                ip = saved_path or "."
                root = gitrepo.get_repo_root()
                if ip == root:
                    rel = ""
                elif ip.startswith(root + os.sep):
                    rel = ip[len(root) + 1 :]
                else:
                    rel = os.path.relpath(ip, root)
                if os.path.isdir(ip):
                    rdir = rel
                    rpath = None
                else:
                    rdir = os.path.dirname(rel) or ""
                    rpath = os.path.basename(rel)
                try:
                    self.file_mode_file_list.app.rel_dir = rdir
                    self.file_mode_file_list.app.rel_file = rpath
                except Exception as _e:
                    self.printException(_e, "toggle_history_file: setting file_mode app rels failed")
                self.file_mode_file_list.prepFileModeFileList(highlight=self.highlight)
            except Exception as _ex:
                self.printException(_ex, "toggle_history_file prepFileModeFileList failed")
        except Exception as e:
            self.printException(e, "toggle_history_file prepFileModeFileList failed")
        # Prepare the right history list for the current file and request
        # the preparer highlight/mark the provided commit hashes.
        self.file_mode_history_list.prepFileModeHistoryList(
            saved_path or ".", prev_hash=saved_prev, curr_hash=saved_curr
        )
        self.change_state("file_history", f"#{RIGHT_HISTORY_LIST_ID}", RIGHT_HISTORY_FOOTER)

    def toggle_file_history_diff(self) -> None:
        """
        Toggle to a file-history diff in the right diff column.

        Prepares file-history state then shows the diff and updates `diff_list.go_back`.
        """
        self.toggle_file_history()
        try:
            # show diff in the right diff column and set go_back
            self.change_state("history_file_diff", f"#{DIFF_LIST_ID}", DIFF_FOOTER_1)
            try:
                self.diff_list.go_back = ("history_file", RIGHT_FILE_LIST_ID, RIGHT_FILE_FOOTER)
            except Exception as e:
                self.printException(e, "toggle_file_history_diff setting diff_list.go_back failed")
        except Exception as e:
            self.printException(e, "toggle_file_history_diff change_state failed")

    def toggle_history_file_diff(self) -> None:
        """Toggle to a history-file diff view and set appropriate go-back state."""
        self.toggle_history_file()
        try:
            self.change_state("file_history_diff", f"#{DIFF_LIST_ID}", DIFF_FOOTER_1)
            try:
                self.diff_list.go_back = ("file_history", RIGHT_HISTORY_LIST_ID, RIGHT_HISTORY_FOOTER)
            except Exception as e:
                self.printException(e, "toggle_history_file_diff setting diff_list.go_back failed")
        except Exception as e:
            self.printException(e, "toggle_history_file_diff change_state failed")

    def toggle_diff_fullscreen(self) -> None:
        """If a saved diff layout exists, toggle back to it via recursive dispatch."""
        try:
            saved = self.diff_list._saved_layout
            if saved:
                self.toggle(saved)
        except Exception as e:
            self.printException(e, "toggle_diff_fullscreen retrieving saved layout failed")


def handle_init_config(args) -> int:
    """
    Handle the --init-config CLI action.

    Writes a commented template when no config exists, or appends a
    comment-only block for missing/empty known options after creating
    a non-conflicting backup of the existing config file.
    Returns an exit code suitable for returning from `main()`.
    """
    target_dir = os.getcwd() if args.init_config == "cwd" else os.path.expanduser("~")
    target_path = os.path.join(target_dir, ".gitdiffnavtool.ini")
    try:
        if os.path.exists(target_path):
            with open(target_path, "r", encoding="utf-8") as fh:
                existing_text = fh.read()
            cfg_existing = configparser.ConfigParser()
            cfg_existing.read(target_path)
            has_section = "gitdiffnavtool" in cfg_existing
            src_existing = cfg_existing["gitdiffnavtool"] if has_section else cfg_existing.defaults()
            known_keys = [
                "repo-first",
                "ignored-files",
                "untracked-files",
                "initial-popup",
                "add-authors",
                "trim-debug",
                "branch",
                "color",
                "diff",
                "debug",
                "output-directory",
                "hash-length",
                "unified-context",
                "history-limit",
                "minimum-sidebyside-width",
            ]
            missing_value_keys: list[str] = []
            for key in known_keys:
                raw = src_existing.get(key) if key in src_existing else None
                if raw is None or str(raw).strip() == "":
                    missing_value_keys.append(key)

            comment_block = build_missing_config_option_comment_block(missing_value_keys, has_section, existing_text)
            if not comment_block:
                print(f"config exists and already has values or comment hints for known options: {target_path}")
                return 0

            appended_option_count = len(re.findall(r"(?im)^\s*#\s*[a-z0-9-]+\s*=", comment_block))

            backup_path = build_backup_path(target_path)
            try:
                with open(backup_path, "x", encoding="utf-8") as fh:
                    fh.write(existing_text)
            except Exception as e:
                printException(e, f"failed creating config backup {backup_path}")
                return 2

            with open(target_path, "a", encoding="utf-8") as fh:
                fh.write(comment_block)
            print(
                "config exists; created backup "
                f"{backup_path} and appended comment hints for {appended_option_count} "
                f"missing-value option(s): {target_path}"
            )
            return 0
        with open(target_path, "x", encoding="utf-8") as fh:
            fh.write(build_default_config_template())
        print(f"wrote config template: {target_path}")
        return 0
    except FileExistsError as e:
        printException(e, f"config already exists: {target_path}")
        print(f"config already exists: {target_path}", file=sys.stderr)
        return 2
    except Exception as e:
        printException(e, f"failed writing config template to {target_path}")
        return 2


def parse_cli_and_config(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """
    Build the argument parser, load config defaults from .gitdiffnavtool.ini,
    and return the parsed `args` Namespace.
    """
    parser = argparse.ArgumentParser(prog="gitdiffnavtool.py")

    parser.add_argument(
        "--init-config",
        dest="init_config",
        nargs="?",
        choices=["cwd", "home"],
        const="cwd",
        metavar="LOCATION",
        help="write a commented .gitdiffnavtool.ini template and exit (LOCATION: cwd or home, default: cwd)",
    )

    parser.add_argument(
        "-c",
        "--config",
        dest="config",
        metavar="FILE",
        help="use the specified config FILE instead of searching cwd and $HOME for .gitdiffnavtool.ini",
    )

    # Help option (processed before other options)
    help_group = parser.add_mutually_exclusive_group()
    help_group.add_argument(
        "--show-help",
        dest="show_help",
        action="store_true",
        help="display formatted help text without colors and exit",
    )
    help_group.add_argument(
        "--show-help-color",
        dest="show_help_color",
        action="store_true",
        help="display formatted help text with colors and exit",
    )

    # Initial popup preview options (processed before other options)
    popup_preview_group = parser.add_mutually_exclusive_group()
    popup_preview_group.add_argument(
        "--show-initial-popup",
        dest="show_initial_popup",
        action="store_true",
        help="display startup popup text without colors and exit",
    )
    popup_preview_group.add_argument(
        "--show-initial-popup-color",
        dest="show_initial_popup_color",
        action="store_true",
        help="display startup popup text with colors and exit",
    )

    # Startup options group
    startup_group = parser.add_argument_group("Startup Options")
    startup_group.add_argument(
        "-r", "--repo-first", dest="repo_first", action="store_true", help="start in repo-first mode"
    )
    startup_group.add_argument(
        "-R",
        "--repo-hash",
        dest="repo_hash",
        action="append",
        metavar="HASH",
        help="specify a repo commit hash; may be provided up to two times (implies --repo-first)",
    )

    # Mutually exclusive group for initial-popup flags
    popup_group = startup_group.add_mutually_exclusive_group()
    popup_group.add_argument(
        "-p",
        "--initial-popup",
        dest="initial_popup",
        action="store_true",
        help="enable the startup popup (overrides config setting)",
    )
    popup_group.add_argument(
        "-P",
        "--no-initial-popup",
        dest="no_initial_popup",
        action="store_true",
        help="disable the startup popup",
    )

    # Mutually exclusive group for branch flags
    branch_group = startup_group.add_mutually_exclusive_group()
    branch_group.add_argument(
        "-b",
        "--branch",
        dest="branch",
        metavar="BRANCH",
        help="use a specific git branch (overrides config setting)",
    )
    branch_group.add_argument(
        "-B",
        "--no-branch",
        dest="no_branch",
        action="store_true",
        help="disable branch configuration (overrides config setting)",
    )

    startup_group.add_argument(
        "-o",
        "--output-directory",
        dest="output_directory",
        metavar="DIR",
        default=None,
        help="directory where w/W snapshot files are written (default: beside source file)",
    )

    # History List Options: options affecting history list rendering/behavior
    history_group = parser.add_argument_group("History List Options")
    history_group.add_argument(
        "--hash-length",
        dest="hash_length",
        metavar="N",
        type=int,
        default=HASH_LENGTH,
        help=f"number of characters to display for short hashes (default: {HASH_LENGTH})",
    )
    history_group.add_argument(
        "--history-limit",
        dest="history_limit",
        metavar="N",
        type=int,
        default=0,
        help="limit number of history entries to display (default: 0 for unlimited)",
    )

    # Mutually exclusive group for author display flags (history related)
    author_group = history_group.add_mutually_exclusive_group()
    author_group.add_argument(
        "--add-authors",
        dest="add_authors",
        action="store_true",
        help="display author name and email in commit rows (default)",
    )
    author_group.add_argument(
        "--no-add-authors",
        dest="no_add_authors",
        action="store_true",
        help="hide author name and email in commit rows",
    )

    startup_group.add_argument(
        "path", nargs="?", default=".", help="git repository or file within it (default: current directory)"
    )

    # Diff options group
    diff_group = parser.add_argument_group("Diff Options")
    # Mutually-exclusive color options within diff group
    color_group = diff_group.add_mutually_exclusive_group()
    color_group.add_argument(
        "--color",
        dest="color",
        metavar="SCHEME",
        choices=DIFF_COLOR_SCHEMES,
        help=f"start with color scheme (one of: {', '.join(DIFF_COLOR_SCHEMES)})",
    )
    color_group.add_argument(
        "-C",
        "--no-color",
        dest="no_color",
        action="store_true",
        help="same as `--color=none` with diff colorization off",
    )

    diff_group.add_argument(
        "--diff",
        dest="diff",
        metavar="VARIANT",
        choices=DIFF_VARIANT_NAMES,
        help=f"start with diff variant (one of: {', '.join(DIFF_VARIANT_NAMES)})",
    )
    diff_group.add_argument(
        "--unified-context",
        dest="unified_context",
        metavar="N",
        type=int,
        default=3,
        help="number of context lines for unified diffs (default: 3, git diff -U option)",
    )
    blank_group = diff_group.add_mutually_exclusive_group()
    blank_group.add_argument(
        "--blank-before-hunk",
        dest="blank_before_hunk",
        action="store_true",
        default=True,
        help="insert a blank line before each diff hunk header (lines starting with @@)",
    )
    blank_group.add_argument(
        "--no-blank-before-hunk",
        dest="no_blank_before_hunk",
        action="store_true",
        help="do not insert blank lines before diff hunk headers",
    )
    diff_group.add_argument(
        "--minimum-sidebyside-width",
        dest="minimum_sidebyside_width",
        metavar="N",
        type=int,
        default=MIN_SIDE_BY_SIDE_WIDTH,
        help=f"minimum terminal width for side-by-side view (default: {MIN_SIDE_BY_SIDE_WIDTH})",
    )

    # File List Options group
    filelist_group = parser.add_argument_group("File List Options")
    # Mutually exclusive group for ignored-files flags
    ignored_group = filelist_group.add_mutually_exclusive_group()
    ignored_group.add_argument(
        "-i",
        "--ignored-files",
        dest="ignored_files",
        action="store_true",
        help="include ignored files in file-mode listings (overrides config setting)",
    )
    ignored_group.add_argument(
        "-I",
        "--no-ignored-files",
        dest="no_ignored",
        action="store_true",
        help="exclude ignored files from file-mode listings",
    )

    # Mutually exclusive group for untracked-files flags
    untracked_group = filelist_group.add_mutually_exclusive_group()
    untracked_group.add_argument(
        "-u",
        "--untracked-files",
        dest="untracked_files",
        action="store_true",
        help="include untracked files in file-mode listings (overrides config setting)",
    )
    untracked_group.add_argument(
        "-U",
        "--no-untracked-files",
        dest="no_untracked",
        action="store_true",
        help="exclude untracked files from file-mode listings",
    )

    # Debug options group
    debug_group = parser.add_argument_group("Debug Options")
    debug_group.add_argument(
        "-d", "--debug", dest="debug", metavar="FILE", help="write debug log to FILE (enables debug logging)"
    )

    trim_debug_group = debug_group.add_mutually_exclusive_group()
    trim_debug_group.add_argument(
        "--trim-debug",
        dest="trim_debug",
        action="store_true",
        help="truncate debug file before writing (overrides config setting)",
    )
    trim_debug_group.add_argument(
        "--no-trim-debug",
        dest="no_trim_debug",
        action="store_true",
        help="do not truncate debug file (append mode)",
    )

    debug_group.add_argument(
        "-D",
        "--debug-tracing",
        dest="debug_tracing",
        action="store_true",
        help="enable TRACE-level (very verbose) logging",
    )
    debug_group.add_argument(
        "-v", "--verbose", dest="verbose", action="count", default=0, help="increase verbosity (repeatable)"
    )
    debug_group.add_argument(
        "--highlight",
        dest="highlight",
        metavar="BASENAME",
        help="basename of a file to pre-highlight (must be a basename, no path elements)",
    )

    # Load optional configuration from .gitdiffnavtool.ini (cwd then $HOME).
    # Keys in the [gitdiffnavtool] section:
    #   ignored-files=true/false
    #   untracked-files=true/false
    #   repo-first=true/false
    #   initial-popup=true/false
    #   branch=<branch-name>
    #   color=<scheme> (red-green, blue-orange, teal-purple, style, none)
    #   diff=<variant> (classic, ignore-spaces, patience, word-diff, side-by-side)
    #   hash-length=<integer >= 1>
    #   unified-context=<integer >= 0>
    #   history-limit=<integer >= 0>
    #   output-directory=<directory path>
    #   minimum-sidebyside-width=<integer >= 1>
    #   blank-before-hunk=true/false
    #   add-authors=true/false
    #   debug=<filename>
    #   trim-debug=true/false
    # CLI options always take precedence over config defaults.
    # Allow the user to specify an explicit config path via --config; when
    # provided, only that file (if present) will be used. Otherwise search
    # current directory then $HOME for `.gitdiffnavtool.ini`.
    parsed_args, _ = parser.parse_known_args(argv)
    if getattr(parsed_args, "config", None):
        cfg_files = [parsed_args.config]
    else:
        cfg_files = [
            os.path.join(os.getcwd(), ".gitdiffnavtool.ini"),
            os.path.join(os.path.expanduser("~"), ".gitdiffnavtool.ini"),
        ]
    cfg = configparser.ConfigParser()
    read_files = [p for p in cfg_files if os.path.exists(p)]
    if read_files:
        try:
            cfg.read(read_files)
            if "gitdiffnavtool" in cfg:
                src = cfg["gitdiffnavtool"]
            else:
                src = cfg.defaults()

            def _getbool(name: str):
                """
                Parse a boolean config value from common truthy/falsy strings.

                Returns True for '1', 'true', 'yes', 'on' (case-insensitive).
                Returns False for '0', 'false', 'no', 'off' (case-insensitive).
                Returns None if key not present or value doesn't match.
                """
                if name in src:
                    v = src.get(name)
                    if v is None:
                        return None
                    vs = v.strip().lower()
                    if vs in ("1", "true", "yes", "on"):
                        return True
                    if vs in ("0", "false", "no", "off"):
                        return False
                return None

            def _match_choice(key: str, allowed: list[str]) -> str | None:
                """
                Normalize and validate a choice config value against allowed list.

                Performs case-insensitive matching and returns the canonical value
                from the allowed list, or None if not found or empty.
                """
                raw = src.get(key)
                if raw is None:
                    return None
                v = raw.strip() if isinstance(raw, str) else str(raw).strip()
                if v == "":
                    return None
                vs = v.lower()
                for s in allowed:
                    if s.lower() == vs:
                        return s
                return None

            def _get_choice(key: str, cfg_key_name: str, allowed: list[str]) -> str | None:
                """Get and validate a choice config value against allowed list."""
                match = _match_choice(key, allowed)
                if match:
                    return match
                raw = _get_string(key)
                if raw:
                    sys.exit(f"invalid {cfg_key_name} '{raw}' in config; must be one of: {', '.join(allowed)}")
                return None

            def _get_string(key: str) -> str | None:
                """Get and strip a string config value."""
                if key not in src:
                    return None
                raw = src.get(key)
                s = raw.strip() if isinstance(raw, str) else str(raw).strip()
                return s if s else None

            def _get_int(key: str, cfg_key_name: str, min_val: int | None, max_val: int | None) -> int | None:
                """Get and validate an integer config value with optional min/max constraints."""
                if key not in src:
                    return None
                raw = src.get(key)
                raw_str = raw if isinstance(raw, str) else str(raw)
                s = raw_str.strip()
                if not s:
                    return None
                try:
                    val = int(s)
                    if min_val is not None and val < min_val:
                        raise ValueError(f"must be >= {min_val}")
                    if max_val is not None and val > max_val:
                        raise ValueError(f"must be <= {max_val}")
                    return val
                except Exception as e:
                    printException(e, f"invalid {cfg_key_name} in config")
                    constraint = ""
                    if min_val is not None and max_val is not None:
                        constraint = f" >= {min_val} and <= {max_val}"
                    elif min_val is not None:
                        constraint = f" >= {min_val}"
                    elif max_val is not None:
                        constraint = f" <= {max_val}"
                    sys.exit(f"invalid {cfg_key_name} '{raw_str}' in config; must be an integer{constraint}")

            defaults = {}

            # Map simple boolean-like config keys to parser defaults. The
            # transform callable converts the parsed boolean to the desired
            # destination value (e.g. invert for `no_` flags).
            bool_map: list[tuple[str, str, Callable[[bool], object]]] = [
                ("repo-first", "repo_first", lambda x: bool(x)),
                ("ignored-files", "no_ignored", lambda x: not bool(x)),
                ("untracked-files", "no_untracked", lambda x: not bool(x)),
                ("initial-popup", "no_initial_popup", lambda x: not bool(x)),
                ("add-authors", "no_add_authors", lambda x: not bool(x)),
                ("trim-debug", "trim_debug", lambda x: bool(x)),
                ("blank-before-hunk", "blank_before_hunk", lambda x: bool(x)),
            ]

            for cfg_key, dest, transform in bool_map:
                b = _getbool(cfg_key)
                if b is not None:
                    defaults[dest] = transform(b)

            # Optional branch name; blank means no configured branch.
            if "branch" in src:
                branch = (src.get("branch") or "").strip()
                if branch:
                    defaults["branch"] = branch

            # Load choice-based config values
            color_val = _get_choice("color", "color scheme", DIFF_COLOR_SCHEMES)
            if color_val:
                defaults["color"] = color_val

            diff_val = _get_choice("diff", "diff variant", DIFF_VARIANT_NAMES)
            if diff_val:
                defaults["diff"] = diff_val

            # Load string-based config values
            debug_val = _get_string("debug")
            if debug_val:
                defaults["debug"] = debug_val

            output_dir = _get_string("output-directory")
            if output_dir:
                defaults["output_directory"] = output_dir

            # Load integer config values with min/max constraints
            hash_len = _get_int("hash-length", "hash-length", 1, None)
            if hash_len is not None:
                defaults["hash_length"] = hash_len

            unified_ctx = _get_int("unified-context", "unified-context", 0, None)
            if unified_ctx is not None:
                defaults["unified_context"] = unified_ctx

            history_lim = _get_int("history-limit", "history-limit", 0, None)
            if history_lim is not None:
                defaults["history_limit"] = history_lim

            min_width = _get_int("minimum-sidebyside-width", "minimum-sidebyside-width", 1, None)
            if min_width is not None:
                defaults["minimum_sidebyside_width"] = min_width

            if defaults:
                parser.set_defaults(**defaults)
        except Exception as e:
            printException(e, f"failed reading config files {read_files}")

    return parser.parse_args(argv)


def print_md_and_exit(text: str, show_color: bool) -> int:
    """
    Render markdown `text` to the console then return exit code 0.

    `show_color` enables colorized output when True; otherwise output is
    rendered without colors.
    """
    console = Console(no_color=not show_color)
    md = Markdown(text)
    console.print(md)
    return 0


def set_debugging(args) -> None:
    """
    Configure debug logging based on parsed `args`.
    """
    if args.debug:
        try:
            os.makedirs(os.path.dirname(args.debug) or "", exist_ok=True)
        except Exception as e:
            printException(e, "could not create directories for debug log file")
        # Truncate debug file if trim-debug is enabled (default: append mode)
        if args.trim_debug:
            try:
                with open(args.debug, "w") as f:
                    pass  # Truncate the file
            except Exception as e:
                printException(e, f"could not truncate debug log file {args.debug}")
        logging.basicConfig(
            filename=args.debug,
            level=logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled -> %s", args.debug)

        # Enable TRACE-level logging if requested (applies to root and handlers)
        enable_trace_logging(bool(args.debug_tracing))

        # When verbosity is low, silence verbose debug from markdown-it
        if args.verbose < 3:
            logging.getLogger("markdown_it").setLevel(logging.WARNING)


def main(argv: Optional[list[str]] = None) -> int:
    """
    Command-line entry point for gitdiffnavtool.

    Parses CLI arguments, locates the repository worktree, configures
    logging, and launches the `GitDiffNavTool` Textual application.
    Returns process exit code (0 on success).
    """
    args = parse_cli_and_config(argv)

    # Handle --init-config and exit
    if args.init_config:
        return handle_init_config(args)

    # Handle --show-help or --show-help-color: render HELP_TEXT as markdown and exit
    if args.show_help or args.show_help_color:
        return print_md_and_exit(HELP_TEXT, args.show_help_color)

    # Handle --show-initial-popup or --show-initial-popup-color:
    # render INITIAL_POPUP_TEXT as markdown and exit.
    if args.show_initial_popup or args.show_initial_popup_color:
        return print_md_and_exit(INITIAL_POPUP_TEXT, args.show_initial_popup_color)

    # Handle CLI flag overrides for initial-popup, ignored-files, untracked-files,
    # add-authors, trim-debug, and branch selection:
    # Positive flags (e.g., --initial-popup, --ignored-files) take precedence over
    # negative flags and config defaults.
    if args.initial_popup:
        args.no_initial_popup = False
    if args.ignored_files:
        args.no_ignored = False
    if args.untracked_files:
        args.no_untracked = False
    if args.add_authors:
        args.no_add_authors = False
    if args.no_trim_debug:
        args.trim_debug = False
    if args.no_branch:
        args.branch = None
    if getattr(args, "no_blank_before_hunk", False):
        args.blank_before_hunk = False

    # Validate --highlight is a bare basename (no path elements)
    if args.highlight:
        hl = args.highlight
        try:
            if os.path.isabs(hl) or os.path.basename(hl) != hl:
                printException(ValueError("--highlight must be a basename (no path elements)"), "argument error")
                return 2
        except Exception as e:
            printException(e, "argument parsing/validation failed")
            return 2

    if int(args.hash_length) < 1:
        printException(ValueError("--hash-length must be >= 1"), "argument error")
        return 2

    # Configure logging if debug file requested
    set_debugging(args)

    try:
        # If repo-hash provided, validate count and imply repo-first
        repo_hashes = None
        if args.repo_hash:
            repo_hashes = args.repo_hash
            if len(repo_hashes) > 2:
                printException(ValueError("--repo-hash may be specified at most twice"), "argument error")
                return 2
            # imply repo-first when -R used
            args.repo_first = True

        # Allocate the shared `GitRepo` instance here and compute the
        # repository-relative `relpath` for the provided path. The app will
        # receive the `gitRepo` instance so helpers can call into it.
        try:
            gitrepo = GitRepo(args.path, branch=args.branch)
        except ValueError as ve:
            if args.verbose:
                printException(ve, f"repository discovery failed for {args.path}")
            sys.exit(
                f"Invalid branch '{args.branch}' for '{args.path}'"
                if args.branch
                else f"Not a git repository: {args.path}"
            )
        except Exception as e:
            if args.verbose:
                printException(e, f"repository discovery failed for {args.path}")
            sys.exit(f"Not a git repository: {args.path}")
        logger.debug("Discovered repository worktree root: %s", gitrepo.get_repo_root())

        # Compute repository-relative directory/file for the provided path.
        try:
            rel_dir, rel_file = gitrepo.cwd_plus_path_to_reldir_relfile(args.path)
        except Exception as e:
            printException(e, f"Not a git repository or invalid path: {args.path}")
            sys.exit(f"Not a git repository: {args.path}")

        logger.debug(
            "Starting GitDiffNavTool; args.path=%s repo_root=%s rel_dir=%r rel_file=%r",
            args.path,
            gitrepo.get_repo_root(),
            rel_dir,
            rel_file,
        )

        # Default diff variant to 'classic' if not specified via CLI or config
        if not args.diff:
            args.diff = DIFF_VARIANT_NAMES[0]

        # Log args.diff for debugging
        logger.debug(
            "main: creating GitDiffNavTool: args.diff=%r, DIFF_VARIANT_NAMES=%r",
            args.diff,
            DIFF_VARIANT_NAMES,
        )

        app = GitDiffNavTool(
            gitRepo=gitrepo,
            rel_dir=rel_dir,
            rel_file=rel_file,
            repo_first=args.repo_first,
            repo_hashes=repo_hashes,
            no_ignored=args.no_ignored,
            no_untracked=args.no_untracked,
            no_initial_popup=args.no_initial_popup,
            verbose=args.verbose,
            highlight=args.highlight,
            color_scheme=args.color,
            diff_variant=args.diff,
            hash_length=args.hash_length,
            add_authors=not args.no_add_authors,
            unified_context=args.unified_context,
            history_limit=args.history_limit,
            minimum_sidebyside_width=args.minimum_sidebyside_width,
            blank_before_hunk=args.blank_before_hunk,
            output_directory=args.output_directory,
        )
        # Run the textual app (blocks until exit)
        app.run()
        return 0
    except Exception as e:
        printException(e, "fatal error running GitDiffNavTool")
        return 2


if __name__ == "__main__":
    sys.exit(main())
