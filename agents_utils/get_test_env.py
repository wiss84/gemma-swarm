"""
Shared utility for getting the gemma_test environment Python executable.
Dynamically finds conda by walking up from sys.prefix.
"""
import os
import sys
import platform

TEST_ENV_NAME = "gemma_test"


def get_gemma_test_python_exe() -> str:
    """
    Get the absolute path to the Python executable in the gemma_test environment.
    Dynamically finds conda by walking up from sys.prefix until finding conda-meta folder.
    Skips the current env's conda-meta to find the actual conda root.
    """
    current_prefix = sys.prefix

    conda_root = current_prefix
    found_current_env = False
    while conda_root:
        has_conda_meta = os.path.isdir(os.path.join(conda_root, "conda-meta"))
        if has_conda_meta:
            if found_current_env:
                break
            found_current_env = True
        parent = os.path.dirname(conda_root)
        if parent == conda_root:
            break
        conda_root = parent

    if not os.path.isdir(os.path.join(conda_root, "conda-meta")):
        return sys.executable

    if platform.system() == "Windows":
        candidate = os.path.join(conda_root, "envs", TEST_ENV_NAME, "python.exe")
    else:
        candidate = os.path.join(conda_root, "envs", TEST_ENV_NAME, "bin", "python")

    if os.path.exists(candidate):
        return candidate

    return sys.executable