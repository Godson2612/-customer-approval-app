from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from flask import (
    Flask,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from models import ApprovalRepository
from utils.image_extract import ExtractionError, extract_customer_approval_data
from utils.pdf_fill import PDFGenerationError, generate_customer_approval_pdf
from utils.signature_utils import SignatureValidationError, decode_signature_data_url


BASE_DIR = Path(__file__).resolve().parent
STORAGE_ROOT = Path(os.getenv("APP_STORAGE_DIR", str(BASE_DIR))).resolve()

INSTANCE_DIR = STORAGE_ROOT / "instance"
DATA_DIR = STORAGE_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
PDF_DIR = DATA_DIR / "generated"
TEMPLATE_DIR = BASE_DIR / "templates"

MAX_CONTENT_LENGTH = 12 * 1024 * 1024

EPON_REMOTE_PAGE_URL = "https://techops.cuicable.com/index.php/epon-additional-billing/"
EPON_REMOTE_ACTION_URL = "https://techops.cuicable.com/index.php/epon-additional-billing/?wpforms_form_id=22345"
EPON_FORM_ID = "22345"
EPON_PAGE_ID = "22351"


def create_app() -> Flask:
    app = Flask(__name__, instance_path=str(INSTANCE_DIR), instance_relative_config=True)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # type: ignore[assignment]

    default_db_url = f"sqlite:///{(INSTANCE_DIR / 'app.db').as_posix()}"

    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "change-me-in-production"),
        MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
        DATABASE_URL=os.getenv("DATABASE_URL", default_db_url),
        PDF_TEMPLATE_PATH=os.getenv(
            "PDF_TEMPLATE_PATH",
            str(BASE_DIR / "assets" / "customer_approval_template.pdf"),
        ),
        KEEP_SCREENSHOTS=os.getenv("KEEP_SCREENSHOTS", "false").lower() == "true",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
        PREFERRED_URL_SCHEME="https",
        APP_NAME="Operations Toolkit",
        DEFAULT_TECHNICIAN_NAME=os.getenv("DEFAULT_TECHNICIAN_NAME", "").strip(),
        DEFAULT_TECH_NUMBER=os.getenv("DEFAULT_TECH_NUMBER", "").strip(),
        DEFAULT_SUPERVISOR=os.getenv("DEFAULT_SUPERVISOR", "").strip(),
        DEFAULT_EPON_LOCATION=os.getenv("DEFAULT_EPON_LOCATION", "West Palm Beach").strip(),
    )

    _ensure_directories()
    _configure_logging(app)

    app.logger.info("Storage root: %s", STORAGE_ROOT)
    app.logger.info("PDF output dir: %s", PDF_DIR)
    app.logger.info("PDF template path: %s", app.config["PDF_TEMPLATE_PATH"])
    app.logger.info("PDF template exists: %s", Path(app.config["PDF_TEMPLATE_PATH"]).exists())

    repository = ApprovalRepository(app.config["DATABASE_URL"])
    repository.initialize()
    app.config["APPROVAL_REPOSITORY"] = repository

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "app_name": app.config["APP_NAME"],
            "telegram_bot_username": os.getenv("TELEGRAM_BOT_USERNAME", ""),
        }

    @app.before_request
    def ensure_csrf_token() -> None:
        if "csrf_token" not in session:
            session["csrf_token"] = _generate_csrf_token()

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
            if not token or token != session["csrf_token"]:
                abort(400, description="Invalid CSRF token.")

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_upload(_: RequestEntityTooLarge) -> tuple[dict[str, str], int]:
        return {"error": "The uploaded file exceeds the 12 MB limit."}, 413

    @app.errorhandler(400)
    def handle_bad_request(error: Exception) -> tuple[dict[str, str], int]:
        description = getattr(error, "description", "Bad request.")
        return {"error": str(description)}, 400

    @app.errorhandler(404)
    def handle_not_found(_: Exception) -> tuple[dict[str, str], int]:
        return {"error": "Document not found."}, 404

    @app.errorhandler(500)
    def handle_server_error(_: Exception) -> tuple[dict[str, str], int]:
        return {"error": "An unexpected error occurred."}, 500

    @app.get("/")
    def home() -> str:
        return render_template(
            "home.html",
            csrf_token=session["csrf_token"],
            default_supervisor=app.config["DEFAULT_SUPERVISOR"],
            default_tech_number=app.config["DEFAULT_TECH_NUMBER"],
            default_location=app.config["DEFAULT_EPON_LOCATION"],
        )

    @app.get("/customer-approval")
    def customer_approval() -> str:
        technician_name = app.config["DEFAULT_TECHNICIAN_NAME"]
        return render_template(
            "customer_approval.html",
            csrf_token=session["csrf_token"],
            technician_name=technician_name,
            today=datetime.now().strftime("%m/%d/%Y"),
        )

    @app.get("/epon-additional-billing")
    def epon_additional_billing() -> str:
        return render_template(
            "epon_additional_billing.html",
            csrf_token=session["csrf_token"],
            today=datetime.now().strftime("%m/%d/%Y"),
            default_supervisor=app.config["DEFAULT_SUPERVISOR"],
            default_tech_number=app.config["DEFAULT_TECH_NUMBER"],
            default_location=app.config["DEFAULT_EPON_LOCATION"],
        )

    @app.post("/api/customer-approval/extract")
    def extract_customer_approval() -> tuple[Any, int]:
        upload = request.files.get("screenshot")
        technician_name = (request.form.get("technician_name") or "").strip()
        keep_screenshot = _truthy(request.form.get("keep_screenshot"))

        if not upload or not upload.filename:
            return jsonify({"error": "A screenshot image is required."}), 400

        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        safe_name = f"screenshot_{timestamp}.png"

        try:
            stored_path = _save_uploaded_image(upload, safe_name)
        except UnidentifiedImageError:
            return jsonify(
                {
                    "error": "The selected file could not be processed. Please upload a valid screenshot image."
                }
            ), 422
        except Exception:
            app.logger.exception("Image normalization failed")
            return jsonify(
                {
                    "error": "The selected file could not be processed. Please upload a valid screenshot image."
                }
            ), 422

        try:
            result = extract_customer_approval_data(
                image_path=stored_path,
                technician_name=technician_name,
                install_date=datetime.now().strftime("%m/%d/%Y"),
            )

            screenshot_filename = safe_name if (keep_screenshot or app.config["KEEP_SCREENSHOTS"]) else None

            payload = {
                "fields": result["fields"],
                "confidence": result["confidence"],
                "warnings": result["warnings"],
                "meta": {
                    "screenshot_filename": screenshot_filename,
                    "original_filename": safe_name,
                },
            }

            if screenshot_filename is None:
                stored_path.unlink(missing_ok=True)

            return jsonify(payload), 200

        except ExtractionError as error:
            app.logger.warning("Image extraction failed: %s", error.public_message)

            payload = _build_manual_extract_payload(
                technician_name=technician_name,
                today=datetime.now().strftime("%m/%d/%Y"),
                screenshot_filename=safe_name if (keep_screenshot or app.config["KEEP_SCREENSHOTS"]) else None,
                warning=error.public_message,
            )

            if payload["meta"]["screenshot_filename"] is None:
                stored_path.unlink(missing_ok=True)

            return jsonify(payload), 200

        except Exception:
            app.logger.exception("Unhandled extraction failure")

            payload = _build_manual_extract_payload(
                technician_name=technician_name,
                today=datetime.now().strftime("%m/%d/%Y"),
                screenshot_filename=safe_name if (keep_screenshot or app.config["KEEP_SCREENSHOTS"]) else None,
                warning="Automatic extraction failed, so please review and complete the form manually.",
            )

            if payload["meta"]["screenshot_filename"] is None:
                stored_path.unlink(missing_ok=True)

            return jsonify(payload), 200

    @app.post("/api/customer-approval/generate")
    def generate_customer_approval() -> tuple[Any, int]:
        repository: ApprovalRepository = app.config["APPROVAL_REPOSITORY"]

        payload = request.get_json(silent=True) or {}
        fields = payload.get("fields") or {}
        screenshot_filename = payload.get("screenshot_filename")
        delete_screenshot_after = _truthy(payload.get("delete_screenshot_after"), default=True)
        extraction_snapshot = payload.get("extraction_json") or {}

        validation_errors = _validate_required_fields(fields)
        if validation_errors:
            return jsonify({"error": "Please complete all required fields.", "fields": validation_errors}), 400

        try:
            customer_signature_bytes = decode_signature_data_url(fields["customer_signature"])
            technician_signature_bytes = decode_signature_data_url(fields["technician_signature"])
        except SignatureValidationError as error:
            app.logger.warning("Signature validation failed: %s", error.public_message)
            return jsonify({"error": error.public_message}), 400

        try:
            document = generate_customer_approval_pdf(
                template_path=Path(app.config["PDF_TEMPLATE_PATH"]),
                output_dir=PDF_DIR,
                form_data=fields,
                customer_signature_bytes=customer_signature_bytes,
                technician_signature_bytes=technician_signature_bytes,
            )
            app.logger.info(
                "PDF generated successfully: filename=%s path=%s",
                document["filename"],
                document["path"],
            )
        except PDFGenerationError as error:
            app.logger.warning("PDF generation failed: %s", error.public_message)
            return jsonify({"error": error.public_message}), 422
        except Exception:
            app.logger.exception("Unhandled PDF generation failure")
            return jsonify({"error": "Unable to generate the approval document."}), 500

        try:
            approval_id = repository.create_approval(
                technician_name=fields["technician_name"].strip(),
                job_number=fields["job_number"].strip(),
                customer_name=fields["customer_name"].strip(),
                service_address=fields["service_address"].strip(),
                city_state_zip=fields["city_state_zip"].strip(),
                phone_number=fields["phone_number"].strip(),
                pdf_filename=document["filename"],
                original_screenshot_filename=screenshot_filename,
                extraction_json=json.dumps(extraction_snapshot, ensure_ascii=True),
                status="generated",
            )
        except Exception:
            app.logger.exception("Approval repository save failed")
            return jsonify({"error": "PDF was generated, but saving the document record failed."}), 500

        if screenshot_filename and delete_screenshot_after and not app.config["KEEP_SCREENSHOTS"]:
            screenshot_path = UPLOAD_DIR / screenshot_filename
            screenshot_path.unlink(missing_ok=True)

        return (
            jsonify(
                {
                    "message": "Document generated successfully",
                    "approval_id": approval_id,
                    "download_url": f"/approvals/{approval_id}/download",
                    "share": {
                        "filename": document["filename"],
                        "title": "Customer Approval",
                    },
                }
            ),
            201,
        )

    @app.post("/api/epon-additional-billing/extract")
    def extract_epon_additional_billing() -> tuple[Any, int]:
        upload = request.files.get("screenshot")
        tech_number = (request.form.get("tech_number") or "").strip()
        supervisor = (request.form.get("supervisor") or "").strip()
        location = (request.form.get("location") or app.config["DEFAULT_EPON_LOCATION"]).strip()
        keep_screenshot = _truthy(request.form.get("keep_screenshot"))

        if not upload or not upload.filename:
            return jsonify({"error": "A screenshot image is required."}), 400

        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        safe_name = f"epon_{timestamp}.png"

        try:
            stored_path = _save_uploaded_image(upload, safe_name)
        except UnidentifiedImageError:
            return jsonify(
                {
                    "error": "The selected file could not be processed. Please upload a valid screenshot image."
                }
            ), 422
        except Exception:
            app.logger.exception("EPON image normalization failed")
            return jsonify(
                {
                    "error": "The selected file could not be processed. Please upload a valid screenshot image."
                }
            ), 422

        try:
            result = extract_customer_approval_data(
                image_path=stored_path,
                technician_name=tech_number,
                install_date=datetime.now().strftime("%m/%d/%Y"),
            )
            screenshot_filename = safe_name if (keep_screenshot or app.config["KEEP_SCREENSHOTS"]) else None

            fields, confidence, warnings = _map_customer_extract_to_epon(
                result=result,
                tech_number=tech_number,
                supervisor=supervisor,
                location=location,
                today=datetime.now().strftime("%m/%d/%Y"),
            )

            payload = {
                "fields": fields,
                "confidence": confidence,
                "warnings": warnings,
                "meta": {
                    "screenshot_filename": screenshot_filename,
                    "original_filename": safe_name,
                },
            }

            if screenshot_filename is None:
                stored_path.unlink(missing_ok=True)

            return jsonify(payload), 200

        except ExtractionError as error:
            app.logger.warning("EPON image extraction failed: %s", error.public_message)

            payload = _build_manual_epon_payload(
                today=datetime.now().strftime("%m/%d/%Y"),
                tech_number=tech_number,
                supervisor=supervisor,
                location=location,
                screenshot_filename=safe_name if (keep_screenshot or app.config["KEEP_SCREENSHOTS"]) else None,
                warning=error.public_message,
            )

            if payload["meta"]["screenshot_filename"] is None:
                stored_path.unlink(missing_ok=True)

            return jsonify(payload), 200

        except Exception:
            app.logger.exception("Unhandled EPON extraction failure")

            payload = _build_manual_epon_payload(
                today=datetime.now().strftime("%m/%d/%Y"),
                tech_number=tech_number,
                supervisor=supervisor,
                location=location,
                screenshot_filename=safe_name if (keep_screenshot or app.config["KEEP_SCREENSHOTS"]) else None,
                warning="Automatic extraction failed, so please review and complete the EPON form manually.",
            )

            if payload["meta"]["screenshot_filename"] is None:
                stored_path.unlink(missing_ok=True)

            return jsonify(payload), 200

    @app.post("/api/epon-additional-billing/submit")
    def submit_epon_additional_billing() -> tuple[Any, int]:
        payload = request.get_json(silent=True) or {}
        fields = payload.get("fields") or {}
        screenshot_filename = payload.get("screenshot_filename")
        delete_screenshot_after = _truthy(payload.get("delete_screenshot_after"), default=True)

        validation_errors = _validate_epon_fields(fields)
        if validation_errors:
            return jsonify({"error": "Please complete all required EPON fields.", "fields": validation_errors}), 400

        nap_file = None
        onu_file = None
        screenshot_path = UPLOAD_DIR / screenshot_filename if screenshot_filename else None

        if screenshot_path and screenshot_path.exists():
            if fields.get("billing_type") == "RR8":
                nap_file = screenshot_path
            elif fields.get("billing_type") == "RS3":
                onu_file = screenshot_path

        try:
            external = _submit_epon_to_remote(fields, nap_file=nap_file, onu_file=onu_file)
        except Exception as error:
            app.logger.exception("EPON submit failed")
            return jsonify({"error": str(error)}), 502

        if screenshot_filename and delete_screenshot_after and not app.config["KEEP_SCREENSHOTS"]:
            screenshot_path = UPLOAD_DIR / screenshot_filename
            screenshot_path.unlink(missing_ok=True)

        return (
            jsonify(
                {
                    "message": "EPON billing submitted successfully.",
                    "external_status": external["status_code"],
                    "external_url": EPON_REMOTE_PAGE_URL,
                }
            ),
            201,
        )

    @app.get("/approvals/<int:approval_id>/download")
    def download_approval(approval_id: int):
        repository: ApprovalRepository = app.config["APPROVAL_REPOSITORY"]
        approval = repository.get_approval(approval_id)
        if not approval:
            abort(404)

        file_path = PDF_DIR / approval["pdf_filename"]
        if not file_path.exists():
            app.logger.warning("Requested PDF does not exist on disk: %s", file_path)
            abort(404)

        return send_file(
            file_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=approval["pdf_filename"],
            max_age=0,
        )

    return app


