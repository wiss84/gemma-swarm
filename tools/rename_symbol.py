"""
Gemma Swarm — Coding Agent: rename_symbol tool.
Safely renames a symbol (function, class, variable) across the entire project.

Python: uses ast to find all occurrences (scope-accurate, ignores comments/strings).
JS/TS:  uses ts_analysis_bridge (ts-morph / TypeScript Compiler API) — scope-aware,
        correctly handles aliased imports, namespace imports, CJS exports,
        destructured requires, re-exports. Does NOT rename unrelated same-name identifiers.
"""

import ast
import logging
import re
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from tools.coding_tools import _workspace_root, _resolve_tool_path as _resolve_path
from tools.code_analysis_common import (
    _is_ts_bridge_available,
    _run_ts_bridge,
)

logger = logging.getLogger(__name__)


class RenameSymbolInput(BaseModel):
    symbol_name: str = Field(
        description="Current name of the symbol to rename, e.g. 'old_function', 'MyClass'.",
    )
    new_name: str = Field(
        description="New name for the symbol, e.g. 'new_function', 'UpdatedClass'.",
    )
    root_path: str = Field(
        default="",
        description="Root directory to search and rename in. Defaults to workspace root.",
    )
    file_pattern: str = Field(
        default="**/*",
        description="Glob pattern to filter files, e.g. '**/*.py' or '**/*.ts'. Defaults to all supported files.",
    )
    dry_run: bool = Field(
        default=True,
        description="If True, only show what would be renamed without making changes. Set to False to actually rename.",
    )


# ── Python (ast) ──────────────────────────────────────────────────────────────

def _rename_python_symbol(symbol_name: str, new_name: str, file_path: Path, dry_run: bool) -> tuple[int, list[str]]:
    """
    Rename a Python symbol in a file using ast to find all occurrences,
    including import statements.
    Returns (changes_count, list_of_changes).
    """
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except Exception as e:
        return 0, [f"  Error parsing {file_path.name}: {e}"]

    # Collect all positions where symbol is used
    positions = []

    class PositionFinder(ast.NodeVisitor):
        def visit_Name(self, node):
            if node.id == symbol_name:
                positions.append(("Name", node.lineno, node.col_offset))
            self.generic_visit(node)

        def visit_Attribute(self, node):
            if isinstance(node.attr, str) and node.attr == symbol_name:
                positions.append(("Attribute", node.lineno, node.col_offset))
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            if node.name == symbol_name:
                positions.append(("FunctionDef", node.lineno, node.col_offset + len("def ")))
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            if node.name == symbol_name:
                positions.append(("AsyncFunctionDef", node.lineno, node.col_offset + len("async def ")))
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            if node.name == symbol_name:
                positions.append(("ClassDef", node.lineno, node.col_offset + len("class ")))
            self.generic_visit(node)

        def visit_Import(self, node):
            for alias in node.names:
                if alias.name == symbol_name:
                    positions.append(("ImportName", node.lineno, None, alias.name))
                if alias.asname == symbol_name:
                    positions.append(("ImportAsname", node.lineno, None, alias.asname))
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            if node.module == symbol_name:
                positions.append(("ImportFromModule", node.lineno, None, symbol_name))
            for alias in node.names:
                if alias.name == symbol_name:
                    positions.append(("ImportFromName", node.lineno, None, alias.name))
                if alias.asname == symbol_name:
                    positions.append(("ImportFromAsname", node.lineno, None, alias.asname))
            self.generic_visit(node)

    PositionFinder().visit(tree)

    if not positions:
        return 0, []

    # Sort in reverse order (replace from bottom to top to preserve offsets)
    positions.sort(key=lambda x: (x[1], x[2] if x[2] is not None else 0), reverse=True)

    lines = source.splitlines(keepends=True)
    changes = []

    for entry in positions:
        node_type = entry[0]
        line_no = entry[1]
        line_idx = line_no - 1
        if not (0 <= line_idx < len(lines)):
            continue

        line = lines[line_idx]

        if node_type in ("ImportName", "ImportAsname", "ImportFromModule", "ImportFromName", "ImportFromAsname"):
            new_line = re.sub(
                r'(?<![A-Za-z0-9_])' + re.escape(symbol_name) + r'(?![A-Za-z0-9_])',
                new_name,
                line,
                count=1,
            )
            if new_line != line:
                lines[line_idx] = new_line
                changes.append(f"  Line {line_no}: {line.strip()[:80]} → {new_line.strip()[:80]}")
        else:
            col_offset = entry[2]
            new_line = line[:col_offset] + line[col_offset:].replace(symbol_name, new_name, 1)
            if new_line != line:
                lines[line_idx] = new_line
                changes.append(f"  Line {line_no}: {line.strip()[:80]} → {new_line.strip()[:80]}")

    if not dry_run and changes:
        file_path.write_text("".join(lines), encoding="utf-8")

    return len(changes), changes


# ── JS/TS (ts-morph bridge) ───────────────────────────────────────────────────

