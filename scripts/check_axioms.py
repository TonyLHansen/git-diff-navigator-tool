#!/usr/bin/env python3
"""
check_axioms.py

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
import configparser
from pathlib import Path
from typing import List, Tuple, Optional, Dict
import fnmatch


# Default scanning root: current working directory. This makes the tool
# behave as a general-purpose checker that defaults to where it's run.
# Users may still override with `--root`.
ROOT = Path.cwd()
PY_EXT = "*.py"

# How many lines after an except to search for a printException call
EXCEPT_LOOKAHEAD = 8

logger = logging.getLogger(__name__)

def printException(e: Exception, msg: Optional[str] = None) -> None:
    """
    Module-level helper to log unexpected exceptions when `self` isn't available.

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
    """
    Read a file and return (source_text, parsed_ast) or (text, None) on failure.

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
    """
    Return a list of Python files under `root` (heuristic).

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
    """
    Return True if `p` is a Python file by extension or shebang.

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
    """
    Return list of (lineno, in_class) for bare `except:` handlers in AST.

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
    """
    Return line numbers for ExceptHandler nodes that specify an
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


def _collect_self_assigned_attrs(path: Path, text: str, tree: ast.AST) -> tuple:
    """
    Collect attributes assigned to `self` in `__init__` or `on_mount` per class
    and also collect each class's base class names.

    Returns a tuple (classes, class_bases) where:
      - classes: mapping class_name -> set(attribute names)
      - class_bases: mapping class_name -> list(base name strings)
    """
    classes: dict = {}
    class_bases: dict = {}

    def _base_name(n: ast.expr) -> str | None:
        # extract a readable base name from ast.Name or ast.Attribute
        try:
            if isinstance(n, ast.Name):
                return n.id
            if isinstance(n, ast.Attribute):
                parts: list[str] = []
                cur = n
                while isinstance(cur, ast.Attribute):
                    parts.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    parts.append(cur.id)
                return ".".join(reversed(parts))
        except Exception as e:
            printException(e, f"_base_name failed for node {n!r} in {path}")
        return None

    class Collector(ast.NodeVisitor):
        def __init__(self):
            self.current_class = None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.current_class = node.name
            classes.setdefault(node.name, set())
            # collect base names for this class
            bases: list[str] = []
            for b in node.bases:
                bn = _base_name(b)
                if bn:
                    bases.append(bn)
            class_bases[node.name] = bases

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
    return (classes, class_bases)


# Default mapping of well-known base-class names to attributes they
# typically provide. These defaults may be overridden by a
# `[base_class_attrs]` section in `.check_axioms.ini` (key = base
# class name, value = comma-separated attribute names). Loading is
# done at import/runtime so users can customize to their project's
# base classes.
# No hardwired defaults: users must supply base-class attributes via
# `.check_axioms.ini` under a `[base_class_attrs]` section. The loader
# will return an empty mapping when no config is present.
DEFAULT_BASE_CLASS_ATTRS: dict = {}

def _load_base_class_attrs_from_ini() -> dict:
    """
    Load BASE_CLASS_ATTRS from .check_axioms.ini when present.

    Looks for a `[base_class_attrs]` section where each option is a
    base class name and its value is a comma-or-space-separated list of
    attribute names. Merges the provided values with the defaults where
    unspecified keys remain as in `DEFAULT_BASE_CLASS_ATTRS`.
    """
    try:
        cfg = configparser.ConfigParser()
        ini_path = Path(".check_axioms.ini")
        project_ini = ROOT / ".check_axioms.ini"
        if not ini_path.exists() and project_ini.exists():
            ini_path = project_ini
        if ini_path.exists():
            try:
                cfg.read(ini_path)
                if cfg.has_section("base_class_attrs"):
                    loaded = {}
                    for k, v in cfg.items("base_class_attrs"):
                        # split on commas or whitespace
                        parts = [p.strip() for p in re.split(r"[,\s]+", v) if p.strip()]
                        # normalize keys to lowercase for case-insensitive matching
                        loaded[k.lower()] = set(parts)
                    merged = {k.lower(): set(v) for k, v in DEFAULT_BASE_CLASS_ATTRS.items()}
                    for k, v in loaded.items():
                        merged[k] = set(v)
                    return merged
            except Exception as e:
                printException(e, f"reading base_class_attrs from {ini_path}")
    except Exception as _e:
        # If something went wrong reading the INI, log and fall back to defaults.
        printException(_e, "loading base_class_attrs")
    return {k: set(v) for k, v in DEFAULT_BASE_CLASS_ATTRS.items()}

# BASE_CLASS_ATTRS is populated from defaults or from `.check_axioms.ini`.
BASE_CLASS_ATTRS: dict = _load_base_class_attrs_from_ini()


def _load_base_class_method_patterns_from_ini() -> dict:
    """
    Load base-class method name patterns from .check_axioms.ini.

    Looks for a `[base_class_methods]` section where each option is a
    base class name and its value is a comma-or-space-separated list of
    glob-style name patterns (e.g. `key_*`). Returns a mapping of
    lowercase base-class-name -> set(patterns).
    """
    try:
        cfg = configparser.ConfigParser()
        ini_path = Path(".check_axioms.ini")
        project_ini = ROOT / ".check_axioms.ini"
        if not ini_path.exists() and project_ini.exists():
            ini_path = project_ini
        if ini_path.exists():
            try:
                cfg.read(ini_path)
                if cfg.has_section("base_class_methods"):
                    loaded = {}
                    for k, v in cfg.items("base_class_methods"):
                        parts = [p.strip() for p in re.split(r"[,\s]+", v) if p.strip()]
                        loaded[k.lower()] = set(parts)
                    return loaded
            except Exception as e:
                printException(e, f"reading base_class_methods from {ini_path}")
    except Exception as _e:
        printException(_e, "loading base_class_method patterns")
    return {}


# BASE_CLASS_METHOD_PATTERNS maps base-class-name (lowercased) -> set of glob patterns
BASE_CLASS_METHOD_PATTERNS: dict = _load_base_class_method_patterns_from_ini()


def _class_inherits_from(enclosing_class: str | None, target_base_lower: str, class_bases: dict) -> bool:
    """
    Return True if `enclosing_class` (transitively) inherits from `target_base_lower`.

    `class_bases` is expected to be the mapping returned by
    `_collect_self_assigned_attrs` (class_name -> list of base-name strings).
    """
    if not enclosing_class:
        return False
    seen: set[str] = set()
    stack: list[str] = list(class_bases.get(enclosing_class, []) or [])
    while stack:
        b = stack.pop()
        simple = b.split(".")[-1]
        key = simple.lower()
        if key in seen:
            continue
        if key == target_base_lower:
            return True
        seen.add(key)
        child_bases = class_bases.get(b) or class_bases.get(simple)
        if child_bases:
            for nb in child_bases:
                if nb is not None:
                    stack.append(nb)
    return False


def _attr_provided_by_bases(enclosing_class: str | None, attr: str, class_bases: dict) -> bool:
    """
    Return True if any base class of `enclosing_class` is known to provide `attr`.

    `class_bases` is the mapping returned by `_collect_self_assigned_attrs`.
    """
    if not enclosing_class:
        return False
    # Walk the base-class graph transitively (BFS/stack) to account for
    # indirect bases: e.g., class A(BaseX): class B(A): then B should
    # inherit attributes provided by BaseX. Normalize keys to lowercase
    # for case-insensitive matching.
    seen: set[str] = set()
    stack: list[str] = list(class_bases.get(enclosing_class, []) or [])
    while stack:
        b = stack.pop()
        # consider simple name (last dotted component) and normalize
        simple = b.split(".")[-1]
        key = simple.lower()
        if key in seen:
            continue
        seen.add(key)
        provided = BASE_CLASS_ATTRS.get(key)
        if provided and attr in provided:
            return True
        # If this base is a class defined in the same module (present in
        # class_bases), enqueue its own bases to continue the traversal.
        # Try both the original and the simple name when looking up.
        child_bases = class_bases.get(b) or class_bases.get(simple)
        if child_bases:
            for nb in child_bases:
                if nb is not None:
                    stack.append(nb)
    return False


def _find_getattr_on_self(path: Path, text: str, tree: ast.AST) -> List[tuple[int, str, str, str]]:
    """
    Find usages of getattr(self, 'attr', ...) and return list of (lineno, attrname, func).

    `func` is the function name used ('getattr'). Does not currently
    attempt to resolve dynamic attribute names.
    """

    results: List[tuple[int, str, str, str]] = []

    # Some Python versions don't expose ast.Str (strings are ast.Constant).
    has_ast_Str = hasattr(ast, "Str")
    str_node_types = (ast.Constant, ast.Str) if has_ast_Str else (ast.Constant,)

    class Finder(ast.NodeVisitor):
        def __init__(self):
            self.stack: List[ast.AST] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.stack.append(node)
            super().generic_visit(node)
            self.stack.pop()

        def _enclosing_class(self) -> str | None:
            """Return the name of the enclosing ClassDef or None."""
            for anc in reversed(self.stack):
                if isinstance(anc, ast.ClassDef):
                    return anc.name
            return None

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
                                results.append((lineno, attr_name, node.func.id, self._enclosing_class()))
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
                                results.append((lineno, f"{first.attr}.{attr_name}", node.func.id, self._enclosing_class()))
            except Exception as e:
                printException(e, f"error walking AST for getattr detection in {path}")
            self.generic_visit(node)

    Finder().visit(tree)
    return results


def _find_hasattr_on_self(path: Path, text: str, tree: ast.AST) -> List[tuple[int, str, str, str]]:
    """
    Find usages of hasattr(self, 'attr') and hasattr(self.xyz, 'attr').

    Returns a list of (lineno, attrname, func, enclosing_class).
    For nested-self patterns, attrname is emitted as "xyz.attr".

    Only matches literal string attribute names. Does not attempt to resolve
    dynamic expressions.
    """

    results: List[tuple[int, str, str, str]] = []

    # Some Python versions don't expose ast.Str (strings are ast.Constant).
    has_ast_Str = hasattr(ast, "Str")
    str_node_types = (ast.Constant, ast.Str) if has_ast_Str else (ast.Constant,)

    class Finder(ast.NodeVisitor):
        def __init__(self):
            self.stack: List[ast.AST] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.stack.append(node)
            super().generic_visit(node)
            self.stack.pop()

        def _enclosing_class(self) -> str | None:
            """Return the name of the enclosing ClassDef or None."""
            for anc in reversed(self.stack):
                if isinstance(anc, ast.ClassDef):
                    return anc.name
            return None

        def visit_Call(self, node: ast.Call) -> None:
            try:
                if isinstance(node.func, ast.Name) and node.func.id == "hasattr" and len(node.args) >= 2:
                    first = node.args[0]
                    second = node.args[1]
                    # match hasattr(self, 'attr') where first arg is Name 'self'
                    if isinstance(first, ast.Name) and first.id == "self":
                        if has_ast_Str and isinstance(second, ast.Str):
                            attr_name = second.s
                        else:
                            attr_name = getattr(second, "value", None)
                        if isinstance(attr_name, str):
                            lineno = getattr(node, "lineno", None)
                            if lineno is not None:
                                results.append((lineno, attr_name, node.func.id, self._enclosing_class()))
                    # match hasattr(self.xyz, 'attr') where first arg is Attribute off self
                    if isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name) and first.value.id == "self":
                        if has_ast_Str and isinstance(second, ast.Str):
                            attr_name = second.s
                        else:
                            attr_name = getattr(second, "value", None)
                        if isinstance(attr_name, str):
                            lineno = getattr(node, "lineno", None)
                            if lineno is not None:
                                results.append((lineno, f"{first.attr}.{attr_name}", node.func.id, self._enclosing_class()))
            except Exception as e:
                printException(e, f"error walking AST for hasattr detection in {path}")
            self.generic_visit(node)

    Finder().visit(tree)
    return results


def check_prefer_no_direct_hasattrs(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """
    Flag redundant hasattr(self, 'attr') checks when `attr` is assigned in __init__/on_mount.

    Returns list of (filepath, lineno, message).
    """
    errs: List[Tuple[str, int, str]] = []
    try:
        classes, class_bases = _collect_self_assigned_attrs(path, text=text, tree=tree)
        hasattr_uses = _find_hasattr_on_self(path, text=text, tree=tree)
    except Exception as e:
        printException(e, f"collecting class attrs/hasattr usages in {path}")
        return errs

    assigned_attrs = set()
    for s in classes.values():
        assigned_attrs.update(s)

    for lineno, attr, func, encl in hasattr_uses:
        if "." in attr:
            right = attr.split(".", 1)[1]
            if right in assigned_attrs or _attr_provided_by_bases(encl, right, class_bases):
                errs.append(
                    (
                        str(path),
                        lineno,
                        f"hasattr(self.{attr.split('.', 1)[0]}, '{right}') used but '{right}' is known to exist; unnecessary check",
                    )
                )
        else:
            if attr in assigned_attrs or _attr_provided_by_bases(encl, attr, class_bases):
                errs.append(
                    (
                        str(path),
                        lineno,
                        f"{func}(self, '{attr}') used but '{attr}' is known to exist; unnecessary check",
                    )
                )

    return errs


def _find_parse_args_targets(path: Path, text: str, tree: ast.AST) -> set:
    """
    Return set of variable names assigned from a call to `*.parse_args()`.

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
    """
    Find usages of getattr(var, 'attr', ...) where var is in varnames.

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
    """
    Run the core AST-based checks for bare excepts, except-as-print, and related checks.

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
            errs.append((str(path), lineno, f"bare 'except [<type>]:' without 'as <var>' detected. Add the 'as <var>' and a {call_example} call (without its own try/except)."))

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
                            or name.startswith("_use_logging")
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
                errs += check_unnecessary_pass_in_except(path, text=text, tree=tree, call_example=call_example)
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
    """
    Detect try/except blocks where the try body contains only a single
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


def check_unnecessary_pass_in_except(path: Path, text: str, tree: ast.AST, call_example: str) -> List[Tuple[str, int, str]]:
    """
    Find `pass` statements inside `except ... as var:` blocks when other statements are present.

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
    """
    Detect try/except blocks where the try body contains only a single
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
    """
    Detect nested `try`/`except` where the outer `try` body is a single
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


