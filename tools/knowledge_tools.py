"""
Gemma Swarm — Coding Agent: Layer 2 Knowledge Tools
=====================================================
Anti-hallucination tools. These exist because LLMs will confidently write
code using APIs that changed 6 months ago. Before writing any code that
uses a package, the coding agent MUST call these tools first.

Tools:
    get_installed_package_info(package_name, root_path) — pip show (Python) or npm/node_modules (JS/TS)
    get_package_latest_version(package_name)          — PyPI JSON API (no key needed)
    fetch_package_docs(package_name, version)         — readthedocs / PyPI homepage

NOT duplicated here (reused from web_search_tool.py):
    search_web      — web search via DuckDuckGo
    fetch_page      — fetch any URL and return clean text
    fetch_next_chunk — read next chunk of a large page

Usage pattern the agent should follow:
    1. get_installed_package_info("httpx")   → what version is installed right now?
    2. get_package_latest_version("httpx")   → is there a newer version? what changed?
    3. fetch_package_docs("httpx", "0.27")   → read the actual current docs
    4. THEN write the code — never before
"""

import os
import sys
import json
import logging
import subprocess
import threading
import platform
from queue import Queue, Empty
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from agents_utils.config import PROJECT_ROOT
from agents_utils.web_fetcher import fetch_page_free
from agents_utils.get_test_env import get_gemma_test_python_exe
from tools.coding_tools import _workspace_root

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

PYPI_API_URL    = "https://pypi.org/pypi/{package}/json"
PYPI_PAGE_URL   = "https://pypi.org/project/{package}/"
RTFD_URL        = "https://{package}.readthedocs.io/en/stable/"
RTFD_LATEST_URL = "https://{package}.readthedocs.io/en/latest/"

# Known readthedocs slug overrides for packages whose slug ≠ package name
RTFD_SLUG_OVERRIDES = {
    "langchain-google-genai":    "langchain-google-genai",
    "langchain-core":            "api.python.langchain.com",   # uses api subdomain
    "langchain":                 "python.langchain.com",
    "langchain-community":       "python.langchain.com",
    "httpx":                     "www.python-httpx.org",
    "pydantic":                  "docs.pydantic.dev",
    "langgraph":                 "langchain-ai.github.io/langgraph",
    "fastapi":                   "fastapi.tiangolo.com",
    "pytest":                    "docs.pytest.org/en/stable",
}

# Packages that use GitHub Pages / custom domains rather than readthedocs
CUSTOM_DOC_URLS = {
    "httpx":           "https://www.python-httpx.org/",
    "pydantic":        "https://docs.pydantic.dev/latest/",
    "langchain":       "https://python.langchain.com/docs/introduction/",
    "langgraph":       "https://langchain-ai.github.io/langgraph/",
    "fastapi":         "https://fastapi.tiangolo.com/",
    "pytest":          "https://docs.pytest.org/en/stable/",
    "requests":        "https://requests.readthedocs.io/en/latest/",
    "flask":           "https://flask.palletsprojects.com/",
    "django":          "https://docs.djangoproject.com/",
    "numpy":           "https://numpy.org/doc/stable/",
    "pandas":          "https://pandas.pydata.org/docs/",
    "sqlalchemy":      "https://docs.sqlalchemy.org/en/20/",
}


PROD_ENV_NAME = "gemma_swarm"
TEST_ENV_NAME = "gemma_test"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _run_pip_show(package_name: str) -> tuple[int, str, str]:
    """Run `python -m pip show <package>` in the gemma_test environment and return (returncode, stdout, stderr). Safe from deadlocks."""
    py_exe = get_gemma_test_python_exe()
    try:
        cmd = [py_exe, "-m", "pip", "show", package_name]
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
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
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

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
        return -1, "", "pip show timed out"
    except Exception as e:
        return -1, "", str(e)


def _fetch_pypi_json(package_name: str) -> dict | None:
    """Fetch PyPI JSON API for a package. Returns parsed dict or None."""
    import httpx
    url = PYPI_API_URL.format(package=package_name)
    try:
        response = httpx.get(url, timeout=10, follow_redirects=True)
        if response.status_code == 200:
            return response.json()
        logger.warning(f"[knowledge] PyPI API returned {response.status_code} for {package_name}")
        return None
    except Exception as e:
        logger.warning(f"[knowledge] PyPI API error for {package_name}: {e}")
        return None