def _save_uploaded_image(upload, safe_name: str) -> Path:
    stored_path = UPLOAD_DIR / safe_name
    upload.stream.seek(0)
    with Image.open(upload.stream) as source_image:
        normalized = ImageOps.exif_transpose(source_image).convert("RGB")
        normalized.save(stored_path, format="PNG", optimize=True)
    return stored_path


def _map_customer_extract_to_epon(
    *,
    result: dict[str, Any],
    tech_number: str,
    supervisor: str,
    location: str,
    today: str,
) -> tuple[dict[str, Any], dict[str, float], list[str]]:
    extracted = result.get("fields") or {}
    confidence = result.get("confidence") or {}
    warnings = list(result.get("warnings") or [])

    full_address = (extracted.get("service_address") or "").strip()
    city_state_zip = (extracted.get("city_state_zip") or "").strip()
    merged_address = ", ".join(part for part in [full_address, city_state_zip] if part)

    split = _split_city_state_zip(city_state_zip)

    fields = {
        "billing_date": today,
        "location": location,
        "tech_number": tech_number,
        "supervisor": supervisor,
        "customer_name": (extracted.get("customer_name") or "").strip(),
        "customer_address": full_address,
        "address_line_2": "",
        "city": split["city"],
        "state": split["state"],
        "postal": split["postal"],
        "account_number": "",
        "job_number": (extracted.get("job_number") or "").strip(),
        "primary_phone": (extracted.get("phone_number") or "").strip(),
        "billing_type": "",
        "rr8_quantity": "",
        "rs3_quantity": "",
        "merged_address": merged_address,
    }

    epon_confidence = {
        "billing_date": 1.0 if today else 0.0,
        "location": 1.0 if location else 0.0,
        "tech_number": 1.0 if tech_number else 0.0,
        "supervisor": 1.0 if supervisor else 0.0,
        "customer_name": float(confidence.get("customer_name") or 0.0),
        "customer_address": float(confidence.get("service_address") or 0.0),
        "city": float(confidence.get("city_state_zip") or 0.0),
        "state": float(confidence.get("city_state_zip") or 0.0),
        "postal": float(confidence.get("city_state_zip") or 0.0),
        "account_number": 0.0,
        "job_number": float(confidence.get("job_number") or 0.0),
        "primary_phone": float(confidence.get("phone_number") or 0.0),
        "billing_type": 0.0,
        "rr8_quantity": 0.0,
        "rs3_quantity": 0.0,
    }

    warnings.extend(
        [
            "Review Account Number carefully. It usually needs to be entered manually for EPON.",
            "Choose RR8 or RS3 and confirm the quantity before submitting.",
            "The uploaded screenshot will also be used as the required photo attachment for the selected billing type.",
        ]
    )

    if supervisor:
        warnings.append("Supervisor was loaded from saved preferences.")
    else:
        warnings.append("Add your supervisor in Home preferences so EPON is prefilled next time.")

    return fields, epon_confidence, warnings


