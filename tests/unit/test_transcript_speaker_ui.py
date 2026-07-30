from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "turing" / "static" / "speech_center"


def test_speaker_edit_js_uses_existing_api():
    text = (STATIC / "speaker_edit.js").read_text(encoding="utf-8")
    assert 'method: "PATCH"' in text
    assert "speaker_name" in text
    assert "sc-speaker-chip-editable" in text
    assert "Escape" in text
    assert "Enter" in text
    assert "aria-label" in text
    assert "previousResolved" in text
