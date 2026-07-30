"""Unicode / RTL helpers for exporters."""

from __future__ import annotations

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


def is_rtl_language(language_code: str) -> bool:
    code = (language_code or "").strip().lower().replace("_", "-")
    if not code:
        return False
    primary = code.split("-", 1)[0]
    return primary in RTL_LANGUAGE_PREFIXES


def shape_rtl(text: str) -> str:
    """Reshape + bidi-reorder Arabic/Persian for PDF engines that paint LTR."""
    if not text:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        return text
    return get_display(arabic_reshaper.reshape(text))


def prepare_visual_text(text: str, *, rtl: bool) -> str:
    if not rtl:
        return text
    return shape_rtl(text)
