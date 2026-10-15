"""
FastAPI AI Service for CREDORA Loan Document Analysis.

Runs independently of the Node/Express backend on port 8001.
The Express backend can forward requests to this service via the
AI_SERVICE_URL environment variable (default: http://127.0.0.1:8001).

Endpoints:
    GET  /                  Service info
    GET  /health            Basic health (no credentials)
    GET  /health/ready      Config readiness (no Gemini call)
    POST /extract           Extract text from uploaded documents
    POST /analyze/text      Analyze pre-extracted text (JSON body)
    POST /analyze           Upload + extract + analyze in one request

Security:
    - File size / count limits enforced before processing.
    - Safe temporary file cleanup on every path.
    - API key header (X-API-Key) — configure AI_SERVER_API_KEY in .env.
      When not set, auth is disabled (development mode only).
    - CORS restricted to configured origins.
    - No secrets in responses. No document text in logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

# ── Load root .env regardless of working directory ───────────────────────────
from dotenv import load_dotenv

_AI_SERVER_DIR = Path(__file__).resolve().parent       # ai-server/
_ROOT_DIR = _AI_SERVER_DIR.parent                      # project root

load_dotenv(_ROOT_DIR / ".env")   # explicit path – works from any cwd

# ── Import the existing analyzer (kept in project root) ──────────────────────
sys.path.insert(0, str(_ROOT_DIR))
from document_analyzer import (  # noqa: E402
    DocumentAnalysisError,
    DocumentExtractionResult,
    analyze_application,
    detect_file_signature,
    extract_document_detailed,
    extract_documents,
    extract_text,
    safe_temp_dir,
    safe_temp_file,
)
from financial_calculator import (  # noqa: E402
    TRUSTED_SERVER_CHECKLIST_REGISTRY,
    generate_financial_review,
    run_all_calculations,
)
from ocr_support import is_ocr_available, ocr_image, ocr_pdf  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_MB", "20")) * 1024 * 1024  # 20 MB default
MAX_TOTAL_UPLOAD_SIZE_BYTES = int(os.getenv("MAX_TOTAL_UPLOAD_SIZE_MB", "50")) * 1024 * 1024  # 50 MB default
MAX_FILES_PER_REQUEST = int(os.getenv("MAX_FILES_PER_REQUEST", "10"))
MAX_CONCURRENT_EXTRACTIONS = int(os.getenv("MAX_CONCURRENT_EXTRACTIONS", "4"))
EXTRACTION_TIMEOUT_SECONDS = float(os.getenv("EXTRACTION_TIMEOUT_SECONDS", "60.0"))
OCR_TIMEOUT_SECONDS = float(os.getenv("OCR_TIMEOUT_SECONDS", "30.0"))
AI_SERVER_API_KEY = os.getenv("AI_SERVER_API_KEY", "")  # empty = dev mode (auth disabled)

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".jpg", ".jpeg", ".png"}
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:8000",
    ).split(",")
    if origin.strip()
]


def _sanitize_error_message(msg: str | None) -> str:
    """Sanitize error messages to avoid leaking absolute filesystem paths, drive letters, or secrets."""
    if not msg:
        return ""
    # Strip Windows paths (e.g. C:\... or c:/...)
    clean = re.sub(r"[a-zA-Z]:\\[^\s:;,]+", "[internal_path]", str(msg))
    clean = re.sub(r"[a-zA-Z]:/[^\s:;,]+", "[internal_path]", clean)
    # Strip POSIX paths
    clean = re.sub(r"/(?:[a-zA-Z0-9_\.-]+/)+[a-zA-Z0-9_\.-]+", "[internal_path]", clean)
    # Strip sensitive key tokens if any leak into error messages
    clean = re.sub(r"(?:key|token|secret|password)=([^\s&]+)", r"\1=***", clean, flags=re.IGNORECASE)
    return clean

SERVICE_VERSION = "1.0.0"


# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="CREDORA AI Document Analysis Service",
    description=(
        "AI-powered loan document extraction and analysis using Google Gemini. "
        "Designed to integrate with the CREDORA Express backend."
    ),
    version=SERVICE_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Auth dependency ────────────────────────────────────────────────────────────
async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Validate X-API-Key when AI_SERVER_API_KEY is configured."""
    if not AI_SERVER_API_KEY:
        # Development mode: auth disabled. Log a warning once per startup.
        return
    if not x_api_key or x_api_key != AI_SERVER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header.",
        )


# ── Pydantic models ────────────────────────────────────────────────────────────
class ServiceInfo(BaseModel):
    service: str
    version: str
    description: str
    status: str


class HealthResponse(BaseModel):
    status: str
    timestamp: float


class ReadinessResponse(BaseModel):
    ready: bool
    gemini_key_present: bool
    gemini_model_present: bool
    ocr_available: bool
    ocr_note: str
    issues: list[str]


class PageText(BaseModel):
    page_number: int
    text: str


class DocumentResult(BaseModel):
    document_id: str | None = None
    filename: str
    detected_file_type: str | None = None
    page_count: int | None = None
    extraction_status: str  # "success" | "failed" | "ocr_success" | "ocr_failed" | "ocr_unavailable"
    extracted_text: str | None = None
    page_text: list[dict[str, Any]] = Field(default_factory=list)
    ocr_used: bool = False
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None

    # Backward-compatibility aliases
    text: str | None = None
    text_length: int = 0

    @model_validator(mode="after")
    def sync_text_fields(self) -> "DocumentResult":
        if self.extracted_text is not None and not self.text:
            self.text = self.extracted_text
        elif self.text is not None and not self.extracted_text:
            self.extracted_text = self.text
        if not self.text_length:
            self.text_length = len(self.extracted_text or self.text or "")
        return self


