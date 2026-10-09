"""PDF report generator for InspectRx audit reports."""

import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from .models import BillAnalysis, SeverityLevel

# Brand colors
PRIMARY = HexColor("#1a56db")
DANGER = HexColor("#dc2626")
WARNING = HexColor("#f59e0b")
SUCCESS = HexColor("#16a34a")
LIGHT_BG = HexColor("#f8fafc")
BORDER = HexColor("#e2e8f0")
TEXT_DARK = HexColor("#1e293b")
TEXT_MED = HexColor("#475569")
TEXT_LIGHT = HexColor("#94a3b8")


def _severity_color(severity: SeverityLevel | None) -> HexColor:
    if severity == SeverityLevel.HIGH:
        return DANGER
    elif severity == SeverityLevel.MEDIUM:
        return WARNING
    elif severity == SeverityLevel.LOW:
        return HexColor("#3b82f6")
    return TEXT_LIGHT


def generate_report_pdf(analysis: BillAnalysis) -> bytes:
    """Generate a PDF audit report from a BillAnalysis."""

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle', parent=styles['Title'],
        fontSize=22, textColor=PRIMARY, spaceAfter=2*mm,
        fontName='Helvetica-Bold',
    )
    subtitle_style = ParagraphStyle(
        'CustomSubtitle', parent=styles['Normal'],
        fontSize=10, textColor=TEXT_MED, spaceAfter=6*mm,
    )
    heading_style = ParagraphStyle(
        'CustomHeading', parent=styles['Heading2'],
        fontSize=14, textColor=TEXT_DARK, spaceBefore=8*mm, spaceAfter=4*mm,
        fontName='Helvetica-Bold',
    )
    body_style = ParagraphStyle(
        'CustomBody', parent=styles['Normal'],
        fontSize=10, textColor=TEXT_DARK, spaceAfter=3*mm,
        leading=14,
    )
    small_style = ParagraphStyle(
        'Small', parent=styles['Normal'],
        fontSize=8, textColor=TEXT_LIGHT, spaceAfter=2*mm,
    )

    elements = []

    # Header
    elements.append(Paragraph("InspectRx", title_style))
    elements.append(Paragraph("Medical Bill Audit Report", subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=1, color=BORDER))
    elements.append(Spacer(1, 4*mm))

    # Bill summary box
    summary_data = []
    if analysis.hospital_name:
        summary_data.append(["Hospital", analysis.hospital_name])
    if analysis.patient_name:
        summary_data.append(["Patient", analysis.patient_name])
    if analysis.bill_date:
        summary_data.append(["Bill Date", analysis.bill_date])
    if analysis.bill_number:
        summary_data.append(["Bill Number", analysis.bill_number])
    summary_data.append(["Total Billed", f"₹{analysis.total_billed:,.2f}"])
    if analysis.total_expected:
        summary_data.append(["Expected Total", f"₹{analysis.total_expected:,.2f}"])
    summary_data.append([
        "Potential Savings",
        f"₹{analysis.total_potential_savings:,.2f}"
    ])
    summary_data.append(["Items Flagged", f"{len(analysis.flagged_items)} of {len(analysis.line_items)}"])
    summary_data.append(["Confidence", f"{analysis.confidence_score:.0%}"])

    summary_table = Table(summary_data, colWidths=[35*mm, 130*mm])
    summary_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), TEXT_MED),
        ('TEXTCOLOR', (1, 0), (1, -1), TEXT_DARK),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3*mm),
        ('TOPPADDING', (0, 0), (-1, -1), 1*mm),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 4*mm))

    # Big savings callout
    if analysis.total_potential_savings > 0:
        savings_pct = (analysis.total_potential_savings / analysis.total_billed * 100) if analysis.total_billed > 0 else 0
        callout_data = [[
            Paragraph(
                f'<font color="white" size="16"><b>₹{analysis.total_potential_savings:,.0f}</b></font>'
                f'<br/><font color="white" size="9">potential savings identified ({savings_pct:.1f}% of bill)</font>',
                ParagraphStyle('callout', alignment=TA_CENTER, textColor=white)
            )
        ]]
        callout_table = Table(callout_data, colWidths=[170*mm])
        callout_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), DANGER if savings_pct > 15 else WARNING),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 6*mm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6*mm),
            ('ROUNDEDCORNERS', [3, 3, 3, 3]),
        ]))
        elements.append(callout_table)
        elements.append(Spacer(1, 4*mm))

    # Summary
    elements.append(Paragraph("Summary", heading_style))
    elements.append(Paragraph(analysis.summary, body_style))

    # Flagged items
    if analysis.flagged_items:
        elements.append(Paragraph("Flagged Items", heading_style))

        for i, item in enumerate(analysis.flagged_items, 1):
            sev_color = _severity_color(item.severity)
            flag_label = (item.flag or "").replace("_", " ").title()

            item_content = []
            item_content.append(Paragraph(
                f'<font color="{sev_color.hexval()}" size="10"><b>#{i} — {flag_label}</b></font>'
                f'  <font color="{TEXT_LIGHT.hexval()}" size="8">[{(item.severity or "info").upper()}]</font>',
                body_style
            ))
            item_content.append(Paragraph(
                f'<b>{item.description}</b> ({item.category})',
                body_style
            ))

            details = f'Billed: ₹{item.billed_amount:,.2f}'
            if item.reference_amount:
                details += f' | Expected: ₹{item.reference_amount:,.2f}'
            if item.potential_savings > 0:
                details += f' | <b>Savings: ₹{item.potential_savings:,.2f}</b>'
            item_content.append(Paragraph(details, body_style))

            if item.explanation:
                item_content.append(Paragraph(
                    f'<i>{item.explanation}</i>',
                    ParagraphStyle('explain', parent=body_style, textColor=TEXT_MED, fontSize=9)
                ))
            if item.reference_source:
                item_content.append(Paragraph(
                    f'Reference: {item.reference_source}', small_style
                ))

            item_content.append(Spacer(1, 2*mm))
            item_content.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
            item_content.append(Spacer(1, 2*mm))

            elements.append(KeepTogether(item_content))

    # Full line items table
    elements.append(Paragraph("All Line Items", heading_style))

    header = ['#', 'Description', 'Category', 'Billed (₹)', 'Expected (₹)', 'Flag']
    table_data = [header]

    for i, item in enumerate(analysis.line_items, 1):
        flag_text = (item.flag or "—").replace("_", " ").title() if item.flag else "—"
        table_data.append([
            str(i),
            item.description[:40] + ("…" if len(item.description) > 40 else ""),
            item.category,
            f'{item.billed_amount:,.0f}',
            f'{item.reference_amount:,.0f}' if item.reference_amount else '—',
            flag_text,
        ])

    col_widths = [8*mm, 60*mm, 25*mm, 25*mm, 25*mm, 25*mm]
    items_table = Table(table_data, colWidths=col_widths, repeatRows=1)

    table_style_cmds = [
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('ALIGN', (3, 0), (4, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 2*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2*mm),
        ('LEFTPADDING', (0, 0), (-1, -1), 2*mm),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]

    # Highlight flagged rows
    for i, item in enumerate(analysis.line_items):
        row = i + 1  # account for header
        if item.flag:
            color = _severity_color(item.severity)
            table_style_cmds.append(('TEXTCOLOR', (5, row), (5, row), color))
            table_style_cmds.append(('FONTNAME', (5, row), (5, row), 'Helvetica-Bold'))
        if row % 2 == 0:
            table_style_cmds.append(('BACKGROUND', (0, row), (-1, row), LIGHT_BG))

    items_table.setStyle(TableStyle(table_style_cmds))
    elements.append(items_table)

    # Recommendations
    if analysis.recommendations:
        elements.append(Paragraph("Recommended Actions", heading_style))
        for i, rec in enumerate(analysis.recommendations, 1):
            elements.append(Paragraph(f'{i}. {rec}', body_style))

    # Limitations
    if analysis.limitations:
        elements.append(Paragraph("Limitations", heading_style))
        for lim in analysis.limitations:
            elements.append(Paragraph(f'• {lim}',
                ParagraphStyle('lim', parent=body_style, textColor=TEXT_MED, fontSize=9)))

    # Footer
    elements.append(Spacer(1, 10*mm))
    elements.append(HRFlowable(width="100%", thickness=1, color=BORDER))
    elements.append(Spacer(1, 3*mm))
    elements.append(Paragraph(
        f'Generated by InspectRx on {analysis.analyzed_at.strftime("%d %b %Y, %I:%M %p")}. '
        f'Bill ID: {analysis.bill_id}. '
        f'This report is for informational purposes. Consult a medical billing professional before disputing charges.',
        ParagraphStyle('footer', parent=small_style, textColor=TEXT_LIGHT, fontSize=7)
    ))

    doc.build(elements)
    return buffer.getvalue()
