"""
Gemma Swarm — Agent Context Usage UI
======================================
A small always-on-top desktop widget that shows the current context window
usage for the active agent session (coding or supervisor).

Usage:
    python agents_utils/context_ui.py
    (launched automatically by the backend on first response)

Behaviour:
    - Polls agent_context_usage.json every 1 second in a background thread.
    - Automatically switches to whichever session was updated most recently.
    - Shows project name, model, percentage bar, token counts, and last update.
    - Colour-coded progress bar: green -> yellow -> orange -> red.
    - Custom frameless window with draggable titlebar and minimize button.
    - Always floats on top of other windows.

Dependencies:
    pip install customtkinter
"""

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR         = Path(__file__).resolve().parent
PROJECT_ROOT       = SCRIPT_DIR.parent  # agents_utils/ -> project root
CONTEXT_USAGE_FILE = PROJECT_ROOT / "agent_context_usage.json"

POLL_INTERVAL = 1.0   # seconds
MAX_CONTEXT   = 256_000

WIN_W, WIN_H  = 380, 228
TITLE_H       = 32   # height of custom title bar


# ── Colour helpers ────────────────────────────────────────────────────────────

def _bar_color(pct: float) -> str:
    if pct < 40:  return "#2ecc71"
    if pct < 65:  return "#f1c40f"
    if pct < 85:  return "#e67e22"
    return "#e74c3c"


# ── Data reader ───────────────────────────────────────────────────────────────

