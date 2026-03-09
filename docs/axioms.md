# Must Follow Axioms


- Prefer direct attribute access when attributes are guaranteed to exist (e.g., `self.app.current_path` and `self.current_prev_sha`) rather than using `getattr` calls when reasonable. To decide if the attribute is guaranteed to exist, consider as safe any attribute defined in the public interface of the class, given a value in `__init__()`, `on_mount()` and `argparse` options.
- Defensive programming: for other attribute access, guard operations with try/except, use safe getters like `getattr(nodes, 'links', None)`, and keep syntax/indentation correct (run `python -m py_compile` to verify changes compile).
- There should be NO bare `except:` handlers anywhere in the code.
- There should be NO `except Exception:` handlers that omit the exception binding (i.e. use `except Exception as e:` not `except Exception:`).
- Every `except Exception as <var>:` must reference the exception variable in a subsequent call to either `self.printException(<var>, ...)` or the module-level `printException(<var>, ...)` within the next few lines (the checker uses an 8-line lookahead heuristic). This ensures exceptions are logged with context.
- Calls to `self.printException()` or `printException()` do NOT need to be wrapped in additional `try/except` blocks.
- Avoid redundant nested `try`/`except` blocks where an outer `try` contains a single inner `try` (both with handlers); this pattern often indicates an accidental extra `try` and is flagged by the checker.

# Checks enforced by `scripts/check_axioms.py`
- No bare `except:` occurrences.
- No `except Exception:` without `as <var>`.
- Every `except Exception as <var>:` must reference the exception variable in a subsequent call to `.printException(` or `printException(` within the next ~8 lines.
- Run `python -m py_compile` on all `.py` files; fix any syntax/parse errors.
- Detect redundant nested `try`/`except` where an outer `try` contains a single inner `try` and both have handlers.

# Functional Axioms for Co-Pilot
- After making changes, always run `python -m py_compile` to verify that the changes compile.
- Run `scripts/check_axioms.py` locally before committing to ensure your changes conform to the repository's style and safety checks.

