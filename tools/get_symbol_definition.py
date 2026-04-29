"""
Gemma Swarm — Coding Agent: get_symbol_definition tool.
Finds where a symbol (function, class, variable) is defined in the codebase.

Python: uses ast (semantic, built-in).
JS/TS:  uses ts_analysis_bridge (ts-morph / TypeScript Compiler API) — correctly resolves
        arrow functions, re-exports, CJS exports, interfaces, enums, type aliases.
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
)

logger = logging.getLogger(__name__)


class GetSymbolDefinitionInput(BaseModel):
    symbol_name: str = Field(
        description="Name of the symbol to find definition for, e.g. 'my_function', 'MyClass'.",
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

def _find_python_definition(symbol_name: str, file_path: Path) -> str | None:
    """Find where a Python symbol is defined using ast."""
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except Exception:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == symbol_name:
            return f"  Function defined at line {node.lineno}"
        elif isinstance(node, ast.AsyncFunctionDef) and node.name == symbol_name:
            return f"  Async function defined at line {node.lineno}"
        elif isinstance(node, ast.ClassDef) and node.name == symbol_name:
            return f"  Class defined at line {node.lineno}"
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(target, ast.Name) and target.id == symbol_name:
                    return f"  Variable assigned at line {node.lineno}"

    # Check imports (symbol imported from elsewhere)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == symbol_name:
                    return f"  Imported from '{node.module}' at line {node.lineno}"

    return None


# ── JS/TS (ts-morph bridge) ───────────────────────────────────────────────────

# Maps ts-morph SyntaxKind names to human-readable labels
_KIND_LABELS = {
    "FunctionDeclaration": "Function",
    "ArrowFunction": "Arrow function",
    "MethodDeclaration": "Method",
    "ClassDeclaration": "Class",
    "InterfaceDeclaration": "Interface",
    "TypeAliasDeclaration": "Type alias",
    "EnumDeclaration": "Enum",
    "VariableDeclaration": "Variable",
    "VariableStatement": "Variable",
    "ExportAssignment": "Export",
}


def _find_js_ts_definitions(symbol_name: str, root: Path) -> list[tuple[Path, str]]:
    """
    Find JS/TS symbol definitions via ts_analysis_bridge.
    Returns a list of (file_path, description_string) tuples.
    """
    if not _is_ts_bridge_available():
        return [(root, "  (ts_analysis_bridge not available — run npm install in tools/ts_analysis_bridge/)")]

    try:
        data = _run_ts_bridge("get_definition", symbol=symbol_name, root=str(root))
    except RuntimeError as e:
        logger.warning(f"[get_symbol_definition] ts_bridge error: {e}")
        return [(root, f"  (ts_bridge error: {e})")]

    defs = data.get("definitions", [])
    results = []
    for d in defs:
        fp = Path(d.get("file", ""))
        line = d.get("line", "?")
        kind = _KIND_LABELS.get(d.get("kind", ""), d.get("kind", "Symbol"))
        text = d.get("text", "")
        results.append((fp, f"  {kind} defined at line {line}: {text}"))

    return results


# ── Tool ──────────────────────────────────────────────────────────────────────

@tool(args_schema=GetSymbolDefinitionInput)
def get_symbol_definition(symbol_name: str, root_path: str = "", file_pattern: str = "**/*") -> str:
    """
    Find exactly where a symbol is declared/defined in the codebase.
    Use this tool when you need to:
      - Locate the source file and line where a function, class, or variable is defined
      - Understand what kind of symbol something is (function, class, interface, type alias, enum)
      - Navigate to the origin of a symbol before reading or modifying it
      - Distinguish between a local definition and an import from another module

    Unlike find_references (which finds all usages), this tool focuses only on the
    declaration site — where the symbol is created, not where it is used.

    Output shows file path, line number, symbol kind, and the source line text.

    Supports .py, .js, .ts, .jsx, .tsx files.
    Python uses ast (detects functions, async functions, classes, variables, imports).
    JS/TS uses TypeScript Compiler API (detects functions, arrow functions, classes,
    interfaces, enums, type aliases, re-exports).

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
        return f"[get_symbol_definition error: Path not found: {root}]"

    want_py = "*.ts" not in file_pattern and "*.js" not in file_pattern
    want_js_ts = "*.py" not in file_pattern

    # ── Python ────────────────────────────────────────────────────────────────
    py_results = []
    if want_py:
        py_files = sorted(root.rglob("*.py"))
        _skip = {"__pycache__", ".venv", "venv", ".git"}
        py_files = [f for f in py_files if not any(p in _skip for p in f.parts)]

        for file_path in py_files:
            try:
                result = _find_python_definition(symbol_name, file_path)
                if result:
                    py_results.append((file_path, result))
            except Exception as e:
                logger.warning(f"[get_symbol_definition] Error processing {file_path}: {e}")

    # ── JS/TS ─────────────────────────────────────────────────────────────────
    js_ts_results = []
    if want_js_ts:
        js_ts_results = _find_js_ts_definitions(symbol_name, root)

    # ── Output ────────────────────────────────────────────────────────────────
    lines = [f"Definition of '{symbol_name}':", "=" * 60, ""]

    if py_results:
        lines.append("Python Files:")
        lines.append("-" * 40)
        for file_path, result in py_results:
            rel = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
            lines.append(f"  {rel}:")
            lines.append(result)
            lines.append("")

    if js_ts_results:
        lines.append("JavaScript/TypeScript Files:")
        lines.append("-" * 40)
        for file_path, result in js_ts_results:
            rel = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
            lines.append(f"  {rel}:")
            lines.append(result)
            lines.append("")

    if not py_results and not js_ts_results:
        lines.append(f"(No definition found for '{symbol_name}' in scanned files.)")

    return "\n".join(lines)
