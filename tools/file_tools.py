"""
Gemma Swarm — File System Tools
==================================
Tools for reading, writing, listing, and deleting files
within the project workspace.

All operations are sandboxed to the workspace path.
Attempts to access files outside workspace are blocked.

delete_file and install_package require human confirmation
via the human gate node — they do NOT execute directly.
"""

import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Workspace path is set at runtime by the graph
# Tools validate all paths against it
_workspace_path: str = ""


def set_workspace(path: str):
    """Called at conversation start when workspace is selected."""
    global _workspace_path
    _workspace_path = path
    logger.info(f"[file_tools] Workspace set to: {path}")


def _validate_path(requested_path: str) -> tuple[bool, str]:
    """
    Ensure requested path is inside the workspace.
    Returns (is_valid, absolute_path_or_error)
    """
    if not _workspace_path:
        return False, "No workspace path set. Please select a workspace first."

    workspace = Path(_workspace_path).resolve()
    target    = (workspace / requested_path).resolve()

    # Check if target is inside workspace
    try:
        target.relative_to(workspace)
    except ValueError:
        return False, f"Access denied: path is outside workspace."

    return True, str(target)


# ── Schemas ────────────────────────────────────────────────────────────────────

class ReadFileInput(BaseModel):
    path: str = Field(description="Path to the file to read, relative to workspace root")


class WriteFileInput(BaseModel):
    path: str    = Field(description="Path to write the file, relative to workspace root")
    content: str = Field(description="Complete file content to write")


class ListDirectoryInput(BaseModel):
    path: str = Field(
        default=".",
        description="Directory path to list, relative to workspace root. Use '.' for workspace root."
    )


class DeleteFileInput(BaseModel):
    path: str = Field(description="Path to the file to delete, relative to workspace root")


class CreateDirectoryInput(BaseModel):
    path: str = Field(description="Directory path to create, relative to workspace root")


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool(args_schema=ReadFileInput)
def read_file(path: str) -> str:
    """
    Read and return the content of a file in the workspace.
    Use this before editing a file to see its current content.
    """
    valid, abs_path = _validate_path(path)
    if not valid:
        return f"Error: {abs_path}"

    target = Path(abs_path)

    if not target.exists():
        return f"Error: File not found: {path}"

    if not target.is_file():
        return f"Error: {path} is a directory, not a file."

    try:
        content = target.read_text(encoding="utf-8")
        # Add line numbers for reference
        lines = content.splitlines()
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        return f"File: {path} ({len(lines)} lines)\n\n{numbered}"
    except OSError as e:
        return f"Error reading file: {e}"


@tool(args_schema=WriteFileInput)
def write_file(path: str, content: str) -> str:
    """
    Write content to a file in the workspace.
    Creates the file if it doesn't exist.
    Overwrites the entire file if it already exists.
    Use read_file first if you need to see the current content before rewriting.
    """
    valid, abs_path = _validate_path(path)
    if not valid:
        return f"Error: {abs_path}"

    target = Path(abs_path)

    # Create parent directories if needed
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        target.write_text(content, encoding="utf-8")
        lines = len(content.splitlines())
        logger.info(f"[file_tools] Written: {path} ({lines} lines)")
        return f"Successfully written: {path} ({lines} lines)"
    except OSError as e:
        return f"Error writing file: {e}"


@tool(args_schema=ListDirectoryInput)
def list_directory(path: str = ".") -> str:
    """
    List all files and directories in a workspace folder.
    Use '.' to list the workspace root.
    Shows file sizes and types.
    """
    valid, abs_path = _validate_path(path)
    if not valid:
        return f"Error: {abs_path}"

    target = Path(abs_path)

    if not target.exists():
        return f"Error: Directory not found: {path}"

    if not target.is_dir():
        return f"Error: {path} is a file, not a directory."

    try:
        items = sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name))
        if not items:
            return f"Directory is empty: {path}"

        lines = [f"Contents of {path}:"]
        for item in items:
            if item.is_dir():
                lines.append(f"  📁 {item.name}/")
            else:
                size = item.stat().st_size
                size_str = f"{size}B" if size < 1024 else f"{size//1024}KB"
                lines.append(f"  📄 {item.name} ({size_str})")

        return "\n".join(lines)
    except OSError as e:
        return f"Error listing directory: {e}"


@tool(args_schema=DeleteFileInput)
def delete_file(path: str) -> str:
    """
    Request deletion of a file in the workspace.
    This requires human confirmation before executing.
    The file will NOT be deleted immediately — a confirmation request will be sent.
    """
    valid, abs_path = _validate_path(path)
    if not valid:
        return f"Error: {abs_path}"

    target = Path(abs_path)

    if not target.exists():
        return f"Error: File not found: {path}"

    # Return a special marker that the tool executor node
    # detects and converts to a human confirmation request
    return f"REQUIRES_CONFIRMATION:delete_file:{path}"


@tool(args_schema=CreateDirectoryInput)
def create_directory(path: str) -> str:
    """
    Create a new directory inside the workspace.
    Creates all intermediate directories if needed.
    """
    valid, abs_path = _validate_path(path)
    if not valid:
        return f"Error: {abs_path}"

    target = Path(abs_path)

    if target.exists():
        return f"Directory already exists: {path}"

    try:
        target.mkdir(parents=True, exist_ok=True)
        logger.info(f"[file_tools] Created directory: {path}")
        return f"Successfully created directory: {path}"
    except OSError as e:
        return f"Error creating directory: {e}"


# ── Tool Registry ──────────────────────────────────────────────────────────────

FILE_TOOLS = [
    read_file,
    write_file,
    list_directory,
    delete_file,
    create_directory,
]
