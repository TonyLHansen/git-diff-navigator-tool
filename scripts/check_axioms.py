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


ROOT = Path(__file__).resolve().parents[1]
PY_EXT = "*.py"

# How many lines after an except to search for a printException call
EXCEPT_LOOKAHEAD = 8


def list_py_files(root: Path) -> List[Path]:
    ignore_parts = {"venv-3.14", "venv", ".venv", "__pycache__"}
    files: List[Path] = []

    # Also include files that have a python shebang (#!...python) even if
    # they don't end with .py. Iterate a limited set of files to avoid
    # scanning large binary directories.
    shebang_re = re.compile(r"^#!.*python")
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
            if any(part in ignore_parts for part in p.parts):
                continue
            name = p.name.lower()
            if "old" in name or "sv" in name:
                continue
            if name.suffix == ".py":
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
                files.append(p)
        except Exception as e:
            printException(e, f"scanning file {p}")
            continue
    return sorted(files)


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


def _find_bare_except_locations(path: Path) -> List[tuple[int, bool]]:
    """Return list of (lineno, in_class) for bare `except:` handlers in AST.

    `in_class` is True when the except is inside a ClassDef (i.e., a method),
    False otherwise.
    """
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        printException(e, f"reading {path}")
        return []
    try:
        tree = ast.parse(src)
    except Exception as e:
        printException(e, f"parsing AST for {path}")
        return []

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
                in_class = "class" in getattr(self, "stack", [])
                # in_class = "class" in self.stack
                lineno = getattr(node, "lineno", None)
                if lineno is not None:
                    results.append((lineno, in_class))
            self.generic_visit(node)

    Visitor().visit(tree)
    return results


def _find_except_without_name_locations(path: Path) -> List[int]:
    """Return line numbers for ExceptHandler nodes that specify an
    exception type but do not bind it with `as <var>`.
    """
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        return []
    try:
        tree = ast.parse(src)
    except Exception as e:
        return []

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


def _collect_self_assigned_attrs(path: Path) -> dict:
    """Collect attributes assigned to `self` in `__init__` or `on_mount` per class.

    Returns mapping class_name -> set(attribute names)
    """
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        printException(e, f"reading {path}")
        return {}
    try:
        tree = ast.parse(src)
    except Exception as e:
        printException(e, f"parsing AST for {path}")
        return {}

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


def _find_getattr_on_self(path: Path) -> List[tuple[int, str]]:
    """Find usages of getattr(self, 'attr', ...) and return list of (lineno, attrname).

    Does not currently attempt to resolve dynamic attribute names.
    """
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        printException(e, f"reading {path}")
        return []
    try:
        tree = ast.parse(src)
    except Exception as e:
        printException(e, f"parsing AST for {path}")
        return []

    results: List[tuple[int, str]] = []

    # Some Python versions don't expose ast.Str (strings are ast.Constant).
    has_ast_Str = hasattr(ast, "Str")
    str_node_types = (ast.Constant, ast.Str) if has_ast_Str else (ast.Constant,)

    class Finder(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            # match getattr(self, 'attr', ...)
            try:
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
                                results.append((lineno, attr_name))
                # also match getattr(self, 'x') when first arg is Attribute like self.app
                if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
                    first = node.args[0]
                    second = node.args[1]
                    if isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name) and first.value.id == "self":
                        # getattr(self.something, 'attr', ...)
                        if has_ast_Str and isinstance(second, ast.Str):
                            attr_name = second.s
                        else:
                            attr_name = getattr(second, "value", None)
                        if isinstance(attr_name, str):
                            lineno = getattr(node, "lineno", None)
                            if lineno is not None:
                                results.append((lineno, f"{first.attr}.{attr_name}"))
            except Exception as e:
                printException(e, f"error walking AST for getattr detection in {path}")
            self.generic_visit(node)

    Finder().visit(tree)
    return results


def _find_parse_args_targets(path: Path) -> set:
    """Return set of variable names assigned from a call to `*.parse_args()`.

    e.g. `args = parser.parse_args()` -> returns {'args'}
    """
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        return set()
    try:
        tree = ast.parse(src)
    except Exception as e:
        return set()

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
                pass
            self.generic_visit(node)

    Finder().visit(tree)
    return targets


