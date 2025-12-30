Git Diff History Navigator Tool — Functional Structure
=====================================================

Overview
- Textual (TUI) app with three primary columns: Files (left), History (middle), Diff (right); optional Help column; layouts swap depending on mode.
- Two modes of use: file-first (default) and repo-first (`--repo-first`). File-first starts with filesystem tree; repo-first starts with repo-wide history.
- Widgets:
  - `FileModeFileList` (left files) shows working tree, status markers, directory navigation.
  - `FileModeHistoryList` (right history) shows commit history for a selected file (with pseudo entries).
  - `RepoModeHistoryList` (left history in repo-first) shows repo-wide commits (with pseudo entries).
  - `RepoModeFileList` (right files in repo-first) shows files changed between two commits or between pseudo states.
  - `DiffList` shows diffs between commit pairs/working state.
  - `HelpList` renders help markdown.
- Layouts are controlled via named layouts: `file_fullscreen`, `history_fullscreen`, `file_history`, `history_file`, `file_history_diff`, `history_file_diff`, `diff_fullscreen`, `help_fullscreen`.


CLI entry
- Script: `gitdiffnavtool.py`
- Options:
  - `-C/--no-color`: start with diff colorization off.
  - `-r/--repo-first`: start in repo-first (repo-history-first) mode.
  - `-d/--debug FILE`: write debug logs to FILE (enables detailed logging).
  - `-R/--repo-hash HASH`: specify a repo commit hash; may be provided up to two times. Using `-R` implies `--repo-first`. When provided the hashes initialize the repo-mode view: one hash sets the current commit; two hashes set previous and current commit for diffs.
  - `path` (positional, optional): directory or file. If file, app starts in its directory and preloads its history.
- Signals: SIGINT/SIGTERM handled to call `logging.shutdown` and exit.
- Diff colorization can be toggled at runtime (c/C).

Coloring scheme for FileList
- Highlight background constant `HIGHLIGHT_FILELIST_BG` (default `#f1c40f`) and `HIGHLIGHT_FILELIST_STYLE` (`white on HIGHLIGHT_FILELIST_BG`). Applied to selected list items and labels.
- Status markers in file list:
- Entry format: each row in the `FileModeFileList` is rendered as "TAG NAME" where TAG is a one-character marker (or an arrow for directories) followed by a space and then the file or directory name. This makes tag meaning explicit and easier to scan.

- Status markers in file list (used as TAG for files):
  - space: tracked/clean (white)
  - U: untracked (bold yellow)
  - M: modified (yellow)
  - A: staged (cyan)
  - D: deleted in working tree (red)
  - I: ignored (dim italic)
  - !: conflicted (magenta)

- Directory tagging: directories are shown with arrows as the TAG instead of a single-status marker:
  - `←` (left-arrow): special parent entry shown as `..` (displayed as "← ..")
  - `→` (right-arrow): normal directory entry (displayed as "→ dirname/")
- Directories styled with white on blue; key legend rows bold white on blue.
- Borders: left columns thin white; right columns heavy gray; consistent across lists and diff/help.
- Footers: bold text strings per mode (file/history/diff/help).

Coloring scheme for RepoList
- Highlighting: uses separate repo-list highlight constants — `HIGHLIGHT_REPOLIST_BG` (default `#44475a`) for background and `HIGHLIGHT_REPOLIST_STYLE` (`white on HIGHLIGHT_REPOLIST_BG`) for the active/selected row.
- Row markers (for files shown as a commit-pair diff or staged/unstaged lists):
  - `+` : added in current (green, bold)
  - `-` : removed in current (red, bold)
  - `~` : modified/changed (yellow)
  - `R` : renamed (cyan)
  - `T` : type/permission change (magenta or dim)
  - `?` : unknown/untracked in this context (dim yellow)
