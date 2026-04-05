"""
PDF generation for invoices using ReportLab.
"""
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
)


def generate_invoice_pdf(invoice, profile):
    """
    Build and return a BytesIO containing the PDF for *invoice*,
    using company details from *profile* (BusinessProfile).
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    elements = []

    # ── Custom styles ──────────────────────────────────────────────
    title_style = ParagraphStyle(
        'InvTitle', parent=styles['Heading1'], fontSize=22,
        textColor=colors.HexColor('#1e293b'), spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        'InvSubtitle', parent=styles['Normal'], fontSize=10,
        textColor=colors.HexColor('#64748b'),
    )
    heading_style = ParagraphStyle(
        'InvHeading', parent=styles['Heading3'], fontSize=11,
        textColor=colors.HexColor('#334155'), spaceBefore=14, spaceAfter=6,
    )
    normal = ParagraphStyle(
        'InvNormal', parent=styles['Normal'], fontSize=10,
        textColor=colors.HexColor('#334155'), leading=14,
    )
    bold_style = ParagraphStyle(
        'InvBold', parent=normal, fontName='Helvetica-Bold',
    )
    small_muted = ParagraphStyle(
        'InvSmall', parent=normal, fontSize=9,
        textColor=colors.HexColor('#94a3b8'),
    )

    # ── Header: Company + Invoice meta ─────────────────────────────
    company_lines = f"<b>{profile.company_name}</b>"
    if profile.company_address:
        company_lines += f"<br/>{profile.company_address}"
    if profile.company_phone:
        company_lines += f"<br/>Phone: {profile.company_phone}"
    if profile.company_email:
        company_lines += f"<br/>Email: {profile.company_email}"
    if profile.gstin:
        company_lines += f"<br/>GSTIN: {profile.gstin}"

    inv_meta = (
        f"<b>Invoice {invoice.invoice_number}</b><br/>"
        f"Status: {invoice.get_status_display()}<br/>"
        f"Date: {invoice.issue_date.strftime('%d %b %Y')}<br/>"
        f"Due: {invoice.due_date.strftime('%d %b %Y')}"
    )

    header_data = [[Paragraph(company_lines, normal), Paragraph(inv_meta, normal)]]
    header_table = Table(header_data, colWidths=[doc.width * 0.55, doc.width * 0.45])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 6 * mm))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    elements.append(Spacer(1, 6 * mm))

    # ── Bill To ────────────────────────────────────────────────────
    elements.append(Paragraph("Bill To", heading_style))
    client_lines = f"<b>{invoice.client_name}</b>"
    if invoice.client_address:
        client_lines += f"<br/>{invoice.client_address}"
    if invoice.client_email:
        client_lines += f"<br/>{invoice.client_email}"
    if invoice.client_gstin:
        client_lines += f"<br/>GSTIN: {invoice.client_gstin}"
    elements.append(Paragraph(client_lines, normal))
    elements.append(Spacer(1, 6 * mm))

    # ── Line-item table ────────────────────────────────────────────
    items = invoice.items.all()
    cur = profile.currency
    table_header = ['#', 'Description', 'Qty', f'Unit Price ({cur})', f'Amount ({cur})']
    table_data = [table_header]
    for idx, item in enumerate(items, start=1):
        table_data.append([
            str(idx),
            Paragraph(item.description, normal),
            f"{item.quantity:.2f}",
            f"{item.unit_price:,.2f}",
            f"{item.amount:,.2f}",
        ])

    col_widths = [
        doc.width * 0.06,
        doc.width * 0.44,
        doc.width * 0.12,
        doc.width * 0.19,
        doc.width * 0.19,
    ]
    item_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    item_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#334155')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(item_table)
    elements.append(Spacer(1, 6 * mm))

    # ── Totals ─────────────────────────────────────────────────────
    totals_rows = [
        ['Subtotal', f"{cur}{invoice.subtotal:,.2f}"],
    ]
    if invoice.tax_type == 'gst':
        totals_rows.append([f"GST ({invoice.tax_rate}%)", f"{cur}{invoice.tax_amount:,.2f}"])
    elif invoice.tax_type == 'cgst_sgst':
        cgst_amt = invoice.subtotal * invoice.cgst_rate / Decimal('100')
        sgst_amt = invoice.subtotal * invoice.sgst_rate / Decimal('100')
        totals_rows.append([f"CGST ({invoice.cgst_rate}%)", f"{cur}{cgst_amt:,.2f}"])
        totals_rows.append([f"SGST ({invoice.sgst_rate}%)", f"{cur}{sgst_amt:,.2f}"])

    totals_rows.append(['Total', f"{cur}{invoice.total:,.2f}"])

    totals_table = Table(totals_rows, colWidths=[doc.width * 0.30, doc.width * 0.20])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#334155')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))

    # Right-align the totals block
    wrapper = Table([[None, totals_table]], colWidths=[doc.width * 0.50, doc.width * 0.50])
    wrapper.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    elements.append(wrapper)
    elements.append(Spacer(1, 8 * mm))

    # ── Bank Details ───────────────────────────────────────────────
    if profile.bank_name or profile.bank_account:
        elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0')))
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph("Bank Details", heading_style))
        bank_text = ""
        if profile.bank_name:
            bank_text += f"Bank: {profile.bank_name}<br/>"
        if profile.bank_account:
            bank_text += f"Account: {profile.bank_account}<br/>"
        if profile.bank_ifsc:
            bank_text += f"IFSC: {profile.bank_ifsc}"
        elements.append(Paragraph(bank_text, normal))
        elements.append(Spacer(1, 4 * mm))

    # ── Notes ──────────────────────────────────────────────────────
    if invoice.notes:
        elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0')))
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph("Notes / Terms", heading_style))
        elements.append(Paragraph(invoice.notes.replace('\n', '<br/>'), small_muted))

    doc.build(elements)
    buf.seek(0)
    return buf
