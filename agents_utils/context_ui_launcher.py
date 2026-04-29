"""
Gemma Swarm — Context UI Launcher
===================================
Manages the lifecycle of the context_ui.py subprocess.

- launch_context_ui() spawns the UI process if it is not already running.
- shutdown_context_ui() kills it cleanly.
- Registered with atexit so the UI closes automatically when the backend exits.

Called from handlers after the first agent response is posted (coding or supervisor).
"""

import atexit
import logging
import subprocess
import sys
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_ui_process: subprocess.Popen | None = None
_ui_lock_launched = False   # true once we have spawned the process

# File lives at: gemma_swarm/agents_utils/
UI_SCRIPT = Path(__file__).resolve().parent / "context_ui.py"


def launch_context_ui():
    """
    Spawn context_ui.py as a detached subprocess using the same Python
    interpreter that is running the backend.  No-ops if already running.
    """
    global _ui_process, _ui_lock_launched

    # Already launched and still alive?
    if _ui_process is not None and _ui_process.poll() is None:
        return

    if not UI_SCRIPT.exists():
        logger.warning(f"[context_ui_launcher] UI script not found: {UI_SCRIPT}")
        return

    try:
        kwargs = dict(
            args=[sys.executable, str(UI_SCRIPT)],
            # Detach from our console so it gets its own window / no console at all
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            # Suppress output so it doesn't pollute the backend terminal
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _ui_process = subprocess.Popen(**kwargs)
        _ui_lock_launched = True
        logger.info(f"[context_ui_launcher] UI launched (pid={_ui_process.pid})")
    except Exception as e:
        logger.error(f"[context_ui_launcher] Failed to launch UI: {e}")


def shutdown_context_ui():
    """Kill the UI subprocess if it is still running."""
    global _ui_process
    if _ui_process is not None and _ui_process.poll() is None:
        try:
            _ui_process.terminate()
            _ui_process.wait(timeout=3)
            logger.info("[context_ui_launcher] UI process terminated.")
        except Exception as e:
            logger.warning(f"[context_ui_launcher] Could not terminate UI: {e}")
            try:
                _ui_process.kill()
            except Exception:
                pass
    _ui_process = None


# Register shutdown hook so UI closes whenever the backend exits
atexit.register(shutdown_context_ui)
