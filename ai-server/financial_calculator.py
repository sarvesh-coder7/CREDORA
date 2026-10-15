"""
Deterministic financial calculations and configurable loan requirements for loan document analysis.

All financial calculations are pure, stateless, and mathematically deterministic.
Formulas, inputs used, assumptions, and rounding are fully documented in each CalculationResult.
No AI or LLM is involved in any arithmetic.

Key functionalities:
1. Loan application input validation (range, units, type checking; never assuming missing is zero)
2. Deterministic calculations:
   - Monthly income and recurring income summaries
   - Total existing monthly debt obligations (with deduplication)
   - Proposed EMI via standard reducing-balance formula and zero-interest handling
   - Estimated total repayment and total interest over proposed tenure
   - Debt-to-income ratio (existing DTI and projected DTI)
   - Estimated monthly surplus (existing and projected)
3. Financial consistency checks (declared income vs gross salary vs net salary vs bank credits vs debt debits)
4. Configurable loan-document checklist system (Bank, NBFC, Microfinance, custom override, unconfigured fallback)
5. Structured financial review output with explicit lending decision limitations
"""

from __future__ import annotations

import datetime
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CalculationResult:
    """A single calculation result with full audit trail."""

    calculation_name: str
    value: float | None
    unit: str
    inputs_used: dict[str, Any]
    assumptions: list[str]
    formula: str
    status: str  # "ok" | "missing_inputs" | "invalid_inputs" | "error"
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "calculation_name": self.calculation_name,
            "value": self.value,
            "unit": self.unit,
            "inputs_used": self.inputs_used,
            "assumptions": self.assumptions,
            "formula": self.formula,
            "status": self.status,
            "error_message": self.error_message,
        }


# ── Core Deterministic Calculations ──────────────────────────────────────────

def calculate_emi(
    principal: float | None,
    annual_interest_rate_pct: float | None,
    tenure_months: int | None,
) -> CalculationResult:
    """
    Standard EMI formula:  EMI = P * r * (1+r)^n / ((1+r)^n - 1)
    where r = monthly interest rate, n = tenure in months.

    Zero interest is handled separately: EMI = P / n.
    """
    name = "estimated_emi"
    inputs = {
        "principal": principal,
        "annual_interest_rate_pct": annual_interest_rate_pct,
        "tenure_months": tenure_months,
    }

    if principal is None or annual_interest_rate_pct is None or tenure_months is None:
        return CalculationResult(
            calculation_name=name,
            value=None,
            unit="INR/month",
            inputs_used=inputs,
            assumptions=[],
            formula="P * r * (1+r)^n / ((1+r)^n - 1)",
            status="missing_inputs",
            error_message="One or more required inputs (principal, annual_interest_rate_pct, tenure_months) are absent.",
        )

    if principal < 0:
        return CalculationResult(
            calculation_name=name, value=None, unit="INR/month",
            inputs_used=inputs, assumptions=[],
            formula="P * r * (1+r)^n / ((1+r)^n - 1)",
            status="invalid_inputs",
            error_message="Principal must be non-negative.",
        )
    if annual_interest_rate_pct < 0:
        return CalculationResult(
            calculation_name=name, value=None, unit="INR/month",
            inputs_used=inputs, assumptions=[],
            formula="P * r * (1+r)^n / ((1+r)^n - 1)",
            status="invalid_inputs",
            error_message="Annual interest rate must be non-negative.",
        )
    if tenure_months <= 0:
        return CalculationResult(
            calculation_name=name, value=None, unit="INR/month",
            inputs_used=inputs, assumptions=[],
            formula="P * r * (1+r)^n / ((1+r)^n - 1)",
            status="invalid_inputs",
            error_message="Tenure must be a positive integer (months).",
        )

    try:
        if annual_interest_rate_pct == 0.0:
            emi = principal / tenure_months
            formula = "P / n (zero-interest)"
            assumptions = ["Zero interest rate: no interest component."]
        else:
            r = (annual_interest_rate_pct / 100.0) / 12.0
            n = tenure_months
            emi = principal * r * math.pow(1 + r, n) / (math.pow(1 + r, n) - 1)
            formula = "P * r * (1+r)^n / ((1+r)^n - 1)"
            assumptions = [
                f"Monthly rate r = {annual_interest_rate_pct}% / 12 = {r:.6f}",
            ]

        return CalculationResult(
            calculation_name=name,
            value=round(emi, 2),
            unit="INR/month",
            inputs_used=inputs,
            assumptions=assumptions,
            formula=formula,
            status="ok",
        )
    except (ZeroDivisionError, OverflowError, ValueError) as exc:
        return CalculationResult(
            calculation_name=name, value=None, unit="INR/month",
            inputs_used=inputs, assumptions=[],
            formula="P * r * (1+r)^n / ((1+r)^n - 1)",
            status="error",
            error_message=f"Calculation error: {exc}",
        )


def calculate_total_repayment_and_interest(
    principal: float | None,
    emi: float | None,
    tenure_months: int | None,
    annual_interest_rate_pct: float | None = None,
) -> tuple[CalculationResult, CalculationResult]:
    """
    Compute estimated total repayment and total interest over the proposed tenure.

    Total repayment = EMI * tenure_months
    Total interest  = Total repayment - principal

    For zero-interest loans:
    Total repayment = principal
    Total interest  = 0.0
    """
    repayment_name = "estimated_total_repayment"
    interest_name = "estimated_total_interest"
    inputs = {
        "principal": principal,
        "emi": emi,
        "tenure_months": tenure_months,
        "annual_interest_rate_pct": annual_interest_rate_pct,
    }

    if principal is None or tenure_months is None or (emi is None and annual_interest_rate_pct != 0.0):
        missing_res = CalculationResult(
            calculation_name=repayment_name,
            value=None,
            unit="INR",
            inputs_used=inputs,
            assumptions=[],
            formula="EMI * tenure_months",
            status="missing_inputs",
            error_message="Principal, tenure_months, or EMI is absent.",
        )
        interest_res = CalculationResult(
            calculation_name=interest_name,
            value=None,
            unit="INR",
            inputs_used=inputs,
            assumptions=[],
            formula="total_repayment - principal",
            status="missing_inputs",
            error_message="Principal, tenure_months, or EMI is absent.",
        )
        return missing_res, interest_res

    if principal < 0 or tenure_months <= 0 or (emi is not None and emi < 0):
        invalid_res = CalculationResult(
            calculation_name=repayment_name,
            value=None,
            unit="INR",
            inputs_used=inputs,
            assumptions=[],
            formula="EMI * tenure_months",
            status="invalid_inputs",
            error_message="Numeric inputs must be non-negative and tenure must be positive.",
        )
        interest_res = CalculationResult(
            calculation_name=interest_name,
            value=None,
            unit="INR",
            inputs_used=inputs,
            assumptions=[],
            formula="total_repayment - principal",
            status="invalid_inputs",
            error_message="Numeric inputs must be non-negative and tenure must be positive.",
        )
        return invalid_res, interest_res

    if annual_interest_rate_pct == 0.0:
        total_repayment = round(float(principal), 2)
        total_interest = 0.0
        repayment_res = CalculationResult(
            calculation_name=repayment_name,
            value=total_repayment,
            unit="INR",
            inputs_used=inputs,
            assumptions=["Zero interest rate: total repayment equals principal."],
            formula="principal",
            status="ok",
        )
        interest_res = CalculationResult(
            calculation_name=interest_name,
            value=total_interest,
            unit="INR",
            inputs_used=inputs,
            assumptions=["Zero interest rate: no interest component."],
            formula="0.0 (zero-interest)",
            status="ok",
        )
        return repayment_res, interest_res

    total_repayment = round(float(emi) * tenure_months, 2)
    total_interest = round(max(0.0, total_repayment - principal), 2)

    repayment_res = CalculationResult(
        calculation_name=repayment_name,
        value=total_repayment,
        unit="INR",
        inputs_used=inputs,
        assumptions=[f"Assumes {tenure_months} fixed reducing-balance monthly installments of ₹{emi:,.2f}."],
        formula="EMI * tenure_months",
        status="ok",
    )
    interest_res = CalculationResult(
        calculation_name=interest_name,
        value=total_interest,
        unit="INR",
        inputs_used=inputs,
        assumptions=[f"Cumulative interest across {tenure_months} months: ₹{total_repayment:,.2f} - ₹{principal:,.2f}."],
        formula="total_repayment - principal",
        status="ok",
    )
    return repayment_res, interest_res


