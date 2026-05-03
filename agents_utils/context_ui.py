"""
Gemma Swarm — Agent Context Usage UI  (Flet edition)
======================================================
Two separate metrics displayed:

  TOP CHART (Token Activity — from agent_token_activity.json):
    Running total of ALL tokens consumed in this session:
    user input + agent output + tool inputs + tool outputs + thinking.
    Drawn with ft.Canvas so it renders correctly without SVG/image issues.

  BOTTOM BAR (Context Window Fill — from agent_context_usage.json):
    How full the model's current context window is right now.
    This is what context_tracker.py calculates.

The UI auto-polls both files every second and updates without needing
a mouse click — fixed by bridging background-thread updates through
page.run_thread() as required by Flet desktop.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import flet as ft
import flet.canvas as cv

# ── Paths ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR            = Path(__file__).resolve().parent
PROJECT_ROOT          = SCRIPT_DIR.parent
CONTEXT_USAGE_FILE    = PROJECT_ROOT / "agent_context_usage.json"
TOKEN_ACTIVITY_FILE   = PROJECT_ROOT / "agent_token_activity.json"

POLL_INTERVAL = 1.0
MAX_CONTEXT   = 256_000

# ── Window ─────────────────────────────────────────────────────────────────────

WIN_W = 420
WIN_H = 490

# ── Palette ────────────────────────────────────────────────────────────────────

BG_DARK    = "#0d1117"
BG_CARD    = "#161b22"
BORDER_CLR = "#30363d"
TEXT_PRI   = "#e6edf3"
TEXT_SEC   = "#8b949e"
TEXT_ACC   = "#58a6ff"

CHART_LINE = "#58a6ff"
CHART_FILL = "#1f3a5f"
CHART_GRID = "#21262d"

CHART_W = 388
CHART_H = 90


def _bar_color(pct: float) -> str:
    if pct < 40:  return "#3fb950"
    if pct < 65:  return "#d29922"
    if pct < 85:  return "#f0883e"
    return "#f85149"


# ── File readers ────────────────────────────────────────────────────────────────

def _read_context_session() -> dict | None:
    """Most-recent entry from agent_context_usage.json."""
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


def _read_activity_session() -> dict | None:
    """Most-recent entry from agent_token_activity.json."""
    try:
        if not TOKEN_ACTIVITY_FILE.exists():
            return None
        with open(TOKEN_ACTIVITY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            return None
        best = max(data.keys(), key=lambda sid: data[sid].get("last_updated", ""))
        return data[best]
    except Exception:
        return None


# ── Canvas chart builder ────────────────────────────────────────────────────────

def _build_chart_shapes(datapoints: list[dict], w: int = CHART_W, h: int = CHART_H) -> list:
    """
    Build a list of ft.canvas shapes for the cumulative token line chart.
    datapoints: list of {"cumulative": int, ...}
    Returns list of cv.Shape objects.
    """
    pad_l, pad_r, pad_t, pad_b = 6, 6, 8, 6
    chart_w = w - pad_l - pad_r
    chart_h = h - pad_t - pad_b

    shapes = []

    # Background
    shapes.append(cv.Rect(
        x=0, y=0, width=w, height=h,
        paint=ft.Paint(color=BG_CARD),
    ))

    # Grid lines at 25%, 50%, 75%
    for frac in (0.25, 0.5, 0.75):
        gy = round(pad_t + chart_h - frac * chart_h)
        shapes.append(cv.Line(
            x1=pad_l, y1=gy, x2=w - pad_r, y2=gy,
            paint=ft.Paint(
                color=CHART_GRID,
                stroke_width=0.8,
            ),
        ))

    if len(datapoints) < 2:
        # "waiting" label drawn as a Path text is awkward — just skip, the
        # label_chart_status text below the chart handles the empty state.
        return shapes

    vals = [p["cumulative"] for p in datapoints]
    y_max = max(max(vals), 1)
    n = len(vals)

    def pt(i: int, v: int):
        x = pad_l + (i / max(n - 1, 1)) * chart_w
        y = pad_t + chart_h - (v / y_max) * chart_h
        return round(x, 1), round(y, 1)

    pts = [pt(i, v) for i, v in enumerate(vals)]

    # Filled area polygon
    bottom_y = pad_t + chart_h
    poly_pts = [(pts[0][0], bottom_y)] + pts + [(pts[-1][0], bottom_y)]
    shapes.append(cv.Path(
        elements=[
            cv.Path.MoveTo(x=poly_pts[0][0], y=poly_pts[0][1]),
            *[cv.Path.LineTo(x=x, y=y) for x, y in poly_pts[1:]],
            cv.Path.Close(),
        ],
        paint=ft.Paint(color=CHART_FILL, style=ft.PaintingStyle.FILL),
    ))

    # Line
    shapes.append(cv.Path(
        elements=[
            cv.Path.MoveTo(x=pts[0][0], y=pts[0][1]),
            *[cv.Path.LineTo(x=x, y=y) for x, y in pts[1:]],
        ],
        paint=ft.Paint(
            color=CHART_LINE,
            stroke_width=2.0,
            stroke_join=ft.StrokeJoin.ROUND,
            stroke_cap=ft.StrokeCap.ROUND,
            style=ft.PaintingStyle.STROKE,
        ),
    ))

    # Trailing dot
    lx, ly = pts[-1]
    dot_color = _bar_color(0)  # activity chart: no context% — use accent colour
    shapes.append(cv.Circle(
        x=lx, y=ly, radius=4,
        paint=ft.Paint(color=dot_color),
    ))

    return shapes


# ── App ────────────────────────────────────────────────────────────────────────

def main(page: ft.Page):
    page.title      = "⚙ Context Monitor"
    page.bgcolor    = BG_DARK
    page.padding    = 10
    page.theme_mode = ft.ThemeMode.DARK

    page.window.width          = WIN_W
    page.window.height         = WIN_H
    page.window.min_width      = WIN_W
    page.window.min_height     = WIN_H
    page.window.always_on_top  = True
    page.window.title_bar_hidden = False
    page.window.resizable      = False

    # ── Controls ───────────────────────────────────────────────────────────────

    lbl_project_label = ft.Text("PROJECT", size=9, color=TEXT_SEC,
                                weight=ft.FontWeight.W_600,
                                style=ft.TextStyle(letter_spacing=1.2))
    lbl_workspace = ft.Text("— waiting for session —", size=13,
                            weight=ft.FontWeight.BOLD, color=TEXT_SEC)
    lbl_model_label = ft.Text("MODEL", size=9, color=TEXT_SEC,
                              weight=ft.FontWeight.W_600,
                              style=ft.TextStyle(letter_spacing=1.2))
    lbl_model     = ft.Text("", size=13, weight=ft.FontWeight.BOLD, color=TEXT_SEC)

    # ── Activity chart section ─────────────────────────────────────────────────
    _activity_tooltip = ft.Tooltip(
        message=(
            "Cumulative tokens processed this session.\n"
            "Includes: LLM input, LLM output, tool inputs,\n"
            "tool outputs, and thinking blocks."
        ),
        wait_duration=300,
        text_style=ft.TextStyle(size=11, color=TEXT_PRI),
        decoration=ft.BoxDecoration(
            bgcolor="#1c2128",
            border_radius=ft.BorderRadius.all(6),
        ),
        padding=ft.padding.all(8),
    )
    lbl_activity_title = ft.Row(
        spacing=4,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        tooltip=_activity_tooltip,
        controls=[
            ft.Text("Token Activity", size=10,
                    color=TEXT_SEC, weight=ft.FontWeight.W_500),
            ft.Container(
                content=ft.Text("?", size=9, color=TEXT_SEC,
                                weight=ft.FontWeight.BOLD),
                width=14, height=14,
                border_radius=7,
                border=ft.border.all(1, TEXT_SEC),
                alignment=ft.Alignment(0, 0),
            ),
        ],
    )
    lbl_cumulative = ft.Text("0 tokens total", size=13,
                              weight=ft.FontWeight.W_600, color=TEXT_ACC)

    # ft.canvas.Canvas for drawing the line chart
    chart_canvas = cv.Canvas(
        shapes=_build_chart_shapes([]),
        width=CHART_W,
        height=CHART_H,
    )
    lbl_chart_status = ft.Text("waiting for data...", size=10,
                                color=TEXT_SEC, italic=True,
                                text_align=ft.TextAlign.CENTER)

    # ── Context window section ─────────────────────────────────────────────────
    lbl_percent  = ft.Text("0.00%", size=36, weight=ft.FontWeight.BOLD, color="#3fb950")
    progress_bar = ft.ProgressBar(
        value=0.0, width=CHART_W, height=10,
        color="#3fb950", bgcolor="#21262d",
        border_radius=ft.BorderRadius.all(5),
    )
    lbl_tokens  = ft.Text("0 / 256,000 tokens", size=11, color=TEXT_SEC)
    lbl_updated = ft.Text("", size=10, color="#484f58")

    # ── Layout helpers ─────────────────────────────────────────────────────────

    def _divider():
        return ft.Container(height=1, bgcolor=BORDER_CLR,
                            margin=ft.margin.symmetric(vertical=5))

    page.add(
        ft.Container(
            bgcolor=BG_CARD,
            border_radius=10,
            border=ft.border.all(1, BORDER_CLR),
            padding=0,
            content=ft.Column(
                spacing=0,
                controls=[
                    # Header: workspace + model
                    ft.Container(
                        padding=ft.padding.only(left=16, right=16, top=12, bottom=8),
                        content=ft.Column(
                            spacing=2,
                            controls=[
                                lbl_project_label,
                                lbl_workspace,
                                ft.Container(height=4),
                                lbl_model_label,
                                lbl_model,
                            ],
                        ),
                    ),
                    _divider(),
                    # Activity chart
                    ft.Container(
                        padding=ft.padding.symmetric(horizontal=16, vertical=8),
                        content=ft.Column(
                            spacing=6,
                            controls=[
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    controls=[lbl_activity_title, lbl_cumulative],
                                ),
                                ft.Container(
                                    content=ft.Stack([
                                        chart_canvas,
                                        ft.Container(
                                        content=lbl_chart_status,
                                        alignment=ft.Alignment.CENTER,
                                        width=CHART_W,
                                        height=CHART_H,
                                        ),
                                    ]),
                                    border=ft.border.all(1, BORDER_CLR),
                                    border_radius=6,
                                    bgcolor=BG_CARD,
                                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                                ),
                            ],
                        ),
                    ),
                    _divider(),
                    # Context window % + bar + counts
                    ft.Container(
                        padding=ft.padding.only(left=16, right=16, top=8, bottom=16),
                        content=ft.Column(
                            spacing=4,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                            ft.Row(
                                spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            tooltip=ft.Tooltip(
                            message=(
                            "Current fill level of the model's context window.\n"
                            "Calculated from: system prompt + message history\n"
                            "+ tool schemas currently loaded.\n"
                            "Does NOT include tool inputs/outputs or thinking."
                            ),
                            wait_duration=300,
                            text_style=ft.TextStyle(size=11, color=TEXT_PRI),
                            decoration=ft.BoxDecoration(
                                bgcolor="#1c2128",
                                border_radius=ft.BorderRadius.all(6),
                            ),
                            padding=ft.padding.all(8),
                            ),
                            controls=[
                            ft.Text("Context Window", size=10,
                            color=TEXT_SEC,
                            weight=ft.FontWeight.W_500),
                            ft.Container(
                            content=ft.Text("?", size=9,
                                color=TEXT_SEC,
                                    weight=ft.FontWeight.BOLD),
                            width=14, height=14,
                            border_radius=7,
                                border=ft.border.all(1, TEXT_SEC),
                                        alignment=ft.Alignment(0, 0),
                                    ),
                                ],
                            ),
                                ft.Container(height=2),
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.CENTER,
                                    controls=[lbl_percent],
                                ),
                                ft.Container(height=2),
                                progress_bar,
                                ft.Container(height=4),
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    controls=[lbl_tokens, lbl_updated],
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )
    )

    # ── State ──────────────────────────────────────────────────────────────────

    _state = {"running": True}

    # ── UI update (runs on Flet's main thread via page.run_thread) ─────────────

    def _apply_update(ctx_entry, act_entry):
        """Called on Flet's main thread — safe to mutate controls here."""

        # ── Header ──────────────────────────────────────────────────────────
        if ctx_entry:
            project = ctx_entry.get("project_name", "unknown")
            model   = ctx_entry.get("model", "")
            lbl_workspace.value = project
            lbl_workspace.color = TEXT_ACC
            lbl_model.value     = model
            lbl_model.color     = TEXT_ACC
        elif act_entry:
            project = act_entry.get("project_name", "unknown")
            model   = act_entry.get("model", "")
            lbl_workspace.value = project
            lbl_workspace.color = TEXT_ACC
            lbl_model.value     = model
            lbl_model.color     = TEXT_ACC
        else:
            lbl_workspace.value = "— waiting for session —"
            lbl_workspace.color = TEXT_SEC
            lbl_model.value     = ""

        # ── Activity chart ───────────────────────────────────────────────────
        if act_entry:
            cumulative = int(act_entry.get("cumulative_tokens", 0))
            datapoints = act_entry.get("datapoints", [])
            lbl_cumulative.value = f"{cumulative:,} tokens total"
            chart_canvas.shapes = _build_chart_shapes(datapoints)
            lbl_chart_status.value  = "" if len(datapoints) >= 2 else "waiting for data..."
            lbl_chart_status.visible = len(datapoints) < 2
        else:
            lbl_cumulative.value    = "0 tokens total"
            chart_canvas.shapes     = _build_chart_shapes([])
            lbl_chart_status.value   = "waiting for data..."
            lbl_chart_status.visible = True

        # ── Context window bar ───────────────────────────────────────────────
        if ctx_entry:
            percent  = float(ctx_entry.get("context_percent", 0.0))
            tokens   = int(ctx_entry.get("context_tokens", 0))
            max_ctx  = int(ctx_entry.get("max_context", MAX_CONTEXT))
            last_upd = ctx_entry.get("last_updated", "")
            color    = _bar_color(percent)

            lbl_percent.value     = f"{percent:.2f}%"
            lbl_percent.color     = color
            progress_bar.value    = min(percent / 100.0, 1.0)
            progress_bar.color    = color
            lbl_tokens.value      = f"{tokens:,} / {max_ctx:,} tokens"
            if last_upd:
                try:
                    lbl_updated.value = datetime.fromisoformat(last_upd).strftime("updated %H:%M:%S")
                except Exception:
                    lbl_updated.value = last_upd
        else:
            lbl_percent.value     = "0.00%"
            lbl_percent.color     = "#3fb950"
            progress_bar.value    = 0.0
            progress_bar.color    = "#3fb950"
            lbl_tokens.value      = "0 / 256,000 tokens"
            lbl_updated.value     = ""

        page.update()

    # ── Poll loop ──────────────────────────────────────────────────────────────
    # page.run_thread() schedules the callable on Flet's main event loop,
    # which is required for UI updates to render without needing a mouse click.

    def _poll_loop():
        while _state["running"]:
            try:
                ctx_entry = _read_context_session()
                act_entry = _read_activity_session()
                page.run_thread(_apply_update, ctx_entry, act_entry)
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)

    threading.Thread(target=_poll_loop, daemon=True).start()

    def _on_window_event(e: ft.WindowEvent):
        if e.type == ft.WindowEventType.CLOSE:
            _state["running"] = False

    page.window.on_event = _on_window_event


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ft.app(target=main)