def _build_manual_extract_payload(
    *,
    technician_name: str,
    today: str,
    screenshot_filename: str | None,
    warning: str,
) -> dict[str, Any]:
    return {
        "fields": {
            "job_number": "",
            "customer_name": "",
            "service_address": "",
            "city_state_zip": "",
            "phone_number": "",
            "work_phone_number": "",
            "email": "",
            "installation_date": today,
            "technician_name": technician_name,
        },
        "confidence": {
            "job_number": 0.0,
            "customer_name": 0.0,
            "service_address": 0.0,
            "city_state_zip": 0.0,
            "phone_number": 0.0,
            "work_phone_number": 0.0,
            "email": 0.0,
            "installation_date": 1.0 if today else 0.0,
            "technician_name": 1.0 if technician_name else 0.0,
        },
        "warnings": [warning],
        "meta": {
            "screenshot_filename": screenshot_filename,
            "original_filename": screenshot_filename or "",
        },
    }


def _build_manual_epon_payload(
    *,
    today: str,
    tech_number: str,
    supervisor: str,
    location: str,
    screenshot_filename: str | None,
    warning: str,
) -> dict[str, Any]:
    split = _split_name(supervisor)

    return {
        "fields": {
            "billing_date": today,
            "location": location,
            "tech_number": tech_number,
            "supervisor": supervisor,
            "customer_name": "",
            "customer_address": "",
            "address_line_2": "",
            "city": "",
            "state": "FL",
            "postal": "",
            "account_number": "",
            "job_number": "",
            "primary_phone": "",
            "billing_type": "",
            "rr8_quantity": "",
            "rs3_quantity": "",
            "supervisor_first": split["first"],
            "supervisor_last": split["last"],
            "merged_address": "",
        },
        "confidence": {
            "billing_date": 1.0 if today else 0.0,
            "location": 1.0 if location else 0.0,
            "tech_number": 1.0 if tech_number else 0.0,
            "supervisor": 1.0 if supervisor else 0.0,
            "customer_name": 0.0,
            "customer_address": 0.0,
            "city": 0.0,
            "state": 0.0,
            "postal": 0.0,
            "account_number": 0.0,
            "job_number": 0.0,
            "primary_phone": 0.0,
            "billing_type": 0.0,
            "rr8_quantity": 0.0,
            "rs3_quantity": 0.0,
        },
        "warnings": [
            warning,
            "Supervisor and Tech Number were loaded from saved preferences when available.",
            "Choose RR8 or RS3 and confirm the quantity before submitting.",
            "The uploaded screenshot will also be used as the required photo attachment for the selected billing type.",
        ],
        "meta": {
            "screenshot_filename": screenshot_filename,
            "original_filename": screenshot_filename or "",
        },
    }


