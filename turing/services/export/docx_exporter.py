"""DOCX exporter (python-docx) with Unicode + RTL paragraphs."""

from __future__ import annotations

from typing import BinaryIO

from turing.services.export.base import BaseExporter, ExportRegistry
from turing.services.export.document import ExportDocument
from turing.services.export.font_assets import unicode_font_paths


@ExportRegistry.register
class DOCXExporter(BaseExporter):
    format_code = "docx"
    content_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    file_extension = "docx"
    label = "Word (DOCX)"

    def write(self, document: ExportDocument, output: BinaryIO) -> None:
        try:
            from docx import Document
            from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
            from docx.oxml.ns import qn
            from docx.shared import Pt, RGBColor
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "DOCX export requires python-docx. Install django-turing[export]."
            ) from exc

        # Resolve fonts early so missing assets fail clearly.
        unicode_font_paths()
        font_name = "DejaVu Sans"
        doc = Document()
        rtl = document.rtl
        align = WD_ALIGN_PARAGRAPH.RIGHT if rtl else WD_ALIGN_PARAGRAPH.LEFT

        title = doc.add_heading(document.project_title or "Speech Center", level=0)
        _set_paragraph_rtl(title, rtl=rtl)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in title.runs:
            run.font.name = font_name
            run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)

        heading = doc.add_heading(document.transcript_title or "Transcript", level=1)
        _set_paragraph_rtl(heading, rtl=rtl)
        heading.alignment = align
        for run in heading.runs:
            run.font.name = font_name

        meta = [
            ("Media", document.media_filename),
            ("Organization", document.organization),
            ("Language", document.language_code or "—"),
            ("Duration", document.duration_display),
            ("Generated", document.generated_at.strftime("%Y-%m-%d %H:%M UTC")),
        ]
        for label, value in meta:
            p = doc.add_paragraph()
            _set_paragraph_rtl(p, rtl=rtl)
            p.alignment = align
            p.paragraph_format.space_after = Pt(2)
            run_l = p.add_run(f"{label}: ")
            run_l.bold = True
            run_l.font.name = font_name
            run_l.font.size = Pt(10)
            run_l.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)
            run_v = p.add_run(value or "—")
            run_v.font.name = font_name
            run_v.font.size = Pt(10)

        speakers_h = doc.add_heading("Speakers", level=2)
        _set_paragraph_rtl(speakers_h, rtl=rtl)
        speakers_h.alignment = align
        if document.speakers:
            for name in document.speakers:
                p = doc.add_paragraph(style="List Bullet")
                _set_paragraph_rtl(p, rtl=rtl)
                p.alignment = align
                run = p.add_run(name)
                run.font.name = font_name
        else:
            p = doc.add_paragraph("—")
            _set_paragraph_rtl(p, rtl=rtl)

        body_h = doc.add_heading("Transcript", level=2)
        _set_paragraph_rtl(body_h, rtl=rtl)
        body_h.alignment = align

        if document.turns:
            for turn in document.turns:
                if turn.speaker_name:
                    sp = doc.add_paragraph()
                    _set_paragraph_rtl(sp, rtl=rtl)
                    sp.alignment = align
                    sp.paragraph_format.space_before = Pt(10)
                    sp.paragraph_format.space_after = Pt(2)
                    run = sp.add_run(turn.speaker_name)
                    run.bold = True
                    run.font.name = font_name
                    run.font.size = Pt(11)
                tp = doc.add_paragraph()
                _set_paragraph_rtl(tp, rtl=rtl)
                tp.alignment = align
                tp.paragraph_format.space_after = Pt(10)
                tp.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
                run = tp.add_run(turn.text)
                run.font.name = font_name
                run.font.size = Pt(11)
        else:
            p = doc.add_paragraph("No transcript available.")
            _set_paragraph_rtl(p, rtl=rtl)

        doc.save(output)


def _set_paragraph_rtl(paragraph, *, rtl: bool) -> None:
    """Mark paragraph as bidirectional RTL when needed."""
    if not rtl:
        return
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()
    bidi = OxmlElement("w:bidi")
    bidi.set(qn("w:val"), "1")
    p_pr.append(bidi)
