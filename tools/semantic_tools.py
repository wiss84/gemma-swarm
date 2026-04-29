"""
Gemma Swarm — Coding Agent: Layer 7 Semantic Code Intelligence Tools
=====================================================================
AST-based tools that understand Python code structure, unlike grep_search
which is text-only. These tools parse the Abstract Syntax Tree (AST) of
Python files to find semantic meaning — ignoring symbol names inside
comments, strings, or unrelated scopes.

Tools:
    find_references(symbol_name, path)          — Find all usages of a symbol (Load contexts)
    get_symbol_definition(symbol_name, path)    — Find where a symbol is defined
    rename_symbol(old_name, new_name, path)     — Replace all semantic usages of a symbol across files
    analyze_module_dependencies(module_path)    — Map what a module imports and what imports it

Design notes:
    - All tools scan .py files recursively under the given path.
    - They handle: simple names, attribute access (self.method), imports,
      async functions, and augmented/annotated assignments.
    - Files with syntax errors are skipped gracefully (not a fatal error).
    - Output uses relative paths from the workspace root for clean LLM context.
    - Path can be a file OR a directory.

rename_symbol approach:
    Uses AST to locate exact line numbers, then applies targeted text
    replacement on those specific lines — not ast.unparse. This preserves
    all formatting, comments, and blank lines in every file.
"""

import ast
import logging
import difflib
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from tools.coding_tools import _workspace_root, _resolve_tool_path

logger = logging.getLogger(__name__)

MAX_SEMANTIC_MATCHES = 200   # Cap results to avoid flooding context
MAX_RENAME_FILES     = 50    # Safety cap: refuse bulk rename across > 50 files


# ── Input Schemas ──────────────────────────────────────────────────────────────

class SemanticSearchInput(BaseModel):
    symbol_name: str = Field(description="The name of the class, function, or variable to find.")
    path: str = Field(
        default=".",
        description="File or directory to search in. Defaults to workspace root."
    )


class RenameSymbolInput(BaseModel):
    old_name: str = Field(description="The exact symbol name to rename (case-sensitive).")
    new_name: str = Field(description="The new name to replace it with.")
    path: str = Field(
        default=".",
        description="File or directory to search in. Defaults to workspace root."
    )


class AnalyzeDependenciesInput(BaseModel):
    module_path: str = Field(
        description="Path to the .py file or package directory to analyze."
    )


# ── AST Visitors ──────────────────────────────────────────────────────────────

