# CREDORA: AI-Powered Multi-Lender Loan Document Analyzer

> **VSBEC 24-Hour Hackathon Project**  
> *Autonomous AI Underwriting Assistant for Banks, NBFCs, and Microfinance Institutions*

CREDORA is an intelligent, multi-document loan ingestion and automated underwriting analysis platform. It ingests borrower document bundles (PDF, DOCX, TXT, scanned images), extracts structured fields via **Google Gemini (2.0 Flash / 1.5 Flash)**, performs deterministic **cross-document consistency checks** (name phonetics, address components, tax IDs, timeline chronology), calculates **financial stress metrics** (FOIR/DTI, EMI, LTV, net disposable income), evaluates configurable **lender policies**, and generates a unified 16-field underwriter report with document-level evidence citations.

---

## 🏛 System Architecture

The platform uses a decoupled dual-service architecture designed for high availability, security, and independent scaling:

```
                      +------------------------------------------+
                      |           Borrower / Frontend            |
                      |  (React / Vite - http://localhost:5173)  |
                      +--------------------+---------------------+
                                           |
                                           v
+------------------------------------------+------------------------------------------+
|  Main Application Gateway (FastAPI)      |  Port 8000 (backend/main.py)             |
|  - Loan application lifecycle            |  - Role-based access control             |
|  - Borrower & Lender profiles            |  - Supabase Database & Auth persistence  |
+------------------------------------------+------------------------------------------+
                                           | (HTTP Multipart Forward / Direct)
                                           v
+-------------------------------------------------------------------------------------+
|  AI Document Analysis Engine (FastAPI)   |  Port 8001 (ai-server/app.py)            |
+-------------------------------------------------------------------------------------+
|  1. Ingestion & Sanitization  | File limits, magic bytes, safe extraction           |
|  2. OCR Engine                | PyMuPDF, pdfplumber, Tesseract (image/scanned PDF)  |
|  3. Gemini 2.0 Flash Engine   | Document classification & structured field parsing  |
|  4. Cross-Document Verifier   | Name similarity, address normalization, date checks |
|  5. Financial Calculator      | Deterministic EMI, FOIR, DTI, DSCR, LTV surplus     |
|  6. Checklist Evaluator       | Bank / NBFC / Microfinance document requirements    |
|  7. Report Aggregator         | Unified 16-field underwriter JSON with citations    |
+-------------------------------------------------------------------------------------+
```

---

## 🚀 Key Capabilities

1. **Multi-Format Ingestion**: Ingests up to 10 files per submission (`.pdf`, `.docx`, `.txt`, `.jpg`, `.jpeg`, `.png`) with strict MIME validation, path sanitization, and partial-failure tolerance (one corrupt file does not abort the entire bundle).
2. **Hybrid OCR & Extraction**: Fast native text extraction via `PyMuPDF`/`pdfplumber` with automatic fallback to `pytesseract` and `pdf2image` for scanned or image-only documents.
3. **Gemini-Powered Extraction**: Accurately classifies 10+ document types (Aadhaar, PAN, Passport, Salary Slips, Bank Statements, ITR, Business Proofs) and extracts structured financial & personal data with zero markdown formatting errors.
4. **Deterministic Cross-Document Verification**:
   - **Identity**: Token sort & phonetic name matching (flags married surname changes and spelling variations without false rejections).
   - **Address**: Component-level comparison (PIN code, city, state, street) distinguishing identical, near-match, and distinct addresses across documents.
   - **Financial Variance**: Compares declared application income vs. verified payslips/ITR and flags discrepancies exceeding 10%.
5. **Deterministic Financial & Risk Assessment**:
   - Validates borrower FOIR (Fixed Obligation to Income Ratio), Debt-to-Income (DTI), and Loan-to-Value (LTV).
   - Flags suspicious round-number transactions, zero-balance drops, and negative cashflow.
6. **Lender-Specific Checklist Engine**:
   - Supports tailored policy checklists for **Commercial Banks**, **NBFCs**, and **Microfinance Institutions** with provenance tracking (`is_trusted_lender_policy`).
7. **Security Hardened**:
   - Bounded concurrency (`asyncio.Semaphore(4)`) preventing LLM exhaustion.
   - Error messages completely stripped of server filesystem paths and credentials.
   - Restricted CORS origin whitelist.
   - Zero persistent PII or raw documents stored in unapproved storage.

---

## 📋 Prerequisites