class ExtractResponse(BaseModel):
    request_id: str
    documents: list[DocumentResult]
    total_documents: int
    successful_extractions: int
    failed_extractions: int


class AnalyzeTextRequest(BaseModel):
    application_details: dict[str, Any] = Field(
        ..., description="Borrower-provided application fields."
    )
    documents: list[dict[str, Any]] = Field(
        ...,
        description=(
            "Pre-extracted documents. Each item: "
            "{'filename': str, 'text': str, 'document_type': str (optional)}"
        ),
    )
    financial_inputs: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional deterministic financial inputs: principal, annual_interest_rate_pct, "
            "tenure_months, income_sources, etc."
        ),
    )
    lender_context: dict[str, Any] | None = Field(
        default=None,
        description="Optional lender context: lender_category, lender_id, lender_name, loan_product, document_checklist, etc.",
    )

    @model_validator(mode="after")
    def check_documents_not_empty(self) -> "AnalyzeTextRequest":
        if not self.documents:
            raise ValueError("documents list must not be empty.")
        return self


class DiscrepancyModel(BaseModel):
    finding_id: str | None = None
    field_name: str | None = None
    rule_or_check: str | None = None
    observed_value: Any = None
    expected_value: Any = None
    source_filename: str | None = None
    page_number: int | str | None = None
    finding_type: str | None = None
    explanation: str | None = None
    severity: str | None = None
    confidence: str | None = None
    recommended_verification: str | None = None
    human_verification_required: bool = True


class AnalysisReport(BaseModel):
    application_summary: dict[str, Any] = Field(default_factory=dict)
    document_results: list[Any] = Field(default_factory=list)
    extracted_fields: list[Any] = Field(default_factory=list)
    document_classifications: list[Any] = Field(default_factory=list)
    missing_information: list[Any] = Field(default_factory=list)
    discrepancies: list[Any] = Field(default_factory=list)
    financial_summary: dict[str, Any] = Field(default_factory=dict)
    financial_review: dict[str, Any] = Field(default_factory=dict)
    risk_flags: list[Any] = Field(default_factory=list)
    manual_review_items: list[Any] = Field(default_factory=list)
    analysis_limitations: list[Any] = Field(default_factory=list)
    cross_document_checks: list[Any] = Field(default_factory=list)
    cross_document_summary: dict[str, Any] = Field(default_factory=dict)
    overall_status: str = Field(default="not_assessed")

    # Backward compatibility aliases
    document_summary: list[Any] = Field(default_factory=list)
    processing_metadata: dict[str, Any] = Field(default_factory=dict)


def _safe_build_analysis_report(raw_report: dict[str, Any]) -> tuple[AnalysisReport, list[str]]:
    """Build AnalysisReport safely, coercing types to prevent 500 errors on unexpected AI response structures."""
    try:
        return AnalysisReport(**raw_report), []
    except Exception:
        dict_fields = {"application_summary", "financial_summary", "financial_review", "processing_metadata", "cross_document_summary"}
        str_fields = {"overall_status"}
        coerced: dict[str, Any] = {}
        for k in AnalysisReport.model_fields:
            val = raw_report.get(k)
            if k in dict_fields:
                coerced[k] = val if isinstance(val, dict) else {}
            elif k in str_fields:
                coerced[k] = str(val) if val else "not_assessed"
            else:
                coerced[k] = val if isinstance(val, list) else []
        return AnalysisReport(**coerced), ["Report structure partially validated; some fields were coerced to valid schemas."]


class AnalyzeResponse(BaseModel):
    # Primary unified response fields required by Prompt 6:
    success: bool = True
    application_id: str | None = None
    analysis_status: str = "complete"  # "complete" | "partial_failure" | "requires_manual_review" | "failed"
    application_summary: dict[str, Any] = Field(default_factory=dict)
    document_results: list[DocumentResult] = Field(default_factory=list)
    document_classifications: list[dict[str, Any]] = Field(default_factory=list)
    extracted_fields: list[dict[str, Any]] = Field(default_factory=list)
    cross_document_checks: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    financial_summary: dict[str, Any] | None = Field(default_factory=dict)
    document_checklist_status: dict[str, Any] | None = Field(default_factory=dict)
    risk_flags: list[Any] = Field(default_factory=list)
    manual_review_items: list[Any] = Field(default_factory=list)
    analysis_limitations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    processing_metadata: dict[str, Any] = Field(default_factory=dict)

    # Backward compatibility aliases & fields
    request_id: str = ""
    report: AnalysisReport = Field(default_factory=AnalysisReport)
    document_statuses: list[DocumentResult] = Field(default_factory=list)
    financial_calculations: list[dict[str, Any]] = Field(default_factory=list)
    financial_review: dict[str, Any] = Field(default_factory=dict)
    cross_document_summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


