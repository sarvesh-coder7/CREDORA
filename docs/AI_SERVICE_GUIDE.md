# CREDORA AI Document Analyzer: Technical Service Guide

> **Service Version:** 2.1.0  
> **Default Port:** 8001 (`http://127.0.0.1:8001`)  
> **Framework:** FastAPI / Pydantic v2 / Google Gemini Generative AI SDK  

---

## 1. Overview & Service Responsibilities

The CREDORA AI Service (`ai-server/app.py`) is a microservice dedicated to multi-file borrower document processing and automated underwriting assessment. It operates independently of the main application gateway (`backend/main.py`), allowing CPU-intensive text extraction, OCR, and AI reasoning to scale autonomously.

### Core Responsibilities

```
                                  +-----------------------+
                                  | Multipart Form Upload |
                                  +-----------+-----------+
                                              |
                     +------------------------+------------------------+
                     |                                                 |
                     v                                                 v
         +-----------------------+                         +-----------------------+
         |  File Sanitization    |                         | Metadata Validation   |
         |  - Max 10 files       |                         | - Loan details        |
         |  - Max 20MB per file  |                         | - Lender requirements |
         |  - Magic byte verify  |                         | - Applicant info      |
         +-----------+-----------+                         +-----------+-----------+
                     |                                                 |
                     v                                                 |
         +-----------------------+                                     |
         | Text/OCR Extraction   |                                     |
         | - PyMuPDF (digital)   |                                     |
         | - Tesseract (scanned) |                                     |
         +-----------+-----------+                                     |
                     |                                                 |
                     v                                                 |
         +-----------------------+                                     |
         | Gemini Flash 2.0      |                                     |
         | - Classification      |                                     |
         | - Structured Parsing  |                                     |
         +-----------+-----------+                                     |
                     |                                                 |
                     +------------------------+------------------------+
                                              |
                                              v
                              +-------------------------------+
                              | Deterministic Rule Engines    |
                              | 1. Cross-Document Consistency |
                              | 2. Financial Metrics & Ratios |
                              | 3. Lender Policy Checklist    |
                              +---------------+---------------+
                                              |
                                              v
                              +-------------------------------+
                              | Consolidated 16-Field JSON    |
                              +-------------------------------+
```

1. **Document Ingestion & Sanitization**: Validates file types (`.pdf`, `.docx`, `.txt`, `.jpg`, `.jpeg`, `.png`), limits file count (≤10) and total payload (≤50MB), and isolates extraction failures per document.
2. **Text Extraction**: Uses `PyMuPDF` (`fitz`) and `pdfplumber` for digital PDFs, `python-docx` for Word files, and native UTF-8 decoding for text files. Image-based PDFs and photos trigger `pytesseract` and `pdf2image`.
3. **Structured Field Extraction**: Calls Google Gemini (`gemini-2.0-flash` or `gemini-1.5-flash`) via strict JSON schemas to extract personal identity, addresses, employers, gross/net income, and dates without hallucinated markdown.
4. **Deterministic Verification Engine**:
   - Compares applicant identity across records using token sorting and phonetic similarity.
   - Parses address tokens (PIN, city, state, street) to identify exact matches, near matches, and conflicts.
   - Cross-checks declared vs. documented financial figures (salary, liabilities, loan amount).
5. **Deterministic Financial Calculator**: Evaluates monthly cashflows, proposed EMIs, FOIR/DTI ratios, and LTV against standard banking thresholds.
6. **Lender Checklist Evaluator**: Compares extracted document categories against lender-specific requirements (Bank, NBFC, Microfinance) and assigns provenance tracking (`is_trusted_lender_policy`).
7. **Consolidated JSON Output**: Assembles all findings into a unified 16-field response.

---

## 2. Configuration & Environment Variables

All settings are configured via environment variables or a local `.env` file at the workspace root:

| Variable Name | Default Value | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | *(Required)* | Google Gemini API key obtained from Google AI Studio. |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model variant (`gemini-2.0-flash` or `gemini-1.5-flash`). |
| `GEMINI_MAX_RETRIES` | `2` | Maximum retry attempts for transient API failures. |
| `AI_SERVER_API_KEY` | `""` | Optional shared bearer secret for securing the AI endpoints. |
| `MAX_FILE_SIZE_MB` | `20` | Maximum allowed size per uploaded file in megabytes. |
| `MAX_TOTAL_UPLOAD_SIZE_MB` | `50` | Maximum cumulative size of all uploaded files. |
| `MAX_FILES_PER_REQUEST` | `10` | Maximum number of files processed in a single submission. |
| `EXTRACTION_TIMEOUT_SECONDS` | `60.0` | Timeout threshold for extracting text from a single file. |
| `OCR_TIMEOUT_SECONDS` | `30.0` | Timeout threshold for optical character recognition tasks. |
| `ALLOWED_ORIGINS` | `http://localhost:3000,...` | Comma-separated CORS allowed origin list. |

