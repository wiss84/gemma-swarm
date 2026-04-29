"""
Gemma Swarm — Coding Agent: Layer 4 Universal Validation Tools
===============================================================
Language-aware validation. The agent calls ONE tool — validate_files — and
Magika detects what language each file actually is, then routes to the correct
validator. No more Python-only assumptions.

Supported languages:
    Python      → import check + ruff/flake8 + pytest/unittest + mypy
    TypeScript  → tsc type check + eslint + npm test
    TSX         → same as TypeScript (TS with JSX, treated identically)
    JavaScript  → eslint + npm test
    JSX         → same as JavaScript

Detection strategy (priority order):
    1. Magika AI content detection (reads actual file bytes, ~99% accuracy)
    2. Extension fallback (if Magika is unavailable or returns low confidence)

For the test runner in JS/TS projects, the tool reads package.json "scripts.test"
and runs `npm test`. This respects whatever test runner the project uses
(jest, vitest, mocha, etc.) without hardcoding assumptions.

Environment note:
    Python validation runs in gemma_test via get_gemma_test_python_exe().
    JS/TS validation runs node/npm/tsc/eslint from system PATH directly —
    these are system-level tools installed globally, not conda-scoped.
"""

import json
import logging
import os
import subprocess
import threading
from queue import Queue, Empty
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from tools.coding_tools import _workspace_root
from tools.validation_tools import (
    _resolve_path,
    _run_command,
    _truncate,
    _tool_available,
    _extract_imports,
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    MAX_OUTPUT_CHARS,
)
from agents_utils.get_test_env import get_gemma_test_python_exe

logger = logging.getLogger(__name__)


# ── Magika language detection ──────────────────────────────────────────────────

# Magika labels that map to each language family.
# Uses StrEnum values — safe to compare as strings.
_PYTHON_LABELS = {"python"}
_TS_LABELS     = {"typescript", "tsx"}
_JS_LABELS     = {"javascript", "jsx"}

# Extension fallback map (used if Magika is unavailable)
_EXT_FALLBACK = {
    ".py":  "python",
    ".ts":  "typescript",
    ".tsx": "tsx",
    ".js":  "javascript",
    ".jsx": "jsx",
}

# Singleton Magika instance — loaded once, reused across all calls.
# None if magika is not installed (graceful degradation to extension fallback).
_magika_instance = None
_magika_checked  = False   # avoid repeated import attempts


def _get_magika():
    """Return the Magika singleton, or None if not installed."""
    global _magika_instance, _magika_checked
    if _magika_checked:
        return _magika_instance
    _magika_checked = True
    try:
        from magika import Magika, PredictionMode
        _magika_instance = Magika(prediction_mode=PredictionMode.BEST_GUESS)
        logger.info("[universal] Magika loaded successfully.")
    except ImportError:
        logger.warning(
            "[universal] magika not installed — falling back to extension-based detection. "
            "Install with: pip install magika"
        )
        _magika_instance = None
    return _magika_instance


def _detect_language(file_path: Path) -> str:
    """
    Detect the programming language of a file.

    Returns one of: 'python', 'typescript', 'tsx', 'javascript', 'jsx', 'unknown'.

    Priority:
        1. Magika AI detection (reads actual file content)
        2. Extension fallback (if Magika is unavailable or returns unrecognised label)
    """
    magika = _get_magika()
    if magika is not None:
        try:
            result = magika.identify_path(file_path)
            if result.ok:
                label = str(result.output.label)
                logger.info(
                    f"[universal] Magika: {file_path.name} -> '{label}' "
                    f"(score={result.score:.2f})"
                )
                if label in _PYTHON_LABELS:
                    return "python"
                if label in _TS_LABELS:
                    return label   # 'typescript' or 'tsx'
                if label in _JS_LABELS:
                    return label   # 'javascript' or 'jsx'
                # Magika detected something else (json, markdown, etc.) —
                # fall through to extension fallback for code files
        except Exception as e:
            logger.warning(f"[universal] Magika detection failed for {file_path.name}: {e}")

    # Extension fallback
    ext  = file_path.suffix.lower()
    lang = _EXT_FALLBACK.get(ext, "unknown")
    logger.info(f"[universal] Extension fallback: {file_path.name} ({ext}) -> '{lang}'")
    return lang


