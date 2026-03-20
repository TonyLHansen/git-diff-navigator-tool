---
description: "Python-specific coding rules and axioms for this repository. Applies to all .py files."
applyTo:
  - "**/*.py"
---

# Python Instructions (Repository-specific)

This instruction set applies to all Python files in this repository. Keep it concise and actionable.

## Formatting
- Use `black` with a line length of 120 characters.
- Run locally or via the provided Make target:

```bash
make run-black
```

Keep edits within 120 columns where practical to reduce unrelated diffs.

## Axioms and Exception Handling
- Follow the repository axioms documented in `docs/axioms.md`.
- Key rules:
  - Do not use bare `except:`.
  - Always bind exceptions: `except Exception as e:` and call `printException(e, "context")`.
  - Add docstrings for non-trivial functions and methods.

Validate axioms after changes:

```bash
venv-3.14/bin/python scripts/check_axioms.py
```

## Testing
- Run focused tests relevant to modified files first, then run the full suite:

```bash
# targeted
venv-3.14/bin/python -m pytest tests/test_gitrepo.py -v
# full
make test
```

## GitRepo vs UI Separation
- Keep `gitrepo.py` UI-agnostic (no Textual widgets or rendering logic).
- Keep rendering, markers, and Textual interactions inside `gitdiffnavtool.py`.
- Attach metadata to `ListItem` rows (e.g., `_raw_text`, `_filename`, `_repo_status`, `_hash`, `_is_dir`) rather than parsing label strings.

## DOM/Refresh Tips (Textual)
- For layout or focus changes that depend on mounted widgets, schedule follow-up work via `call_after_refresh()` to avoid race conditions.

## Example Prompts
- "Add a regression test for `FileModeFileList._collect_filemode_nodes` to ensure staged-new files show `A` marker."
- "Refactor `gitrepo.py` method X into a helper and keep 100% coverage for `gitrepo.py`."

---

If you want, I can expand this into rules for linters or pre-commit hooks. Let me know.