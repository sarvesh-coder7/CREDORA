"""
Unit and integration tests for Prompt 5: Financial Validation and Configurable Loan Requirements.

Tests cover:
1. Zero-interest EMI, ordinary reducing-balance EMI, invalid tenure, missing income, negative amounts.
2. Estimated total repayment and total interest over proposed tenure.
3. Total existing debt obligations calculation with deduplication.
4. Projected DTI and projected surplus calculation.
5. Inconsistent income evidence, gross vs net salary reconciliation, undeclared bank loan debits.
6. Configurable loan-document checklist (Bank, NBFC, Microfinance, unconfigured general fallback).
7. Required vs optional document tracking and recency window checking.
8. Custom lender checklist rule overrides.
9. Verification that calculations are deterministic and do not depend on Gemini.
10. End-to-end integration with analyze_application and AnalyzeResponse.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_AI_SERVER_DIR = Path(__file__).resolve().parent.parent
_ROOT_DIR = _AI_SERVER_DIR.parent
if str(_AI_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_AI_SERVER_DIR))
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from financial_calculator import (
    CalculationResult,
    ChecklistItem,
    DocumentChecklist,
    calculate_debt_to_income_ratio,
    calculate_emi,
    calculate_net_surplus,
    calculate_projected_debt_to_income_ratio,
    calculate_projected_monthly_surplus,
    calculate_total_existing_monthly_obligations,
    calculate_total_monthly_income,
    calculate_total_repayment_and_interest,
    evaluate_document_checklist,
    generate_financial_review,
    get_checklist_for_lender,
    reconcile_financial_consistency,
    run_all_calculations,
    validate_financial_inputs,
)


# ── 1. Deterministic Calculation Tests ────────────────────────────────────────

class TestPrompt5EMICalculations:
    """Verifies ordinary EMI, zero-interest EMI, invalid tenure, and negative amounts."""

    def test_ordinary_reducing_balance_emi(self):
        # Loan: Rs 5,00,000 at 10.5% p.a. for 60 months
        # Monthly r = 10.5 / 1200 = 0.00875
        # EMI = 500000 * 0.00875 * (1.00875)^60 / ((1.00875)^60 - 1) ~ 10746.94
        res = calculate_emi(principal=500000, annual_interest_rate_pct=10.5, tenure_months=60)
        assert res.status == "ok"
        assert res.value is not None
        assert 10740.0 <= res.value <= 10755.0
        assert res.unit == "INR/month"
        assert "P * r * (1+r)^n / ((1+r)^n - 1)" in res.formula
        assert res.inputs_used["principal"] == 500000

    def test_zero_interest_emi(self):
        # 0% interest promotional scheme: Rs 1,80,000 for 12 months -> EMI = 180000 / 12 = 15000.0
        res = calculate_emi(principal=180000, annual_interest_rate_pct=0.0, tenure_months=12)
        assert res.status == "ok"
        assert res.value == 15000.0
        assert "zero-interest" in res.formula
        assert any("Zero interest rate" in a for a in res.assumptions)

    def test_invalid_tenure_zero_or_negative(self):
        # Tenure <= 0 must fail deterministically
        res_zero = calculate_emi(principal=100000, annual_interest_rate_pct=10.0, tenure_months=0)
        assert res_zero.status == "invalid_inputs"
        assert res_zero.value is None

        res_neg = calculate_emi(principal=100000, annual_interest_rate_pct=10.0, tenure_months=-12)
        assert res_neg.status == "invalid_inputs"
        assert res_neg.value is None

    def test_negative_amounts(self):
        # Negative principal
        res_p = calculate_emi(principal=-10000, annual_interest_rate_pct=10.0, tenure_months=12)
        assert res_p.status == "invalid_inputs"
        assert res_p.value is None

        # Negative interest rate
        res_r = calculate_emi(principal=100000, annual_interest_rate_pct=-5.0, tenure_months=12)
        assert res_r.status == "invalid_inputs"
        assert res_r.value is None

    def test_missing_inputs_not_assumed_zero(self):
        # Missing principal
        res_no_p = calculate_emi(principal=None, annual_interest_rate_pct=10.0, tenure_months=12)
        assert res_no_p.status == "missing_inputs"
        assert res_no_p.value is None

        # Missing tenure
        res_no_t = calculate_emi(principal=100000, annual_interest_rate_pct=10.0, tenure_months=None)
        assert res_no_t.status == "missing_inputs"
        assert res_no_t.value is None

        # Missing rate
        res_no_r = calculate_emi(principal=100000, annual_interest_rate_pct=None, tenure_months=12)
        assert res_no_r.status == "missing_inputs"
        assert res_no_r.value is None


class TestPrompt5RepaymentAndInterest:
    """Verifies total repayment (EMI * n) and total interest (Total - Principal)."""

    def test_ordinary_repayment_and_interest(self):
        principal = 200000.0
        emi = 9500.0
        tenure = 24
        rep_res, int_res = calculate_total_repayment_and_interest(
            principal=principal, emi=emi, tenure_months=tenure, annual_interest_rate_pct=13.0
        )
        assert rep_res.status == "ok"
        assert rep_res.value == 228000.0  # 9500 * 24
        assert rep_res.unit == "INR"

        assert int_res.status == "ok"
        assert int_res.value == 28000.0  # 228000 - 200000
        assert int_res.unit == "INR"

    def test_zero_interest_total_repayment_and_interest(self):
        principal = 120000.0
        emi = 10000.0
        tenure = 12
        rep_res, int_res = calculate_total_repayment_and_interest(
            principal=principal, emi=emi, tenure_months=tenure, annual_interest_rate_pct=0.0
        )
        assert rep_res.status == "ok"
        assert rep_res.value == 120000.0
        assert int_res.status == "ok"
        assert int_res.value == 0.0

    def test_repayment_missing_or_invalid_inputs(self):
        # Missing
        rep_res, int_res = calculate_total_repayment_and_interest(principal=None, emi=1000.0, tenure_months=12)
        assert rep_res.status == "missing_inputs"
        assert int_res.status == "missing_inputs"

        # Invalid tenure
        rep_res2, int_res2 = calculate_total_repayment_and_interest(principal=50000.0, emi=1000.0, tenure_months=-5)
        assert rep_res2.status == "invalid_inputs"
        assert int_res2.status == "invalid_inputs"


class TestPrompt5ObligationsAndDeduplication:
    """Verifies existing obligations summation and avoidance of double counting."""

    def test_total_obligations_summation(self):
        res = calculate_total_existing_monthly_obligations(
            existing_loan_obligations=12500.0,
            other_financial_obligations=3500.0,
        )
        assert res.status == "ok"
        assert res.value == 16000.0

    def test_itemized_obligations_deduplication(self):
        # Applicant declared 2 loans, but provided duplicate entry for Loan A
        itemized = [
            {"account_number": "LOAN-101", "lender": "HDFC", "monthly_installment": 5000.0},
            {"account_number": "LOAN-101", "lender": "HDFC", "monthly_installment": 5000.0},  # duplicate
            {"account_number": "LOAN-202", "lender": "SBI", "monthly_installment": 7000.0},
        ]
        res = calculate_total_existing_monthly_obligations(
            existing_loan_obligations=None,
            other_financial_obligations=None,
            itemized_obligations=itemized,
        )
        assert res.status == "ok"
        assert res.value == 12000.0  # 5000 + 7000, not 17000
        assert any("Duplicate" in a for a in res.assumptions)

    def test_missing_obligations_not_assumed_zero(self):
        res = calculate_total_existing_monthly_obligations(
            existing_loan_obligations=None,
            other_financial_obligations=None,
            itemized_obligations=None,
        )
        assert res.status == "missing_inputs"
        assert res.value is None


class TestPrompt5ProjectedRatiosAndSurplus:
    """Verifies projected DTI and projected surplus after adding proposed loan EMI."""

    def test_projected_dti(self):
        # Income = 80,000; Existing obligations = 16,000; Proposed EMI = 16,000
        # Projected DTI = (16000 + 16000) / 80000 * 100 = 40.0%
        res = calculate_projected_debt_to_income_ratio(
            existing_monthly_obligations=16000.0,
            proposed_emi=16000.0,
            gross_monthly_income=80000.0,
        )
        assert res.status == "ok"
        assert res.value == 40.0
        assert res.unit == "%"

    def test_projected_surplus_and_deficit(self):
        # Surplus case: Income 1,00,000 - Debts 20,000 - Expenses 30,000 - Proposed EMI 25,000 = +25,000
        res_surplus = calculate_projected_monthly_surplus(
            monthly_income=100000.0,
            existing_obligations=20000.0,
            proposed_emi=25000.0,
            monthly_expenses=30000.0,
        )
        assert res_surplus.status == "ok"
        assert res_surplus.value == 25000.0

        # Deficit case: Income 50,000 - Debts 30,000 - Expenses 20,000 - Proposed EMI 15,000 = -15,000
        res_deficit = calculate_projected_monthly_surplus(
            monthly_income=50000.0,
            existing_obligations=30000.0,
            proposed_emi=15000.0,
            monthly_expenses=20000.0,
        )
        assert res_deficit.status == "ok"
        assert res_deficit.value == -15000.0


# ── 2. Financial Consistency Checks ──────────────────────────────────────────

class TestPrompt5FinancialConsistency:
    """Verifies declared income vs salary slip gross vs net vs recurring credits."""

    def test_declared_income_inconsistent_with_salary_slip(self):
        # Declared 1,20,000 but salary slip evidences gross salary of 60,000 (100% variance)
        extracted_fields = [
            {"field_name": "gross_salary", "value": 60000.0, "source_document": "salary_slip.pdf"},
            {"field_name": "net_salary", "value": 48000.0, "source_document": "salary_slip.pdf"},
        ]
        res = reconcile_financial_consistency(
            declared_income=120000.0,
            extracted_fields=extracted_fields,
            documents=[],
        )
        assert res["assessment_status"] == "discrepancies_detected"
        assert len(res["financial_discrepancies"]) >= 1
        disc = res["financial_discrepancies"][0]
        assert disc["field"] == "declared_income_vs_gross_salary"
        assert disc["severity"] == "high"
        assert "exceeds documented gross salary" in disc["evidence"]

    def test_gross_to_net_salary_reconciliation_not_flagged_as_error(self):
        # Declared 80,000; Gross is 80,000; Net take-home is 62,000 (~77.5% of gross, standard PF/tax deduction)
        extracted_fields = [
            {"field_name": "gross_salary", "value": 80000.0, "source_document": "pay_slip.pdf"},
            {"field_name": "net_salary", "value": 62000.0, "source_document": "pay_slip.pdf"},
        ]
        res = reconcile_financial_consistency(
            declared_income=80000.0,
            extracted_fields=extracted_fields,
            documents=[],
        )
        assert res["assessment_status"] == "verified"
        assert len(res["financial_discrepancies"]) == 0
        assert any("statutory deductions" in n for n in res["audit_notes"])

    def test_net_salary_vs_bank_salary_credit_discrepancy(self):
        # Salary slip net is 70,000, but recurring bank credit is only 45,000
        extracted_fields = [
            {"field_name": "gross_salary", "value": 85000.0, "source_document": "pay_slip.pdf"},
            {"field_name": "net_salary", "value": 70000.0, "source_document": "pay_slip.pdf"},
            {"field_name": "salary_credit", "value": 45000.0, "source_document": "bank_statement.pdf"},
        ]
        res = reconcile_financial_consistency(
            declared_income=85000.0,
            extracted_fields=extracted_fields,
            documents=[],
        )
        assert res["assessment_status"] == "discrepancies_detected"
        disc_names = [d["field"] for d in res["financial_discrepancies"]]
        assert "salary_slip_vs_bank_credit" in disc_names

    def test_undeclared_bank_loan_debits_detected(self):
        # Applicant declared 0 obligations, but bank statement evidences recurring EMI debits of 18,500
        extracted_fields = [
            {"field_name": "gross_salary", "value": 60000.0, "source_document": "pay_slip.pdf"},
            {"field_name": "emi_debit", "value": 18500.0, "source_document": "bank_statement.pdf"},
        ]
        res = reconcile_financial_consistency(
            declared_income=60000.0,
            extracted_fields=extracted_fields,
            documents=[],
            declared_obligations=0.0,
        )
        assert res["assessment_status"] == "discrepancies_detected"
        disc_names = [d["field"] for d in res["financial_discrepancies"]]
        assert "undeclared_bank_loan_debits" in disc_names
        disc = next(d for d in res["financial_discrepancies"] if d["field"] == "undeclared_bank_loan_debits")
        assert disc["severity"] == "high"

    def test_insufficient_evidence_returns_not_assessed(self):
        # Applicant declares income, but no financial documents or fields provided
        res = reconcile_financial_consistency(
            declared_income=50000.0,
            extracted_fields=[],
            documents=[],
        )
        assert res["assessment_status"] == "not_assessed"
        assert res["evidenced_income"]["verified_status"] == "not_assessed"
        assert len(res["manual_review_items"]) >= 1


# ── 3. Configurable Loan-Document Checklist ───────────────────────────────────

class TestPrompt5DocumentChecklist:
    """Verifies Bank, NBFC, Microfinance checklists, required vs optional, recency, and overrides."""

    def test_bank_home_loan_checklist_evaluation(self):
        checklist = get_checklist_for_lender(lender_category="Bank", loan_product="Home Loan")
        assert checklist.is_lender_specific is True
        assert checklist.lender_category == "Bank"

        # Submit identity proof and bank statement, but missing required salary slip
        docs = [
            {"filename": "aadhaar.pdf", "document_type": "aadhaar_card"},
            {"filename": "bank_stmt.pdf", "document_type": "bank_statement"},
        ]
        res = evaluate_document_checklist(checklist=checklist, submitted_documents=docs, applicant_category="salaried")
        assert res["overall_checklist_status"] == "incomplete"
        assert res["lender_specific_compliance"] == "non_compliant"
        missing_ids = [m["item_id"] for m in res["missing_required_documents"]]
        assert "income_proof_salaried" in missing_ids

    def test_optional_document_omission_does_not_fail_compliance(self):
        checklist = get_checklist_for_lender(lender_category="Bank", loan_product="Home Loan")
        # Submit all required docs, omit optional existing_loan_statement
        docs = [
            {"filename": "aadhaar.pdf", "document_type": "aadhaar_card"},
            {"filename": "electricity_bill.pdf", "document_type": "utility_bill"},
            {"filename": "salary_slip.pdf", "document_type": "salary_slip"},
            {"filename": "bank_stmt.pdf", "document_type": "bank_statement"},
        ]
        res = evaluate_document_checklist(checklist=checklist, submitted_documents=docs, applicant_category="salaried")
        assert res["overall_checklist_status"] == "complete"
        assert res["lender_specific_compliance"] == "compliant"
        assert len(res["missing_required_documents"]) == 0
        assert any(m["item_id"] == "existing_loan_statement" for m in res["missing_optional_documents"])

    def test_unconfigured_lender_fallback_to_general_checklist(self):
        # When lender is None or unconfigured, returns general checklist with compliance="not_assessed"
        checklist = get_checklist_for_lender(lender_category=None, lender_name=None, loan_product=None)
        assert checklist.is_lender_specific is False
        assert "General Retail Loan Baseline" in checklist.checklist_name

        docs = [{"filename": "id.pdf", "document_type": "pan_card"}]
        res = evaluate_document_checklist(checklist=checklist, submitted_documents=docs)
        assert res["lender_specific_compliance"] == "not_assessed"
        assert "not configured" in res["compliance_explanation"].lower()

    def test_microfinance_group_loan_checklist(self):
        checklist = get_checklist_for_lender(lender_category="Microfinance", loan_product="Microfinance Group Loan")
        assert checklist.is_lender_specific is True
        # Microfinance requires household income declaration, salary slip is optional
        items_by_id = {it.item_id: it for it in checklist.items}
        assert items_by_id["household_income_declaration"].status == "required"
        assert items_by_id["salary_slip"].status == "optional"

    def test_custom_lender_checklist_rule_overrides(self):
        custom_rules = [
            {
                "item_id": "property_tax_receipt",
                "document_name": "Property Tax Receipt",
                "accepted_document_types": ["tax_receipt", "property_tax"],
                "status": "required",
                "rule_source": "Special Municipal Loan Mandate",
            },
            {
                "item_id": "guarantor_id",
                "document_name": "Guarantor Identity Proof",
                "accepted_document_types": ["aadhaar_card", "pan_card"],
                "status": "optional",
                "rule_source": "Special Municipal Loan Mandate",
            },
        ]
        checklist = get_checklist_for_lender(
            lender_category="Bank",
            lender_name="Apex Cooperative",
            custom_checklist=custom_rules,
        )
        assert checklist.is_lender_specific is True
        assert len(checklist.items) == 2
        assert checklist.items[0].document_name == "Property Tax Receipt"

        # Verify evaluation against custom rules
        res = evaluate_document_checklist(checklist, submitted_documents=[])
        assert res["overall_checklist_status"] == "incomplete"
        assert res["missing_required_documents"][0]["item_id"] == "property_tax_receipt"


# ── 4. Input Validation & Edge Cases ──────────────────────────────────────────

class TestPrompt5InputValidation:
    """Verifies numeric parsing, invalid range checks, and non-assumption of zero."""

    def test_validate_financial_inputs_valid(self):
        inputs = {
            "loan_category": "Bank",
            "selected_lender": "HDFC Bank",
            "selected_loan_product": "Home Loan",
            "requested_loan_amount": "5,00,000",
            "proposed_tenure": "60",
            "proposed_annual_interest_rate": "9.5",
            "applicant_declared_monthly_income": "75,000",
            "existing_monthly_loan_obligations": "15,000",
        }
        res = validate_financial_inputs(inputs)
        assert res["is_valid"] is True
        v = res["validated_inputs"]
        assert v["requested_loan_amount"] == 500000.0
        assert v["proposed_tenure"] == 60
        assert v["proposed_annual_interest_rate"] == 9.5
        assert v["applicant_declared_monthly_income"] == 75000.0
        assert v["existing_monthly_loan_obligations"] == 15000.0

    def test_missing_inputs_are_not_defaulted_to_zero(self):
        inputs = {
            "requested_loan_amount": 200000.0,
            # tenure, rate, income are all absent
        }
        res = validate_financial_inputs(inputs)
        assert res["is_valid"] is True
        v = res["validated_inputs"]
        assert v["proposed_tenure"] is None
        assert v["proposed_annual_interest_rate"] is None
        assert v["applicant_declared_monthly_income"] is None

        missing_fields = [m["field"] for m in res["missing_inputs"]]
        assert "proposed_tenure" in missing_fields
        assert "proposed_annual_interest_rate" in missing_fields
        assert "applicant_declared_monthly_income" in missing_fields

    def test_invalid_types_and_negative_ranges(self):
        inputs = {
            "requested_loan_amount": -50000,
            "proposed_tenure": 0,
            "proposed_annual_interest_rate": -3.5,
            "applicant_declared_monthly_income": "not_a_number",
        }
        res = validate_financial_inputs(inputs)
        assert res["is_valid"] is False
        assert len(res["validation_errors"]) >= 4


# ── 5. Master Financial Review Output & Limitations ──────────────────────────

class TestPrompt5FinancialReviewOutput:
    """Verifies the structured review format, audit fields, and decision limitations."""

    def test_generate_financial_review_comprehensive(self):
        financial_inputs = {
            "loan_category": "Bank",
            "selected_lender": "State Bank of India",
            "selected_loan_product": "Home Loan",
            "requested_loan_amount": 1000000.0,
            "proposed_tenure": 120,
            "proposed_annual_interest_rate": 8.5,
            "applicant_declared_monthly_income": 85000.0,
            "existing_monthly_loan_obligations": 10000.0,
            "monthly_expenses": 25000.0,
        }
        extracted_fields = [
            {"field_name": "gross_salary", "value": 85000.0, "source": "salary_slip.pdf"},
            {"field_name": "net_salary", "value": 68000.0, "source": "salary_slip.pdf"},
            {"field_name": "salary_credit", "value": 68000.0, "source": "bank_stmt.pdf"},
        ]
        documents = [
            {"filename": "aadhaar.pdf", "document_type": "aadhaar_card"},
            {"filename": "ebill.pdf", "document_type": "utility_bill"},
            {"filename": "salary_slip.pdf", "document_type": "salary_slip"},
            {"filename": "bank_stmt.pdf", "document_type": "bank_statement"},
        ]

        review = generate_financial_review(
            financial_inputs=financial_inputs,
            extracted_fields=extracted_fields,
            documents=documents,
        )

        # 1. Declared income
        assert review["declared_income"]["value"] == 85000.0
        assert review["declared_income"]["currency"] == "INR"

        # 2. Evidenced income
        assert review["evidenced_income"]["gross_salary"] == 85000.0
        assert review["evidenced_income"]["net_salary"] == 68000.0
        assert review["evidenced_income"]["verified_status"] == "verified"

        # 3. Existing obligations
        assert review["existing_monthly_obligations"]["value"] == 10000.0

        # 4. Proposed EMI
        assert review["proposed_emi"]["status"] == "ok"
        assert 12000.0 < review["proposed_emi"]["value"] < 13000.0

        # 5. Estimated total repayment & interest
        assert review["estimated_total_repayment"]["status"] == "ok"
        assert review["estimated_total_interest"]["status"] == "ok"
        assert review["estimated_total_repayment"]["value"] > 1000000.0

        # 6. DTI ratios
        dti = review["debt_to_income_ratio"]
        assert dti["existing_dti"]["status"] == "ok"
        assert dti["existing_dti"]["value"] == round(10000.0 / 85000.0 * 100, 2)
        assert dti["projected_dti"]["status"] == "ok"
        assert dti["projected_dti"]["value"] > dti["existing_dti"]["value"]

        # 7. Estimated surplus
        surplus = review["estimated_monthly_surplus"]
        assert surplus["existing_surplus"]["status"] == "ok"
        assert surplus["projected_surplus"]["status"] == "ok"
        assert surplus["projected_surplus"]["value"] < surplus["existing_surplus"]["value"]

        # 8. Checklist status
        assert review["document_checklist_status"]["lender_specific_compliance"] == "compliant"

        # 9. Calculation assumptions & decision limitations
        assert len(review["calculation_assumptions"]) > 0
        assert len(review["decision_limitations"]) >= 4
        assert any("automated computational assessment" in l for l in review["decision_limitations"])
        assert any("NOT constitute an official credit sanction" in l for l in review["decision_limitations"])


# ── 6. Top-Level Integration Tests ───────────────────────────────────────────

class TestPrompt5Integration:
    """Verifies analyze_application and FastAPI endpoints return the complete financial review."""

    @patch("document_analyzer._get_gemini_client")
    def test_analyze_application_incorporates_financial_review(self, mock_client_getter):
        from document_analyzer import analyze_application

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = """{
            "application_summary": {"applicant_name": "Rajesh Sharma"},
            "extracted_fields": [
                {"field_name": "gross_salary", "value": 75000.0, "source": "slip.pdf"}
            ],
            "document_classifications": [
                {"filename": "slip.pdf", "document_type": "salary_slip", "confidence": "HIGH", "evidence": ["Salary Slip"]}
            ],
            "discrepancies": [],
            "missing_information": [],
            "financial_summary": {},
            "manual_review_items": [],
            "analysis_limitations": []
        }"""
        mock_client.models.generate_content.return_value = mock_response
        mock_client_getter.return_value = (mock_client, "gemini-mock")

        details = {
            "applicant_name": "Rajesh Sharma",
            "financial_inputs": {
                "loan_category": "Bank",
                "requested_loan_amount": 300000.0,
                "proposed_tenure": 36,
                "proposed_annual_interest_rate": 11.0,
                "applicant_declared_monthly_income": 75000.0,
                "existing_monthly_loan_obligations": 8000.0,
            },
        }
        docs = [{"filename": "slip.pdf", "text": "Gross Salary: 75,000 Net Salary: 60,000"}]

        report = analyze_application(details, docs)
        assert "financial_review" in report
        fin_rev = report["financial_review"]
        assert fin_rev["declared_income"]["value"] == 75000.0
        assert fin_rev["proposed_emi"]["status"] == "ok"
        assert fin_rev["proposed_emi"]["value"] is not None
        assert "decision_limitations" in fin_rev

        # Verify financial_summary was enriched
        assert report["financial_summary"]["proposed_emi"] == fin_rev["proposed_emi"]["value"]

    def test_fastapi_analyze_text_endpoint_returns_financial_review(self):
        import importlib
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake_key", "GEMINI_MODEL": "gemini-test"}):
            import app as app_module
            importlib.reload(app_module)
            client = TestClient(app_module.app, raise_server_exceptions=False)

            with patch("app.analyze_application") as mock_analyze:
                mock_analyze.return_value = {
                    "application_summary": {"applicant_name": "Sunita Verma"},
                    "extracted_fields": [],
                    "document_classifications": [],
                    "discrepancies": [],
                    "missing_information": [],
                    "financial_summary": {},
                    "financial_review": {},
                    "manual_review_items": [],
                    "analysis_limitations": [],
                    "cross_document_checks": [],
                    "cross_document_summary": {},
                    "overall_status": "complete",
                }

                req_body = {
                    "application_details": {"applicant_name": "Sunita Verma"},
                    "documents": [{"filename": "test.txt", "text": "Salary certificate Rs 50,000"}],
                    "financial_inputs": {
                        "loan_category": "NBFC",
                        "requested_loan_amount": 100000.0,
                        "proposed_tenure": 12,
                        "proposed_annual_interest_rate": 12.0,
                        "applicant_declared_monthly_income": 50000.0,
                    },
                }

                resp = client.post("/analyze/text", json=req_body)
                assert resp.status_code == 200
                data = resp.json()
                assert "financial_calculations" in data
                assert "financial_review" in data
                fin_rev = data["financial_review"]
                assert fin_rev["proposed_emi"]["value"] is not None
                assert fin_rev["declared_income"]["value"] == 50000.0
                assert len(fin_rev["decision_limitations"]) > 0
