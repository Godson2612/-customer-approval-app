from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

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

NOISE_WORDS = {
    "approval workflow",
    "customer approval",
    "details",
    "health",
    "history",
    "resolution",
    "notes",
    "details health history",
    "account information",
    "account contact information",
    "plant information",
    "current balance",
    "call first",
    "legal name",
    "dwelling type",
    "drop type",
    "drop length",
    "hookup type",
    "node",
    "fiber node",
    "complete job",
    "resolution notes",
    "saved messages",
    "customer signature",
    "technician signature",
    "generate",
}

STOP_WORDS = (
    "dwelling type",
    "drop type",
    "drop length",
    "hookup type",
    "account information",
    "account contact information",
)


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
        "job_number": 0.96 if fields["job_number"] else 0.0,
        "customer_name": 0.94 if fields["customer_name"] else 0.0,
        "service_address": 0.93 if fields["service_address"] else 0.0,
        "city_state_zip": 0.91 if fields["city_state_zip"] else 0.0,
        "phone_number": 0.90 if fields["phone_number"] else 0.0,
        "work_phone_number": 0.78 if fields["work_phone_number"] else 0.0,
        "email": 0.86 if fields["email"] else 0.0,
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
    except Exception as error:
        raise ExtractionError("Unable to read the uploaded screenshot.") from error

    images = []
    images.extend(_prepare_images(base))
    images.extend(_prepare_images(_crop_top_section(base)))
    images.extend(_prepare_images(_crop_contact_section(base)))

    collected: list[str] = []

    for image in images:
        for config in ("--oem 3 --psm 6", "--oem 3 --psm 4", "--oem 3 --psm 11"):
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
                collected.append(text)

    return "\n".join(_dedupe_lines("\n".join(collected)))


def _crop_top_section(image: Image.Image) -> Image.Image:
    width, height = image.size
    top = int(height * 0.08)
    bottom = int(height * 0.46)
    return image.crop((0, top, width, bottom))


def _crop_contact_section(image: Image.Image) -> Image.Image:
    width, height = image.size
    top = int(height * 0.40)
    bottom = int(height * 0.82)
    return image.crop((0, top, width, bottom))


def _prepare_images(image: Image.Image) -> list[Image.Image]:
    images: list[Image.Image] = []

    resized = image
    if image.width < 1800:
        scale = max(2, round(1800 / max(image.width, 1)))
        resized = image.resize((image.width * scale, image.height * scale))

    gray = ImageOps.grayscale(resized)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.9)
    gray = ImageEnhance.Sharpness(gray).enhance(1.9)
    gray = gray.filter(ImageFilter.MedianFilter(size=3))

    inverted = ImageOps.invert(gray)
    inverted = ImageOps.autocontrast(inverted)

    binary_light = gray.point(lambda value: 255 if value > 168 else 0)
    binary_dark = inverted.point(lambda value: 255 if value > 145 else 0)

    images.append(gray)
    images.append(inverted)
    images.append(binary_light)
    images.append(binary_dark)
    return images


def _dedupe_lines(text: str) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        normalized = re.sub(r"\s+", " ", line).strip().lower()
        if normalized in seen:
            continue

        seen.add(normalized)
        results.append(re.sub(r"\s+", " ", line).strip())

    return results


def _parse_customer_screen(text: str) -> dict[str, str]:
    normalized_text = _normalize_text(text)
    lines = _clean_lines(normalized_text)

    job_number = _match(
        normalized_text,
        [
            r"\bNew\s+Install\s*-\s*Job\s*#\s*([A-Z0-9\-]{4,})",
            r"\bJob\s*#\s*([A-Z0-9\-]{4,})",
            r"\bJob\s*(?:Number|No\.?)\s*[:\-]?\s*([A-Z0-9\-]{4,})",
        ],
    )

    email = _match(
        normalized_text,
        [r"\b([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})\b"],
        flags=re.IGNORECASE,
    )

    primary_phone = _extract_labeled_phone(lines, "Primary")
    work_phone = _extract_labeled_phone(lines, "Work")
    if not primary_phone:
        phones = _find_all_phones(normalized_text)
        primary_phone = phones[0] if phones else ""
        if len(phones) > 1 and not work_phone:
            work_phone = phones[1]

    city_state_zip, city_index = _extract_city_state_zip(lines)
    service_address = _extract_address(lines, city_index)
    customer_name = _extract_name(lines, service_address, city_index, job_number)

    return {
        "job_number": _clean_value(job_number),
        "customer_name": _clean_customer_name(customer_name),
        "service_address": _clean_service_address(service_address),
        "city_state_zip": _clean_city_state_zip(city_state_zip),
        "phone_number": primary_phone,
        "work_phone_number": work_phone,
        "email": email,
    }