---

## 3. API Specifications

### 3.1. Health Check

```http
GET /health HTTP/1.1
Host: 127.0.0.1:8001
```

**Response (200 OK):**
```json
{
  "status": "healthy",
  "timestamp": 1729000000.0
}
```

---

### 3.2. Text Extraction Endpoint

Extracts text from one or more documents without invoking Gemini or financial analysis. Useful for quick pre-validation or previewing document content.

```http
POST /extract HTTP/1.1
Host: 127.0.0.1:8001
Content-Type: multipart/form-data
```

**Form Fields:**
- `files`: One or more binary files (`.pdf`, `.docx`, `.txt`, `.jpg`, `.jpeg`, `.png`).

**Response (200 OK):**
```json
{
  "total_files": 2,
  "successful_extractions": 2,
  "failed_extractions": 0,
  "documents": [
    {
      "filename": "aadhaar.pdf",
      "file_type": "pdf",
      "extraction_status": "success",
      "extracted_text": "GOVERNMENT OF INDIA\nRajesh Kumar Patel...",
      "page_count": 1,
      "character_count": 482,
      "word_count": 78,
      "warnings": [],
      "error": null
    }
  ]
}
```

---

### 3.3. Complete Loan Analysis Endpoint

The primary underwriting endpoint that ingests document bundles and application metadata, returning a consolidated report.

```http
POST /analyze HTTP/1.1
Host: 127.0.0.1:8001
Content-Type: multipart/form-data
```

**Accepted Input Methods:**
The endpoint flexibly accepts application metadata in either of two ways:
1. **JSON String (`application_details`)**: A single JSON string containing all loan fields and checklist requirements.
2. **Direct Form Fields**: Flat multipart form fields (`applicant_name`, `application_id`, `lender_category`, `selected_lender`, `selected_loan_product`, `requested_loan_amount`, `proposed_tenure_months`, `proposed_interest_rate`, `declared_monthly_income`, `existing_monthly_obligations`).

#### Example Request (cURL):

```bash
curl -X POST "http://127.0.0.1:8001/analyze" \
  -F "applicant_name=Rajesh Kumar Patel" \
  -F "application_id=APP-2026-001" \
  -F "lender_category=Bank" \
  -F "selected_lender=State Bank of India" \
  -F "selected_loan_product=Personal Loan" \
  -F "requested_loan_amount=500000" \
  -F "proposed_tenure_months=36" \
  -F "proposed_interest_rate=11.5" \
  -F "declared_monthly_income=60000" \
  -F "existing_monthly_obligations=5000" \
  -F "files=@synthetic_aadhaar.txt" \
  -F "files=@synthetic_salary_slip.txt"
```

---

## 4. The Unified 16-Field Response Schema

Every call to `POST /analyze` returns a standardized, predictable JSON schema. The 16 top-level keys provide immediate insight to frontends, underwriting engines, and audit logs:

| Key | Type | Description |
| :--- | :--- | :--- |
| `success` | `boolean` | `true` if processing succeeded (even with partial document warnings); `false` on fatal request rejection. |
| `application_id` | `string` | Unique identifier echoing the application ID. |
| `analysis_status` | `string` | Status indicator: `"COMPLETED"`, `"PARTIALLY_COMPLETED"`, `"REJECTED"`, or `"FAILED"`. |
| `application_summary` | `object` | Echoes loan metadata (applicant name, lender, product, requested amount, tenure). |
| `document_results` | `array` | Detailed extraction status, page count, and character statistics per submitted file. |
| `document_classifications` | `array` | Classification assigned to each document (category, subcategory, confidence score). |
| `extracted_fields` | `object` | Structured dictionary mapping each document to its extracted identity and financial fields. |
| `cross_document_checks` | `object` | Deterministic verification results (identity match, address comparison, financial variance). |
| `missing_information` | `array` | Critical fields missing across documents (e.g., missing PAN, missing employer name). |
| `financial_summary` | `object` | Deterministic calculations (EMI, FOIR, DTI, LTV, net surplus, verified income). |
| `document_checklist_status` | `object` | Lender policy compliance (mandatory vs. received documents, checklist match status). |
| `risk_flags` | `array` | Prioritized risk flags (`HIGH`, `MEDIUM`, `LOW`) identifying potential fraud or default risks. |
| `manual_review_items` | `array` | Clear, actionable checklist items requiring human underwriter inspection before sign-off. |
| `analysis_limitations` | `array` | Explicit disclaimers documenting unverified aspects or extraction warnings. |
| `errors` | `array` | Document-level or field-level extraction warnings and non-fatal processing errors. |
| `processing_metadata` | `object` | Performance metadata including processing time, model name, and engine version. |