def _fetch_npm_registry(package_name: str) -> dict | None:
    """Fetch NPM registry data for a package. Returns parsed dict or None."""
    import httpx
    url = f"https://registry.npmjs.org/{package_name}/latest"
    try:
        response = httpx.get(url, timeout=10, follow_redirects=True)
        if response.status_code == 200:
            return response.json()
        logger.warning(f"[knowledge] NPM registry returned {response.status_code} for {package_name}")
        return None
    except Exception as e:
        logger.warning(f"[knowledge] NPM registry error for {package_name}: {e}")
        return None


def _get_pypi_latest_version(package_name: str) -> str:
    """Query PyPI and return formatted result."""
    data = _fetch_pypi_json(package_name)
    if data is None:
        return (
            f"[get_package_latest_version: Could not fetch PyPI data for '{package_name}'. "
            f"The package may not exist on PyPI, or PyPI may be unreachable.]"
        )
    return _format_pypi_result(data, package_name)


def _get_npm_latest_version(package_name: str) -> str:
    """Query NPM registry and return formatted result."""
    data = _fetch_npm_registry(package_name)
    if data is None:
        return (
            f"[get_package_latest_version: Could not fetch NPM data for '{package_name}'. "
            f"The package may not exist on NPM, or NPM may be unreachable.]"
        )
    return _format_npm_result(data, package_name)


def _format_pypi_result(data: dict, package_name: str) -> str:
    """Format PyPI data into a clean summary."""
    info    = data.get("info", {})
    name    = info.get("name", package_name)
    latest  = info.get("version", "unknown")
    summary = info.get("summary", "")
    homepage = info.get("home_page", "") or info.get("project_url", "")
    pypi_url = f"https://pypi.org/project/{name}/"

    # Get recent release history (last 5 versions)
    releases     = data.get("releases", {})
    version_list = sorted(
        releases.keys(),
        key=lambda v: releases[v][0]["upload_time"] if releases[v] else "0",
        reverse=True,
    )
    recent_versions = version_list[:5]

    # Get upload date of latest version
    latest_files = releases.get(latest, [])
    upload_date  = latest_files[0].get("upload_time", "unknown")[:10] if latest_files else "unknown"

    lines = [
        f"PyPI package info for '{name}':",
        f"  Latest version: {latest}",
        f"  Released:       {upload_date}",
        f"  Summary:        {summary}",
        f"  PyPI URL:       {pypi_url}",
    ]
    if homepage:
        lines.append(f"  Homepage:       {homepage}")

    if recent_versions:
        lines.append(f"\n  Recent versions (newest first): {', '.join(recent_versions)}")

    lines.append(
        f"\nNext step: Call fetch_package_docs('{name}', '{latest}') to read the "
        f"current API documentation before writing any code."
    )
    return "\n".join(lines)


def _format_npm_result(data: dict, package_name: str) -> str:
    """Format NPM registry data into a clean summary."""
    name        = data.get("name", package_name)
    latest      = data.get("version", "unknown")
    description = data.get("description", "")
    homepage    = data.get("homepage", "")
    npm_url     = f"https://www.npmjs.com/package/{name}"

    lines = [
        f"NPM package info for '{name}':",
        f"  Latest version: {latest}",
        f"  Description:    {description}",
        f"  NPM URL:        {npm_url}",
    ]
    if homepage:
        lines.append(f"  Homepage:       {homepage}")

    lines.append(
        f"\nNote: This package is from the NPM registry (JavaScript/TypeScript). "
        f"Use 'npm install {name}' to install it."
    )
    return "\n".join(lines)


def _build_docs_url(package_name: str, version: str = "") -> str:
    """
    Build the most likely docs URL for a package.
    Priority: CUSTOM_DOC_URLS → readthedocs → PyPI page fallback.
    """
    pkg_lower = package_name.lower()

    # 1. Known custom URL
    if pkg_lower in CUSTOM_DOC_URLS:
        return CUSTOM_DOC_URLS[pkg_lower]

    # 2. readthedocs with known slug override
    if pkg_lower in RTFD_SLUG_OVERRIDES:
        slug = RTFD_SLUG_OVERRIDES[pkg_lower]
        return f"https://{slug}/"

    # 3. readthedocs default pattern
    # Most packages use <package-name>.readthedocs.io
    # Replace underscores with hyphens (PyPI convention)
    slug = pkg_lower.replace("_", "-")
    if version:
        return f"https://{slug}.readthedocs.io/en/{version}/"
    return f"https://{slug}.readthedocs.io/en/stable/"

# ── JS/TS package info helper ──────────────────────────────────────────────

