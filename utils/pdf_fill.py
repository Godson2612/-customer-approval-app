from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


class PDFGenerationError(ValueError):
    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


def generate_customer_approval_pdf(
    *,
    template_path: Path,
    output_dir: Path,
    form_data: dict[str, Any],
    customer_signature_bytes: bytes,
    technician_signature_bytes: bytes,
) -> dict[str, Any]:
    template_path = Path(template_path)
    output_dir = Path(output_dir)

    if not template_path.exists():
        raise PDFGenerationError(f"PDF template file was not found: {template_path}")

    if template_path.suffix.lower() != ".pdf":
        raise PDFGenerationError("PDF template path is invalid.")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as error:
        raise PDFGenerationError("Could not create the PDF output directory.") from error

    try:
        reader = PdfReader(str(template_path))
    except Exception as error:
        raise PDFGenerationError("Unable to open the PDF template.") from error

    if not reader.pages:
        raise PDFGenerationError("The PDF template has no pages.")

    page = reader.pages[0]
    page_width = float(page.mediabox.width)
    page_height = float(page.mediabox.height)

    field_rects = _extract_field_rects(page)

    raw_job_number = _clean_text(form_data.get("job_number", ""))
    job_number = _last_6_digits(raw_job_number)

    service_address = _clean_text(form_data.get("service_address", ""))
    city_state_zip = _clean_text(form_data.get("city_state_zip", ""))
    phone_number = _clean_text(form_data.get("phone_number", ""))
    installation_date = _clean_text(form_data.get("installation_date", ""))
    customer_name = _clean_text(form_data.get("customer_name", ""))
    technician_name = _clean_text(form_data.get("technician_name", ""))

    if not job_number:
        raise PDFGenerationError("Job number is required to generate the PDF.")

    customer_signature_png = _normalize_signature_image(customer_signature_bytes, "customer")
    technician_signature_png = _normalize_signature_image(technician_signature_bytes, "technician")

    overlay_buffer = io.BytesIO()

    try:
        pdf = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))
        pdf.setTitle("Customer Approval")
        pdf.setAuthor("Customer Approval App")
        pdf.setSubject("Temporary Cable Acknowledgment and Installation Consent Form")

        _draw_text_in_rect(pdf, job_number, field_rects["Job Number"])
        _draw_text_in_rect(pdf, service_address, field_rects["Service Address"])
        _draw_text_in_rect(pdf, city_state_zip, field_rects["City State ZIP"])
        _draw_text_in_rect(pdf, phone_number, field_rects["Phone Number"])
        _draw_text_in_rect(pdf, installation_date, field_rects["Date of Installation"])

        # Customer Name appears twice in the PDF:
        # 1) next to "I, ________, acknowledge"
        # 2) on the bottom "Customer Name" line
        customer_name_rects = field_rects["Customer Name"]
        _draw_text_in_rect(pdf, customer_name, customer_name_rects[0])  # top "I, ____"
        _draw_text_in_rect(pdf, customer_name, customer_name_rects[1])  # bottom customer name

        _draw_text_in_rect(pdf, installation_date, field_rects["Date"])
        _draw_text_in_rect(pdf, technician_name, field_rects["Technician Name"])
        _draw_text_in_rect(pdf, installation_date, field_rects["Date_2"])

        _draw_signature_in_rect(
            pdf,
            signature_bytes=customer_signature_png,
            rect=field_rects["Customer Signature"],
        )
        _draw_signature_in_rect(
            pdf,
            signature_bytes=technician_signature_png,
            rect=field_rects["Technician Signature"],
        )

        pdf.save()
        overlay_buffer.seek(0)

    except PDFGenerationError:
        raise
    except Exception as error:
        raise PDFGenerationError("Unable to draw the approval PDF.") from error

    final_bytes = _merge_overlay_and_flatten(
        template_reader=reader,
        overlay_bytes=overlay_buffer.getvalue(),
    )

    file_stem = _safe_filename(
        f"customer-approval-{job_number}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    )
    output_path = output_dir / f"{file_stem}.pdf"

    try:
        output_path.write_bytes(final_bytes)
    except Exception as error:
        raise PDFGenerationError("Unable to save the generated PDF file.") from error

    return {
        "filename": output_path.name,
        "path": str(output_path),
        "bytes": final_bytes,
    }


def _extract_field_rects(page) -> dict[str, Any]:
    annots = page.get("/Annots")
    if not annots:
        raise PDFGenerationError("The PDF template does not contain form annotations.")

    field_rects: dict[str, Any] = {}
    customer_name_rects: list[tuple[float, float, float, float]] = []

    for annot_ref in annots:
        annot = annot_ref.get_object()
        rect = annot.get("/Rect")
        if not rect:
            continue

        rect_tuple = (
            float(rect[0]),
            float(rect[1]),
            float(rect[2]),
            float(rect[3]),
        )

        field_name = annot.get("/T")
        if not field_name and annot.get("/Parent"):
            parent = annot["/Parent"].get_object()
            field_name = parent.get("/T")

        if field_name == "Customer Name":
            customer_name_rects.append(rect_tuple)
        elif field_name:
            field_rects[str(field_name)] = rect_tuple

    if len(customer_name_rects) != 2:
        raise PDFGenerationError("The PDF template is missing one of the Customer Name fields.")

    # Top one first, bottom one second
    customer_name_rects.sort(key=lambda item: item[1], reverse=True)
    field_rects["Customer Name"] = customer_name_rects

    required = [
        "Job Number",
        "Service Address",
        "City State ZIP",
        "Phone Number",
        "Date of Installation",
        "Customer Name",
        "Customer Signature",
        "Date",
        "Technician Name",
        "Technician Signature",
        "Date_2",
    ]

    missing = [name for name in required if name not in field_rects]
    if missing:
        raise PDFGenerationError(f"The PDF template is missing required fields: {', '.join(missing)}")

    return field_rects


def _draw_text_in_rect(
    pdf: canvas.Canvas,
    text: str,
    rect: tuple[float, float, float, float],
    *,
    font_name: str = "Helvetica",
    start_size: float = 10.5,
    min_size: float = 7.0,
    left_padding: float = 2.0,
) -> None:
    cleaned = _clean_text(text)
    if not cleaned:
        return

    x0, y0, x1, y1 = rect
    width = max(0.0, x1 - x0)
    height = max(0.0, y1 - y0)
    usable_width = max(0.0, width - (left_padding * 2))

    font_size = start_size
    while font_size >= min_size and pdf.stringWidth(cleaned, font_name, font_size) > usable_width:
        font_size -= 0.25

    if font_size < min_size:
        font_size = min_size
        cleaned = _ellipsize_text(
            pdf,
            cleaned,
            max_width=usable_width,
            font_name=font_name,
            font_size=font_size,
        )

    text_x = x0 + left_padding
    text_y = y0 + max(1.5, (height - font_size) / 2.0)

    pdf.setFont(font_name, font_size)
    pdf.drawString(text_x, text_y, cleaned)


def _draw_signature_in_rect(
    pdf: canvas.Canvas,
    *,
    signature_bytes: bytes,
    rect: tuple[float, float, float, float],
) -> None:
    x0, y0, x1, y1 = rect
    field_width = max(0.0, x1 - x0)
    field_height = max(0.0, y1 - y0)

    # Make signatures clearly visible while still anchored to the real signature field.
    draw_height = max(field_height * 2.2, 24.0)
    draw_width = field_width
    draw_x = x0
    draw_y = y0 - ((draw_height - field_height) / 2.0)

    try:
        pdf.drawImage(
            ImageReader(io.BytesIO(signature_bytes)),
            draw_x,
            draw_y,
            width=draw_width,
            height=draw_height,
            preserveAspectRatio=True,
            anchor="sw",
            mask="auto",
        )
    except Exception as error:
        raise PDFGenerationError("Unable to render signature image.") from error


def _normalize_signature_image(signature_bytes: bytes, role: str) -> bytes:
    if not signature_bytes:
        raise PDFGenerationError(f"The {role} signature is empty.")

    try:
        with Image.open(io.BytesIO(signature_bytes)) as source:
            normalized = ImageOps.exif_transpose(source).convert("RGBA")

            white_bg = Image.new("RGBA", normalized.size, (255, 255, 255, 255))
            diff = ImageChops.difference(normalized, white_bg)
            bbox = diff.getbbox() or normalized.getbbox()

            if bbox:
                normalized = normalized.crop(bbox)

            max_width = 2400
            max_height = 900
            normalized.thumbnail((max_width, max_height))

            output = io.BytesIO()
            normalized.save(output, format="PNG")
            return output.getvalue()

    except UnidentifiedImageError as error:
        raise PDFGenerationError(f"The {role} signature image is invalid.") from error
    except Exception as error:
        raise PDFGenerationError(f"Unable to process the {role} signature.") from error


def _merge_overlay_and_flatten(*, template_reader: PdfReader, overlay_bytes: bytes) -> bytes:
    try:
        overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
    except Exception as error:
        raise PDFGenerationError("Unable to read the generated PDF overlay.") from error

    if not overlay_reader.pages:
        raise PDFGenerationError("The generated overlay has no pages.")

    try:
        writer = PdfWriter()

        first_page = template_reader.pages[0]
        first_page.merge_page(overlay_reader.pages[0])

        # Remove interactive fields/highlights from final PDF
        if "/Annots" in first_page:
            del first_page[NameObject("/Annots")]

        writer.add_page(first_page)

        for remaining_page in template_reader.pages[1:]:
            if "/Annots" in remaining_page:
                del remaining_page[NameObject("/Annots")]
            writer.add_page(remaining_page)

        if "/AcroForm" in writer._root_object:
            del writer._root_object[NameObject("/AcroForm")]

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    except Exception as error:
        raise PDFGenerationError("Unable to apply the PDF template.") from error


def _last_6_digits(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    return digits[-6:]


def _ellipsize_text(
    pdf: canvas.Canvas,
    text: str,
    *,
    max_width: float,
    font_name: str,
    font_size: float,
) -> str:
    if pdf.stringWidth(text, font_name, font_size) <= max_width:
        return text

    suffix = "..."
    while text and pdf.stringWidth(text + suffix, font_name, font_size) > max_width:
        text = text[:-1]

    return (text + suffix) if text else suffix


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_filename(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in value
    ).strip("-")
    return cleaned or "customer-approval"
