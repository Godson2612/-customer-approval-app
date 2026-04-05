from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter
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
    if not form_data.get("job_number"):
        raise PDFGenerationError("Job number is required to generate the PDF.")

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
        template_reader = PdfReader(str(template_path))
    except Exception as error:
        raise PDFGenerationError("Unable to open the PDF template.") from error

    if not template_reader.pages:
        raise PDFGenerationError("The PDF template has no pages.")

    page = template_reader.pages[0]
    page_width = float(page.mediabox.width)
    page_height = float(page.mediabox.height)

    customer_signature_png = _normalize_signature_image(customer_signature_bytes, "customer")
    technician_signature_png = _normalize_signature_image(technician_signature_bytes, "technician")

    file_stem = _safe_filename(
        f"customer-approval-{form_data.get('job_number', 'document')}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    )
    output_path = output_dir / f"{file_stem}.pdf"

    overlay_buffer = io.BytesIO()

    try:
        pdf = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))
        pdf.setTitle("Customer Approval")
        pdf.setAuthor("Customer Approval App")
        pdf.setSubject("Temporary Cable Acknowledgment and Installation Consent Form")

        # ---- Top form fields ----
        _draw_line_text(pdf, _clean_text(form_data.get("job_number", "")), x=205, y=672, max_width=155)
        _draw_line_text(pdf, _clean_text(form_data.get("service_address", "")), x=272, y=653, max_width=175)
        _draw_line_text(pdf, _clean_text(form_data.get("city_state_zip", "")), x=230, y=634, max_width=205)
        _draw_line_text(pdf, _clean_text(form_data.get("phone_number", "")), x=248, y=615, max_width=188)
        _draw_line_text(pdf, _clean_text(form_data.get("installation_date", "")), x=248, y=544, max_width=210)

        # ---- Acknowledgment line ----
        _draw_line_text(pdf, _clean_text(form_data.get("customer_name", "")), x=130, y=474, max_width=250)

        # ---- Bottom signature section ----
        customer_name = _clean_text(form_data.get("customer_name", ""))
        technician_name = _clean_text(form_data.get("technician_name", ""))
        install_date = _clean_text(form_data.get("installation_date", ""))

        _draw_line_text(pdf, customer_name, x=183, y=157, max_width=205)
        _draw_signature_on_line(
            pdf,
            signature_bytes=customer_signature_png,
            line_x=225,
            line_y=138,
            line_width=180,
            image_height=22,
        )
        _draw_line_text(pdf, install_date, x=95, y=119, max_width=185)

        _draw_line_text(pdf, technician_name, x=196, y=74, max_width=210)
        _draw_signature_on_line(
            pdf,
            signature_bytes=technician_signature_png,
            line_x=235,
            line_y=55,
            line_width=170,
            image_height=22,
        )
        _draw_line_text(pdf, install_date, x=95, y=36, max_width=185)

        pdf.save()
        overlay_buffer.seek(0)

    except PDFGenerationError:
        raise
    except Exception as error:
        raise PDFGenerationError("Unable to draw the approval PDF.") from error

    final_bytes = _merge_with_template(template_reader=template_reader, overlay_bytes=overlay_buffer.getvalue())

    try:
        output_path.write_bytes(final_bytes)
    except Exception as error:
        raise PDFGenerationError("Unable to save the generated PDF file.") from error

    return {
        "filename": output_path.name,
        "path": str(output_path),
        "bytes": final_bytes,
    }


def _draw_line_text(
    pdf: canvas.Canvas,
    text: str,
    *,
    x: float,
    y: float,
    max_width: float,
    font_name: str = "Helvetica",
    start_size: float = 11,
    min_size: float = 7,
) -> None:
    cleaned = _clean_text(text)
    if not cleaned:
        return

    font_size = start_size
    while font_size >= min_size and pdf.stringWidth(cleaned, font_name, font_size) > max_width:
        font_size -= 0.5

    if font_size < min_size:
        font_size = min_size
        cleaned = _ellipsize_text(pdf, cleaned, max_width=max_width, font_name=font_name, font_size=font_size)

    pdf.setFont(font_name, font_size)
    pdf.drawString(x, y, cleaned)


def _draw_signature_on_line(
    pdf: canvas.Canvas,
    *,
    signature_bytes: bytes,
    line_x: float,
    line_y: float,
    line_width: float,
    image_height: float,
) -> None:
    try:
        image = ImageReader(io.BytesIO(signature_bytes))
        pdf.drawImage(
            image,
            line_x,
            line_y + 4,
            width=line_width,
            height=image_height,
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

            # Make solid white background transparent if present
            bg = Image.new("RGBA", normalized.size, (255, 255, 255, 255))
            diff = ImageChops.difference(normalized, bg)
            bbox = diff.getbbox() or normalized.getbbox()

            if bbox:
                normalized = normalized.crop(bbox)

            max_width = 1200
            max_height = 280
            normalized.thumbnail((max_width, max_height))

            output = io.BytesIO()
            normalized.save(output, format="PNG")
            return output.getvalue()

    except UnidentifiedImageError as error:
        raise PDFGenerationError(f"The {role} signature image is invalid.") from error
    except Exception as error:
        raise PDFGenerationError(f"Unable to process the {role} signature.") from error


def _merge_with_template(*, template_reader: PdfReader, overlay_bytes: bytes) -> bytes:
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
        writer.add_page(first_page)

        for remaining_page in template_reader.pages[1:]:
            writer.add_page(remaining_page)

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    except Exception as error:
        raise PDFGenerationError("Unable to apply the PDF template.") from error


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
