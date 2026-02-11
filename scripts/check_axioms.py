#!/usr/bin/env python3
"""check_axioms.py

Lightweight checker that enforces a few project-specific axioms listed
in `axioms.md`.

Checks implemented:
- No bare `except:` occurrences.
- No `except Exception:` without `as <var>`.
- Every `except Exception as <var>:` must reference the exception
  variable in a subsequent call to `.printException(` or `printException(`
  within the next N lines.
- Run `python -m py_compile` on all .py files and surface failures.
 - Detect redundant nested `try`/`except` where an outer `try` contains
     a single inner `try` (both with handlers) which is likely accidental.

Exit code: 0 if no violations, 1 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import traceback
import logging
from pathlib import Path
from typing import List, Tuple, Optional


# Default scanning root: current working directory. This makes the tool
# behave as a general-purpose checker that defaults to where it's run.
# Users may still override with `--root`.
ROOT = Path.cwd()
PY_EXT = "*.py"

# How many lines after an except to search for a printException call
EXCEPT_LOOKAHEAD = 8

logger = logging.getLogger(__name__)

def printException(e: Exception, msg: Optional[str] = None) -> None:
    """Module-level helper to log unexpected exceptions when `self` isn't available.

    Mirrors the helper used in the main app so outputs are consistent.
    """
    try:
        short_msg = msg or ""
        logger.warning("%s: %s", short_msg, e)
        logger.warning(traceback.format_exc())
    except Exception as _use_stderr:
        sys.stderr.write(f"printException fallback: {e}\n")
        sys.stderr.write(f"secondary exception: {_use_stderr}\n")


def load_source_and_ast(path: Path) -> Tuple[str, Optional[ast.AST]]:
    """Read a file and return (source_text, parsed_ast) or (text, None) on failure.

    This centralizes reading and parsing so callers can avoid repeated
    parse attempts and gracefully handle parse failures.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        printException(e, f"reading {path}")
        return "", None
    try:
        tree = ast.parse(text, filename=str(path))
    except Exception as e:
        printException(e, f"parsing {path}")
        return text, None
    return text, tree


def list_py_files(root: Path) -> List[Path]:
    """Return a list of Python files under `root` (heuristic).

    Includes files ending in `.py` and files with a Python shebang.
    Skips common virtualenv/site-packages and backup files.
    """
    files: List[Path] = []

    # Also include files that have a python shebang (#!...python) even if
    # they don't end with .py. Use os.walk with directory pruning to avoid
    # descending into large virtualenv/site-packages/__pycache__ trees.
    shebang_re = re.compile(r"^#!.*python")
    exclude_names = {"__pycache__", ".git", "build", "dist", "tmp"}

    for dirpath, dirnames, filenames in os.walk(root):
        try:
            # Prune directories in-place so os.walk won't descend into them.
            kept = []
            for d in dirnames:
                dl = d.lower()
                if dl in exclude_names or dl.startswith("venv") or dl.startswith(".venv") or dl.startswith("env") or "site-packages" in dl:
                    logger.debug("skipping directory during walk: %s", Path(dirpath) / d)
                    continue
                kept.append(d)
            dirnames[:] = kept

            for fname in filenames:
                p = Path(dirpath) / fname
                try:
                    logger.debug("considering path: %s", p)
                    if not p.is_file():
                        continue
                    parts_lower = [part.lower() for part in p.parts]
                    skip = False
                    for part in parts_lower:
                        if part in exclude_names or part.startswith("venv") or part.startswith(".venv") or part.startswith("env") or "site-packages" in part:
                            skip = True
                            break
                    if skip:
                        logger.debug("skipping path (excluded): %s", p)
                        continue
                    name = p.name.lower()
                    # Keep a light heuristic to ignore backup files named like 'old'
                    if "old." in name or name.startswith("old") or name.endswith("~"):
                        logger.debug("ignoring backup/temp file: %s", p)
                        continue
                    if p.suffix.lower() == ".py":
                        logger.debug("including python file: %s", p)
                        files.append(p)
                        continue

                    # read only first line cheaply
                    try:
                        with p.open("rb") as fh:
                            first = fh.readline(200).decode("utf-8", errors="ignore")
                    except Exception as e:
                        printException(e, f"reading first line of {p}")
                        continue
                    if shebang_re.search(first):
                        logger.debug("including shebang-marked file: %s", p)
                        files.append(p)
                except Exception as e:
                    printException(e, f"scanning file {p}")
                    continue
        except Exception as e:
            printException(e, f"walking directory {dirpath}")
            continue
    return files


def is_python_file(p: Path) -> bool:
    """Return True if `p` is a Python file by extension or shebang.

    This mirrors the heuristic used by `list_py_files` so callers can
    easily decide whether to treat a supplied path as Python source.
    """
    try:
        if p.suffix.lower() == ".py":
            return True
        if not p.is_file():
            return False
        with p.open("rb") as fh:
            first = fh.readline(200).decode("utf-8", errors="ignore")
        if re.search(r"^#!.*python", first):
            return True
    except Exception as e:
        printException(e, f"is_python_file failed for {p}")
        return False
    return False


