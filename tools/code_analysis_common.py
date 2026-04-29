"""
Shared utilities for code analysis tools.
Provides tree-sitter setup for JS/TS parsing and common helper functions.
Also provides _run_ts_bridge() for semantic JS/TS analysis via the ts-morph Node.js bridge.

Compatibility: tree-sitter-language-pack (modern replacement for unmaintained tree-sitter-languages).
Install: pip install tree-sitter-language-pack

Note on JS/TS semantic analysis:
  tree-sitter is a SYNTAX parser (CST). It cannot resolve symbol bindings, scopes, or imports.
  For semantic operations (find_references, get_definition, rename_symbol), use _run_ts_bridge()
  which shells out to tools/ts_analysis_bridge/index.js (ts-morph / TypeScript Compiler API).
  analyze_module_deps still uses tree-sitter because it only reads import path strings.
"""

import json
import logging
import shutil
import os
import subprocess
import threading
from queue import Queue, Empty
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ── TS Bridge availability ────────────────────────────────────────────────────

_BRIDGE_SCRIPT = Path(__file__).parent / "ts_analysis_bridge" / "index.js"
_TS_BRIDGE_AVAILABLE = None  # None = not tested yet


def _is_ts_bridge_available() -> bool:
    """
    Check that both node is on PATH and ts_analysis_bridge/index.js exists
    with its node_modules installed (ts-morph present).
    """
    global _TS_BRIDGE_AVAILABLE
    if _TS_BRIDGE_AVAILABLE is not None:
        return _TS_BRIDGE_AVAILABLE

    if not _BRIDGE_SCRIPT.exists():
        logger.warning("[code_analysis_common] ts_analysis_bridge/index.js not found")
        _TS_BRIDGE_AVAILABLE = False
        return False

    if shutil.which("node") is None:
        logger.warning("[code_analysis_common] node not found on PATH")
        _TS_BRIDGE_AVAILABLE = False
        return False

    # Quick smoke-test: require ts-morph
    node_modules = _BRIDGE_SCRIPT.parent / "node_modules" / "ts-morph"
    if not node_modules.exists():
        logger.warning(
            "[code_analysis_common] ts-morph not installed. "
            "Run: cd tools/ts_analysis_bridge && npm install"
        )
        _TS_BRIDGE_AVAILABLE = False
        return False

    _TS_BRIDGE_AVAILABLE = True
    return True


def _run_ts_bridge(command: str, timeout: int = 30, **kwargs) -> dict[str, Any]:
    """
    Call the ts_analysis_bridge Node.js CLI and return its parsed JSON output.

    Args:
        command:  One of 'find_references', 'get_definition', 'rename_symbol'
        timeout:  Seconds before subprocess is killed (default 30)
        **kwargs: CLI flags — keys become --key, True values become bare flags.
                  Use underscore for hyphens: new_name -> --new-name

    Returns:
        Parsed JSON dict from the bridge.

    Raises:
        RuntimeError: if bridge not available, subprocess fails, or JSON parse fails.
    """
    if not _is_ts_bridge_available():
        raise RuntimeError(
            "ts_analysis_bridge not available. "
            "Ensure Node.js is installed and run 'npm install' in tools/ts_analysis_bridge/."
        )

    cmd = ["node", str(_BRIDGE_SCRIPT), command]
    for key, val in kwargs.items():
        cli_key = key.replace("_", "-")
        if val is True:
            cmd.append(f"--{cli_key}")
        elif val is not False and val is not None:
            cmd.extend([f"--{cli_key}", str(val)])

    logger.debug(f"[ts_bridge] Running: {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ},
        )

        stdout_queue = Queue()
        stderr_queue = Queue()

        def _drain_pipe(pipe, queue):
            for line in iter(pipe.readline, b''):
                queue.put(line)
            pipe.close()

        threading.Thread(target=_drain_pipe, args=(proc.stdout, stdout_queue), daemon=True).start()
        threading.Thread(target=_drain_pipe, args=(proc.stderr, stderr_queue), daemon=True).start()

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise RuntimeError(f"ts_analysis_bridge timed out after {timeout}s for command '{command}'")

        stdout_chunks = []
        stderr_chunks = []

        while True:
            try:
                stdout_chunks.append(stdout_queue.get_nowait())
            except Empty:
                break

        while True:
            try:
                stderr_chunks.append(stderr_queue.get_nowait())
            except Empty:
                break

        stdout_bytes = b''.join(stdout_chunks)
        stderr_bytes = b''.join(stderr_chunks)

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if proc.returncode != 0 and not stdout.strip():
            raise RuntimeError(
                f"ts_analysis_bridge exited {proc.returncode}: {stderr[:300]}"
            )

        try:
            data = json.loads(stdout.strip())
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"ts_analysis_bridge returned non-JSON output: {stdout[:200]} | err: {e}"
            )

        if "error" in data:
            raise RuntimeError(f"ts_analysis_bridge error: {data['error']}")

        return data

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ts_analysis_bridge timed out after {timeout}s for command '{command}'")
    except Exception as e:
        raise RuntimeError(f"ts_analysis_bridge subprocess error: {e}")


# ── Tree-sitter availability check ───────────────────────────────────────────

_TREE_SITTER_AVAILABLE = None  # None = not tested yet