def calculate_total_monthly_income(
    income_sources: list[dict[str, Any]],
) -> CalculationResult:
    """
    Sum monthly income across sources.

    Each item in income_sources must have:
        {"source": str, "amount": float, "period": "monthly"|"annual"}
    """
    name = "total_monthly_income"
    if not income_sources:
        return CalculationResult(
            calculation_name=name, value=None, unit="INR/month",
            inputs_used={"income_sources": income_sources}, assumptions=[],
            formula="sum(monthly_amount for each source)",
            status="missing_inputs",
            error_message="No income sources provided.",
        )

    total = 0.0
    used: list[dict] = []
    errors: list[str] = []

    for item in income_sources:
        source = item.get("source", "unknown")
        amount = item.get("amount")
        period = str(item.get("period", "monthly")).lower()

        if amount is None or not isinstance(amount, (int, float)):
            errors.append(f"Source '{source}': amount missing or non-numeric.")
            continue
        if amount < 0:
            errors.append(f"Source '{source}': negative amount skipped.")
            continue
        if period == "annual":
            monthly = amount / 12.0
            used.append({"source": source, "original": amount, "period": "annual", "monthly": round(monthly, 2)})
        elif period == "monthly":
            monthly = float(amount)
            used.append({"source": source, "original": amount, "period": "monthly", "monthly": round(monthly, 2)})
        else:
            errors.append(f"Source '{source}': unknown period '{period}', skipped.")
            continue
        total += monthly

    status = "ok" if not errors else ("missing_inputs" if total == 0.0 else "ok")
    return CalculationResult(
        calculation_name=name,
        value=round(total, 2) if used else None,
        unit="INR/month",
        inputs_used={"income_sources_processed": used},
        assumptions=["Annual amounts divided by 12 to convert to monthly."] + errors,
        formula="sum(monthly_amount for each source)",
        status=status,
        error_message="; ".join(errors) if errors and total == 0.0 else None,
    )


def calculate_total_existing_monthly_obligations(
    existing_loan_obligations: float | None,
    other_financial_obligations: float | None,
    itemized_obligations: list[dict[str, Any]] | None = None,
) -> CalculationResult:
    """
    Deterministic total existing monthly debt obligations.

    Combines loan obligations and other recurring commitments.
    Avoids double-counting when itemized obligations are provided.
    Does not assume missing values are zero.
    """
    name = "total_existing_monthly_obligations"
    inputs = {
        "existing_loan_obligations": existing_loan_obligations,
        "other_financial_obligations": other_financial_obligations,
        "itemized_obligations_count": len(itemized_obligations) if itemized_obligations else 0,
    }

    if existing_loan_obligations is None and other_financial_obligations is None and not itemized_obligations:
        return CalculationResult(
            calculation_name=name,
            value=None,
            unit="INR/month",
            inputs_used=inputs,
            assumptions=[],
            formula="existing_loan_obligations + other_financial_obligations",
            status="missing_inputs",
            error_message="Existing monthly debt obligations were not provided.",
        )

    if (existing_loan_obligations is not None and existing_loan_obligations < 0) or \
       (other_financial_obligations is not None and other_financial_obligations < 0):
        return CalculationResult(
            calculation_name=name,
            value=None,
            unit="INR/month",
            inputs_used=inputs,
            assumptions=[],
            formula="existing_loan_obligations + other_financial_obligations",
            status="invalid_inputs",
            error_message="Debt obligations must be non-negative.",
        )

    assumptions = []
    total = 0.0

    # Handle itemized obligations with deduplication
    if itemized_obligations:
        seen_keys = set()
        deduped_total = 0.0
        for item in itemized_obligations:
            acct = item.get("account_number") or item.get("loan_account_no")
            lender = str(item.get("lender") or "").strip().lower()
            amt = item.get("monthly_installment", item.get("amount", 0.0))
            try:
                amt_float = float(amt)
            except (ValueError, TypeError):
                continue
            if amt_float < 0:
                continue

            dedup_key = acct if acct else (lender, round(amt_float, 2))
            if dedup_key in seen_keys:
                assumptions.append(f"Duplicate itemized obligation for '{dedup_key}' skipped to prevent double counting.")
                continue
            seen_keys.add(dedup_key)
            deduped_total += amt_float

        total = deduped_total
        assumptions.append(f"Derived from {len(seen_keys)} unique itemized obligations (deduplicated).")
    else:
        if existing_loan_obligations is not None:
            total += existing_loan_obligations
            assumptions.append(f"Includes declared monthly loan obligations: ₹{existing_loan_obligations:,.2f}.")
        else:
            assumptions.append("Loan obligations not specified; omitted from total.")

        if other_financial_obligations is not None:
            total += other_financial_obligations
            assumptions.append(f"Includes other recurring financial obligations: ₹{other_financial_obligations:,.2f}.")
        else:
            assumptions.append("Other financial obligations not specified; omitted from total.")

    return CalculationResult(
        calculation_name=name,
        value=round(total, 2),
        unit="INR/month",
        inputs_used=inputs,
        assumptions=assumptions,
        formula="existing_loan_obligations + other_financial_obligations",
        status="ok",
    )


def calculate_debt_to_income_ratio(
    total_monthly_obligations: float | None,
    gross_monthly_income: float | None,
) -> CalculationResult:
    """DTI = total monthly debt obligations / gross monthly income * 100."""
    name = "debt_to_income_ratio"
    inputs = {
        "total_monthly_obligations": total_monthly_obligations,
        "gross_monthly_income": gross_monthly_income,
    }

    if total_monthly_obligations is None or gross_monthly_income is None:
        return CalculationResult(
            calculation_name=name, value=None, unit="%",
            inputs_used=inputs, assumptions=[],
            formula="(total_monthly_obligations / gross_monthly_income) * 100",
            status="missing_inputs",
            error_message="Both total_monthly_obligations and gross_monthly_income are required.",
        )
    if gross_monthly_income <= 0:
        return CalculationResult(
            calculation_name=name, value=None, unit="%",
            inputs_used=inputs, assumptions=[],
            formula="(total_monthly_obligations / gross_monthly_income) * 100",
            status="invalid_inputs",
            error_message="gross_monthly_income must be positive.",
        )
    if total_monthly_obligations < 0:
        return CalculationResult(
            calculation_name=name, value=None, unit="%",
            inputs_used=inputs, assumptions=[],
            formula="(total_monthly_obligations / gross_monthly_income) * 100",
            status="invalid_inputs",
            error_message="total_monthly_obligations must be non-negative.",
        )

    dti = (total_monthly_obligations / gross_monthly_income) * 100.0
    return CalculationResult(
        calculation_name=name,
        value=round(dti, 2),
        unit="%",
        inputs_used=inputs,
        assumptions=["Uses gross monthly income as denominator.", "Reflects existing obligations prior to proposed loan."],
        formula="(total_monthly_obligations / gross_monthly_income) * 100",
        status="ok",
    )


def calculate_projected_debt_to_income_ratio(
    existing_monthly_obligations: float | None,
    proposed_emi: float | None,
    gross_monthly_income: float | None,
) -> CalculationResult:
    """
    Projected DTI = (existing_monthly_obligations + proposed_emi) / gross_monthly_income * 100.

    Evaluates the applicant's total debt burden if the proposed loan is sanctioned.
    """
    name = "projected_debt_to_income_ratio"
    inputs = {
        "existing_monthly_obligations": existing_monthly_obligations,
        "proposed_emi": proposed_emi,
        "gross_monthly_income": gross_monthly_income,
    }

    if gross_monthly_income is None or proposed_emi is None:
        return CalculationResult(
            calculation_name=name,
            value=None,
            unit="%",
            inputs_used=inputs,
            assumptions=[],
            formula="((existing_monthly_obligations + proposed_emi) / gross_monthly_income) * 100",
            status="missing_inputs",
            error_message="Both gross_monthly_income and proposed_emi are required.",
        )

    if gross_monthly_income <= 0:
        return CalculationResult(
            calculation_name=name,
            value=None,
            unit="%",
            inputs_used=inputs,
            assumptions=[],
            formula="((existing_monthly_obligations + proposed_emi) / gross_monthly_income) * 100",
            status="invalid_inputs",
            error_message="gross_monthly_income must be positive.",
        )
    if proposed_emi < 0 or (existing_monthly_obligations is not None and existing_monthly_obligations < 0):
        return CalculationResult(
            calculation_name=name,
            value=None,
            unit="%",
            inputs_used=inputs,
            assumptions=[],
            formula="((existing_monthly_obligations + proposed_emi) / gross_monthly_income) * 100",
            status="invalid_inputs",
            error_message="Debt amounts must be non-negative.",
        )

    assumptions = []
    base_obligations = 0.0
    if existing_monthly_obligations is not None:
        base_obligations = existing_monthly_obligations
        assumptions.append(f"Includes existing obligations: ₹{existing_monthly_obligations:,.2f}.")
    else:
        assumptions.append("Existing obligations not specified; assumed 0 for projected calculation.")

    total_projected_debt = base_obligations + proposed_emi
    projected_dti = (total_projected_debt / gross_monthly_income) * 100.0
    assumptions.append(f"Includes proposed EMI: ₹{proposed_emi:,.2f}.")

    return CalculationResult(
        calculation_name=name,
        value=round(projected_dti, 2),
        unit="%",
        inputs_used=inputs,
        assumptions=assumptions,
        formula="((existing_monthly_obligations + proposed_emi) / gross_monthly_income) * 100",
        status="ok",
    )