def _find_bare_except_locations(path: Path, text: str, tree: ast.AST) -> List[tuple[int, bool]]:
    """Return list of (lineno, in_class) for bare `except:` handlers in AST.

    `in_class` is True when the except is inside a ClassDef (i.e., a method),
    False otherwise.
    """

    results: List[tuple[int, bool]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: List[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stack.append("class")
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append("func")
            self.generic_visit(node)
            self.stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.stack.append("func")
            self.generic_visit(node)
            self.stack.pop()

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            # ast.ExceptHandler.type is None for bare `except:`
            if node.type is None:
                in_class = "class" in self.stack
                lineno = getattr(node, "lineno", None)
                if lineno is not None:
                    results.append((lineno, in_class))
            self.generic_visit(node)

    Visitor().visit(tree)
    return results


def _find_except_without_name_locations(path: Path, text: str, tree: ast.AST) -> List[int]:
    """Return line numbers for ExceptHandler nodes that specify an
    exception type but do not bind it with `as <var>`.
    """

    results: List[int] = []

    class Visitor(ast.NodeVisitor):
        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            # node.type is not None for 'except Name:' and node.name is None
            # when there is no 'as var'. Record those line numbers.
            if node.type is not None:
                name = getattr(node, "name", None)
                if name is None:
                    lineno = getattr(node, "lineno", None)
                    if lineno is not None:
                        results.append(lineno)
            self.generic_visit(node)

    Visitor().visit(tree)
    return results


def _collect_self_assigned_attrs(path: Path, text: str, tree: ast.AST) -> dict:
    """Collect attributes assigned to `self` in `__init__` or `on_mount` per class.

    Returns mapping class_name -> set(attribute names)
    """
    classes: dict = {}

    class Collector(ast.NodeVisitor):
        def __init__(self):
            self.current_class = None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.current_class = node.name
            classes.setdefault(node.name, set())
            # scan __init__ and on_mount in this class
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in ("__init__", "on_mount"):
                    for stmt in ast.walk(item):
                        if isinstance(stmt, ast.Assign):
                            for target in stmt.targets:
                                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                                    classes[node.name].add(target.attr)
                        elif isinstance(stmt, ast.AnnAssign):
                            target = stmt.target
                            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                                classes[node.name].add(target.attr)
            self.generic_visit(node)
            self.current_class = None

    Collector().visit(tree)
    return classes


def _find_getattr_on_self(path: Path, text: str, tree: ast.AST) -> List[tuple[int, str, str]]:
    """Find usages of getattr(self, 'attr', ...) and return list of (lineno, attrname, func).

    `func` is the function name used ('getattr'). Does not currently
    attempt to resolve dynamic attribute names.
    """

    results: List[tuple[int, str]] = []

    # Some Python versions don't expose ast.Str (strings are ast.Constant).
    has_ast_Str = hasattr(ast, "Str")
    str_node_types = (ast.Constant, ast.Str) if has_ast_Str else (ast.Constant,)

    class Finder(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            # match getattr(self, 'attr', ...)
            try:
                # match getattr(self, 'attr', ...)
                if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
                    first = node.args[0]
                    second = node.args[1]
                    if isinstance(first, ast.Name) and first.id == "self" and isinstance(second, str_node_types):
                        if has_ast_Str and isinstance(second, ast.Str):
                            attr_name = second.s
                        else:
                            attr_name = getattr(second, "value", None)
                        if isinstance(attr_name, str):
                            lineno = getattr(node, "lineno", None)
                            if lineno is not None:
                                results.append((lineno, attr_name, node.func.id))
                # also match getattr(self.attr, 'name', ...) when first arg is Attribute like self.app
                if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
                    first = node.args[0]
                    second = node.args[1]
                    if isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name) and first.value.id == "self":
                        # getattr(self.something, 'attr', ...) or hasattr(self.something, 'attr')
                        if has_ast_Str and isinstance(second, ast.Str):
                            attr_name = second.s
                        else:
                            attr_name = getattr(second, "value", None)
                        if isinstance(attr_name, str):
                            lineno = getattr(node, "lineno", None)
                            if lineno is not None:
                                results.append((lineno, f"{first.attr}.{attr_name}", node.func.id))
            except Exception as e:
                printException(e, f"error walking AST for getattr detection in {path}")
            self.generic_visit(node)

    Finder().visit(tree)
    return results


def _find_parse_args_targets(path: Path, text: str, tree: ast.AST) -> set:
    """Return set of variable names assigned from a call to `*.parse_args()`.

    e.g. `args = parser.parse_args()` -> returns {'args'}
    """

    targets: set = set()

    class Finder(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            try:
                val = node.value
                if isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute) and val.func.attr == "parse_args":
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            targets.add(t.id)
            except Exception as e:
                printException(e)
            self.generic_visit(node)

    Finder().visit(tree)
    return targets


def _find_getattr_on_vars(path: Path, varnames: set, text: str, tree: ast.AST) -> List[tuple[int, str, str, str]]:
    """Find usages of getattr(var, 'attr', ...) where var is in varnames.

    Returns list of (lineno, varname, attrname, func) where `func` is 'getattr'.
    """

    results: List[tuple[int, str, str]] = []

    class Finder(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            try:
                if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
                    first = node.args[0]
                    second = node.args[1]
                    if isinstance(first, ast.Name) and first.id in varnames:
                        # second may be Constant or Str
                        if (hasattr(ast, "Str") and isinstance(second, ast.Str)) or isinstance(second, ast.Constant):
                            if hasattr(second, "s"):
                                attr = second.s
                            else:
                                attr = getattr(second, "value", None)
                            if isinstance(attr, str):
                                lineno = getattr(node, "lineno", None)
                                if lineno is not None:
                                    results.append((lineno, first.id, attr, node.func.id))
            except Exception as e:
                printException(e)
            self.generic_visit(node)

    Finder().visit(tree)
    return results


def check_file(
    path: Path,
    text: str,
    tree: ast.AST,
    check_global_excepts: bool,
    check_bare_excepts: bool,
    check_except_as_print: bool,
    check_pass: bool,
    check_logger_in_try: bool,
    check_printexception_in_try: bool,
    call_example: str,
) -> List[Tuple[str, int, str]]:
    """Run the core AST-based checks for bare excepts, except-as-print, and related checks.

    Returns a list of (filepath, lineno, message) tuples for violations detected
    within this file's AST.
    """
    errs: List[Tuple[str, int, str]] = []
    lines = text.splitlines()

    # Detect bare excepts (only inside classes by default)
    try:
        bare_locations = _find_bare_except_locations(path, text=text, tree=tree)
    except Exception as e:
        printException(e, f"finding bare excepts in {path}")
        bare_locations = []

    if check_bare_excepts:
        for lineno, in_class in bare_locations:
            if in_class or check_global_excepts:
                errs.append((str(path), lineno, "bare 'except:' detected"))
        # Also flag `except Name:` (type present but no `as var`)
        try:
            no_name_linenos = _find_except_without_name_locations(path, text=text, tree=tree)
        except Exception as e:
            printException(e)
            no_name_linenos = []
        for lineno in no_name_linenos:
            errs.append((str(path), lineno, f"'except [<type>]:' without 'as <var>' detected. Add the 'as <var>' and a {call_example} call (without its own try/except)."))

    # Detect 'except Exception:' without 'as' and ensure except-as blocks
    # reference the exception variable in a subsequent printException call.
    # These checks are gated by `check_except_as_print` so they can be
    # disabled independently.
    if check_except_as_print:
        # Use AST-based detection: for each ExceptHandler that binds a
        # name (`except Type as var:`), ensure the handler body contains
        # a call to `printException(var, ...)` or `.printException(..., var)`.
        try:
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    name = getattr(node, "name", None)
                    if name is None:
                        continue
                    # `_use_stderr` intentionally writes to stderr; names
                    # starting with `_use_pass` indicate the handler
                    # intentionally contains only a `4` and shouldn't be
                    # required to call `printException`.
                    if (
                        isinstance(name, str)
                        and (
                            name.startswith("_use_stderr")
                            or name.startswith("_use_pass")
                            or name.startswith("_no_logging")
                            or name.startswith("_use_raise")
                        )
                    ):
                        continue
                    # Search the except body for a Call that calls
                    # printException (either as Name or attribute) with
                    # the exception variable as an argument.
                    found = False
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call):
                            func = sub.func
                            is_print = False
                            if isinstance(func, ast.Name) and func.id == call_example:
                                is_print = True
                            elif isinstance(func, ast.Attribute) and func.attr == call_example:
                                is_print = True
                            if not is_print:
                                continue
                            # Check arguments for a Name matching the exception
                            for a in list(sub.args) + list(getattr(sub, 'keywords', [])):
                                # keywords are ast.keyword; check .arg for name and .value for expression
                                if isinstance(a, ast.keyword):
                                    val = a.value
                                else:
                                    val = a
                                if isinstance(val, ast.Name) and val.id == name:
                                    found = True
                                    break
                            if found:
                                break
                    if not found:
                        lineno = getattr(node, "lineno", None) or 0
                        # Determine a printable type name if possible
                        typ = None
                        tnode = getattr(node, "type", None)
                        if isinstance(tnode, ast.Name):
                            typ = tnode.id
                        elif isinstance(tnode, ast.Attribute):
                            # e.g. module.Error
                            parts = []
                            cur = tnode
                            while isinstance(cur, ast.Attribute):
                                parts.append(cur.attr)
                                cur = cur.value
                            if isinstance(cur, ast.Name):
                                parts.append(cur.id)
                                typ = ".".join(reversed(parts))
                        typ = typ or "<type>"
                        msg = f"'except {typ} as {name}:' not followed by {call_example}({name}) or self.{call_example}({name}, ...) in handler body. That {call_example}() does NOT in turn require its own try/except."
                        # If the except body is a single `pass`, give a more
                        # actionable message suggesting replacement.
                        try:
                            body = getattr(node, "body", []) or []
                            if len(body) == 1 and isinstance(body[0], ast.Pass):
                                msg += f". Replace the 'pass' with {call_example}"
                        except Exception as e:
                            printException(e, f"inspecting except body in {path}")
                        errs.append((str(path), lineno, msg))
        except Exception as e:
            printException(e, f"parsing AST for except-as-print in {path}")

    # Additional check: flag unnecessary `pass` statements inside
    # `except ... as var:` blocks when other statements are present.
    try:
        if check_pass:
            try:
                errs += check_unnecessary_pass_in_except(path, text=text, tree=tree)
            except NameError as e:
                printException(e, f"check_unnecessary_pass_in_except not available yet for {path}")
            except Exception as e:
                printException(e, f"checking unnecessary pass in except for {path}")
    except Exception as e:
        printException(e)

    # Detect try/except handlers whose body only contains a
    # single logger.<method>(...) call. In such cases the try/except adds
    # little value and can be removed around the logger call.
    if check_logger_in_try:
        try:
            errs += check_logger_in_try_blocks(path, text=text, tree=tree)
        except NameError as e:
            printException(e, f"check_logger_in_try_blocks not available yet for {path}")
        except Exception as e:
            printException(e, f"checking logger-in-try in {path}")

    if check_printexception_in_try:
        try:
            errs += check_printexception_in_try_blocks(path, text=text, tree=tree)
        except NameError as e:
            printException(e, f"check_printexception_in_try_blocks not available yet for {path}")
        except Exception as e:
            printException(e, f"checking printexception-in-try in {path}")

    return errs


def check_printexception_in_try_blocks(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """Detect try/except blocks where the try body contains only a single
    `printException(...)` call. In that case the surrounding try/except is
    likely unnecessary and can be removed.

    Returns list of error messages with line numbers pointing to the `try`.
    """
    errs: List[Tuple[str, int, str]] = []

    try:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            # must have except handlers
            if not getattr(node, "handlers", None):
                continue
            body = getattr(node, "body", []) or []
            if len(body) != 1:
                continue
            stmt = body[0]
            call_node = None
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call_node = stmt.value
            elif isinstance(stmt, ast.Assign) and isinstance(getattr(stmt, "value", None), ast.Call):
                call_node = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(getattr(stmt, "value", None), ast.Call):
                call_node = stmt.value
            if call_node is None:
                continue
            func = call_node.func
            is_print = False
            if isinstance(func, ast.Name) and func.id == "printException":
                is_print = True
            elif isinstance(func, ast.Attribute) and func.attr == "printException":
                is_print = True
            if not is_print:
                continue
            lineno = getattr(node, "lineno", None)
            logger.debug("printexception-in-try match at %s", lineno)
            if lineno is not None:
                errs.append(
                    (str(path), lineno, "try/except wraps single printException(...); remove the try/except around the printException call")
                )
    except Exception as e:
        printException(e, f"walking Try in {path} for printexception-in-try")

    return errs


def check_unnecessary_pass_in_except(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """Find `pass` statements inside `except ... as var:` blocks when other statements are present.

    Reports each `pass` statement's line number when the except-handler body
    contains at least one `pass` and at least one other statement.
    """
    errs: List[Tuple[str, int, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            try:
                name = getattr(node, "name", None)
                # Only consider except blocks that bind the exception
                if name is None:
                    return
                body = getattr(node, "body", []) or []
                # If body has only a single stmt, nothing to do
                if len(body) <= 1:
                    return
                # If there's at least one non-Pass node and at least one Pass, flag Pass lines
                has_nonpass = any(not isinstance(s, ast.Pass) for s in body)
                if not has_nonpass:
                    return
                for s in body:
                    if isinstance(s, ast.Pass):
                        lineno = getattr(s, "lineno", None)
                        if lineno is not None:
                            errs.append(
                                (str(path), lineno, f"unnecessary 'pass' in except block that also contains other statements (e.g., {call_example}())")
                            )
            except Exception as e:
                printException(e, f"walking ExceptHandler in {path}")
            self.generic_visit(node)

    Visitor().visit(tree)
    return errs


def check_logger_in_try_blocks(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """Detect try/except blocks where the try body contains only a single
    `logger.<method>(...)` call. In that case the surrounding try/except is
    likely unnecessary and can be removed.

    Returns list of error messages with line numbers pointing to the `try`.
    """
    errs: List[Tuple[str, int, str]] = []

    # Simpler, robust implementation: walk Try nodes and apply the predicate
    try:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            # must have except handlers
            if not getattr(node, "handlers", None):
                continue
            body = getattr(node, "body", []) or []
            if len(body) != 1:
                continue
            stmt = body[0]
            call_node = None
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call_node = stmt.value
            elif isinstance(stmt, ast.Assign) and isinstance(getattr(stmt, "value", None), ast.Call):
                call_node = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(getattr(stmt, "value", None), ast.Call):
                call_node = stmt.value
            if call_node is None:
                continue
            func = call_node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "logger":
                lineno = getattr(node, "lineno", None)
                method = getattr(func, "attr", "<method>")
                logger.debug("logger-in-try match at %s method=%s", lineno, method)
                if lineno is not None:
                    errs.append(
                        (str(path), lineno, f"try/except wraps single logger.{method}(...); remove the try/except around the logger call")
                    )
    except Exception as e:
        printException(e, f"walking Try in {path} for logger-in-try")

    return errs


def check_redundant_nested_try(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """Detect nested `try`/`except` where the outer `try` body is a single
    inner `try` (both having except handlers). This pattern is usually
    redundant (an accidental duplication of handlers) and should be merged.

    Returns list of (path, lineno, msg) with lineno pointing to the outer
    `try` node.
    """
    errs: List[Tuple[str, int, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit_Try(self, node: ast.Try) -> None:
            try:
                body = getattr(node, "body", []) or []
                # If the try body's sole statement is another Try and both
                # the inner and outer Try nodes have exception handlers,
                # flag this as likely redundant.
                if len(body) == 1 and isinstance(body[0], ast.Try):
                    inner = body[0]
                    if getattr(node, "handlers", None) and getattr(inner, "handlers", None):
                        lineno = getattr(node, "lineno", None)
                        if lineno is not None:
                            errs.append(
                                (
                                    str(path),
                                    lineno,
                                    "redundant nested try/except: outer try contains only an inner try with handlers; merge handlers and remove redundant outer try",
                                )
                            )
            except Exception as e:
                printException(e, f"walking Try in {path} for redundant nested try")
            self.generic_visit(node)

    Visitor().visit(tree)
    return errs


def check_imports_module_level(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """Flag Import/ImportFrom nodes that are not at module top-level.

    Allows imports inside `if TYPE_CHECKING:` blocks.
    """
    errs: List[Tuple[str, int, str]] = []

    def if_is_type_checking(node: ast.If) -> bool:
        t = getattr(node, "test", None)
        # NAME: TYPE_CHECKING
        if isinstance(t, ast.Name) and t.id == "TYPE_CHECKING":
            return True
        # attr: typing.TYPE_CHECKING
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.attr == "TYPE_CHECKING":
            return True
        return False

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: List[ast.AST] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.stack.append(node)
            super().generic_visit(node)
            self.stack.pop()

        def _is_in_non_module(self) -> bool:
            """Return True when the current node stack indicates non-module scope.

            Treats TYPE_CHECKING `if` blocks and module top-level as module scope.
            """
            # Consider ancestors other than ast.Module and TYPE_CHECKING ifs.
            non_module_ancs: List[ast.AST] = []
            for anc in self.stack:
                if isinstance(anc, ast.Module):
                    continue
                if isinstance(anc, ast.If) and if_is_type_checking(anc):
                    continue
                non_module_ancs.append(anc)

            # No non-module ancestors -> module-level
            if not non_module_ancs:
                return False

            # Allow imports inside a module-level `try:` or inside its `except:`
            # handlers. That corresponds to non-module ancestors being a Try and
            # optionally an ExceptHandler. If there are other non-module
            # ancestors (e.g., a function or class), treat as non-module.
            allowed = all(isinstance(a, (ast.Try, ast.ExceptHandler)) for a in non_module_ancs)
            if allowed:
                return False

            return True

        def visit_Import(self, node: ast.Import) -> None:
            try:
                if self._is_in_non_module():
                    names = [a.name for a in node.names]
                    lineno = getattr(node, "lineno", 0)
                    errs.append((str(path), lineno, f"import {', '.join(names)} not at module level; move imports to top-level"))
            except Exception as e:
                printException(e, f"inspecting Import in {path}")
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            try:
                if self._is_in_non_module():
                    module = node.module or "<module>"
                    names = [a.name for a in node.names]
                    lineno = getattr(node, "lineno", 0)
                    errs.append((str(path), lineno, f"from {module} import {', '.join(names)} not at module level; move imports to top-level"))
            except Exception as e:
                printException(e, f"inspecting ImportFrom in {path}")
            self.generic_visit(node)

    Visitor().visit(tree)
    return errs


def check_prefer_direct_attrs(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """Enforce the axiom: prefer direct attribute access when attributes are
    assigned in __init__ or on_mount. Finds getattr(self, 'attr', ...) uses
    where `attr` was assigned earlier in the class and reports them.
    """
    errs: List[Tuple[str, int, str]] = []
    try:
        classes = _collect_self_assigned_attrs(path, text=text, tree=tree)
        getattr_uses = _find_getattr_on_self(path, text=text, tree=tree)
    except Exception as e:
        printException(e, f"collecting class attrs/getattr usages in {path}")
        return errs


    # We can't reliably map which class scope a getattr usage belongs to
    # without doing more complex AST mapping, so we'll conservatively flag
    # any getattr(self, 'attr') where attr appears in any class's init/on_mount
    # as a potential violation in the same file. This keeps the check simple
    # and helpful while avoiding deep dataflow analysis.
    assigned_attrs = set()
    for s in classes.values():
        assigned_attrs.update(s)

    for lineno, attr, func in getattr_uses:
        # attr may be "app.current_path" style when getattr called on self.app
        if "." in attr:
            # treat 'app.current_path' -> attribute name 'current_path'
            right = attr.split(".", 1)[1]
            if right in assigned_attrs:
                errs.append((str(path), lineno, f"{func} used for guaranteed attribute '{attr}' (prefer direct access)"))
        else:
            if attr in assigned_attrs:
                errs.append((str(path), lineno, f"{func}(self, '{attr}', ...) used but '{attr}' is assigned in __init__/on_mount; prefer direct access"))

    # Also flag getattr usage on argparse Namespace objects returned by parse_args()
    try:
        parse_args_vars = _find_parse_args_targets(path, text=text, tree=tree)
    except Exception as e:
        printException(e, f"finding parse_args targets in {path}")
        parse_args_vars = set()


    if parse_args_vars:
        try:
            getattr_on_args = _find_getattr_on_vars(path, parse_args_vars, text=text, tree=tree)
            for lineno, varname, attr, func in getattr_on_args:
                tpl = (str(path), lineno, f"{func}({varname}, '{attr}', ...) used on parse_args() result; prefer direct access {varname}.{attr}")
                errs.append(tpl)
        except Exception as e:
            printException(e, f"checking parse_args getattr usages in {path}")

    return errs


def check_getattr_not_initialized(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """Flag getattr(self, 'attr', ...) usages where `attr` is not assigned in __init__/on_mount.

    This helps catch cases where code relies on implicit attributes that should
    instead be initialized in the class initializer or guarded before use.
    """
    errs: List[Tuple[str, int, str]] = []
    try:
        classes = _collect_self_assigned_attrs(path, text=text, tree=tree)
        getattr_uses = _find_getattr_on_self(path, text=text, tree=tree)
    except Exception as e:
        printException(e, f"collecting class attrs/getattr usages in {path}")
        return errs

    # Build the set of assigned attributes (from __init__/on_mount)
    assigned_attrs = set()
    for s in classes.values():
        assigned_attrs.update(s)

    for lineno, attr, func in getattr_uses:
        # attr may be 'app.current_path' style when getattr called on self.app
        if "." in attr:
            right = attr.split(".", 1)[1]
            if right not in assigned_attrs:
                errs.append(
                    (str(path), lineno, f"{func} used for attribute '{attr}' but '{right}' is not initialized in __init__/on_mount")
                )
        else:
            if attr not in assigned_attrs:
                errs.append(
                    (str(path), lineno, f"{func}(self, '{attr}', ...) used but '{attr}' is not initialized in __init__/on_mount")
                )

    return errs


def check_docstrings(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """Ensure module-level functions and class methods have docstrings.

    Reports each function/method missing a docstring as an axiom violation.
    Skips nested functions defined inside other functions to avoid noise.
    """
    errs: List[Tuple[str, int, str]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: List[ast.AST] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.stack.append(node)
            super().generic_visit(node)
            self.stack.pop()

        def _check_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            """Check a function node for a missing docstring when it's a
            module-level function or a direct method of a class.

            Nested functions are skipped.
            """
            # Only consider functions directly under Module (module-level)
            # or directly under ClassDef (methods). Skip nested functions.
            parent = self.stack[-1] if self.stack else None
            if not isinstance(parent, (ast.Module, ast.ClassDef)):
                return
            # Skip constructors; __init__ often intentionally lacks docstrings
            if getattr(node, "name", None) == "__init__":
                return
            # Skip visitor hooks and other AST NodeVisitor methods to avoid
            # noisy reports for many nested helper classes that implement
            # `visit_*` methods.
            name = getattr(node, "name", "")
            if name.startswith("visit_") or name == "generic_visit":
                return
            try:
                doc = ast.get_docstring(node)
                if doc is None:
                    kind = "method" if isinstance(parent, ast.ClassDef) else "function"
                    lineno = getattr(node, "lineno", 0)
                    errs.append((str(path), lineno, f"{kind} '{node.name}' missing docstring"))
            except Exception as e:
                printException(e, f"checking docstring for {getattr(node, 'name', '<anon>')} in {path}")

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._check_function(node)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._check_function(node)
            self.generic_visit(node)

    try:
        Visitor().visit(tree)
    except Exception as e:
        printException(e, f"walking AST for docstring checks in {path}")
    return errs


def check_getattr_method_calls(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """Detect immediate calls of getattr(...)(...) or assignments from such calls.

    Flags occurrences like `getattr(obj, 'name')(...)` or `(a,b) = getattr(...)(...)`.
    Recommend assigning the attribute to a local variable first: `fn = getattr(...); fn(...)`.
    """
    errs: List[Tuple[str, int, str]] = []

    # Track reported messages and the underlying getattr-call nodes so the
    # same `getattr(...)(...)` instance isn't reported twice (e.g. once as
    # an assignment and once as a direct call). We record `id(inner_call)`
    # where `inner_call` is the ast.Call node for the `getattr(...)` call.
    reported: set = set()
    reported_nodes: set = set()

    # Build a mapping of class_name -> set(method_names) for classes
    # defined in this module. This allows heuristics to recognize calls
    # like `getattr(test_repo, 'runFileListSampledComparisons')` when the
    # `TestRepo` class in the same file defines that method — such cases
    # are likely safe and should not be flagged.
    class_methods: dict = {}
    try:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                mset = set()
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        mset.add(getattr(item, "name", None))
                class_methods[node.name] = mset
    except Exception as e:
        printException(e, f"building class_methods in {path}")
        # Be conservative: if class extraction fails, leave class_methods empty
        class_methods = {}

    class Finder(ast.NodeVisitor):
        def _get_str_value(self, node) -> Optional[str]:
            """Return the literal string value for ast.Str/ast.Constant nodes.

            Returns the contained string when `node` represents a string
            literal (supports `ast.Str` for older Pythons and `ast.Constant`
            for newer ones). Returns `None` for non-string nodes.
            """
            # Handle ast.Constant or ast.Str depending on Python version
            if node is None:
                return None
            if hasattr(ast, "Str") and isinstance(node, ast.Str):
                return node.s
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            return None

        def visit_Call(self, node: ast.Call) -> None:
            try:
                inner = getattr(node, "func", None)
                # pattern: Call(func=Call(func=Name(id='getattr'), ...), args=...)
                if isinstance(inner, ast.Call):
                    inner_func = getattr(inner, "func", None)
                    if isinstance(inner_func, ast.Name) and inner_func.id == "getattr":
                        # Only flag when the attribute arg (second arg) is a literal string
                        arg2 = inner.args[1] if len(inner.args) >= 2 else None
                        attr_name = self._get_str_value(arg2)
                        if attr_name is None:
                            # dynamic attribute name; skip
                            pass
                        else:
                            # Heuristic: only report when we can prove the
                            # attribute refers to a method defined on a class in
                            # this same file. If we can't determine that, skip
                            # reporting because calling getattr(...) may be
                            # legitimate for dynamic / adapter-like cases.
                            first = inner.args[0] if len(inner.args) >= 1 else None
                            provably_method = False
                            try:
                                if isinstance(first, ast.Name):
                                    varnorm = first.id.replace("_", "").lower()
                                    for cname, methods in class_methods.items():
                                        if cname and cname.lower() in varnorm and attr_name in methods:
                                            provably_method = True
                                            break
                            except Exception as e:
                                printException(e, f"determining provably_method in visit_Call for {path}")
                                provably_method = False
                            if not provably_method:
                                # Unknown target; avoid flagging.
                                return
                            # Avoid duplicate reports for the same underlying
                            # getattr(...) call node.
                            inner_id = id(inner)
                            if inner_id in reported_nodes:
                                # already reported (likely via Assign visitor)
                                pass
                            else:
                                lineno = getattr(node, "lineno", None)
                                if lineno is not None:
                                    tpl = (
                                        str(path),
                                        lineno,
                                        f"direct call of getattr(..., '{attr_name}', ...)(...); avoid calling getattr(...); call the method directly (e.g., x.{attr_name}(...))",
                                    )
                                    if tpl not in reported:
                                        errs.append(tpl)
                                        reported.add(tpl)
                                        reported_nodes.add(inner_id)
            except Exception as e:
                printException(e, f"error scanning for getattr method calls in {path}")
            self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> None:
            try:
                val = getattr(node, "value", None)
                if isinstance(val, ast.Call):
                    inner = getattr(val, "func", None)
                    if isinstance(inner, ast.Call):
                        inner_func = getattr(inner, "func", None)
                        if isinstance(inner_func, ast.Name) and inner_func.id == "getattr":
                            # Only flag when getattr second arg is a literal string
                            arg2 = inner.args[1] if len(inner.args) >= 2 else None
                            attr_name = self._get_str_value(arg2)
                            if attr_name is None:
                                pass
                            else:
                                # Heuristic: only report when we can prove the
                                # attribute refers to a method defined on a class
                                # in this same file. Otherwise skip.
                                first = inner.args[0] if len(inner.args) >= 1 else None
                                provably_method = False
                                try:
                                    if isinstance(first, ast.Name):
                                        varnorm = first.id.replace("_", "").lower()
                                        for cname, methods in class_methods.items():
                                            if cname and cname.lower() in varnorm and attr_name in methods:
                                                provably_method = True
                                                break
                                except Exception as e:
                                    printException(e, f"determining provably_method in visit_Assign for {path}")
                                    provably_method = False
                                if not provably_method:
                                    return

                                # Avoid duplicate reports for the same underlying
                                # getattr(...) call node.
                                inner_id = id(inner)
                                if inner_id in reported_nodes:
                                    # already reported
                                    pass
                                else:
                                    lineno = getattr(node, "lineno", None)
                                    if lineno is not None:
                                        tpl = (
                                            str(path),
                                            lineno,
                                            f"assignment from getattr(..., '{attr_name}', ...)(...); avoid calling getattr(...); call the method directly (e.g., (t, p, f) = x.{attr_name}(...))",
                                        )
                                        if tpl not in reported:
                                            errs.append(tpl)
                                            reported.add(tpl)
                                            reported_nodes.add(inner_id)
            except Exception as e:
                printException(e, f"error scanning Assign for getattr method calls in {path}")
            self.generic_visit(node)

    try:
        Finder().visit(tree)
    except Exception as e:
        printException(e, f"visiting AST for getattr method calls in {path}")

    return errs


def run_py_compile(py_files: List[Path]) -> List[Tuple[Path, str]]:
    """Run `py_compile` on each path in `py_files` and return failures.

    Each failure is (Path, combined_output_str).
    """
    failures: List[Tuple[Path, str]] = []
    for p in py_files:
        try:
            subprocess.run([sys.executable, "-m", "py_compile", str(p)], check=True, capture_output=True)
        except subprocess.CalledProcessError as cpe:
            printException(cpe)
            out = (cpe.stdout or b"").decode("utf-8", errors="replace")
            err = (cpe.stderr or b"").decode("utf-8", errors="replace")
            failures.append((p, out + "\n" + err))
    return failures


def main(argv: List[str] | None = None) -> int:
    """Command-line entry point for the axiom checker.

    Parses flags, discovers Python files, runs AST checks and `py_compile`.
    Returns exit code 0 on success, 1 on violations.
    """
    parser = argparse.ArgumentParser(prog="check_axioms.py")
    parser.add_argument("--root", "-r", default=str(ROOT), help="Project root to scan")
    parser.add_argument("--compile-only", action="store_true", help="Only run py_compile checks")

    # Paired options are represented as mutually-exclusive groups so users
    # cannot accidentally pass both the enable and disable variants.
    # Alphabetical ordering of groups: B, C, D, E, I, L, N, P, S, T
    gb = parser.add_mutually_exclusive_group()
    gb.add_argument("-B",
        "--no-bare-excepts",
        dest="check_bare_excepts",
        action="store_false",
        help="Skip checking for bare 'except:' (default: check)",
    )
    gb.add_argument("-b",
        "--bare-excepts",
        dest="enable_bare_excepts",
        action="store_true",
        help="Enable bare-excepts check (opposite of -B)."
    )
    parser.add_argument("-g",
        "--check-global-excepts",
        action="store_true",
        help="Also flag bare 'except:' in global functions and modules (off by default)",
    )

    gc = parser.add_mutually_exclusive_group()
    gc.add_argument("-C",
        "--no-py-compile",
        dest="check_py_compile",
        action="store_false",
        help="Skip running py_compile on files (default: run)",
    )
    gc.add_argument("-c",
        "--py-compile",
        dest="enable_py_compile",
        action="store_true",
        help="Enable py_compile checks (opposite of -C)."
    )

    gd = parser.add_mutually_exclusive_group()
    gd.add_argument("-D",
        "--no-prefer-direct-attrs",
        dest="check_prefer_direct_attrs",
        action="store_false",
        help="Skip checking for prefer-direct-attribute usages (default: check)",
    )
    gd.add_argument("-d",
        "--prefer-direct-attrs",
        dest="enable_prefer_direct_attrs",
        action="store_true",
        help="Enable prefer-direct-attrs check (opposite of -D)."
    )

    ge = parser.add_mutually_exclusive_group()
    ge.add_argument("-E",
        "--no-except-as-print",
        dest="check_except_as_print",
        action="store_false",
        help="Skip checking that 'except Exception as e:' is followed by a printException call (default: check)",
    )
    ge.add_argument("-e",
        "--except-as-print",
        dest="enable_except_as_print",
        action="store_true",
        help="Enable except-as-print check (opposite of -E)."
    )

    parser.add_argument(
        "--print",
        dest="print_mode",
        action="store_true",
        help="When true, suggested messages will reference print() instead of printException (used for checking codebases that use print() instead of printException).",
    )

    gi = parser.add_mutually_exclusive_group()
    gi.add_argument("-I",
        "--no-check-imports",
        dest="check_imports",
        action="store_false",
        help="Skip checking for imports-at-module-level (default: check)",
    )
    gi.add_argument("-i",
        "--check-imports",
        dest="enable_check_imports",
        action="store_true",
        help="Enable imports-at-module-level check (opposite of -I).",
    )

    gl = parser.add_mutually_exclusive_group()
    gl.add_argument("-L",
        "--no-logger-in-try",
        dest="check_logger_in_try",
        action="store_false",
        help="Skip checking for try/except where the try body contains only a logger.<method>(...) call (default: check)",
    )
    gl.add_argument("-l",
        "--logger-in-try",
        dest="enable_logger_in_try",
        action="store_true",
        help="Enable logger-in-try check (opposite of -L).",
    )

    gm = parser.add_mutually_exclusive_group()
    gm.add_argument("-M",
        "--no-printexception-in-try",
        dest="check_printexception_in_try",
        action="store_false",
        help="Skip checking for try/except where the try body contains only a printException(...) call (default: check)",
    )
    gm.add_argument("-m",
        "--printexception-in-try",
        dest="enable_printexception_in_try",
        action="store_true",
        help="Enable printexception-in-try check (opposite of -M).",
    )

    gn = parser.add_mutually_exclusive_group()
    gn.add_argument("-N",
        "--no-nested-try-except",
        dest="check_nested_try",
        action="store_false",
        help="Skip checking for redundant nested try/except (default: check)",
    )
    gn.add_argument("-n",
        "--nested-try-except",
        dest="enable_nested_try",
        action="store_true",
        help="Enable nested-try-except check (opposite of -N).",
    )

    gp = parser.add_mutually_exclusive_group()
    gp.add_argument("-P",
        "--no-pass-check",
        dest="check_pass",
        action="store_false",
        help="Skip checking for unnecessary 'pass' inside except-as blocks (default: check)",
    )
    gp.add_argument("-p",
        "--pass-check",
        dest="enable_pass_check",
        action="store_true",
        help="Enable unnecessary-pass check (opposite of -P)."
    )

    gs = parser.add_mutually_exclusive_group()
    gs.add_argument("-S",
        "--no-check-docstrings",
        dest="check_docstrings",
        action="store_false",
        help="Skip checking that functions/methods have docstrings (default: check)",
    )
    gs.add_argument("-s",
        "--check-docstrings",
        dest="enable_check_docstrings",
        action="store_true",
        help="Enable docstring checks (opposite of -S).",
    )

    gt = parser.add_mutually_exclusive_group()
    gt.add_argument("-T",
        "--no-getattr-not-initialized",
        dest="check_getattr_not_initialized",
        action="store_false",
        help="Skip checking for getattr-not-initialized (default: check)",
    )
    gt.add_argument("-t",
        "--getattr-not-initialized",
        dest="enable_getattr_not_initialized",
        action="store_true",
        help="Enable getattr-not-initialized check (opposite of -T).",
    )

    gu = parser.add_mutually_exclusive_group()
    gu.add_argument(
        "--no-check_getattr_methods",
        dest="check_getattr_methods",
        action="store_false",
        help="Skip checking for getattr(...) immediate-call usages (default: check)",
    )
    gu.add_argument(
        "--check_getattr_methods",
        dest="enable_check_getattr_methods",
        action="store_true",
        help="Enable check for immediate calls of getattr(...)(...) (opposite of --no-check_getattr_methods)",
    )
    
    # `--NONE` convenience flag: keep near the end of the options list
    parser.add_argument("--NONE",
        dest="none",
        action="store_true",
        help="Disable all checks (equivalent to -B -E -D -C etc.)",
    )
    parser.add_argument("-v", "--verbose", dest="verbose", action="count", default=0,
        help="Increase verbosity (specify multiple times for more detail)."
    )
    parser.add_argument("files", nargs="*", help="Optional explicit files or directories to check (overrides discovery)")
    args = parser.parse_args(argv)

    root = Path(args.root)

    # Configure logging verbosity: 0=WARNING (default), 1=INFO, 2+=DEBUG
    if args.verbose >= 2:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose == 1:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    # If -N/--NONE specified, disable all checks (convenience shorthand)
    if args.none:
        args.check_bare_excepts = False
        args.check_except_as_print = False
        args.check_prefer_direct_attrs = False
        args.check_py_compile = False
        args.check_pass = False
        args.check_getattr_not_initialized = False
        args.check_getattr_methods = False
        args.check_logger_in_try = False
        args.check_printexception_in_try = False
        args.check_imports = False
        args.check_docstrings = False

    # Honor explicit enable flags after -N/--NONE

    # Honor explicit small-letter re-enable flags after -N/--NONE
    if args.enable_bare_excepts:
        args.check_bare_excepts = True
    if args.enable_except_as_print:
        args.check_except_as_print = True
    if args.enable_prefer_direct_attrs:
        args.check_prefer_direct_attrs = True
    if args.enable_py_compile:
        args.check_py_compile = True
    if args.enable_pass_check:
        args.check_pass = True
    if args.enable_getattr_not_initialized:
        args.check_getattr_not_initialized = True
    if args.enable_check_getattr_methods:
        args.check_getattr_methods = True
    if args.enable_logger_in_try:
        args.check_logger_in_try = True
    if args.enable_printexception_in_try:
        args.check_printexception_in_try = True
    if args.enable_check_imports:
        args.check_imports = True
    if args.enable_nested_try:
        args.check_nested_try = True
    if args.enable_check_docstrings:
        args.check_docstrings = True

    logger.info("cwd: %s", Path.cwd())
    # Single computed display token for suggested calls: either 'print' or 'printException'
    call_example = "print" if args.print_mode else "printException"

    # If explicit files/dirs were provided, use them (override discovery).
    if args.files:
        supplied: List[Path] = []
        for f in args.files:
            logger.debug("Supplied path: %s", f)
            p = Path(f)
            if not p.is_absolute():
                p = root.joinpath(p)
            supplied.append(p)
            logger.debug("  resolved to: %s", p)

        py_files_set = []
        for p in supplied:
            try:
                logger.debug("Processing supplied path: %s", p)
                if p.is_dir():
                    for q in list_py_files(p):
                        logger.debug("  found: %s", q)
                        py_files_set.append(q)
                elif p.exists():
                    # Only include explicit files that look like Python source
                    if is_python_file(p):
                        logger.debug("including supplied python file: %s", p)
                        py_files_set.append(p)
                    else:
                        logger.debug("skipping supplied non-python file: %s", p)
            except Exception as e:
                printException(e, f"processing supplied path {p}")
                continue
        py_files = sorted({p for p in py_files_set})
    else:
        py_files = sorted(list_py_files(root))

    error_count = 0

    if not args.compile_only:
        all_errs: List[Tuple[str, int, str]] = []
        for p in py_files:
            errs: List[Tuple[str, int, str]] = []
            try:
                # Parse the file once up-front. If parsing fails, emit an
                # error and skip further AST-dependent checks for this file.
                text, tree = load_source_and_ast(p)
                if tree is None:
                    print(f"{p}: could not be parsed as Python source; skipping AST-based checks")
                    continue

                logger.debug(
                    "AST-check decision: check_bare_excepts=%s check_except_as_print=%s check_pass=%s check_logger_in_try=%s check_printexception_in_try=%s",
                    args.check_bare_excepts,
                    args.check_except_as_print,
                    args.check_pass,
                    args.check_logger_in_try,
                    args.check_printexception_in_try,
                )
                if (
                    args.check_bare_excepts
                    or args.check_except_as_print
                    or args.check_pass
                    or args.check_logger_in_try
                ):
                    errs += check_file(
                        p,
                        text,
                        tree,
                        bool(args.check_global_excepts),
                        bool(args.check_bare_excepts),
                        bool(args.check_except_as_print),
                        bool(args.check_pass),
                        bool(args.check_logger_in_try),
                        bool(args.check_printexception_in_try),
                        call_example,
                    )
                # Enforce 'prefer direct attribute access' axiom
                if args.check_prefer_direct_attrs:
                    try:
                        errs += check_prefer_direct_attrs(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_prefer_direct_attrs failed for {p}")
                # Enforce 'getattr-not-initialized' axiom
                if args.check_getattr_not_initialized:
                    try:
                        errs += check_getattr_not_initialized(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_getattr_not_initialized failed for {p}")
                # Enforce docstring presence for module-level functions and class methods
                if args.check_docstrings:
                    try:
                        errs += check_docstrings(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_docstrings failed for {p}")
                # Check for getattr(...)() immediate calls when requested
                if args.check_getattr_methods:
                    try:
                        errs += check_getattr_method_calls(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_getattr_method_calls failed for {p}")
                # Enforce 'imports at module level' axiom
                if args.check_imports:
                    try:
                        errs += check_imports_module_level(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_imports_module_level failed for {p}")
                # Detect redundant nested try/except patterns
                if args.check_nested_try:
                    try:
                        errs += check_redundant_nested_try(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_redundant_nested_try failed for {p}")
            except Exception as e:
                printException(e, f"error checking {p}")
            if errs:
                all_errs.extend(errs)
        if all_errs:
            error_count += len(all_errs)
            # Sort errors by file path then numeric line number
            all_errs.sort(key=lambda t: (t[0], t[1]))

            print("Axiom violations detected:")
            for fpath, lineno, msg in all_errs:
                print(f"{fpath}:{lineno}: {msg}")
            print()

    # Always run py_compile as a final gate
    failures = []
    if args.check_py_compile:
        failures = run_py_compile(py_files)
    if failures:
        error_count += len(failures)
        print("py_compile failures:")
        for p, out in failures:
            print(f"--- {p} ---")
            print(out)
        print()

    if error_count > 0:
        print(f"check_axioms: FAILED — {error_count} error{'s' if error_count != 1 else ''}")
        return 1

    print("check_axioms: OK — no violations found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