def _validate_required_fields(fields: dict[str, Any]) -> dict[str, str]:
    required_fields = {
        "job_number": "Job Number is required.",
        "customer_name": "Customer Name is required.",
        "service_address": "Service Address is required.",
        "city_state_zip": "City, State, ZIP is required.",
        "phone_number": "Phone Number is required.",
        "installation_date": "Date of Installation is required.",
        "customer_signature": "Customer Signature is required.",
        "technician_name": "Technician Name is required.",
        "technician_signature": "Technician Signature is required.",
    }

    errors: dict[str, str] = {}
    for key, message in required_fields.items():
        value = fields.get(key)
        if not isinstance(value, str) or not value.strip():
            errors[key] = message
    return errors


def _validate_epon_fields(fields: dict[str, Any]) -> dict[str, str]:
    required_fields = {
        "billing_date": "Billing Date is required.",
        "location": "Location is required.",
        "tech_number": "Tech Number is required.",
        "supervisor": "Supervisor is required.",
        "customer_address": "Customer Address is required.",
        "city": "City is required.",
        "state": "State is required.",
        "postal": "ZIP is required.",
        "account_number": "Account Number is required.",
        "billing_type": "Billing Type is required.",
    }

    errors: dict[str, str] = {}
    for key, message in required_fields.items():
        value = fields.get(key)
        if not isinstance(value, str) or not value.strip():
            errors[key] = message

    billing_type = str(fields.get("billing_type") or "").strip()
    if billing_type == "RR8" and not str(fields.get("rr8_quantity") or "").strip():
        errors["rr8_quantity"] = "RR8 quantity is required."
    if billing_type == "RS3" and not str(fields.get("rs3_quantity") or "").strip():
        errors["rs3_quantity"] = "RS3 quantity is required."

    return errors