- Directory/Tree rows: directories and tree headers are styled with bold cyan on default background; collapsed/legend rows use white on blue to match FileList legends.
- Hunk/file header rows: the per-file header shown in repo file lists (when listing diffs between trees) is bold and slightly dimmed (bold white or bright) so it reads above per-file items.
- Borders & separators: keep the same column border rules as FileList (thin white left borders, heavier gray separators on the right) to preserve visual alignment when swapping layouts.
- Accessibility: avoid using highly-saturated background colors for the repo list; prefer bold foreground accents (colored text) for markers and use `HIGHLIGHT_REPOLIST_BG` only for the single focused row.

Coloring scheme for Diff
- General:
  - Diff colorization mirrors standard `git` coloring: additions green, deletions red, and hunk metadata highlighted.
  - Colorization can be toggled at runtime with `c` / `C`. When disabled the diff is shown as plain text.
- Line-level styles:
  - Lines beginning with `+`: bright green (use a green foreground, not a background), additions are bold to aid scanning.
  - Lines beginning with `-`: bright red, deletions bold.
  - Lines beginning with a single space (context): default terminal foreground (no strong styling), dimmed slightly to de-emphasize.
  - Hunk headers (lines starting with `@@`): magenta or cyan, bold — should stand out and remain sticky for quick navigation.
  - File header lines (e.g. `diff --git a/... b/...`, `index ...`): use bold white on a subtle blue/dim background to separate file blocks from hunks.
  - Metadata lines (e.g. `--- a/...`, `+++ b/...`): use bold white with a subtle color accent (cyan) so they are distinct but not as prominent as additions/deletions.
- Subtle affordances:
  - Leading whitespace-only changes and purely whitespace hunks (when visible) are dim + italic to show low significance when `--ignore-space-change` variants are off.
  - Binary or mode-change notices (e.g. "Binary files differ" or "Mode change") use yellow or magenta and are bold to call attention.
- Diff variants and rotation:
  - The app supports rotating diff variants (normal, ignore-space-change, patience/algorithm variants). The current variant is indicated in the header and does not change the color scheme — only the content differs.
- Fullscreen behavior:
  - In `diff_fullscreen`, disable line-wrapping and prefer monospace font rendering; keep the same colors but increase contrast (brighter greens/reds) if the terminal supports it.
- Performance and parsing:
  - Colorization is applied by scanning line prefixes rather than attempting full syntax parse; this keeps the renderer fast and consistent with `git` output while avoiding heavy parsing libraries.

Constants
- `HIGHLIGHT_FILELIST_BG` — default `#f1c40f` (used for FileList selected-row background)
- `HIGHLIGHT_FILELIST_STYLE` — `white on HIGHLIGHT_FILELIST_BG` (used for FileList selected-row style)
- `HIGHLIGHT_REPOLIST_BG` — default `#44475a` (used for RepoList selected-row background)
- `HIGHLIGHT_REPOLIST_STYLE` — `white on HIGHLIGHT_REPOLIST_BG` (used for RepoList selected-row style)
- `DOLOGGING` — boolean flag; when true enables debug output to `tmp/debug.log` via the module `logging` configuration.

Key bindings
- Key-handling rule: the main application (`GitHistoryNavTool`) handles true app-level keys (quit, help, swap). Widgets (subclasses of `AppBase` / `ListView`) implement `key_` methods for widget-local keys. `AppBase` provides shared helpers and provides centralized dispatch for navigation keys (`up`/`down`/`pageup`/`pagedown`/`home`/`end`) so widgets can reuse consistent behavior. This keeps key logic co-located with the widget state it manipulates and makes overriding, testing, and refactoring safer.
- Global: q/Q to quit; h/H/? to show help (saves/restores state); s/S to swap between file-first and repo-first (handled by `GitHistoryNavTool`).
- Navigation in lists: up/down; PageUp/PageDown (half-page); Home/End; selected item is preserved via the widget index and `app.current_diff_file`; `_min_index` controls the top-row/start index (used to skip legend/header rows).
- Left/right keys handled per widget:
  - FileModeFileList: right enters directory or opens file history; left on ".." goes to the parent; left otherwise is a no-op.
  - FileModeHistoryList: right opens diff between current and checked/next commit; left returns to file_fullscreen and re-highlights filename.
  - RepoModeHistoryList: right prepares RepoModeFileList for selected commit pair and switches to history_file layout; left handled by App (state restore).
  - RepoModeFileList: right shows diff between selected commits for file; left restores history_fullscreen.
  - DiffList: left exits fullscreen or returns to previous layout (file_history/history_file); right enters diff_fullscreen; f also toggles fullscreen when in diff; c toggles color; d cycles diff variant; f/left behavior unified.
  - HelpList: any key restores previous saved state.
  - History lists: m/M toggles checkmark on current row (only one checked).
