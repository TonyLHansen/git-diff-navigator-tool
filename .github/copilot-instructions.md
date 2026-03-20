# Project Guidelines

## Code Style
- Use Python 3.14-compatible code.
- Keep edits focused and local; preserve existing naming and architecture patterns.
- Follow project axiom rules:
  - No bare `except:`.
  - Prefer `except Exception as e:` and call `printException(e, "context")` when handling exceptions.
  - Add docstrings for non-trivial methods.
- Keep `gitrepo.py` UI-agnostic: no Textual widgets, no display formatting.
- Keep UI formatting/status markers in `gitdiffnavtool.py`.
 - Formatting: use `black` with a line length of 120 characters. The repo provides a make target:
   - `make run-black` — runs `black -l 120` across the primary source files.
   - Prefer keeping changes within the 120-column limit to minimize unrelated diffs.
 - Axioms and style rules: see `docs/axioms.md` for the project-specific axioms (exception handling, required docstrings, and other enforced conventions). Refer to that document when making Python changes.

## Architecture
- `gitrepo.py`:
  - Git CLI wrapper and cache layer.
  - Provides normalized history/file-status APIs used by UI.
- `gitdiffnavtool.py`:
  - Textual TUI app and widget classes.
  - Owns layout changes, focus handling, key handlers, and row rendering.
- `tests/`:
  - `tests/test_gitrepo.py` for repository/data layer behavior.
  - `tests/test_appbase_key_w.py` and related files for UI/key behavior.
- `scripts/check_axioms.py`:
  - Enforces project-specific structural/error-handling conventions.
- `scripts/create_test_repo.sh`:
  - Generate a set of test repositories that have branches and variations on no staged files, modified files, staged files, staged and modified files

## Build And Test
- Run full tests:
  - `make test`
- Run gitrepo-focused tests:
  - `make coverage`
- Run targeted test file during iteration:
  - `venv-3.14/bin/python -m pytest tests/test_appbase_key_w.py -v`
  - `venv-3.14/bin/python -m pytest tests/test_gitrepo.py -v`
- Validate axioms after code edits:
  - `venv-3.14/bin/python scripts/check_axioms.py`
- Optional formatting:
  - `make run-black`

## Conventions
- Status rendering (file rows) is marker-based and must stay consistent:
  - `staged -> A`, `modified -> M`, `untracked -> U`, `ignored -> I`, `wt_deleted -> D`, `conflicted -> !`.
- Preserve overlay precedence in file-mode list generation:
  - baseline committed -> untracked -> staged -> ignored -> mods (with explicit edge-case handling where staged-new files should remain staged).
- Attach and use row metadata (`_raw_text`, `_filename`, `_repo_status`, `_hash`, `_is_dir`) instead of parsing rendered label text.
- In Textual layout/selection changes, prefer post-refresh scheduling (`call_after_refresh`) for DOM-dependent operations.
- Keep mode semantics intact:
  - File-first mode and repo-first mode have different list ownership and key behaviors.
  - Do not move key handlers between widgets unless tests are updated accordingly.

## Safe Change Workflow
- For small changes:
  1. Run the most relevant targeted pytest file.
  2. Run `venv-3.14/bin/python scripts/check_axioms.py`.
  3. Run `make test` before finalizing.
- For behavior changes in file/history rendering:
  - Add or update regression tests in `tests/test_appbase_key_w.py`.
- For `gitrepo.py` changes:
  - Add/update tests in `tests/test_gitrepo.py` and keep coverage at 100% for `gitrepo.py`.
