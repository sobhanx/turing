"""Shared branding / layout constants for PDF and DOCX exporters."""

from __future__ import annotations

# Elegant business palette (muted, not flashy).
BRAND_PRIMARY = "#1e293b"  # slate-800
BRAND_ACCENT = "#334155"  # slate-700
BRAND_MUTED = "#64748b"  # slate-500
BRAND_BORDER = "#e2e8f0"  # slate-200
BRAND_SURFACE = "#f8fafc"  # slate-50
BRAND_SUMMARY_BG = "#eff6ff"  # blue-50
BRAND_SUMMARY_BORDER = "#bfdbfe"  # blue-200
BRAND_DECISION_BG = "#f0fdf4"  # green-50
BRAND_DECISION_BORDER = "#bbf7d0"  # green-200
BRAND_KEYWORD_BG = "#f1f5f9"  # slate-100
BRAND_FOOTER = "#94a3b8"  # slate-400

# Subtle speaker accent colors (cycled by speaker index).
SPEAKER_COLORS = (
    "#1d4ed8",  # blue
    "#0f766e",  # teal
    "#7c3aed",  # violet
    "#b45309",  # amber
    "#be123c",  # rose
    "#0369a1",  # sky
    "#4d7c0f",  # lime
    "#9333ea",  # purple
)


def speaker_color(index: int) -> str:
    if index < 0:
        return SPEAKER_COLORS[0]
    return SPEAKER_COLORS[index % len(SPEAKER_COLORS)]


def format_generated_at(dt) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")