- Focus management sets active title label class `active` and ensures indices are valid.
- Avoid placing per-widget key logic inside `on_key` handlers or a shared dispatcher in `App`/`AppBase`. 

Modes and state
- File-first: layout starts with `file_fullscreen` (left file list). Selecting a file and pressing right opens its history (`file_history`), then diff (`file_history_diff`).
- Repo-first: layout starts with `history_fullscreen` (repo history). Right opens repo file list (`history_file`), then diff (`history_file_diff`).

State kept during navigation
- FileList navigation: as the user moves the cursor within any `FileList` widget (left or right), the application maintains and updates the current directory and filename in real time. Concretely:
  - `displayed_path` tracks the currently shown directory path.
  - `current_diff_file` tracks the filename highlighted by the widget's index/selection.
  - `watch_index` and `on_list_view_highlighted` handlers keep these values synchronized as selection changes.

- Repo/History navigation: as the user navigates history lists (`RepoModeHistoryList` or `FileModeHistoryList`), the app maintains the currently-highlighted commit hash and any marked (checked) commit hash used for pairwise diffs:
  - `current_commit_sha` and `current_prev_sha` are updated from the highlighted item (and from checked items when applicable).
  - The `compute_commit_pair_hashes` helper derives the pair to diff from the highlighted index and the single checked index (if set). These values are preserved across view changes and used by the diff builder.

Swap behavior (s/S)
- Pressing `s` or `S` swaps the active view between file-first and repo-first variants (or toggles the left/right file/history pairing) while preserving key navigation state:
  - The app flips its `log_first`/`repo-first` mode flag and invokes the appropriate toggle helper (`_toggle_left_file_to_history`, `_toggle_left_history_to_file`, `_toggle_right_history_to_file`, `_toggle_right_file_to_history`) depending on the current focus.
  - When swapping, the implementation preps the counterpart lists using the current `displayed_path` and `current_diff_file` (if present) so the same directory and filename remain visible after the swap. These prep calls schedule highlighting after DOM refreshes (see 'Interplay and lifecycle').
  - For history/repo swaps, `current_commit_sha` and `current_prev_sha` (and any checked commit index) are preserved; the `_choose_hash_in_history` and `compute_commit_pair_hashes` helpers are used to re-select the nearest matching commits in the target history widget.
  - Overall effect: swap changes only which widgets and layouts are visible; the user's working directory, currently-highlighted filename, and the current/marked commit selection persist and are restored in the new view.
  - If in one of the diff layouts, additionally the appropriate git diff will be displayed.

- Single-slot state save/restore via `save_state` / `restore_state` used for help overlay and other temporary overlays.
- Current context is tracked in the FileListBase, RepoListBase, and DiffList classes: `current_diff_file`, `current_commit_sha`, `current_prev_sha`, `displayed_path` (note: `RepoListBase` here refers to the repo-mode file-list classes such as `RepoModeFileList`).
- Highlighting enforced via watch_index/on_list_view_highlighted plus inline styles and Label updates.

Git interactions
- Repo discovery via `pygit2.discover_repository`; repo status and index mtimes via pygit2.
- Working tree statuses used for markers and pseudo entries.
- History:
  - File history: `git log --follow --date=short --pretty=format:%ad %h %s -- <file>`
  - Repo history: pygit2 walk from HEAD (or branches/tags) sorted by time.
