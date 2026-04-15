"""
Gemma Swarm — Coding Agent: Layer 2 Knowledge Tools
=====================================================
Anti-hallucination tools. These exist because LLMs will confidently write
code using APIs that changed 6 months ago. Before writing any code that
uses a package, the coding agent MUST call these tools first.

Tools:
    get_installed_package_info(package_name)          — pip show <package>
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

import json
import logging
import subprocess
import platform
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from agents_utils.config import PROJECT_ROOT
from agents_utils.web_fetcher import fetch_page_free

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


# ── Internal helpers ───────────────────────────────────────────────────────────

def _run_pip_show(package_name: str) -> tuple[int, str, str]:
    """Run `pip show <package>` and return (returncode, stdout, stderr)."""
    use_shell = platform.system() == "Windows"
    cmd = f"pip show {package_name}"
    try:
        result = subprocess.run(
            cmd if use_shell else cmd.split(),
            capture_output=True,
            text=True,
            timeout=15,
            shell=use_shell,
            cwd=str(PROJECT_ROOT),
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
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


# ── Tool 1: get_installed_package_info ────────────────────────────────────────

class GetInstalledPackageInfoInput(BaseModel):
    package_name: str = Field(
        description="Name of the Python package to inspect, e.g. 'httpx', 'langchain-google-genai'."
    )


@tool(args_schema=GetInstalledPackageInfoInput)
def get_installed_package_info(package_name: str) -> str:
    """
    Get information about an installed Python package using pip show.
    Returns name, version, location, dependencies, and summary.
    Call this BEFORE writing any code that uses the package — you need to know
    the exact installed version to use the correct API.
    Returns an error string starting with '[' if the package is not installed.
    """
    logger.info(f"[knowledge] pip show {package_name}")
    returncode, stdout, stderr = _run_pip_show(package_name)

    if returncode != 0 or not stdout:
        if "not found" in stderr.lower() or "WARNING: Package(s) not found" in stderr:
            return (
                f"[get_installed_package_info: '{package_name}' is not installed "
                f"in the current environment. Use get_package_latest_version to find "
                f"the latest version, then request install approval.]"
            )
        return f"[get_installed_package_info error: {stderr or 'pip show returned no output'}]"

    # Parse the pip show output into a clean summary
    fields   = {}
    for line in stdout.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()

    lines = [f"Package info for '{package_name}':"]
    for key in ("Name", "Version", "Summary", "Home-page", "Location", "Requires", "Required-by"):
        if key in fields and fields[key]:
            lines.append(f"  {key}: {fields[key]}")

    lines.append(
        f"\nIMPORTANT: This is the version currently installed in the active environment. "
        f"Use this version's API when writing code. Do NOT assume the API matches "
        f"documentation for a different version."
    )
    return "\n".join(lines)


# ── Tool 2: get_package_latest_version ────────────────────────────────────────

class GetPackageLatestVersionInput(BaseModel):
    package_name: str = Field(
        description="Name of the PyPI package, e.g. 'requests', 'httpx', 'langchain-google-genai'."
    )


@tool(args_schema=GetPackageLatestVersionInput)
def get_package_latest_version(package_name: str) -> str:
    """
    Fetch the latest published version of a package from PyPI (no API key needed).
    Returns the latest version, release date, PyPI URL, and a summary.
    Also returns the 5 most recent versions so you can see what changed recently.
    Use this to check if the installed version is up to date, or to find
    what version to recommend installing.
    Returns an error string starting with '[' on failure.
    """
    logger.info(f"[knowledge] PyPI lookup: {package_name}")
    data = _fetch_pypi_json(package_name)

    if data is None:
        return (
            f"[get_package_latest_version: Could not fetch PyPI data for '{package_name}'. "
            f"The package may not exist on PyPI, or PyPI may be unreachable.]"
        )

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
        f"PyPI info for '{name}':",
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