def _split_city_state_zip(value: str) -> dict[str, str]:
    raw = value.strip()
    if not raw:
        return {"city": "", "state": "FL", "postal": ""}

    postal_match = re.search(r"(\d{5}(?:-\d{4})?)$", raw)
    postal = postal_match.group(1) if postal_match else ""
    remaining = raw[: postal_match.start()].rstrip(", ").strip() if postal_match else raw

    state_match = re.search(r"(?:,\s*|\s+)([A-Z]{2})$", remaining)
    state = state_match.group(1) if state_match else "FL"
    city = remaining[: state_match.start()].rstrip(", ").strip() if state_match else remaining

    return {
        "city": city,
        "state": state,
        "postal": postal,
    }


def _split_name(value: str) -> dict[str, str]:
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        return {"first": "", "last": ""}

    parts = cleaned.split(" ", 1)
    if len(parts) == 1:
        return {"first": parts[0], "last": ""}
    return {"first": parts[0], "last": parts[1]}


def _submit_epon_to_remote(
    fields: dict[str, Any],
    *,
    nap_file: Path | None,
    onu_file: Path | None,
) -> dict[str, Any]:
    supervisor = _split_name(str(fields.get("supervisor") or "").strip())

    session_client = requests.Session()
    session_client.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": EPON_REMOTE_PAGE_URL,
        }
    )

    page_response = session_client.get(EPON_REMOTE_PAGE_URL, timeout=30)
    page_response.raise_for_status()

    action_url = _extract_remote_action_url(page_response.text) or EPON_REMOTE_ACTION_URL
    post_url = urljoin(EPON_REMOTE_PAGE_URL, action_url)

    billing_type = str(fields.get("billing_type") or "").strip()
    form_payload: list[tuple[str, Any]] = [
        ("wpforms[id]", EPON_FORM_ID),
        ("page_title", "EPON Additional Billing"),
        ("page_url", EPON_REMOTE_PAGE_URL),
        ("url_referer", ""),
        ("page_id", EPON_PAGE_ID),
        ("wpforms[post_id]", EPON_PAGE_ID),
        ("wpforms[submit]", "wpforms-submit"),
        ("wpforms[fields][1]", str(fields.get("billing_date") or "").strip()),
        ("wpforms[fields][2]", str(fields.get("location") or "").strip()),
        ("wpforms[fields][3]", str(fields.get("tech_number") or "").strip()),
        ("wpforms[fields][4][first]", supervisor["first"]),
        ("wpforms[fields][4][last]", supervisor["last"]),
        ("wpforms[fields][5][address1]", str(fields.get("customer_address") or "").strip()),
        ("wpforms[fields][5][address2]", str(fields.get("address_line_2") or "").strip()),
        ("wpforms[fields][5][city]", str(fields.get("city") or "").strip()),
        ("wpforms[fields][5][state]", str(fields.get("state") or "").strip()),
        ("wpforms[fields][5][postal]", str(fields.get("postal") or "").strip()),
        ("wpforms[fields][6]", str(fields.get("account_number") or "").strip()),
        ("wpforms[fields][12]", str(fields.get("tech_number") or "").strip()),
    ]

    if billing_type == "RR8":
        form_payload.append(("wpforms[fields][7][]", "RR8 - Aerial EPON 300' or Greater"))
        form_payload.append(("wpforms[fields][8]", str(fields.get("rr8_quantity") or "").strip()))
    elif billing_type == "RS3":
        form_payload.append(("wpforms[fields][7][]", "RS3 - Wallfish"))
        form_payload.append(("wpforms[fields][9]", str(fields.get("rs3_quantity") or "").strip()))

    files: list[tuple[str, tuple[str, bytes, str]]] = []
    opened_files: list[Any] = []

    try:
        if nap_file and nap_file.exists():
            nap_handle = open(nap_file, "rb")
            opened_files.append(nap_handle)
            files.append(("wpforms_22345_11", (nap_file.name, nap_handle, "image/png")))

        if onu_file and onu_file.exists():
            onu_handle = open(onu_file, "rb")
            opened_files.append(onu_handle)
            files.append(("wpforms_22345_10", (onu_file.name, onu_handle, "image/png")))

        submit_response = session_client.post(
            post_url,
            data=form_payload,
            files=files or None,
            timeout=60,
            allow_redirects=True,
        )
        submit_response.raise_for_status()
    finally:
        for handle in opened_files:
            try:
                handle.close()
            except Exception:
                pass

    body_lower = submit_response.text.lower()
    if "error" in body_lower and "wpforms-error" in body_lower:
        raise RuntimeError("The remote EPON form returned a validation error. Review the fields and try again.")

    return {
        "status_code": submit_response.status_code,
        "url": submit_response.url,
    }


def _extract_remote_action_url(html: str) -> str:
    match = re.search(
        r'<form[^>]+id="wpforms-form-22345"[^>]+action="([^"]+)"',
        html,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _ensure_directories() -> None:
    for directory in (INSTANCE_DIR, DATA_DIR, UPLOAD_DIR, PDF_DIR, TEMPLATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _configure_logging(app: Flask) -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app.logger.setLevel(log_level)


def _generate_csrf_token() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
