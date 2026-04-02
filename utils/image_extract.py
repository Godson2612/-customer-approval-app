# utils/image_extract.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None


class ExtractionError(ValueError):
    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


def extract_customer_approval_data(
    *,
    image_path: Path,
    technician_name: str,
    install_date: str,
) -> dict[str, Any]:
    warnings: list[str] = []

    if pytesseract is None:
        return {
            "fields": {
                "job_number": "",
                "customer_name": "",
                "service_address": "",
                "city_state_zip": "",
                "phone_number": "",
                "work_phone_number": "",
                "email": "",
                "installation_date": install_date,
                "technician_name": technician_name.strip(),
            },
            "confidence": {
                "job_number": 0.0,
                "customer_name": 0.0,
                "service_address": 0.0,
                "city_state_zip": 0.0,
                "phone_number": 0.0,
                "work_phone_number": 0.0,
                "email": 0.0,
                "installation_date": 1.0,
                "technician_name": 1.0 if technician_name.strip() else 0.0,
            },
            "warnings": [
                "Automatic text extraction is unavailable in this deployment, so please review and complete the form manually."
            ],
        }

    try:
        with Image.open(image_path) as source_image:
            normalized = ImageOps.exif_transpose(source_image).convert("L")
            text = pytesseract.image_to_string(normalized)
    except Exception as error:
        raise ExtractionError("Unable to read the uploaded screenshot.") from error

    fields = {
        "job_number": _match(text, [r"\bjob(?:\s*(?:#|number|no\.?))?\s*[:\-]?\s*([A-Z0-9\-]{4,})"]),
        "customer_name": _match(text, [r"\bcustomer\s*[:\-]?\s*([A-Z][A-Za-z ,.'-]{2,})"]),
        "service_address": _match(
            text,
            [r"\b(?:service|address)\s*[:\-]?\s*([0-9]{1,6}\s+[A-Za-z0-9 .,'#-]{6,})"],
        ),
        "city_state_zip": _match(text, [r"\b([A-Za-z .'-]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)"]),
        "phone_number": _match(text, [r"(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})"]),
        "work_phone_number": "",
        "email": _match(text, [r"\b([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})"], flags=re.IGNORECASE),
        "installation_date": install_date,
        "technician_name": technician_name.strip(),
    }

    confidence = {key: 0.55 if value else 0.0 for key, value in fields.items()}
    warnings.extend(
        message
        for key, message in (
            ("job_number", "Verify the job number before generating the final PDF."),
            ("customer_name", "Customer name could not be confidently extracted."),
            ("service_address", "Service address could not be confidently extracted."),
            ("city_state_zip", "City, State, ZIP could not be confidently extracted."),
            ("phone_number", "Phone number could not be confidently extracted."),
        )
        if not fields.get(key)
    )

    return {
        "fields": fields,
        "confidence": confidence,
        "warnings": warnings,
    }


def _match(text: str, patterns: list[str], *, flags: int = re.IGNORECASE) -> str:
    normalized_text = " ".join(text.split())
    for pattern in patterns:
        match = re.search(pattern, normalized_text, flags)
        if match:
            return match.group(1).strip()
    return ""