- **Python**: Version 3.10, 3.11, or 3.12 (Windows PowerShell or Linux/macOS)
- **Google Gemini API Key**: [Google AI Studio](https://aistudio.google.com/)
- *(Optional for OCR)*:
  - **Tesseract OCR**: Installed and added to system `PATH`
  - **Poppler**: Installed for `pdf2image` PDF page rendering

---

## ⚡ Quickstart Guide (Windows PowerShell)

### 1. Clone & Set Up Virtual Environment

```powershell
# Navigate to the project root
cd c:\HACKATHON\VSBEC-24hrs-Hackathon

# Create a virtual environment (if not already created)
python -m venv venv

# Activate the virtual environment
.\venv\Scripts\Activate.ps1
```

### 2. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure Environment Variables

```powershell
# Copy the example environment template
Copy-Item .env.example .env

# Edit .env and supply your Gemini API Key:
# GEMINI_API_KEY=AIzaSy...
```

### 4. Start the AI Document Analysis Service (Port 8001)

The dedicated AI service runs on port `8001` and handles all document extraction, Gemini reasoning, and verification checks:

```powershell
.\venv\Scripts\python -m uvicorn app:app --app-dir ai-server --port 8001 --reload
```

*Verify service health:*
```powershell
curl.exe -s http://127.0.0.1:8001/health
# Response: {"status":"healthy","timestamp":1729000000.0}
```

Interactive API documentation is available at: [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)

### 5. Start the Main Backend Gateway (Port 8000)

In a separate terminal window:

```powershell
cd c:\HACKATHON\VSBEC-24hrs-Hackathon
.\venv\Scripts\Activate.ps1
.\venv\Scripts\python -m uvicorn backend.main:app --port 8000 --reload
```

Interactive API documentation is available at: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

## 🧪 Running the Automated Test Suite

The test suite validates text extraction, Gemini parsing, cross-document verification, financial metrics, and end-to-end API workflows with **100% deterministic test coverage**:

```powershell
.\venv\Scripts\python -m pytest ai-server/tests/ -v
```

**Test Suite Coverage Summary:**
- `test_ai_service.py` (99 tests): Document ingestion, extraction, OCR fallback, prompt builders, schema parsing.
- `test_cross_document.py` (29 tests): Name matching, address normalization, tax ID checks, chronology.
- `test_financial_validation.py` (29 tests): EMI, FOIR, DTI, DSCR, LTV, net surplus, checklist verification.
- `test_api_integration.py` (17 tests): End-to-end multipart `/analyze` workflows, error sanitization, synthetic hackathon scenarios.
- **Total: 174 Passed | 0 Failed | 0 Errors**

---

## 📡 Key API Endpoints

### AI Service (Port 8001)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Health check endpoint reporting service availability |
| `POST` | `/extract` | Multi-file text extraction returning page count & warnings |
| `POST` | `/analyze` | Multi-document loan underwriting analysis (16-field report) |

#### Sample Request (`POST /analyze`):

```powershell
curl.exe -X POST "http://127.0.0.1:8001/analyze" `
  -F "applicant_name=Rajesh Kumar Patel" `
  -F "application_id=APP-2026-HACK" `
  -F "lender_category=Bank" `
  -F "selected_lender=State Bank" `
  -F "selected_loan_product=Personal Loan" `
  -F "requested_loan_amount=500000" `
  -F "declared_monthly_income=60000" `
  -F "files=@path/to/aadhaar_card.pdf" `
  -F "files=@path/to/salary_slip.pdf"
```

---

## 📖 Documentation Index

- [Live Hackathon Demo Checklist](docs/AI_ANALYZER_DEMO_CHECKLIST.md): Step-by-step executable commands for the live hackathon demo.
- [AI Service Technical Guide](docs/AI_SERVICE_GUIDE.md): Comprehensive developer reference, schema definitions, and threat model.
- [Backend Audit Report](docs/AI_BACKEND_AUDIT.md): Deep-dive audit report and architecture mapping.

---

## ⚖️ Hackathon Disclaimer & Limitations

1. **Advisory Tool**: CREDORA is an automated decision-support assistant designed for loan underwriters. It does not replace human underwriters and does not issue final legal credit approvals or rejections.
2. **Synthetic Data**: Consistency checks confirm alignment across submitted documents; they do not independently verify document authenticity with government databases (UIDAI/NSDL) unless official APIs are configured.
3. **OCR Constraints**: Image-based PDF and photo extraction requires system-installed Tesseract and Poppler binaries. Native digital PDFs extract with 100% fidelity out-of-the-box.
