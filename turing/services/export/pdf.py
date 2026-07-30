"""PDF exporter (ReportLab) — premium meeting report layout."""

from __future__ import annotations

from io import BytesIO
from typing import BinaryIO

from turing.services.export.base import BaseExporter, ExportRegistry
from turing.services.export.document import ExportDocument
from turing.services.export.font_assets import unicode_font_paths
from turing.services.export import labels as L
from turing.services.export.layout import (
    BRAND_ACCENT,
    BRAND_BORDER,
    BRAND_DECISION_BG,
    BRAND_DECISION_BORDER,
    BRAND_FOOTER,
    BRAND_KEYWORD_BG,
    BRAND_MUTED,
    BRAND_PRIMARY,
    BRAND_SUMMARY_BG,
    BRAND_SUMMARY_BORDER,
    BRAND_SURFACE,
    format_generated_at,
    speaker_color,
)
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
                HRFlowable,
                ListFlowable,
                ListItem,
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
        vis = document.visibility
        gen_ts = document.generated_display or format_generated_at(document.generated_at)

        def t(value: str) -> str:
            return prepare_visual_text(value or "", rtl=rtl)

        def P(text: str, style: ParagraphStyle) -> Paragraph:
            return Paragraph(_escape(t(text)), style)

        styles = getSampleStyleSheet()
        cover_eyebrow = ParagraphStyle(
            "CoverEyebrow",
            parent=styles["Normal"],
            fontName="TuringSans",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor(BRAND_MUTED),
            alignment=TA_CENTER,
            spaceAfter=6,
        )
        cover_title = ParagraphStyle(
            "CoverTitle",
            parent=styles["Heading1"],
            fontName="TuringSans-Bold",
            fontSize=22,
            leading=28,
            textColor=colors.HexColor(BRAND_PRIMARY),
            alignment=TA_CENTER,
            spaceAfter=6,
        )
        cover_subtitle = ParagraphStyle(
            "CoverSubtitle",
            parent=styles["Normal"],
            fontName="TuringSans",
            fontSize=12,
            leading=16,
            textColor=colors.HexColor(BRAND_ACCENT),
            alignment=TA_CENTER,
            spaceAfter=14,
        )
        section_h = ParagraphStyle(
            "SectionH",
            parent=styles["Heading2"],
            fontName="TuringSans-Bold",
            fontSize=13,
            leading=18,
            textColor=colors.HexColor(BRAND_PRIMARY),
            alignment=align,
            spaceBefore=14,
            spaceAfter=8,
        )
        meta_label = ParagraphStyle(
            "MetaLabel",
            parent=styles["Normal"],
            fontName="TuringSans",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor(BRAND_MUTED),
            alignment=align,
        )
        meta_value = ParagraphStyle(
            "MetaValue",
            parent=styles["Normal"],
            fontName="TuringSans",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor(BRAND_PRIMARY),
            alignment=align,
        )
        body = ParagraphStyle(
            "Body",
            parent=styles["Normal"],
            fontName="TuringSans",
            fontSize=10,
            leading=15,
            textColor=colors.HexColor(BRAND_PRIMARY),
            alignment=align,
            spaceAfter=4,
        )
        summary_body = ParagraphStyle(
            "SummaryBody",
            parent=body,
            fontSize=10.5,
            leading=16,
            spaceBefore=2,
            spaceAfter=2,
        )
        bullet = ParagraphStyle(
            "Bullet",
            parent=body,
            leftIndent=4,
            bulletIndent=0,
            spaceAfter=3,
        )
        speaker_name = ParagraphStyle(
            "SpeakerName",
            parent=styles["Normal"],
            fontName="TuringSans-Bold",
            fontSize=11,
            leading=14,
            alignment=align,
            spaceBefore=2,
            spaceAfter=1,
        )
        speaker_time = ParagraphStyle(
            "SpeakerTime",
            parent=styles["Normal"],
            fontName="TuringSans",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor(BRAND_MUTED),
            alignment=align,
            spaceAfter=3,
        )
        turn_body = ParagraphStyle(
            "TurnBody",
            parent=body,
            fontSize=10,
            leading=15,
            spaceAfter=2,
        )
        keyword_style = ParagraphStyle(
            "Keyword",
            parent=styles["Normal"],
            fontName="TuringSans",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor(BRAND_ACCENT),
            alignment=TA_CENTER,
        )
        muted = ParagraphStyle(
            "Muted",
            parent=body,
            textColor=colors.HexColor(BRAND_MUTED),
            fontSize=9,
        )

        buffer = output if hasattr(output, "write") else BytesIO()
        page_w, page_h = A4
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=document.transcript_title,
            author=document.organization,
        )
        content_w = page_w - doc.leftMargin - doc.rightMargin

        story: list = []

        # ----- Cover -----
        story.append(Spacer(1, 8 * mm))
        story.append(P(L.BRAND_NAME, cover_eyebrow))
        story.append(P(L.REPORT_TITLE, cover_title))
        story.append(P(document.cover_title(), cover_subtitle))

        logo_cell = Paragraph(
            _escape(t(L.LOGO_PLACEHOLDER)),
            ParagraphStyle(
                "LogoPh",
                parent=styles["Normal"],
                fontName="TuringSans",
                fontSize=9,
                textColor=colors.HexColor(BRAND_MUTED),
                alignment=TA_CENTER,
            ),
        )
        logo_table = Table([[logo_cell]], colWidths=[40 * mm])
        logo_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(BRAND_SURFACE)),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(BRAND_BORDER)),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        story.append(logo_table)
        story.append(Spacer(1, 8 * mm))

        cover_stats = [(t(a), t(b)) for a, b in document.cover_rows()]
        if cover_stats:
            cover_rows = [
                [Paragraph(_escape(a), meta_label), Paragraph(_escape(b), meta_value)]
                for a, b in cover_stats
            ]
            cover_table = Table(cover_rows, colWidths=[45 * mm, content_w - 45 * mm])
            cover_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(BRAND_SURFACE)),
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(BRAND_BORDER)),
                        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor(BRAND_BORDER)),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            story.append(cover_table)
            story.append(Spacer(1, 6 * mm))
        story.append(
            HRFlowable(
                width="100%",
                thickness=0.8,
                color=colors.HexColor(BRAND_BORDER),
                spaceBefore=2,
                spaceAfter=2,
            )
        )

        # ----- Meeting Information -----
        meeting_rows = [(t(a), t(b)) for a, b in document.meeting_info_rows()]
        if meeting_rows:
            story.append(P(L.SECTION_MEETING_INFO, section_h))
            info_data = [
                [Paragraph(_escape(a), meta_label), Paragraph(_escape(b), meta_value)]
                for a, b in meeting_rows
            ]
            info_table = Table(info_data, colWidths=[45 * mm, content_w - 45 * mm])
            info_table.setStyle(
                TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(BRAND_BORDER)),
                        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor(BRAND_BORDER)),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor(BRAND_SURFACE)),
                    ]
                )
            )
            story.append(info_table)

        # ----- Executive Summary -----
        if vis.show_ai_summary:
            story.append(P(L.SECTION_EXECUTIVE_SUMMARY, section_h))
            summary_text = (document.summary or "").strip() or L.EMPTY_SUMMARY
            summary_para = P(summary_text, summary_body)
            summary_card = Table([[summary_para]], colWidths=[content_w])
            summary_card.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(BRAND_SUMMARY_BG)),
                        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(BRAND_SUMMARY_BORDER)),
                        ("LEFTPADDING", (0, 0), (-1, -1), 12),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                        ("TOPPADDING", (0, 0), (-1, -1), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ]
                )
            )
            story.append(summary_card)

        # ----- Key Topics -----
        if vis.show_key_topics:
            story.append(P(L.SECTION_KEY_TOPICS, section_h))
            if document.topics:
                items = []
                for topic in document.topics:
                    items.append(
                        ListItem(
                            P(topic, bullet),
                            leftIndent=12,
                            bulletColor=colors.HexColor(BRAND_ACCENT),
                        )
                    )
                story.append(
                    ListFlowable(
                        items,
                        bulletType="bullet",
                        start="•",
                        leftIndent=10,
                        bulletFontName="TuringSans",
                        bulletFontSize=9,
                    )
                )
            else:
                story.append(P(L.EMPTY_TOPICS, muted))

        # ----- Action Items -----
        if vis.show_action_items:
            story.append(P(L.SECTION_ACTION_ITEMS, section_h))
            if document.action_items:
                for item in document.action_items:
                    line = f"☐  {item.task}"
                    if item.owner:
                        line += f"  —  {item.owner}"
                    if item.deadline:
                        line += f"  ({item.deadline})"
                    story.append(P(line, bullet))
            else:
                story.append(P(L.EMPTY_ACTION_ITEMS, muted))

        # ----- Decisions -----
        if vis.show_decisions:
            story.append(P(L.SECTION_DECISIONS, section_h))
            if document.decisions:
                decision_paras = [P(d, bullet) for d in document.decisions]
                decision_card = Table([[x] for x in decision_paras], colWidths=[content_w])
                decision_card.setStyle(
                    TableStyle(
                        [
                            (
                                "BACKGROUND",
                                (0, 0),
                                (-1, -1),
                                colors.HexColor(BRAND_DECISION_BG),
                            ),
                            (
                                "BOX",
                                (0, 0),
                                (-1, -1),
                                0.8,
                                colors.HexColor(BRAND_DECISION_BORDER),
                            ),
                            ("LEFTPADDING", (0, 0), (-1, -1), 10),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ]
                    )
                )
                story.append(decision_card)
            else:
                story.append(P(L.EMPTY_DECISIONS, muted))

        # ----- Keywords -----
        if vis.show_keywords:
            story.append(P(L.SECTION_KEYWORDS, section_h))
            if document.keywords:
                chip_cells = []
                for kw in document.keywords:
                    chip = Paragraph(_escape(t(kw)), keyword_style)
                    chip_table = Table([[chip]], colWidths=[None])
                    chip_table.setStyle(
                        TableStyle(
                            [
                                (
                                    "BACKGROUND",
                                    (0, 0),
                                    (-1, -1),
                                    colors.HexColor(BRAND_KEYWORD_BG),
                                ),
                                (
                                    "BOX",
                                    (0, 0),
                                    (-1, -1),
                                    0.4,
                                    colors.HexColor(BRAND_BORDER),
                                ),
                                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                                ("TOPPADDING", (0, 0), (-1, -1), 4),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ]
                        )
                    )
                    chip_cells.append(chip_table)
                rows = []
                row: list = []
                for chip in chip_cells:
                    row.append(chip)
                    if len(row) == 4:
                        rows.append(row)
                        row = []
                if row:
                    while len(row) < 4:
                        row.append("")
                    rows.append(row)
                if rows:
                    chip_w = content_w / 4 - 2
                    kw_table = Table(rows, colWidths=[chip_w] * 4)
                    kw_table.setStyle(
                        TableStyle(
                            [
                                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                                ("TOPPADDING", (0, 0), (-1, -1), 2),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                            ]
                        )
                    )
                    story.append(kw_table)
            else:
                story.append(P(L.EMPTY_KEYWORDS, muted))

        # ----- Transcript -----
        if vis.show_full_transcript:
            story.append(P(L.SECTION_TRANSCRIPT, section_h))
            story.append(
                HRFlowable(
                    width="100%",
                    thickness=0.6,
                    color=colors.HexColor(BRAND_BORDER),
                    spaceBefore=0,
                    spaceAfter=8,
                )
            )

            speaker_index: dict[str, int] = {}
            next_idx = 0
            if document.turns:
                for turn in document.turns:
                    name = turn.speaker_name or "—"
                    if name not in speaker_index:
                        speaker_index[name] = next_idx
                        next_idx += 1
                    color = colors.HexColor(speaker_color(speaker_index[name]))
                    name_style = ParagraphStyle(
                        f"SpeakerName_{speaker_index[name]}",
                        parent=speaker_name,
                        textColor=color,
                    )
                    story.append(Paragraph(_escape(t(name)), name_style))
                    ts = document.turn_timestamp(turn.start_display)
                    if ts:
                        story.append(P(ts, speaker_time))
                    for para in (turn.text or "").split("\n"):
                        para = para.strip()
                        if para:
                            story.append(P(para, turn_body))
                    story.append(
                        HRFlowable(
                            width="100%",
                            thickness=0.6,
                            color=color,
                            spaceBefore=2,
                            spaceAfter=8,
                        )
                    )
            else:
                story.append(P(L.EMPTY_TRANSCRIPT, muted))

        def _footer(canvas, doc_):
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor(BRAND_BORDER))
            canvas.setLineWidth(0.5)
            y = 12 * mm
            canvas.line(doc.leftMargin, y + 5, page_w - doc.rightMargin, y + 5)
            canvas.setFont("TuringSans", 7)
            canvas.setFillColor(colors.HexColor(BRAND_FOOTER))
            page = canvas.getPageNumber()
            left = prepare_visual_text(L.FOOTER_GENERATED_BY, rtl=rtl)
            center = prepare_visual_text(str(page), rtl=False)
            right = prepare_visual_text(gen_ts, rtl=rtl)
            if rtl:
                canvas.drawRightString(page_w - doc.rightMargin, y, left)
                canvas.drawString(doc.leftMargin, y, right)
            else:
                canvas.drawString(doc.leftMargin, y, left)
                canvas.drawRightString(page_w - doc.rightMargin, y, right)
            canvas.drawCentredString(page_w / 2, y, center)
            canvas.restoreState()

        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
        if buffer is not output and hasattr(buffer, "getvalue"):
            output.write(buffer.getvalue())


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