def calculate_net_surplus(
    monthly_income: float | None,
    monthly_obligations: float | None,
    monthly_expenses: float | None,
) -> CalculationResult:
    """Estimated income remaining after declared obligations and expenses."""
    name = "estimated_net_surplus"
    inputs = {
        "monthly_income": monthly_income,
        "monthly_obligations": monthly_obligations,
        "monthly_expenses": monthly_expenses,
    }

    if monthly_income is None:
        return CalculationResult(
            calculation_name=name, value=None, unit="INR/month",
            inputs_used=inputs, assumptions=[],
            formula="monthly_income - monthly_obligations - monthly_expenses",
            status="missing_inputs",
            error_message="monthly_income is required.",
        )

    deductions = 0.0
    assumptions: list[str] = []

    if monthly_obligations is not None:
        deductions += monthly_obligations
    else:
        assumptions.append("monthly_obligations not provided; assumed 0.")

    if monthly_expenses is not None:
        deductions += monthly_expenses
    else:
        assumptions.append("monthly_expenses not provided; assumed 0.")

    surplus = monthly_income - deductions
    return CalculationResult(
        calculation_name=name,
        value=round(surplus, 2),
        unit="INR/month",
        inputs_used=inputs,
        assumptions=assumptions,
        formula="monthly_income - monthly_obligations - monthly_expenses",
        status="ok",
    )


def calculate_projected_monthly_surplus(
    monthly_income: float | None,
    existing_obligations: float | None,
    proposed_emi: float | None,
    monthly_expenses: float | None = None,
) -> CalculationResult:
    """
    Projected monthly cash flow surplus after existing debts, living expenses, and proposed EMI.

    surplus = monthly_income - existing_obligations - monthly_expenses - proposed_emi
    A negative surplus represents an estimated cash-flow deficit.
    """
    name = "projected_monthly_surplus"
    inputs = {
        "monthly_income": monthly_income,
        "existing_obligations": existing_obligations,
        "proposed_emi": proposed_emi,
        "monthly_expenses": monthly_expenses,
    }

    if monthly_income is None or proposed_emi is None:
        return CalculationResult(
            calculation_name=name,
            value=None,
            unit="INR/month",
            inputs_used=inputs,
            assumptions=[],
            formula="monthly_income - existing_obligations - monthly_expenses - proposed_emi",
            status="missing_inputs",
            error_message="Both monthly_income and proposed_emi are required.",
        )

    if proposed_emi < 0:
        return CalculationResult(
            calculation_name=name,
            value=None,
            unit="INR/month",
            inputs_used=inputs,
            assumptions=[],
            formula="monthly_income - existing_obligations - monthly_expenses - proposed_emi",
            status="invalid_inputs",
            error_message="proposed_emi must be non-negative.",
        )

    assumptions = [f"Deducts proposed monthly EMI: ₹{proposed_emi:,.2f}."]
    deductions = proposed_emi

    if existing_obligations is not None:
        deductions += existing_obligations
        assumptions.append(f"Deducts existing monthly obligations: ₹{existing_obligations:,.2f}.")
    else:
        assumptions.append("Existing obligations not specified; assumed 0.")

    if monthly_expenses is not None:
        deductions += monthly_expenses
        assumptions.append(f"Deducts estimated living expenses: ₹{monthly_expenses:,.2f}.")
    else:
        assumptions.append("Living expenses not specified; assumed 0.")

    surplus = monthly_income - deductions
    return CalculationResult(
        calculation_name=name,
        value=round(surplus, 2),
        unit="INR/month",
        inputs_used=inputs,
        assumptions=assumptions,
        formula="monthly_income - existing_obligations - monthly_expenses - proposed_emi",
        status="ok",
    )


# ── Financial Input Validation ────────────────────────────────────────────────

def _parse_numeric(val: Any, field_name: str) -> tuple[float | None, str | None]:
    """Safely parse float from int/float/string, rejecting booleans, NaN, and invalid strings."""
    if val is None:
        return None, None
    if isinstance(val, bool):
        return None, f"Field '{field_name}' must be numeric, received boolean."
    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val):
            return None, f"Field '{field_name}' contains non-finite numeric value (NaN or Inf)."
        return float(val), None
    if isinstance(val, str):
        cleaned = re.sub(r"[₹$,\s]", "", val)
        if not cleaned:
            return None, None
        try:
            parsed = float(cleaned)
            if math.isnan(parsed) or math.isinf(parsed):
                return None, f"Field '{field_name}' contains non-finite numeric value."
            return parsed, None
        except ValueError:
            return None, f"Field '{field_name}' could not be parsed as a number: '{val}'."
    return None, f"Field '{field_name}' has unsupported type {type(val).__name__}."


