"""Font resolution for PDF/DOCX Unicode rendering."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_FONT_DIR = Path(__file__).resolve().parent / "fonts"


@lru_cache(maxsize=1)
def unicode_font_paths() -> tuple[Path, Path]:
    regular = _FONT_DIR / "DejaVuSans.ttf"
    bold = _FONT_DIR / "DejaVuSans-Bold.ttf"
    if not regular.is_file() or not bold.is_file():
        raise FileNotFoundError(
            f"Export Unicode fonts missing under {_FONT_DIR}. "
            "Expected DejaVuSans.ttf and DejaVuSans-Bold.ttf."
        )
    return regular, bold
