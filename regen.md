Regenerate gitdiffnavtool.py — step-by-step plan for Copilot
=============================================================

Purpose
- Provide a deterministic, testable plan Copilot can follow to generate a clean, from-scratch `gitdiffnavtool.py` implementation derived from `program-structure.md`.
- The plan is a sequence of numbered steps with explicit pause/test/validation points. After each step Copilot must stop and request confirmation before continuing.

How to use this with Copilot (instructions to the AI)
- Read this file fully before generating any code.
- Follow steps in order. After completing each numbered step produce a short completion message in the format:

  STEP <n> COMPLETE — RUN TESTS / READY FOR REVIEW

  Then stop and wait for one of the user's replies:
  - `CONTINUE` — proceed to next step
  - `REVISE: <brief reason>` — propose and apply a small change for this step, then re-output the completion message
  - `ABORT` — stop generation

- When producing code, only output the file contents (or apply patches) requested by the step. Do not bundle unrelated changes.
- Keep generated code minimal and well-structured, following `program-structure.md` conventions (constants, AppBase, FileListBase, HistoryListBase, DiffList, HelpList, GitHistoryNavTool, canonical ids, use of `call_after_refresh`, centralized logging via `printException`).

Canonical widget mapping (six canonical widgets):

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

Prerequisites (environment notes)
- Target Python >= 3.11; the workspace already contains a `venv-3.14`.
- Assume `textual`, `rich`, and `pygit2` are available in the environment where the app will be run.

Plan (steps with pause points)

1) Create repository scaffolding and constants
   - Create `gitdiffnavtool.py` header, imports, logging setup and constants block:
     - `HIGHLIGHT_FILELIST_BG`, `HIGHLIGHT_FILELIST_STYLE`, `HIGHLIGHT_REPOLIST_BG`, `HIGHLIGHT_REPOLIST_STYLE`, `DOLOGGING`
   - Add `__main__` guard and an argparse CLI stub with `--no-color`, `--repo-first` and positional `path`.
   - Add small `logger = logging.getLogger(__name__)` and `printException` module helper used when `self` is not available.
   - PAUSE: output the new `gitdiffnavtool.py` file and stop.
   - Tests/validation: run `python -m py_compile gitdiffnavtool.py` (or equivalent). Expect: no syntax errors.

2) Implement `AppBase` (subclassing `ListView`) and shared helpers
   - Implement `AppBase` minimal class with:
     - __init__ defaults for `_min_index`, `_populated`, `_filename`, `current_prev_sha`, `current_commit_sha`, `current_diff_file`.
     - `printException(self, e, msg=None)` method that logs and writes tracebacks to `tmp/debug.log` when `DOLOGGING`.
     - `text_of(node)` and `_extract_label_text(lbl)` helpers as in program-structure.
   - Implement navigation handling using `key_` methods (no `on_key` dispatcher). Do not implement `more_keys`.
     - Use `call_after_refresh` for page/home/end behavior where DOM-update scheduling is needed.
   - PAUSE: present `AppBase` portion only.
   - Tests/validation: py_compile and a small snippet that imports the module and inspects `AppBase` attributes. (Provide instruction for the user to run if they want.)

3) Implement `FileListBase` and its two concrete derived classes
   - `FileListBase` responsibilities: `on_focus`, `_highlight_filename`, `_highlight_top`, `watch_index`, `on_list_view_highlighted`, `_child_filename`, `_enter_directory`.
   - Concrete classes:
     - `FileModeFileList(FileListBase)` with `prepFileModeFileList(path)`, `key_left`, `key_right` (enter directory/open history).
     - `RepoModeFileList(FileListBase)` with `prepRepoModeFileList(prev_hash, curr_hash)`, `key_left`, `key_right` (show diff).
   - Use `call_after_refresh` to schedule DOM-dependent index/scroll updates.
   - PAUSE: present these classes and stop.
   - Tests/validation: import and verify class MRO and that methods exist; run py_compile.

4) Implement `HistoryListBase` and its concrete history lists
   - `HistoryListBase` methods: `toggle_check_current`, `compute_commit_pair_hashes`, `on_focus`, `on_list_view_highlighted`.
   - Concrete classes: `FileModeHistoryList(HistoryListBase)` and `RepoModeHistoryList(HistoryListBase)` with `prep*` methods that stub external `git`/`pygit2` calls (for now return fixed example rows).
   - Attach `_hash` and `_raw_text` metadata to ListItem rows.
   - PAUSE: present these classes and stop.
   - Tests/validation: verify check toggling logic and MRO; py_compile.

5) Implement `DiffList` and `HelpList`
   - `DiffList(AppBase)` with `prepDiffList(filename, prev, curr, variant_index=None)`, colorization toggle, `key_c`, `key_d`, `key_f` handlers, and safe rendering into the ListView (append ListItem(Label(Text(...))))
   - `HelpList(AppBase)` should render `HELP_TEXT` as Rich Markdown blocks split into ListItems and implement a `key_` handler to restore state via `self.app.restore_state()`.
   - PAUSE: present these classes and stop.
   - Tests/validation: py_compile and inspect that `prepDiffList` runs for a small synthetic diff string.