- Diff building:
  - Core: `git diff` with variants: default, `--ignore-space-change`, `--diff-algorithm=patience` (rotated with d/D).
  - Pseudo hashes handling:
    - STAGED vs commits: `git diff --cached <commit> -- <file>`
    - MODS vs commits: `git diff <commit> -- <file>` or `git diff -- <file>` when no commit.
    - STAGED vs MODS: `git diff --name-only --cached` etc. for file lists; `git diff --cached -- --file` vs `git diff -- --file` for diffs.
- RepoModeFileList when comparing commits uses pygit2 diff between trees; when involving STAGED/MODS, uses `git diff --name-only` commands to list files.

- Columns and mapping
- The UI is built from six dedicated columns whose widths and visibility are controlled at runtime. The app composes these columns in a fixed order and `change_layout` / `_apply_column_layout` adjust each column's width percent and `styles.display` to show or hide columns as needed. Layout changes are immediate and recorded in `_current_layout` for state/save semantics.
- Column order (left→right):
  1. `left-file-column` — contains the `left-file-title` label and the `FileModeFileList` widget (id `left-file-list`). This column is the canonical display for file-mode file lists and will always host the `FileModeFileList` output.
  2. `left-history-column` — contains the `left-history-title` label and the `RepoModeHistoryList` widget (id `left-history-list`). This column is the canonical repository-wide history view used in repo-first mode.
  3. `right-history-column` — contains the `right-history-title` label and the `FileModeHistoryList` widget (id `right-history-list`). This column is the canonical file-scoped history view used in file-first mode or when showing a file's history.
  4. `right-file-column` — contains the `right-file-title` label and the `RepoModeFileList` widget (id `right-file-list`). This column is the canonical display for repo-mode file lists (files changed between commits / pseudo-state lists).
  5. `diff-column` — contains the `diff-title` label and the `DiffList` widget (id `diff-list`). This single canonical Diff widget is reused for all diff rendering and may be shown full-screen or columnated.
  6. `help-column` — contains the `help-title` label and the `HelpList` widget (id `help-list`). When visible this column shows the Markdown-based help content fullscreen.

- Mapping guarantees: the output produced by each preparatory/populate method is rendered into its canonical column/widget id so other code can rely on fixed ids when scheduling highlights or restoring selection. For example, `prepFileModeFileList` always populates the widget at `#left-file-list`, and `prepRepoModeFileList` always appends into `#right-file-list`.

Layout control: `_apply_column_layout` and `change_layout`
- The app implements two helpers to control column widths and visibility:
  - `_apply_column_layout(left_file_w, left_history_w, right_history_w, right_file_w, diff_w, help_w)` — sets each column container's `styles.width` to "{width}%" and `styles.flex` to 0. If a width is 0, the corresponding canonical widget's `styles.display` is set to `"none"`; otherwise `styles.display` is set to `None` so the CSS determines visibility.
  - `change_layout(newlayout)` — convenience mapping defining named layouts and calling `_apply_column_layout` with specific width tuples. Implemented layouts and widths:

```python
if newlayout == "file_fullscreen":
  self._apply_column_layout(100, 0, 0, 0, 0, 0)
elif newlayout == "history_fullscreen":
  self._apply_column_layout(0, 100, 0, 0, 0, 0)
elif newlayout == "file_history":
  self._apply_column_layout(25, 0, 75, 0, 0, 0)
elif newlayout == "history_file":
  self._apply_column_layout(0, 25, 0, 75, 0, 0)
elif newlayout == "file_history_diff":
  self._apply_column_layout(5, 0, 20, 0, 75, 0)
elif newlayout == "history_file_diff":
  self._apply_column_layout(0, 5, 0, 20, 75, 0)
elif newlayout == "diff_fullscreen":
  self._apply_column_layout(0, 0, 0, 0, 100, 0)
elif newlayout == "help_fullscreen":
  self._apply_column_layout(0, 0, 0, 0, 0, 100)
```