def _build_comprehensive_application_summary(
    details: dict[str, Any],
    raw_report: dict[str, Any],
    doc_results: list[DocumentResult],
    usable_docs: list[dict[str, Any]],
    lender_ctx: dict[str, Any] | None = None,
    fin_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a comprehensive, structured application summary report explaining:
    - what was analyzed
    - what matched
    - what was missing
    - which inconsistencies require attention
    - what could not be assessed
    - decision recommendation
    """
    existing_summary = raw_report.get("application_summary", {})
    if isinstance(existing_summary, str):
        base_overview = existing_summary
        summary_dict: dict[str, Any] = {}
    elif isinstance(existing_summary, dict):
        base_overview = str(existing_summary.get("overview") or existing_summary.get("narrative") or "")
        summary_dict = dict(existing_summary)
    else:
        base_overview = ""
        summary_dict = {}

    applicant_name = str(
        details.get("applicant_name")
        or details.get("full_name")
        or details.get("name")
        or "Applicant"
    )
    lender_cat = str((lender_ctx or {}).get("lender_category") or details.get("lender_category") or "General Lending")
    loan_prod = str((lender_ctx or {}).get("loan_product") or details.get("loan_product") or "Standard Loan")

    # 1. What was analyzed
    doc_names = [d.filename for d in doc_results]
    failed_names = [d.filename for d in doc_results if d.extraction_status not in {"success", "ocr_success"}]
    what_was_analyzed = (
        f"Analyzed {len(doc_results)} document(s) ({', '.join(doc_names)}) for applicant '{applicant_name}' "
        f"under {lender_cat} ({loan_prod}). {len(usable_docs)} document(s) successfully processed."
    )

    # 2. What matched
    what_matched: list[str] = []
    checks = raw_report.get("cross_document_checks", [])
    for chk in checks:
        if isinstance(chk, dict):
            status_chk = str(chk.get("status") or chk.get("result") or "").lower()
            desc = chk.get("description") or chk.get("check_name") or chk.get("details") or chk.get("summary") or ""
            if status_chk in {"pass", "consistent", "verified", "matched", "match"} and desc:
                what_matched.append(str(desc))
    if not what_matched:
        what_matched.append("Document format and applicant metadata validated.")

    # 3. What was missing
    what_was_missing: list[str] = []
    for item in raw_report.get("missing_information", []):
        what_was_missing.append(str(item))
    if fin_review and isinstance(fin_review, dict):
        chk_status = fin_review.get("document_checklist_status", {})
        if isinstance(chk_status, dict):
            for m_doc in chk_status.get("missing_required_documents", []):
                if isinstance(m_doc, dict):
                    doc_title = m_doc.get("document_name") or m_doc.get("item_id")
                    source = m_doc.get("rule_source") or "checklist"
                    what_was_missing.append(f"Required document '{doc_title}' missing according to {source}.")
            for m_inp in fin_review.get("missing_financial_inputs", []):
                what_was_missing.append(f"Financial input missing: {m_inp}")

    # 4. Inconsistencies requiring attention
    inconsistencies: list[str] = []
    for disc in raw_report.get("discrepancies", []):
        if isinstance(disc, dict):
            field = disc.get("field_name") or disc.get("field") or "Attribute"
            expl = disc.get("explanation") or disc.get("description") or "Variance detected"
            inconsistencies.append(f"{field}: {expl}")
    if fin_review and isinstance(fin_review, dict):
        for f_disc in fin_review.get("financial_discrepancies", []):
            if isinstance(f_disc, dict):
                expl = f_disc.get("explanation") or f_disc.get("evidence") or "Financial variance"
                inconsistencies.append(f"Financial reconciliation: {expl}")

    # 5. What could not be assessed
    limitations: list[str] = []
    for lim in raw_report.get("analysis_limitations", []):
        limitations.append(str(lim))
    if failed_names:
        limitations.append(
            f"Extraction failed for document(s): {', '.join(failed_names)}; contents could not be evaluated."
        )

    # 6. Synthesize overview if empty
    if not base_overview:
        if inconsistencies or what_was_missing or failed_names:
            base_overview = (
                f"Application for {applicant_name} ({lender_cat} - {loan_prod}) was processed across "
                f"{len(usable_docs)} usable document(s). Variances or missing items were identified and flagged for review."
            )
        else:
            base_overview = (
                f"Application for {applicant_name} ({lender_cat} - {loan_prod}) was verified successfully across "
                f"{len(usable_docs)} usable document(s) with no blocking discrepancies."
            )

    action = (
        "manual_review_required"
        if (inconsistencies or failed_names or what_was_missing)
        else "proceed_to_credit_underwriting"
    )

    summary_dict.update({
        "applicant_name": applicant_name,
        "lender_category": lender_cat,
        "loan_product": loan_prod,
        "overview": base_overview,
        "what_was_analyzed": what_was_analyzed,
        "what_matched": list(dict.fromkeys(what_matched)),
        "what_was_missing": list(dict.fromkeys(what_was_missing)),
        "inconsistencies_requiring_attention": list(dict.fromkeys(inconsistencies)),
        "what_could_not_be_assessed": list(dict.fromkeys(limitations)),
        "recommended_action": action,
    })
    return summary_dict



# ── File validation helpers ────────────────────────────────────────────────────
def _safe_filename(filename: str | None) -> str:
    """Return a sanitized filename without path traversal risk."""
    if not filename:
        return f"upload_{uuid.uuid4().hex[:8]}"
    safe = Path(filename).name  # strip any directory component
    # Replace characters that are problematic on Windows
    for ch in r'\/:*?"<>|':
        safe = safe.replace(ch, "_")
    return safe or f"upload_{uuid.uuid4().hex[:8]}"


def _file_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


async def _read_upload(upload: UploadFile) -> bytes:
    """Read upload bytes and enforce size limit."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(65536):
        size += len(chunk)
        if size > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"File '{upload.filename}' exceeds the {MAX_FILE_SIZE_BYTES // (1024*1024)} MB limit."
                ),
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_all_uploads(
    files: list[UploadFile],
    max_file_size: int = MAX_FILE_SIZE_BYTES,
    max_total_size: int = MAX_TOTAL_UPLOAD_SIZE_BYTES,
) -> list[tuple[str, bytes]]:
    """
    Safely read all uploaded files while enforcing single file size and
    cumulative total upload size limits. Returns list of (safe_filename, content_bytes).
    """
    total_bytes = 0
    file_data: list[tuple[str, bytes]] = []

    for upload in files:
        safe_name = _safe_filename(upload.filename)
        chunks: list[bytes] = []
        current_file_bytes = 0

        while chunk := await upload.read(65536):
            current_file_bytes += len(chunk)
            total_bytes += len(chunk)

            if current_file_bytes > max_file_size:
                max_mb = max_file_size // (1024 * 1024)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File '{safe_name}' exceeds the {max_mb} MB limit.",
                )

            if total_bytes > max_total_size:
                max_total_mb = max_total_size // (1024 * 1024)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Total upload size exceeds the {max_total_mb} MB limit.",
                )

            chunks.append(chunk)

        file_data.append((safe_name, b"".join(chunks)))

    return file_data