def _get_js_package_info(package_name: str, root: Path) -> str | None:
    """
    Get info about an installed JS/TS package from node_modules and package.json.
    Returns formatted string if package found, None if not found.
    """
    # Find package.json and node_modules
    current = root
    pkg_json_path = None
    for _ in range(6):  # max 6 levels up
        if (current / "package.json").exists():
            pkg_json_path = current / "package.json"
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    if not pkg_json_path:
        return None

    project_root = pkg_json_path.parent
    node_modules_pkg = project_root / "node_modules" / package_name

    if not node_modules_pkg.exists():
        return None

    # Get version from package.json in node_modules
    pkg_info_path = node_modules_pkg / "package.json"
    version = "unknown"
    if pkg_info_path.exists():
        try:
            data = json.loads(pkg_info_path.read_text(encoding="utf-8", errors="replace"))
            version = data.get("version", "unknown")
        except Exception:
            pass

    # Check which section in project's package.json
    section = "dependencies"
    try:
        project_data = json.loads(pkg_json_path.read_text(encoding="utf-8", errors="replace"))
        if package_name in project_data.get("devDependencies", {}):
            section = "devDependencies (dev)"
        elif package_name not in project_data.get("dependencies", {}):
            section = "unknown (not in package.json)"
    except Exception:
        section = "unknown"

    # Get npm list info
    npm_version = ""
    try:
        cmd = ["npm", "list", package_name, "--prefix", str(project_root)]
        use_shell = platform.system() == "Windows"
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_root),
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
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

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

        if proc.returncode == 0:
            npm_version = stdout_bytes.decode("utf-8", errors="replace").strip()
    except Exception:
        pass

    lines = [
        f"JS/TS Package info for '{package_name}':",
        f"  Name: {package_name}",
        f"  Version: {version}",
        f"  Section: {section}",
        f"  Location: {node_modules_pkg}",
    ]
    if npm_version:
        lines.append(f"  npm list output: {npm_version}")

    return "\n".join(lines)


# ── Tool 1: get_installed_package_info ────────────────────────────────────────

class GetInstalledPackageInfoInput(BaseModel):
    package_name: str = Field(
        description="Name of the package to inspect, e.g. 'httpx', 'axios', 'langchain-google-genai'."
    )
    root_path: str = Field(
        default="",
        description="Project root for JS/TS workspace (where package.json lives). Defaults to workspace root."
    )


@tool(args_schema=GetInstalledPackageInfoInput)
def get_installed_package_info(package_name: str, root_path: str = "") -> str:
    """
    Get information about an installed package, auto-detecting if it's
    a Python or JS/TS package.
    For Python: uses pip show to get version, dependencies, and summary.
    For JS/TS: checks node_modules and package.json for version and section.
    Auto-detects the ecosystem: tries Python first (pip show), then
    checks the workspace for JS/TS packages in node_modules.
    Call this BEFORE writing any code that uses the package — you need to know
    the exact installed version to use the correct API.
    Call with just the package_name to scan the current workspace, or provide
    a root_path to check a specific project directory.
    Returns an error string starting with '[' if the package is not found.
    """
    # Resolve workspace root
    if root_path:
        from tools.coding_tools import _resolve_tool_path
        root = _resolve_tool_path(root_path)
    else:
        root = _workspace_root()

    # Try Python first
    logger.info(f"[knowledge] Checking Python package: {package_name}")
    returncode, stdout, stderr = _run_pip_show(package_name)

    if returncode == 0 and stdout:
        # Parse the pip show output into a clean summary
        fields   = {}
        for line in stdout.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()

        lines = [f"Python Package info for '{package_name}':"]
        for key in ("Name", "Version", "Summary", "Home-page", "Location", "Requires", "Required-by"):
            if key in fields and fields[key]:
                lines.append(f"  {key}: {fields[key]}")

        lines.append(
            f"\nIMPORTANT: This is the version currently installed in the active environment. "
            f"Use this version's API when writing code. Do NOT assume the API matches "
            f"documentation for a different version."
        )
        return "\n".join(lines)

    # Python package not found, try JS/TS
    logger.info(f"[knowledge] Python package not found, checking JS/TS: {package_name}")
    js_info = _get_js_package_info(package_name, root)

    if js_info:
        return js_info

    # Not found in either ecosystem
    return (
        f"[get_installed_package_info: '{package_name}' is not installed "
        f"in the current Python environment or JS/TS workspace ({root}). "
        f"Use get_package_latest_version to find the latest version, "
        f"then request install approval.]"
    )


# ── Tool 2: get_package_latest_version ────────────────────────────────────────