These helpers make layout changes deterministic and testable; use `change_layout(...)` from app-level handlers and `call_after_refresh(...)` when scheduling DOM-dependent highlight/index updates immediately after layout changes.

 - Runtime control: `_apply_column_layout` sets the percent widths and `styles.display` on the canonical widgets (e.g. `self.file_mode_file_list.styles.display = show if left_file_w else hide`). See 'Interplay and lifecycle' for guidance on scheduling DOM-dependent index/highlight updates.

Canonical widget mapping

The app uses six canonical widgets, each with a canonical column name, title label CSS id, and widget class:

```text
column_name: "left-file-list":
label_name: "left-file-title"
class: FileModeFileList

widget: repo_mode_history_list
column_name: "left-history-list":
label_name: "left-history-title"
class: RepoModeHistoryList

widget: repo_mode_file_list
column_name: "right-file-list":
label_name: "right-file-title"
class: RepoModeFileList

widget: file_mode_history_list
column_name: "right-history-list":
label_name: "right-history-title"
class: FileModeHistoryList

widget: diff_list
column_name: "diff-list":
label_name: "diff-title"
class: DiffList

widget: help_list
column_name: "help-list":
label_name: "help-title"
class: HelpList
```

Mapping guarantees: each `prep*` method writes into its canonical widget id so scheduling highlights and restores can rely on fixed ids (e.g. `prepFileModeFileList` populates `#left-file-list`).

Keymap table
- A compact key → scope → action table to make implementing `key_` methods straightforward:

  | Key | Scope | Action |
  |---:|:---|:---|
  | `Up` / `Down` | List widgets | Move selection up/down in the focused list (`key_up`/`key_down`). |
  | `PageUp` / `PageDown` | List widgets | Move half-screen up/down in focused list (`key_page_up`/`key_page_down`). |
  | `Home` / `End` | List widgets | Jump to first/last selectable item (`key_home`/`key_end`). |
  | `Left` / `Right` | Contextual (widget) | Widget-specific navigation (`key_left` / `key_right`) — e.g., enter directory, open history, show diff, exit fullscreen. |
  | `m` / `M` | History lists | Toggle single-slot mark/check on current history row (`key_m`). |
  | `c` / `C` | Diff | Toggle colorization (`key_c`). |
  | `d` | Diff | Cycle diff variant (`key_d`). |
  | `f` | Diff | Toggle fullscreen (`key_f`). |
  | `s` / `S` | App-level | Swap file-first ↔ repo-first views (handled at app level if not widget specific). |
  | `?` / `h` / `H` | App-level | Show help overlay (`key_question` / `key_h`). |
  | `q` / `Q` | App-level | Quit application (`key_q`). |

  - Implementation note: widget classes should implement only the `key_` methods relevant to their functionality; any global keys  should remain in  `AppBase` but may delegate to handlers in the main `App` class when they  genuinely affect the whole app, where appropriate.

Class Structure
**Derived classes (hierarchy from `AppBase`)**
  - `AppBase` (base list-widget for the app)
    - `FileListBase` (directory/file listing behaviors)
      - `FileModeFileList` (concrete file-mode listing used in file-first)
      - `RepoModeFileList` (repo-mode file listing used in repo-first)
    - `HistoryListBase` (commit-history behaviors)
      - `FileModeHistoryList` (file-scoped history)
      - `RepoModeHistoryList` (repo-wide history)
    - `DiffList` (diff rendering widget)
    - `HelpList` (help/content widget)

- `AppBase` (base widget for list-like components)
  - Role: centralizes common ListView utilities (text extraction, key handling, exception logging, small state defaults).
  - Key attributes: `_min_index`, `_populated`, `_filename`, `current_prev_sha`, `current_commit_sha`, `current_diff_file`.
  - Key methods: `printException(e, msg)`, `text_of(node)` (extracts visible text from a ListItem), `_extract_label_text(lbl)` (safe label text extraction), `on_key(event)` (navigation helpers, paging/home/end), `prep_and_show_diff(...)` (helper to populate diff and change layout).
  - Notes: Implements defensive exception handling and uses scheduled callbacks for DOM-dependent actions so subclasses can rely on stable scheduling (see 'Interplay and lifecycle').