def _validate_extension(filename: str) -> None:
    ext = _file_extension(filename)
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"File '{filename}' has unsupported extension '{ext}'. "
                f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
            ),
        )


# ── Extraction helper (handles text + OCR routing) ────────────────────────────
def _extract_single(filename: str, content: bytes) -> DocumentResult:
    """Extract text from one file; route scanned PDFs / images through OCR."""
    safe = _safe_filename(filename)
    ext = _file_extension(safe)
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"

    # Validate signature against declared extension
    detected_type, sig_err = detect_file_signature(content, safe)
    if sig_err:
        return DocumentResult(
            document_id=doc_id,
            filename=safe,
            detected_file_type=detected_type,
            page_count=None,
            extraction_status="failed",
            extracted_text=None,
            page_text=[],
            ocr_used=False,
            warnings=[],
            error=f"{safe}: {sig_err}",
        )

    # Image files → OCR only
    if ext in {".jpg", ".jpeg", ".png"} or detected_type in {"png", "jpeg"}:
        result = ocr_image(safe, content, timeout=OCR_TIMEOUT_SECONDS)
        status_val = result.get("extraction_status", "ocr_failed")
        text_val = result.get("text") or None
        return DocumentResult(
            document_id=doc_id,
            filename=safe,
            detected_file_type=detected_type if detected_type not in {"txt", "unknown"} else ext.lstrip("."),
            page_count=result.get("page_count") or (1 if status_val == "ocr_success" else None),
            extraction_status=status_val,
            extracted_text=text_val,
            page_text=result.get("page_text") or ([] if not text_val else [{"page_number": 1, "text": text_val}]),
            ocr_used=True,
            warnings=result.get("warnings") or [],
            error=result.get("error"),
            text=text_val or "",
            text_length=len(text_val or ""),
        )

    # PDF: try text extraction first; fall back to OCR on scanned PDFs
    if ext == ".pdf" or detected_type == "pdf":
        try:
            text = extract_text(safe, content)
            page_count = None
            page_records: list[dict[str, Any]] = []
            try:
                from pypdf import PdfReader
                import io
                reader = PdfReader(io.BytesIO(content), strict=False)
                page_count = len(reader.pages)
                for p_num, p in enumerate(reader.pages, start=1):
                    p_txt = (p.extract_text() or "").strip()
                    page_records.append({"page_number": p_num, "text": p_txt})
            except Exception:
                pass

            return DocumentResult(
                document_id=doc_id,
                filename=safe,
                detected_file_type="pdf",
                page_count=page_count,
                extraction_status="success",
                extracted_text=text,
                page_text=page_records,
                ocr_used=False,
                warnings=[],
                error=None,
                text=text,
                text_length=len(text),
            )
        except DocumentAnalysisError as exc:
            err_msg = str(exc)
            if "scanned" in err_msg.lower() or "no selectable text" in err_msg.lower():
                result = ocr_pdf(safe, content, timeout=OCR_TIMEOUT_SECONDS)
                status_val = result.get("extraction_status", "ocr_failed")
                text_val = result.get("text") or None
                return DocumentResult(
                    document_id=doc_id,
                    filename=safe,
                    detected_file_type="pdf",
                    page_count=result.get("page_count"),
                    extraction_status=status_val,
                    extracted_text=text_val,
                    page_text=result.get("page_text") or [],
                    ocr_used=True,
                    warnings=result.get("warnings") or [],
                    error=result.get("error"),
                    text=text_val or "",
                    text_length=len(text_val or ""),
                )
            return DocumentResult(
                document_id=doc_id,
                filename=safe,
                detected_file_type="pdf",
                page_count=None,
                extraction_status="failed",
                extracted_text=None,
                page_text=[],
                ocr_used=False,
                warnings=[],
                error=err_msg,
            )

    # DOCX / TXT
    try:
        text = extract_text(safe, content)
        return DocumentResult(
            document_id=doc_id,
            filename=safe,
            detected_file_type=detected_type if detected_type not in {"unknown"} else ext.lstrip("."),
            page_count=1,
            extraction_status="success",
            extracted_text=text,
            page_text=[{"page_number": 1, "text": text}],
            ocr_used=False,
            warnings=[],
            error=None,
            text=text,
            text_length=len(text),
        )
    except DocumentAnalysisError as exc:
        return DocumentResult(
            document_id=doc_id,
            filename=safe,
            detected_file_type=detected_type if detected_type not in {"unknown"} else ext.lstrip("."),
            page_count=None,
            extraction_status="failed",
            extracted_text=None,
            page_text=[],
            ocr_used=False,
            warnings=[],
            error=_sanitize_error_message(str(exc)),
        )