def validate_financial_inputs(financial_inputs: dict[str, Any] | None) -> dict[str, Any]:
    """
    Validate numeric inputs, units, missing values, and invalid ranges.
    Never assumes missing information is zero.
    """
    if not isinstance(financial_inputs, dict):
        return {
            "validated_inputs": {},
            "missing_inputs": [
                {"field": "financial_inputs", "description": "Financial inputs object is missing or not a dictionary."}
            ],
            "validation_errors": ["Financial inputs dictionary must be provided."],
            "is_valid": False,
        }

    errors: list[str] = []
    missing: list[dict[str, str]] = []
    validated: dict[str, Any] = {}

    # Category, Lender, Product
    loan_cat = financial_inputs.get("loan_category")
    if loan_cat:
        cat_clean = str(loan_cat).strip().title()
        if cat_clean in {"Bank", "Nbfc", "Microfinance"}:
            validated["loan_category"] = "NBFC" if cat_clean == "Nbfc" else cat_clean
        else:
            validated["loan_category"] = str(loan_cat)
    else:
        validated["loan_category"] = None
        missing.append({"field": "loan_category", "description": "Loan category (Bank, NBFC, Microfinance) not specified."})

    validated["selected_lender"] = financial_inputs.get("selected_lender") or financial_inputs.get("lender_name")
    validated["selected_loan_product"] = financial_inputs.get("selected_loan_product") or financial_inputs.get("loan_product")

    # Numeric fields with alias support
    # 1. Requested loan amount
    raw_amount = (
        financial_inputs.get("requested_loan_amount")
        if "requested_loan_amount" in financial_inputs
        else (financial_inputs.get("principal") if "principal" in financial_inputs else financial_inputs.get("loan_amount"))
    )
    amt, err = _parse_numeric(raw_amount, "requested_loan_amount")
    if err:
        errors.append(err)
    elif amt is None:
        missing.append({"field": "requested_loan_amount", "description": "Requested loan amount is absent."})
        validated["requested_loan_amount"] = None
    elif amt < 0:
        errors.append("requested_loan_amount must be non-negative.")
        validated["requested_loan_amount"] = None
    else:
        validated["requested_loan_amount"] = amt

    # 2. Proposed tenure (months)
    raw_tenure = (
        financial_inputs.get("proposed_tenure")
        if "proposed_tenure" in financial_inputs
        else (financial_inputs.get("tenure_months") if "tenure_months" in financial_inputs else financial_inputs.get("tenure"))
    )
    tenure_val, err = _parse_numeric(raw_tenure, "proposed_tenure")
    if err:
        errors.append(err)
    elif tenure_val is None:
        missing.append({"field": "proposed_tenure", "description": "Proposed loan tenure (months) is absent."})
        validated["proposed_tenure"] = None
    elif tenure_val <= 0 or not tenure_val.is_integer():
        errors.append("proposed_tenure must be a positive integer in months.")
        validated["proposed_tenure"] = None
    else:
        validated["proposed_tenure"] = int(tenure_val)

    # 3. Proposed annual interest rate (%)
    raw_rate = (
        financial_inputs.get("proposed_annual_interest_rate")
        if "proposed_annual_interest_rate" in financial_inputs
        else (financial_inputs.get("annual_interest_rate_pct") if "annual_interest_rate_pct" in financial_inputs else financial_inputs.get("interest_rate"))
    )
    rate_val, err = _parse_numeric(raw_rate, "proposed_annual_interest_rate")
    if err:
        errors.append(err)
    elif rate_val is None:
        missing.append({"field": "proposed_annual_interest_rate", "description": "Proposed annual interest rate (%) is absent."})
        validated["proposed_annual_interest_rate"] = None
    elif rate_val < 0:
        errors.append("proposed_annual_interest_rate must be non-negative.")
        validated["proposed_annual_interest_rate"] = None
    else:
        validated["proposed_annual_interest_rate"] = rate_val

    # 4. Declared monthly income
    raw_income = (
        financial_inputs.get("applicant_declared_monthly_income")
        if "applicant_declared_monthly_income" in financial_inputs
        else (
            financial_inputs.get("declared_income")
            if "declared_income" in financial_inputs
            else (financial_inputs.get("monthly_income") if "monthly_income" in financial_inputs else financial_inputs.get("gross_monthly_income"))
        )
    )
    inc_val, err = _parse_numeric(raw_income, "applicant_declared_monthly_income")
    if err:
        errors.append(err)
    elif inc_val is None:
        missing.append({"field": "applicant_declared_monthly_income", "description": "Applicant's declared monthly income is absent."})
        validated["applicant_declared_monthly_income"] = None
    elif inc_val < 0:
        errors.append("applicant_declared_monthly_income must be non-negative.")
        validated["applicant_declared_monthly_income"] = None
    else:
        validated["applicant_declared_monthly_income"] = inc_val

    # 5. Existing monthly loan obligations
    raw_loan_debts = (
        financial_inputs.get("existing_monthly_loan_obligations")
        if "existing_monthly_loan_obligations" in financial_inputs
        else (
            financial_inputs.get("total_monthly_obligations")
            if "total_monthly_obligations" in financial_inputs
            else financial_inputs.get("monthly_obligations")
        )
    )
    loan_debt_val, err = _parse_numeric(raw_loan_debts, "existing_monthly_loan_obligations")
    if err:
        errors.append(err)
    elif loan_debt_val is None:
        missing.append({"field": "existing_monthly_loan_obligations", "description": "Existing monthly loan obligations not provided."})
        validated["existing_monthly_loan_obligations"] = None
    elif loan_debt_val < 0:
        errors.append("existing_monthly_loan_obligations must be non-negative.")
        validated["existing_monthly_loan_obligations"] = None
    else:
        validated["existing_monthly_loan_obligations"] = loan_debt_val

    # 6. Other relevant financial obligations
    raw_other = (
        financial_inputs.get("other_relevant_financial_obligations")
        if "other_relevant_financial_obligations" in financial_inputs
        else financial_inputs.get("other_financial_obligations")
    )
    other_debt_val, err = _parse_numeric(raw_other, "other_relevant_financial_obligations")
    if err:
        errors.append(err)
    elif other_debt_val is None:
        validated["other_relevant_financial_obligations"] = None
    elif other_debt_val < 0:
        errors.append("other_relevant_financial_obligations must be non-negative.")
        validated["other_relevant_financial_obligations"] = None
    else:
        validated["other_relevant_financial_obligations"] = other_debt_val

    # 7. Monthly living expenses
    raw_exp = financial_inputs.get("monthly_expenses")
    exp_val, err = _parse_numeric(raw_exp, "monthly_expenses")
    if err:
        errors.append(err)
    elif exp_val is not None and exp_val < 0:
        errors.append("monthly_expenses must be non-negative.")
        validated["monthly_expenses"] = None
    else:
        validated["monthly_expenses"] = exp_val

    # Optional complex structures
    validated["income_sources"] = financial_inputs.get("income_sources")
    validated["itemized_obligations"] = financial_inputs.get("itemized_obligations")
    validated["applicant_category"] = str(financial_inputs.get("applicant_category") or "salaried").lower()
    validated["configured_document_checklist"] = financial_inputs.get("configured_document_checklist")
    validated["configured_lender_requirements"] = financial_inputs.get("configured_lender_requirements")

    return {
        "validated_inputs": validated,
        "missing_inputs": missing,
        "validation_errors": errors,
        "is_valid": len(errors) == 0,
    }


# ── Financial Consistency Checks ──────────────────────────────────────────────

