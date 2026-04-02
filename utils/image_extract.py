from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None


class ExtractionError(ValueError):
    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


CORE_FIELDS = (
    "job_number",
    "customer_name",
    "service_address",
    "city_state_zip",
    "phone_number",
)

EMPTY_FIELDS = {
    "job_number": "",
    "customer_name": "",
    "service_address": "",
    "city_state_zip": "",
    "phone_number": "",
    "work_phone_number": "",
    "email": "",
}


def extract_customer_approval_data(
    *,
    image_path: Path,
    technician_name: str,
    install_date: str,
) -> dict[str, Any]:
    if not image_path.exists():
        raise ExtractionError("The uploaded screenshot could not be found.")

    if not _ocr_available():
        return _manual_result(
            technician_name=technician_name,
            install_date=install_date,
            warning=(
                "Automatic text extraction is unavailable in this deployment, "
                "so please review and complete the form manually."
            ),
        )

    raw_text = _read_text(image_path)
    if not raw_text.strip():
        return _manual_result(
            technician_name=technician_name,
            install_date=install_date,
            warning=(
                "No readable text was extracted from the screenshot. "
                "Please review and complete the form manually."
            ),
        )

    parsed = _parse_customer_screen(raw_text)

    fields = {
        "job_number": parsed.get("job_number", ""),
        "customer_name": parsed.get("customer_name", ""),
        "service_address": parsed.get("service_address", ""),
        "city_state_zip": parsed.get("city_state_zip", ""),
        "phone_number": parsed.get("phone_number", ""),
        "work_phone_number": parsed.get("work_phone_number", ""),
        "email": parsed.get("email", ""),
        "installation_date": install_date,
        "technician_name": technician_name.strip(),
    }

    confidence = {
        "job_number": 0.92 if fields["job_number"] else 0.0,
        "customer_name": 0.82 if fields["customer_name"] else 0.0,
        "service_address": 0.84 if fields["service_address"] else 0.0,
        "city_state_zip": 0.86 if fields["city_state_zip"] else 0.0,
        "phone_number": 0.84 if fields["phone_number"] else 0.0,
        "work_phone_number": 0.76 if fields["work_phone_number"] else 0.0,
        "email": 0.78 if fields["email"] else 0.0,
        "installation_date": 1.0 if install_date else 0.0,
        "technician_name": 1.0 if technician_name.strip() else 0.0,
    }

    warnings: list[str] = []
    missing_messages = {
        "job_number": "Verify the job number before generating the final PDF.",
        "customer_name": "Customer name could not be confidently extracted.",
        "service_address": "Service address could not be confidently extracted.",
        "city_state_zip": "City, State, ZIP could not be confidently extracted.",
        "phone_number": "Phone number could not be confidently extracted.",
    }
    for key in CORE_FIELDS:
        if not fields.get(key):
            warnings.append(missing_messages[key])

    return {
        "fields": fields,
        "confidence": confidence,
        "warnings": warnings,
    }


def _manual_result(*, technician_name: str, install_date: str, warning: str) -> dict[str, Any]:
    fields = {
        **EMPTY_FIELDS,
        "installation_date": install_date,
        "technician_name": technician_name.strip(),
    }
    confidence = {
        "job_number": 0.0,
        "customer_name": 0.0,
        "service_address": 0.0,
        "city_state_zip": 0.0,
        "phone_number": 0.0,
        "work_phone_number": 0.0,
        "email": 0.0,
        "installation_date": 1.0 if install_date else 0.0,
        "technician_name": 1.0 if technician_name.strip() else 0.0,
    }
    return {
        "fields": fields,
        "confidence": confidence,
        "warnings": [warning],
    }


def _ocr_available() -> bool:
    if pytesseract is None:
        return False

    configured_cmd = os.getenv("OCR_TESSERACT_CMD", "").strip()
    if configured_cmd:
        pytesseract.pytesseract.tesseract_cmd = configured_cmd
        return Path(configured_cmd).exists()

    executable = shutil.which("tesseract")
    if executable:
        pytesseract.pytesseract.tesseract_cmd = executable
        return True

    return False


def _read_text(image_path: Path) -> str:
    try:
        with Image.open(image_path) as source_image:
            base = ImageOps.exif_transpose(source_image).convert("RGB")
    except Exception as error:  # pragma: no cover
        raise ExtractionError("Unable to read the uploaded screenshot.") from error

    prepared_images = _prepare_images(base)

    texts: list[str] = []
    for image in prepared_images:
        for config in ("--oem 3 --psm 6", "--oem 3 --psm 11"):
            try:
                text = pytesseract.image_to_string(image, config=config)
            except pytesseract.TesseractNotFoundError as error:  # type: ignore[attr-defined]
                raise ExtractionError(
                    "Automatic text extraction is unavailable in this deployment, "
                    "so please review and complete the form manually."
                ) from error
            except Exception:
                continue
            if text and text.strip():
                texts.append(text)

    return "\n".join(texts)