def _read_active_session() -> dict | None:
    """Return the entry with the most recent last_updated, or None."""
    try:
        if not CONTEXT_USAGE_FILE.exists():
            return None
        with open(CONTEXT_USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            return None
        best = max(data.keys(), key=lambda sid: data[sid].get("last_updated", ""))
        entry = dict(data[best])
        entry["session_id"] = best
        return entry
    except Exception:
        return None


# ── Main UI ───────────────────────────────────────────────────────────────────

class ContextUI(ctk.CTk):

    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        # ── Frameless, always on top ─────────────────────────────────────────
        self.overrideredirect(True)   # remove native title bar / frame
        self.attributes("-topmost", True)
        self.wm_attributes("-alpha", 0.96)

        self.geometry(f"{WIN_W}x{WIN_H}")
        self._center_window()

        # ── Drag state ───────────────────────────────────────────────────────
        self._drag_x = 0
        self._drag_y = 0

        # ── Internal state ───────────────────────────────────────────────────
        self._last_session_id = None
        self._minimized       = False

        # ── Root frame (rounded feel via bg colour) ───────────────────────────
        self._root_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#1a1a2e")
        self._root_frame.pack(fill="both", expand=True)

        # ── Custom title bar ─────────────────────────────────────────────────
        self._titlebar = ctk.CTkFrame(
            self._root_frame, height=TITLE_H,
            fg_color="#16213e", corner_radius=0,
        )
        self._titlebar.pack(fill="x", side="top")
        self._titlebar.pack_propagate(False)

        self._title_lbl = ctk.CTkLabel(
            self._titlebar,
            text="⚙  Context Monitor",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#88aaff",
            anchor="w",
        )
        self._title_lbl.pack(side="left", padx=10)

        # Minimize button
        self._min_btn = ctk.CTkButton(
            self._titlebar,
            text="—",
            width=28, height=22,
            fg_color="#2a2a4a",
            hover_color="#3a3a6a",
            text_color="#cccccc",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=4,
            command=self._toggle_minimize,
        )
        self._min_btn.pack(side="right", padx=(4, 8), pady=4)

        # Drag bindings on titlebar and its children
        for widget in (self._titlebar, self._title_lbl):
            widget.bind("<ButtonPress-1>",   self._drag_start)
            widget.bind("<B1-Motion>",        self._drag_move)

        # ── Content frame (hidden when minimized) ────────────────────────────
        self._content = ctk.CTkFrame(self._root_frame, fg_color="transparent")
        self._content.pack(fill="both", expand=True, padx=14, pady=(6, 12))

        pad = {"padx": 0, "pady": 3}

        self._lbl_project = ctk.CTkLabel(
            self._content,
            text="— waiting for session —",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        )
        self._lbl_project.pack(fill="x", **pad)

        self._lbl_model = ctk.CTkLabel(
            self._content,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#7788bb",
            anchor="w",
        )
        self._lbl_model.pack(fill="x", pady=(0, 4))

        self._lbl_percent = ctk.CTkLabel(
            self._content,
            text="0.00%",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color="#2ecc71",
        )
        self._lbl_percent.pack(pady=(2, 4))

        self._bar = ctk.CTkProgressBar(
            self._content, width=340, height=16, corner_radius=6,
        )
        self._bar.set(0)
        self._bar.configure(progress_color="#2ecc71")
        self._bar.pack(pady=4)

        self._lbl_tokens = ctk.CTkLabel(
            self._content,
            text="0 / 256,000 tokens",
            font=ctk.CTkFont(size=11),
            text_color="#aaaaaa",
        )
        self._lbl_tokens.pack(**pad)

        self._lbl_updated = ctk.CTkLabel(
            self._content,
            text="",
            font=ctk.CTkFont(size=10),
            text_color="#555577",
        )
        self._lbl_updated.pack(**pad)

        # ── Start polling ────────────────────────────────────────────────────
        self._running     = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Window centering ──────────────────────────────────────────────────────

    def _center_window(self):
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x  = sw - WIN_W - 30
        y  = sh - WIN_H - 60  # near bottom-right, above taskbar
        self.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

    # ── Drag handlers ─────────────────────────────────────────────────────────

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_move(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.geometry(f"+{x}+{y}")

    # ── Minimize / restore ────────────────────────────────────────────────────

    def _toggle_minimize(self):
        if self._minimized:
            # Restore
            self._content.pack(fill="both", expand=True, padx=14, pady=(6, 12))
            self.geometry(f"{WIN_W}x{WIN_H}")
            self._min_btn.configure(text="—")
            self._minimized = False
        else:
            # Collapse to title-bar only
            self._content.pack_forget()
            self.geometry(f"{WIN_W}x{TITLE_H}")
            self._min_btn.configure(text="□")
            self._minimized = True

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            entry = _read_active_session()
            self.after(0, self._update_ui, entry)
            time.sleep(POLL_INTERVAL)

    # ── UI update (must run on main thread via after()) ───────────────────────

    def _update_ui(self, entry: dict | None):
        if entry is None:
            self._lbl_project.configure(text="— waiting for session —")
            self._lbl_model.configure(text="")
            self._lbl_percent.configure(text="0.00%", text_color="#2ecc71")
            self._bar.set(0)
            self._bar.configure(progress_color="#2ecc71")
            self._lbl_tokens.configure(text="0 / 256,000 tokens")
            self._lbl_updated.configure(text="")
            return

        project    = entry.get("project_name", "unknown")
        model      = entry.get("model", "")
        percent    = float(entry.get("context_percent", 0.0))
        cumulative = int(entry.get("context_tokens", 0))
        max_ctx    = int(entry.get("max_context", MAX_CONTEXT))
        last_upd   = entry.get("last_updated", "")
        session_id = entry.get("session_id", "")

        # Flash project name briefly on session switch
        if session_id and session_id != self._last_session_id:
            self._last_session_id = session_id
            project = f"↻  {project}"

        color = _bar_color(percent)

        self._lbl_project.configure(text=project)
        self._lbl_model.configure(text=model)
        self._lbl_percent.configure(text=f"{percent:.2f}%", text_color=color)
        self._bar.set(min(percent / 100.0, 1.0))
        self._bar.configure(progress_color=color)
        self._lbl_tokens.configure(text=f"{cumulative:,} / {max_ctx:,} tokens")

        if last_upd:
            try:
                upd_str = datetime.fromisoformat(last_upd).strftime("updated %H:%M:%S")
            except Exception:
                upd_str = last_upd
        else:
            upd_str = ""
        self._lbl_updated.configure(text=upd_str)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def _on_close(self):
        self._running = False
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ContextUI()
    app.mainloop()
