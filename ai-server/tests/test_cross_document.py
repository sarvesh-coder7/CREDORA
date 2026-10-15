"""
Test Suite for Prompt 4: Cross-Document Consistency and Address Verification.

Verifies:
1. Identity consistency: exact matches, case variations, initials, reordered names, phonetic typos.
2. Address consistency: abbreviations, PIN code extraction, partial matches, current vs permanent addresses.
3. Date & employment: corporate suffixes, acronyms, recency windows (90d, 180d), expired IDs.
4. Financial consistency: declared vs gross/net salary, bank credit reconciliation, account holder names.
5. Duplicate & contradictory documents: SHA-256 duplicate detection, overlapping periods.
6. Inconclusive cases: ensuring inconclusive is returned rather than false mismatches.
7. Masking: sensitive numbers (Aadhaar, PAN, card, bank accounts) masked in all checks.
8. API endpoint integration: /analyze/text and /analyze returning consolidated cross-document report.
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cross_document_verifier import (
    CrossDocConfig,
    CrossDocumentVerifier,
    mask_sensitive_value,
    normalize_address,
    normalize_company_name,
    normalize_name,
    verify_cross_document_consistency,
)
from document_analyzer import analyze_application
from app import app


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "mock_gemini_key_for_testing")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ── 1. Identity Consistency Tests ─────────────────────────────────────────────

class TestIdentityConsistency:
    def test_exact_and_case_insensitive_match(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_identity(
            app_name="Arjun Sharma",
            doc_names=[{"value": "arjun sharma", "source_document": "Aadhaar.pdf", "page_number": 1}],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "exact_match"
        assert checks[0].severity == "info"
        assert checks[0].requires_manual_review is False

    def test_name_order_variation(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_identity(
            app_name="Arjun Sharma",
            doc_names=[{"value": "Sharma Arjun", "source_document": "SalarySlip.pdf", "page_number": 1}],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "normalized_match"
        assert checks[0].severity == "info"
        assert checks[0].requires_manual_review is False

    def test_honorifics_and_punctuation_stripping(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_identity(
            app_name="Mr. Arjun K. Sharma",
            doc_names=[{"value": "Arjun K Sharma", "source_document": "Passport.pdf", "page_number": 1}],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "normalized_match"
        assert checks[0].severity == "info"

    def test_initials_matching(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_identity(
            app_name="Arjun Kumar Sharma",
            doc_names=[{"value": "A. K. Sharma", "source_document": "BankStatement.pdf", "page_number": 1}],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "partial_match"
        assert checks[0].severity == "low"
        assert "abbreviated initial" in checks[0].explanation.lower()

    def test_middle_name_omission(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_identity(
            app_name="Arjun Kumar Sharma",
            doc_names=[{"value": "Arjun Sharma", "source_document": "VoterID.pdf", "page_number": 1}],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "partial_match"
        assert checks[0].severity == "low"
        assert "middle name" in checks[0].explanation.lower()

    def test_ambiguous_phonetic_typo_requires_manual_review(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_identity(
            app_name="Arjun Sharma",
            doc_names=[{"value": "Arun Sharma", "source_document": "UtilityBill.pdf", "page_number": 1}],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "ambiguous_match"
        assert checks[0].severity == "medium"
        assert checks[0].requires_manual_review is True
        assert "does not prove identity" in checks[0].explanation.lower()

    def test_distinct_names_potential_mismatch(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_identity(
            app_name="Arjun Sharma",
            doc_names=[{"value": "Ramesh Gupta", "source_document": "Form16.pdf", "page_number": 1}],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "potential_mismatch"
        assert checks[0].severity == "high"
        assert checks[0].requires_manual_review is True


# ── 2. Address Consistency Tests ──────────────────────────────────────────────

class TestAddressConsistency:
    def test_address_abbreviations_normalized_match(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_addresses(
            app_addresses={"address": "Flat 402, Lotus Apts, M.G. Rd, Bengaluru - 560001"},
            doc_addresses=[{
                "value": "402 Lotus Apartments, MG Road, Bangalore 560001",
                "source_document": "ElectricityBill.pdf",
                "page_number": 1,
            }],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "normalized_match"
        assert checks[0].severity == "info"
        assert checks[0].requires_manual_review is False
        assert "geocoded" in checks[0].explanation.lower()

    def test_legitimate_current_vs_permanent_address_difference(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_addresses(
            app_addresses={
                "current_address": "Flat 201, Indiranagar, Bengaluru, Karnataka 560038",
                "permanent_address": "House 12, Civil Lines, Jaipur, Rajasthan 302006",
            },
            doc_addresses=[
                {
                    "field_name": "permanent_address",
                    "value": "House 12, Civil Lines, Jaipur 302006",
                    "source_document": "Aadhaar.pdf",
                    "page_number": 1,
                },
                {
                    "field_name": "current_address",
                    "value": "Flat 201, Indiranagar, Bengaluru 560038",
                    "source_document": "RentalAgreement.pdf",
                    "page_number": 1,
                },
            ],
        )
        # Verify declared current vs permanent is recognized as legitimate difference
        legit_checks = [c for c in checks if c.match_status == "legitimate_difference"]
        assert len(legit_checks) >= 1
        assert legit_checks[0].severity == "info"
        assert legit_checks[0].requires_manual_review is False
        assert "permissible" in legit_checks[0].explanation.lower()

    def test_postal_pin_code_mismatch(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_addresses(
            app_addresses={"current_address": "Flat 101, Green Glen Layout, Bengaluru 560103"},
            doc_addresses=[{
                "value": "Flat 101, Green Glen Layout, Bengaluru 560001",
                "source_document": "BankStatement.pdf",
                "page_number": 1,
            }],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "potential_mismatch"
        assert checks[0].severity == "high"
        assert checks[0].requires_manual_review is True
        assert "pin code mismatch" in checks[0].explanation.lower()

    def test_partial_address_match(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_addresses(
            app_addresses={"address": "Plot 55, Sector 14, Gurugram, Haryana 122001"},
            doc_addresses=[{
                "value": "Sector 14, Gurugram 122001",
                "source_document": "WaterBill.pdf",
                "page_number": 1,
            }],
        )
        assert len(checks) == 1
        assert checks[0].match_status in {"normalized_match", "partial_match"}
        assert checks[0].requires_manual_review is False

    def test_inconclusive_address_on_sparse_tokens(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_addresses(
            app_addresses={"address": "Flat 303, Sunrise Apts, Whitefield, Bengaluru 560066"},
            doc_addresses=[{
                "value": "Bengaluru",
                "source_document": "UnstructuredSlip.pdf",
                "page_number": 1,
            }],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "inconclusive"
        assert checks[0].severity == "info"


# ── 3. Date & Employment Consistency Tests ────────────────────────────────────

class TestEmploymentAndDateConsistency:
    def test_employer_corporate_suffix_normalized_match(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_dates_and_employment(
            app_details={"employer_name": "Infosys Limited"},
            doc_employers=[{"value": "Infosys Pvt Ltd", "source_document": "SalarySlip.pdf", "page_number": 1}],
            doc_dates=[],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "normalized_match"
        assert checks[0].severity == "info"
        assert checks[0].requires_manual_review is False

    def test_corporate_acronym_correspondence(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_dates_and_employment(
            app_details={"employer_name": "Tata Consultancy Services"},
            doc_employers=[{"value": "TCS", "source_document": "SalarySlip.pdf", "page_number": 1}],
            doc_dates=[],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "partial_match"
        assert checks[0].requires_manual_review is True
        assert "acronym" in checks[0].explanation.lower()

    def test_conflicting_employer_potential_mismatch(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_dates_and_employment(
            app_details={"employer_name": "Google India Pvt Ltd"},
            doc_employers=[{"value": "Microsoft India", "source_document": "SalarySlip.pdf", "page_number": 1}],
            doc_dates=[],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "potential_mismatch"
        assert checks[0].severity == "high"
        assert checks[0].requires_manual_review is True

    def test_document_recency_within_validity_window(self):
        verifier = CrossDocumentVerifier()
        recent_date = (date.today() - timedelta(days=20)).isoformat()
        checks = verifier.compare_dates_and_employment(
            app_details={"application_date": date.today().isoformat()},
            doc_employers=[],
            doc_dates=[{
                "field_name": "document_date",
                "value": recent_date,
                "source_document": "SalarySlip.pdf",
                "document_category": "Salary slip",
                "page_number": 1,
            }],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "normalized_match"
        assert checks[0].severity == "info"

    def test_document_recency_exceeds_configured_window(self):
        verifier = CrossDocumentVerifier()
        old_date = (date.today() - timedelta(days=120)).isoformat()  # > 90 days
        checks = verifier.compare_dates_and_employment(
            app_details={"application_date": date.today().isoformat()},
            doc_employers=[],
            doc_dates=[{
                "field_name": "document_date",
                "value": old_date,
                "source_document": "SalarySlip.pdf",
                "document_category": "Salary slip",
                "page_number": 1,
            }],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "potential_mismatch"
        assert checks[0].severity == "medium"
        assert checks[0].requires_manual_review is True
        assert "exceeding" in checks[0].explanation.lower()

    def test_expired_identity_document(self):
        verifier = CrossDocumentVerifier()
        past_expiry = (date.today() - timedelta(days=30)).isoformat()
        checks = verifier.compare_dates_and_employment(
            app_details={"application_date": date.today().isoformat()},
            doc_employers=[],
            doc_dates=[{
                "field_name": "expiry_date",
                "value": past_expiry,
                "source_document": "Passport.pdf",
                "page_number": 1,
            }],
        )
        assert len(checks) == 1
        assert checks[0].match_status == "potential_mismatch"
        assert checks[0].severity == "high"
        assert checks[0].requires_manual_review is True
        assert "expired" in checks[0].explanation.lower()


# ── 4. Financial Consistency Tests ────────────────────────────────────────────

class TestFinancialConsistency:
    def test_declared_income_matches_gross_salary(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_financials(
            app_income=80000.0,
            salary_slip_figures={
                "gross_salary": "82000",
                "net_salary": "68000",
                "source_document": "SalarySlip.pdf",
                "page_number": 1,
            },
            bank_statement_figures=None,
        )
        assert len(checks) == 1
        assert checks[0].match_status == "normalized_match"
        assert checks[0].severity == "info"
        assert "gross salary" in checks[0].explanation.lower()

    def test_declared_income_matches_net_take_home_salary(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_financials(
            app_income=68000.0,
            salary_slip_figures={
                "gross_salary": "85000",
                "net_salary": "68000",
                "source_document": "SalarySlip.pdf",
                "page_number": 1,
            },
            bank_statement_figures=None,
        )
        assert len(checks) == 1
        assert checks[0].match_status == "normalized_match"
        assert checks[0].severity == "info"
        assert "net take-home" in checks[0].explanation.lower()

    def test_declared_income_significantly_exceeds_documented_gross(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_financials(
            app_income=150000.0,  # +87.5% over gross
            salary_slip_figures={
                "gross_salary": "80000",
                "net_salary": "65000",
                "source_document": "SalarySlip.pdf",
                "page_number": 1,
            },
            bank_statement_figures=None,
        )
        assert len(checks) == 1
        assert checks[0].match_status == "potential_mismatch"
        assert checks[0].severity == "high"
        assert checks[0].requires_manual_review is True

    def test_salary_slip_net_matches_bank_salary_credit(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_financials(
            app_income=75000.0,
            salary_slip_figures={"gross_salary": "90000", "net_salary": "74800", "source_document": "SalarySlip.pdf"},
            bank_statement_figures={"salary_credit_amount": "74800", "source_document": "BankStatement.pdf"},
        )
        credit_checks = [c for c in checks if "bank_salary_credit" in c.fields_compared]
        assert len(credit_checks) == 1
        assert credit_checks[0].match_status == "exact_match"
        assert credit_checks[0].severity == "info"

    def test_bank_statement_account_holder_match(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.compare_financials(
            app_income=50000.0,
            salary_slip_figures={"applicant_name": "Priya Verma"},
            bank_statement_figures={"account_holder_name": "Priya Verma", "source_document": "BankStatement.pdf"},
        )
        holder_checks = [c for c in checks if "account_holder_name" in c.fields_compared]
        assert len(holder_checks) == 1
        assert holder_checks[0].match_status == "normalized_match"
        assert holder_checks[0].severity == "info"


# ── 5. Duplicate & Contradictory Document Tests ───────────────────────────────

class TestDuplicateAndContradictoryDocuments:
    def test_duplicate_file_content_hash(self):
        verifier = CrossDocumentVerifier()
        payload = b"Sample identical PDF document content for duplicate check testing"
        checks = verifier.detect_duplicates_and_contradictions(
            documents=[
                {"filename": "Statement_Copy1.pdf", "text": "Content A"},
                {"filename": "Statement_Copy2.pdf", "text": "Content A"},
            ],
            document_bytes_map={
                "Statement_Copy1.pdf": payload,
                "Statement_Copy2.pdf": payload,
            },
        )
        dup_checks = [c for c in checks if c.match_status == "duplicate_detected"]
        assert len(dup_checks) == 1
        assert dup_checks[0].severity == "medium"
        assert dup_checks[0].requires_manual_review is True
        assert "sha-256" in dup_checks[0].explanation.lower()

    def test_overlapping_statement_period(self):
        verifier = CrossDocumentVerifier()
        checks = verifier.detect_duplicates_and_contradictions(
            documents=[
                {"filename": "BankStatement_Q2_v1.pdf", "statement_period": "2026-04-01 to 2026-06-30"},
                {"filename": "BankStatement_Q2_v2.pdf", "statement_period": "2026-04-01 to 2026-06-30"},
            ],
        )
        period_checks = [c for c in checks if c.match_status == "overlapping_period_conflict"]
        assert len(period_checks) == 1
        assert period_checks[0].severity == "medium"
        assert period_checks[0].requires_manual_review is True


# ── 6. Sensitive Data Masking Tests ───────────────────────────────────────────

class TestSensitiveDataMasking:
    def test_aadhaar_and_pan_masked_in_all_checks(self):
        raw_text = "Applicant Aadhaar is 2345 6789 0123 and PAN is ABCDE1234F with Account 987654321098."
        masked = mask_sensitive_value(raw_text)
        assert "2345 6789 0123" not in masked
        assert "XXXX-XXXX-0123" in masked
        assert "ABCDE1234F" not in masked
        assert "XXXXX1234F" in masked

    def test_report_observed_values_masked(self):
        res = verify_cross_document_consistency(
            application_details={"applicant_name": "Arjun Sharma"},
            extracted_fields=[
                {"field_name": "aadhaar_number", "observed_value": "3456 7890 1234", "source_document": "Aadhaar.pdf"},
                {"field_name": "pan_number", "observed_value": "BKZPS1234K", "source_document": "PAN.pdf"},
            ],
            documents=[{"filename": "Aadhaar.pdf"}, {"filename": "PAN.pdf"}],
        )
        json_dump = json.dumps(res)
        assert "3456 7890 1234" not in json_dump
        assert "BKZPS1234K" not in json_dump
        assert "XXXX-XXXX-1234" in json_dump
        assert "XXXXX1234K" in json_dump


# ── 7. Top-Level Integration Tests ───────────────────────────────────────────

class TestCrossDocumentTopLevelIntegration:
    @patch("document_analyzer._call_gemini_with_retries")
    def test_analyze_application_incorporates_cross_doc_checks(self, mock_gemini):
        mock_gemini.return_value = json.dumps({
            "application_summary": {"applicant_name": "Arjun Sharma"},
            "extracted_fields": [
                {"field_name": "full_name", "observed_value": "Arjun Sharma", "source_document": "SalarySlip.pdf"},
                {"field_name": "address", "observed_value": "MG Road, Bengaluru 560001", "source_document": "Utility.pdf"},
                {"field_name": "gross_salary", "observed_value": "85000", "source_document": "SalarySlip.pdf"},
            ],
            "document_results": [],
            "document_classifications": [],
            "discrepancies": [],
            "missing_information": [],
            "financial_summary": {},
            "risk_flags": [],
            "manual_review_items": [],
            "analysis_limitations": [],
            "overall_status": "complete",
        })

        report = analyze_application(
            details={
                "applicant_name": "Arjun Sharma",
                "address": "MG Rd, Bangalore 560001",
                "monthly_income": 85000,
            },
            documents=[
                {"filename": "SalarySlip.pdf", "text": "Salary Slip gross 85000"},
                {"filename": "Utility.pdf", "text": "Electricity Bill Bangalore"},
            ],
        )

        assert "cross_document_checks" in report
        assert "cross_document_summary" in report
        assert len(report["cross_document_checks"]) >= 2
        summary = report["cross_document_summary"]
        assert summary["total_checks"] >= 2
        assert summary["overall_consistency_status"] == "consistent"
        assert any("geocod" in lim.lower() for lim in report["analysis_limitations"])

    def test_endpoint_analyze_text_contains_cross_document_report(self, client):
        mock_ai_resp = json.dumps({
            "application_summary": {"applicant_name": "Arjun Sharma"},
            "extracted_fields": [
                {"field_name": "full_name", "observed_value": "Arjun Sharma", "source_document": "doc1.txt"},
            ],
            "document_results": [],
            "document_classifications": [],
            "discrepancies": [],
            "missing_information": [],
            "financial_summary": {},
            "risk_flags": [],
            "manual_review_items": [],
            "analysis_limitations": [],
            "overall_status": "complete",
        })

        with patch("document_analyzer._call_gemini_with_retries", return_value=mock_ai_resp):
            resp = client.post(
                "/analyze/text",
                json={
                    "application_details": {
                        "applicant_name": "Arjun Sharma",
                        "monthly_income": 75000,
                    },
                    "documents": [
                        {"filename": "doc1.txt", "text": "Employee Name: Arjun Sharma"},
                    ],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "cross_document_checks" in data
            assert "cross_document_summary" in data
            assert "cross_document_checks" in data["report"]
            assert data["cross_document_summary"]["overall_consistency_status"] in {"consistent", "inconclusive"}