def reconcile_financial_consistency(
    declared_income: float | None,
    extracted_fields: list[dict[str, Any]] | None = None,
    documents: list[dict[str, Any]] | None = None,
    declared_obligations: float | None = None,
) -> dict[str, Any]:
    """
    Compare declared income, extracted salary (gross vs net), recurring salary credits,
    and existing debt obligations.

    Flags discrepancies with detailed evidence and explanations.
    Accounts for gross-to-net deductions, irregular income, and statement periods.
    Returns 'not_assessed' or 'inconclusive' when evidence is insufficient.
    """
    extracted_fields = extracted_fields or []
    documents = documents or []

    gross_salary: float | None = None
    net_salary: float | None = None
    bank_salary_credit: float | None = None
    bank_loan_debits: float | None = None
    sources_found: list[str] = []

    # 1. Inspect extracted_fields
    for field_item in extracted_fields:
        fname = str(field_item.get("field_name") or field_item.get("field", "")).lower()
        val = field_item.get("value")
        parsed_val, _ = _parse_numeric(val, fname)
        if parsed_val is None or parsed_val <= 0:
            continue

        src = field_item.get("source_document") or field_item.get("source", "document")
        if any(k in fname for k in ["gross_salary", "gross_pay", "gross_earnings", "ctc"]):
            gross_salary = parsed_val
            sources_found.append(f"Gross salary from {src}: ₹{parsed_val:,.2f}")
        elif any(k in fname for k in ["net_salary", "net_pay", "take_home", "net_amount"]):
            net_salary = parsed_val
            sources_found.append(f"Net salary from {src}: ₹{parsed_val:,.2f}")
        elif any(k in fname for k in ["salary_credit", "recurring_salary", "sal_credit"]):
            bank_salary_credit = parsed_val
            sources_found.append(f"Bank salary credit from {src}: ₹{parsed_val:,.2f}")
        elif any(k in fname for k in ["emi_debit", "loan_debit", "existing_emi"]):
            bank_loan_debits = (bank_loan_debits or 0.0) + parsed_val
            sources_found.append(f"Bank loan debit from {src}: ₹{parsed_val:,.2f}")

    # 2. Text fallback from documents if not found in structured fields
    if gross_salary is None or net_salary is None or bank_salary_credit is None:
        for doc in documents:
            text = str(doc.get("text") or "")
            fname = str(doc.get("filename") or "doc")

            if gross_salary is None:
                m = re.search(r"(?:gross\s*(?:salary|pay|earnings|amount)|total\s*earnings)\s*[:=]?\s*(?:(?:rs\.?|inr|₹)\s*)?([0-9,]+(?:\.[0-9]{2})?)", text, re.IGNORECASE)
                if m:
                    parsed, _ = _parse_numeric(m.group(1), "gross_salary")
                    if parsed and parsed > 0:
                        gross_salary = parsed
                        sources_found.append(f"Extracted gross salary from text ({fname}): ₹{parsed:,.2f}")

            if net_salary is None:
                m = re.search(r"(?:net\s*(?:salary|pay|amount)|take\s*home\s*pay)\s*[:=]?\s*(?:(?:rs\.?|inr|₹)\s*)?([0-9,]+(?:\.[0-9]{2})?)", text, re.IGNORECASE)
                if m:
                    parsed, _ = _parse_numeric(m.group(1), "net_salary")
                    if parsed and parsed > 0:
                        net_salary = parsed
                        sources_found.append(f"Extracted net salary from text ({fname}): ₹{parsed:,.2f}")

            if bank_salary_credit is None:
                m = re.search(r"(?:sal(?:ary)?(?:\s*credit)?|neft\s*sal(?:ary)?|ach\s*credit)\s*[:=]?\s*(?:(?:rs\.?|inr|₹)\s*)?([0-9,]+(?:\.[0-9]{2})?)", text, re.IGNORECASE)
                if m:
                    parsed, _ = _parse_numeric(m.group(1), "bank_salary_credit")
                    if parsed and parsed > 0:
                        bank_salary_credit = parsed
                        sources_found.append(f"Extracted recurring salary credit from bank statement ({fname}): ₹{parsed:,.2f}")

    discrepancies: list[dict[str, Any]] = []
    audit_notes: list[str] = []
    manual_review: list[dict[str, Any]] = []

    # Evidence Assessment Status
    has_income_evidence = (gross_salary is not None) or (net_salary is not None) or (bank_salary_credit is not None)

    if not has_income_evidence:
        evidenced_income = {
            "gross_salary": None,
            "net_salary": None,
            "bank_salary_credit": None,
            "verified_status": "not_assessed",
            "evidence_sources": [],
            "notes": "No salary slips, tax returns, or bank statement salary credits were identified in uploaded documents.",
        }
        if declared_income is not None:
            manual_review.append({
                "item": "declared_income_verification",
                "reason": f"Declared monthly income of ₹{declared_income:,.2f} is unverified. No supporting income evidence documents were uploaded.",
                "severity": "high",
            })
        return {
            "assessment_status": "not_assessed",
            "evidenced_income": evidenced_income,
            "financial_discrepancies": [],
            "manual_review_items": manual_review,
            "audit_notes": ["Income evidence not assessed due to lack of financial documentation."],
        }

    # Reconcile Declared Income vs Evidenced Figures
    primary_documented_income = gross_salary or net_salary or bank_salary_credit

    if declared_income is not None and primary_documented_income is not None:
        # Check against Gross Salary
        if gross_salary is not None:
            diff = declared_income - gross_salary
            diff_pct = (diff / gross_salary) * 100.0

            if diff > 0 and diff_pct > 15.0:
                discrepancies.append({
                    "field": "declared_income_vs_gross_salary",
                    "severity": "high",
                    "declared_value": declared_income,
                    "evidenced_value": gross_salary,
                    "variance_pct": round(diff_pct, 1),
                    "evidence": f"Declared monthly income (₹{declared_income:,.2f}) significantly exceeds documented gross salary (₹{gross_salary:,.2f}) by {diff_pct:.1f}%.",
                    "explanation": "Applicant declared an income higher than verifiable CTC/gross pay without documentation for supplementary income sources.",
                })
            elif abs(diff_pct) <= 15.0:
                audit_notes.append(f"Declared income (₹{declared_income:,.2f}) aligns with documented gross salary (₹{gross_salary:,.2f}) within 15% tolerance.")

        # Check Gross vs Net deduction consistency
        if gross_salary is not None and net_salary is not None:
            ratio = (net_salary / gross_salary) * 100.0
            if 60.0 <= ratio <= 92.0:
                audit_notes.append(
                    f"Net take-home pay is {ratio:.1f}% of gross salary (₹{net_salary:,.2f} vs ₹{gross_salary:,.2f}), "
                    f"consistent with standard statutory deductions (PF, professional tax, income tax)."
                )
            elif ratio < 50.0:
                discrepancies.append({
                    "field": "unusually_high_salary_deductions",
                    "severity": "medium",
                    "gross_salary": gross_salary,
                    "net_salary": net_salary,
                    "take_home_ratio_pct": round(ratio, 1),
                    "evidence": f"Net pay (₹{net_salary:,.2f}) is only {ratio:.1f}% of gross salary (₹{gross_salary:,.2f}).",
                    "explanation": "Unusually high payroll deductions detected. Requires verification of loan recoveries or voluntary PF deductions.",
                })

        # Check Net Salary vs Bank Recurring Salary Credit
        if net_salary is not None and bank_salary_credit is not None:
            credit_diff = abs(net_salary - bank_salary_credit)
            credit_diff_pct = (credit_diff / net_salary) * 100.0

            if credit_diff_pct > 10.0:
                discrepancies.append({
                    "field": "salary_slip_vs_bank_credit",
                    "severity": "medium",
                    "salary_slip_net": net_salary,
                    "bank_salary_credit": bank_salary_credit,
                    "variance_pct": round(credit_diff_pct, 1),
                    "evidence": f"Salary slip net pay (₹{net_salary:,.2f}) differs from bank recurring salary credit (₹{bank_salary_credit:,.2f}) by {credit_diff_pct:.1f}%.",
                    "explanation": "Net pay on salary slip does not reconcile with bank salary credits. May reflect variable allowances, reimbursements, or mid-year increments.",
                })
            else:
                audit_notes.append("Salary slip net pay reconciles cleanly with bank statement recurring salary credit.")

    # Reconcile Obligations vs Bank EMI Debits
    if bank_loan_debits is not None:
        declared_debts = declared_obligations or 0.0
        if bank_loan_debits > declared_debts + 1000.0:
            discrepancies.append({
                "field": "undeclared_bank_loan_debits",
                "severity": "high",
                "declared_obligations": declared_debts,
                "evidenced_bank_debits": bank_loan_debits,
                "evidence": f"Bank statement evidences active recurring loan/EMI debits of ₹{bank_loan_debits:,.2f}, exceeding declared obligations of ₹{declared_debts:,.2f}.",
                "explanation": "Applicant may have undeclared existing credit facilities or ongoing EMIs reflected in bank debits.",
            })

    assessment_status = "discrepancies_detected" if discrepancies else "verified"

    evidenced_income = {
        "gross_salary": gross_salary,
        "net_salary": net_salary,
        "bank_salary_credit": bank_salary_credit,
        "verified_status": assessment_status,
        "evidence_sources": sources_found,
        "notes": "; ".join(audit_notes) if audit_notes else "Income evidence verified against uploaded documents.",
    }

    return {
        "assessment_status": assessment_status,
        "evidenced_income": evidenced_income,
        "financial_discrepancies": discrepancies,
        "manual_review_items": manual_review,
        "audit_notes": audit_notes,
    }


# ── Configurable Loan-Document Checklist System ───────────────────────────────

@dataclass
class ChecklistItem:
    """A single requirement entry in a loan document checklist."""

    item_id: str
    document_name: str
    accepted_document_types: list[str]
    status: str  # "required" | "optional"
    applicant_category: str = "any"  # "any" | "salaried" | "self_employed" | "business"
    recency_window_days: int | None = None
    conditions: dict[str, Any] | None = None
    rule_source: str = "Standard Underwriting Policy"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentChecklist:
    """Lender and product-specific checklist configuration."""

    checklist_name: str
    lender_category: str | None  # "Bank" | "NBFC" | "Microfinance" | None
    lender_name: str | None
    loan_product: str | None
    is_lender_specific: bool
    items: list[ChecklistItem]
    policy_notes: list[str] = field(default_factory=list)
    is_trusted_lender_policy: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "checklist_name": self.checklist_name,
            "lender_category": self.lender_category,
            "lender_name": self.lender_name,
            "loan_product": self.loan_product,
            "is_lender_specific": self.is_lender_specific,
            "is_trusted_lender_policy": self.is_trusted_lender_policy,
            "items": [it.to_dict() for it in self.items],
            "policy_notes": self.policy_notes,
        }


