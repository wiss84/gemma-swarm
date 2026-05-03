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
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_ui_process: subprocess.Popen | None = None
_ui_lock_launched = False   # true once we have spawned the process
_ui_launch_lock = threading.Lock()  # prevents duplicate spawns from concurrent threads

# File lives at: gemma_swarm/agents_utils/
UI_SCRIPT = Path(__file__).resolve().parent / "context_ui.py"


def launch_context_ui():
    """
    Spawn context_ui.py as a subprocess using the same Python interpreter
    that is running the backend.  No-ops if already running.
    Thread-safe: uses a lock to prevent duplicate spawns from concurrent threads.

    Intentionally does NOT use CREATE_NO_WINDOW on Windows — keeping the UI
    attached to the same console session means Windows closes it automatically
    when the terminal (and therefore the backend) is shut down.
    """
    global _ui_process, _ui_lock_launched

    with _ui_launch_lock:
        # Already launched and still alive?
        if _ui_process is not None and _ui_process.poll() is None:
            return

        if not UI_SCRIPT.exists():
            logger.warning(f"[context_ui_launcher] UI script not found: {UI_SCRIPT}")
            return

        try:
            _ui_process = subprocess.Popen(
                args=[sys.executable, str(UI_SCRIPT)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
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