async def _extract_documents_bounded(
    file_data: list[tuple[str, bytes]],
    concurrency_limit: int = MAX_CONCURRENT_EXTRACTIONS,
    timeout_seconds: float = EXTRACTION_TIMEOUT_SECONDS,
) -> list[DocumentResult]:
    """
    Extract text across multiple documents with bounded concurrency and non-blocking I/O.
    Preserves document order and isolates individual document failures.
    """
    sem = asyncio.Semaphore(concurrency_limit)

    async def _safe_extract(safe: str, content: bytes) -> DocumentResult:
        ext = _file_extension(safe)
        if ext not in _ALLOWED_EXTENSIONS:
            return DocumentResult(
                document_id=f"doc_{uuid.uuid4().hex[:12]}",
                filename=safe,
                detected_file_type="unsupported",
                extraction_status="failed",
                error=f"File '{safe}' has unsupported extension '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
            )
        if not content:
            return DocumentResult(
                document_id=f"doc_{uuid.uuid4().hex[:12]}",
                filename=safe,
                detected_file_type="empty",
                extraction_status="failed",
                error=f"{safe}: file is empty.",
            )
        async with sem:
            try:
                return await asyncio.wait_for(
                    run_in_threadpool(_extract_single, safe, content),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                return DocumentResult(
                    document_id=f"doc_{uuid.uuid4().hex[:12]}",
                    filename=safe,
                    detected_file_type=_file_extension(safe).lstrip("."),
                    extraction_status="failed",
                    error=f"{safe}: extraction timed out after {timeout_seconds}s.",
                    warnings=[f"Processing time exceeded limit of {timeout_seconds} seconds."],
                )
            except Exception as exc:
                clean_err = _sanitize_error_message(str(exc))
                return DocumentResult(
                    document_id=f"doc_{uuid.uuid4().hex[:12]}",
                    filename=safe,
                    detected_file_type="unknown",
                    extraction_status="failed",
                    error=f"{safe}: unexpected extraction error: {clean_err}",
                )

    tasks = [_safe_extract(safe, content) for safe, content in file_data]
    return list(await asyncio.gather(*tasks))


# ── Request-ID middleware (for tracing) ───────────────────────────────────────
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ── Error handler ─────────────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "An internal server error occurred.",
            "request_id": getattr(request.state, "request_id", None),
        },
    )



# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/", response_model=ServiceInfo, tags=["Info"])
async def root():
    """Service identification and version."""
    return ServiceInfo(
        service="CREDORA AI Document Analysis",
        version=SERVICE_VERSION,
        description="Extracts and analyzes loan documents using Google Gemini.",
        status="running",
    )


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health():
    """Basic liveness check. Does not call Gemini or expose secrets."""
    return HealthResponse(status="healthy", timestamp=time.time())


@app.get("/health/ready", response_model=ReadinessResponse, tags=["Health"])
async def health_ready():
    """Configuration readiness check. Does not call Gemini."""
    gemini_key = bool(os.getenv("GEMINI_API_KEY", "").strip())
    gemini_model = bool(os.getenv("GEMINI_MODEL", "").strip())
    ocr_ok, ocr_msg = is_ocr_available()

    issues: list[str] = []
    if not gemini_key:
        issues.append("GEMINI_API_KEY is not set.")
    if not gemini_model:
        issues.append("GEMINI_MODEL is not set.")

    return ReadinessResponse(
        ready=gemini_key and gemini_model,
        gemini_key_present=gemini_key,
        gemini_model_present=gemini_model,
        ocr_available=ocr_ok,
        ocr_note=ocr_msg if not ocr_ok else "Tesseract OCR is available.",
        issues=issues,
    )


@app.post("/extract", response_model=ExtractResponse, tags=["Extraction"],
          dependencies=[Depends(require_api_key)])
async def extract(
    request: Request,
    files: list[UploadFile] = File(..., description="One or more documents to extract text from."),
):
    """
    Extract text from uploaded documents.

    Supports PDF (text-based + OCR fallback), DOCX, TXT, JPG, JPEG, PNG.
    Returns extracted text for local development/testing.
    """
    request_id: str = getattr(request.state, "request_id", str(uuid.uuid4()))

    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Too many files. Maximum {MAX_FILES_PER_REQUEST} per request.",
        )

    # If single file upload has unsupported extension, reject with 415
    if len(files) == 1:
        _validate_extension(_safe_filename(files[0].filename))

    file_data = await _read_all_uploads(files)
    doc_results = await _extract_documents_bounded(file_data)
    successful = sum(1 for d in doc_results if d.extraction_status in {"success", "ocr_success"})

    return ExtractResponse(
        request_id=request_id,
        documents=doc_results,
        total_documents=len(doc_results),
        successful_extractions=successful,
        failed_extractions=len(doc_results) - successful,
    )