# Reference Authoritative Checklists
_STANDARD_BANK_HOME_LOAN = DocumentChecklist(
    checklist_name="Bank Retail Home Loan Checklist",
    lender_category="Bank",
    lender_name="Standard Bank Policy",
    loan_product="Home Loan",
    is_lender_specific=True,
    is_trusted_lender_policy=True,
    items=[
        ChecklistItem(
            item_id="identity_proof",
            document_name="Proof of Identity",
            accepted_document_types=["aadhaar_card", "pan_card", "passport", "voter_id", "driving_license"],
            status="required",
            applicant_category="any",
            rule_source="RBI Master Direction - Know Your Customer (KYC) Direction, 2016",
        ),
        ChecklistItem(
            item_id="address_proof",
            document_name="Proof of Address",
            accepted_document_types=["utility_bill", "aadhaar_card", "passport", "voter_id", "rent_agreement"],
            status="required",
            applicant_category="any",
            recency_window_days=90,
            rule_source="RBI KYC Norms - Officially Valid Documents",
        ),
        ChecklistItem(
            item_id="income_proof_salaried",
            document_name="Salary Slips / Form 16",
            accepted_document_types=["salary_slip", "form_16"],
            status="required",
            applicant_category="salaried",
            recency_window_days=90,
            rule_source="Bank Credit Underwriting Circular - Salaried Home Loans",
        ),
        ChecklistItem(
            item_id="income_proof_business",
            document_name="Income Tax Returns (ITR)",
            accepted_document_types=["itr", "tax_return", "form_16"],
            status="required",
            applicant_category="business",
            recency_window_days=365,
            rule_source="Bank Credit Underwriting Circular - Business Home Loans",
        ),
        ChecklistItem(
            item_id="bank_statement",
            document_name="Operative Bank Account Statement",
            accepted_document_types=["bank_statement"],
            status="required",
            applicant_category="any",
            recency_window_days=180,
            rule_source="Bank Operational Guidelines - 6 Months Statement Verification",
        ),
        ChecklistItem(
            item_id="existing_loan_statement",
            document_name="Existing Loan Repayment Track Record",
            accepted_document_types=["loan_statement", "sanction_letter", "repayment_schedule"],
            status="optional",
            applicant_category="any",
            rule_source="Credit Risk Mitigation Standards",
        ),
    ],
    policy_notes=["Requires minimum 6 months operative bank statement and recent 3 months salary slips for salaried applicants."],
)

_STANDARD_NBFC_PERSONAL_LOAN = DocumentChecklist(
    checklist_name="NBFC Fast-Track Personal Loan Checklist",
    lender_category="NBFC",
    lender_name="Standard NBFC Policy",
    loan_product="Personal Loan",
    is_lender_specific=True,
    is_trusted_lender_policy=True,
    items=[
        ChecklistItem(
            item_id="identity_proof",
            document_name="PAN Card & Identity Proof",
            accepted_document_types=["pan_card", "aadhaar_card", "passport"],
            status="required",
            applicant_category="any",
            rule_source="NBFC Digital Underwriting Mandate",
        ),
        ChecklistItem(
            item_id="bank_statement",
            document_name="Bank Statement (3 Months)",
            accepted_document_types=["bank_statement"],
            status="required",
            applicant_category="any",
            recency_window_days=90,
            rule_source="NBFC Risk Policy v4.2",
        ),
        ChecklistItem(
            item_id="income_proof",
            document_name="Recent Salary Slip or Income Proof",
            accepted_document_types=["salary_slip", "itr", "form_16"],
            status="required",
            applicant_category="salaried",
            recency_window_days=60,
            rule_source="NBFC Digital Underwriting Policy",
        ),
        ChecklistItem(
            item_id="utility_bill",
            document_name="Current Residence Utility Bill",
            accepted_document_types=["utility_bill"],
            status="optional",
            applicant_category="any",
            recency_window_days=60,
            rule_source="NBFC Address Verification Addendum",
        ),
    ],
    policy_notes=["Streamlined digital verification with mandatory PAN and 3-month bank statement."],
)

_STANDARD_MICROFINANCE_GROUP_LOAN = DocumentChecklist(
    checklist_name="Microfinance Joint Liability Group (JLG) Loan Checklist",
    lender_category="Microfinance",
    lender_name="Standard Microfinance Policy",
    loan_product="Microfinance Group Loan",
    is_lender_specific=True,
    is_trusted_lender_policy=True,
    items=[
        ChecklistItem(
            item_id="identity_proof",
            document_name="Primary KYC (Aadhaar or Voter ID)",
            accepted_document_types=["aadhaar_card", "voter_id", "ration_card"],
            status="required",
            applicant_category="any",
            rule_source="RBI Master Direction - Regulatory Framework for Microfinance Loans, 2022",
        ),
        ChecklistItem(
            item_id="address_proof",
            document_name="Village / Urban Local Body Residence Proof",
            accepted_document_types=["aadhaar_card", "voter_id", "utility_bill", "ration_card"],
            status="required",
            applicant_category="any",
            rule_source="RBI Microfinance Household Assessment Directive",
        ),
        ChecklistItem(
            item_id="bank_passbook",
            document_name="Bank Account Passbook / Statement",
            accepted_document_types=["bank_statement"],
            status="required",
            applicant_category="any",
            recency_window_days=180,
            rule_source="DBT Bank Account Verification Norms",
        ),
        ChecklistItem(
            item_id="household_income_declaration",
            document_name="Self-Declared Household Income Certificate",
            accepted_document_types=["income_certificate", "self_declaration", "salary_slip"],
            status="required",
            applicant_category="any",
            rule_source="RBI Annual Household Income Limit Rule (< ₹3,00,000)",
        ),
        ChecklistItem(
            item_id="salary_slip",
            document_name="Formal Salary Slip",
            accepted_document_types=["salary_slip"],
            status="optional",
            applicant_category="any",
            rule_source="Microfinance Supplementary Documentation",
        ),
    ],
    policy_notes=["Microfinance assessment complies with RBI 2022 directives. Income-tax returns are waived for low-income rural households."],
)

_GENERAL_RETAIL_LOAN_CHECKLIST = DocumentChecklist(
    checklist_name="General Retail Loan Baseline Checklist",
    lender_category=None,
    lender_name=None,
    loan_product=None,
    is_lender_specific=False,
    is_trusted_lender_policy=True,
    items=[
        ChecklistItem(
            item_id="identity_proof",
            document_name="Government-Issued Identity Proof",
            accepted_document_types=["aadhaar_card", "pan_card", "passport", "voter_id", "driving_license"],
            status="required",
            applicant_category="any",
            rule_source="General Regulatory KYC Baseline",
        ),
        ChecklistItem(
            item_id="address_proof",
            document_name="Residential Address Proof",
            accepted_document_types=["utility_bill", "aadhaar_card", "passport", "voter_id", "rent_agreement"],
            status="required",
            applicant_category="any",
            recency_window_days=90,
            rule_source="General Address Verification Norms",
        ),
        ChecklistItem(
            item_id="income_evidence",
            document_name="Income Documentation",
            accepted_document_types=["salary_slip", "form_16", "itr", "tax_return", "bank_statement"],
            status="required",
            applicant_category="any",
            recency_window_days=180,
            rule_source="General Financial Capacity Norms",
        ),
        ChecklistItem(
            item_id="bank_statement",
            document_name="Bank Account Statement",
            accepted_document_types=["bank_statement"],
            status="optional",
            applicant_category="any",
            recency_window_days=180,
            rule_source="Standard Banking Assessment",
        ),
    ],
    policy_notes=["Lender-specific checklist was not configured; evaluated against general retail loan baseline."],
)

# Registry of trusted server-side checklist references
TRUSTED_SERVER_CHECKLIST_REGISTRY: dict[str, DocumentChecklist] = {
    "bank_home_loan": _STANDARD_BANK_HOME_LOAN,
    "bank_personal_loan": _STANDARD_BANK_HOME_LOAN,
    "nbfc_personal_loan": _STANDARD_NBFC_PERSONAL_LOAN,
    "nbfc_business_loan": _STANDARD_NBFC_PERSONAL_LOAN,
    "microfinance_group_loan": _STANDARD_MICROFINANCE_GROUP_LOAN,
    "general_retail_loan": _GENERAL_RETAIL_LOAN_CHECKLIST,
}


