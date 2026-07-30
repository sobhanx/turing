from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "turing" / "static" / "speech_center"


def test_transcript_edit_js_uses_edit_body_api():
    text = (STATIC / "transcript_edit.js").read_text(encoding="utf-8")
    assert 'method: "PATCH"' in text
    assert "edit-body" in text or "editBodyUrl" in text
    assert "sc-transcript-textarea" in text
    assert "Escape" in text
    assert 'ev.key.toLowerCase() === "s"' in text
    assert "beforeunload" in text
    assert "isSaving" in text


def test_transcript_edit_js_starts_read_only():
    text = (STATIC / "transcript_edit.js").read_text(encoding="utf-8")
    assert "applyReadOnlyMode" in text
    assert "Page always starts read-only" in text
    assert "editPanel.hidden = true" in text
    assert "readPanel.hidden = false" in text
    assert "enterEditMode" in text
    assert "textarea.focus()" in text
    assert "focusEdit" in text


def test_transcript_edit_css_hides_editor_when_hidden():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".sc-transcript-edit[hidden]" in css
    assert "display: none" in css