- `FileListBase` (ListView for directory/file listings)
  - Role: shared behavior for file lists (left and right contexts) including highlight management and directory-change helper.
  - Key attributes: inherits `AppBase` attributes; `_last_highlighted`, `path`.
  - Key methods: `on_focus(event)` (ensure valid index), `_highlight_filename(name)` (select matching filename), `_highlight_top()` (select top usable index), `watch_index(old, new)` (react to index changes updating `displayed_path` and `current_diff_file` and applying highlight styles), `on_list_view_highlighted(event)` (sync app state on highlight), `_child_filename(child)` (safe extraction of child display string), `_enter_directory(new_path, highlight_name)` (robust path change using prep API).
  - Notes: highlights both ListItem and inner Label (CSS + inline style) and updates label renderable carefully to avoid replacing widgets with reprs.

- `FileModeFileList` (file-mode, left canonical file list)
  - Role: concrete FileList that lists a filesystem directory and decorates entries with git-status markers when repo data is available.
  - Key methods: `prepFileModeFileList(path)` (populate entries; insert legend key; set `_min_index`), `key_left(event)` (handle parent `..`), `key_right(event)` (enter directory or request file history).
  - Git usage: consults `app.repo_status_map` and `pygit2` flags to determine per-file markers and styles.

- `RepoModeFileList` (repo-mode file list — right canonical in repo-first)
  - Role: show files changed between two commits or pseudo states (STAGED/MODS). Used when exploring commit diffs across the repo.
  - Key methods: `prepRepoModeFileList(prev_hash, curr_hash)` (builds file list via pygit2 tree diffs or via `git diff --name-only` for pseudo tokens), `key_left(event)` (restore history-focused layout), `key_right(event)` (show diff for selected file).
  - Notes: builds items buffer then appends after DOM refresh to avoid mount races (see 'Interplay and lifecycle'); attaches per-item metadata (`_hash_prev`, `_hash_curr`, `_old_filename`, `_new_filename`).

- `HistoryListBase` (base for commit-history ListViews)
  - Role: shared history behaviors (single-check toggling, computing pair hashes, synchronized highlighting and state updates).
  - Key methods: `toggle_check_current()` (single-slot checkmark behavior), `compute_commit_pair_hashes()` (derive commit pair & lines for diffs), `on_focus(event)` (ensure valid index), `on_list_view_highlighted` (update `current_commit_sha`/`current_prev_sha` and apply highlight styles).
  - Notes: history rows carry `_hash` and `_raw_text` and may include pseudo `_hash` values like `STAGED`/`MODS`.

- `FileModeHistoryList` (file-scoped history — right canonical in file-first)
  - Role: populate commit history for a single file via `git log --follow` and insert pseudo entries when appropriate.
  - Key methods: `prepFileModeHistoryList(file_path)` (run `git log --follow` in `app.path` and build ListItem rows), `key_left(event)` (restore left file view and reschedule highlight), `key_right(event)` (compute pair and show diff via `prep_and_show_diff`).
  - Notes: attaches `_raw_text` and `_hash` for quick parsing and uses the `m`/`M` key to mark a commit.

- `RepoModeHistoryList` (repo-wide history — left canonical in repo-first)
  - Role: populate a repository-wide commit log (pygit2 walker) and optionally insert pseudo entries for staged/unstaged changes.
  - Key methods: `prepRepoModeHistoryList()` (walk commits, build `_raw_text`/`_hash` rows), `key_right(event)` (prepare `RepoModeFileList` and change to `history_file` layout to show files for the selected diff pair).

