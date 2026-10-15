"""
OCR support module for scanned PDFs and image files.

Architecture:
- Attempts pytesseract (Tesseract OCR) when available.
- Falls back to a clear "OCR_UNAVAILABLE" status when Tesseract is not installed.
- Kept modular: importing this module will NEVER raise an import error; it
  gracefully degrades if optional dependencies are missing.

Windows installation (Tesseract):
    1. Download installer from https://github.com/UB-Mannheim/tesseract/wiki
    2. Install to default path (C:\\Program Files\\Tesseract-OCR\\tesseract.exe)
    3. pip install pytesseract pillow pdf2image
    4. Install Poppler for Windows (required by pdf2image):
       https://github.com/oschwartz10612/poppler-windows/releases
       Add Poppler bin/ folder to your PATH.

Without those installations, OCR returns status "ocr_unavailable".
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Availability detection (lazy – checked at call time, not import time)        #
# --------------------------------------------------------------------------- #

def _check_ocr_available() -> tuple[bool, str]:
    """Return (is_available, reason_message)."""
    for pkg in ("pytesseract", "PIL", "pdf2image"):
        if importlib.util.find_spec(pkg) is None:
            return False, (
                f"OCR package '{pkg}' is not installed. "
                "See ai-server/ocr_support.py for installation instructions."
            )
    # Probe Tesseract binary
    try:
        import pytesseract  # type: ignore
        pytesseract.get_tesseract_version()
        return True, "ok"
    except Exception as exc:
        return False, (
            f"Tesseract binary not found or not executable: {exc}. "
            "See ai-server/ocr_support.py for installation instructions."
        )


_ocr_status: tuple[bool, str] | None = None  # cached after first check


def is_ocr_available() -> tuple[bool, str]:
    """Return (available, message). Result is cached after first call."""
    global _ocr_status
    if _ocr_status is None:
        _ocr_status = _check_ocr_available()
    return _ocr_status


# --------------------------------------------------------------------------- #
# OCR helpers                                                                   #
# --------------------------------------------------------------------------- #

import concurrent.futures

DEFAULT_OCR_TIMEOUT_SECONDS = 30.0


def _ocr_image_bytes(image_bytes: bytes) -> str:
    """Run Tesseract on raw image bytes. Requires pytesseract + pillow."""
    from PIL import Image  # type: ignore
    import pytesseract  # type: ignore

    image = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(image, lang="eng")


def _run_ocr_pdf(filename: str, content: bytes) -> dict[str, Any]:
    """Internal helper to convert PDF to images and run Tesseract page by page."""
    from pdf2image import convert_from_bytes  # type: ignore

    images = convert_from_bytes(content, dpi=200)
    page_texts: list[str] = []
    page_records: list[dict[str, Any]] = []
    warnings: list[str] = []

    for page_number, image in enumerate(images, start=1):
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        page_raw = _ocr_image_bytes(buf.getvalue()).strip()
        page_records.append({"page_number": page_number, "text": page_raw})
        if page_raw:
            page_texts.append(f"[Page {page_number}]\n{page_raw}")
        else:
            warnings.append(f"Page {page_number}: OCR yielded no text.")

    combined = "\n\n".join(page_texts).strip()
    return {
        "filename": filename,
        "text": combined,
        "extraction_status": "ocr_success" if combined else "ocr_failed",
        "page_count": len(images),
        "page_text": page_records,
        "warnings": warnings,
        "error": None if combined else "OCR produced no text from any page.",
    }


def ocr_pdf(filename: str, content: bytes, timeout: float = DEFAULT_OCR_TIMEOUT_SECONDS) -> dict[str, Any]:
    """
    Extract text from a scanned PDF using Tesseract via pdf2image with timeout.

    Returns:
        {
            "filename": str,
            "text": str,
            "extraction_status": "ocr_success" | "ocr_failed" | "ocr_unavailable",
            "page_count": int | None,
            "page_text": list[dict[str, Any]],
            "warnings": list[str],
            "error": str | None,
        }
    """
    available, reason = is_ocr_available()
    if not available:
        return {
            "filename": filename,
            "text": "",
            "extraction_status": "ocr_unavailable",
            "page_count": None,
            "page_text": [],
            "warnings": [],
            "error": reason,
        }

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_ocr_pdf, filename, content)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning("OCR timed out for %s after %ss", filename, timeout)
                return {
                    "filename": filename,
                    "text": "",
                    "extraction_status": "ocr_failed",
                    "page_count": None,
                    "page_text": [],
                    "warnings": [f"OCR operation timed out after {timeout} seconds."],
                    "error": f"OCR processing timed out after {timeout} seconds.",
                }
    except Exception as exc:
        logger.warning("OCR failed for %s: %s", filename, exc)
        return {
            "filename": filename,
            "text": "",
            "extraction_status": "ocr_failed",
            "page_count": None,
            "page_text": [],
            "warnings": [],
            "error": f"OCR processing error: {exc}",
        }


def _run_ocr_image(filename: str, content: bytes) -> dict[str, Any]:
    text = _ocr_image_bytes(content).strip()
    return {
        "filename": filename,
        "text": text,
        "extraction_status": "ocr_success" if text else "ocr_failed",
        "page_count": 1 if text else None,
        "page_text": [{"page_number": 1, "text": text}] if text else [],
        "warnings": [] if text else ["Image OCR yielded no text."],
        "error": None if text else "OCR produced no text from the image.",
    }


def ocr_image(filename: str, content: bytes, timeout: float = DEFAULT_OCR_TIMEOUT_SECONDS) -> dict[str, Any]:
    """
    Extract text from a JPG/PNG/JPEG image using Tesseract with timeout.

    Returns:
        {
            "filename": str,
            "text": str,
            "extraction_status": "ocr_success" | "ocr_failed" | "ocr_unavailable",
            "page_count": int | None,
            "page_text": list[dict[str, Any]],
            "warnings": list[str],
            "error": str | None,
        }
    """
    available, reason = is_ocr_available()
    if not available:
        return {
            "filename": filename,
            "text": "",
            "extraction_status": "ocr_unavailable",
            "page_count": None,
            "page_text": [],
            "warnings": [],
            "error": reason,
        }

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_ocr_image, filename, content)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning("OCR image timed out for %s after %ss", filename, timeout)
                return {
                    "filename": filename,
                    "text": "",
                    "extraction_status": "ocr_failed",
                    "page_count": None,
                    "page_text": [],
                    "warnings": [f"OCR image timed out after {timeout} seconds."],
                    "error": f"OCR processing timed out after {timeout} seconds.",
                }
    except Exception as exc:
        logger.warning("OCR image failed for %s: %s", filename, exc)
        return {
            "filename": filename,
            "text": "",
            "extraction_status": "ocr_failed",
            "page_count": None,
            "page_text": [],
            "warnings": [],
            "error": f"OCR processing error: {exc}",
        }