# ── System-level command runner (for node / npm / tsc / eslint) ───────────────

def _run_system_command(
    cmd: list[str],
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, str, str]:
    """
    Run a system-level command (node, npm, tsc, eslint) directly from PATH.
    Unlike _run_command in validation_tools.py, does NOT redirect to gemma_test —
    these tools live at the system / global npm level.
    shell=True on Windows so npm.cmd / tsc.cmd / npx.cmd are found from PATH.
    Decodes output as UTF-8 with replacement to avoid Windows cp1252 errors.
    Safe from deadlocks — uses threaded output draining.
    """
    import platform
    use_shell = platform.system() == "Windows"
    try:
        command = subprocess.list2cmdline(cmd) if use_shell else cmd
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            shell=use_shell,
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
            return -1, "", f"Command timed out after {timeout}s"

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

        return proc.returncode, stdout.strip(), stderr.strip()

    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError as e:
        return -1, "", f"Command not found: {e}"
    except Exception as e:
        return -1, "", str(e)


# ── Project structure helpers ──────────────────────────────────────────────────

def _find_package_json(start: Path) -> Path | None:
    """Walk up from start looking for package.json. Returns its directory or None."""
    current = start if start.is_dir() else start.parent
    for _ in range(6):   # max 6 levels up
        if (current / "package.json").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _find_tsconfig(start: Path) -> Path | None:
    """Walk up from start looking for tsconfig.json. Returns its directory or None."""
    current = start if start.is_dir() else start.parent
    for _ in range(6):
        if (current / "tsconfig.json").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _node_modules_exist(project_root: Path) -> bool:
    return (project_root / "node_modules").is_dir()


# ── Python validator ───────────────────────────────────────────────────────────

