"""
Comprehensive integration test suite for the CREDORA AI Document Analysis API (Prompt 6).

Covers:
1. Single PDF document analysis
2. Multi-document analysis (PDF, DOCX, TXT combined)
3. Direct multipart form fields without JSON string (applicant_name, lender, product, finances)
4. Missing application details validation (422)
5. Malformed JSON metadata validation (422)
6. Unsupported file formats alone (422)
7. Partial extraction failure: 1 failed file among valid files continues with partial_failure status
8. Gemini unconfigured service unavailable (503)
9. Gemini upstream provider failure (502) with sanitized message
10. OCR unavailable graceful handling
11. Request limits: file count exceeded > 10 (422)
12. Request limits: single file size exceeded > 20MB (413)
13. Unexpected internal exceptions safe error handling (500 without leaking stack traces or paths)
14. Security: client-supplied checklist policy flagged as untrusted
15. Request-ID tracing via X-Request-ID header
16. Strict verification of all 16 unified response schema fields
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure ai-server is on sys.path
import sys
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parent.parent
_ROOT_DIR = _SERVER_DIR.parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

import app as app_module
from app import app
from document_analyzer import DocumentAnalysisError


# --------------------------------------------------------------------------- #
# Helpers & Fixtures                                                          #
# --------------------------------------------------------------------------- #

def make_sample_pdf(text: str = "Aadhaar Card: 1234-5678-9012 Name: Ramesh Kumar") -> bytes:
    """Generate minimal valid PDF bytes containing text extractable by pypdf."""
    stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("latin1")
    stream_len = len(stream)
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length " + str(stream_len).encode("ascii") + b" >> stream\n" +
        stream + b"\nendstream\nendobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000010 00000 n \n"
        b"0000000060 00000 n \n"
        b"0000000117 00000 n \n"
        b"0000000250 00000 n \n"
        b"0000000350 00000 n \n"
        b"trailer << /Size 6 /Root 1 0 R >>\n"
        b"startxref\n450\n%%EOF\n"
    )


def make_sample_docx(text: str = "Salary Certificate: Rs. 75,000 per month") -> bytes:
    """Generate minimal DOCX bytes."""
    import docx
    doc = docx.Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


_MOCK_REPORT = {
    "application_summary": {
        "overview": "Applicant demonstrates stable verified employment and documented income.",
        "applicant_name": "Ramesh Kumar",
        "declared_income": 75000,
    },
    "document_classifications": [
        {"filename": "identity.pdf", "document_type": "Identity proof", "confidence": "HIGH"},
        {"filename": "salary.docx", "document_type": "Salary slip", "confidence": "HIGH"},
        {"filename": "bank.txt", "document_type": "Bank statement", "confidence": "HIGH"},
    ],
    "extracted_fields": [
        {"field_name": "applicant_name", "extracted_value": "Ramesh Kumar", "source_file": "identity.pdf"},
        {"field_name": "monthly_salary", "extracted_value": "75000", "source_file": "salary.docx"},
    ],
    "cross_document_checks": [
        {
            "check_name": "Identity Consistency Check",
            "status": "consistent",
            "description": "Applicant name 'Ramesh Kumar' is consistent across submitted records.",
        }
    ],
    "cross_document_summary": {
        "verified_fields": ["applicant_name"],
        "critical_discrepancies": [],
    },
    "discrepancies": [],
    "missing_information": [],
    "financial_summary": {
        "declared_monthly_income": 75000,
        "evidenced_net_salary": 72000,
    },
    "risk_flags": [],
    "manual_review_items": [],
    "analysis_limitations": ["Physical in-person address verification was not conducted."],
    "processing_metadata": {
        "model": "gemini-test-2.5",
        "analysis_version": "1.0",
    },
    "overall_status": "complete",
}


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test_key_for_integration")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)



# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #

class TestPrompt6ApiIntegration:

    def test_single_pdf_analysis_and_unified_schema(self, client):
        """Test analyzing a single PDF file and verify all 16 unified fields exist with valid types."""
        pdf_bytes = make_sample_pdf("Aadhaar: 1234-5678-9012 Name: Ramesh Kumar")

        with patch("app.analyze_application", return_value=_MOCK_REPORT):
            r = client.post(
                "/analyze",
                data={
                    "application_details": json.dumps({
                        "application_id": "APP-2026-001",
                        "applicant_name": "Ramesh Kumar",
                        "lender_category": "Bank",
                        "selected_loan_product": "Home Loan",
                    })
                },
                files=[("files", ("identity.pdf", pdf_bytes, "application/pdf"))],
            )

        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()

        # 16 Unified Response Fields
        assert data["success"] is True
        assert data["application_id"] == "APP-2026-001"
        assert data["analysis_status"] in {"complete", "requires_manual_review", "partial_failure"}
        assert isinstance(data["application_summary"], dict)
        assert "what_was_analyzed" in data["application_summary"]
        assert "what_matched" in data["application_summary"]
        assert "what_was_missing" in data["application_summary"]
        assert "inconsistencies_requiring_attention" in data["application_summary"]
        assert "what_could_not_be_assessed" in data["application_summary"]
        assert "recommended_action" in data["application_summary"]

        assert isinstance(data["document_results"], list)
        assert len(data["document_results"]) == 1
        assert data["document_results"][0]["filename"] == "identity.pdf"
        assert data["document_results"][0]["extraction_status"] == "success"

        assert isinstance(data["document_classifications"], list)
        assert isinstance(data["extracted_fields"], list)
        assert isinstance(data["cross_document_checks"], list)
        assert isinstance(data["missing_information"], list)
        assert isinstance(data["financial_summary"], dict)
        assert isinstance(data["document_checklist_status"], dict)
        assert isinstance(data["risk_flags"], list)
        assert isinstance(data["manual_review_items"], list)
        assert isinstance(data["analysis_limitations"], list)
        assert isinstance(data["errors"], list)
        assert isinstance(data["processing_metadata"], dict)

        # Backward compatibility fields
        assert "report" in data
        assert "document_statuses" in data
        assert "request_id" in data

    def test_multi_document_analysis(self, client):
        """Test analyzing a batch of different valid file formats (PDF, DOCX, TXT) simultaneously."""
        pdf_bytes = make_sample_pdf("Aadhaar Card: 1234-5678-9012 Ramesh Kumar")
        docx_bytes = make_sample_docx("Salary slip for Ramesh Kumar. Net salary: 75000 INR.")
        txt_bytes = b"Bank Statement: Ramesh Kumar. Monthly credit 75000 INR from TechCorp."

        with patch("app.analyze_application", return_value=_MOCK_REPORT):
            r = client.post(
                "/analyze",
                data={
                    "application_details": json.dumps({
                        "application_id": "APP-MULTI-99",
                        "applicant_name": "Ramesh Kumar",
                        "monthly_income": 75000,
                    }),
                    "lender_context": json.dumps({
                        "lender_category": "Bank",
                        "loan_product": "Home Loan",
                    }),
                },
                files=[
                    ("files", ("identity.pdf", pdf_bytes, "application/pdf")),
                    ("files", ("salary.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
                    ("files", ("bank.txt", txt_bytes, "text/plain")),
                ],
            )

        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert len(data["document_results"]) == 3
        filenames = [d["filename"] for d in data["document_results"]]
        assert "identity.pdf" in filenames
        assert "salary.docx" in filenames
        assert "bank.txt" in filenames
        assert all(d["extraction_status"] == "success" for d in data["document_results"])

    def test_direct_form_fields_without_json_string(self, client):
        """Test submitting application metadata via direct form fields instead of a JSON string."""
        txt_bytes = b"Bank statement for Sunil Sharma. Regular salary credits Rs. 90,000."

        with patch("app.analyze_application", return_value=_MOCK_REPORT):
            r = client.post(
                "/analyze",
                data={
                    "applicant_name": "Sunil Sharma",
                    "application_id": "APP-FORM-101",
                    "email": "sunil@example.com",
                    "phone": "9876543210",
                    "lender_category": "Bank",
                    "selected_lender": "State Bank of India",
                    "selected_loan_product": "Home Loan",
                    "requested_loan_amount": 2500000,
                    "proposed_tenure_months": 240,
                    "proposed_interest_rate": 8.5,
                    "monthly_income": 90000,
                    "existing_monthly_obligations": 15000,
                    "document_checklist": "bank_home_loan",
                },
                files=[("files", ("bank.txt", txt_bytes, "text/plain"))],
            )

        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["application_id"] == "APP-FORM-101"
        assert data["processing_metadata"]["lender_category"] == "Bank"
        assert data["processing_metadata"]["loan_product"] == "Home Loan"
        # Financial review was run
        assert "proposed_emi" in data["financial_review"] or "proposed_emi" in data["financial_summary"]
        # Document checklist status resolved server-side
        assert data["document_checklist_status"]["checklist_name"] is not None

    def test_missing_application_fields_rejected(self, client):
        """Requests with neither application_details JSON nor direct form fields must return 422."""
        r = client.post(
            "/analyze",
            data={},
            files=[("files", ("doc.txt", b"Sample text", "text/plain"))],
        )
        assert r.status_code == 422
        assert "Missing required application details" in r.text or "application_details" in r.text

    def test_invalid_json_metadata_rejected(self, client):
        """Malformed application_details JSON must be rejected with 422."""
        r = client.post(
            "/analyze",
            data={"application_details": "invalid json {{{ missing closing braces"},
            files=[("files", ("doc.txt", b"Sample text", "text/plain"))],
        )
        assert r.status_code == 422
        assert "must be valid JSON" in r.json()["detail"]

    def test_unsupported_files_alone_rejected(self, client):
        """When only unsupported files are submitted, request must fail with 422."""
        r = client.post(
            "/analyze",
            data={"applicant_name": "Test User"},
            files=[("files", ("unsupported.exe", b"MZBinaryData", "application/octet-stream"))],
        )
        assert r.status_code == 422
        assert "No documents could be extracted" in r.json()["detail"]

    def test_one_failed_file_among_valid_files_continues(self, client):
        """A single unreadable or invalid file must not discard results from valid files."""
        pdf_bytes = make_sample_pdf("Valid PDF document for loan application.")
        empty_bytes = b""
        unsupported_bytes = b"EXEBinaryExecutable"

        with patch("app.analyze_application", return_value=_MOCK_REPORT):
            r = client.post(
                "/analyze",
                data={"applicant_name": "Resilient Applicant"},
                files=[
                    ("files", ("good.pdf", pdf_bytes, "application/pdf")),
                    ("files", ("empty.txt", empty_bytes, "text/plain")),
                    ("files", ("bad.exe", unsupported_bytes, "application/octet-stream")),
                ],
            )

        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["analysis_status"] == "partial_failure"
        assert len(data["document_results"]) == 3
        assert len(data["errors"]) >= 2
        # Good file succeeded
        good_doc = next(d for d in data["document_results"] if d["filename"] == "good.pdf")
        assert good_doc["extraction_status"] == "success"
        # Warning captures failed files
        assert any("empty.txt" in w or "bad.exe" in w for w in data["warnings"])

    def test_gemini_unavailable_503(self, monkeypatch):
        """When Gemini environment variables are absent, return 503."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post(
            "/analyze",
            data={"applicant_name": "Test User"},
            files=[("files", ("test.txt", b"Valid text", "text/plain"))],
        )
        assert r.status_code == 503
        assert "Gemini is not configured" in r.json()["detail"]


    def test_gemini_upstream_failure_502(self, client):
        """When Gemini raises DocumentAnalysisError, return 502 with sanitized message."""
        with patch("app.analyze_application", side_effect=DocumentAnalysisError("Resource exhausted: Rate limit exceeded C:\\internal\\secret\\path")):
            r = client.post(
                "/analyze",
                data={"applicant_name": "Test User"},
                files=[("files", ("test.txt", b"Valid text", "text/plain"))],
            )
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert "Rate limit exceeded" in detail
        assert "C:\\internal" not in detail  # path stripped!

    def test_ocr_unavailable_reported(self, client):
        """When scanned PDF arrives and OCR is unavailable, status is reported gracefully."""
        def mock_extract(filename, content):
            if filename.endswith(".pdf"):
                raise DocumentAnalysisError("no selectable text found. It may be scanned")
            return "Monthly salary: 60000"

        with patch("app.extract_text", side_effect=mock_extract), \
             patch("app.ocr_pdf", return_value={"text": "", "extraction_status": "ocr_unavailable", "page_count": None, "error": "OCR not installed"}), \
             patch("app.analyze_application", return_value=_MOCK_REPORT):

            # One scanned PDF + one text document
            r = client.post(
                "/analyze",
                data={"applicant_name": "Test Applicant"},
                files=[
                    ("files", ("scanned.pdf", b"%PDF-1.4 minimal", "application/pdf")),
                    ("files", ("salary.txt", b"Monthly salary: 60000", "text/plain")),
                ],
            )
            assert r.status_code == 200
            data = r.json()
            scanned_res = next(d for d in data["document_results"] if d["filename"] == "scanned.pdf")
            assert scanned_res["extraction_status"] == "ocr_unavailable"


    def test_request_limits_file_count_exceeded(self, client):
        """Submitting more than 10 files must be rejected with 422."""
        files = [("files", (f"file_{i}.txt", b"content", "text/plain")) for i in range(11)]
        r = client.post(
            "/analyze",
            data={"applicant_name": "Test User"},
            files=files,
        )
        assert r.status_code == 422
        assert "Too many files" in r.json()["detail"]

    def test_request_limits_file_size_exceeded(self, client):
        """Submitting a file exceeding MAX_FILE_SIZE_MB (20MB) must be rejected."""
        oversized = b"0" * (21 * 1024 * 1024)  # 21 MB
        r = client.post(
            "/analyze",
            data={"applicant_name": "Test User"},
            files=[("files", ("large.txt", oversized, "text/plain"))],
        )
        assert r.status_code in {413, 422, 400}

    def test_unexpected_internal_exception_safe_500(self, client):
        """Unhandled exceptions must return 500 without leaking stack traces or internal paths."""
        with patch("app._extract_documents_bounded", side_effect=RuntimeError("Secret database connection string in C:\\app\\secret.py failed!")):
            r = client.post(
                "/analyze",
                data={"applicant_name": "Test User"},
                files=[("files", ("test.txt", b"text", "text/plain"))],
            )
        assert r.status_code == 500
        data = r.json()
        assert data["success"] is False
        assert "internal server error occurred" in data["error"].lower()
        assert "secret" not in json.dumps(data).lower()
        assert "C:\\" not in json.dumps(data)
        assert "Traceback" not in json.dumps(data)

    def test_client_supplied_checklist_untrusted_provenance(self, client):
        """Arbitrary client-supplied checklist policies must never masquerade as trusted lender rules."""
        arbitrary_client_rules = [
            {"item_id": "rule_1", "document_name": "Custom VIP Pass", "required": True}
        ]

        with patch("app.analyze_application", return_value=_MOCK_REPORT):
            r = client.post(
                "/analyze",
                data={
                    "applicant_name": "VIP Applicant",
                    "document_checklist": json.dumps(arbitrary_client_rules),
                },
                files=[("files", ("id.txt", b"ID document", "text/plain"))],
            )
        assert r.status_code == 200
        data = r.json()
        chk = data["document_checklist_status"]
        assert chk["is_trusted_lender_policy"] is False
        assert any("untrusted" in str(p).lower() or "security" in str(p).lower() for p in chk.get("policy_notes", []))

    def test_request_id_tracing_headers(self, client):
        """X-Request-ID sent by client must be echoed back in header and response body."""
        custom_id = "trace-req-uuid-987654"
        with patch("app.analyze_application", return_value=_MOCK_REPORT):
            r = client.post(
                "/analyze",
                headers={"X-Request-ID": custom_id},
                data={"applicant_name": "Trace Applicant"},
                files=[("files", ("test.txt", b"Test content", "text/plain"))],
            )
        assert r.status_code == 200
        assert r.headers.get("X-Request-ID") == custom_id
        assert r.json()["request_id"] == custom_id

    def test_analyze_text_endpoint_returns_unified_response(self, client):
        """The /analyze/text endpoint must also return the identical 16-field unified schema."""
        req_body = {
            "application_details": {"applicant_name": "Pre-extracted Applicant", "application_id": "APP-TEXT-1"},
            "documents": [{"filename": "doc1.txt", "text": "Extracted text content"}],
        }
        with patch("app.analyze_application", return_value=_MOCK_REPORT):
            r = client.post("/analyze/text", json=req_body)

        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["application_id"] == "APP-TEXT-1"
        assert "what_was_analyzed" in data["application_summary"]
        assert "document_results" in data
        assert "document_checklist_status" in data
        assert "errors" in data
        assert "processing_metadata" in data

    def test_end_to_end_hackathon_demo_scenario(self, client):
        """
        Verify the live hackathon demonstration scenario:
        - Synthetic multi-file submission (PDF Aadhaar, DOCX Salary Slip, TXT Utility Bill)
        - Deliberately inconsistent address: Pune vs Navi Mumbai
        - Deliberately missing mandatory document: 6-month Bank Statement under bank_home_loan checklist
        - Deliberate financial discrepancy: Declared income (Rs 95,000) exceeds documented salary (Rs 60,000)
        - Verify report flags issues, cites evidence, preserves all docs, and demands manual underwriter review.
        """
        pdf_aadhaar = make_sample_pdf("GOVERNMENT OF INDIA Aadhaar Card: 9988-7766-1234 Name: Rajesh V Patel Address: Flat 402, Shanti Heights, Pune, Maharashtra 411001")
        docx_salary = make_sample_docx("Apex InfoTech Pvt Ltd Salary Slip for Rajesh Patel. Gross: Rs. 60,000. Net: Rs. 54,000.")
        txt_utility = b"MSEDCL Electricity Bill. Consumer: Rajesh Patel. Address: B-12, Sector 5, Vashi, Navi Mumbai, Maharashtra 400703."

        demo_mock_report = {
            "application_summary": {
                "overview": "Applicant has submitted proof of identity and salary evidence, but critical cross-document address variances and mandatory document omissions were detected.",
                "applicant_name": "Rajesh V Patel",
                "declared_income": 95000,
            },
            "document_classifications": [
                {"filename": "aadhaar.pdf", "document_type": "Identity proof", "confidence": "HIGH"},
                {"filename": "salary_slip.docx", "document_type": "Salary slip", "confidence": "HIGH"},
                {"filename": "electricity_bill.txt", "document_type": "Address proof", "confidence": "HIGH"},
            ],
            "extracted_fields": [
                {"field_name": "applicant_name", "extracted_value": "Rajesh V Patel", "source_file": "aadhaar.pdf"},
                {"field_name": "address", "extracted_value": "Pune, Maharashtra 411001", "source_file": "aadhaar.pdf"},
                {"field_name": "address", "extracted_value": "Vashi, Navi Mumbai, Maharashtra 400703", "source_file": "electricity_bill.txt"},
                {"field_name": "monthly_salary", "extracted_value": "60000", "source_file": "salary_slip.docx"},
            ],
            "cross_document_checks": [
                {
                    "check_name": "Cross-Document Address Verification",
                    "status": "mismatch",
                    "description": "Primary address discrepancy: Aadhaar indicates Pune (411001) while electricity bill indicates Navi Mumbai (400703).",
                }
            ],
            "cross_document_summary": {
                "verified_fields": ["applicant_name"],
                "critical_discrepancies": ["address_mismatch"],
            },
            "discrepancies": [
                {
                    "field_name": "residential_address",
                    "rule_or_check": "cross_document_address_reconciliation",
                    "explanation": "Conflicting city/pincode between Aadhaar (Pune 411001) and Utility Bill (Navi Mumbai 400703).",
                    "severity": "high",
                }
            ],
            "missing_information": [
                "6-Month Bank Statement required for Bank Home Loan verification."
            ],
            "financial_summary": {
                "declared_monthly_income": 95000,
                "evidenced_gross_salary": 60000,
            },
            "risk_flags": [
                "Address mismatch between primary KYC and utility bill.",
                "Declared income (95,000) exceeds documented gross salary (60,000) by >58%."
            ],
            "manual_review_items": [
                {"item": "address_clarification", "reason": "Clarify current residential address with applicant.", "severity": "high"}
            ],
            "analysis_limitations": [
                "AI analysis does not replace physical inspection or field investigation."
            ],
            "processing_metadata": {
                "model": "gemini-2.5-flash",
            },
            "overall_status": "review_required",
        }

        with patch("app.analyze_application", return_value=demo_mock_report):
            r = client.post(
                "/analyze",
                data={
                    "applicant_name": "Rajesh V Patel",
                    "application_id": "APP-DEMO-2026",
                    "lender_category": "Bank",
                    "selected_loan_product": "Home Loan",
                    "requested_loan_amount": 3500000,
                    "proposed_tenure_months": 240,
                    "proposed_interest_rate": 8.75,
                    "monthly_income": 95000,
                    "document_checklist": "bank_home_loan",
                },
                files=[
                    ("files", ("aadhaar.pdf", pdf_aadhaar, "application/pdf")),
                    ("files", ("salary_slip.docx", docx_salary, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
                    ("files", ("electricity_bill.txt", txt_utility, "text/plain")),
                ],
            )

        assert r.status_code == 200
        data = r.json()

        # 1. Verification of top-level response
        assert data["success"] is True
        assert data["application_id"] == "APP-DEMO-2026"
        assert data["analysis_status"] == "requires_manual_review"

        # 2. All 3 documents preserved
        assert len(data["document_results"]) == 3
        filenames = [d["filename"] for d in data["document_results"]]
        assert "aadhaar.pdf" in filenames
        assert "salary_slip.docx" in filenames
        assert "electricity_bill.txt" in filenames

        # 3. Application summary identifies issues and requests manual review
        app_sum = data["application_summary"]
        assert app_sum["recommended_action"] == "manual_review_required"
        assert len(app_sum["inconsistencies_requiring_attention"]) > 0
        assert any("address" in str(i).lower() or "income" in str(i).lower() for i in app_sum["inconsistencies_requiring_attention"])

        # 4. Missing required document identified (Bank Statement)
        chk = data["document_checklist_status"]
        missing_docs = [m["document_name"].lower() for m in chk.get("missing_required_documents", []) if isinstance(m, dict)]
        assert any("bank statement" in d or "bank" in d for d in missing_docs)

        # 5. Financial discrepancy detected (95k vs 60k)
        assert len(data["risk_flags"]) > 0
        assert any("95" in str(rf) or "income" in str(rf).lower() or "address" in str(rf).lower() for rf in data["risk_flags"])