def _normalize_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = text.replace("|", " ")
    text = text.replace("—", "-")
    text = text.replace("–", "-")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _clean_lines(text: str) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip(" •\t")
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue

        lower = line.lower()
        if lower in NOISE_WORDS:
            continue
        if any(word in lower for word in ("resolution", "notes", "health")) and len(line) < 40:
            continue
        if len(line) == 1:
            continue
        if lower in seen:
            continue

        seen.add(lower)
        results.append(line)

    return results


def _match(text: str, patterns: list[str], *, flags: int = re.IGNORECASE) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip()
    return ""


def _extract_labeled_phone(lines: list[str], label: str) -> str:
    for index, line in enumerate(lines):
        if re.fullmatch(label, line, flags=re.IGNORECASE):
            for candidate in lines[index + 1 : index + 3]:
                phone = _format_phone(candidate)
                if phone:
                    return phone

        inline = re.search(rf"{label}\s*[:\-]?\s*(.+)$", line, re.IGNORECASE)
        if inline:
            phone = _format_phone(inline.group(1))
            if phone:
                return phone
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


def _extract_city_state_zip(lines: list[str]) -> tuple[str, int]:
    for index, line in enumerate(lines):
        if _looks_like_city_state_zip(line):
            return line, index
    return "", -1


def _extract_address(lines: list[str], city_index: int) -> str:
    if city_index > 0:
        for offset in range(1, 3):
            candidate_index = city_index - offset
            if candidate_index < 0:
                break
            candidate = lines[candidate_index].rstrip(",")
            if _looks_like_street(candidate):
                return candidate

    for line in lines:
        candidate = line.rstrip(",")
        if _looks_like_street(candidate):
            return candidate

    return ""


def _extract_name(lines: list[str], service_address: str, city_index: int, job_number: str) -> str:
    job_index = _find_job_index(lines, job_number)

    if job_index >= 0:
        focus_lines = _collect_focus_lines(lines, start=job_index + 1)
        for line in focus_lines:
            if _looks_like_name(line):
                return line

    if city_index > 1:
        for offset in range(2, 5):
            candidate_index = city_index - offset
            if candidate_index < 0:
                break
            candidate = lines[candidate_index]
            if _looks_like_name(candidate):
                return candidate

    if service_address:
        for index, line in enumerate(lines):
            if line.rstrip(",") == service_address and index > 0:
                for offset in range(1, 4):
                    candidate_index = index - offset
                    if candidate_index < 0:
                        break
                    candidate = lines[candidate_index]
                    if _looks_like_name(candidate):
                        return candidate

    for line in lines:
        if _looks_like_name(line):
            return line

    return ""


def _find_job_index(lines: list[str], job_number: str) -> int:
    for index, line in enumerate(lines):
        lower = line.lower()
        if "job #" in lower or lower.startswith("job "):
            return index
        if job_number and job_number in line:
            return index
    return -1


def _collect_focus_lines(lines: list[str], start: int) -> list[str]:
    focused: list[str] = []
    for line in lines[start:]:
        lower = line.lower()
        if any(marker in lower for marker in STOP_WORDS):
            break
        focused.append(line)
    return focused


def _looks_like_name(value: str) -> bool:
    lower = value.lower()
    if lower in NOISE_WORDS:
        return False
    if any(word in lower for word in ("details", "health", "history", "resolution", "notes", "approval")):
        return False
    if "@" in value:
        return False
    if re.search(r"\d", value):
        return False
    if _looks_like_street(value):
        return False
    if _looks_like_city_state_zip(value):
        return False

    words = value.split()
    if not (2 <= len(words) <= 4):
        return False
    if len(value) > 42:
        return False

    alpha_words = [word for word in words if word[:1].isalpha()]
    if not alpha_words:
        return False

    return all(word[:1].isupper() for word in alpha_words)


def _looks_like_street(value: str) -> bool:
    return bool(re.match(r"^\d{1,6}\s+", value)) and not _looks_like_city_state_zip(value)


def _looks_like_city_state_zip(value: str) -> bool:
    return bool(
        re.search(
            r"\b[A-Z][A-Za-z\s\.\-']+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b",
            value,
        )
    )


def _clean_value(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().rstrip(",")


def _clean_customer_name(value: str) -> str:
    value = _clean_value(value)
    if not value:
        return ""
    if any(word in value.lower() for word in ("details", "health", "history", "resolution", "notes")):
        return ""
    return value


def _clean_service_address(value: str) -> str:
    value = _clean_value(value)
    if len(value) < 8:
        return ""
    return value


def _clean_city_state_zip(value: str) -> str:
    return _clean_value(value)
