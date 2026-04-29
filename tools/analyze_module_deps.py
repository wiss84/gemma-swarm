"""
Gemma Swarm — Coding Agent: analyze_module_dependencies tool.
Analyzes import/require statements across Python, JavaScript, and TypeScript files.
Uses ast for Python, tree-sitter for JS/TS.
"""

import ast
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from tools.coding_tools import _workspace_root, _resolve_tool_path as _resolve_path
from tools.code_analysis_common import (
    get_js_ts_imports,
    _is_tree_sitter_available,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx"}
MAX_FILES_SCAN = 50
MAX_OUTPUT_CHARS = 10_000


# ── Input schema ─────────────────────────────────────────────────────

class AnalyzeModuleDepsInput(BaseModel):
    root_path: str = Field(
        default="",
        description=(
            "Root directory to scan for source files. "
            "Defaults to workspace root. Scans .py, .js, .ts, .jsx, .tsx files."
        ),
    )
    file_pattern: str = Field(
        default="",
        description=(
            "Optional: only analyze files matching this glob pattern, e.g. 'src/**/*.ts'. "
            "Leave empty to scan all supported files in root_path."
        ),
    )


# ── Python import analysis (uses built-in ast) ─────────────────────

def _get_python_imports(file_path: Path) -> list[str]:
    """Extract import statements from a Python file using ast."""
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except Exception as e:
        logger.warning(f"[analyze_deps] Failed to parse {file_path.name}: {e}")
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # from X import Y → report X
                imports.append(node.module)
    return imports


# ── JS/TS import analysis (uses tree-sitter) ─────────────────────

def _get_js_ts_file_imports(file_path: Path) -> list[str]:
    """Extract import/require statements from a JS/TS file using tree-sitter."""
    ext = file_path.suffix.lower()
    lang = "ts" if ext in (".ts", ".tsx") else "js"
    try:
        return get_js_ts_imports(file_path, lang=lang)
    except Exception as e:
        logger.warning(f"[analyze_deps] Failed to analyze {file_path.name}: {e}")
        return []


# ── Main tool ────────────────────────────────────────────────────────

@tool(args_schema=AnalyzeModuleDepsInput)
def analyze_module_dependencies(root_path: str = "", file_pattern: str = "") -> str:
    """
    Analyze module dependencies across Python, JavaScript, and TypeScript files.
    Finds all import / require / export statements and summarizes dependencies per file.
    Groups results by language (Python vs JS/TS) with clear labels.
    Call this to understand what external modules a project depends on.
    Returns an error string starting with '[' on failure.
    """
    # Resolve root path
    if root_path:
        root = _resolve_path(root_path)
    else:
        root = _workspace_root()

    if not root.exists():
        return f"[analyze_module_dependencies error: Path not found: {root}]"

    # Collect files to analyze
    if file_pattern:
        files = sorted(root.glob(file_pattern))
    else:
        files = []
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(root.rglob(f"*{ext}"))
        files = sorted(set(files))

    # Filter out noise directories
    _skip = {"__pycache__", "node_modules", ".venv", "venv", ".git",
              ".tox", ".pytest_cache", ".mypy_cache", "dist", "build"}
    files = [f for f in files if not any(part in _skip for part in f.parts)]

    if not files:
        return f"[analyze_module_dependencies: No supported files found in {root}]"

    if len(files) > MAX_FILES_SCAN:
        logger.warning(f"[analyze_deps] Limiting scan to {MAX_FILES_SCAN} files (found {len(files)})")
        files = files[:MAX_FILES_SCAN]

    # Analyze each file
    py_results = []
    js_ts_results = []

    for file_path in files:
        ext = file_path.suffix.lower()

        if ext == ".py":
            imports = _get_python_imports(file_path)
            if imports:
                py_results.append((file_path, imports))

        elif ext in (".js", ".jsx", ".ts", ".tsx"):
            if not _is_tree_sitter_available():
                js_ts_results.append((file_path, ["(tree-sitter not installed)"]))
                continue
            imports = _get_js_ts_file_imports(file_path)
            if imports:
                js_ts_results.append((file_path, imports))

    # Build output
    lines = [f"Module dependencies for: {root}", "=" * 60, ""]

    if py_results:
        lines.append("Python Files:")
        lines.append("-" * 40)
        for file_path, imports in py_results:
            rel = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
            lines.append(f"  {rel}:")
            for imp in imports:
                lines.append(f"    • {imp}")
            lines.append("")

    if js_ts_results:
        lines.append("JavaScript/TypeScript Files:")
        lines.append("-" * 40)
        for file_path, imports in js_ts_results:
            rel = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
            lines.append(f"  {rel}:")
            for imp in imports:
                lines.append(f"    • {imp}")
            lines.append("")

    if not py_results and not js_ts_results:
        lines.append("(No import statements found in scanned files.)")

    output = "\n".join(lines)
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n...[truncated]"

    return output