def _validate_python(
    file_paths: list[Path],
    test_path: str,
    cwd: Path,
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """
    Full Python validation pipeline.
    Returns (report_lines, import_failures, lint_failures, type_errors, test_failures).
    """
    from tools.validation_tools import (
        _extract_imports,
        _run_command as _py_run,
        _truncate    as _py_trunc,
        _tool_available as _py_tool,
    )

    py_exe          = get_gemma_test_python_exe()
    report_lines    = []
    import_failures = []
    lint_failures   = []
    type_errors     = []
    test_failures   = []

    # Phase 1 — import check
    report_lines.append("=== Phase 1: Import Check ===")
    for resolved in file_paths:
        modules = _extract_imports(resolved)
        if not modules:
            report_lines.append(f"  - {resolved.name}: no imports")
            continue
        file_ok = True
        for module in modules:
            safe_cwd = str(cwd).replace("\\", "/")
            inject   = f"import sys; sys.path.insert(0, r'{safe_cwd}'); "
            rc, _, stderr = _py_run(
                ["python", "-c", inject + f"import {module}"], cwd, timeout=10
            )
            if rc != 0:
                err = next(
                    (l for l in reversed(stderr.splitlines()) if l.strip()),
                    "unknown error"
                )
                report_lines.append(f"  x {resolved.name}: import {module} -> {err}")
                import_failures.append(str(resolved))
                file_ok = False
                break
        if file_ok:
            report_lines.append(f"  ok {resolved.name}: all {len(modules)} import(s) OK")

    # Phase 2 — linter
    report_lines.append("\n=== Phase 2: Linter (ruff / flake8) ===")
    for resolved in file_paths:
        if _py_tool("ruff"):
            cmd, linter = ["ruff", "check", str(resolved), "--output-format=concise"], "ruff"
        elif _py_tool("flake8"):
            cmd, linter = ["flake8", str(resolved), "--max-line-length=120"], "flake8"
        else:
            report_lines.append(f"  - {resolved.name}: no linter installed (ruff/flake8)")
            continue
        rc, stdout, stderr = _py_run(cmd, cwd)
        if rc == 0:
            report_lines.append(f"  ok {resolved.name}: no issues ({linter})")
        else:
            out = _py_trunc(stdout or stderr, 2000)
            report_lines.append(f"  x {resolved.name}: lint issues ({linter})\n{out}")
            lint_failures.append(str(resolved))

    # Phase 3 — tests (only if phases 1+2 passed)
    if test_path:
        report_lines.append("\n=== Phase 3: Tests (pytest / unittest) ===")
        if import_failures or lint_failures:
            report_lines.append(
                f"  skip: fix {len(import_failures)} import / "
                f"{len(lint_failures)} lint failure(s) first"
            )
        else:
            resolved_test = _resolve_path(test_path)
            if not resolved_test.exists():
                report_lines.append(f"  x test path not found: {resolved_test}")
                test_failures.append(test_path)
            else:
                rc_chk, _, _ = _py_run([py_exe, "-m", "pytest", "--version"], cwd, timeout=5)
                if rc_chk == 0:
                    test_cmd = ["pytest", str(resolved_test), "-v", "--tb=short", "--no-header"]
                    runner   = "pytest"
                else:
                    test_cmd = ["python", "-m", "unittest", str(resolved_test), "-v"]
                    runner   = "unittest"
                rc_t, out_t, err_t = _py_run(test_cmd, cwd, timeout=DEFAULT_TIMEOUT)
                raw    = _py_trunc((out_t + "\n\n" + err_t).strip())
                status = "PASSED" if rc_t == 0 else "FAILED"
                report_lines.append(f"  {status} ({runner}, exit {rc_t})\n{raw}")
                if rc_t != 0:
                    test_failures.append(test_path)

    # Phase 4 — mypy type check (only if phases 1+2 passed)
    if not import_failures and not lint_failures:
        report_lines.append("\n=== Phase 4: Type Check (mypy) ===")
        for resolved in file_paths:
            if not _py_tool("mypy"):
                report_lines.append(f"  - {resolved.name}: mypy not installed")
                continue
            cmd = ["mypy", str(resolved), "--ignore-missing-imports", "--no-error-summary"]
            rc, stdout, stderr = _py_run(cmd, cwd)
            output = stdout or stderr
            if rc == 0:
                report_lines.append(f"  ok {resolved.name}: no type errors (mypy)")
            else:
                error_count = len([l for l in output.splitlines() if ": error:" in l])
                out = _py_trunc(output, 2000)
                report_lines.append(
                    f"  x {resolved.name}: {error_count} type error(s) (mypy)\n{out}"
                )
                type_errors.append(str(resolved))

    return report_lines, import_failures, lint_failures, type_errors, test_failures


# ── TypeScript / TSX validator ─────────────────────────────────────────────────

def _validate_typescript(
    file_paths: list[Path],
    test_path: str,
    cwd: Path,
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """
    TypeScript / TSX validation pipeline.
    Phase 1: tsc type check (project-wide via tsconfig if found, per-file otherwise)
    Phase 2: eslint (via npx, skips gracefully if no config found)
    Phase 3: npm test (reads package.json scripts.test)
    Returns (report_lines, [], lint_failures, type_errors, test_failures).
    """
    report_lines  = []
    type_errors   = []
    lint_failures = []
    test_failures = []

    project_root = _find_tsconfig(cwd) or _find_package_json(cwd) or cwd

    # Phase 1 — tsc type check
    report_lines.append("=== Phase 1: Type Check (tsc) ===")
    tsconfig_dir = _find_tsconfig(cwd)
    if tsconfig_dir:
        rc, stdout, stderr = _run_system_command(
            ["npx", "tsc", "--noEmit"], tsconfig_dir
        )
        output = _truncate((stdout + "\n" + stderr).strip(), 4000)
        if rc == 0:
            report_lines.append("  ok: no type errors (project-wide via tsconfig.json)")
        else:
            error_count = len([l for l in output.splitlines() if "error TS" in l])
            report_lines.append(f"  x: {error_count} type error(s)\n{output}")
            for fp in file_paths:
                type_errors.append(str(fp))
    else:
        # No tsconfig — check each file individually
        for resolved in file_paths:
            rc, stdout, stderr = _run_system_command(
                ["npx", "tsc", str(resolved), "--noEmit", "--allowJs",
                 "--target", "ESNext", "--moduleResolution", "node"],
                cwd,
            )
            output = _truncate((stdout + "\n" + stderr).strip(), 2000)
            if rc == 0:
                report_lines.append(f"  ok {resolved.name}: no type errors (tsc)")
            else:
                error_count = len([l for l in output.splitlines() if "error TS" in l])
                report_lines.append(
                    f"  x {resolved.name}: {error_count} type error(s)\n{output}"
                )
                type_errors.append(str(resolved))

    # Phase 2 — eslint
    report_lines.append("\n=== Phase 2: Linter (eslint) ===")
    if not _node_modules_exist(project_root):
        report_lines.append(
            "  warn: node_modules not found — run `npm install` first. Skipping eslint."
        )
    else:
        for resolved in file_paths:
            rc, stdout, stderr = _run_system_command(
                ["npx", "eslint", str(resolved), "--format=compact"],
                project_root,
            )
            output = _truncate((stdout or stderr).strip(), 2000)
            if rc == 0:
                report_lines.append(f"  ok {resolved.name}: no eslint issues")
            elif "Could not find" in output or "No ESLint configuration" in output:
                report_lines.append(
                    f"  - {resolved.name}: no eslint config found — skipped"
                )
            else:
                issue_count = len([l for l in output.splitlines() if l.strip()])
                report_lines.append(
                    f"  x {resolved.name}: {issue_count} eslint issue(s)\n{output}"
                )
                lint_failures.append(str(resolved))

    # Phase 3 — npm test
    if test_path:
        report_lines.append("\n=== Phase 3: Tests (npm test) ===")
        if type_errors or lint_failures:
            report_lines.append(
                f"  skip: fix {len(type_errors)} type / "
                f"{len(lint_failures)} lint failure(s) first"
            )
        elif not _node_modules_exist(project_root):
            report_lines.append(
                "  warn: node_modules not found — run `npm install` first. Skipping tests."
            )
        else:
            pkg_json_path   = project_root / "package.json"
            has_test_script = False
            try:
                pkg = json.loads(pkg_json_path.read_text(encoding="utf-8"))
                has_test_script = bool(pkg.get("scripts", {}).get("test"))
            except Exception:
                pass

            if not has_test_script:
                report_lines.append(
                    "  warn: no 'test' script in package.json — "
                    "add one (e.g. 'jest' or 'vitest') to enable test running."
                )
            else:
                rc, stdout, stderr = _run_system_command(
                    ["npm", "test", "--", "--passWithNoTests"],
                    project_root,
                    timeout=DEFAULT_TIMEOUT,
                )
                raw    = _truncate((stdout + "\n\n" + stderr).strip())
                status = "PASSED" if rc == 0 else "FAILED"
                report_lines.append(f"  {status} (npm test, exit {rc})\n{raw}")
                if rc != 0:
                    test_failures.append(test_path)

    return report_lines, [], lint_failures, type_errors, test_failures


# ── JavaScript / JSX validator ─────────────────────────────────────────────────

def _validate_javascript(
    file_paths: list[Path],
    test_path: str,
    cwd: Path,
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """
    JavaScript / JSX validation pipeline.
    Phase 1: eslint
    Phase 2: npm test
    No tsc — JS projects don't have TS type checking unless explicitly configured.
    Returns (report_lines, [], lint_failures, [], test_failures).
    """
    report_lines  = []
    lint_failures = []
    test_failures = []

    project_root = _find_package_json(cwd) or cwd

    # Phase 1 — eslint
    report_lines.append("=== Phase 1: Linter (eslint) ===")
    if not _node_modules_exist(project_root):
        report_lines.append(
            "  warn: node_modules not found — run `npm install` first. Skipping eslint."
        )
    else:
        for resolved in file_paths:
            rc, stdout, stderr = _run_system_command(
                ["npx", "eslint", str(resolved), "--format=compact"],
                project_root,
            )
            output = _truncate((stdout or stderr).strip(), 2000)
            if rc == 0:
                report_lines.append(f"  ok {resolved.name}: no eslint issues")
            elif "Could not find" in output or "No ESLint configuration" in output:
                report_lines.append(
                    f"  - {resolved.name}: no eslint config found — skipped"
                )
            else:
                issue_count = len([l for l in output.splitlines() if l.strip()])
                report_lines.append(
                    f"  x {resolved.name}: {issue_count} eslint issue(s)\n{output}"
                )
                lint_failures.append(str(resolved))

    # Phase 2 — npm test
    if test_path:
        report_lines.append("\n=== Phase 2: Tests (npm test) ===")
        if lint_failures:
            report_lines.append(
                f"  skip: fix {len(lint_failures)} lint failure(s) first"
            )
        elif not _node_modules_exist(project_root):
            report_lines.append(
                "  warn: node_modules not found — run `npm install` first. Skipping."
            )
        else:
            pkg_json_path   = project_root / "package.json"
            has_test_script = False
            try:
                pkg = json.loads(pkg_json_path.read_text(encoding="utf-8"))
                has_test_script = bool(pkg.get("scripts", {}).get("test"))
            except Exception:
                pass

            if not has_test_script:
                report_lines.append("  warn: no 'test' script in package.json. Skipping.")
            else:
                rc, stdout, stderr = _run_system_command(
                    ["npm", "test", "--", "--passWithNoTests"],
                    project_root,
                    timeout=DEFAULT_TIMEOUT,
                )
                raw    = _truncate((stdout + "\n\n" + stderr).strip())
                status = "PASSED" if rc == 0 else "FAILED"
                report_lines.append(f"  {status} (npm test, exit {rc})\n{raw}")
                if rc != 0:
                    test_failures.append(test_path)

    return report_lines, [], lint_failures, [], test_failures


# ── Universal validate_files tool ──────────────────────────────────────────────

class ValidateFilesInput(BaseModel):
    file_paths: list[str] = Field(
        description=(
            "List of source file paths to validate (absolute or project-relative). "
            "Can be any mix of Python (.py), TypeScript (.ts/.tsx), or JavaScript (.js/.jsx). "
            "Up to 10 files. Pass all files you just wrote or edited in one call."
        )
    )
    test_path: str = Field(
        default="",
        description=(
            "Optional: path to run tests from after validation passes. "
            "For Python: path to a test file or directory (pytest). "
            "For JS/TS: path to the project root where npm test will run. "
            "Leave empty to skip test execution."
        )
    )
    working_dir: str = Field(
        default="",
        description="Working directory. Defaults to the current workspace root."
    )


@tool(args_schema=ValidateFilesInput)
def validate_files(
    file_paths: list[str],
    test_path: str = "",
    working_dir: str = "",
) -> str:
    """
    Validate source files after writing or editing them. Automatically detects
    the language of each file using Google Magika AI and routes to the correct
    validation pipeline:

        Python (.py)            -> import check + ruff/flake8 lint + pytest + mypy
        TypeScript (.ts/.tsx)   -> tsc type check + eslint + npm test
        JavaScript (.js/.jsx)   -> eslint + npm test

    Mixed-language projects are handled in one call: files are grouped by language
    and each group runs through its own pipeline, merged into a single report.

    Always call this after write_files or edit_files — never leave code unvalidated.
    Up to 10 files per call.

    NOTE for JS/TS projects: ensure `npm install` has been run in the project
    directory (via execute_shell) before calling this tool, so that eslint and
    npm test can find their dependencies in node_modules/.

    Returns a structured report with per-language sections and an overall pass/fail.
    Returns an error string starting with '[' only on catastrophic failure.
    """
    if not file_paths:
        return "[validate_files error: file_paths list is empty]"
    if len(file_paths) > 10:
        return "[validate_files error: too many files — max 10 per call]"

    cwd = _resolve_path(working_dir) if working_dir else _workspace_root()

    # ── Step 1: Resolve and detect language per file ──────────────────────────
    py_files = []
    ts_files = []
    js_files = []
    unknown  = []

    detection_lines = ["=== Language Detection (Magika) ==="]
    for fp_str in file_paths:
        resolved = _resolve_path(fp_str)
        if not resolved.exists():
            detection_lines.append(f"  x {fp_str}: file not found")
            unknown.append(fp_str)
            continue

        lang = _detect_language(resolved)
        if lang == "python":
            py_files.append(resolved)
            detection_lines.append(f"  py {resolved.name}")
        elif lang in _TS_LABELS:
            ts_files.append(resolved)
            suffix = " (TSX)" if lang == "tsx" else ""
            detection_lines.append(f"  ts {resolved.name}{suffix}")
        elif lang in _JS_LABELS:
            js_files.append(resolved)
            suffix = " (JSX)" if lang == "jsx" else ""
            detection_lines.append(f"  js {resolved.name}{suffix}")
        else:
            unknown.append(fp_str)
            detection_lines.append(
                f"  -- {resolved.name}: '{lang}' not a supported language — skipped"
            )

    all_report   = detection_lines[:]
    all_import_f = []
    all_lint_f   = []
    all_type_e   = []
    all_test_f   = []

    # ── Step 2: Per-language pipelines ────────────────────────────────────────
    if py_files:
        all_report.append("\n" + "=" * 60)
        all_report.append("PYTHON VALIDATION")
        all_report.append("=" * 60)
        lines, imp_f, lint_f, type_e, test_f = _validate_python(py_files, test_path, cwd)
        all_report.extend(lines)
        all_import_f.extend(imp_f)
        all_lint_f.extend(lint_f)
        all_type_e.extend(type_e)
        all_test_f.extend(test_f)

    if ts_files:
        all_report.append("\n" + "=" * 60)
        all_report.append("TYPESCRIPT VALIDATION")
        all_report.append("=" * 60)
        lines, _, lint_f, type_e, test_f = _validate_typescript(ts_files, test_path, cwd)
        all_report.extend(lines)
        all_lint_f.extend(lint_f)
        all_type_e.extend(type_e)
        all_test_f.extend(test_f)

    if js_files:
        all_report.append("\n" + "=" * 60)
        all_report.append("JAVASCRIPT VALIDATION")
        all_report.append("=" * 60)
        lines, _, lint_f, _, test_f = _validate_javascript(js_files, test_path, cwd)
        all_report.extend(lines)
        all_lint_f.extend(lint_f)
        all_test_f.extend(test_f)

    # ── Step 3: Overall summary ───────────────────────────────────────────────
    total_issues = (
        len(all_import_f) + len(all_lint_f) +
        len(all_type_e)   + len(all_test_f) +
        len(unknown)
    )
    overall = "ALL CHECKS PASSED" if total_issues == 0 else f"{total_issues} ISSUE(S) FOUND"

    header = (
        f"validate_files: {overall}\n"
        f"Files: {len(file_paths)}  |  "
        f"Import failures: {len(all_import_f)}  |  "
        f"Lint failures: {len(all_lint_f)}  |  "
        f"Type errors: {len(all_type_e)}  |  "
        f"Test failures: {len(all_test_f)}  |  "
        f"Skipped: {len(unknown)}\n"
        + "-" * 60 + "\n\n"
    )
    return header + "\n".join(all_report)