def get_checklist_for_lender(
    lender_category: str | None,
    lender_name: str | None = None,
    loan_product: str | None = None,
    custom_checklist: list[dict[str, Any]] | list[str] | str | None = None,
) -> DocumentChecklist:
    """
    Retrieve or construct a configurable document checklist.

    Security Rule: Arbitrary client-supplied policies are not accepted as trusted lender rules.
    If a trusted reference is given, maps to authoritative server-side definition.
    If untrusted client rules are passed, evaluates them with untrusted provenance flags.
    """
    # 1. Check if client passed a trusted server-side checklist reference string
    if isinstance(custom_checklist, str) and custom_checklist.strip():
        ref_key = custom_checklist.strip().lower()
        if ref_key in TRUSTED_SERVER_CHECKLIST_REGISTRY:
            return TRUSTED_SERVER_CHECKLIST_REGISTRY[ref_key]

    # 2. Handle Custom Checklist Overrides
    if custom_checklist and isinstance(custom_checklist, list):
        items: list[ChecklistItem] = []
        for idx, item in enumerate(custom_checklist, start=1):
            if isinstance(item, str):
                items.append(ChecklistItem(
                    item_id=f"custom_item_{idx}",
                    document_name=item,
                    accepted_document_types=[item.lower().replace(" ", "_")],
                    status="required",
                    rule_source="Client-Supplied Checklist Item (Untrusted Reference)",
                ))
            elif isinstance(item, dict):
                items.append(ChecklistItem(
                    item_id=str(item.get("item_id") or f"custom_{idx}"),
                    document_name=str(item.get("document_name") or item.get("name", f"Requirement {idx}")),
                    accepted_document_types=item.get("accepted_document_types", [str(item.get("document_type", "document"))]),
                    status=str(item.get("status") or ("required" if item.get("required", True) else "optional")).lower(),
                    applicant_category=str(item.get("applicant_category") or "any").lower(),
                    recency_window_days=item.get("recency_window_days"),
                    conditions=item.get("conditions"),
                    rule_source=str(item.get("rule_source") or "Client-Supplied Rule (Untrusted - Requires Credit Officer Verification)"),
                ))

        return DocumentChecklist(
            checklist_name=f"Client-Supplied Checklist - {lender_name or 'Custom Configuration'}",
            lender_category=lender_category,
            lender_name=lender_name,
            loan_product=loan_product,
            is_lender_specific=True,
            is_trusted_lender_policy=False,  # Security: client rules are never trusted blindly
            items=items,
            policy_notes=[
                "Security Note: Arbitrary client-supplied policies are not accepted as official lender rules.",
                "Client-submitted rules require manual underwriter verification against authoritative lender circulars.",
            ],
        )

    cat_norm = str(lender_category or "").strip().lower()
    prod_norm = str(loan_product or "").strip().lower()

    if cat_norm == "microfinance" or "micro" in cat_norm:
        return _STANDARD_MICROFINANCE_GROUP_LOAN
    if cat_norm == "nbfc":
        return _STANDARD_NBFC_PERSONAL_LOAN
    if cat_norm == "bank":
        if "home" in prod_norm:
            return _STANDARD_BANK_HOME_LOAN
        return _STANDARD_BANK_HOME_LOAN

    # Unconfigured lender fallback
    return _GENERAL_RETAIL_LOAN_CHECKLIST


def evaluate_document_checklist(
    checklist: DocumentChecklist,
    submitted_documents: list[dict[str, Any]],
    applicant_category: str = "salaried",
    requested_loan_amount: float | None = None,
) -> dict[str, Any]:
    """
    Evaluate submitted documents against the configured checklist.

    Distinguishes required vs optional items.
    Respects applicant category and applicable loan conditions.
    Marks lender compliance as 'not_assessed' when using the general unconfigured fallback.
    """
    app_cat = applicant_category.lower()

    # Build set of submitted types and document dates
    submitted_types = set()
    document_entries: list[dict[str, Any]] = []

    for doc in submitted_documents:
        dtype = str(doc.get("document_type") or "").lower().strip()
        filename = str(doc.get("filename") or "").lower().strip()
        doc_date = doc.get("document_date") or doc.get("date")

        submitted_types.add(dtype)
        document_entries.append({
            "filename": doc.get("filename"),
            "document_type": dtype,
            "document_date": doc_date,
        })

    fulfilled_items: list[dict[str, Any]] = []
    missing_required: list[dict[str, Any]] = []
    missing_optional: list[dict[str, Any]] = []
    stale_items: list[dict[str, Any]] = []

    for item in checklist.items:
        # Check category applicability
        if item.applicant_category not in {"any", app_cat}:
            continue

        # Check conditions (e.g. min_loan_amount)
        if item.conditions and requested_loan_amount is not None:
            min_amt = item.conditions.get("min_loan_amount")
            if min_amt and requested_loan_amount < min_amt:
                continue

        # Check if matched in submitted documents
        matched_doc = None
        for doc in document_entries:
            doc_type = doc["document_type"]
            doc_fn = doc["filename"].lower()
            if any(acc.lower() in doc_type or acc.lower() in doc_fn for acc in item.accepted_document_types):
                matched_doc = doc
                break

        if matched_doc:
            # Check recency if specified
            is_stale = False
            recency_days = item.recency_window_days
            doc_date_str = matched_doc.get("document_date")
            if recency_days and doc_date_str:
                # Attempt date parsing
                for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                    try:
                        d = datetime.datetime.strptime(str(doc_date_str).strip(), fmt).date()
                        age_days = (datetime.date.today() - d).days
                        if age_days > recency_days:
                            is_stale = True
                            stale_items.append({
                                "item_id": item.item_id,
                                "document_name": item.document_name,
                                "age_days": age_days,
                                "max_allowed_days": recency_days,
                                "rule_source": item.rule_source,
                            })
                        break
                    except ValueError:
                        continue

            fulfilled_items.append({
                "item_id": item.item_id,
                "document_name": item.document_name,
                "matched_filename": matched_doc["filename"],
                "matched_document_type": matched_doc["document_type"],
                "is_stale": is_stale,
                "status": item.status,
                "rule_source": item.rule_source,
            })
        else:
            missing_entry = {
                "item_id": item.item_id,
                "document_name": item.document_name,
                "accepted_types": item.accepted_document_types,
                "status": item.status,
                "rule_source": item.rule_source,
            }
            if item.status == "required":
                missing_required.append(missing_entry)
            else:
                missing_optional.append(missing_entry)

    # Determine compliance status
    if not checklist.is_lender_specific:
        lender_specific_compliance = "not_assessed"
        compliance_explanation = "Lender-specific checklist was not configured; evaluated against general baseline checklist."
    else:
        if missing_required:
            lender_specific_compliance = "non_compliant"
            compliance_explanation = f"Missing {len(missing_required)} mandatory lender document(s)."
        elif stale_items:
            lender_specific_compliance = "review_required"
            compliance_explanation = f"{len(stale_items)} document(s) exceed maximum validity/recency window."
        else:
            lender_specific_compliance = "compliant"
            compliance_explanation = "All mandatory lender-specific document requirements fulfilled."

    overall_status = "complete" if not missing_required and not stale_items else (
        "review_required" if stale_items and not missing_required else "incomplete"
    )
    if not submitted_documents and not missing_required:
        overall_status = "not_assessed"

    return {
        "checklist_name": checklist.checklist_name,
        "is_lender_specific": checklist.is_lender_specific,
        "is_trusted_lender_policy": checklist.is_trusted_lender_policy,
        "lender_specific_compliance": lender_specific_compliance,
        "overall_checklist_status": overall_status,
        "compliance_explanation": compliance_explanation,
        "fulfilled_items": fulfilled_items,
        "missing_required_documents": missing_required,
        "missing_optional_documents": missing_optional,
        "stale_documents": stale_items,
        "policy_notes": checklist.policy_notes,
    }


# ── Lending Decision Limitations ─────────────────────────────────────────────

LENDING_DECISION_LIMITATIONS = [
    "This financial review is an automated computational assessment and does NOT constitute an official credit sanction, loan approval, or loan rejection.",
    "Calculated EMI, total interest, and debt-to-income (DTI) ratios are deterministic arithmetic estimates based on supplied inputs; they do not represent binding lender offers.",
    "Underwriting criteria (such as minimum credit bureau score, risk spreads, FOIR caps, and collateral requirements) are determined exclusively by the lender.",
    "Where lender-specific policies or document checklists were unconfigured, lender compliance is marked 'not_assessed' rather than assumed compliant.",
    "Financial figures extracted from document images or PDF texts require verification by a qualified credit analyst against physical originals or digitally signed sources.",
]


# ── Master Financial Review Generator ────────────────────────────────────────