- `DiffList` (single canonical Diff widget)
  - Role: present textual diffs between two commit-ish identifiers for a filename; supports colorization and variant rotation.
  - Key methods: `prepDiffList(filename, prev, curr, variant_index)` (builds `git diff` command via `app.build_diff_cmd`, captures stdout, colorizes lines into `Text` items), `key_left`/`key_right` (enter/exit fullscreen and restore previous layout), and widget-specific `key_` handlers for toggles (`key_c`, `key_d`, `key_f`).
  - Notes: `app.build_diff_cmd` handles pseudo-hash variants (STAGED/MODS) and injects flags for the selected variant.

- `HelpList` (help content)
  - Role: render `HELP_TEXT` as Rich Markdown split into list blocks; any key restores previous state.

- `_TBDModal` (simple modal)
  - Role: small ModalScreen used as minimal error/placeholder display.

- `GitHistoryNavTool` (main App)
  - Role: compose the UI, track global state, respond to global keys, build repo cache, orchestrate layout changes and focus transitions.
  - Key attributes: `path`, `displayed_path`, `repo_available`, `repo_root`, `repo_index_set`, `repo_status_map`, `repo_index_mtime_map`, single-slot `_saved_state`, `_current_layout`, `_current_focus`, `_current_footer`, `current_commit_sha`, `current_prev_sha`, `current_diff_file`, `diff_variants`, `diff_cmd_index`, footer Texts (`footer_file`, `footer_history`, `footer_diff3`, `footer_diff_full`, `footer_help`).
  - Key methods: `compose()` (build columns and canonical widgets), `on_mount()` (resolve canonical widgets, build repo cache, perform initial prep depending on startup mode), `change_layout(newlayout)` / `_apply_column_layout(...)` (set widths and displays), `change_state(layout, focus, footer)` (apply layout/focus/footer), `change_focus(target)` (focus widget and update title label classes), `save_state()`/`restore_state()` (single-slot save/restore), `build_repo_cache()` (pygit2 discovery/status/index mtime map), `_choose_hash_in_history(...)`, `build_diff_cmd(...)`.
  - Notes: centralizes the mapping of logical behaviors to canonical widget ids and provides helpers used by all widgets to preserve selection/indices across layout changes.

Interplay and lifecycle
- Widgets populate via `prep*` methods that write into their canonical widget id; app-level helpers call these and then invoke `change_state`/`change_focus` to show and focus the target widget.
- Scheduling DOM-dependent work: use `call_after_refresh` as the preferred mechanism to schedule actions that depend on mounts/DOM refreshes (for example: applying highlights, resetting indices, or focusing newly-mounted widgets). Avoid polling loops or arbitrary sleeps — `call_after_refresh` makes selection/index transitions deterministic across swaps and layout changes.
- Exception handling funnels through `printException` so logs in `tmp/debug.log` aid debugging.


Headers and footers
 - Title labels: Files (left/right), History (left/right), Diff, Help; active column gets `active` class.
- Footers (Text objects):
  - File: "File: q(uit)  s(wap)  ?/h(elp)  ← ↑/↓/PgUp/PgDn/Begin/End"
  - History: "History: q(uit)  s(wap)  ?/h(elp)  ← ↑/↓/ PgUp/PgDn/Begin/End  →  m(ark)"
  - Diff (column mode): "Diff: q(uit)  ?/h(elp)  ← ↑/↓/PgUp/PgDn/Begin/End →/f(ull) c(olor) d(iff-type)"
  - Diff (fullscreen): "Diff: q(uit)  ?/h(elp)  ←/f(ull) ↑/↓/PgUp/PgDn/Begin/End c(olor) d(iff-type)"
  - Help: "Help: q(uit)  ↑/↓/PgUp/PgDn/Begin/End  Press any key to return"

Help rendering
- HELP_TEXT is markdown; rendered via Rich Markdown in HelpList, split into blocks and appended to ListView so paging keys work.

