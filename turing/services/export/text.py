"""Unicode / RTL helpers for exporters."""

from __future__ import annotations

import re

RTL_LANGUAGE_PREFIXES = (
    "fa",
    "ar",
    "he",
    "ur",
    "yi",
    "ps",
    "sd",
    "ku",
    "dv",
)

# Arabic / Persian presentation forms need reshaping for LTR PDF engines.
_ARABIC_SCRIPT_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]"
)


def is_rtl_language(language_code: str) -> bool:
    code = (language_code or "").strip().lower().replace("_", "-")
    if not code:
        return False
    primary = code.split("-", 1)[0]
    return primary in RTL_LANGUAGE_PREFIXES


def contains_arabic_script(text: str) -> bool:
    return bool(text and _ARABIC_SCRIPT_RE.search(text))


def shape_rtl(text: str) -> str:
    """Reshape + bidi-reorder Arabic/Persian for PDF engines that paint LTR."""
    if not text or not contains_arabic_script(text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        return text
    # configuration=False keeps letters closer to logical Unicode for mixed runs.
    reshaper = arabic_reshaper.ArabicReshaper(
        configuration={
            "delete_harakat": False,
            "support_ligatures": True,
        }
    )
    return get_display(reshaper.reshape(text))


def prepare_visual_text(text: str, *, rtl: bool) -> str:
    """
    Prepare text for visual PDF layout.

    Logical Unicode is preserved for DOCX (caller skips this). For PDF RTL
    documents, Arabic-script runs are reshaped so ReportLab paints correctly
    without rewriting non-Arabic content.
    """
    if not rtl:
        return text
    return shape_rtl(text)