def _prepare_images(image: Image.Image) -> list[Image.Image]:
    images: list[Image.Image] = []

    resized = image
    if image.width < 1400:
        scale = max(2, round(1400 / max(image.width, 1)))
        resized = image.resize((image.width * scale, image.height * scale))

    gray = ImageOps.grayscale(resized)
    gray = ImageOps.autocontrast(gray)
    gray = gray.filter(ImageFilter.SHARPEN)

    binary = gray.point(lambda value: 255 if value > 165 else 0)

    images.append(gray)
    images.append(binary)
    return images


def _parse_customer_screen(text: str) -> dict[str, str]:
    normalized_text = _normalize_text(text)
    lines = _clean_lines(normalized_text)

    job_number = _match(
        normalized_text,
        [
            r"\bJob\s*#\s*([A-Z0-9\-]{4,})",
            r"\bJob\s*(?:Number|No\.?)\s*[:\-]?\s*([A-Z0-9\-]{4,})",
        ],
    )

    email = _match(
        normalized_text,
        [r"\b([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})\b"],
        flags=re.IGNORECASE,
    )

    primary_phone = _extract_labeled_value(lines, "Primary", kind="phone")
    work_phone = _extract_labeled_value(lines, "Work", kind="phone")
    if not primary_phone:
        phones = _find_all_phones(normalized_text)
        primary_phone = phones[0] if phones else ""
        if len(phones) > 1 and not work_phone:
            work_phone = phones[1]

    customer_name = _extract_name(lines)
    service_address, city_state_zip = _extract_address(lines)

    return {
        "job_number": job_number,
        "customer_name": customer_name,
        "service_address": service_address,
        "city_state_zip": city_state_zip,
        "phone_number": primary_phone,
        "work_phone_number": work_phone,
        "email": email,
    }


def _normalize_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _clean_lines(text: str) -> list[str]:
    return [line.strip(" •\t") for line in text.splitlines() if line.strip()]


def _match(text: str, patterns: list[str], *, flags: int = re.IGNORECASE) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip()
    return ""


def _extract_labeled_value(lines: list[str], label: str, *, kind: str) -> str:
    for index, line in enumerate(lines):
        if re.fullmatch(label, line, flags=re.IGNORECASE):
            candidate = _first_candidate(lines[index + 1 : index + 3], kind=kind)
            if candidate:
                return candidate

        inline = re.search(rf"{label}\s*[:\-]?\s*(.+)$", line, re.IGNORECASE)
        if inline:
            candidate = inline.group(1).strip()
            if kind == "phone":
                candidate = _format_phone(candidate)
            return candidate
    return ""


def _first_candidate(lines: list[str], *, kind: str) -> str:
    for line in lines:
        if kind == "phone":
            phone = _format_phone(line)
            if phone:
                return phone
        elif kind == "email":
            email = _match(line, [r"\b([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})\b"], flags=re.IGNORECASE)
            if email:
                return email
        else:
            if line:
                return line
    return ""


def _find_all_phones(text: str) -> list[str]:
    raw = re.findall(r"(\+?1?[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4})", text)
    seen: list[str] = []
    for item in raw:
        phone = _format_phone(item)
        if phone and phone not in seen:
            seen.append(phone)
    return seen


def _format_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _extract_name(lines: list[str]) -> str:
    blacklist = {
        "approval workflow",
        "customer approval",
        "details",
        "health",
        "history",
        "account information",
        "account contact information",
        "plant information",
        "active",
        "job details",
        "review details",
    }

    for line in lines:
        lower = line.lower()
        if lower in blacklist:
            continue
        if "job#" in lower or lower.startswith("job "):
            continue
        if re.search(r"\d", line):
            continue
        if "@" in line:
            continue
        words = line.split()
        if not (2 <= len(words) <= 4):
            continue
        if len(line) > 42:
            continue
        if all(word[:1].isupper() for word in words if word[:1].isalpha()):
            return line
    return ""


def _extract_address(lines: list[str]) -> tuple[str, str]:
    street = ""
    city_state_zip = ""

    for index, line in enumerate(lines):
        if _looks_like_street(line):
            street = line.rstrip(",")
            if index + 1 < len(lines) and _looks_like_city_state_zip(lines[index + 1]):
                city_state_zip = lines[index + 1]
            break

    return street, city_state_zip


def _looks_like_street(value: str) -> bool:
    return bool(re.search(r"^\d{1,6}\s+.+", value)) and not _looks_like_city_state_zip(value)


def _looks_like_city_state_zip(value: str) -> bool:
    return bool(
        re.search(r"\b[A-Z][A-Za-z\s\.\-']+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", value)
    )
