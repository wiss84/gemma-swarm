"""
Gemma Swarm — Coding Agent: find_references tool.
Finds all usages/references of a symbol (function, class, variable) in a codebase.

Python: uses ast (semantic, built-in).
JS/TS:  uses ts_analysis_bridge (ts-morph / TypeScript Compiler API) — scope-aware,
        correctly handles aliased imports, namespace imports, CJS, comments, etc.
"""

import ast
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from tools.coding_tools import _workspace_root, _resolve_tool_path as _resolve_path
from tools.code_analysis_common import (
    _is_ts_bridge_available,
    _run_ts_bridge,
    _is_tree_sitter_available,
)

logger = logging.getLogger(__name__)


class FindReferencesInput(BaseModel):
    symbol_name: str = Field(
        description="Name of the symbol to find references for, e.g. 'my_function', 'MyClass', 'config_var'.",
    )
    root_path: str = Field(
        default="",
        description="Root directory to search. Defaults to workspace root.",
    )
    file_pattern: str = Field(
        default="**/*",
        description="Glob pattern to filter files, e.g. '**/*.py' or '**/*.ts'. Defaults to all supported files.",
    )


# ── Python (ast) ──────────────────────────────────────────────────────────────

def _find_python_references(symbol_name: str, file_path: Path) -> list[str]:
    """Find all references to a Python symbol using ast."""
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except Exception:
        return []

    references = []

    class ReferenceFinder(ast.NodeVisitor):
        def visit_Name(self, node):
            if node.id == symbol_name:
                references.append(f"  Line {node.lineno}: {source.splitlines()[node.lineno - 1].strip()}")
            self.generic_visit(node)

        def visit_Attribute(self, node):
            if isinstance(node.attr, str) and node.attr == symbol_name:
                references.append(f"  Line {node.lineno}: {source.splitlines()[node.lineno - 1].strip()}")
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            if node.name == symbol_name:
                references.append(f"  Line {node.lineno}: {source.splitlines()[node.lineno - 1].strip()} [definition]")
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            if node.name == symbol_name:
                references.append(f"  Line {node.lineno}: {source.splitlines()[node.lineno - 1].strip()} [definition]")
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            if node.name == symbol_name:
                references.append(f"  Line {node.lineno}: {source.splitlines()[node.lineno - 1].strip()} [definition]")
            self.generic_visit(node)

        def visit_Import(self, node):
            for alias in node.names:
                if alias.name == symbol_name or alias.asname == symbol_name:
                    references.append(f"  Line {node.lineno}: {source.splitlines()[node.lineno - 1].strip()} [import]")
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            for alias in node.names:
                if alias.name == symbol_name or alias.asname == symbol_name:
                    references.append(f"  Line {node.lineno}: {source.splitlines()[node.lineno - 1].strip()} [import]")
            self.generic_visit(node)

    ReferenceFinder().visit(tree)
    return references


# ── JS/TS (ts-morph bridge) ───────────────────────────────────────────────────

def _find_js_ts_references(symbol_name: str, root: Path, file: str | None = None) -> list[tuple[Path, list[str]]]:
    """
    Find all JS/TS references to symbol_name across the project rooted at root.
    Uses the ts_analysis_bridge (ts-morph) for scope-aware, semantic resolution.
    If file is provided, the bridge uses it as the starting point for definition lookup.

    Returns a list of (file_path, [formatted_line_strings]) tuples, grouped by file.
    Falls back to an error entry if the bridge is unavailable.
    """
    if not _is_ts_bridge_available():
        return [(root, ["  (ts_analysis_bridge not available — run npm install in tools/ts_analysis_bridge/)"])]

    bridge_kwargs = dict(symbol=symbol_name, root=str(root))
    if file:
        bridge_kwargs["file"] = file

    try:
        data = _run_ts_bridge("find_references", **bridge_kwargs)
    except RuntimeError as e:
        logger.warning(f"[find_references] ts_bridge error: {e}")
        return [(root, [f"  (ts_bridge error: {e})"])]

    refs = data.get("references", [])

    # Group by file
    grouped: dict[str, list[str]] = {}
    for ref in refs:
        fp = ref.get("file", "")
        line = ref.get("line", "?")
        text = ref.get("text", "")
        tag = " [definition]" if ref.get("is_definition") else ""
        grouped.setdefault(fp, []).append(f"  Line {line}: {text}{tag}")

    return [(Path(fp), lines) for fp, lines in grouped.items()]


