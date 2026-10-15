# CREDORA Live Hackathon Demonstration Checklist

> **Target Audience:** Hackathon Judges, Evaluators & Technical Presenters  
> **Estimated Demo Duration:** 3 to 5 minutes  
> **Platform Requirements:** Windows PowerShell (or macOS/Linux Bash), Python 3.10+, Port 8001  

---

## 🚀 Pre-Flight Readiness Check (30 Seconds Before Demo)

Run these checks in PowerShell to guarantee everything is running smoothly before you begin:

```powershell
# 1. Ensure the AI Service is running on Port 8001
curl.exe -s http://127.0.0.1:8001/health

# Or in PowerShell:
# (Invoke-RestMethod http://127.0.0.1:8001/health)

# Expected Output:
# {"status":"healthy","timestamp":...}
```

*If the service is not running, launch it in a dedicated terminal window:*
```powershell
cd c:\HACKATHON\VSBEC-24hrs-Hackathon
.\venv\Scripts\Activate.ps1
.\venv\Scripts\python -m uvicorn app:app --app-dir ai-server --port 8001
```

---

## 🎬 Demo Scenario: Multi-Anomaly Underwriting Stress Test (The "Showstopper")

This scenario demonstrates the depth of CREDORA's deterministic and AI verification engine by submitting a realistic, multi-document loan package containing **three deliberate discrepancies**:

1. **Address Mismatch**: Aadhaar shows **Pune, Maharashtra** (411001), while the electricity utility bill shows **Navi Mumbai, Maharashtra** (400703).
2. **Missing Mandatory Document**: Commercial Bank policy requires a **6-month Bank Statement**, which is omitted from the submission bundle.
3. **Declared Income Discrepancy**: The applicant declared **₹60,000/month** on the application form, but the verified salary slip reflects **₹95,000/month gross** (a 58.3% variance requiring underwriter sign-off).

---

### Step 1: Generate Synthetic Demo Documents & Execute Analysis

Copy and paste this single block into your PowerShell terminal. It creates the synthetic test documents and immediately submits them to the live analysis endpoint:

```powershell
# Create temporary directory for demo files
$DemoDir = Join-Path $env:TEMP "credora_demo"
New-Item -ItemType Directory -Force -Path $DemoDir | Out-Null

# 1. Create Synthetic Aadhaar Card (Permanent Address: Pune, MH)
@"
GOVERNMENT OF INDIA
UNIQUE IDENTIFICATION AUTHORITY OF INDIA
Aadhaar - Mera Aadhaar, Meri Pehchan
Name: Rajesh Kumar Patel
DOB: 15/08/1988
Gender: Male
Aadhaar Number: 9876 5432 1098
Address: Flat 402, Shivam Enclave, Senapati Bapat Road, Pune, Maharashtra - 411001
"@ | Set-Content (Join-Path $DemoDir "aadhaar_card.txt")

# 2. Create Synthetic Electricity Bill (Current Address: Navi Mumbai, MH)
@"
MAHARASHTRA STATE ELECTRICITY DISTRIBUTION CO. LTD.
Consumer Name: Rajesh Kumar Patel
Consumer Number: 028471928472
Billing Date: 05/10/2026
Service Address: House 12, Sector 19, Vashi, Navi Mumbai, Maharashtra - 400703
Bill Amount: Rs. 2,450.00
Status: Paid
"@ | Set-Content (Join-Path $DemoDir "electricity_bill.txt")

# 3. Create Synthetic Salary Slip (Employer: Infosys, Gross: Rs. 95,000)
@"
INFOSYS LIMITED
SALARY SLIP FOR THE MONTH OF SEPTEMBER 2026
Employee Name: Rajesh Kumar Patel
Employee ID: INF-89210
Designation: Senior Systems Engineer
PAN: ABCDP1234F
Gross Salary: Rs. 95,000.00
Basic Salary: Rs. 45,000.00
HRA: Rs. 22,500.00
Special Allowance: Rs. 27,500.00
Total Deductions: Rs. 13,000.00
Net Pay: Rs. 82,000.00
Bank Account: 501004829102 HDFC Bank
"@ | Set-Content (Join-Path $DemoDir "salary_slip.txt")

# Submit Multi-Document Bundle to CREDORA AI Service
Write-Host "`nSubmitting Loan Bundle to CREDORA AI Analyzer..." -ForegroundColor Cyan

