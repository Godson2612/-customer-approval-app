# utils/pdf_fill.py
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


class PDFGenerationError(ValueError):
    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


def generate_customer_approval_pdf(
    *,
    template_path: Path,
    form_data: dict[str, Any],
    customer_signature_bytes: bytes,
    technician_signature_bytes: bytes,
) -> dict[str, Any]:
    if not form_data.get("job_number"):
        raise PDFGenerationError("Job number is required to generate the PDF.")

    file_stem = _safe_filename(
        f"customer-approval-{form_data.get('job_number', 'document')}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    )

    overlay_buffer = io.BytesIO()
    pdf = canvas.Canvas(overlay_buffer, pagesize=LETTER)
    width, height = LETTER

    pdf.setTitle("Customer Approval")
    pdf.setAuthor("Customer Approval App")

    pdf.setFillColor(HexColor("#F4F7FB"))
    pdf.rect(0, 0, width, height, fill=1, stroke=0)

    pdf.setFillColor(HexColor("#102033"))
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(48, height - 56, "Customer Approval")
    pdf.setFont("Helvetica", 10)
    pdf.setFillColor(HexColor("#617188"))
    pdf.drawString(48, height - 74, "Generated for Render-compatible ephemeral deployments")

    sections = [
        ("Job Details", [("Job Number", form_data.get("job_number", "")), ("Installation Date", form_data.get("installation_date", ""))]),
        (
            "Customer Details",
            [
                ("Customer Name", form_data.get("customer_name", "")),
                ("Service Address", form_data.get("service_address", "")),
                ("City, State, ZIP", form_data.get("city_state_zip", "")),
                ("Primary Phone", form_data.get("phone_number", "")),
                ("Work Phone", form_data.get("work_phone_number", "")),
                ("Email", form_data.get("email", "")),
            ],
        ),
        ("Technician Details", [("Technician Name", form_data.get("technician_name", ""))]),
    ]

    top = height - 112
    for title, rows in sections:
        top = _draw_section(pdf, title, rows, top)

    top -= 8
    pdf.setFont("Helvetica-Bold", 11)
    pdf.setFillColor(HexColor("#102033"))
    pdf.drawString(48, top, "Signatures")
    top -= 16

    _draw_signature_block(pdf, x=48, y=top - 88, label="Customer Signature", signature_bytes=customer_signature_bytes)
    _draw_signature_block(pdf, x=320, y=top - 88, label="Technician Signature", signature_bytes=technician_signature_bytes)

    pdf.showPage()
    pdf.save()
    overlay_buffer.seek(0)

    final_bytes = _merge_with_template(template_path=template_path, overlay_bytes=overlay_buffer.getvalue())

    return {
        "filename": f"{file_stem}.pdf",
        "bytes": final_bytes,
    }


def _draw_section(pdf: canvas.Canvas, title: str, rows: list[tuple[str, str]], top: float) -> float:
    pdf.setFillColor(HexColor("#FFFFFF"))
    height = 34 + (len(rows) * 24)
    pdf.roundRect(40, top - height, 532, height, 18, fill=1, stroke=0)

    pdf.setFillColor(HexColor("#102033"))
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(56, top - 22, title)

    current_y = top - 42
    for label, value in rows:
        pdf.setFont("Helvetica-Bold", 9)
        pdf.setFillColor(HexColor("#617188"))
        pdf.drawString(56, current_y, label.upper())
        pdf.setFont("Helvetica", 11)
        pdf.setFillColor(HexColor("#102033"))
        pdf.drawString(180, current_y, str(value or "-"))
        current_y -= 24

    return top - height - 16


def _draw_signature_block(pdf: canvas.Canvas, *, x: float, y: float, label: str, signature_bytes: bytes) -> None:
    pdf.setFillColor(HexColor("#FFFFFF"))
    pdf.roundRect(x, y, 224, 96, 18, fill=1, stroke=0)
    pdf.setFillColor(HexColor("#617188"))
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(x + 14, y + 78, label.upper())
    pdf.line(x + 14, y + 24, x + 210, y + 24)
    pdf.drawImage(
        ImageReader(io.BytesIO(signature_bytes)),
        x + 18,
        y + 30,
        width=188,
        height=40,
        preserveAspectRatio=True,
        mask="auto",
    )


def _merge_with_template(*, template_path: Path, overlay_bytes: bytes) -> bytes:
    if not template_path.exists():
        return overlay_bytes

    try:
        template_reader = PdfReader(str(template_path))
        overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
        writer = PdfWriter()

        if not template_reader.pages:
            return overlay_bytes

        first_page = template_reader.pages[0]
        first_page.merge_page(overlay_reader.pages[0])
        writer.add_page(first_page)

        for page in template_reader.pages[1:]:
            writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()
    except Exception as error:
        raise PDFGenerationError("Unable to apply the PDF template.") from error


def _safe_filename(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in value).strip("-") or "customer-approval"