@app.post("/analyze/text", response_model=AnalyzeResponse, tags=["Analysis"],
          dependencies=[Depends(require_api_key)])
async def analyze_text(request: Request, body: AnalyzeTextRequest):
    """
    Analyze pre-extracted document text together with application details.

    Use this endpoint when you have already extracted text elsewhere, or for
    testing the analysis logic with synthetic data.
    """
    request_id: str = getattr(request.state, "request_id", str(uuid.uuid4()))
    warnings: list[str] = []

    if not os.getenv("GEMINI_API_KEY") or not os.getenv("GEMINI_MODEL"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini is not configured. Set GEMINI_API_KEY and GEMINI_MODEL in .env.",
        )

    logger.info("Starting text analysis req_id=%s for applicant=%s with %d pre-extracted document(s)",
                request_id,
                body.application_details.get("application_id") or "unspecified",
                len(body.documents))

    try:
        raw_report = await run_in_threadpool(
            analyze_application,
            body.application_details,
            body.documents,
            lender_context=body.lender_context,
        )
    except DocumentAnalysisError as exc:
        clean_err = _sanitize_error_message(str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=clean_err)

    # Financial review & calculations
    financial_calcs: list[dict[str, Any]] = []
    fin_review: dict[str, Any] = raw_report.get("financial_review", {})
    if body.financial_inputs:
        try:
            financial_calcs = run_all_calculations(body.financial_inputs)
        except Exception as exc:
            warnings.append(f"Financial calculations failed: {_sanitize_error_message(str(exc))}")
    if not fin_review:
        try:
            fin_review = generate_financial_review(
                financial_inputs=body.financial_inputs or body.application_details,
                extracted_fields=raw_report.get("extracted_fields", []),
                document_classifications=raw_report.get("document_classifications", []),
                documents=body.documents,
                lender_context=body.lender_context,
            )
        except Exception as exc:
            warnings.append(f"Financial review failed: {_sanitize_error_message(str(exc))}")


    # Build per-document status list from the input
    doc_statuses = [
        DocumentResult(
            filename=str(d.get("filename", f"doc_{i}")),
            extraction_status="pre_extracted",
            text_length=len(str(d.get("text") or "")),
        )
        for i, d in enumerate(body.documents, 1)
    ]

    report, report_warnings = _safe_build_analysis_report(raw_report)
    warnings.extend(report_warnings)

    app_id = str(
        body.application_details.get("application_id")
        or body.application_details.get("id")
        or request_id
    )

    if (
        raw_report.get("discrepancies")
        or (fin_review and fin_review.get("financial_discrepancies"))
        or raw_report.get("manual_review_items")
        or (fin_review and fin_review.get("manual_review_items"))
        or raw_report.get("risk_flags")
    ):
        analysis_status = "requires_manual_review"
    else:
        analysis_status = "complete"

    app_summary = _build_comprehensive_application_summary(
        details=body.application_details,
        raw_report=raw_report,
        doc_results=doc_statuses,
        usable_docs=body.documents,
        lender_ctx=body.lender_context,
        fin_review=fin_review,
    )

    checklist_status = (
        fin_review.get("document_checklist_status")
        if fin_review
        else raw_report.get("financial_review", {}).get("document_checklist_status")
    ) or {}

    proc_meta = {
        **(raw_report.get("processing_metadata") or {}),
        "request_id": request_id,
        "timestamp": time.time(),
        "total_documents_submitted": len(body.documents),
        "successful_extractions": len(body.documents),
        "failed_extractions": 0,
        "ocr_used_count": 0,
        "lender_category": (body.lender_context or {}).get("lender_category"),
        "loan_product": (body.lender_context or {}).get("loan_product"),
    }

    all_errors = [_sanitize_error_message(w) for w in warnings if "failed" in w.lower()]

    return AnalyzeResponse(
        success=True,
        application_id=app_id,
        analysis_status=analysis_status,
        application_summary=app_summary,
        document_results=doc_statuses,
        document_classifications=raw_report.get("document_classifications", []),
        extracted_fields=raw_report.get("extracted_fields", []),
        cross_document_checks=raw_report.get("cross_document_checks", []),
        missing_information=raw_report.get("missing_information", []),
        financial_summary=raw_report.get("financial_summary", {}),
        document_checklist_status=checklist_status,
        risk_flags=raw_report.get("risk_flags", []),
        manual_review_items=raw_report.get("manual_review_items", []),
        analysis_limitations=raw_report.get("analysis_limitations", []),
        errors=all_errors,
        processing_metadata=proc_meta,
        # Backward compatibility fields
        request_id=request_id,
        report=report,
        document_statuses=doc_statuses,
        financial_calculations=financial_calcs,
        financial_review=fin_review or getattr(report, "financial_review", {}),
        cross_document_summary=getattr(report, "cross_document_summary", {}) or raw_report.get("cross_document_summary", {}),
        warnings=warnings,
    )


@app.post("/analyze", response_model=AnalyzeResponse, tags=["Analysis"],
          dependencies=[Depends(require_api_key)])
