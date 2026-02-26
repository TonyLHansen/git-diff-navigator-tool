# Must Follow Axioms

- Prefer direct attribute access when attributes are guaranteed to exist (e.g., `self.app.current_path` and `self.current_prev_sha`) rather than using `getattr` calls when reasonable. To decide if the attribute is guaranteed to exist, consider as safe any attribute defined in the public interface of the class, given a value in `__init__()`, `on_mount()` and `argparse` options.
- Defensive programming: for other attribute access, guard operations with try/except, use safe getters like `getattr(nodes, 'links', None)`, and keep syntax/indentation correct (run `python -m py_compile` to verify changes compile).
- There should be NO silent `except Exception:` blocks in any of the classes; always capture the Exceptions of any `try/except` block used in the class methods, as in `except Exception as e:`. Vary the name of the variable capturing the exception when in nested `try/except` blocks.
- Every `except Exception as e:` should be followed a call to `self.printException(e,"message about the cause")`. 
- Calls to `self.printException()` do NOT need to be surrounded by `try/except` block.
- A `pass` after `except Exception as var` is not needed if there is another statement present (such as `printException()`)

# Functional Axioms for Co-Pilot
- After making changes, always run `python -m py_compile` to verify that the changes compile.