def _find_getattr_on_vars(path: Path, varnames: set) -> List[tuple[int, str, str]]:
    """Find usages of getattr(var, 'attr', ...) where var is in varnames.

    Returns list of (lineno, varname, attrname).
    """
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        return []
    try:
        tree = ast.parse(src)
    except Exception as e:
        return []

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
                                    results.append((lineno, first.id, attr))
            except Exception as e:
                pass
            self.generic_visit(node)

    Finder().visit(tree)
    return results


def check_file(path: Path, check_global_excepts: bool = False, check_bare_excepts: bool = True, check_except_as_print: bool = True, check_pass: bool = True) -> List[str]:
    errs: List[str] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Detect bare excepts (only inside classes by default)
    try:
        bare_locations = _find_bare_except_locations(path)
    except Exception as e:
        printException(e, f"finding bare excepts in {path}")
        bare_locations = []

    if check_bare_excepts:
        for lineno, in_class in bare_locations:
            if in_class or check_global_excepts:
                errs.append(f"{path}:{lineno}: bare 'except:' detected")
        # Also flag `except Name:` (type present but no `as var`)
        try:
            no_name_linenos = _find_except_without_name_locations(path)
        except Exception as e:
            no_name_linenos = []
        for lineno in no_name_linenos:
            errs.append(f"{path}:{lineno}: 'except [<type>]:' without 'as <var>' detected")

    # Detect 'except Exception:' without 'as' and ensure except-as blocks
    # reference the exception variable in a subsequent printException call.
    # These checks are gated by `check_except_as_print` so they can be
    # disabled independently.
    if check_except_as_print:
        # Match any `except TYPE as var:` to ensure `var` is used in
        # a subsequent printException(...) call. This check is separate
        # from the bare-except detection above.
        pattern_except_as = re.compile(r"^\s*except\s+(?P<typ>[A-Za-z0-9_\.]+)\s+as\s+(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*:")

        for i, ln in enumerate(lines, start=1):
            m = pattern_except_as.match(ln)
            if m:
                var = m.group("var")
                # If the exception identifier is `_use_stderr`, the caller
                # intentionally uses stderr directly and we don't require
                # a subsequent printException(...) call.
                if var == "_use_stderr":
                    continue
                # Look ahead for a printException call referencing var
                found = False
                for j in range(i, min(i + EXCEPT_LOOKAHEAD, len(lines))):
                    snippet = lines[j]
                    if re.search(rf"\b(printException|\.printException)\s*\(.*\b{re.escape(var)}\b", snippet):
                        found = True
                        break
                if not found:
                    errs.append(
                        f"{path}:{i}: 'except {m.group('typ')} as {var}:' not followed by printException({var}, ...) within {EXCEPT_LOOKAHEAD} lines"
                    )

    # Additional check: flag unnecessary `pass` statements inside
    # `except ... as var:` blocks when other statements are present.
    try:
        if check_pass:
            try:
                errs += check_unnecessary_pass_in_except(path)
            except NameError as e:
                # function may be defined later in the file; skip for now
                pass
            except Exception as e:
                printException(e, f"checking unnecessary pass in except for {path}")
    except Exception as e:
        pass

    return errs


def check_unnecessary_pass_in_except(path: Path) -> List[str]:
    """Find `pass` statements inside `except ... as var:` blocks when other statements are present.

    Reports each `pass` statement's line number when the except-handler body
    contains at least one `pass` and at least one other statement.
    """
    errs: List[str] = []
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        printException(e, f"reading {path}")
        return errs
    try:
        tree = ast.parse(src)
    except Exception as e:
        printException(e, f"parsing AST for {path}")
        return errs

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
                                f"{path}:{lineno}: unnecessary 'pass' in except block that also contains other statements (e.g., printException)"
                            )
            except Exception as e:
                printException(e, f"walking ExceptHandler in {path}")
            self.generic_visit(node)

    Visitor().visit(tree)
    return errs


