# CREDORA AI Document Analysis Service — Integration Guide

## Overview

This FastAPI service provides AI-powered loan document extraction and analysis using Google Gemini. It runs independently on **port 8001** and is called by the Express backend on port 8000.

```
CREDORA Architecture
─────────────────────────────────────
Browser  ──►  Express (port 8000)  ──►  AI Service (port 8001)
                   │                          │
              Supabase DB              Google Gemini API
```

---

## Project Structure (new files created)

```
VSBEC-24hrs-Hackathon/
├── document_analyzer.py        ← existing (untouched, reused)
├── .env                        ← add GEMINI_API_KEY + GEMINI_MODEL here
├── pyproject.toml              ← new: pytest config
├── ai-server/
│   ├── app.py                  ← new: FastAPI service (port 8001)
│   ├── financial_calculator.py ← new: deterministic calculations
│   ├── ocr_support.py          ← new: optional OCR (Tesseract)
│   ├── README.md               ← this file
│   └── tests/
│       ├── __init__.py
│       └── test_ai_service.py  ← new: 71 tests (all offline)
└── backend/
    └── main.py                 ← existing (untouched)
```

---

## 1. Add required variables to your `.env`

Add these lines to `C:\HACKATHON\VSBEC-24hrs-Hackathon\.env`:

```env
# IMPORTANT: Rotate your API key if it was exposed in a screenshot.
GEMINI_API_KEY=your_rotated_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash

# Optional: protect /extract and /analyze endpoints
AI_SERVER_API_KEY=

# Optional: comma-separated CORS origins
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173,http://localhost:8000

# Optional: limits
MAX_FILE_SIZE_MB=20
MAX_FILES_PER_REQUEST=10
```

---

## 2. Start the AI Service (PowerShell)

```powershell
cd C:\HACKATHON\VSBEC-24hrs-Hackathon
.\venv\Scripts\Activate.ps1
python -m uvicorn app:app --app-dir ai-server --host 0.0.0.0 --port 8001 --reload
```

**Swagger docs:** http://localhost:8001/docs

---

## 3. Endpoint Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | No | Service info |
| GET | `/health` | No | Liveness |
| GET | `/health/ready` | No | Config readiness |
| POST | `/extract` | Yes | Extract text from uploads |
| POST | `/analyze/text` | Yes | Analyze pre-extracted JSON |
| POST | `/analyze` | Yes | Full pipeline (upload + extract + analyze) |

Auth = `X-API-Key` header (disabled when `AI_SERVER_API_KEY` is empty).

---

## 4. POST /analyze — Full Pipeline

**Content-Type:** `multipart/form-data`

| Field | Type | Required |
|-------|------|----------|
| `application_details` | JSON string (form field) | Yes |
| `files` | file upload (repeat for multiple) | Yes |
| `financial_inputs` | JSON string (form field) | No |

**curl example:**
```bash
curl -X POST http://localhost:8001/analyze \
  -F 'application_details={"applicant_name":"Priya Sharma","declared_monthly_income":55000}' \
  -F "files=@salary_slip.pdf" \
  -F "files=@bank_statement.pdf" \
  -F 'financial_inputs={"principal":500000,"annual_interest_rate_pct":10.5,"tenure_months":60}'
```

**PowerShell example:**
```powershell
$details = '{"applicant_name":"Priya Sharma","declared_monthly_income":55000}'
Invoke-RestMethod -Uri http://localhost:8001/analyze -Method POST -Form @{
  application_details = $details
  files = Get-Item "salary_slip.pdf"
} | ConvertTo-Json -Depth 10
```

---

## 5. Supported File Types

| Extension | Method |
|-----------|--------|
| `.pdf` | pypdf → OCR fallback if scanned |
| `.docx` | python-docx |
| `.txt` | UTF-8 decode |
| `.jpg` / `.jpeg` / `.png` | OCR only |

Max 20 MB per file, max 10 files per request.

---

## 6. OCR Setup (Windows, optional)

Only needed for scanned PDFs and images.

```powershell
# 1. Install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki
# 2. Install Poppler: https://github.com/oschwartz10612/poppler-windows/releases
#    Add Poppler bin/ to your PATH
# 3. Install Python packages:
.\venv\Scripts\pip.exe install pytesseract pdf2image
```

Without OCR, scanned documents return `"extraction_status": "ocr_unavailable"`. Text-based documents work normally.

---

## 7. Run Tests (offline, no API key needed)

```powershell
cd C:\HACKATHON\VSBEC-24hrs-Hackathon
.\venv\Scripts\python.exe -m pytest ai-server/tests/ -v
```

Result: **71 passed** in ~2 seconds.

---

## 8. Express Integration Point

Add to `backend/main.py`:

```python
import os, httpx
from fastapi import UploadFile, Form, File

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://127.0.0.1:8001")

@app.post("/api/ai/analyze")
async def proxy_ai_analyze(
    application_details: str = Form(...),
    files: list[UploadFile] = File(...),
):
    """Forward documents to the Python AI service. Gemini key never leaves server."""
    file_data = [("files", (f.filename, await f.read(), f.content_type)) for f in files]
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{AI_SERVICE_URL}/analyze",
            data={"application_details": application_details},
            files=file_data,
        )
    return resp.json()
```

Add `AI_SERVICE_URL=http://127.0.0.1:8001` to `.env`.

---

## 9. Financial Calculations

The `financial_inputs` field triggers deterministic Python calculations (no AI):

- EMI: `P * r * (1+r)^n / ((1+r)^n - 1)` (zero-interest handled separately)
- Total monthly income (supports monthly + annual sources)
- Debt-to-income ratio
- Estimated net surplus

Every result includes inputs, formula, assumptions, and status.

---

## 10. Security

- Rotate exposed credentials before production deployment.
- Set `AI_SERVER_API_KEY` to a strong random secret.
- Document text is never logged.
- The service never makes a loan approval decision.
- Do not commit `.env` to git.