Library usage
- Textual: layout via `Vertical`/`Horizontal`, widgets `ListView`, `ListItem`, `Label`, focus/events (use `call_after_refresh` for DOM-dependent work; see 'Interplay and lifecycle'), CSS styling for highlight/borders.
- Rich: `Text` for styled text, `Markdown` for help rendering, `Align` (not heavily used), color styles for statuses and highlights.
- pygit2: repo discovery, status, index mtime map, commit walking, tree diffs.
- subprocess: shell out to `git log`, `git diff`, `git ls-tree`, and `git diff --name-only` for staged/unstaged lists.
- argparse: CLI parsing for path, no-color, repo-first.
  

Error / logging expectations
- Log file and level:
  - Primary debug output is written to `tmp/debug.log` when `DOLOGGING` is enabled. Use `logging.debug()` for verbose traces and `logging.warning()` for recoverable problems; reserve `logging.error()` for serious unexpected failures.
- Exception handling discipline:
  - Do not swallow exceptions with bare `except:` clauses. Catch specific exceptions where possible; if a general catch is required use `except Exception as e:` and call the widget's `printException(e, "<context>")` helper to record the error, stacktrace, and a short contextual message.
  - Prefer `self.printException(e, msg)` in widget methods so logs include the class/method context and formatted traceback. If `self` is not available, call the module-level `logger` with the same information.
- When to add debug traces:
  - Add `logger.debug(...)` calls around complex control-flow boundaries (e.g., `watch_index`, `on_list_view_highlighted`, `prep*` methods, repo discovery, and diff building) to capture input parameters and decisions.
  - Keep debug messages concise and include `get_caller_short()` only when you need a condensed stack trace for intermittent issues; avoid excessive stack logging in hot loops.
- Log contents guidance:
  - Include contextual identifiers (widget `id`, `displayed_path`, `current_diff_file`, commit hashes) where relevant to make `tmp/debug.log` actionable.
  - Avoid logging large blobs (full diffs or file contents) at debug level; instead log summaries (line counts, variant flags) and provide a separate mode to dump full content if needed.
- Developer workflow:
  - When diagnosing UI or timing issues, enable `DOLOGGING` and reproduce the problem to collect `tmp/debug.log` for analysis.
  - Use `self.printException` to surface exceptions; review `tmp/debug.log` for stack traces and add targeted debug lines where the log shows uncertainty.


Assumptions and axioms
- Axioms: follow the canonical guidance in "Error / logging expectations" (no bare `except:`; use `self.printException` for unexpected errors), prefer scheduled callbacks for DOM-dependent work (see 'Interplay and lifecycle') over polling, and use single-slot `save_state`/`restore_state` for temporary overlays.
- Highlight constants: `HIGHLIGHT_FILELIST_BG`/`HIGHLIGHT_FILELIST_STYLE` for FileList classes, and `HIGHLIGHT_REPOLIST_BG`/`HIGHLIGHT_REPOLIST_STYLE` for RepoList classes.
- Other assumptions: help rendered as Markdown; pseudo entries `STAGED`/`MODS` represent staged and unstaged changes; consistent highlight styling applied to ListItem and inner Label; layouts controlled by named states and `change_state` is immediate (no stack).

- Coding / DRY guideline: prefer shared helper methods over duplicated code. When behavior or logic would be identical between two concrete classes (for example, `FileModeRepoList` and `RepoModeRepoList`), move that logic into a common base such as `RepoListBase`. When functionality would otherwise be duplicated across both `FileListBase` and `RepoListBase`, implement it once on `AppBase` and call it from the subclasses. Before adding new code that looks similar to existing code elsewhere, refactor the existing implementation into a shared method and reuse it. This reduces bugs, centralizes exception/logging handling, and keeps the UI behavior consistent across modes.


- Assumptions: 
Git repo available for full functionality; 
terminal supports ANSI colors; Textual APIs available; 
pygit2 installed; working directory is repo or contains path argument; 
diff commands use `git` in PATH; 
highlight color may be adjusted via constant; 
ListView items contain `Label` children; 
focus/title labels exist; 
only one checked history item allowed.

