"""PDF exporter (ReportLab) with Unicode + RTL support."""

from __future__ import annotations

from io import BytesIO
from typing import BinaryIO

from turing.services.export.base import BaseExporter, ExportRegistry
from turing.services.export.document import ExportDocument
from turing.services.export.font_assets import unicode_font_paths
from turing.services.export.text import prepare_visual_text


@ExportRegistry.register
class PDFExporter(BaseExporter):
    format_code = "pdf"
    content_type = "application/pdf"
    file_extension = "pdf"
    label = "PDF"

    def write(self, document: ExportDocument, output: BinaryIO) -> None:
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.platypus import (
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PDF export requires reportlab. Install django-turing[export]."
            ) from exc

        regular, bold = unicode_font_paths()
        if "TuringSans" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("TuringSans", str(regular)))
            pdfmetrics.registerFont(TTFont("TuringSans-Bold", str(bold)))

        rtl = document.rtl
        align = TA_RIGHT if rtl else TA_LEFT

        def t(value: str) -> str:
            return prepare_visual_text(value or "", rtl=rtl)

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "TuringTitle",
            parent=styles["Heading1"],
            fontName="TuringSans-Bold",
            fontSize=16,
            leading=22,
            alignment=TA_CENTER,
            spaceAfter=8,
        )
        heading_style = ParagraphStyle(
            "TuringHeading",
            parent=styles["Heading2"],
            fontName="TuringSans-Bold",
            fontSize=12,
            leading=16,
            alignment=align,
            spaceBefore=12,
            spaceAfter=6,
        )
        meta_style = ParagraphStyle(
            "TuringMeta",
            parent=styles["Normal"],
            fontName="TuringSans",
            fontSize=9,
            leading=12,
            alignment=align,
        )
        speaker_style = ParagraphStyle(
            "TuringSpeaker",
            parent=styles["Normal"],
            fontName="TuringSans-Bold",
            fontSize=11,
            leading=14,
            alignment=align,
            spaceBefore=10,
            spaceAfter=2,
        )
        body_style = ParagraphStyle(
            "TuringBody",
            parent=styles["Normal"],
            fontName="TuringSans",
            fontSize=10,
            leading=14,
            alignment=align,
            spaceAfter=8,
        )

        buffer = output if hasattr(output, "write") else BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
            title=document.transcript_title,
            author=document.organization,
        )

        story: list = []
        story.append(Paragraph(_escape(t(document.project_title)), title_style))
        story.append(Paragraph(_escape(t(document.transcript_title)), heading_style))
        story.append(Spacer(1, 4 * mm))

        meta_rows = [
            [_escape(t("Media")), _escape(t(document.media_filename))],
            [_escape(t("Organization")), _escape(t(document.organization))],
            [_escape(t("Language")), _escape(t(document.language_code or "—"))],
            [_escape(t("Duration")), _escape(t(document.duration_display))],
            [
                _escape(t("Generated")),
                _escape(t(document.generated_at.strftime("%Y-%m-%d %H:%M UTC"))),
            ],
        ]
        meta_table = Table(
            [
                [Paragraph(a, meta_style), Paragraph(b, meta_style)]
                for a, b in meta_rows
            ],
            colWidths=[35 * mm, 130 * mm],
        )
        meta_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#6b7280")),
                ]
            )
        )
        story.append(meta_table)
        story.append(Spacer(1, 6 * mm))

        story.append(Paragraph(_escape(t("Speakers")), heading_style))
        if document.speakers:
            for name in document.speakers:
                story.append(Paragraph(_escape(f"• {t(name)}"), meta_style))
        else:
            story.append(Paragraph(_escape(t("—")), meta_style))

        story.append(Paragraph(_escape(t("Transcript")), heading_style))
        if document.turns:
            for turn in document.turns:
                if turn.speaker_name:
                    story.append(Paragraph(_escape(t(turn.speaker_name)), speaker_style))
                for para in (turn.text or "").split("\n"):
                    para = para.strip()
                    if para:
                        story.append(Paragraph(_escape(t(para)), body_style))
        else:
            story.append(Paragraph(_escape(t("No transcript available.")), body_style))

        def _add_page_number(canvas, doc_):
            canvas.saveState()
            canvas.setFont("TuringSans", 8)
            page = canvas.getPageNumber()
            label = prepare_visual_text(f"{page}", rtl=False)
            canvas.drawCentredString(A4[0] / 2, 10 * mm, label)
            canvas.restoreState()

        doc.build(story, onFirstPage=_add_page_number, onLaterPages=_add_page_number)
        if buffer is not output and hasattr(buffer, "getvalue"):
            output.write(buffer.getvalue())


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