class GetPackageLatestVersionInput(BaseModel):
    package_name: str = Field(
        description="Name of the package, e.g. 'requests', 'httpx' (Python) or 'axios', 'express' (JavaScript/TypeScript)."
    )
    ecosystem: str = Field(
        default="",
        description="Registry to query: 'pypi' for Python packages, 'npm' for JS/TS packages. Leave empty for auto-detect (searches both)."
    )


@tool(args_schema=GetPackageLatestVersionInput)
def get_package_latest_version(package_name: str, ecosystem: str = "") -> str:
    """
    Fetch the latest published version of a package from PyPI (Python)
    or NPM registry (JavaScript/TypeScript).
    If ecosystem is specified, queries only that registry.
    If ecosystem is NOT provided, uses smart fallback:
      - Searches both PyPI and NPM registries
      - If found in only one, returns that result
      - If found in both, returns both results clearly labeled
      - If found in neither, returns an error
    Returns version, release date, package URL, and summary.
    Returns an error string starting with '[' on failure.
    """
    ecosystem = ecosystem.lower().strip()

    # If ecosystem is specified, query only that registry
    if ecosystem == "pypi":
        return _get_pypi_latest_version(package_name)
    elif ecosystem == "npm":
        return _get_npm_latest_version(package_name)
    elif ecosystem:
        return f"[get_package_latest_version: Invalid ecosystem '{ecosystem}'. Use 'pypi' or 'npm']"

    # Smart fallback: search both registries
    logger.info(f"[knowledge] Smart fallback for: {package_name}")
    pypi_data = _fetch_pypi_json(package_name)
    npm_data   = _fetch_npm_registry(package_name)

    pypi_found = pypi_data is not None
    npm_found  = npm_data is not None

    if pypi_found and npm_found:
        # Found in both - return both results
        pypi_result = _format_pypi_result(pypi_data, package_name)
        npm_result  = _format_npm_result(npm_data, package_name)
        return (
            f"[Found in both PyPI and NPM. Please specify ecosystem='pypi' or ecosystem='npm']\n\n"
            f"{pypi_result}\n\n{'='*60}\n\n{npm_result}"
        )
    elif pypi_found:
        return _format_pypi_result(pypi_data, package_name)
    elif npm_found:
        return _format_npm_result(npm_data, package_name)
    else:
        return (
            f"[get_package_latest_version: Could not fetch data for '{package_name}'. "
            f"The package may not exist on PyPI or NPM, or the registries may be unreachable.]"
        )


# ── Tool 3: fetch_package_docs ────────────────────────────────────────────────

class FetchPackageDocsInput(BaseModel):
    package_name: str = Field(
        description="Name of the package to fetch docs for, e.g. 'httpx', 'requests'."
    )
    version: str = Field(
        default="",
        description="Version string to target docs for, e.g. '0.27.0'. Leave empty for latest/stable."
    )


@tool(args_schema=FetchPackageDocsInput)
def fetch_package_docs(package_name: str, version: str = "") -> str:
    """
    Fetch the documentation for a Python package.
    Tries the package's official docs URL first (readthedocs, custom domains),
    then falls back to the PyPI project page.
    Returns the first chunk of the docs page as clean text.
    For large doc pages, use fetch_page + fetch_next_chunk with the URL directly.
    Returns an error string starting with '[' on failure.
    """
    pkg_lower = package_name.lower()
    logger.info(f"[knowledge] Fetching docs for {package_name} {version or '(latest)'}")

    docs_url = _build_docs_url(pkg_lower, version)
    logger.info(f"[knowledge] Trying docs URL: {docs_url}")

    content = fetch_page_free(docs_url, force_refresh=True)

    # If the primary URL failed, try the PyPI page as fallback
    if content.startswith("["):
        fallback_url = PYPI_PAGE_URL.format(package=pkg_lower)
        logger.info(f"[knowledge] Primary docs failed ({content[:60]}), trying PyPI page: {fallback_url}")
        content = fetch_page_free(fallback_url, force_refresh=True)

    if content.startswith("["):
        return (
            f"[fetch_package_docs: Could not fetch docs for '{package_name}'. "
            f"Tried: {docs_url} and {PYPI_PAGE_URL.format(package=pkg_lower)}. "
            f"Try using search_web('{package_name} {version} python documentation') "
            f"to find the correct docs URL, then call fetch_page with that URL directly.]"
        )

    header = (
        f"Documentation for '{package_name}'"
        + (f" v{version}" if version else " (latest/stable)")
        + f"\nSource: {docs_url}\n"
        + "─" * 60 + "\n\n"
    )
    return header + content