$response = curl.exe -s -X POST "http://127.0.0.1:8001/analyze" `
  -F "applicant_name=Rajesh Kumar Patel" `
  -F "application_id=APP-DEMO-2026" `
  -F "lender_category=Bank" `
  -F "selected_lender=State Bank of India" `
  -F "selected_loan_product=Personal Loan" `
  -F "requested_loan_amount=500000" `
  -F "proposed_tenure_months=36" `
  -F "proposed_interest_rate=11.5" `
  -F "declared_monthly_income=60000" `
  -F "existing_monthly_obligations=5000" `
  -F "files=@$DemoDir\aadhaar_card.txt" `
  -F "files=@$DemoDir\electricity_bill.txt" `
  -F "files=@$DemoDir\salary_slip.txt"

# Pretty-print key findings
$json = $response | ConvertFrom-Json
Write-Host "`n========================================================" -ForegroundColor Green
Write-Host "CREDORA AUTOMATED UNDERWRITING AUDIT REPORT" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host "Application ID       : $($json.application_id)"
Write-Host "Analysis Status      : $($json.analysis_status)"
Write-Host "Applicant Name       : $($json.application_summary.applicant_name)"
Write-Host "Requested Loan       : Rs. $($json.application_summary.requested_loan_amount) over $($json.application_summary.proposed_tenure_months) months"
Write-Host "Calculated EMI       : Rs. $($json.financial_summary.calculated_emi)"
Write-Host "Applicant FOIR       : $($json.financial_summary.calculated_foir_percentage)% (Threshold: $($json.financial_summary.foir_threshold_percentage)%)"
Write-Host "`n--- CROSS-DOCUMENT VERIFICATION ---" -ForegroundColor Yellow
Write-Host "Identity Check       : $($json.cross_document_checks.identity_consistency.name_matches) (Score: $($json.cross_document_checks.identity_consistency.similarity_score))"
Write-Host "Address Consistency  : Status -> $($json.cross_document_checks.address_consistency.status)"
Write-Host "Address Notes        : $($json.cross_document_checks.address_consistency.notes)"
Write-Host "`n--- CHECKLIST & MISSING DOCUMENTS ---" -ForegroundColor Yellow
Write-Host "All Docs Provided?   : $($json.document_checklist_status.all_mandatory_provided)"
Write-Host "Missing Mandatory    : $($json.document_checklist_status.missing_mandatory_documents -join ', ')"
Write-Host "`n--- INCOME VARIANCE CHECK ---" -ForegroundColor Yellow
Write-Host "Declared Income      : Rs. $($json.application_summary.declared_monthly_income)"
Write-Host "Verified Net Pay     : Rs. $($json.financial_summary.verified_monthly_income)"
Write-Host "Income Discrepancy   : $($json.financial_summary.income_discrepancy.variance_percentage)% ($($json.financial_summary.income_discrepancy.discrepancy_level))"
Write-Host "`n--- MANUAL REVIEW ACTION ITEMS ---" -ForegroundColor Magenta
$json.manual_review_items | ForEach-Object { Write-Host " - $_" }
Write-Host "========================================================`n" -ForegroundColor Green
```

---

## 🎯 Step-by-Step Judge Talking Points

When presenting the output above to judges, guide their attention through these 4 pillars:

### 1. Highlight Deterministic Identity & Address Verification
> *"Judges, notice the cross-document engine. It didn't just read the name—it used token-sort algorithms to confirm 100% identity match across the Aadhaar and Salary Slip. But look at the address: it compared the Pune PIN code on the Aadhaar against the Navi Mumbai PIN code on the utility bill. Instead of blindly accepting it, CREDORA flagged the address variance as `DIFFERENT`, providing exact source document citations."*

### 2. Highlight Lender-Specific Checklist Enforcement
> *"Notice the checklist engine. The borrower applied for a Bank Personal Loan. The system checked the uploaded documents against the Bank's mandatory policy and immediately flagged that the 6-Month Bank Statement was missing, automatically marking `all_mandatory_provided: false`."*

### 3. Highlight Financial Variance & Metric Precision
> *"Look at the financial section. The applicant declared Rs. 60,000 on the loan form, but their payslip showed Rs. 95,000 gross. CREDORA calculated an exact 58.33% variance. Furthermore, the calculated EMI of Rs. 16,490 and FOIR of 27.48% are computed using deterministic actuarial formulas—not hallucinated by an LLM."*

### 4. Highlight Underwriter Review Checklist (Actionable AI)
> *"Finally, CREDORA does not attempt to play god and blindly reject or approve the loan. It generates prioritized risk flags and specific manual review checklist items for the human underwriter: verify the Pune vs Navi Mumbai address change, and collect the missing bank statement."*

---

## 🛡️ Step 2: Demonstrating Partial-Failure Fault Tolerance (1 Minute)

To prove enterprise reliability to judges, demonstrate what happens when a corrupted or empty file is submitted alongside valid documents:

```powershell
# Create a zero-byte empty file
New-Item -ItemType File -Force -Path (Join-Path $DemoDir "corrupted_scan.pdf") | Out-Null

# Submit bundle with the corrupt file included
$faultResponse = curl.exe -s -X POST "http://127.0.0.1:8001/analyze" `
  -F "applicant_name=Rajesh Kumar Patel" `
  -F "lender_category=Bank" `
  -F "selected_lender=State Bank of India" `
  -F "selected_loan_product=Personal Loan" `
  -F "requested_loan_amount=500000" `
  -F "declared_monthly_income=60000" `
  -F "files=@$DemoDir\aadhaar_card.txt" `
  -F "files=@$DemoDir\corrupted_scan.pdf"

$faultJson = $faultResponse | ConvertFrom-Json

Write-Host "Overall Success: $($faultJson.success)" -ForegroundColor Cyan
Write-Host "Document Results Summary:"
$faultJson.document_results | ForEach-Object {
    Write-Host " - $($_.filename): Status = $($_.extraction_status), Error = $($_.error)"
}
```

**Judge Talking Point:**
> *"Most hackathon prototypes crash with a 500 Internal Server Error if one file is damaged or unreadable. As you can see here, CREDORA gracefully isolates the failure: `corrupted_scan.pdf` is flagged with a clear diagnostic message, while `aadhaar_card.txt` continues through the full analysis pipeline."*

---

## 🛠️ Emergency Presenter Troubleshooting

| Issue Encountered | Root Cause | Instant Resolution |
| :--- | :--- | :--- |
| `curl: (7) Failed to connect to 127.0.0.1:8001` | AI Service is not running | Run `.\venv\Scripts\python -m uvicorn app:app --app-dir ai-server --port 8001` in a separate terminal. |
| `400 Bad Request: Missing required form fields` | Forgot required parameters | Ensure `applicant_name` or `application_details` is included in the multipart request. |
| `500 Internal Server Error` | Unhandled runtime exception | Check the server terminal log. Verify your `.env` contains a valid `GEMINI_API_KEY`. |
| Port 8001 already in use | Stale process running | Run `Get-Process python | Stop-Process` in PowerShell and restart the server. |