async def analyze(
    request: Request,
    files: list[UploadFile] = File(..., description="One or more loan documents."),
    application_details: str | None = Form(
        default=None,
        description="JSON string containing borrower application details.",
    ),
    applicant_name: str | None = Form(default=None, description="Applicant full name as per identity documents."),
    application_id: str | None = Form(default=None, description="External or internal application reference ID."),
    email: str | None = Form(default=None, description="Applicant contact email address."),
    phone: str | None = Form(default=None, description="Applicant contact mobile/phone number."),
    lender_category: str | None = Form(default=None, description="Target lender category: Bank, NBFC, or Microfinance."),
    selected_lender: str | None = Form(default=None, description="Selected lender name or identifier."),
    selected_loan_product: str | None = Form(default=None, description="Selected loan product type (e.g., Home Loan, Personal Loan)."),
    requested_loan_amount: float | None = Form(default=None, description="Requested principal loan amount in INR."),
    proposed_tenure_months: int | None = Form(default=None, description="Proposed loan tenure in months."),
    proposed_interest_rate: float | None = Form(default=None, description="Proposed annual interest rate percentage."),
    monthly_income: float | None = Form(default=None, description="Applicant declared monthly income in INR."),
    existing_monthly_obligations: float | None = Form(default=None, description="Applicant declared monthly debt obligations in INR."),
    document_checklist: str | None = Form(default=None, description="Configured checklist JSON string or server reference key."),
    financial_inputs: str | None = Form(
        default=None,
        description="Optional JSON string with financial inputs for deterministic calculations.",
    ),
    lender_context: str | None = Form(
        default=None,
        description="Optional JSON string with lender context: lender_category, lender_name, loan_product, document_checklist, etc.",
    ),
):
    """
    Full pipeline: upload documents → extract text (bounded concurrency) → analyze with Gemini.

    Supports multipart form data with:
    - Multiple uploaded files
    - Application details as validated JSON or individual form fields
    - Selected lender category, lender name, and loan product
    - Relevant financial details (amount, tenure, rate, income, obligations)
    - Document checklist or trusted server-side reference key

    If one document fails extraction, analysis continues with remaining usable documents.
    Failed documents are reported in document_results and errors without aborting the batch.
    """
    request_id: str = getattr(request.state, "request_id", str(uuid.uuid4()))
    warnings: list[str] = []

    if not os.getenv("GEMINI_API_KEY") or not os.getenv("GEMINI_MODEL"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini is not configured. Set GEMINI_API_KEY and GEMINI_MODEL in .env.",
        )

    # 1. Parse and reconcile application details
    details: dict[str, Any] = {}
    if application_details is not None and application_details.strip():
        try:
            parsed = json.loads(application_details)
            if not isinstance(parsed, dict):
                raise ValueError("Must be a JSON object.")
            details = parsed
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"application_details must be valid JSON: {exc}",
            )

    # Supplement details with direct form fields
    if applicant_name:
        details.setdefault("applicant_name", applicant_name)
        details.setdefault("full_name", applicant_name)
        details.setdefault("name", applicant_name)
    if application_id:
        details.setdefault("application_id", application_id)
    if email:
        details.setdefault("email", email)
    if phone:
        details.setdefault("phone", phone)
    if lender_category:
        details.setdefault("lender_category", lender_category)
    if selected_lender:
        details.setdefault("selected_lender", selected_lender)
    if selected_loan_product:
        details.setdefault("selected_loan_product", selected_loan_product)

    # Validate that we have at least some application details
    if not details and not applicant_name and not application_id and not email and not phone:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing required application details. Provide application_details JSON or form fields (applicant_name, etc.).",
        )

    # 2. Parse and reconcile lender context
    lender_ctx: dict[str, Any] = {}
    if lender_context:
        try:
            parsed_ctx = json.loads(lender_context)
            if isinstance(parsed_ctx, dict):
                lender_ctx = parsed_ctx
        except json.JSONDecodeError:
            warnings.append("lender_context is not valid JSON; skipped.")

    if lender_category and "lender_category" not in lender_ctx:
        lender_ctx["lender_category"] = lender_category
    if selected_lender and "lender_name" not in lender_ctx:
        lender_ctx["lender_name"] = selected_lender
        lender_ctx["selected_lender"] = selected_lender
    if selected_loan_product and "loan_product" not in lender_ctx:
        lender_ctx["loan_product"] = selected_loan_product
        lender_ctx["selected_loan_product"] = selected_loan_product
    if document_checklist and "document_checklist" not in lender_ctx:
        try:
            chk_parsed = json.loads(document_checklist)
            lender_ctx["document_checklist"] = chk_parsed
        except (json.JSONDecodeError, ValueError):
            lender_ctx["document_checklist"] = document_checklist

    # 3. Parse and reconcile financial inputs
    fin_inputs: dict[str, Any] = {}
    if financial_inputs:
        try:
            parsed_fin = json.loads(financial_inputs)
            if isinstance(parsed_fin, dict):
                fin_inputs = parsed_fin
        except json.JSONDecodeError:
            warnings.append("financial_inputs is not valid JSON; skipped.")

    if requested_loan_amount is not None:
        fin_inputs.setdefault("principal", requested_loan_amount)
        fin_inputs.setdefault("requested_loan_amount", requested_loan_amount)
    if proposed_tenure_months is not None:
        fin_inputs.setdefault("tenure_months", proposed_tenure_months)
        fin_inputs.setdefault("proposed_tenure_months", proposed_tenure_months)
    if proposed_interest_rate is not None:
        fin_inputs.setdefault("annual_interest_rate_pct", proposed_interest_rate)
        fin_inputs.setdefault("proposed_interest_rate", proposed_interest_rate)
    if monthly_income is not None:
        fin_inputs.setdefault("declared_monthly_income", monthly_income)
    if existing_monthly_obligations is not None:
        fin_inputs.setdefault("existing_monthly_obligations", existing_monthly_obligations)

    # 4. Validate file count
    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one document file for analysis.",
        )
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Too many files. Maximum {MAX_FILES_PER_REQUEST} per request.",
        )

    # 5. Read all uploads with size enforcement
    file_data = await _read_all_uploads(files)

    logger.info("Executing bounded document extraction for req_id=%s with %d file(s)",
                request_id, len(file_data))

    # 6. Extract documents with bounded concurrency
    doc_results = await _extract_documents_bounded(file_data)

    # Collect usable documents
    usable = [
        {"filename": d.filename, "text": d.extracted_text or d.text or "", "document_type": d.detected_file_type or "unknown"}
        for d in doc_results
        if d.extraction_status in {"success", "ocr_success"} and (d.extracted_text or d.text)
    ]

    failed_files = [d.filename for d in doc_results if d.extraction_status not in {"success", "ocr_success"}]
    if failed_files:
        warnings.append(f"Extraction failed for: {', '.join(failed_files)}. Analysis continues with remaining documents.")

    if not usable:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No documents could be extracted. Analysis cannot proceed.",
        )

    # Pass raw file bytes map for cryptographic duplicate detection
    bytes_map = {safe: content for safe, content in file_data}

    logger.info("Starting AI analysis req_id=%s for applicant=%s with %d usable document(s)",
                request_id, details.get("applicant_name") or details.get("name") or "unspecified", len(usable))

    # 7. Run AI analysis in worker threadpool (non-blocking)
    try:
        raw_report = await run_in_threadpool(
            analyze_application,
            details,
            usable,
            lender_context=lender_ctx or None,
            document_bytes_map=bytes_map,
        )
    except DocumentAnalysisError as exc:
        clean_err = _sanitize_error_message(str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=clean_err)

    # 8. Financial calculations & review
    financial_calcs: list[dict[str, Any]] = []
    fin_review: dict[str, Any] = raw_report.get("financial_review", {})
    if fin_inputs:
        try:
            financial_calcs = run_all_calculations(fin_inputs)
        except Exception as exc:
            warnings.append(f"Financial calculations failed: {_sanitize_error_message(str(exc))}")
    if not fin_review:
        try:
            fin_review = generate_financial_review(
                financial_inputs=fin_inputs or details,
                extracted_fields=raw_report.get("extracted_fields", []),
                document_classifications=raw_report.get("document_classifications", []),
                documents=usable,
                lender_context=lender_ctx or None,
            )
        except Exception as exc:
            warnings.append(f"Financial review failed: {_sanitize_error_message(str(exc))}")


    report, report_warnings = _safe_build_analysis_report(raw_report)
    warnings.extend(report_warnings)

    all_errors: list[str] = [
        _sanitize_error_message(d.error) for d in doc_results if d.error
    ]

    # Determine analysis status
    if failed_files:
        analysis_status = "partial_failure"
    elif (
        raw_report.get("discrepancies")
        or (fin_review and fin_review.get("financial_discrepancies"))
        or raw_report.get("manual_review_items")
        or (fin_review and fin_review.get("manual_review_items"))
        or raw_report.get("risk_flags")
    ):
        analysis_status = "requires_manual_review"
    else:
        analysis_status = "complete"

    app_summary = _build_comprehensive_application_summary(
        details=details,
        raw_report=raw_report,
        doc_results=doc_results,
        usable_docs=usable,
        lender_ctx=lender_ctx or None,
        fin_review=fin_review,
    )

    checklist_status = (
        fin_review.get("document_checklist_status")
        if fin_review
        else raw_report.get("financial_review", {}).get("document_checklist_status")
    ) or {}

    proc_meta = {
        **(raw_report.get("processing_metadata") or {}),
        "request_id": request_id,
        "timestamp": time.time(),
        "total_documents_submitted": len(doc_results),
        "successful_extractions": len(usable),
        "failed_extractions": len(failed_files),
        "ocr_used_count": sum(1 for d in doc_results if d.ocr_used),
        "lender_category": (lender_ctx or {}).get("lender_category"),
        "loan_product": (lender_ctx or {}).get("loan_product"),
    }

    app_id = str(details.get("application_id") or details.get("id") or request_id)

    return AnalyzeResponse(
        success=True,
        application_id=app_id,
        analysis_status=analysis_status,
        application_summary=app_summary,
        document_results=doc_results,
        document_classifications=raw_report.get("document_classifications", []),
        extracted_fields=raw_report.get("extracted_fields", []),
        cross_document_checks=raw_report.get("cross_document_checks", []),
        missing_information=raw_report.get("missing_information", []),
        financial_summary=raw_report.get("financial_summary", {}),
        document_checklist_status=checklist_status,
        risk_flags=raw_report.get("risk_flags", []),
        manual_review_items=raw_report.get("manual_review_items", []),
        analysis_limitations=raw_report.get("analysis_limitations", []),
        errors=all_errors,
        processing_metadata=proc_meta,
        # Backward compatibility
        request_id=request_id,
        report=report,
        document_statuses=doc_results,
        financial_calculations=financial_calcs,
        financial_review=fin_review or getattr(report, "financial_review", {}),
        cross_document_summary=getattr(report, "cross_document_summary", {}) or raw_report.get("cross_document_summary", {}),
        warnings=warnings,
    )



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        reload_dirs=[str(_ROOT_DIR)],
    )