def check_prefer_direct_attrs(path: Path) -> List[str]:
    """Enforce the axiom: prefer direct attribute access when attributes are
    assigned in __init__ or on_mount. Finds getattr(self, 'attr', ...) uses
    where `attr` was assigned earlier in the class and reports them.
    """
    errs: List[str] = []
    try:
        classes = _collect_self_assigned_attrs(path)
        getattr_uses = _find_getattr_on_self(path)
    except Exception as e:
        printException(e, f"collecting class attrs/getattr usages in {path}")
        return errs

    if not classes:
        return errs

    # We can't reliably map which class scope a getattr usage belongs to
    # without doing more complex AST mapping, so we'll conservatively flag
    # any getattr(self, 'attr') where attr appears in any class's init/on_mount
    # as a potential violation in the same file. This keeps the check simple
    # and helpful while avoiding deep dataflow analysis.
    assigned_attrs = set()
    for s in classes.values():
        assigned_attrs.update(s)

    for lineno, attr in _find_getattr_on_self(path):
        # attr may be "app.current_path" style when getattr called on self.app
        if "." in attr:
            # treat 'app.current_path' -> attribute name 'current_path'
            right = attr.split(".", 1)[1]
            if right in assigned_attrs:
                errs.append(f"{path}:{lineno}: getattr used for guaranteed attribute '{attr}' (prefer direct access)")
        else:
            if attr in assigned_attrs:
                errs.append(f"{path}:{lineno}: getattr(self, '{attr}', ...) used but '{attr}' is assigned in __init__/on_mount; prefer direct access")

    # Also flag getattr usage on argparse Namespace objects returned by parse_args()
    try:
        parse_args_vars = _find_parse_args_targets(path)
    except Exception as e:
        parse_args_vars = set()

    if parse_args_vars:
        try:
            getattr_on_args = _find_getattr_on_vars(path, parse_args_vars)
            for lineno, varname, attr in getattr_on_args:
                errs.append(
                    f"{path}:{lineno}: getattr({varname}, '{attr}', ...) used on parse_args() result; prefer direct access {varname}.{attr}"
                )
        except Exception as e:
            printException(e, f"checking parse_args getattr usages in {path}")

    return errs