class ReferenceVisitor(ast.NodeVisitor):
    """
    Finds all usages (Load contexts) of a symbol across an AST.

    Covers:
        - Simple names:        my_func(), x = MyClass()
        - Attribute access:    self.my_method(), obj.MyClass
        - Import statements:   import symbol / from x import symbol
        - Import aliases:      import symbol as alias (matches both)
    """

    def __init__(self, symbol_name: str):
        self.symbol_name = symbol_name
        self.references: list[int] = []

    def visit_Name(self, node: ast.Name):
        # Simple name usage: func(), MyClass(), variable
        if node.id == self.symbol_name and isinstance(node.ctx, ast.Load):
            self.references.append(node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # Attribute access: self.method_name, obj.ClassName
        if node.attr == self.symbol_name and isinstance(node.ctx, ast.Load):
            self.references.append(node.lineno)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        # import symbol  OR  import symbol as alias
        for alias in node.names:
            base_name = alias.name.split(".")[-1]
            if base_name == self.symbol_name:
                self.references.append(node.lineno)
            elif alias.asname == self.symbol_name:
                self.references.append(node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        # from module import symbol  OR  from module import symbol as alias
        for alias in node.names:
            if alias.name == self.symbol_name:
                self.references.append(node.lineno)
            elif alias.asname == self.symbol_name:
                self.references.append(node.lineno)


class DefinitionVisitor(ast.NodeVisitor):
    """
    Finds the first definition of a symbol (function, async function, class,
    or variable assignment) across an AST.

    Covers:
        - def my_func(...)
        - async def my_func(...)
        - class MyClass(...)
        - x = value                  (simple assignment)
        - x: int = value             (annotated assignment)
        - x += value                 (augmented assignment)
        - x, y = ...                 (tuple unpacking)
        - [x, y] = ...               (list unpacking)
    """

    def __init__(self, symbol_name: str):
        self.symbol_name = symbol_name
        self.definition: int | None = None

    def _found(self, lineno: int):
        """Record first match only."""
        if self.definition is None:
            self.definition = lineno

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if node.name == self.symbol_name:
            self._found(node.lineno)
            return
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        if node.name == self.symbol_name:
            self._found(node.lineno)
            return
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        if node.name == self.symbol_name:
            self._found(node.lineno)
            return
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == self.symbol_name:
                self._found(node.lineno)
                return
            elif isinstance(target, (ast.Tuple, ast.List)):
                for elt in target.elts:
                    if isinstance(elt, ast.Name) and elt.id == self.symbol_name:
                        self._found(node.lineno)
                        return
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if isinstance(node.target, ast.Name) and node.target.id == self.symbol_name:
            self._found(node.lineno)
            return
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        if isinstance(node.target, ast.Name) and node.target.id == self.symbol_name:
            self._found(node.lineno)
            return
        self.generic_visit(node)


class AllReferencesVisitor(ast.NodeVisitor):
    """
    Collects all line numbers where a symbol name appears as a definition
    OR a reference. Used by rename_symbol to find every line that needs
    a targeted replacement.

    Intentionally broader than ReferenceVisitor — it also catches:
        - Store contexts (assignments, function args)
        - Definition sites (def, class, async def)
        - All import forms
    """

    def __init__(self, symbol_name: str):
        self.symbol_name = symbol_name
        self.lines: set[int] = set()

    def visit_Name(self, node: ast.Name):
        if node.id == self.symbol_name:
            self.lines.add(node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr == self.symbol_name:
            self.lines.add(node.lineno)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if node.name == self.symbol_name:
            self.lines.add(node.lineno)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        if node.name == self.symbol_name:
            self.lines.add(node.lineno)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        if node.name == self.symbol_name:
            self.lines.add(node.lineno)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            base_name = alias.name.split(".")[-1]
            if base_name == self.symbol_name or alias.asname == self.symbol_name:
                self.lines.add(node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        for alias in node.names:
            if alias.name == self.symbol_name or alias.asname == self.symbol_name:
                self.lines.add(node.lineno)

    def visit_arg(self, node: ast.arg):
        # Function parameter names: def foo(symbol_name: int)
        if node.arg == self.symbol_name:
            self.lines.add(node.lineno)
        self.generic_visit(node)


class ImportVisitor(ast.NodeVisitor):
    """
    Collects all imports from a single file's AST.
    Used by analyze_module_dependencies.

    Returns:
        imports: list of (module, names, lineno) tuples
            module — the module being imported (e.g. "os.path", "tools.coding_tools")
            names  — list of specific names imported, or [] for plain `import module`
            lineno — line number of the import statement
    """

    def __init__(self):
        self.imports: list[tuple[str, list[str], int]] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append((alias.name, [], node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        # Handle relative imports: level > 0 means "from . import ..." or "from .. import ..."
        if node.level > 0:
            module = ("." * node.level) + module
        names = [alias.name for alias in node.names]
        self.imports.append((module, names, node.lineno))


# ── Internal helpers ───────────────────────────────────────────────────────────

def _collect_py_files(resolved: Path) -> list[Path]:
    """Return sorted list of .py files from a file or directory path."""
    if resolved.is_file():
        return [resolved] if resolved.suffix == ".py" else []
    return sorted(
        f for f in resolved.rglob("*.py")
        if f.is_file()
        and "__pycache__" not in f.parts
        and not any(part.startswith(".") for part in f.parts)
    )


def _rel(file: Path) -> str:
    """Return a workspace-relative path string, or absolute if outside workspace."""
    try:
        return str(file.relative_to(_workspace_root()))
    except ValueError:
        return str(file)


def _replace_word_on_lines(
    source_lines: list[str],
    target_lines: set[int],
    old_name: str,
    new_name: str,
) -> list[str]:
    """
    Replace all whole-word occurrences of old_name with new_name,
    but only on the specific line numbers identified by the AST visitor.
    Uses word-boundary regex so 'foo' doesn't match 'foobar'.
    Preserves all other content on the line (comments, spacing, etc.).
    Line numbers are 1-based (as returned by the AST).
    """
    import re
    pattern = re.compile(r'\b' + re.escape(old_name) + r'\b')
    result = []
    for i, line in enumerate(source_lines, 1):
        if i in target_lines:
            result.append(pattern.sub(new_name, line))
        else:
            result.append(line)
    return result


# ── Tool 1: find_references ────────────────────────────────────────────────────

@tool(args_schema=SemanticSearchInput)
def find_references(symbol_name: str, path: str = ".") -> str:
    """
    Find all semantic usages of a symbol (class, function, or variable) in Python files.
    Uses the AST — ignores occurrences inside comments or unrelated strings.
    Catches: direct calls, attribute access (self.method), imports, and import aliases.
    Returns file:line pairs, or an error string starting with '['.
    """
    try:
        resolved = _resolve_tool_path(path)

        if not resolved.exists():
            return f"[find_references error: Path not found: {resolved}]"

        files = _collect_py_files(resolved)
        if not files:
            return f"[find_references error: No .py files found in: {resolved}]"

        all_matches: list[str] = []
        parse_errors: list[str] = []
        truncated = False

        for file in files:
            try:
                tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
                visitor = ReferenceVisitor(symbol_name)
                visitor.visit(tree)

                for lineno in visitor.references:
                    all_matches.append(f"{_rel(file)}:{lineno}")
                    if len(all_matches) >= MAX_SEMANTIC_MATCHES:
                        truncated = True
                        break

            except SyntaxError:
                parse_errors.append(_rel(file))
                continue
            except Exception as e:
                parse_errors.append(f"{_rel(file)} ({e})")
                continue

            if truncated:
                break

        if not all_matches:
            note = f" ({len(parse_errors)} file(s) skipped due to syntax errors)" if parse_errors else ""
            return f"No references found for symbol '{symbol_name}'{note}"

        header = f"Found {len(all_matches)} reference(s) for '{symbol_name}':\n"
        body = "\n".join(all_matches)
        footer = ""
        if truncated:
            footer = f"\n\n[Capped at {MAX_SEMANTIC_MATCHES} results. Narrow the search path.]"
        if parse_errors:
            footer += f"\n[Skipped {len(parse_errors)} file(s) with syntax errors: {', '.join(parse_errors[:5])}]"

        return header + body + footer

    except Exception as e:
        return f"[find_references error: {e}]"


# ── Tool 2: get_symbol_definition ──────────────────────────────────────────────

@tool(args_schema=SemanticSearchInput)
def get_symbol_definition(symbol_name: str, path: str = ".") -> str:
    """
    Find where a symbol (class, function, or variable) is first defined in Python files.
    Uses the AST — handles def, async def, class, assignments, annotated assignments,
    augmented assignments, and tuple/list unpacking.
    Returns the file and line number of the first definition found.
    Returns an error string starting with '[' on failure.
    """
    try:
        resolved = _resolve_tool_path(path)

        if not resolved.exists():
            return f"[get_symbol_definition error: Path not found: {resolved}]"

        files = _collect_py_files(resolved)
        if not files:
            return f"[get_symbol_definition error: No .py files found in: {resolved}]"

        parse_errors: list[str] = []

        for file in files:
            try:
                tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
                visitor = DefinitionVisitor(symbol_name)
                visitor.visit(tree)

                if visitor.definition is not None:
                    return (
                        f"Symbol '{symbol_name}' defined in "
                        f"{_rel(file)} at line {visitor.definition}"
                    )

            except SyntaxError:
                parse_errors.append(_rel(file))
                continue
            except Exception as e:
                parse_errors.append(f"{_rel(file)} ({e})")
                continue

        note = ""
        if parse_errors:
            note = f" ({len(parse_errors)} file(s) skipped due to syntax errors)"
        return f"Definition for symbol '{symbol_name}' not found{note}."

    except Exception as e:
        return f"[get_symbol_definition error: {e}]"


# ── Tool 3: rename_symbol ──────────────────────────────────────────────────────

@tool(args_schema=RenameSymbolInput)
def rename_symbol(old_name: str, new_name: str, path: str = ".") -> str:
    """
    Rename a symbol (class, function, variable) everywhere it appears across Python files.
    Uses the AST to identify exactly which lines contain the symbol, then applies
    targeted whole-word text replacement on those lines only — preserving all
    formatting, comments, and blank lines in every file.

    This is NOT ast.unparse — it never rewrites a file from scratch.
    It only modifies the specific lines the AST says the symbol is on.

    Safe to use for:
        - Renaming a class/function across an entire codebase
        - Updating a constant or variable name in multiple files
        - Any multi-file change where you'd otherwise call edit_file 10+ times

    Returns a per-file diff summary, or an error string starting with '['.
    Refuses if the change would touch more than 50 files (safety cap).
    """
    if not old_name or not new_name:
        return "[rename_symbol error: old_name and new_name must both be non-empty]"
    if old_name == new_name:
        return "[rename_symbol error: old_name and new_name are identical — nothing to do]"

    # Basic identifier validation
    if not new_name.isidentifier():
        return f"[rename_symbol error: '{new_name}' is not a valid Python identifier]"

    try:
        resolved = _resolve_tool_path(path)

        if not resolved.exists():
            return f"[rename_symbol error: Path not found: {resolved}]"

        files = _collect_py_files(resolved)
        if not files:
            return f"[rename_symbol error: No .py files found in: {resolved}]"

        # ── Pass 1: find which files actually contain the symbol ───────────────
        # Only parse files that contain the name as a raw string first (fast pre-filter).
        candidate_files: list[tuple[Path, set[int]]] = []
        parse_errors: list[str] = []

        for file in files:
            raw = file.read_text(encoding="utf-8", errors="replace")
            if old_name not in raw:
                continue   # fast skip — symbol definitely not here
            try:
                tree = ast.parse(raw)
                visitor = AllReferencesVisitor(old_name)
                visitor.visit(tree)
                if visitor.lines:
                    candidate_files.append((file, visitor.lines))
            except SyntaxError:
                parse_errors.append(_rel(file))
                continue
            except Exception as e:
                parse_errors.append(f"{_rel(file)} ({e})")
                continue

        if not candidate_files:
            note = f" ({len(parse_errors)} file(s) skipped)" if parse_errors else ""
            return f"Symbol '{old_name}' not found in any Python files{note}. Nothing changed."

        if len(candidate_files) > MAX_RENAME_FILES:
            return (
                f"[rename_symbol refused: Would modify {len(candidate_files)} files "
                f"(safety cap is {MAX_RENAME_FILES}). "
                f"Narrow the path to a specific subdirectory.]"
            )

        # ── Pass 2: apply targeted replacement and write ───────────────────────
        results: list[str] = []
        total_replacements = 0

        for file, target_lines in candidate_files:
            try:
                original = file.read_text(encoding="utf-8", errors="replace")
                original_lines = original.splitlines(keepends=True)

                updated_lines = _replace_word_on_lines(
                    original_lines, target_lines, old_name, new_name
                )
                updated = "".join(updated_lines)

                if updated == original:
                    # AST found lines but regex found no whole-word matches
                    # (can happen with aliases or complex expressions — skip safely)
                    continue

                file.write_text(updated, encoding="utf-8")

                # Build a compact diff for the report
                diff = difflib.unified_diff(
                    original_lines,
                    updated_lines,
                    fromfile=f"a/{_rel(file)}",
                    tofile=f"b/{_rel(file)}",
                    n=2,
                )
                diff_text = "".join(diff)
                replacements = len(target_lines)
                total_replacements += replacements
                results.append(
                    f"  ✓ {_rel(file)}  ({replacements} line(s) changed)\n{diff_text}"
                )
                logger.info(f"[rename_symbol] Updated {_rel(file)} ({replacements} lines)")

            except Exception as e:
                results.append(f"  ✗ {_rel(file)}: error — {e}")

        if not results:
            return f"Symbol '{old_name}' found in AST but no substitutions were made. Check if it's part of a larger identifier."

        summary = (
            f"rename_symbol: '{old_name}' → '{new_name}'\n"
            f"Modified {len(results)} file(s), {total_replacements} line(s) total\n"
            + "─" * 60 + "\n"
        )
        if parse_errors:
            summary += f"Skipped {len(parse_errors)} file(s) with syntax errors: {', '.join(parse_errors[:5])}\n"
            summary += "─" * 60 + "\n"

        return summary + "\n".join(results)

    except Exception as e:
        return f"[rename_symbol error: {e}]"


# ── Tool 4: analyze_module_dependencies ───────────────────────────────────────

@tool(args_schema=AnalyzeDependenciesInput)
def analyze_module_dependencies(module_path: str) -> str:
    """
    Analyze the import dependencies of a Python module or package.
    Shows two things:
        1. What this module imports (its outgoing dependencies)
        2. Which other modules in the project import it (its incoming dependents)

    Use this before refactoring a module to understand:
        - What it depends on (risk of breaking if those change)
        - What depends on it (files that will need updating if you change this module's API)

    Returns a structured dependency report, or an error string starting with '['.
    """
    try:
        resolved = _resolve_tool_path(module_path)

        if not resolved.exists():
            return f"[analyze_module_dependencies error: Path not found: {resolved}]"

        # ── Step 1: collect the target file(s) ────────────────────────────────
        target_files = _collect_py_files(resolved)
        if not target_files:
            return f"[analyze_module_dependencies error: No .py files found at: {resolved}]"

        # ── Step 2: parse outgoing imports from target ─────────────────────────
        outgoing: list[str] = []   # what this module imports
        parse_errors: list[str] = []

        for file in target_files:
            try:
                tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
                visitor = ImportVisitor()
                visitor.visit(tree)

                for module, names, lineno in visitor.imports:
                    if names:
                        names_str = ", ".join(names[:6])
                        if len(names) > 6:
                            names_str += f", ... (+{len(names) - 6} more)"
                        outgoing.append(f"  line {lineno:>4}: from {module} import {names_str}")
                    else:
                        outgoing.append(f"  line {lineno:>4}: import {module}")

            except SyntaxError:
                parse_errors.append(_rel(file))
            except Exception as e:
                parse_errors.append(f"{_rel(file)} ({e})")

        # ── Step 3: scan the whole workspace for files that import this module ─
        # Build the set of module names / relative import paths we're looking for.
        # E.g. if target is tools/coding_tools.py, we look for:
        #   "tools.coding_tools", "coding_tools", and relative "from .coding_tools import ..."
        workspace = _workspace_root()
        all_project_files = _collect_py_files(workspace)

        # Build candidate module name patterns from target file paths
        target_module_names: set[str] = set()
        for file in target_files:
            # Dotted path from workspace root: tools/coding_tools.py → tools.coding_tools
            try:
                rel = file.relative_to(workspace)
                parts = list(rel.parts)
                parts[-1] = parts[-1].removesuffix(".py")  # drop .py
                if parts[-1] == "__init__":
                    parts = parts[:-1]   # package import — drop __init__
                target_module_names.add(".".join(parts))
                target_module_names.add(parts[-1])   # bare name as fallback
            except ValueError:
                target_module_names.add(file.stem)

        incoming: dict[str, list[str]] = {}   # rel_path → list of import lines

        for file in all_project_files:
            if file in target_files:
                continue   # skip self-references
            try:
                raw = file.read_text(encoding="utf-8", errors="replace")
                # Fast pre-filter: any target name in raw text?
                if not any(name in raw for name in target_module_names):
                    continue
                tree = ast.parse(raw)
                visitor = ImportVisitor()
                visitor.visit(tree)

                matched_imports: list[str] = []
                for module, names, lineno in visitor.imports:
                    # Normalize relative imports for matching
                    module_bare = module.lstrip(".")
                    if any(
                        module_bare == t or module_bare.endswith("." + t)
                        for t in target_module_names
                    ):
                        if names:
                            names_str = ", ".join(names[:4])
                            if len(names) > 4:
                                names_str += f" (+{len(names) - 4} more)"
                            matched_imports.append(
                                f"    line {lineno:>4}: from {module} import {names_str}"
                            )
                        else:
                            matched_imports.append(f"    line {lineno:>4}: import {module}")

                if matched_imports:
                    incoming[_rel(file)] = matched_imports

            except SyntaxError:
                continue
            except Exception:
                continue

        # ── Step 4: format report ──────────────────────────────────────────────
        target_label = _rel(resolved) if resolved.is_file() else f"{_rel(resolved)}/ (package)"
        lines = [
            f"Dependency analysis for: {target_label}",
            "═" * 60,
        ]

        # Outgoing
        lines.append(f"\n▶ IMPORTS ({len(outgoing)} statement(s)) — what this module depends on:")
        if outgoing:
            lines.extend(outgoing)
        else:
            lines.append("  (no imports found)")

        # Incoming
        lines.append(f"\n◀ IMPORTED BY ({len(incoming)} file(s)) — what depends on this module:")
        if incoming:
            for dep_file, import_lines in sorted(incoming.items()):
                lines.append(f"  {dep_file}")
                lines.extend(import_lines)
        else:
            lines.append("  (no other project files import this module)")

        if parse_errors:
            lines.append(f"\n⚠ Skipped {len(parse_errors)} file(s) with syntax errors: {', '.join(parse_errors[:5])}")

        return "\n".join(lines)

    except Exception as e:
        return f"[analyze_module_dependencies error: {e}]"