# ── Tool ──────────────────────────────────────────────────────────────────────

@tool(args_schema=FindReferencesInput)
def find_references(symbol_name: str, root_path: str = "", file_pattern: str = "**/*") -> str:
    """
    Find every place a symbol is used OR defined across the entire project.
    Use this tool when you need to:
      - See all call sites of a function before refactoring or deleting it
      - Check whether a class/variable is actually used anywhere
      - Understand how a symbol flows across files before making changes
      - Confirm a rename will not break anything (use before rename_symbol)

    Output includes BOTH definition sites (tagged [definition]) and all usage sites,
    grouped by file with line numbers and the source line text.

    Supports .py, .js, .ts, .jsx, .tsx files.
    Python uses ast (scope-accurate, ignores comments and string literals).
    JS/TS uses TypeScript Compiler API (scope-aware: correctly distinguishes same-named
    symbols in different scopes, handles aliased/namespace/CommonJS imports).

    file_pattern examples:
      '**/*.py'  — Python files only
      '**/*.ts'  — TypeScript files only
      '**/*'     — all supported files (default)

    Returns a formatted report string. Returns an error string starting with '[' on failure.
    """
    if root_path:
        root = _resolve_path(root_path)
    else:
        root = _workspace_root()

    if not root.exists():
        return f"[find_references error: Path not found: {root}]"

    # ── Determine scope from file_pattern ────────────────────────────────────
    # file_pattern can be:
    #   '**/*'           → scan all supported files (default)
    #   '**/*.py'        → Python only
    #   '**/*.ts'        → TS only
    #   'src/utils.py'   → single specific file
    #   'src/**/*.py'    → subtree
    _pat = file_pattern.strip()
    _pat_lower = _pat.lower()
    want_py    = not any(x in _pat_lower for x in (".ts", ".tsx", ".js", ".jsx"))
    want_js_ts = not _pat_lower.endswith(".py") and ".py" not in _pat_lower

    # When a specific .py file is given directly (no glob), only scan that file.
    _specific_py_file: Path | None = None
    _specific_js_ts_file: str | None = None
    if want_py and "*" not in _pat and (_pat.endswith(".py")):
        candidate = (root / _pat) if not Path(_pat).is_absolute() else Path(_pat)
        if candidate.exists():
            _specific_py_file = candidate

    # ── Python ────────────────────────────────────────────────────────────────
    py_results = []
    if want_py:
        if _specific_py_file:
            py_files = [_specific_py_file]
        else:
            # Derive the glob pattern for Python files from file_pattern
            if "*" in _pat and ".py" in _pat_lower:
                py_files = sorted(root.glob(_pat))
            else:
                py_files = sorted(root.rglob("*.py"))
        _skip = {"__pycache__", ".venv", "venv", ".git"}
        py_files = [f for f in py_files if not any(p in _skip for p in f.parts)]

        for file_path in py_files:
            try:
                refs = _find_python_references(symbol_name, file_path)
                if refs:
                    py_results.append((file_path, refs))
            except Exception as e:
                logger.warning(f"[find_references] Error processing {file_path}: {e}")

    # ── JS/TS ─────────────────────────────────────────────────────────────────
    js_ts_results = []
    if want_js_ts:
        # Pass a specific file to the bridge if the pattern points to one JS/TS file
        specific_js_ts: str | None = None
        if "*" not in _pat and any(_pat_lower.endswith(ext) for ext in (".ts", ".tsx", ".js", ".jsx")):
            candidate = (root / _pat) if not Path(_pat).is_absolute() else Path(_pat)
            if candidate.exists():
                specific_js_ts = str(candidate)
        js_ts_results = _find_js_ts_references(symbol_name, root, file=specific_js_ts)

    # ── Output ────────────────────────────────────────────────────────────────
    lines = [f"References to '{symbol_name}':", "=" * 60, ""]

    if py_results:
        lines.append("Python Files:")
        lines.append("-" * 40)
        for file_path, refs in py_results:
            rel = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
            lines.append(f"  {rel}:")
            lines.extend(refs)
            lines.append("")

    if js_ts_results:
        lines.append("JavaScript/TypeScript Files:")
        lines.append("-" * 40)
        for file_path, refs in js_ts_results:
            rel = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
            lines.append(f"  {rel}:")
            lines.extend(refs)
            lines.append("")

    if not py_results and not js_ts_results:
        lines.append(f"(No references to '{symbol_name}' found in scanned files.)")

    return "\n".join(lines)