6) Implement main `GitHistoryNavTool` App
   - Subclass `textual.app.App` and implement `compose()`, `on_mount()`, `build_repo_cache()` (pygit2 discovery can be stubbed to work without a repo), `_apply_column_layout`, `change_state`, `change_focus`, `save_state`, `restore_state`, `build_diff_cmd`.
   - Wire canonical widget ids and title labels; create footer Text objects referenced by `change_state` calls.
   - Implement CLI-driven startup mode (`--repo-first`) behavior: call initial `prep*` methods accordingly.
   - PAUSE: present the main App class and stop.
   - Tests/validation: py_compile; run a dry startup `python gitdiffnavtool.py --no-color .` (user should run in terminal). App should start without immediate exceptions (TUI not validated here).

   Additions/implementation notes (recent revisions):
   - The `GitHistoryNavTool` constructor should accept CLI values and set them on the app instance: `__init__(self, path: str = '.', no_color: bool = False, repo_first: bool = False, **kwargs)` so the app can inspect startup options during `on_mount()`.
   - Inline CSS is stored in the module as `INLINE_CSS` and assigned to the app via `GitHistoryNavTool.CSS = INLINE_CSS` (no external `CSS_PATH`).
   - `compose()` should build the six canonical widgets and title labels (left-file, left-history, right-history, right-file, diff, help) using the canonical ids described elsewhere (e.g. `left-file-list`, `left-file-title`, etc.).
   - `on_mount()` should resolve widgets by their canonical ids (querying `#left-file-list`, `#right-history-list`, `#diff-list`, ...) and call the appropriate `prep*` methods (for the initial path or repo-first flow).
   - `main()` should instantiate the app passing CLI args into the constructor and call `app.run()` (example: `GitHistoryNavTool(path=args.path, no_color=args.no_color, repo_first=args.repo_first).run()`).
   - Tests/validation: in addition to `py_compile`, verify that `main()` starts the app via `python3 gitdiffnavtool.py --no-color .` and that the app's `path`, `no_color`, and `repo_first` attributes reflect the CLI flags.

7) Replace stubs with real git/pygit2 interactions
   - Implement file-system listing, repo status map, `prepFileModeFileList` full implementation, `prepFileModeHistoryList` using `git log --follow`, `prepRepoModeHistoryList` using pygit2 walker or `git log`, and `prepRepoModeFileList` using pygit2/tree diff or `git diff --name-only` for pseudo-hashes.
   - Implement `build_diff_cmd` and `prepDiffList` to call `git diff` and colorize output by line prefix. Respect `DOLOGGING` and limit debug output in logs.
   - PAUSE: provide the updated methods and stop.
   - Tests/validation: run sample commands to fetch history for a known file, and run `gitdiffnavtool.py <file>` to confirm history populates. Collect `tmp/debug.log` if failures occur.

8) Final polish, exception-safety, and documentation
   - Sweep to replace bare `except:` with `except Exception as e:` and call `self.printException(e, "<context>")`.
   - Add/verify constants naming consistency and docstring header.
   - Add inline comments that map back to `program-structure.md` sections where appropriate.
   - PAUSE: present the final `gitdiffnavtool.py` file and tests.
   - Tests/validation: full import/py_compile and manual run; collect logs.

Validation guidance (commands for user)
- Syntax check:

```bash
python -m py_compile gitdiffnavtool.py
```

- Quick import test (in Python REPL):

```python
import importlib.util
spec = importlib.util.spec_from_file_location('g', 'gitdiffnavtool.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.AppBase)
```

- Run app (manual observation required):

```bash
python gitdiffnavtool.py --no-color .
```

How to instruct Copilot to follow this regen plan
- Copy the entire contents of `regen.md` into the prompt area for Copilot (or give Copilot the path and ask it to read it).
- Prepend an explicit instruction, exactly worded like this:

  "You are Copilot. Read `regen.md` and follow the numbered steps. After each step output a single-line completion token exactly as specified (STEP <n> COMPLETE — RUN TESTS / READY FOR REVIEW) and then wait for the user's `CONTINUE` or `REVISE:` or `ABORT` reply before proceeding. Do not proceed automatically."

- If you want Copilot to run faster without human pauses, replace the single-line completion rule with:

  "After each step, proceed automatically after generating code unless the user replies with `STOP`."

- To request a partial re-run/refactor, reply to Copilot with `REVISE: <reason>` and it should update only the code for that step.

Notes and constraints
- Keep generated files minimal and modular; prefer small helper functions and clear separation of concerns matching `program-structure.md`.
- Avoid runtime integration tests here — the plan uses manual pause points so you can run the app in your environment and provide logs.
- If network access or native git interactions fail in the environment, Copilot should provide clear stubs and comments where the user must run the commands locally.


End of regen.md