def check_swallowing_callers(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """
    Detect functions or methods whose top-level body contains a Try with
    ExceptHandler(s) that do not contain any `raise` statements (i.e.
    they swallow exceptions). For such callees, find call sites within
    the same file that are wrapped in a Try/Except and flag the caller's
    try/except as likely unnecessary.

    This check is intentionally conservative: it only inspects top-level
    Try nodes in the callee's body and only considers explicit `raise`
    statements as evidence that the callee re-raises.
    """
    errs: List[Tuple[str, int, str]] = []

    try:
        swallowing: List[Tuple[str, Optional[str], int]] = []

        class FuncCollector(ast.NodeVisitor):
            def __init__(self):
                self.current_class: Optional[str] = None

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                prev = self.current_class
                self.current_class = node.name
                for item in node.body:
                    # inspect direct methods only
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.visit(item)
                self.current_class = prev

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                try:
                    for stmt in getattr(node, "body", []) or []:
                        if isinstance(stmt, ast.Try):
                            handlers = getattr(stmt, "handlers", []) or []
                            if not handlers:
                                continue
                            any_raise = False
                            for h in handlers:
                                for sub in ast.walk(h):
                                    if isinstance(sub, ast.Raise):
                                        any_raise = True
                                        break
                                if any_raise:
                                    break
                            if not any_raise:
                                swallowing.append((node.name, self.current_class, getattr(node, "lineno", 0)))
                                break
                except Exception as e:
                    printException(e, f"collecting swallowing functions in {path}")

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                # treat async defs the same way
                self.visit_FunctionDef(node)  # type: ignore[arg-type]

        FuncCollector().visit(tree)

        if not swallowing:
            return errs

        # Build quick lookup sets
        swallow_names = {name for name, cls, ln in swallowing}

        class CallFinder(ast.NodeVisitor):
            def __init__(self):
                self.stack: List[ast.AST] = []

            def generic_visit(self, node: ast.AST) -> None:
                self.stack.append(node)
                super().generic_visit(node)
                self.stack.pop()

            def _enclosing_function(self) -> Optional[str]:
                """Return the name of the enclosing function (if any)."""
                for anc in reversed(self.stack):
                    if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        return getattr(anc, "name", None)
                return None

            def _has_try_ancestor(self) -> Optional[ast.Try]:
                """
                Return the nearest enclosing ast.Try node whose `body` (or orelse)
                contains the current node. If the node is inside an ExceptHandler
                (i.e. the try's handler), treat it as not being in the try: portion
                and continue searching outer Try nodes.

                This checks the ancestor stack to ensure there is no ExceptHandler
                between the Try node and the current node.
                """
                # Walk ancestors from nearest to farthest
                for idx in range(len(self.stack) - 1, -1, -1):
                    anc = self.stack[idx]
                    if isinstance(anc, ast.Try):
                        # Any ExceptHandler between this Try and the current node
                        # indicates the current node is inside the except: branch
                        between = self.stack[idx + 1 :]
                        in_except = any(isinstance(b, ast.ExceptHandler) for b in between)
                        if in_except:
                            # skip this Try, keep looking for an outer Try
                            continue
                        # Otherwise, this Try encloses the node in its body/orelse
                        return anc
                return None

            def visit_Call(self, node: ast.Call) -> None:
                try:
                    func = getattr(node, "func", None)
                    callee_name = None
                    is_attr = False
                    if isinstance(func, ast.Name):
                        callee_name = func.id
                    elif isinstance(func, ast.Attribute):
                        callee_name = getattr(func, "attr", None)
                        is_attr = True
                    if not callee_name:
                        return

                    if callee_name not in swallow_names:
                        return

                    # Skip calls that occur inside the callee's own definition
                    encl_fn = self._enclosing_function()
                    if encl_fn and encl_fn == callee_name:
                        return

                    try_node = self._has_try_ancestor()
                    if try_node is None:
                        return

                    # Only flag when the Try's body is a single top-level
                    # statement and that statement contains this Call. This
                    # keeps the check conservative: callers that perform
                    # additional work in the try: portion likely need the
                    # exception handling and should not be flagged.
                    body = getattr(try_node, "body", []) or []
                    if len(body) != 1:
                        return

                    # Ensure the call is the direct/top-level expression in the sole body stmt.
                    sole_stmt = body[0]
                    is_direct_call = False
                    # Expr(value=Call(...))
                    if isinstance(sole_stmt, ast.Expr) and sole_stmt.value is node:
                        is_direct_call = True
                    # Assign/AnnAssign with value being the call
                    elif isinstance(sole_stmt, ast.Assign) and getattr(sole_stmt, "value", None) is node:
                        is_direct_call = True
                    elif isinstance(sole_stmt, ast.AnnAssign) and getattr(sole_stmt, "value", None) is node:
                        is_direct_call = True
                    # Return value being the call
                    elif isinstance(sole_stmt, ast.Return) and getattr(sole_stmt, "value", None) is node:
                        is_direct_call = True
                    if not is_direct_call:
                        return

                    lineno = getattr(node, "lineno", None) or 0
                    qual = callee_name
                    msg = (
                        f"call to '{qual}' at line {lineno} is the sole statement in a try:, but '{qual}' swallows exceptions (its handler contains no 'raise'); "
                        f"the caller's try/except may be safely removed"
                    )
                    try_lineno = getattr(try_node, "lineno", None) or 0
                    errs.append((str(path), try_lineno, msg))
                except Exception as e:
                    printException(e, f"inspecting Call nodes in {path}")
                self.generic_visit(node)

        CallFinder().visit(tree)
    except Exception as e:
        printException(e, f"check_swallowing_callers failed for {path}")

    return errs


def check_imports_module_level(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """
    Flag Import/ImportFrom nodes that are not at module top-level.

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
            """
            Return True when the current node stack indicates non-module scope.

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
    """
    Enforce the axiom: prefer direct attribute access when attributes are
    assigned in __init__ or on_mount. Finds getattr(self, 'attr', ...) uses
    where `attr` was assigned earlier in the class and reports them.
    """
    errs: List[Tuple[str, int, str]] = []
    try:
        classes, class_bases = _collect_self_assigned_attrs(path, text=text, tree=tree)
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

    for lineno, attr, func, encl in getattr_uses:
        # attr may be "app.current_path" style when getattr called on self.app
        if "." in attr:
            # treat 'app.current_path' -> attribute name 'current_path'
            right = attr.split(".", 1)[1]
            if right in assigned_attrs:
                errs.append((str(path), lineno, f"{func} used for guaranteed attribute '{attr}' (prefer direct access)"))
            # otherwise do not flag here; missing initialization is handled by
            # the separate check_getattr_not_initialized check.
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
    """
    Flag getattr(self, 'attr', ...) usages where `attr` is not assigned in __init__/on_mount.

    This helps catch cases where code relies on implicit attributes that should
    instead be initialized in the class initializer or guarded before use.
    """
    errs: List[Tuple[str, int, str]] = []
    try:
        classes, class_bases = _collect_self_assigned_attrs(path, text=text, tree=tree)
        getattr_uses = _find_getattr_on_self(path, text=text, tree=tree)
    except Exception as e:
        printException(e, f"collecting class attrs/getattr usages in {path}")
        return errs

    # Build the set of assigned attributes (from __init__/on_mount)
    assigned_attrs = set()
    for s in classes.values():
        assigned_attrs.update(s)

    for lineno, attr, func, encl in getattr_uses:
        # attr may be 'app.current_path' style when getattr called on self.app
        if "." in attr:
            right = attr.split(".", 1)[1]
            if right not in assigned_attrs:
                # If a base class is known to provide the attribute, skip
                # reporting to avoid false positives.
                if _attr_provided_by_bases(encl, right, class_bases):
                    continue
                errs.append(
                    (
                        str(path),
                        lineno,
                        f"{func} used for attribute '{attr}' but '{right}' is not initialized in __init__/on_mount; hint: initialize '{right}' in __init__ or on_mount or add them to config file",
                    )
                )
        else:
            if attr not in assigned_attrs:
                # If a base class is known to provide the attribute, skip
                # reporting to avoid false positives.
                if _attr_provided_by_bases(encl, attr, class_bases):
                    continue
                errs.append(
                    (
                        str(path),
                        lineno,
                        f"{func}(self, '{attr}', ...) used but '{attr}' is not initialized in __init__/on_mount; hint: initialize '{attr}' in __init__ or on_mount or add them to config file",
                    )
                )

    return errs


def check_docstrings(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """
    Ensure module-level functions and class methods have docstrings.

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
            """
            Check a function node for a missing docstring when it's a
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


def check_repeated_defs(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """
    Detect repeated function/method definitions.

    - Module-level functions with the same name declared multiple times.
    - Methods within the same class that share the same name.

    Reports each repeated definition (beyond the first) with its line number.
    """
    errs: List[Tuple[str, int, str]] = []

    def _is_overload_decorator(d: ast.AST) -> bool:
        try:
            if isinstance(d, ast.Name):
                return d.id == "overload"
            if isinstance(d, ast.Attribute):
                return getattr(d, "attr", "") == "overload"
        except Exception as _e:
            printException(_e, f"inspecting decorator for overload detection in {path}")
        return False

    try:
        # Collect defs: key = (class_name_or_None, name) -> list of (lineno, is_overload)
        defs: Dict[Tuple[Optional[str], str], List[Tuple[int, bool]]] = {}

        if getattr(tree, "body", None):
            for node in tree.body:
                # Module-level functions
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name
                    is_ov = any(_is_overload_decorator(d) for d in node.decorator_list or [])
                    defs.setdefault((None, name), []).append((node.lineno, is_ov))
                # Classes: collect direct methods only (skip nested classes/functions)
                elif isinstance(node, ast.ClassDef):
                    class_name = node.name
                    for member in getattr(node, "body", []):
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            name = member.name
                            is_ov = any(_is_overload_decorator(d) for d in member.decorator_list or [])
                            defs.setdefault((class_name, name), []).append((member.lineno, is_ov))
    except Exception as e:
        printException(e, f"collecting definitions for repeated-definitions check in {path}")
        return errs

    # Analyze collected defs
    for (class_name, name), entries in defs.items():
        if len(entries) <= 1:
            continue
        total = len(entries)
        non_overloads = [ln for ln, is_ov in entries if not is_ov]
        overloads = [ln for ln, is_ov in entries if is_ov]

        # Case: only overload signatures, no implementation -> report
        if len(non_overloads) == 0 and overloads:
            lineno = overloads[0]
            target = f"class {class_name}" if class_name else "module"
            errs.append((str(path), lineno, f"multiple @overload signatures for {target} '{name}' with no implementation"))
            continue

        # Case: multiple real implementations -> flag extras
        if len(non_overloads) > 1:
            # Keep first implementation, report subsequent ones
            first = sorted(non_overloads)[0]
            extras = [ln for ln in sorted(non_overloads) if ln != first]
            for ln in extras:
                target = f"class {class_name}" if class_name else "module"
                errs.append((str(path), ln, f"redefined {target} '{name}'; multiple non-@overload definitions"))

    return errs


def check_init_first(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """
    Ensure that if a class defines an `__init__` method, it is the first
    method (FunctionDef/AsyncFunctionDef) defined within the class body.

    Ignores module/class docstrings or assignments that may precede methods.
    Reports the `__init__` line when it's not the first method.
    """
    errs: List[Tuple[str, int, str]] = []
    try:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # collect direct methods in order
            methods: list[tuple[str, int]] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append((getattr(item, 'name', None), getattr(item, 'lineno', 0)))
            if not methods:
                continue
            # find __init__ if present
            init_positions = [i for i, (n, _) in enumerate(methods) if n == '__init__']
            if not init_positions:
                continue
            init_pos = init_positions[0]
            if init_pos != 0:
                init_lineno = methods[init_pos][1]
                first_name, first_lineno = methods[0]
                errs.append((str(path), init_lineno, f"__init__ in class '{node.name}' is not the first method defined (first method '{first_name}' at line {first_lineno}); move __init__ to be the first method."))
    except Exception as e:
        printException(e, f"checking __init__ first in {path}")
    return errs


def check_multiline_docstring_start(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """
    Enforce that multiline docstrings (containing a newline) start with a newline.

    For docstrings on modules, classes and functions: if the docstring value
    contains at least one '\n', then it should begin with a leading '\n'.
    This detects inconsistent triple-quote placement where the first
    line of the docstring appears on the same line as the opening quotes
    (e.g. opening quotes immediately followed by text) instead of the
    preferred form where the first content line begins on the next line
    after the opening quotes.

    For example, the following form is preferred for multiline docstrings:
    '''
    This is a multiline docstring with multiple lines
    that starts on the line after the opening quotes.
    '''
    instead of:
    '''This is a multiline docstring with multiple lines
    that starts on the same line as the opening quotes.
    '''

    Returns list of (filepath, lineno, message).
    """
    errs: List[Tuple[str, int, str]] = []

    def _check_docnode(node, docnode_expr):
        # docnode_expr is ast.Expr whose value is the constant docstring
        try:
            val = getattr(docnode_expr, "value", None)
            s = None
            if isinstance(val, ast.Constant) and isinstance(getattr(val, "value", None), str):
                s = val.value
            elif hasattr(ast, "Str") and isinstance(val, ast.Str):
                s = val.s
            elif isinstance(val, ast.JoinedStr):
                # JoinedStr (f-strings) - try to reconstruct simple literal parts
                pieces: list[str] = []
                for part in val.values:
                    if isinstance(part, ast.Constant) and isinstance(getattr(part, "value", None), str):
                        pieces.append(part.value)
                    elif hasattr(ast, "Str") and isinstance(part, ast.Str):
                        pieces.append(part.s)
                    else:
                        # non-literal part - give up
                        pieces = []
                        break
                if pieces:
                    s = "".join(pieces)
            if s is None:
                return
            if "\n" in s and not s.startswith("\n"):
                lineno = getattr(docnode_expr, "lineno", None) or getattr(node, "lineno", None) or 0
                errs.append((str(path), lineno, "multiline docstring should start with a newline"))
        except Exception as e:
            printException(e, f"checking multiline docstring at {path}")

    try:
        # Module docstring: locate module-level Expr if present
        if getattr(tree, "body", None):
            first = tree.body[0]
            val = getattr(first, "value", None)
            is_doc_expr = False
            if isinstance(first, ast.Expr):
                if isinstance(val, ast.Constant) and isinstance(getattr(val, "value", None), str):
                    is_doc_expr = True
                elif hasattr(ast, "Str") and isinstance(val, ast.Str):
                    is_doc_expr = True
                elif hasattr(ast, "JoinedStr") and isinstance(val, ast.JoinedStr):
                    is_doc_expr = True
            if is_doc_expr:
                _check_docnode(tree, first)

        # Class and function docstrings
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                try:
                    if getattr(node, "body", None):
                        first = node.body[0]
                        val = getattr(first, "value", None)
                        is_doc_expr = False
                        if isinstance(first, ast.Expr):
                            if isinstance(val, ast.Constant) and isinstance(getattr(val, "value", None), str):
                                is_doc_expr = True
                            elif hasattr(ast, "Str") and isinstance(val, ast.Str):
                                is_doc_expr = True
                            elif hasattr(ast, "JoinedStr") and isinstance(val, ast.JoinedStr):
                                is_doc_expr = True
                        if is_doc_expr:
                            _check_docnode(node, first)
                except Exception as e:
                    printException(e, f"checking docstring for node {getattr(node, 'name', '<anon>')} in {path}")
    except Exception as e:
        printException(e, f"walking AST for multiline docstring checks in {path}")

    return errs


def check_getattr_method_calls(path: Path, text: str, tree: ast.AST) -> List[Tuple[str, int, str]]:
    """
    Detect immediate calls of getattr(...)(...) or assignments from such calls.

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
            """
            Return the literal string value for ast.Str/ast.Constant nodes.

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


def _collect_defs_for_unused(path: Path, tree: ast.AST) -> List[Tuple[str, str, Optional[str], int]]:
    """
    Collect definitions in `tree` for unused analysis.

    Returns list of tuples: (kind, name, class_name_or_None, lineno)
    where kind is 'function' or 'method'.
    """
    defs: List[Tuple[str, str, Optional[str], int]] = []
    try:
        # Iterate top-level module body to collect module-level functions and
        # direct methods of classes (skip nested functions).
        if getattr(tree, 'body', None):
            for item in tree.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs.append(('function', item.name, None, getattr(item, 'lineno', 0)))
                elif isinstance(item, ast.ClassDef):
                    for m in item.body:
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            defs.append(('method', m.name, item.name, getattr(m, 'lineno', 0)))
    except Exception as e:
        printException(e, f"collecting defs for unused analysis in {path}")
    return defs


def _find_usages_in_tree(tree: ast.AST, target_names: set) -> List[Tuple[str, int]]:
    """Return list of (name, lineno) occurrences for Name or Attribute matches."""
    occ: List[Tuple[str, int]] = []

    class Finder(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            try:
                if node.id in target_names:
                    lineno = getattr(node, 'lineno', None)
                    if lineno is not None:
                        occ.append((node.id, lineno))
            except Exception as e:
                printException(e, "error visiting Name node")
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            try:
                attr = getattr(node, 'attr', None)
                if attr in target_names:
                    lineno = getattr(node, 'lineno', None)
                    if lineno is not None:
                        occ.append((attr, lineno))
            except Exception as e:
                printException(e, "error visiting Attribute node")
            self.generic_visit(node)

    try:
        Finder().visit(tree)
    except Exception as e:
        printException(e, "walking AST for usage finding")
    return occ


def check_unused_symbols(patterns: List[str], all_py_files: List[Path], root: Path) -> List[Tuple[str, int, str]]:
    """
    Analyze files matching `patterns` for definitions with no references in `all_py_files`.

    Returns list of error tuples (file, lineno, message).
    """
    errs: List[Tuple[str, int, str]] = []
    try:
        if not patterns:
            return errs

        # Resolve target files from patterns against all_py_files
        targets: List[Path] = []
        for p in all_py_files:
            for pat in patterns:
                try:
                    if fnmatch.fnmatch(p.name, pat) or fnmatch.fnmatch(str(p), pat):
                        targets.append(p)
                        break
                except Exception as e:
                    printException(e, f"invalid check-unused pattern {pat}")
        targets = sorted(set(targets))
        if not targets:
            return errs

        # For each target file, collect defs and only scan that file for usages.
        for t in targets:
            text, tree = load_source_and_ast(t)
            if tree is None:
                continue
            # collect class bases info for inheritance checks
            try:
                classes, class_bases = _collect_self_assigned_attrs(t, text=text, tree=tree)
            except Exception as e:
                printException(e, f"error collecting self-assigned attrs in {t}")
                classes, class_bases = {}, {}

            local_defs: List[dict] = []
            local_names: set = set()
            for kind, name, cls, lineno in _collect_defs_for_unused(t, tree):
                # skip dunder constructors and common special names
                if name.startswith('__') and name.endswith('__') and name != '__init__':
                    continue
                # If this is a method and it matches any implicit-method pattern
                # for a base class that this class inherits from, treat it as
                # implicitly used and do not include it in the analysis.
                implicit = False
                if kind == 'method' and cls:
                    for base_lower, patterns in BASE_CLASS_METHOD_PATTERNS.items():
                        try:
                            if _class_inherits_from(cls, base_lower, class_bases):
                                for pat in patterns:
                                    try:
                                        if fnmatch.fnmatch(name, pat):
                                            implicit = True
                                            break
                                    except Exception as e:
                                        printException(e, f"invalid implicit method pattern {pat} for base {base_lower}")
                                        # skip invalid pattern but continue checking other patterns
                                        continue
                            if implicit:
                                break
                        except Exception as e:
                            printException(e, f"error checking inheritance for class {cls} and base {base_lower}")
                            continue
                if implicit:
                    # don't add implicit methods to defs/target_names
                    continue
                local_defs.append({'path': t, 'kind': kind, 'name': name, 'class': cls, 'lineno': lineno})
                local_names.add(name)

            if not local_defs:
                continue

            # Find usages only within this file's AST
            usage_map: dict = {d['name']: [] for d in local_defs}
            try:
                occ = _find_usages_in_tree(tree, local_names)
                for name, lineno in occ:
                    usage_map.setdefault(name, []).append((t, lineno))
            except Exception as e:
                printException(e, f"finding usages in {t}")

            # Report defs that have no usages besides their own definition
            for d in local_defs:
                name = d['name']
                def_lineno = d['lineno']
                uses = usage_map.get(name, [])
                filtered = [u for u in uses if not (u[0] == t and u[1] == def_lineno)]
                if not filtered:
                    if d.get('kind') == 'method':
                        cls_name = d.get('class') or '<unknown>'
                        errs.append((str(t), def_lineno, f"unused method '{cls_name}.{name}' (no references found in file)"))
                    else:
                        errs.append((str(t), def_lineno, f"unused function '{name}' (no references found in file)"))
    except Exception as e:
        printException(e, "check_unused_symbols failed")
    return errs


def run_py_compile(py_files: List[Path]) -> List[Tuple[Path, str]]:
    """
    Run `py_compile` on each path in `py_files` and return failures.

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


def build_default_config_template() -> str:
    """Build a commented .check_axioms.ini template with defaults."""
    lines = [
        "[check]",
        "# check_axioms configuration file",
        "# Boolean options (true/false, yes/no, 1/0, on/off)",
        "",
        "# Bare except checks",
        "bare-excepts = true",
        "check-global-excepts = false",
        "",
        "# Exception handling checks",
        "except-as-print = true",
        "logger-in-try = true",
        "printexception-in-try = true",
        "",
        "# Attribute and initialization checks",
        "prefer-no-direct-hasattrs = true",
        "prefer-direct-attrs = true",
        "getattr-not-initialized = true",
        "check-getattr-methods = true",
        "",
        "# Code quality checks",
        "py-compile = true",
        "nested-try-except = true",
        "pass-check = true",
        "check-imports = true",
        "check-docstrings = true",
        "multiline-starts-with-newline = true",
        "check-repeat = true",
        "check-init-first = true",
        "",
        "# Swallowing exception callers",
        "swallowing-caller-check = true",
        "",
        "# Other options",
        "print = false",
        "verbose = 0",
    ]
    return "\n".join(lines) + "\n"


def handle_init_config(location: str) -> int:
    """
    Handle the --init-config action.

    Writes a commented template to .check_axioms.ini in the specified location.
    Returns an exit code suitable for returning from main().
    """
    target_dir = os.getcwd() if location == "cwd" else os.path.expanduser("~")
    target_path = os.path.join(target_dir, ".check_axioms.ini")
    try:
        if os.path.exists(target_path):
            print(f"config already exists: {target_path}", file=sys.stderr)
            return 2
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


def main(argv: List[str] | None = None) -> int:
    """
    Command-line entry point for the axiom checker.

    Parses flags, discovers Python files, runs AST checks and `py_compile`.
    Returns exit code 0 on success, 1 on violations.
    """
    parser = argparse.ArgumentParser(prog="check_axioms.py")
    parser.add_argument(
        "--init-config",
        dest="init_config",
        nargs="?",
        choices=["cwd", "home"],
        const="cwd",
        metavar="LOCATION",
        help="write a commented .check_axioms.ini template and exit (LOCATION: cwd or home, default: cwd)",
    )
    parser.add_argument(
        "--config",
        dest="config",
        metavar="FILE",
        help="use the specified config FILE instead of searching cwd and $HOME for .check_axioms.ini",
    )
    parser.add_argument("--root", "-r", default=str(ROOT), help="Project root to scan")
    parser.add_argument("--compile-only", action="store_true", help="Only run py_compile checks")

    # Paired options are represented as mutually-exclusive groups so users
    # cannot accidentally pass both the enable and disable variants.
    # Alphabetical ordering of groups: A, B, C, D, E, I, L, N, P, S, T

    # paired options to control redundant hasattr(self, 'attr') checks
    gh = parser.add_mutually_exclusive_group()
    gh.add_argument("-A",
        "--no-prefer-no-direct-hasattrs",
        dest="check_prefer_no_direct_hasattrs",
        action="store_false",
        help="Skip checking for redundant hasattr(self, ...) usages (default: check)",
    )
    gh.add_argument("-a",
        "--prefer-no-direct-hasattrs",
        dest="enable_prefer_no_direct_hasattrs",
        action="store_true",
        help="Enable prefer-no-direct-hasattrs check (opposite of -A).",
    )

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

    # New check: callers wrapping calls to functions/methods that swallow
    # exceptions (function body has a try/except whose handlers do not raise).
    gk = parser.add_mutually_exclusive_group()
    gk.add_argument("-K",
        "--no-swallowing-caller-check",
        dest="check_swallowing_callers",
        action="store_false",
        help="Skip checking for callers that wrap calls to functions that swallow exceptions (default: check)",
    )
    gk.add_argument("-k",
        "--swallowing-caller-check",
        dest="enable_swallowing_callers",
        action="store_true",
        help="Enable swallowing-caller check (opposite of -K).",
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
        "--no-check-getattr-methods",
        dest="check_getattr_methods",
        action="store_false",
        help="Skip checking for getattr(...) immediate-call usages (default: check)",
    )
    gu.add_argument(
        "--check-getattr-methods",
        dest="enable_check_getattr_methods",
        action="store_true",
        help="Enable check for immediate calls of getattr(...)(...) (opposite of --no-check-getattr-methods)",
    )
    
    # `--NONE` convenience flag: keep near the end of the options list
    parser.add_argument("--NONE",
        dest="none",
        action="store_true",
        help="Disable all checks (equivalent to -B -E -D -C etc.)",
    )
    # New paired options to control multiline-docstring-start rule
    gx = parser.add_mutually_exclusive_group()
    gx.add_argument("-X",
        "--no-multiline-starts-with-newline",
        dest="check_multiline_docstring_start",
        action="store_false",
        help="Skip checking that multiline docstrings start with a newline (default: check)",
    )
    gx.add_argument("-x",
        "--multiline-starts-with-newline",
        dest="enable_multiline_docstring_start",
        action="store_true",
        help="Enable multiline-docstring-start check (opposite of -X).",
    )

    # Repeated-definitions check (enable/disable)
    gr = parser.add_mutually_exclusive_group()
    gr.add_argument("-Y",
        "--no-check-repeat",
        dest="check_repeat",
        action="store_false",
        help="Skip checking for repeated function/method definitions (default: check)",
    )
    gr.add_argument("-y",
        "--check-repeat",
        dest="enable_check_repeat",
        action="store_true",
        help="Enable repeated-definitions check (opposite of -Y).",
    )

    # __init__-first check (enable/disable)
    gj = parser.add_mutually_exclusive_group()
    gj.add_argument("-J",
        "--no-check-init-first",
        dest="check_init_first",
        action="store_false",
        help="Skip enforcing __init__ as the first method in a class (default: check)",
    )
    gj.add_argument("-j",
        "--check-init-first",
        dest="enable_check_init_first",
        action="store_true",
        help="Require __init__ to be the first method defined within the class (opposite of -J)",
    )

    parser.add_argument(
        "--check-unused",
        dest="check_unused",
        action="append",
        default=[],
        help="File pattern(s) to analyze for unused module-level functions and class methods (may be specified multiple times). In config file, provide comma-separated values or multiple 'check-unused' entries.",
    )

    parser.add_argument("-v", "--verbose", dest="verbose", action="count", default=0,
        help="Increase verbosity (specify multiple times for more detail)."
    )
    parser.add_argument(
        "--reverse",
        dest="reverse",
        action="store_true",
        help="Print reported issues in reverse order (bottom-up).",
    )
    parser.add_argument("--ignore",
        dest="ignore",
        action="append",
        default=[],
        help="Glob pattern to ignore (may be specified multiple times). In config file, provide comma-separated values or multiple 'ignore' entries.",
    )
    parser.add_argument("files", nargs="*", help="Optional explicit files or directories to check (overrides discovery)")

    # Load optional configuration from .check_axioms.ini (cwd then $HOME).
    # Config keys are the long-option names without leading dashes; e.g.
    # "prefer-no-direct-hasattrs = false" maps to `--no-prefer-no-direct-hasattrs`.
    # Parse early to get --config if provided
    parsed_args, _ = parser.parse_known_args(argv)
    
    # Determine config files to use
    if getattr(parsed_args, "config", None):
        cfg_files = [Path(parsed_args.config)]
    else:
        cfg_files = [Path.cwd() / ".check_axioms.ini", Path.home() / ".check_axioms.ini"]
    cfg = configparser.ConfigParser()
    read_files = [str(p) for p in cfg_files if p.exists()]
    if read_files:
        try:
            cfg.read(read_files)
            # prefer [check] section if provided; otherwise use defaults()
            if "check" in cfg:
                src = cfg["check"]
            else:
                src = cfg.defaults()

            def _getbool(name: str):
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

            # Map canonical config key -> (enable_dest, disable_dest)
            optmap = {
                "prefer-no-direct-hasattrs": ("enable_prefer_no_direct_hasattrs", "check_prefer_no_direct_hasattrs"),
                "bare-excepts": ("enable_bare_excepts", "check_bare_excepts"),
                "py-compile": ("enable_py_compile", "check_py_compile"),
                "prefer-direct-attrs": ("enable_prefer_direct_attrs", "check_prefer_direct_attrs"),
                "except-as-print": ("enable_except_as_print", "check_except_as_print"),
                "check-imports": ("enable_check_imports", "check_imports"),
                "logger-in-try": ("enable_logger_in_try", "check_logger_in_try"),
                "printexception-in-try": ("enable_printexception_in_try", "check_printexception_in_try"),
                "nested-try-except": ("enable_nested_try", "check_nested_try"),
                "swallowing-caller-check": ("enable_swallowing_callers", "check_swallowing_callers"),
                "pass-check": ("enable_pass_check", "check_pass"),
                "check-docstrings": ("enable_check_docstrings", "check_docstrings"),
                "getattr-not-initialized": ("enable_getattr_not_initialized", "check_getattr_not_initialized"),
                "check-getattr-methods": ("enable_check_getattr_methods", "check_getattr_methods"),
                "multiline-starts-with-newline": ("enable_multiline_docstring_start", "check_multiline_docstring_start"),
                "check-repeat": ("enable_check_repeat", "check_repeat"),
                "check-init-first": ("enable_check_init_first", "check_init_first"),
            }

            single_map = {"print": "print_mode", "none": "none", "verbose": "verbose"}

            # Accumulate parser defaults from config
            defaults = {}

            # Collect ignore patterns from config: support 'ignore' key (comma-separated)
            # and any keys that start with 'ignore' (e.g. ignore1=...).
            ignored: List[str] = []
            for k, v in src.items():
                if not k:
                    continue
                if k == "ignore" or k.startswith("ignore"):
                    if v:
                        for part in v.split(","):
                            s = part.strip()
                            if s:
                                ignored.append(s)

            # Collect check-unused patterns similarly (support comma-separated and multiple entries)
            check_unused_patterns: List[str] = []
            for k, v in src.items():
                if not k:
                    continue
                if k == "check-unused" or k.startswith("check-unused"):
                    if v:
                        for part in v.split(","):
                            s = part.strip()
                            if s:
                                check_unused_patterns.append(s)

            if ignored:
                defaults["ignore"] = ignored
            if check_unused_patterns:
                defaults["check_unused"] = check_unused_patterns

            for key, (enable_dest, disable_dest) in optmap.items():
                b = _getbool(key)
                if b is None:
                    continue
                if b:
                    defaults[enable_dest] = True
                else:
                    defaults[disable_dest] = False

            for key, dest in single_map.items():
                b = _getbool(key)
                if b is None:
                    continue
                defaults[dest] = bool(b)

            if defaults:
                parser.set_defaults(**defaults)
        except Exception as e:
            printException(e, f"failed reading config files {read_files}")
            logger.warning("failed reading config files %s: %s", read_files, e)

    args = parser.parse_args(argv)

    # Handle --init-config and exit early
    if args.init_config is not None:
        return handle_init_config(args.init_config)

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
        args.check_prefer_no_direct_hasattrs = False
        args.check_multiline_docstring_start = False
        args.check_repeat = False
        args.check_init_first = False

    # Honor explicit small-letter re-enable flags after -N/--NONE
    if args.enable_prefer_no_direct_hasattrs:
        args.check_prefer_no_direct_hasattrs = True
    if args.enable_multiline_docstring_start:
        args.check_multiline_docstring_start = True
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
    if args.enable_check_repeat:
        args.check_repeat = True
    if args.enable_check_init_first:
        args.check_init_first = True
    if args.enable_swallowing_callers:
        args.check_swallowing_callers = True

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

    # Apply ignore patterns (glob) if provided via CLI or config
    try:
        patterns = args.ignore
        if patterns:
            filtered: List[Path] = []
            for p in py_files:
                skip = False
                for pat in patterns:
                    try:
                        if fnmatch.fnmatch(p.name, pat) or fnmatch.fnmatch(str(p), pat):
                            skip = True
                            break
                    except Exception as e:
                        printException(e, f"invalid ignore pattern {pat}")
                if not skip:
                    filtered.append(p)
            py_files = filtered
    except Exception as e:
        printException(e, "applying ignore patterns")

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
                # Enforce 'redundant hasattr(self, attr)' axiom
                if args.check_prefer_no_direct_hasattrs:
                    try:
                        errs += check_prefer_no_direct_hasattrs(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_prefer_no_direct_hasattrs failed for {p}")
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
                # Enforce multiline-docstring-start rule
                if args.check_multiline_docstring_start:
                    try:
                        errs += check_multiline_docstring_start(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_multiline_docstring_start failed for {p}")
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
                # Detect callers wrapping calls to functions/methods that swallow exceptions
                if args.check_swallowing_callers:
                    try:
                        errs += check_swallowing_callers(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_swallowing_callers failed for {p}")
                # Detect repeated definitions (module functions and class methods)
                if args.check_repeat:
                    try:
                        errs += check_repeated_defs(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_repeated_defs failed for {p}")
                # Enforce __init__ first rule when requested
                if args.check_init_first:
                    try:
                        errs += check_init_first(p, text=text, tree=tree)
                    except Exception as e:
                        printException(e, f"check_init_first failed for {p}")
            except Exception as e:
                printException(e, f"error checking {p}")
            if errs:
                all_errs.extend(errs)
        if all_errs:
            error_count += len(all_errs)
            # Sort errors by file path then numeric line number
            all_errs.sort(key=lambda t: (t[0], t[1]), reverse=args.reverse)

            print("Axiom violations detected:")
            for fpath, lineno, msg in all_errs:
                print(f"{fpath}:{lineno}: {msg}")
            print()

        # Optionally run unused-symbol analysis for configured patterns
        if args.check_unused:
            try:
                unused_errs = check_unused_symbols(args.check_unused, py_files, root)
                if unused_errs:
                    # extend and report immediately
                    unused_errs.sort(key=lambda t: (t[0], t[1]), reverse=args.reverse)
                    print("Unused symbol(s) detected:")
                    for fpath, lineno, msg in unused_errs:
                        print(f"{fpath}:{lineno}: {msg}")
                    print()
                    error_count += len(unused_errs)
            except Exception as e:
                printException(e, "check_unused analysis failed")

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