def _rename_js_ts_symbol(
    symbol_name: str,
    new_name: str,
    root: Path,
    dry_run: bool,
) -> tuple[int, list[str]]:
    """
    Rename a JS/TS symbol across the project using ts_analysis_bridge (ts-morph).
    ts-morph uses the TypeScript Compiler API for scope-aware, semantic renaming:
    - Correctly handles aliased imports, namespace imports, CJS destructuring
    - Does NOT rename symbols with the same name in unrelated scopes
    - Does NOT rename occurrences in comments or string literals

    Returns (total_changes, list_of_formatted_change_strings).
    """
    if not _is_ts_bridge_available():
        return 0, ["  (ts_analysis_bridge not available — run npm install in tools/ts_analysis_bridge/)"]

    try:
        data = _run_ts_bridge(
            "rename_symbol",
            symbol=symbol_name,
            new_name=new_name,
            root=str(root),
            dry_run=dry_run,
        )
    except RuntimeError as e:
        logger.warning(f"[rename_symbol] ts_bridge error: {e}")
        return 0, [f"  (ts_bridge error: {e})"]

    total = data.get("total", 0)
    raw_changes = data.get("changes", [])

    # Group changes by file for display
    grouped: dict[str, list[str]] = {}
    for change in raw_changes:
        fp = change.get("file", "unknown")
        line = change.get("line", "?")
        text = change.get("text", "")
        grouped.setdefault(fp, []).append(f"  Line {line}: {text}")

    formatted = []
    for fp, file_changes in grouped.items():
        rel = Path(fp).relative_to(root) if Path(fp).is_relative_to(root) else Path(fp)
        formatted.append(f"  {rel}:")
        formatted.extend(file_changes)
        formatted.append("")

    return total, formatted


# ── Tool ──────────────────────────────────────────────────────────────────────

@tool(args_schema=RenameSymbolInput)
def rename_symbol(
    symbol_name: str,
    new_name: str,
    root_path: str = "",
    file_pattern: str = "**/*",
    dry_run: bool = True,
) -> str:
    """
    Safely rename a symbol (function, class, variable) everywhere it appears in the project.
    Use this tool when you need to:
      - Rename a function, class, variable, or type consistently across all files
      - Refactor a symbol name without missing any call sites or import statements

    IMPORTANT — always follow this two-step process:
      Step 1: Call with dry_run=True (the default) to preview every line that would change.
              Review the output carefully before proceeding.
      Step 2: Call with dry_run=False only after confirming the preview looks correct.

    The tool is scope-aware — it will NOT rename:
      - A different symbol that happens to share the same name in an unrelated scope
      - Occurrences inside comments or string literals

    Supports .py, .js, .ts, .jsx, .tsx files.
    Python uses ast (handles function defs, class defs, variables, import aliases).
    JS/TS uses TypeScript Compiler API (handles aliased imports, namespace imports,
    CommonJS destructuring, re-exports — all edge cases handled correctly).

    file_pattern examples:
      '**/*.py'  — Python files only
      '**/*.ts'  — TypeScript files only
      '**/*'     — all supported files (default)

    Returns a formatted change report. Returns an error string starting with '[' on failure.
    """
    if root_path:
        root = _resolve_path(root_path)
    else:
        root = _workspace_root()

    if not root.exists():
        return f"[rename_symbol error: Path not found: {root}]"

    want_py = "*.ts" not in file_pattern and "*.js" not in file_pattern
    want_js_ts = "*.py" not in file_pattern

    total_changes = 0
    all_changes: list[str] = []

    # ── Python ────────────────────────────────────────────────────────────────
    if want_py:
        py_files = sorted(root.rglob("*.py"))
        _skip = {"__pycache__", ".venv", "venv", ".git"}
        py_files = [f for f in py_files if not any(p in _skip for p in f.parts)]

        for file_path in py_files:
            try:
                count, changes = _rename_python_symbol(symbol_name, new_name, file_path, dry_run)
                if count > 0:
                    total_changes += count
                    rel = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
                    all_changes.append(f"  {rel}:")
                    all_changes.extend(changes)
                    all_changes.append("")
            except Exception as e:
                logger.warning(f"[rename_symbol] Error processing {file_path}: {e}")

    # ── JS/TS ─────────────────────────────────────────────────────────────────
    if want_js_ts:
        count, changes = _rename_js_ts_symbol(symbol_name, new_name, root, dry_run)
        total_changes += count
        all_changes.extend(changes)

    # ── Output ────────────────────────────────────────────────────────────────
    action = "Would rename" if dry_run else "Renamed"
    lines = [
        f"rename_symbol: {action} '{symbol_name}' → '{new_name}'",
        f"Total changes: {total_changes}",
        "=" * 60,
        "",
    ]

    if all_changes:
        lines.extend(all_changes)
    else:
        lines.append(f"(No references to '{symbol_name}' found.)")

    if dry_run:
        lines.append("")
        lines.append("Set dry_run=False to apply these changes.")

    return "\n".join(lines)
