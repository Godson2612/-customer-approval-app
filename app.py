from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
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
        APP_NAME="Customer Approval",
    )

    _ensure_directories()
    _configure_logging(app)

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
    def home():
        return redirect(url_for("customer_approval"))

    @app.get("/customer-approval")
    def customer_approval() -> str:
        technician_name = os.getenv("DEFAULT_TECHNICIAN_NAME", "").strip()
        return render_template(
            "customer_approval.html",
            csrf_token=session["csrf_token"],
            technician_name=technician_name,
            today=datetime.now().strftime("%m/%d/%Y"),
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
        stored_path = UPLOAD_DIR / safe_name

        try:
            upload.stream.seek(0)
            with Image.open(upload.stream) as source_image:
                normalized = ImageOps.exif_transpose(source_image).convert("RGB")
                normalized.save(stored_path, format="PNG", optimize=True)
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

            manual_payload = _build_manual_extract_payload(
                technician_name=technician_name,
                today=datetime.now().strftime("%m/%d/%Y"),
                screenshot_filename=safe_name if (keep_screenshot or app.config["KEEP_SCREENSHOTS"]) else None,
                warning=error.public_message,
            )

            if manual_payload["meta"]["screenshot_filename"] is None:
                stored_path.unlink(missing_ok=True)

            return jsonify(manual_payload), 200

        except Exception:
            app.logger.exception("Unhandled extraction failure")

            manual_payload = _build_manual_extract_payload(
                technician_name=technician_name,
                today=datetime.now().strftime("%m/%d/%Y"),
                screenshot_filename=safe_name if (keep_screenshot or app.config["KEEP_SCREENSHOTS"]) else None,
                warning="Automatic extraction failed, so please review and complete the form manually.",
            )

            if manual_payload["meta"]["screenshot_filename"] is None:
                stored_path.unlink(missing_ok=True)

            return jsonify(manual_payload), 200

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
            return jsonify({"error": error.public_message}), 400

        try:
            document = generate_customer_approval_pdf(
                template_path=Path(app.config["PDF_TEMPLATE_PATH"]),
                output_dir=PDF_DIR,
                form_data=fields,
                customer_signature_bytes=customer_signature_bytes,
                technician_signature_bytes=technician_signature_bytes,
            )
        except PDFGenerationError as error:
            app.logger.warning("PDF generation failed: %s", error.public_message)
            return jsonify({"error": error.public_message}), 422
        except Exception:
            app.logger.exception("Unhandled PDF generation failure")
            return jsonify({"error": "Unable to generate the approval document."}), 500

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

        if screenshot_filename and delete_screenshot_after and not app.config["KEEP_SCREENSHOTS"]:
            (UPLOAD_DIR / screenshot_filename).unlink(missing_ok=True)

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

    @app.get("/approvals/<int:approval_id>/download")
    def download_approval(approval_id: int):
        repository: ApprovalRepository = app.config["APPROVAL_REPOSITORY"]
        approval = repository.get_approval(approval_id)
        if not approval:
            abort(404)

        file_path = PDF_DIR / approval["pdf_filename"]
        if not file_path.exists():
            abort(404)

        return send_file(
            file_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=approval["pdf_filename"],
            max_age=0,
        )

    return app


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


def _ensure_directories() -> None:
    for directory in (INSTANCE_DIR, DATA_DIR, UPLOAD_DIR, PDF_DIR, TEMPLATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _configure_logging(app: Flask) -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app.logger.setLevel(log_level)


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