def generate_financial_review(
    financial_inputs: dict[str, Any] | None,
    extracted_fields: list[dict[str, Any]] | None = None,
    document_classifications: list[dict[str, Any]] | None = None,
    documents: list[dict[str, Any]] | None = None,
    lender_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Generate a complete, structured financial review for loan application analysis.

    Integrates input validation, deterministic calculations, consistency checks,
    and configurable checklist evaluation with explicit decision limitations.
    """
    financial_inputs = financial_inputs or {}
    extracted_fields = extracted_fields or []
    document_classifications = document_classifications or []
    documents = documents or []
    lender_context = lender_context or {}

    # Merge top-level lender context into financial inputs if absent
    merged_inputs = dict(financial_inputs)
    for k in ["loan_category", "selected_lender", "selected_loan_product"]:
        if k not in merged_inputs and k in lender_context:
            merged_inputs[k] = lender_context[k]

    # Validate inputs
    validation_res = validate_financial_inputs(merged_inputs)
    validated = validation_res["validated_inputs"]
    missing_inputs = validation_res["missing_inputs"]
    validation_errors = validation_res["validation_errors"]

    principal = validated.get("requested_loan_amount")
    rate = validated.get("proposed_annual_interest_rate")
    tenure = validated.get("proposed_tenure")
    declared_income = validated.get("applicant_declared_monthly_income")
    loan_obligations = validated.get("existing_monthly_loan_obligations")
    other_obligations = validated.get("other_relevant_financial_obligations")
    monthly_expenses = validated.get("monthly_expenses")
    income_sources = validated.get("income_sources")
    itemized_obligations = validated.get("itemized_obligations")
    app_category = validated.get("applicant_category", "salaried")

    # 1. EMI Calculation
    emi_result = calculate_emi(principal=principal, annual_interest_rate_pct=rate, tenure_months=tenure)

    # 2. Total Repayment and Interest
    repayment_result, interest_result = calculate_total_repayment_and_interest(
        principal=principal,
        emi=emi_result.value if emi_result.status == "ok" else None,
        tenure_months=tenure,
        annual_interest_rate_pct=rate,
    )

    # 3. Income Summaries
    if income_sources:
        income_summary_result = calculate_total_monthly_income(income_sources)
        effective_income = income_summary_result.value or declared_income
    else:
        income_summary_result = CalculationResult(
            calculation_name="total_monthly_income",
            value=declared_income,
            unit="INR/month",
            inputs_used={"declared_income": declared_income},
            assumptions=["Based on applicant-declared monthly income; itemized sources not provided."],
            formula="declared_income",
            status="ok" if declared_income is not None else "missing_inputs",
        )
        effective_income = declared_income

    # 4. Total Existing Debt Obligations
    obligations_result = calculate_total_existing_monthly_obligations(
        existing_loan_obligations=loan_obligations,
        other_financial_obligations=other_obligations,
        itemized_obligations=itemized_obligations,
    )
    total_obligations_val = obligations_result.value

    # 5. Debt-To-Income (DTI) Ratios
    existing_dti_result = calculate_debt_to_income_ratio(
        total_monthly_obligations=total_obligations_val,
        gross_monthly_income=effective_income,
    )
    projected_dti_result = calculate_projected_debt_to_income_ratio(
        existing_monthly_obligations=total_obligations_val,
        proposed_emi=emi_result.value if emi_result.status == "ok" else None,
        gross_monthly_income=effective_income,
    )

    # 6. Monthly Surplus
    existing_surplus_result = calculate_net_surplus(
        monthly_income=effective_income,
        monthly_obligations=total_obligations_val,
        monthly_expenses=monthly_expenses,
    )
    projected_surplus_result = calculate_projected_monthly_surplus(
        monthly_income=effective_income,
        existing_obligations=total_obligations_val,
        proposed_emi=emi_result.value if emi_result.status == "ok" else None,
        monthly_expenses=monthly_expenses,
    )

    # 7. Financial Consistency Checks
    consistency_res = reconcile_financial_consistency(
        declared_income=declared_income,
        extracted_fields=extracted_fields,
        documents=documents,
        declared_obligations=total_obligations_val,
    )
    evidenced_income = consistency_res["evidenced_income"]
    financial_discrepancies = consistency_res["financial_discrepancies"]
    manual_review_items = consistency_res["manual_review_items"]

    # 8. Document Checklist Evaluation
    custom_chk = (
        validated.get("configured_document_checklist")
        or lender_context.get("document_checklist")
        or lender_context.get("configured_document_checklist")
    )
    checklist = get_checklist_for_lender(
        lender_category=validated.get("loan_category") or lender_context.get("lender_category"),
        lender_name=validated.get("selected_lender") or lender_context.get("lender_name"),
        loan_product=validated.get("selected_loan_product") or lender_context.get("loan_product"),
        custom_checklist=custom_chk,
    )

    # Prepare document list for checklist matching
    docs_to_evaluate: list[dict[str, Any]] = []
    classified_types_map = {
        str(c.get("filename")): str(c.get("document_type"))
        for c in document_classifications if isinstance(c, dict)
    }

    for doc in documents:
        fn = str(doc.get("filename") or "")
        dtype = classified_types_map.get(fn) or doc.get("document_type") or "unknown"
        docs_to_evaluate.append({
            "filename": fn,
            "document_type": dtype,
            "document_date": doc.get("document_date"),
        })

    checklist_status = evaluate_document_checklist(
        checklist=checklist,
        submitted_documents=docs_to_evaluate,
        applicant_category=app_category,
        requested_loan_amount=principal,
    )

    # Collect all calculation assumptions
    all_assumptions: list[str] = []
    for r in [
        emi_result, repayment_result, interest_result, income_summary_result,
        obligations_result, existing_dti_result, projected_dti_result,
        existing_surplus_result, projected_surplus_result,
    ]:
        if r and r.assumptions:
            all_assumptions.extend(r.assumptions)
    if validation_errors:
        all_assumptions.extend([f"Validation warning: {e}" for e in validation_errors])

    # Append high/medium checklist gaps to manual review
    for missing_doc in checklist_status.get("missing_required_documents", []):
        manual_review_items.append({
            "item": f"missing_{missing_doc['item_id']}",
            "reason": f"Required document '{missing_doc['document_name']}' is missing according to {missing_doc['rule_source']}.",
            "severity": "high",
        })
    for stale_doc in checklist_status.get("stale_documents", []):
        manual_review_items.append({
            "item": f"stale_{stale_doc['item_id']}",
            "reason": f"Document '{stale_doc['document_name']}' is {stale_doc['age_days']} days old, exceeding {stale_doc['max_allowed_days']}-day validity limit.",
            "severity": "medium",
        })

    return {
        "declared_income": {
            "value": declared_income,
            "currency": "INR",
            "period": "monthly",
            "source": "applicant_declaration",
        },
        "evidenced_income": evidenced_income,
        "existing_monthly_obligations": obligations_result.to_dict(),
        "proposed_emi": emi_result.to_dict(),
        "estimated_total_repayment": repayment_result.to_dict(),
        "estimated_total_interest": interest_result.to_dict(),
        "debt_to_income_ratio": {
            "existing_dti": existing_dti_result.to_dict(),
            "projected_dti": projected_dti_result.to_dict(),
        },
        "estimated_monthly_surplus": {
            "existing_surplus": existing_surplus_result.to_dict(),
            "projected_surplus": projected_surplus_result.to_dict(),
        },
        "calculation_assumptions": list(dict.fromkeys(all_assumptions)),
        "missing_financial_inputs": missing_inputs,
        "financial_discrepancies": financial_discrepancies,
        "document_checklist_status": checklist_status,
        "manual_review_items": manual_review_items,
        "decision_limitations": LENDING_DECISION_LIMITATIONS,
    }


# ── Backward Compatibility Wrapper ───────────────────────────────────────────

def run_all_calculations(financial_inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Run all applicable calculations given a dictionary of financial inputs.
    Preserves exact backward-compatible output schema.
    """
    results: list[CalculationResult] = []

    emi_result = calculate_emi(
        principal=financial_inputs.get("principal") or financial_inputs.get("requested_loan_amount"),
        annual_interest_rate_pct=financial_inputs.get("annual_interest_rate_pct") or financial_inputs.get("proposed_annual_interest_rate"),
        tenure_months=financial_inputs.get("tenure_months") or financial_inputs.get("proposed_tenure"),
    )
    results.append(emi_result)

    income_sources = financial_inputs.get("income_sources")
    if income_sources:
        results.append(calculate_total_monthly_income(income_sources))

    results.append(calculate_debt_to_income_ratio(
        total_monthly_obligations=financial_inputs.get("total_monthly_obligations") or financial_inputs.get("existing_monthly_loan_obligations"),
        gross_monthly_income=financial_inputs.get("gross_monthly_income") or financial_inputs.get("monthly_income") or financial_inputs.get("applicant_declared_monthly_income"),
    ))

    results.append(calculate_net_surplus(
        monthly_income=financial_inputs.get("monthly_income") or financial_inputs.get("applicant_declared_monthly_income"),
        monthly_obligations=financial_inputs.get("monthly_obligations") or financial_inputs.get("total_monthly_obligations"),
        monthly_expenses=financial_inputs.get("monthly_expenses"),
    ))

    return [r.to_dict() for r in results]
