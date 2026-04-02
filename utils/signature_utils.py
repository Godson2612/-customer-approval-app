# utils/signature_utils.py
from __future__ import annotations

import base64
import re


class SignatureValidationError(ValueError):
    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


DATA_URL_PATTERN = re.compile(r"^data:image\/png;base64,(?P<data>[A-Za-z0-9+/=\s]+)$")


def decode_signature_data_url(data_url: str) -> bytes:
    if not isinstance(data_url, str) or not data_url.strip():
        raise SignatureValidationError("A signature is required.")

    match = DATA_URL_PATTERN.match(data_url.strip())
    if not match:
        raise SignatureValidationError("Signature data must be a PNG image.")

    try:
        return base64.b64decode(match.group("data"), validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise SignatureValidationError("Signature data is invalid.") from error