### Sample Response Snippet

```json
{
  "success": true,
  "application_id": "APP-2026-001",
  "analysis_status": "COMPLETED",
  "application_summary": {
    "applicant_name": "Rajesh Kumar Patel",
    "lender_category": "Bank",
    "selected_lender": "State Bank of India",
    "selected_loan_product": "Personal Loan",
    "requested_loan_amount": 500000.0,
    "proposed_tenure_months": 36,
    "proposed_interest_rate": 11.5,
    "declared_monthly_income": 60000.0,
    "existing_monthly_obligations": 5000.0
  },
  "document_results": [
    {
      "filename": "synthetic_aadhaar.txt",
      "file_type": "txt",
      "extraction_status": "success",
      "page_count": 1,
      "character_count": 312,
      "warnings": [],
      "error": null
    }
  ],
  "document_classifications": [
    {
      "filename": "synthetic_aadhaar.txt",
      "document_type": "identity_proof",
      "document_subtype": "aadhaar_card",
      "confidence": 0.95
    }
  ],
  "cross_document_checks": {
    "overall_match_status": "REVIEW_REQUIRED",
    "identity_consistency": {
      "name_matches": true,
      "similarity_score": 1.0,
      "notes": "Exact match across identity and income documents."
    },
    "address_consistency": {
      "address_matches": false,
      "status": "DIFFERENT",
      "notes": "Aadhaar lists Pune, MH 411001 while Utility Bill lists Navi Mumbai, MH 400703."
    }
  },
  "financial_summary": {
    "calculated_emi": 16490.17,
    "calculated_foir_percentage": 27.48,
    "calculated_dti_percentage": 27.48,
    "foir_threshold_percentage": 50.0,
    "foir_assessment": "PASS",
    "disposable_monthly_income": 43509.83,
    "income_discrepancy": {
      "variance_percentage": 58.33,
      "discrepancy_level": "HIGH",
      "notes": "Declared income (₹60,000) differs significantly from verified payslip (₹95,000)."
    }
  },
  "document_checklist_status": {
    "is_trusted_lender_policy": true,
    "policy_name": "Commercial Bank Retail Loan",
    "all_mandatory_provided": false,
    "missing_mandatory_documents": [
      "bank_statement"
    ]
  },
  "risk_flags": [
    {
      "level": "HIGH",
      "category": "DOCUMENT_CHECKLIST",
      "description": "Missing mandatory document: bank_statement"
    },
    {
      "level": "MEDIUM",
      "category": "ADDRESS_VERIFICATION",
      "description": "Address mismatch between Aadhaar and utility bill."
    }
  ],
  "manual_review_items": [
    "Verify reason for address variance between Pune and Navi Mumbai.",
    "Obtain required 6-month bank statement to verify salary credits."
  ],
  "analysis_limitations": [
    "AI consistency checks verify coherence across submitted documents, not sovereign database authenticity."
  ],
  "errors": [],
  "processing_metadata": {
    "model_used": "gemini-2.0-flash",
    "total_documents_processed": 2,
    "processing_time_seconds": 1.42,
    "timestamp": 1729000000.0
  }
}
```

---

## 5. Security Architecture & Threat Defenses

1. **Path Traversal Defense**: Filenames are sanitized using `os.path.basename` and stripped of illegal characters (`..`, `/`, `\`) before memory handling. No temporary files are persisted to uncontrolled directories.
2. **Error Message Scrubbing**: The service includes an active sanitizer (`_sanitize_error_message`) that scrubs absolute local filesystem paths (e.g., `C:\Users\...` or `/home/...`), internal line numbers, and API keys from all client-facing error payloads.
3. **Bounded Concurrency**: All extraction and Gemini requests are throttled through an `asyncio.Semaphore(4)` queue to prevent rate limit saturation and denial-of-service on local memory.
4. **CORS Restrictions**: Unlike typical hackathon setups with wildcard `allow_origins=["*"]`, CORS is restricted to authorized frontend and gateway ports (`localhost:3000`, `localhost:5173`, `localhost:8000`).
5. **No PII Storage**: Extracted borrower records exist exclusively in transient execution memory and are not logged or stored on disk unless routed to an authorized database.

---

## 6. Known Limitations

- **Image Scanned PDF Quality**: Scanned image PDFs rely on Tesseract OCR. Low-resolution scans (<150 DPI) or handwritten notes may yield partial text extraction.
- **Government Registry Verification**: This service performs document-to-document cross-validation. It does not ping UIDAI or NSDL sovereign APIs; underwriters must use official government portals for official verification.
- **LLM Rate Limits**: When using Google AI Studio free-tier API keys, high burst traffic may encounter 429 quota exhaustion. The service includes exponential backoff retries (`GEMINI_MAX_RETRIES=2`) to mitigate this.