def _is_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack is installed."""
    global _TREE_SITTER_AVAILABLE
    if _TREE_SITTER_AVAILABLE is not None:
        return _TREE_SITTER_AVAILABLE
    try:
        from tree_sitter_language_pack import get_parser  # noqa: F401
        _TREE_SITTER_AVAILABLE = True
    except ImportError:
        _TREE_SITTER_AVAILABLE = False
    return _TREE_SITTER_AVAILABLE


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_js_ts_file(file_path: Path, lang: Literal["js", "ts"] = "js"):
    """
    Parse a JS or TS file using tree-sitter-language-pack.
    Returns the tree-sitter Tree, or None on failure.

    get_parser(name) returns a ready-to-use Parser — no Language setup needed.
    NOTE: Used only by analyze_module_deps (import path extraction).
          Semantic tools (find_references, get_definition, rename_symbol) use _run_ts_bridge().
    """
    lang_name = "typescript" if lang == "ts" else "javascript"
    try:
        from tree_sitter_language_pack import get_parser
        parser = get_parser(lang_name)
        source = file_path.read_bytes()
        return parser.parse(source)
    except ImportError:
        logger.error(
            "[code_analysis_common] tree-sitter-language-pack not installed. "
            "Run: pip install tree-sitter-language-pack"
        )
        return None
    except Exception as e:
        logger.warning(f"[code_analysis_common] Failed to parse {file_path}: {e}")
        return None


# ── String extraction helpers ─────────────────────────────────────────────────

def _node_text(node, source_bytes: bytes) -> str:
    """Extract the raw text for a node from source bytes."""
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _string_value(node, source_bytes: bytes) -> str | None:
    """
    Extract the inner value of a string/template_string node, stripping quotes.
    tree-sitter JS/TS grammar wraps content in a 'string_fragment' child node.
    Falls back to stripping enclosing quote characters from raw text.
    """
    if node.type not in ("string", "template_string"):
        return None
    for child in node.children:
        if child.type in ("string_fragment", "template_chars"):
            return _node_text(child, source_bytes)
    # Fallback: strip enclosing quotes from raw text
    return _node_text(node, source_bytes).strip("'\"` ")


# ── Public analysis functions ─────────────────────────────────────────────────

def get_js_ts_imports(file_path: Path, lang: Literal["js", "ts"] = "js") -> list[str]:
    """
    Extract import and require statements from a JS/TS file using tree-sitter.
    Returns a list of imported module specifiers (the string in 'from "..."' or require('...')).

    NOTE: tree-sitter is appropriate here because we only read string literals,
          not symbol identities. No semantic analysis required.
    """
    tree = parse_js_ts_file(file_path, lang)
    if tree is None:
        return []

    source_bytes = file_path.read_bytes()
    imports = []

    def _walk(node):
        # import ... from '...'  or  import '...'
        if node.type == "import_statement":
            for child in node.children:
                if child.type in ("string", "template_string"):
                    val = _string_value(child, source_bytes)
                    if val:
                        imports.append(val)
                    break

        # require('...') or require("...")
        elif node.type == "call_expression":
            func_name = None
            args_node = None
            for child in node.children:
                if child.type == "identifier":
                    func_name = _node_text(child, source_bytes)
                elif child.type == "arguments":
                    args_node = child
            if func_name == "require" and args_node:
                for arg in args_node.children:
                    if arg.type in ("string", "template_string"):
                        val = _string_value(arg, source_bytes)
                        if val:
                            imports.append(val)
                        break

        # export { ... } from '...'
        elif node.type == "export_statement":
            for child in node.children:
                if child.type in ("string", "template_string"):
                    val = _string_value(child, source_bytes)
                    if val:
                        imports.append(val)
                    break

        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return imports


def get_js_ts_symbols(file_path: Path, lang: Literal["js", "ts"] = "js") -> list[tuple[str, str]]:
    """
    Extract symbols (functions, classes, variables) from a JS/TS file.
    Returns a list of (symbol_type, symbol_name) tuples.
    Types: 'function', 'class', 'const', 'let', 'var', 'variable'
    """
    tree = parse_js_ts_file(file_path, lang)
    if tree is None:
        return []

    source_bytes = file_path.read_bytes()
    symbols = []

    def _walk(node):
        if node.type == "function_declaration":
            for child in node.children:
                if child.type == "identifier":
                    symbols.append(("function", _node_text(child, source_bytes)))
                    break

        elif node.type == "class_declaration":
            for child in node.children:
                # JS uses 'identifier', TS uses 'type_identifier'
                if child.type in ("identifier", "type_identifier"):
                    symbols.append(("class", _node_text(child, source_bytes)))
                    break

        elif node.type == "interface_declaration":
            for child in node.children:
                if child.type == "type_identifier":
                    symbols.append(("interface", _node_text(child, source_bytes)))
                    break

        elif node.type == "type_alias_declaration":
            for child in node.children:
                if child.type == "type_identifier":
                    symbols.append(("type", _node_text(child, source_bytes)))
                    break

        elif node.type == "variable_declarator":
            for child in node.children:
                if child.type == "identifier":
                    var_name = _node_text(child, source_bytes)
                    parent_type = node.parent.type if node.parent else ""
                    kind = "const" if "const" in parent_type else \
                           "let" if "let" in parent_type else \
                           "var" if "var" in parent_type else "variable"
                    symbols.append((kind, var_name))
                    break

        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return symbols