def run_py_compile(py_files: List[Path]) -> List[Tuple[Path, str]]:
    failures: List[Tuple[Path, str]] = []
    for p in py_files:
        try:
            subprocess.run([sys.executable, "-m", "py_compile", str(p)], check=True, capture_output=True)
        except subprocess.CalledProcessError as cpe:
            out = (cpe.stdout or b"").decode("utf-8", errors="replace")
            err = (cpe.stderr or b"").decode("utf-8", errors="replace")
            failures.append((p, out + "\n" + err))
    return failures


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_axioms.py")
    parser.add_argument("--root", "-r", default=str(ROOT), help="Project root to scan")
    parser.add_argument("--compile-only", action="store_true", help="Only run py_compile checks")
    parser.add_argument("-g",
        "--check-global-excepts",
        action="store_true",
        help="Also flag bare 'except:' in global functions and modules (off by default)",
    )
    parser.add_argument("-B",
        "--no-bare-excepts",
        dest="check_bare_excepts",
        action="store_false",
        help="Skip checking for bare 'except:' (default: check)",
    )
    parser.add_argument("-E",
        "--no-except-as-print",
        dest="check_except_as_print",
        action="store_false",
        help="Skip checking that 'except Exception as e:' is followed by a printException call (default: check)",
    )
    parser.add_argument("-D",
        "--no-prefer-direct-attrs",
        dest="check_prefer_direct_attrs",
        action="store_false",
        help="Skip checking for prefer-direct-attribute usages (default: check)",
    )
    parser.add_argument("-C",
        "--no-py-compile",
        dest="check_py_compile",
        action="store_false",
        help="Skip running py_compile on files (default: run)",
    )
    parser.add_argument("-P",
        "--no-pass-check",
        dest="check_pass",
        action="store_false",
        help="Skip checking for unnecessary 'pass' inside except-as blocks (default: check)",
    )
    parser.add_argument("--NONE",
        dest="none",
        action="store_true",
        help="Disable all checks (equivalent to -B -E -D -C)",
    )
    # Short enable flags (opposites of -B/-E/-D/-C) — useful when combined
    # with --NONE to selectively re-enable specific checks.
    parser.add_argument("-b",
        "--bare-excepts",
        dest="enable_bare_excepts",
        action="store_true",
        help="Enable bare-excepts check (opposite of -B)."
    )
    parser.add_argument("-e",
        "--except-as-print",
        dest="enable_except_as_print",
        action="store_true",
        help="Enable except-as-print check (opposite of -E)."
    )
    parser.add_argument("-d",
        "--prefer-direct-attrs",
        dest="enable_prefer_direct_attrs",
        action="store_true",
        help="Enable prefer-direct-attrs check (opposite of -D)."
    )
    parser.add_argument("-c",
        "--py-compile",
        dest="enable_py_compile",
        action="store_true",
        help="Enable py_compile checks (opposite of -C)."
    )
    parser.add_argument("-p",
        "--pass-check",
        dest="enable_pass_check",
        action="store_true",
        help="Enable unnecessary-pass check (opposite of -P)."
    )
    parser.add_argument("files", nargs="*", help="Optional explicit files or directories to check (overrides discovery)")
    args = parser.parse_args(argv)

    root = Path(args.root)

    # If --NONE specified, disable all checks (convenience shorthand)
    if getattr(args, "none", False):
        args.check_bare_excepts = False
        args.check_except_as_print = False
        args.check_prefer_direct_attrs = False
        args.check_py_compile = False
        args.check_pass = False

    # Honor explicit small-letter re-enable flags after --NONE
    if getattr(args, "enable_bare_excepts", False):
        args.check_bare_excepts = True
    if getattr(args, "enable_except_as_print", False):
        args.check_except_as_print = True
    if getattr(args, "enable_prefer_direct_attrs", False):
        args.check_prefer_direct_attrs = True
    if getattr(args, "enable_py_compile", False):
        args.check_py_compile = True
    if getattr(args, "enable_pass_check", False):
        args.check_pass = True

    # If explicit files/dirs were provided, use them (override discovery).
    if getattr(args, "files", None):
        supplied: List[Path] = []
        for f in args.files:
            p = Path(f)
            if not p.is_absolute():
                p = root.joinpath(p)
            supplied.append(p)

        py_files_set = []
        for p in supplied:
            try:
                if p.is_dir():
                    for q in list_py_files(p):
                        py_files_set.append(q)
                elif p.exists():
                    py_files_set.append(p)
            # except Exception as e:
            except Exception as e:
                # printException(e, f"processing supplied path {p}")
                continue
        py_files = sorted({p for p in py_files_set})
    else:
        py_files = list_py_files(root)

    error_count = 0

    if not args.compile_only:
        all_errs: List[str] = []
        for p in py_files:
            errs: List[str] = []
            try:
                if args.check_bare_excepts or args.check_except_as_print or args.check_pass:
                    errs += check_file(
                        p,
                        check_global_excepts=bool(getattr(args, "check_global_excepts", False)),
                        check_bare_excepts=bool(getattr(args, "check_bare_excepts", True)),
                        check_except_as_print=bool(getattr(args, "check_except_as_print", True)),
                        check_pass=bool(getattr(args, "check_pass", True)),
                    )
                # Enforce 'prefer direct attribute access' axiom
                if getattr(args, "check_prefer_direct_attrs", True):
                    try:
                        errs += check_prefer_direct_attrs(p)
                    except Exception as e:
                        printException(e, f"check_prefer_direct_attrs failed for {p}")
            except Exception as e:
                printException(e, f"error checking {p}")
            if errs:
                all_errs.extend(errs)
        if all_errs:
            error_count += len(all_errs)
            print("Axiom violations detected:")
            for e in all_errs:
                print(e)
            print()

    # Always run py_compile as a final gate
    failures = []
    if getattr(args, "check_py_compile", True):
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
