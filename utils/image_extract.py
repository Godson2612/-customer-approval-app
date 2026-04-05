from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI


class ExtractionError(ValueError):
    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "job_number": {"type": "string"},
        "customer_name": {"type": "string"},
        "service_address": {"type": "string"},
        "city_state_zip": {"type": "string"},
        "phone_number": {"type": "string"},
        "work_phone_number": {"type": "string"},
        "email": {"type": "string"},
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "job_number": {"type": "number"},
                "customer_name": {"type": "number"},
                "service_address": {"type": "number"},
                "city_state_zip": {"type": "number"},
                "phone_number": {"type": "number"},
                "work_phone_number": {"type": "number"},
                "email": {"type": "number"},
            },
            "required": [
                "job_number",
                "customer_name",
                "service_address",
                "city_state_zip",
                "phone_number",
                "work_phone_number",
                "email",
            ],
        },
    },
    "required": [
        "job_number",
        "customer_name",
        "service_address",
        "city_state_zip",
        "phone_number",
        "work_phone_number",
        "email",
        "warnings",
        "confidence",
    ],
}

SYSTEM_INSTRUCTIONS = """
You extract customer approval data from cable-installation job screenshots.

The screenshots usually contain:
- Job number near the top center
- Customer full name near the top left under the tabs
- Street address on the next line
- City, State ZIP on the next line
- Primary and work phone numbers in Account Contact Information
- Email in the same section

Rules:
- Extract only what is clearly visible in the screenshot
- Do not invent data
- If a field is missing or uncertain, return an empty string
- Return service_address as street only
- Return city_state_zip as city, state ZIP only
- warnings should mention unclear or missing fields
- confidence values must be between 0.0 and 1.0
"""


def extract_customer_approval_data(
    *,
    image_path: Path,
    technician_name: str,
    install_date: str,
) -> dict[str, Any]:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

    if not api_key:
        raise ExtractionError("OPENAI_API_KEY is not configured on the server.")

    if not image_path.exists():
        raise ExtractionError("The uploaded screenshot could not be found.")

    data_url = _image_path_to_data_url(image_path)

    try:
        client = OpenAI(api_key=api_key)

        response = client.responses.create(
            model=model,
            instructions=SYSTEM_INSTRUCTIONS,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Extract the customer approval fields from this screenshot and return the schema only.",
                        },
                        {
                            "type": "input_image",
                            "image_url": data_url,
                            "detail": "high",
                        },
                    ],
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "customer_approval_extraction",
                    "schema": SCHEMA,
                    "strict": True,
                }
            },
            max_output_tokens=800,
        )

        raw_text = getattr(response, "output_text", "") or ""
        if not raw_text.strip():
            raise ExtractionError("OpenAI returned an empty extraction response.")

        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            raise ExtractionError("OpenAI returned an invalid extraction response.")

    except ExtractionError:
        raise
    except Exception as error:
        raise ExtractionError(f"OpenAI extraction failed: {str(error)}") from error

    fields = {
        "job_number": _clean_job_number(payload.get("job_number", "")),
        "customer_name": _clean_name(payload.get("customer_name", "")),
        "service_address": _clean_street(payload.get("service_address", "")),
        "city_state_zip": _clean_city_state_zip(payload.get("city_state_zip", "")),
        "phone_number": _clean_phone(payload.get("phone_number", "")),
        "work_phone_number": _clean_phone(payload.get("work_phone_number", "")),
        "email": _clean_email(payload.get("email", "")),
        "installation_date": install_date,
        "technician_name": technician_name.strip(),
    }

    _split_combined_address(fields)

    confidence_payload = payload.get("confidence", {}) if isinstance(payload.get("confidence"), dict) else {}
    confidence = {
        "job_number": _clamp_confidence(confidence_payload.get("job_number"), fields["job_number"]),
        "customer_name": _clamp_confidence(confidence_payload.get("customer_name"), fields["customer_name"]),
        "service_address": _clamp_confidence(confidence_payload.get("service_address"), fields["service_address"]),
        "city_state_zip": _clamp_confidence(confidence_payload.get("city_state_zip"), fields["city_state_zip"]),
        "phone_number": _clamp_confidence(confidence_payload.get("phone_number"), fields["phone_number"]),
        "work_phone_number": _clamp_confidence(confidence_payload.get("work_phone_number"), fields["work_phone_number"]),
        "email": _clamp_confidence(confidence_payload.get("email"), fields["email"]),
        "installation_date": 1.0 if install_date else 0.0,
        "technician_name": 1.0 if technician_name.strip() else 0.0,
    }

    warnings = []
    raw_warnings = payload.get("warnings", [])
    if isinstance(raw_warnings, list):
        warnings.extend(str(item).strip() for item in raw_warnings if str(item).strip())

    required_messages = {
        "job_number": "Verify the job number before generating the final PDF.",
        "customer_name": "Customer name could not be confidently extracted.",
        "service_address": "Service address could not be confidently extracted.",
        "city_state_zip": "City, State, ZIP could not be confidently extracted.",
        "phone_number": "Primary phone number could not be confidently extracted.",
    }

    for key, message in required_messages.items():
        if not fields.get(key):
            warnings.append(message)

    warnings = _dedupe_list(warnings)

    return {
        "fields": fields,
        "confidence": confidence,
        "warnings": warnings,
    }


def _image_path_to_data_url(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type:
        mime_type = "image/png"

    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _clean_job_number(value: str) -> str:
    value = _clean_text(value)
    match = re.search(r"([A-Z0-9\\-]{4,})", value, flags=re.IGNORECASE)
    return match.group(1) if match else value


def _clean_name(value: str) -> str:
    value = _clean_text(value)
    if "@" in value:
        return ""
    return value


def _clean_street(value: str) -> str:
    value = _clean_text(value)
    return value.rstrip(",")


def _clean_city_state_zip(value: str) -> str:
    value = _clean_text(value)
    match = re.search(r"([A-Za-z .'\-]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)", value)
    return match.group(1) if match else value


def _clean_phone(value: str) -> str:
    value = _clean_text(value)
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return value


def _clean_email(value: str) -> str:
    value = _clean_text(value)
    match = re.search(r"([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", value, flags=re.IGNORECASE)
    return match.group(1) if match else value


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _split_combined_address(fields: dict[str, str]) -> None:
    combined = fields.get("service_address", "")
    if not combined:
        return

    match = re.search(r"(.+?),\s*([A-Za-z .'\-]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)$", combined)
    if match:
        street = match.group(1).strip()
        city_state_zip = match.group(2).strip()
        fields["service_address"] = street
        if not fields.get("city_state_zip"):
            fields["city_state_zip"] = city_state_zip


def _clamp_confidence(value: Any, field_value: str) -> float:
    try:
        number = float(value)
        if number < 0:
            return 0.0
        if number > 1:
            return 1.0
        return number
    except Exception:
        return 0.85 if field_value else 0.0


def _dedupe_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value.strip())
    return result
